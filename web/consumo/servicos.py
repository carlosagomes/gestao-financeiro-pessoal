"""Faturas de água (Sanepar) e luz (Copel): gravação e conciliação com os lançamentos do mês.

Conciliação: cada fatura ajusta o lançamento da categoria (Água/Luz) do mês correspondente.
- Sanepar: fatura de referência M+1 = lançamento do mês M (defasagem 1).
- Copel: fatura de referência M = lançamento do mês M (defasagem 0).
Só mexe em meses que já têm lançamentos, e só quando há exatamente um lançamento da categoria no mês.
O "Pago": a fatura paga pode marcar o lançamento como pago, mas nunca desmarca, e não mexe em lançamento
cujo Pago o usuário já decidiu na tela.
"""
from __future__ import annotations

import hashlib
import re
import tempfile
from datetime import date, timedelta
from pathlib import Path

from django.core.files.base import ContentFile
from django.db import transaction

from core.conversao import campos_do_modelo
from core.formatos import somar_meses
from consumo.leitores import copel_pdf, sanepar_pdf
from consumo.models import FaturaCopel, FaturaSanepar
from financeiro import servicos as financeiro
from financeiro.models import SAIDA, Lancamento

ORIGEM_SANEPAR, ORIGEM_COPEL = "Sanepar", "Copel"


def _anexar(fatura, pdf: bytes | None, nome: str | None) -> None:
    if pdf:
        if fatura.pdf:
            fatura.pdf.delete(save=False)
        fatura.pdf.save(nome or f"{fatura.referencia}.pdf", ContentFile(pdf), save=False)


@transaction.atomic
def salvar_fatura_sanepar(usuario, dados: dict, pdf: bytes | None = None, nome_pdf: str | None = None) -> FaturaSanepar:
    campos = campos_do_modelo(FaturaSanepar, dados, ignorar=("id", "usuario", "importado_em"), manter_vazios=False)
    fatura, _ = FaturaSanepar.objects.update_or_create(usuario=usuario, referencia=campos.pop("referencia"),
                                                       defaults=campos)
    _anexar(fatura, pdf, nome_pdf)
    fatura.save()
    return fatura


@transaction.atomic
def salvar_fatura_copel(usuario, dados: dict, pdf: bytes | None = None, nome_pdf: str | None = None) -> FaturaCopel:
    """Campos ausentes (ex.: só a linha do histórico, sem o PDF) não apagam os já salvos."""
    campos = campos_do_modelo(FaturaCopel, dados, ignorar=("id", "usuario", "importado_em"), manter_vazios=False)
    fatura, _ = FaturaCopel.objects.update_or_create(usuario=usuario, numero_fatura=campos.pop("numero_fatura"),
                                                     defaults=campos)
    _anexar(fatura, pdf, nome_pdf or f"{fatura.referencia}-{fatura.numero_fatura}.pdf")
    fatura.save()
    return fatura


def completar_consumo_sanepar(usuario) -> int:
    """Faturas sem leitura no PDF (as de 2025) ganham o consumo do gráfico de histórico das faturas seguintes."""
    historico: dict[str, int] = {}
    for detalhes in FaturaSanepar.objects.filter(usuario=usuario).values_list("detalhes", flat=True):
        historico.update((detalhes or {}).get("historico_m3", {}))
    total = 0
    for fatura in FaturaSanepar.objects.filter(usuario=usuario, consumo_m3__isnull=True):
        if fatura.referencia in historico:
            fatura.consumo_m3 = historico[fatura.referencia]
            fatura.save(update_fields=["consumo_m3"])
            total += 1
    return total


