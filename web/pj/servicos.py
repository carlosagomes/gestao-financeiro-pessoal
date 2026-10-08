"""Informes PJ: horas em h:mm, gravação do informe do mês, conferência com o Salário e a previsão até dezembro.

O informe do mês M é pago como o "Salário" (Entrada) do próprio mês M. A previsão cria informes marcados como
previsão e lançamentos de Salário "PREVISÃO", mas nunca mexe num Salário de verdade.
"""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Sum
from django.db.models.functions import TruncMonth

from core.formatos import brl
from financeiro import servicos as financeiro
from financeiro.models import ENTRADA, Lancamento
from pj.models import Informe

CATEGORIA_SALARIO = "Salário"
MARCA_PREVISAO = "PREVISÃO"
ORIGEM_PREVISAO = "previsão PJ"


def hhmm(horas: float | None) -> str:
    if horas is None:
        return "—"
    total = round(horas * 60)
    return f"{total // 60}:{total % 60:02d}"


def ler_hhmm(texto: str) -> float:
    """'181:39' -> 181.65; também aceita '181' e '181,5'. Levanta ValueError com mensagem para o usuário."""
    texto = (texto or "").strip()
    if m := re.fullmatch(r"(\d{1,4}):([0-5]\d)", texto):
        return int(m[1]) + int(m[2]) / 60
    if re.fullmatch(r"\d{1,4}(?:[.,]\d+)?", texto):
        return float(texto.replace(",", "."))
    raise ValueError("Use o formato h:mm, por exemplo 178:51.")


def numero(texto, nome: str, obrigatorio: bool = True, positivo: bool = False) -> Decimal | None:
    """Número da tela ('1.234,56', '1234.56' ou vazio) em Decimal >= 0."""
    texto = str(texto if texto is not None else "").strip().replace("R$", "").strip()
    if not texto:
        if obrigatorio:
            raise ValueError(f"Preencha {nome}.")
        return None
    if "," in texto:
        texto = texto.replace(".", "").replace(",", ".")
    try:
        valor = Decimal(texto)
    except InvalidOperation:
        raise ValueError(f"{nome[:1].upper()}{nome[1:]} não é um número.")
    if valor < 0 or (positivo and valor == 0) or not valor.is_finite():
        raise ValueError(f"{nome[:1].upper()}{nome[1:]} precisa ser maior que zero." if positivo
                         else f"{nome[:1].upper()}{nome[1:]} não pode ser negativo.")
    return valor


def valor_nf(informe: Informe) -> float:
    """O valor do informe quando veio; senão, o calculado (bruto − plano)."""
    return float(informe.valor_nf) if informe.valor_nf is not None else informe.total_calculado


@transaction.atomic
def salvar_informe(usuario, mes: str, horas_texto: str, sobreaviso, valor_hora, plano, nf) -> Informe:
    """Grava o informe de verdade do mês (deixa de ser previsão)."""
    horas = ler_hhmm(horas_texto)
    campos = dict(
        horas=round(horas, 6),
        horas_sobreaviso=float(numero(sobreaviso, "as horas de sobreaviso", obrigatorio=False) or 0),
        valor_hora=numero(valor_hora, "o valor hora", positivo=True),
        desconto=numero(plano, "o plano de saúde", obrigatorio=False) or Decimal("0"),
        valor_nf=numero(nf, "o valor da NF", obrigatorio=False) or None,
        previsao=False,
        observacao=f"Informe: {hhmm(horas)} h",
    )
    informe, _ = Informe.objects.update_or_create(usuario=usuario, mes=mes, defaults=campos)
    return informe


def salario_por_mes(usuario) -> dict[str, float]:
    linhas = (Lancamento.objects.filter(usuario=usuario, movimentacao=ENTRADA, categoria=CATEGORIA_SALARIO)
              .annotate(mes=TruncMonth("data")).values("mes").annotate(total=Sum("valor")))
    return {f"{l['mes']:%Y-%m}": float(l["total"]) for l in linhas}


