"""Consultas do Resumo e dos Lançamentos, sempre de UM usuário.

Regra da planilha: Entrada conta tudo; Saída só o que está pago; o resto das saídas é "A pagar";
Saldo = Entrada − Saída paga. As notas do Nota Paraná nunca entram aqui.
"""
from __future__ import annotations

import unicodedata
from collections import Counter
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.db.models import Count, Q, Sum
from django.db.models.functions import ExtractMonth
from django.utils import timezone

from core.formatos import MESES
from financeiro.models import ENTRADA, SAIDA, Banco, Categoria, Lancamento

ZERO = Decimal("0")
SITUACOES = {"pagas": True, "a-pagar": False}

_SOMAS = dict(
    entradas=Sum("valor", filter=Q(movimentacao=ENTRADA), default=ZERO),
    saidas=Sum("valor", filter=Q(movimentacao=SAIDA, pago=True), default=ZERO),
    a_pagar=Sum("valor", filter=Q(movimentacao=SAIDA, pago=False), default=ZERO),
    n=Count("id"),
    n_entradas=Count("id", filter=Q(movimentacao=ENTRADA)),
    n_saidas=Count("id", filter=Q(movimentacao=SAIDA)),
    n_pagas=Count("id", filter=Q(movimentacao=SAIDA, pago=True)),
    pendentes=Count("id", filter=Q(movimentacao=SAIDA, pago=False)),
)


def ordem_alfabetica(texto: str) -> str:
    return unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode().casefold()


def anos(usuario) -> list[int]:
    """Anos com lançamento, mais o atual."""
    return sorted({d.year for d in Lancamento.objects.filter(usuario=usuario).dates("data", "year")}
                  | {timezone.localdate().year})


def totais(lancamentos) -> dict:
    t = lancamentos.order_by().aggregate(**_SOMAS)
    t["saldo"] = t["entradas"] - t["saidas"]
    return t


def status_mes(ano: int, mes: int, linha: dict, hoje: date) -> str:
    """'' (sem lançamento), 'previsao' (mês futuro ou ainda sem as contas), 'pendente' ou 'lancado'."""
    if not linha["n"]:
        return ""
    if (ano, mes) > (hoje.year, hoje.month) or not linha["n_saidas"]:
        return "previsao"
    return "pendente" if linha["pendentes"] else "lancado"


def resumo_anual(usuario, ano: int) -> dict:
    """Os 12 meses do ano (como a aba Início da planilha) e o total anual."""
    hoje = timezone.localdate()
    por_mes = {l["mes"]: l for l in Lancamento.objects.filter(usuario=usuario, data__year=ano).order_by()
               .annotate(mes=ExtractMonth("data")).values("mes").annotate(**_SOMAS)}
    vazio = {c: (ZERO if c in ("entradas", "saidas", "a_pagar") else 0) for c in _SOMAS}
    meses = []
    for mes in range(1, 13):
        linha = {**vazio, **por_mes.get(mes, {})}
        linha.update(mes=mes, nome=MESES[mes - 1], saldo=linha["entradas"] - linha["saidas"],
                     status=status_mes(ano, mes, linha, hoje))
        meses.append(linha)
    total = {c: sum((m[c] for m in meses), ZERO) for c in ("entradas", "saidas", "a_pagar", "saldo")}
    total["pendentes"] = sum(m["pendentes"] for m in meses)
    return {"ano": ano, "meses": meses, "total": total, "tem_dados": any(m["n"] for m in meses)}


def saidas_por_categoria(usuario, ano: int) -> list[tuple[str, Decimal]]:
    return list(Lancamento.objects.filter(usuario=usuario, data__year=ano, movimentacao=SAIDA, pago=True)
                .order_by().values("categoria").annotate(total=Sum("valor")).order_by("-total", "categoria")
                .values_list("categoria", "total"))


# --- tela de lançamentos ---------------------------------------------------------------------

@dataclass
class Filtros:
    ano: int
    mes: int  # 0 = todos os meses
    movimentacao: str = ""  # "" = todas
    situacao: str = ""  # "", "pagas" ou "a-pagar"
    busca: str = ""

    def como_query(self) -> dict:
        return {"ano": self.ano, "mes": self.mes, "mov": self.movimentacao, "situacao": self.situacao,
                "busca": self.busca}


def inteiro(valor, padrao: int, minimo: int, maximo: int) -> int:
    try:
        numero = int(valor)
    except (TypeError, ValueError):
        return padrao
    return numero if minimo <= numero <= maximo else padrao


