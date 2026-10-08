"""Gráficos Plotly montados no servidor e desenhados no navegador ({% grafico %} + static/js/app.js).

Cores do tema (texto, grade, fundo) e a troca da paleta no modo escuro são aplicadas no navegador; aqui só
entram as cores das séries. Paleta categórica validada para daltonismo: a cor segue a série, nunca o ranking.
"""
from __future__ import annotations

import plotly.graph_objects as go

PALETA = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
CINZA_OUTROS = "#9a9893"
# Cores fixas de significado (as mesmas da tabela do Resumo).
ENTRADA, SAIDA, SALDO, A_PAGAR = "#137333", "#b3261e", "#1a56db", "#b45309"


def estilizar(fig: go.Figure, altura: int = 340, moeda: bool = True) -> go.Figure:
    fig.update_layout(
        template="none", height=altura, separators=",.", margin=dict(l=0, r=0, t=30, b=0),
        bargap=0.35, bargroupgap=0.08, barcornerradius=4, autosize=True,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0, title_text="", traceorder="normal"),
        hoverlabel=dict(namelength=-1), xaxis_title=None, yaxis_title=None,
    )
    fig.update_xaxes(showgrid=False, automargin=True)
    fig.update_yaxes(showgrid=True, zeroline=False, automargin=True,
                     **(dict(tickprefix="R$ ", tickformat=",.0f") if moeda else {}))
    fig.update_traces(marker_line_width=1, selector=dict(type="bar"))
    return fig


def barras_horizontais(rotulos: list[str], valores: list[float], altura: int = 340, dica: str = "",
                       cor: str = PALETA[0], customdata=None) -> go.Figure:
    fig = go.Figure(go.Bar(
        x=valores, y=rotulos, orientation="h", marker_color=cor, customdata=customdata,
        hovertemplate="%{y}<br>R$ %{x:,.2f}" + (f"<br><i>{dica}</i>" if dica else "") + "<extra></extra>",
    ))
    estilizar(fig, altura)
    fig.update_xaxes(showgrid=True, tickprefix="R$ ", tickformat=",.0f")
    fig.update_yaxes(showgrid=False, tickprefix="", autorange="reversed")
    return fig


def mini(valores: list[float], tipo: str = "area", cor: str = PALETA[0]) -> go.Figure:
    """Minigráfico dos cartões (sem eixos)."""
    if tipo == "bar":
        traco = go.Bar(y=valores, marker_color=cor, hoverinfo="skip")
    else:
        r, g, b = (int(cor[i:i + 2], 16) for i in (1, 3, 5))
        traco = go.Scatter(y=valores, mode="lines", line=dict(color=cor, width=2), fill="tozeroy",
                           fillcolor=f"rgba({r},{g},{b},0.12)", hoverinfo="skip")
    fig = go.Figure(traco)
    fig.update_layout(template="none", height=34, margin=dict(l=0, r=0, t=0, b=0), showlegend=False,
                      bargap=0.2, barcornerradius=2)
    fig.update_xaxes(visible=False)
    fig.update_yaxes(visible=False)
    return fig


def para_json(fig: go.Figure) -> str:
    """JSON enxuto para o atributo data-figura (sem o template padrão do Plotly)."""
    fig.layout.template = None
    return fig.to_json(engine="json")
