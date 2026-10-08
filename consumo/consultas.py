"""Montagem das páginas de Água e Luz: KPIs, gráficos e a tabela faturas × lançamentos (sempre de UM usuário)."""
from __future__ import annotations

import re
from statistics import mean

import plotly.graph_objects as go
from django.db.models import Sum
from django.db.models.functions import TruncMonth

from consumo.models import FaturaCopel, FaturaSanepar
from core.formatos import brl, mes_rotulo, somar_meses
from core.graficos import CINZA_OUTROS, PALETA, estilizar, mini, para_json
from financeiro.models import SAIDA, Lancamento
from integracoes.models import Tarefa

# Itens da conta de luz em 4 grupos fixos (cor por grupo, nunca por ranking) + "Outros".
GRUPOS_COPEL = [("Energia", "ENERGIA ELET CONSUMO"), ("Uso do sistema", "ENERGIA ELET USO SISTEMA"),
                ("Bandeira tarifária", "B."), ("Iluminação pública", "ILUMIN")]
CORES_GRUPOS = {nome: PALETA[i] for i, (nome, _) in enumerate(GRUPOS_COPEL)} | {"Outros": CINZA_OUTROS}
CORES_BANDEIRA = {"verde": "verde", "amarela": "laranja", "vermelha": "vermelho"}


def lancado_por_mes(usuario, categoria: str) -> dict[str, float]:
    """Soma das saídas da categoria por mês ('AAAA-MM')."""
    linhas = (Lancamento.objects.filter(usuario=usuario, categoria=categoria, movimentacao=SAIDA)
              .annotate(mes=TruncMonth("data")).values("mes").annotate(total=Sum("valor")))
    return {f"{l['mes']:%Y-%m}": float(l["total"]) for l in linhas}


def conferencia(lancado: float | None, total: float, mes: str) -> dict:
    """Célula "Lançamento": confere com a fatura (✅) ou não (⚠️)."""
    if lancado is None:
        return {"valor": None, "mes": mes}
    return {"valor": lancado, "mes": mes, "confere": abs(lancado - total) < 0.005, "diferenca": lancado - total}


def sincronizacao(usuario, servico: str) -> dict:
    """Última busca automática concluída e se a mais recente deu erro."""
    tarefas = Tarefa.objects.filter(usuario=usuario, servico=servico)
    ultima_ok = tarefas.filter(status=Tarefa.OK).exclude(terminada_em=None).order_by("-terminada_em").first()
    recente = tarefas.exclude(status=Tarefa.CANCELADA).order_by("-criada_em").first()
    return {"ultima_ok": ultima_ok, "erro": recente if recente and recente.status == Tarefa.ERRO else None}


def _kpis(faturas, campo: str, unidade: str, casas: int) -> dict:
    ultima, recentes = faturas[-1], faturas[-6:]
    valores = [float(f.valor_total) for f in faturas]
    consumos = [getattr(f, campo) for f in recentes if getattr(f, campo) is not None]
    com_consumo = [f for f in recentes if (getattr(f, campo) or 0) > 0]
    kpis = {
        "ultima": ultima,
        "ultima_rotulo": f"Última fatura ({ultima.referencia[5:]}/{ultima.referencia[2:4]})",
        "ultima_detalhe": " · ".join(filter(None, [ultima.situacao, ultima.vencimento
                                                   and f"vence {ultima.vencimento:%d/%m/%Y}"])),
        "media": mean(valores[-6:]),
        "mini_valores": para_json(mini(valores)),
        "consumo_medio": (f"{mean(consumos):,.{casas}f} {unidade}".replace(",", "X").replace(".", ",")
                          .replace("X", ".")) if consumos else "—",
        "mini_consumo": para_json(mini([float(getattr(f, campo) or 0) for f in faturas], "bar")),
        "custo_unidade": (brl(sum(float(f.valor_total) for f in com_consumo)
                              / sum(getattr(f, campo) for f in com_consumo)) if com_consumo else "—"),
    }
    if len(faturas) > 1 and float(faturas[-2].valor_total):
        variacao = float(ultima.valor_total) / float(faturas[-2].valor_total) - 1
        kpis["delta"] = f"{variacao:+.1%}".replace(".", ",") + " vs anterior"
        kpis["delta_classe"] = "sobe-ruim" if variacao > 0.005 else "desce-bom" if variacao < -0.005 else "neutro"
    return kpis