def informes_x_salario(usuario) -> list[dict]:
    """Linhas da tabela "Informes × Salário recebido", do mês mais novo para o mais antigo."""
    recebido = salario_por_mes(usuario)
    linhas = []
    for informe in Informe.objects.filter(usuario=usuario).order_by("-mes"):
        nf = valor_nf(informe)
        lancado = recebido.get(informe.mes)
        diferenca = None if lancado is None else round(lancado - nf, 2)
        linhas.append({"informe": informe, "horas": hhmm(informe.horas), "valor_nf": nf,
                        "nf_do_informe": informe.valor_nf is not None, "salario": lancado,
                        "diferenca": diferenca, "confere": diferenca is not None and abs(diferenca) < 0.02,
                        "diferenca_texto": None if diferenca is None else ("+" if diferenca > 0 else "−") + brl(abs(diferenca))})
    return linhas


def base_previsao(usuario) -> Informe | None:
    """O informe mais recente de verdade (valor hora e plano da previsão); sem nenhum, o mais recente."""
    informes = Informe.objects.filter(usuario=usuario).order_by("-mes")
    return informes.filter(previsao=False).first() or informes.first()


def meses_previsao(hoje: date) -> list[str]:
    """Do mês atual até dezembro."""
    return [f"{hoje.year}-{m:02d}" for m in range(hoje.month, 13)]


@transaction.atomic
def prever_ate_dezembro(usuario, horas: float, valor_hora: Decimal, plano: Decimal, hoje: date) -> dict:
    """Para cada mês do atual até dezembro sem informe de verdade: grava o informe de previsão e o Salário
    previsto (Entrada, não pago, dia 05), só se o mês não tiver Salário ou se o que existe for previsão."""
    reais = set(Informe.objects.filter(usuario=usuario, previsao=False).values_list("mes", flat=True))
    ultimo_salario = (Lancamento.objects.filter(usuario=usuario, movimentacao=ENTRADA, categoria=CATEGORIA_SALARIO)
                      .exclude(descricao__contains=MARCA_PREVISAO).order_by("-data", "-id").first())
    resultado = {"meses": [], "criados": 0, "atualizados": 0, "mantidos": [], "com_informe": []}
    for mes in meses_previsao(hoje):
        if mes in reais:
            resultado["com_informe"].append(mes)
            continue
        informe, _ = Informe.objects.update_or_create(usuario=usuario, mes=mes, defaults=dict(
            horas=float(horas), horas_sobreaviso=0, valor_hora=valor_hora, desconto=plano, valor_nf=None,
            previsao=True, observacao=f"Previsão: {horas:g} h/mês".replace(".", ",")))
        resultado["meses"].append(mes)

        ano, m = int(mes[:4]), int(mes[5:])
        salarios = list(Lancamento.objects.filter(usuario=usuario, movimentacao=ENTRADA, categoria=CATEGORIA_SALARIO,
                                                  data__year=ano, data__month=m).order_by("id"))
        if any(MARCA_PREVISAO not in (s.descricao or "") for s in salarios):
            resultado["mantidos"].append(mes)  # já tem Salário de verdade: nunca sobrescrever
            continue
        valor = round(informe.total_calculado, 2)
        sufixo = f" · {MARCA_PREVISAO} {horas:g} h".replace(".", ",")
        if salarios:  # atualiza a previsão anterior (o Pago e a data continuam como estão)
            previsto = salarios[0]
            prefixo = (previsto.descricao.split(f" · {MARCA_PREVISAO}")[0]
                       if f" · {MARCA_PREVISAO}" in previsto.descricao else "Pagamento PJ")
            if financeiro.atualizar_lancamento(previsto, {"valor": valor, "descricao": prefixo + sufixo},
                                               origem=ORIGEM_PREVISAO):
                resultado["atualizados"] += 1
        else:
            financeiro.criar_lancamento(usuario, dict(
                data=date(ano, m, 5), movimentacao=ENTRADA, categoria=CATEGORIA_SALARIO,
                banco=ultimo_salario.banco if ultimo_salario else "", valor=valor, pago=False,
                descricao="Pagamento PJ" + sufixo), origem=ORIGEM_PREVISAO)
            resultado["criados"] += 1
    return resultado

