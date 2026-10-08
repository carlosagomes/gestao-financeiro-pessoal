"""Filtros e tags dos templates: {% load gfp %}. Dados para o JavaScript vão pelo |json_script do Django."""
from django import template
from django.utils.html import format_html

from core import formatos

register = template.Library()


@register.filter
def brl(valor):
    return formatos.brl(valor)


@register.filter
def brl_sem_simbolo(valor):
    return formatos.brl(valor, simbolo=False)


@register.filter
def pct(valor, casas=1):
    return formatos.pct(valor, int(casas))


@register.filter
def qtde(valor):
    return formatos.qtde(valor)


@register.filter
def mes_rotulo(ref):
    return formatos.mes_rotulo(ref) if ref else ""


@register.filter
def mes_curto(ref):
    return formatos.mes_rotulo(ref, curto=True) if ref else ""


@register.simple_tag
def grafico(fig_json: str, altura: int = 340, clique: str = "", classe: str = ""):
    """Gráfico Plotly renderizado no navegador (static/js/app.js). `fig_json` vem de core.graficos.para_json.
    `clique`: URL que recebe o rótulo clicado (?alvo=...) e abre o resultado no modal."""
    return format_html(
        '<div class="grafico {}" style="min-height:{}px" data-figura="{}" data-clique="{}"></div>',
        classe, altura, fig_json, clique)


@register.simple_tag
def icone(nome: str, classe: str = ""):
    """Ícone Material Symbols Rounded (fonte carregada no base.html)."""
    return format_html('<span class="icone {}" aria-hidden="true">{}</span>', classe, nome)
