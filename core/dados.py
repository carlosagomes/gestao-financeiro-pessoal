"""Dados de UM usuário em DataFrames (pandas), para os relatórios e gráficos.

Toda leitura passa por aqui ou por `Modelo.objects.filter(usuario=request.user)`: nunca consultar sem o
usuário. Valores monetários saem como float (o banco guarda Decimal).
"""
from __future__ import annotations

import pandas as pd

from core.categorias import categorizar
from financeiro.models import Lancamento
from notas.models import ItemNota, Nota, PagamentoNota, RegraCategoria

COLUNAS_LANCAMENTO = ["id", "data", "movimentacao", "banco", "categoria", "descricao", "valor", "pago"]


def _df(queryset, colunas: list[str]) -> pd.DataFrame:
    return pd.DataFrame.from_records(list(queryset.values_list(*colunas)), columns=colunas)


def lancamentos_df(usuario) -> pd.DataFrame:
    df = _df(Lancamento.objects.filter(usuario=usuario).order_by("data", "id"), COLUNAS_LANCAMENTO)
    df["data"] = pd.to_datetime(df["data"])
    df["valor"] = df["valor"].astype(float)
    df["pago"] = df["pago"].astype(bool)
    return df


def regras(usuario) -> dict[str, str]:
    return dict(RegraCategoria.objects.filter(usuario=usuario).values_list("padrao", "categoria"))


def notas_df(usuario) -> pd.DataFrame:
    """Notas com a categoria efetiva (a escolhida à mão ou a das regras) e o nome curto da loja."""
    colunas = ["id", "chave", "data_emissao", "emitente_nome", "emitente_fantasia", "emitente_cnpj",
               "emitente_municipio", "valor_total", "valor_desconto", "credito", "situacao_credito", "categoria",
               "qtd_itens", "numero", "serie"]
    df = _df(Nota.objects.filter(usuario=usuario).order_by("data_emissao"), colunas)
    df["data_emissao"] = pd.to_datetime(df["data_emissao"], utc=True).dt.tz_convert("America/Sao_Paulo").dt.tz_localize(None)
    for c in ("valor_total", "valor_desconto", "credito"):
        df[c] = pd.to_numeric(df[c], errors="coerce").astype(float)
    r = regras(usuario)
    nomes = (df["emitente_nome"].fillna("") + " " + df["emitente_fantasia"].fillna("")).tolist()
    df["categoria_auto"] = df["categoria"].fillna("") == ""
    df["categoria"] = [c if c else categorizar(n, r) for c, n in zip(df["categoria"], nomes)]
    fantasia = df["emitente_fantasia"].replace("", pd.NA)
    df["loja"] = fantasia.fillna(df["emitente_nome"]).fillna("").str.slice(0, 32)
    df["mes"] = df["data_emissao"].dt.to_period("M")
    return df


def itens_df(usuario) -> pd.DataFrame:
    colunas = ["nota_id", "nota__chave", "seq", "codigo", "ean", "descricao", "quantidade", "unidade",
               "valor_unitario", "valor_desconto", "valor_total", "nota__data_emissao"]
    df = _df(ItemNota.objects.filter(nota__usuario=usuario), colunas).rename(
        columns={"nota__chave": "chave", "nota__data_emissao": "data_emissao"})
    for c in ("quantidade", "valor_unitario", "valor_desconto", "valor_total"):
        df[c] = pd.to_numeric(df[c], errors="coerce").astype(float)
    df["data_emissao"] = pd.to_datetime(df["data_emissao"], utc=True).dt.tz_convert("America/Sao_Paulo").dt.tz_localize(None)
    return df


def pagamentos_df(usuario) -> pd.DataFrame:
    df = _df(PagamentoNota.objects.filter(nota__usuario=usuario), ["nota_id", "nota__chave", "forma", "bandeira", "valor"])
    df = df.rename(columns={"nota__chave": "chave"})
    df["valor"] = pd.to_numeric(df["valor"], errors="coerce").astype(float)
    return df
