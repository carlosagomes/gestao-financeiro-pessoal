"""Formatos brasileiros usados em telas e gráficos."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

MESES = ["Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho", "Julho", "Agosto", "Setembro", "Outubro",
         "Novembro", "Dezembro"]
MESES_CURTOS = [m[:3] for m in MESES]


def brl(valor: float | Decimal | None, simbolo: bool = True) -> str:
    if valor is None or valor == "":  # "" = variável ausente no template
        return "—"
    texto = f"{float(valor):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {texto}" if simbolo else texto


def pct(valor: float | None, casas: int = 1, sinal: bool = True) -> str:
    if valor is None or valor == "":
        return "—"
    return f"{valor:{'+' if sinal else ''}.{casas}%}".replace(".", ",")


def qtde(valor: float | Decimal | None) -> str:
    if valor is None or valor == "":
        return "—"
    return f"{float(valor):.3f}".rstrip("0").rstrip(".").replace(".", ",")


def mes_rotulo(ref: str, curto: bool = False) -> str:
    """'2026-09' -> 'Setembro/2026' (ou 'Set/26')."""
    ano, mes = int(ref[:4]), int(ref[5:7])
    return f"{MESES_CURTOS[mes - 1]}/{ano % 100:02d}" if curto else f"{MESES[mes - 1]}/{ano}"


def somar_meses(ref: str, n: int) -> str:
    ano, mes = divmod(int(ref[:4]) * 12 + int(ref[5:7]) - 1 + n, 12)
    return f"{ano}-{mes + 1:02d}"


def mes_de(d: date) -> str:
    return f"{d.year}-{d.month:02d}"