def _eixo_x(fig: go.Figure) -> go.Figure:
    fig.update_xaxes(type="category")  # "09/26" não pode virar data
    return fig


def _grafico_consumo(rotulos: list[str], consumos: list, unidade: str, customdata=None, extra: str = "") -> str:
    fig = go.Figure(go.Bar(x=rotulos, y=consumos, marker_color=PALETA[0], customdata=customdata,
                           hovertemplate=f"<b>%{{x}}</b><br>%{{y:,.0f}} {unidade}{extra}<extra></extra>"))
    estilizar(fig, 320, moeda=False).update_yaxes(ticksuffix=f" {unidade}", tickformat=",d")
    return para_json(_eixo_x(fig))


# ---------- Água · Sanepar -----------------------------------------------------------------------------

def pagina_agua(usuario) -> dict:
    faturas = list(FaturaSanepar.objects.filter(usuario=usuario).order_by("referencia"))
    contexto = {"faturas": faturas, "sync": sincronizacao(usuario, "sanepar")}
    if not faturas:
        return contexto
    rotulos = [mes_rotulo(f.referencia, curto=True) for f in faturas]
    totais = [float(f.valor_total) for f in faturas]

    # Valor por fatura: água + esgoto + serviços (o que não vier separado no PDF entra em "Serviços e outros").
    agua = [float(f.valor_agua or 0) for f in faturas]
    esgoto = [float(f.valor_esgoto or 0) for f in faturas]
    partes = [("Água", agua), ("Esgoto", esgoto),
              ("Serviços e outros", [round(t - a - e, 2) for t, a, e in zip(totais, agua, esgoto)])]
    fig = go.Figure()
    for i, (nome, valores) in enumerate(partes):
        fig.add_bar(x=rotulos, y=valores, name=nome, marker_color=PALETA[i], customdata=totais,
                    hovertemplate=f"<b>%{{x}}</b><br>{nome}: R$ %{{y:,.2f}}<br>Total da fatura: R$ %{{customdata:,.2f}}"
                                  "<extra></extra>")
    fig.update_layout(barmode="stack")
    estilizar(fig, 320)

    contexto.update(
        kpis=_kpis(faturas, "consumo_m3", "m³", 1),
        fig_valores=para_json(_eixo_x(fig)),
        fig_consumo=_grafico_consumo(rotulos, [f.consumo_m3 for f in faturas], "m³"),
        faixas=faixas(faturas[-1]),
        **tabela_agua(usuario, faturas),
    )
    return contexto


def tabela_agua(usuario, faturas=None) -> dict:
    """Linhas da tabela, da mais nova para a mais antiga. A fatura de referência M+1 é o lançamento do mês M."""
    if faturas is None:
        faturas = list(FaturaSanepar.objects.filter(usuario=usuario).order_by("referencia"))
    lancado = lancado_por_mes(usuario, "Água")
    linhas = []
    for f in reversed(faturas):
        mes = somar_meses(f.referencia, -1)
        linhas.append({"fatura": f, "lancamento": conferencia(lancado.get(mes), float(f.valor_total), mes)})
    return {"linhas": linhas}


def faixas(fatura: FaturaSanepar) -> list[dict]:
    """Faixas de consumo da fatura: "RES MÍNIMO" traz água e esgoto; as demais, tarifa, água e esgoto."""
    saida = []
    for f in (fatura.detalhes or {}).get("faixas", []):
        valores = list(f.get("valores") or [])
        tarifa = valores.pop(0) if len(valores) == 3 else None
        saida.append({"faixa": f.get("faixa", ""), "volume": f.get("volume_m3"), "tarifa": tarifa,
                      "agua": valores[0] if valores else None, "esgoto": valores[1] if len(valores) > 1 else None})
    return saida