def ler_filtros(params) -> Filtros:
    """Filtros da URL (?ano=&mes=&mov=&situacao=&busca=); sem parâmetro, o mês atual."""
    hoje = timezone.localdate()
    movimentacao = params.get("mov", "")
    situacao = params.get("situacao", "")
    return Filtros(
        ano=inteiro(params.get("ano"), hoje.year, 1900, 2999),
        mes=inteiro(params.get("mes"), hoje.month, 0, 12),
        movimentacao=movimentacao if movimentacao in (ENTRADA, SAIDA) else "",
        situacao=situacao if situacao in SITUACOES else "",
        busca=params.get("busca", "").strip()[:100],
    )


def filtrar(usuario, f: Filtros):
    lancamentos = Lancamento.objects.filter(usuario=usuario, data__year=f.ano)
    if f.mes:
        lancamentos = lancamentos.filter(data__month=f.mes)
    if f.movimentacao:
        lancamentos = lancamentos.filter(movimentacao=f.movimentacao)
    if f.situacao:
        lancamentos = lancamentos.filter(pago=SITUACOES[f.situacao])
    if f.busca:
        lancamentos = lancamentos.filter(Q(descricao__icontains=f.busca) | Q(categoria__icontains=f.busca)
                                         | Q(banco__icontains=f.busca))
    # No mês, a ordem é a de lançamento (como na planilha); no ano todo, por data.
    return lancamentos.order_by("id") if f.mes else lancamentos.order_by("data", "id")


def opcoes(usuario) -> dict:
    """Categorias (por movimentação) e bancos: os cadastrados mais os já usados nos lançamentos."""
    categorias = {ENTRADA: set(), SAIDA: set()}
    for nome, mov in Categoria.objects.filter(usuario=usuario).values_list("nome", "movimentacao"):
        categorias[mov].add(nome)
    usados = Lancamento.objects.filter(usuario=usuario).order_by()
    for nome, mov in usados.values_list("categoria", "movimentacao").distinct():
        categorias[mov].add(nome)
    bancos = set(Banco.objects.filter(usuario=usuario).values_list("nome", flat=True))
    bancos |= set(usados.exclude(banco="").values_list("banco", flat=True).distinct())
    return {"categorias": {m: sorted(v, key=ordem_alfabetica) for m, v in categorias.items()},
            "bancos": sorted(bancos, key=ordem_alfabetica)}


def padrao_novo(usuario, f: Filtros) -> dict:
    """Valores da linha nova: hoje no mês atual, senão dia 05 do mês escolhido; banco mais usado."""
    hoje = timezone.localdate()
    data = hoje if (f.ano, f.mes or hoje.month) == (hoje.year, hoje.month) else date(f.ano, f.mes or 1, 5)
    movimentacao = f.movimentacao or SAIDA
    bancos = Counter(Lancamento.objects.filter(usuario=usuario).exclude(banco="").values_list("banco", flat=True))
    categorias = opcoes(usuario)["categorias"][movimentacao]
    preferida = "Outros" if movimentacao == SAIDA else "Outras entradas"
    return {"data": data.isoformat(), "movimentacao": movimentacao,
            "banco": bancos.most_common(1)[0][0] if bancos else "",
            "categoria": preferida if preferida in categorias or not categorias else categorias[0],
            "descricao": "", "valor": 0, "pago": f.situacao == "pagas"}


def mes_anterior(ano: int, mes: int) -> tuple[int, int]:
    return (ano, mes - 1) if mes > 1 else (ano - 1, 12)


def contexto(usuario, f: Filtros) -> dict:
    """O que a tela precisa além das linhas: linha nova padrão e o "copiar mês anterior"."""
    ctx = {"ano": f.ano, "mes": f.mes, "padrao": padrao_novo(usuario, f), "anterior": None}
    if f.mes:
        do_usuario = Lancamento.objects.filter(usuario=usuario)
        ano_ant, mes_ant = mes_anterior(f.ano, f.mes)
        ctx["anterior"] = {"nome": MESES[mes_ant - 1].lower(), "ano": ano_ant, "mes": mes_ant,
                           "qtd": do_usuario.filter(data__year=ano_ant, data__month=mes_ant).count()}
        ctx["qtd_mes"] = do_usuario.filter(data__year=f.ano, data__month=f.mes).count()
    return ctx


def linha(l: Lancamento) -> dict:
    """Lançamento no formato da tabela (Tabulator)."""
    return {"id": l.id, "data": l.data.isoformat(), "movimentacao": l.movimentacao, "banco": l.banco,
            "categoria": l.categoria, "descricao": l.descricao, "valor": float(l.valor), "pago": l.pago}