def conciliar_conta(usuario, modelo, categoria: str, defasagem: int, nome: str, unidade: str, campo_consumo: str,
                    pago_se: str) -> list[dict]:
    mudancas = []
    por_ref: dict[str, dict] = {}
    for f in modelo.objects.filter(usuario=usuario).order_by("referencia"):
        r = por_ref.setdefault(f.referencia, {"total": 0, "pago": True, "consumo": None, "vencimento": None})
        r["total"] += float(f.valor_total)
        r["pago"] = r["pago"] and f.situacao == pago_se
        consumo = getattr(f, campo_consumo)
        if consumo is not None:
            r["consumo"] = max(r["consumo"] or 0, consumo)
        if f.vencimento and (r["vencimento"] is None or f.vencimento > r["vencimento"]):
            r["vencimento"] = f.vencimento
    for ref, r in por_ref.items():
        mes = somar_meses(ref, -defasagem)
        ano, m = int(mes[:4]), int(mes[5:])
        do_mes = Lancamento.objects.filter(usuario=usuario, data__year=ano, data__month=m)
        if not do_mes.exists():
            continue
        contas = list(do_mes.filter(categoria=categoria, movimentacao=SAIDA))
        descricao = (f"{nome} {ref[5:]}/{ref[:4]}" + (f" · {r['consumo']:g} {unidade}" if r["consumo"] is not None else "")
                     + (f" · vence {r['vencimento']:%d/%m}" if r["vencimento"] else ""))
        total = round(r["total"], 2)
        if not contas:
            financeiro.criar_lancamento(usuario, dict(data=date(ano, m, 5), movimentacao=SAIDA, categoria=categoria,
                                                      descricao=descricao, valor=total, pago=r["pago"]), origem=nome)
            mudancas.append(dict(mes=mes, referencia=ref, antes=None, depois=total, acao="criado"))
        elif len(contas) == 1:
            lancamento = contas[0]
            campos = {"valor": total, "descricao": descricao}
            if r["pago"] and not lancamento.pago and not financeiro.usuario_mexeu_no_pago(
                    lancamento, (ORIGEM_SANEPAR, ORIGEM_COPEL)):
                campos["pago"] = True
            antes = float(lancamento.valor)
            if financeiro.atualizar_lancamento(lancamento, campos, origem=nome):
                mudancas.append(dict(mes=mes, referencia=ref, antes=antes, depois=total, acao="atualizado"))
        else:
            mudancas.append(dict(mes=mes, referencia=ref, antes=sum(float(c.valor) for c in contas), depois=total,
                                 acao=f"ignorado: {len(contas)} lançamentos de {categoria} no mês"))
    return mudancas


def conciliar_agua(usuario, categoria: str = "Água") -> list[dict]:
    return conciliar_conta(usuario, FaturaSanepar, categoria, 1, ORIGEM_SANEPAR, "m³", "consumo_m3", "Paga")


def conciliar_luz(usuario, categoria: str = "Luz") -> list[dict]:
    return conciliar_conta(usuario, FaturaCopel, categoria, 0, ORIGEM_COPEL, "kWh", "consumo_kwh", "Quitada")


def proxima_conta_copel(usuario) -> tuple[str, date] | None:
    """(referência, data) da próxima conta de luz: a Copel emite no dia da leitura, e a última fatura traz a
    próxima leitura. Se a data já passou e a referência não existe, a conta nova saiu e falta importar."""
    ultima = FaturaCopel.objects.filter(usuario=usuario).order_by("-referencia").first()
    if not ultima:
        return None
    proxima = (ultima.detalhes or {}).get("proxima_leitura")
    if proxima:
        data = date.fromisoformat(proxima)
    elif ultima.data_leitura:
        data = ultima.data_leitura + timedelta(days=30)
    else:
        return None
    return somar_meses(ultima.referencia, 1), data


# ---------- PDF enviado pela tela ----------------------------------------------------------------------

class PdfInvalido(ValueError):
    """Erro de leitura do PDF enviado, com a mensagem pronta para o usuário."""


def _texto(pdf: bytes) -> str:
    if not pdf.startswith(b"%PDF"):
        raise PdfInvalido("O arquivo não é um PDF.")
    try:
        return sanepar_pdf.texto_pdf(pdf)
    except Exception:
        raise PdfInvalido("Não consegui abrir este PDF. Ele pode estar corrompido ou protegido por senha.")


