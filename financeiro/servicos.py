"""Alterações de lançamentos sempre com histórico (quem/onde mudou o quê).

Use estas funções em vez de `.save()` direto em qualquer lugar que mexa em lançamento existente: a tela,
os botões, a conciliação das contas de água e luz, a previsão do PJ.
"""
from __future__ import annotations

import calendar
from datetime import date
from decimal import Decimal

from django.db import transaction

from financeiro.models import ENTRADA, SAIDA, HistoricoLancamento, Lancamento

CAMPOS = ("data", "movimentacao", "banco", "categoria", "descricao", "valor", "pago")


def _texto(valor) -> str | None:
    if valor is None:
        return None
    if isinstance(valor, bool):
        return "sim" if valor else "não"
    if isinstance(valor, Decimal):
        return f"{valor:.2f}"
    if isinstance(valor, date):
        return valor.isoformat()
    return str(valor)


def normalizar(campos: dict) -> dict:
    """Valida e converte os campos vindos da tela/API. Levanta ValueError com mensagem para o usuário."""
    saida = {}
    for nome, valor in campos.items():
        if nome not in CAMPOS:
            continue
        if nome == "data":
            valor = valor if isinstance(valor, date) else date.fromisoformat(str(valor)[:10])
        elif nome == "movimentacao":
            if valor not in (ENTRADA, SAIDA):
                raise ValueError("Movimentação deve ser Entrada ou Saída")
        elif nome == "valor":
            valor = Decimal(str(valor).replace(",", ".")).quantize(Decimal("0.01"))
            if valor < 0:
                raise ValueError("Valor não pode ser negativo")
        elif nome == "pago":
            valor = valor in (True, 1, "1", "true", "True", "sim", "on")
        elif nome in ("categoria",):
            valor = str(valor or "").strip()
            if not valor:
                raise ValueError("Escolha a categoria")
        else:
            valor = str(valor or "").strip()
        saida[nome] = valor
    return saida


def registrar(usuario, lancamento_id: int, antes: dict, depois: dict, origem: str) -> None:
    HistoricoLancamento.objects.bulk_create([
        HistoricoLancamento(usuario=usuario, lancamento_id=lancamento_id, campo=c, antes=_texto(antes.get(c)),
                            depois=_texto(v), origem=origem)
        for c, v in depois.items() if _texto(antes.get(c)) != _texto(v)
    ])


@transaction.atomic
def criar_lancamento(usuario, campos: dict, origem: str = "tela") -> Lancamento:
    dados = normalizar(campos)
    faltando = [c for c in ("data", "movimentacao", "categoria", "valor") if c not in dados]
    if faltando:
        raise ValueError("Preencha: " + ", ".join(faltando))
    lancamento = Lancamento.objects.create(usuario=usuario, **dados)
    HistoricoLancamento.objects.create(usuario=usuario, lancamento_id=lancamento.id, campo="criado",
                                       depois=f"{lancamento.data} · {lancamento.categoria} · {lancamento.valor}",
                                       origem=origem)
    return lancamento


@transaction.atomic
def atualizar_lancamento(lancamento: Lancamento, campos: dict, origem: str = "tela") -> dict:
    """Grava só o que mudou e devolve {campo: (antes, depois)}."""
    dados = normalizar(campos)
    antes = {c: getattr(lancamento, c) for c in dados}
    mudou = {c: v for c, v in dados.items() if _texto(antes[c]) != _texto(v)}
    if not mudou:
        return {}
    for campo, valor in mudou.items():
        setattr(lancamento, campo, valor)
    lancamento.save(update_fields=[*mudou, "atualizado_em"])
    registrar(lancamento.usuario, lancamento.id, antes, mudou, origem)
    return {c: (antes[c], v) for c, v in mudou.items()}


@transaction.atomic
def excluir_lancamentos(usuario, ids, origem: str = "tela") -> int:
    lancamentos = list(Lancamento.objects.filter(usuario=usuario, id__in=list(ids)))
    HistoricoLancamento.objects.bulk_create([
        HistoricoLancamento(usuario=usuario, lancamento_id=l.id, campo="excluído",
                            antes=f"{l.data} · {l.movimentacao} · {l.categoria} · {l.descricao} · {l.valor}",
                            origem=origem) for l in lancamentos])
    Lancamento.objects.filter(usuario=usuario, id__in=[l.id for l in lancamentos]).delete()
    return len(lancamentos)


def marcar_pagos(usuario, ids, origem: str = "botão marcar como pagas") -> int:
    total = 0
    for lancamento in Lancamento.objects.filter(usuario=usuario, id__in=list(ids), pago=False):
        total += bool(atualizar_lancamento(lancamento, {"pago": True}, origem))
    return total


def usuario_mexeu_no_pago(lancamento: Lancamento, origens_automaticas: tuple[str, ...]) -> bool:
    """O usuário já decidiu o Pago deste lançamento? Então nada automático mexe mais nele."""
    return HistoricoLancamento.objects.filter(usuario=lancamento.usuario, lancamento_id=lancamento.id,
                                              campo="pago").exclude(origem__in=origens_automaticas).exists()


@transaction.atomic
def copiar_mes(usuario, ano: int, mes: int, origem: str = "copiar mês") -> list[Lancamento]:
    """Cria no mês os lançamentos do mês anterior (mesmo dia, limitado ao fim do mês), todos como não pagos."""
    ano_ant, mes_ant = (ano, mes - 1) if mes > 1 else (ano - 1, 12)
    ultimo_dia = calendar.monthrange(ano, mes)[1]
    return [
        criar_lancamento(usuario, {**{c: getattr(l, c) for c in CAMPOS},
                                   "data": date(ano, mes, min(l.data.day, ultimo_dia)), "pago": False}, origem)
        for l in Lancamento.objects.filter(usuario=usuario, data__year=ano_ant, data__month=mes_ant).order_by("id")
    ]