# ---------- Luz · Copel --------------------------------------------------------------------------------

def grupo_copel(item: str) -> str:
    for nome, trecho in GRUPOS_COPEL:
        if trecho in item:
            return nome
    return "Outros"


def bandeiras(texto: str) -> list[dict]:
    """'Amarela:22/12-31/12 Verde:01/01-15/01' -> [{nome: Amarela, periodo: 22/12 a 31/12, cor: laranja}, ...]"""
    saida = [{"nome": nome.strip().title(), "periodo": f"{de} a {ate}",
              "cor": next((c for k, c in CORES_BANDEIRA.items() if nome.lower().startswith(k)), "cinza")}
             for nome, de, ate in re.findall(r"([A-Za-zÀ-ú]+(?: ?P?\d)?):\s*(\d{2}/\d{2})-(\d{2}/\d{2})", texto or "")]
    if not saida and texto:
        saida = [{"nome": texto, "periodo": "", "cor": "cinza"}]
    return saida


def pagina_luz(usuario) -> dict:
    faturas = list(FaturaCopel.objects.filter(usuario=usuario).order_by("referencia", "numero_fatura"))
    contexto = {"faturas": faturas, "sync": sincronizacao(usuario, "copel")}
    if not faturas:
        return contexto
    rotulos = [mes_rotulo(f.referencia, curto=True) for f in faturas]
    totais = [float(f.valor_total) for f in faturas]

    # Do que é feita a conta: itens da nota fiscal somados por grupo (os impostos já estão dentro de cada item).
    por_grupo = {nome: [0.0] * len(faturas) for nome in CORES_GRUPOS}
    for i, f in enumerate(faturas):
        for item in (f.detalhes or {}).get("itens", []):
            por_grupo[grupo_copel(item.get("item", ""))][i] += float(item.get("valor") or 0)
    fig = go.Figure()
    for nome, valores in por_grupo.items():
        if any(valores):
            fig.add_bar(x=rotulos, y=[round(v, 2) for v in valores], name=nome, marker_color=CORES_GRUPOS[nome],
                        customdata=totais,
                        hovertemplate=f"<b>%{{x}}</b><br>{nome}: R$ %{{y:,.2f}}<br>Total da fatura: R$ "
                                      "%{customdata:,.2f}<extra></extra>")
    fig.update_layout(barmode="stack")
    estilizar(fig, 320)

    textos_bandeira = [", ".join(f"{b['nome']} ({b['periodo']})" if b["periodo"] else b["nome"]
                                 for b in bandeiras(f.bandeira)) or "—" for f in faturas]
    contexto.update(
        kpis=_kpis(faturas, "consumo_kwh", "kWh", 0),
        fig_itens=para_json(_eixo_x(fig)),
        tem_itens=bool(fig.data),
        fig_consumo=_grafico_consumo(rotulos, [f.consumo_kwh for f in faturas], "kWh", textos_bandeira,
                                     "<br>Bandeira: %{customdata}"),
        **tabela_luz(usuario, faturas),
    )
    return contexto


def tabela_luz(usuario, faturas=None) -> dict:
    """Linhas da tabela, da mais nova para a mais antiga. A fatura de referência M é o lançamento de Luz do mês M
    (várias faturas da mesma referência somam)."""
    if faturas is None:
        faturas = list(FaturaCopel.objects.filter(usuario=usuario).order_by("referencia", "numero_fatura"))
    lancado = lancado_por_mes(usuario, "Luz")
    total_ref: dict[str, float] = {}
    for f in faturas:
        total_ref[f.referencia] = total_ref.get(f.referencia, 0) + float(f.valor_total)
    linhas = []
    for f in reversed(faturas):
        tributos = sum(float(t.get("valor") or 0) for t in ((f.detalhes or {}).get("tributos") or {}).values())
        linhas.append({"fatura": f, "bandeiras": bandeiras(f.bandeira), "impostos": tributos or None,
                       "lancamento": conferencia(lancado.get(f.referencia), total_ref[f.referencia], f.referencia)})
    return {"linhas": linhas}
