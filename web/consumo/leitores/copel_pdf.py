"""Leitura do PDF da 2ª via da Copel (DANF3E, a nota fiscal de energia elétrica).

O texto extraído sai por colunas: cada campo é reconhecido pelo formato da linha ou pela posição relativa
a um rótulo. A soma dos itens precisa bater com o total; senão a leitura é recusada.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from consumo.leitores.sanepar_pdf import texto_pdf

DATA = r"(\d{2}/\d{2}/\d{4})"
MESES = {m: i for i, m in enumerate(["JAN", "FEV", "MAR", "ABR", "MAI", "JUN", "JUL", "AGO", "SET", "OUT",
                                       "NOV", "DEZ"], 1)}


def _num(texto: str) -> float:
    return float(texto.replace("R$", "").replace("%", "").replace(".", "").replace(",", ".").strip())


def _iso(data: str) -> str:
    d, m, a = data.split("/")
    return f"{a}-{m}-{d}"


def _bloco(linhas: list[str], inicio: int, padrao: str) -> list[str]:
    """Linhas consecutivas a partir de `inicio` que casam com o padrão."""
    fim = inicio
    while fim < len(linhas) and re.fullmatch(padrao, linhas[fim]):
        fim += 1
    return linhas[inicio:fim]


def ler_fatura(caminho: Path) -> dict:
    texto = texto_pdf(str(caminho))
    linhas = [l.strip() for l in texto.splitlines() if l.strip()]

    # "16/07/2026 17/08/2026 32 16/09/2026": leitura anterior, leitura atual, dias, próxima leitura
    leitura = next((m for l in linhas if (m := re.fullmatch(rf"{DATA} {DATA} (\d+) {DATA}", l))), None)
    # "0360631384 CONSUMO kWh TP 12596 12934 1 338": medidor, leituras, constante, consumo
    medidor = next((m for l in linhas if (m := re.search(r"CONSUMO kWh \S+ (\d+) (\d+) (\d+) (\d+)$", l))), None)
    bandeira = re.search(r"Periodos Band\.Tarif\.:\s*(.+)", texto)
    nota = re.search(rf"NOTA FISCAL No\. (\d+) - SÉRIE (\d+) / DATA DE EMISSÃO: {DATA}", texto)
    chave = re.search(r"Chave de Acesso\s+([\d ]{50,})", texto)

    # Itens: nomes em sequência; depois as tarifas (6 casas) e logo em seguida os valores, na mesma ordem.
    i_itens = next(i for i, l in enumerate(linhas) if l.startswith("ENERGIA ELET"))
    nomes = _bloco(linhas, i_itens, r"[A-ZÇÃÕÁÉÍÓÚÂÊÔ .\-/]+")
    i_tarifas = next(i for i in range(i_itens, len(linhas)) if re.fullmatch(r"\d+,\d{6}", linhas[i]))
    tarifas = _bloco(linhas, i_tarifas, r"\d+,\d{6}")[:len(nomes)]
    valores = linhas[i_tarifas + len(tarifas): i_tarifas + len(tarifas) + len(nomes)]
    itens = [{"item": n, "tarifa": _num(t), "valor": _num(v)} for n, t, v in zip(nomes, tarifas, valores)]

    total_m = next((m for l in linhas if (m := re.fullmatch(r"\d{2}/\d{4} \d{2}/\d{2}/\d{4} R\$([\d.]+,\d{2})", l))), None)
    total = _num(total_m[1]) if total_m else None
    if total is None or abs(sum(i["valor"] for i in itens) - total) > 0.01:
        raise ValueError(f"itens ({sum(i['valor'] for i in itens):.2f}) não batem com o total ({total})")

    # Tributos: "ICMS / COFINS / PIS", depois 3 bases, 3 alíquotas e 3 valores.
    tributos = {}
    if "ICMS" in linhas:
        i = linhas.index("ICMS")
        nomes_trib = _bloco(linhas, i, r"ICMS|COFINS|PIS|PIS/PASEP")
        n = len(nomes_trib)
        numeros = linhas[i + n: i + 4 * n]
        for k, nome in enumerate(nomes_trib):
            tributos[nome] = {"base": _num(numeros[k]), "aliquota": numeros[n + k], "valor": _num(numeros[2 * n + k])}

    # Histórico: rótulos "AGO26", depois os consumos e os dias faturados (só dos meses com dado).
    historico = {}
    if "HISTÓRICO DE CONSUMO / kWh" in texto:
        i = next(i for i, l in enumerate(linhas) if re.fullmatch(r"[A-Z]{3}\d{2}", l))
        rotulos = _bloco(linhas, i, r"[A-Z]{3}\d{2}")
        numeros = _bloco(linhas, i + len(rotulos), r"\d+")
        consumos = numeros[: len(numeros) // 2]
        for rotulo, kwh in zip(rotulos, consumos):
            historico[f"20{rotulo[3:]}-{MESES[rotulo[:3]]:02d}"] = int(kwh)

    return dict(
        consumo_kwh=float(medidor[4]) if medidor else None,
        leitura_anterior=float(medidor[1]) if medidor else None,
        leitura_atual=float(medidor[2]) if medidor else None,
        data_leitura=_iso(leitura[2]) if leitura else None,
        dias=int(leitura[3]) if leitura else None,
        bandeira=bandeira[1].strip() if bandeira else None,
        detalhes={
            "itens": itens,
            "tributos": tributos,
            "historico_kwh": historico,
            "leitura_anterior_em": _iso(leitura[1]) if leitura else None,
            "proxima_leitura": _iso(leitura[4]) if leitura else None,
            "nota_fiscal": {"numero": nota[1], "serie": nota[2], "emissao": _iso(nota[3])} if nota else None,
            "chave_acesso": re.sub(r"\D", "", chave[1]) if chave else None,
            "arrecadada": "FATURA ARRECADADA" in texto,
        },
    )
