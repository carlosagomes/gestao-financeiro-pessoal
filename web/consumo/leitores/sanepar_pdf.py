"""Leitura do PDF da fatura da Sanepar ("Segunda Via de Conta Paga").

O texto extraído do PDF sai por posição, não por rótulo; cada campo é reconhecido pelo formato da linha.
O texto inteiro também é guardado, para reprocessar se algum campo mudar de lugar.
"""
from __future__ import annotations

import re
from io import BytesIO
from typing import Optional, Union

from pypdf import PdfReader

VALOR = r"(\d{1,3}(?:\.\d{3})*,\d{2})"
DATA = r"(\d{2}/\d{2}/\d{4})"


def _valor(texto: Optional[str]) -> Optional[float]:
    return float(texto.replace(".", "").replace(",", ".")) if texto else None


def _iso(data: str) -> str:
    dia, mes, ano = data.split("/")
    return f"{ano}-{mes}-{dia}"


def texto_pdf(pdf: Union[bytes, str]) -> str:
    leitor = PdfReader(BytesIO(pdf) if isinstance(pdf, bytes) else pdf)
    return "\n".join(pagina.extract_text() or "" for pagina in leitor.pages)


def ler_fatura(texto: str) -> dict:
    linhas = [l.strip() for l in texto.splitlines() if l.strip()]

    # Rodapé: "4130.4928 09/2026 23/09/2026 199,87" -> matrícula, referência, vencimento, total
    rodape = next((m for l in reversed(linhas)
                   if (m := re.fullmatch(rf"(\d{{4}}\.\d{{4}})\s+(\d{{2}}/\d{{4}})\s+{DATA}\s+{VALOR}", l))), None)
    if not rodape:
        raise ValueError("não achei referência/vencimento/total no PDF")
    matricula, referencia, vencimento, total = rodape.groups()
    mes, ano = referencia.split("/")

    # "09/10/2026 111,04 88,83 0,00 199,87" -> próxima leitura, água, esgoto, serviços, total
    valores = next((m for l in linhas if (m := re.fullmatch(rf"{DATA}\s+{VALOR}\s+{VALOR}\s+{VALOR}\s+{VALOR}", l))), None)
    # "30 10/09/2026 119 134 15 09/2026" -> dias, data da leitura, leitura anterior, atual, consumo, referência
    leitura = next((m for l in linhas if (m := re.fullmatch(rf"(\d+)\s+{DATA}\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d{{2}}/\d{{4}})", l))), None)
    tributos = re.search(rf"VALOR APROXIMADO R\$\s*{VALOR}", texto)

    # Faixas: "RES MÍNIMO 5 55,09 44,07" / "DE 6 A 10M3 5 1,70 8,50 6,80"
    faixas = []
    for l in linhas:
        if m := re.fullmatch(rf"(RES M[ÍI]NIMO|DE \d+ A \d+M3|ACIMA DE \d+M3)\s+(\d+)\s+(.+)", l):
            nums = re.findall(VALOR, m[3])
            faixas.append({"faixa": m[1], "volume_m3": int(m[2]), "valores": [_valor(n) for n in nums]})

    # Histórico do gráfico: "10/25" na linha e o consumo (m³) na linha seguinte
    historico = {}
    for atual, seguinte in zip(linhas, linhas[1:]):
        if re.fullmatch(r"\d{2}/\d{2}", atual) and re.fullmatch(r"\d+", seguinte):
            m, a = atual.split("/")
            historico[f"20{a}-{m}"] = int(seguinte)

    fatura = dict(
        referencia=f"{ano}-{mes}",
        matricula=matricula,
        vencimento=_iso(vencimento),
        valor_total=_valor(total),
        valor_agua=_valor(valores[2]) if valores else None,
        valor_esgoto=_valor(valores[3]) if valores else None,
        valor_servicos=_valor(valores[4]) if valores else None,
        proxima_leitura=_iso(valores[1]) if valores else None,
        dias=int(leitura[1]) if leitura else None,
        data_leitura=_iso(leitura[2]) if leitura else None,
        leitura_anterior=int(leitura[3]) if leitura else None,
        leitura_atual=int(leitura[4]) if leitura else None,
        consumo_m3=int(leitura[5]) if leitura else None,
        tributos=_valor(tributos[1]) if tributos else None,
        situacao="Paga" if "FATURA PAGA" in texto else "Em aberto",
        detalhes={"faixas": faixas, "historico_m3": historico},
    )
    partes = [fatura["valor_agua"], fatura["valor_esgoto"], fatura["valor_servicos"]]
    if None not in partes and abs(sum(partes) - fatura["valor_total"]) > 0.01:
        raise ValueError(f"água + esgoto + serviços ({sum(partes):.2f}) ≠ total ({fatura['valor_total']:.2f})")
    return fatura
