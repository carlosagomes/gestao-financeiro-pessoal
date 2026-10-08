"""Gráficos das páginas Notas Paraná e Produtos (figuras Plotly; o tema é aplicado no navegador)."""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

from core.categorias import OUTROS
from core.graficos import CINZA_OUTROS, PALETA, barras_horizontais, estilizar
from notas.consultas import DIVISAO, Periodo, rotulo_mes

ALTA, QUEDA = "#b3261e", "#137333"  # as mesmas cores de Saída e Entrada do Resumo
OUTRAS = "Outras"


def altura_barras(n: int, minimo: int = 200, por_barra: int = 30) -> int:
    return max(minimo, 40 + por_barra * n)


def rotulos_curtos(nomes, tamanho: int = 32) -> list[str]:
    """Rótulos do eixo cortados com "…" e sem repetição (rótulos iguais viram a mesma barra no Plotly)."""
    vistos, saida = {}, []
    for nome in nomes:
        rotulo = nome if len(nome) <= tamanho else nome[:tamanho - 1].rstrip() + "…"
        vistos[rotulo] = vistos.get(rotulo, 0) + 1
        saida.append(rotulo if vistos[rotulo] == 1 else f"{rotulo} ({vistos[rotulo]})")
    return saida


def poucas_barras(fig: go.Figure, n: int) -> go.Figure:
    """Com uma ou duas barras o Plotly as engorda até ocupar o gráfico: afina."""
    return fig.update_layout(bargap=0.65) if n <= 2 else fig


def barras(serie: pd.Series, dica: str = "", cores: dict[str, str] | None = None, altura: int | None = None,
           chaves: list[str] | None = None, tamanho: int = 32) -> go.Figure:
    """Barras horizontais em que o clique manda o nome inteiro (ou `chaves`, ex.: o grupo do produto)."""
    nomes = [str(n) for n in serie.index]
    fig = barras_horizontais(rotulos_curtos(nomes, tamanho), serie.round(2).tolist(),
                             altura=altura or altura_barras(len(nomes)),
                             customdata=[[c, n] for c, n in zip(chaves or nomes, nomes)])
    poucas_barras(fig, len(nomes))
    fig.update_traces(hovertemplate="%{customdata[1]}<br>R$ %{x:,.2f}" + (f"<br><i>{dica}</i>" if dica else "")
                      + "<extra></extra>")
    if cores is not None:
        fig.update_traces(marker_color=[cores.get(n, CINZA_OUTROS) for n in nomes])
    return fig


def consumo_por_mes(notas: pd.DataFrame, cores: dict[str, str], periodo: Periodo) -> go.Figure:
    """Barras empilhadas por categoria: as de cor fixa e o resto em "Outros"."""
    principais = list(cores)
    grupo = notas["categoria"].where(notas["categoria"].isin(principais), OUTROS)
    eixo = pd.period_range(periodo.inicio, periodo.fim, freq="M")
    tabela = (notas.assign(grupo=grupo)
              .pivot_table(index="mes", columns="grupo", values="valor_total", aggfunc="sum", fill_value=0)
              .reindex(eixo, fill_value=0))
    rotulos = [rotulo_mes(m) for m in eixo]
    fig = go.Figure()
    for g in [*principais, OUTROS]:
        if g in tabela.columns:
            fig.add_bar(x=rotulos, y=tabela[g].round(2), name=g, marker_color=cores.get(g, CINZA_OUTROS),
                        hovertemplate="%{fullData.name} em %{x}<br>R$ %{y:,.2f}<extra></extra>")
    fig.update_layout(barmode="stack")
    return estilizar(fig, 340)