@transaction.atomic
def importar_pdf_sanepar(usuario, pdf: bytes) -> tuple[FaturaSanepar, list[dict]]:
    """Lê a 2ª via da Sanepar, grava a fatura com o PDF e ajusta o lançamento de Água."""
    texto = _texto(pdf)
    try:
        dados = sanepar_pdf.ler_fatura(texto)
    except ValueError as erro:
        if "não achei" in str(erro):
            raise PdfInvalido("Este PDF não parece ser uma conta da Sanepar. Envie a 2ª via baixada no site "
                              "ou no app da Sanepar.")
        raise PdfInvalido(f"Os valores da conta não fecham: {erro}.")
    atual = FaturaSanepar.objects.filter(usuario=usuario, referencia=dados["referencia"]).first()
    if atual and atual.matricula and dados.get("matricula") and atual.matricula != dados["matricula"]:
        raise PdfInvalido(f"Este PDF é da matrícula {dados['matricula']}, mas a sua fatura de "
                          f"{dados['referencia'][5:]}/{dados['referencia'][:4]} é da matrícula {atual.matricula}.")
    if atual and atual.situacao == "Paga":  # um PDF antigo (emitido antes do pagamento) não desfaz a quitação
        dados.pop("situacao", None)
    fatura = salvar_fatura_sanepar(usuario, dados, pdf=pdf)
    completar_consumo_sanepar(usuario)
    fatura.refresh_from_db()
    return fatura, conciliar_agua(usuario)


def ler_pdf_copel(pdf: bytes) -> dict:
    """Campos do PDF da Copel no formato de salvar_fatura_copel. O número da fatura (o mesmo da tabela do site)
    vem do código de barras ("FAT-01-<número>"); a referência, o vencimento e o total, da linha "MM/AAAA"."""
    texto = _texto(pdf)
    total = re.search(r"^(\d{2})/(\d{4}) (\d{2})/(\d{2})/(\d{4}) R\$\s?([\d.]+,\d{2})\s*$", texto, re.M)
    if "DANF3E" not in texto or not total:
        raise PdfInvalido("Este PDF não parece ser uma conta da Copel. Envie a 2ª via (DANF3E) baixada no site "
                          "da Copel.")
    with tempfile.NamedTemporaryFile(suffix=".pdf") as arquivo:
        arquivo.write(pdf)
        arquivo.flush()
        try:
            dados = copel_pdf.ler_fatura(Path(arquivo.name))
        except ValueError as erro:
            raise PdfInvalido(f"Os valores da conta não fecham: {erro}.")
        except Exception:
            raise PdfInvalido("Não reconheci os itens desta conta da Copel. Envie a 2ª via original, sem edição.")
    mes, ano, dia, mes_venc, ano_venc, valor = total.groups()
    numero = re.search(r"FAT-\d+-(\d+)", texto)
    return dict(
        dados,
        numero_fatura=numero[1] if numero else None,
        referencia=f"{ano}-{mes}",
        vencimento=f"{ano_venc}-{mes_venc}-{dia}",
        valor_total=float(valor.replace(".", "").replace(",", ".")),
        situacao="Quitada" if dados["detalhes"].get("arrecadada") else "Pendente",
    )


@transaction.atomic
def importar_pdf_copel(usuario, pdf: bytes) -> tuple[FaturaCopel, list[dict]]:
    """Lê a 2ª via da Copel, grava a fatura com o PDF e ajusta o lançamento de Luz."""
    dados = ler_pdf_copel(pdf)
    numero = dados.pop("numero_fatura")
    if not numero:  # sem código de barras: a mesma referência já importada, senão o número da nota fiscal
        mesma_ref = list(FaturaCopel.objects.filter(usuario=usuario, referencia=dados["referencia"])[:2])
        nota = (dados["detalhes"].get("nota_fiscal") or {}).get("numero")
        numero = (mesma_ref[0].numero_fatura if len(mesma_ref) == 1
                  else f"NF{nota}" if nota else "PDF" + hashlib.sha1(pdf).hexdigest()[:12])
    atual = FaturaCopel.objects.filter(usuario=usuario, numero_fatura=numero).first()
    if atual and atual.situacao == "Quitada":  # um PDF emitido antes do pagamento não desfaz a quitação
        dados.pop("situacao")
    fatura = salvar_fatura_copel(usuario, {"numero_fatura": numero, **dados}, pdf=pdf)
    return fatura, conciliar_luz(usuario)