def linha_do_tempo(linhas: pd.DataFrame, todas: pd.DataFrame, tipo: str, grao: str, inicio: pd.Period,
                   fim: pd.Period) -> go.Figure:
    if grao == "dia":
        dia = linhas.groupby(linhas["data"].dt.normalize()).agg(
            valor=("valor", "sum"), compras=("nota_id", "nunique"),
            lojas=("loja", lambda s: ", ".join(dict.fromkeys(s))[:80]))
        fig = go.Figure(go.Bar(
            x=dia.index, y=dia["valor"].round(2), marker_color=PALETA[0],
            customdata=list(zip(dia["compras"], dia["lojas"])),
            hovertemplate="%{x|%d/%m/%Y}<br>R$ %{y:,.2f} · %{customdata[0]} compra(s)<br>%{customdata[1]}"
                          "<extra></extra>"))
        estilizar(fig, 300).update_xaxes(type="date", tickformat="%d/%m/%y")
        # Um ano tem ~365 barras de um dia: sem o contorno e com largura mínima, para não virarem fios.
        dia_ms = 86_400_000
        largura = max(0.8 * dia_ms, (dia.index.max() - dia.index.min()).total_seconds() * 1000 / 300)
        fig.update_traces(width=largura, marker_line_width=0)
        return fig

    # Cor fixa para as lojas (ou formas) que mais pesam no histórico todo: trocar o período não repinta.
    divisao = DIVISAO[tipo]
    principais = todas.groupby(divisao)["valor"].sum().sort_values(ascending=False, kind="stable").index[:6].tolist()
    cores = dict(zip(principais, PALETA))
    if grao == "mes":
        eixo, quando = pd.period_range(inicio, fim, freq="M"), linhas["mes"]
        rotulos = [rotulo_mes(m) for m in eixo]
    else:
        eixo, quando = range(inicio.year, fim.year + 1), linhas["data"].dt.year
        rotulos = [str(a) for a in eixo]
    tabela = (linhas.assign(grupo=linhas[divisao].where(linhas[divisao].isin(principais), OUTRAS), quando=quando)
              .pivot_table(index="quando", columns="grupo", values="valor", aggfunc="sum", fill_value=0)
              .reindex(eixo, fill_value=0))
    fig = go.Figure()
    for g in [*principais, OUTRAS]:
        if g in tabela.columns:
            fig.add_bar(x=rotulos, y=tabela[g].round(2), name=g, marker_color=cores.get(g, CINZA_OUTROS),
                        hovertemplate="%{fullData.name}<br>%{x}: R$ %{y:,.2f}<extra></extra>")
    fig.update_layout(barmode="stack")
    return estilizar(fig, 300)


def variacoes(parte: pd.DataFrame, cor: str) -> go.Figure:
    """Produtos que mais subiram ou caíram (variação %); o clique manda o grupo do produto."""
    fig = go.Figure(go.Bar(
        x=(parte["variacao"] * 100).round(2), y=rotulos_curtos(parte["produto"]), orientation="h", marker_color=cor,
        customdata=list(zip(parte.index, parte["primeiro"].round(2), parte["ultimo"].round(2), parte["compras"],
                            parte["produto"])),
        hovertemplate="%{customdata[4]}<br>%{x:+.1f}%: de R$ %{customdata[1]:,.2f} para R$ %{customdata[2]:,.2f}"
                      "<br>%{customdata[3]} compras<br><i>clique para ver o histórico</i><extra></extra>"))
    estilizar(fig, altura_barras(len(parte), 160), moeda=False)
    poucas_barras(fig, len(parte))
    fig.update_xaxes(showgrid=True, ticksuffix="%", tickformat=",.0f")
    fig.update_yaxes(showgrid=False, autorange="reversed")
    return fig


def cores_lojas(lojas: list[str]) -> dict[str, str]:
    """A cor segue a loja com mais compras do produto (ordem fixa da paleta; as demais em cinza)."""
    return dict(zip(lojas, PALETA))


def preco_no_tempo(hist: pd.DataFrame, lojas: list[str], cores: dict[str, str]) -> go.Figure:
    fig = go.Figure()
    for loja in lojas:
        parte = hist[hist["loja"] == loja]
        fig.add_scatter(
            x=parte["data"], y=parte["preco"].round(4), name=loja, mode="lines+markers",
            line=dict(width=2, color=cores.get(loja, CINZA_OUTROS)), marker=dict(size=8),
            customdata=list(zip(parte["descricao"], parte["quantidade"], parte["unidade"])),
            hovertemplate="%{fullData.name}<br>%{x|%d/%m/%Y}: R$ %{y:,.2f}<br>%{customdata[0]} · "
                          "%{customdata[1]:,.3~f} %{customdata[2]}<extra></extra>")
    estilizar(fig, 340, moeda=False).update_yaxes(tickprefix="R$ ", tickformat=",.2f")
    meses = (hist["data"].max() - hist["data"].min()).days / 30
    if meses >= 2:
        fig.update_xaxes(type="date", tickformat="%m/%y", dtick="M1" if meses <= 18 else "M3")
    else:
        fig.update_xaxes(type="date", tickformat="%d/%m")
    return fig


def ultimo_por_loja(hist: pd.DataFrame, cores: dict[str, str]) -> go.Figure:
    ultimos = hist.groupby("loja").last().sort_values("preco", kind="stable")  # hist vem em ordem de data
    fig = barras_horizontais(ultimos.index.tolist(), ultimos["preco"].round(2).tolist(),
                             altura=altura_barras(len(ultimos), 130, 34))
    poucas_barras(fig, len(ultimos))
    fig.update_traces(marker_color=[cores.get(loja, CINZA_OUTROS) for loja in ultimos.index],  # a cor da linha
                      customdata=ultimos["data"].dt.strftime("%d/%m/%Y").tolist(),
                      hovertemplate="%{y}<br>R$ %{x:,.2f} em %{customdata}<extra></extra>")
    fig.update_xaxes(tickformat=",.2f")
    return fig
