"""Produtos das notas: o mesmo produto reconhecido entre lojas e o histórico do preço pago por ele.

Usado pela página Produtos e pelo detalhe da página Notas Paraná. Só pandas: recebe os DataFrames de
`core.dados` (já filtrados pelo usuário).
"""
from __future__ import annotations

import pandas as pd

MINIMO_VARIACAO = 3  # compras para entrar em "subiram/caíram": com menos, a variação é ruído
MESMO_PRECO = 0.0005  # |variação| abaixo disso conta como "mesmo preço"
BRINDE = 0.9  # desconto de 90% ou mais do item = brinde ("leve 2 pague 1")


def mais_frequente(df: pd.DataFrame, chave: str, coluna: str) -> pd.Series:
    """Moda de `coluna` em cada `chave` (empate: a primeira em ordem alfabética, como `Series.mode`).
    Vetorizado: um groupby com função Python por grupo fica lento com dezenas de milhares de produtos."""
    contagem = df.groupby([chave, coluna], sort=False).size().reset_index(name="n")
    contagem = contagem.sort_values([chave, "n", coluna], ascending=[True, False, True])
    return contagem.drop_duplicates(chave).set_index(chave)[coluna]


def grupos(ean: pd.Series, descricao: pd.Series) -> pd.Series:
    """Chave do produto: o código de barras (EAN/GTIN de 8 a 14 dígitos, não zerado) quando a nota traz um,
    senão o nome em maiúsculas. Assim "LEITE PARMALAT TP 1L" e "L INT PARMALAT 1L" de lojas diferentes
    viram o mesmo produto."""
    ean = ean.fillna("").astype(str).str.strip()
    valido = ean.str.fullmatch(r"\d{8,14}") & ~ean.str.fullmatch(r"0+")
    nome = descricao.fillna("").astype(str).str.upper().str.split().str.join(" ")
    return ("ean:" + ean).where(valido, "nome:" + nome)


def preparar(itens: pd.DataFrame, notas: pd.DataFrame) -> pd.DataFrame:
    """Itens (`core.dados.itens_df`) com loja, categoria e data da nota, o preço pago por unidade e o grupo.

    O `valor_total` do item é bruto e o desconto vem à parte: preço pago = (total − desconto) / quantidade.
    Item de brinde (desconto >= 90%) fica com o preço de etiqueta: senão um iogurte de R$ 0,01 vira o
    "menor preço" e a variação passa de 30.000%. O valor pago continua certo em `pago`."""
    colunas = ["nota_id", "seq", "ean", "descricao", "quantidade", "unidade", "valor_unitario", "valor_desconto",
               "valor_total", "data_emissao"]
    i = itens[colunas].merge(notas[["id", "loja", "categoria"]].rename(columns={"id": "nota_id"}), on="nota_id")
    total = i["valor_total"].fillna(0.0)
    desconto = i["valor_desconto"].fillna(0.0)
    pago = total - desconto
    brinde = desconto >= BRINDE * total
    quantidade = i["quantidade"].where(i["quantidade"] > 0)
    unidade = i["unidade"].fillna("").astype(str).str.upper().str.strip()
    i = i.assign(
        data=i["data_emissao"], descricao=i["descricao"].fillna("").astype(str).str.strip(),
        unidade=unidade.mask(unidade == "", "UN"), valor_desconto=desconto.where(desconto > 0),
        pago=pago, preco=(pago / quantidade).where(~brinde, i["valor_unitario"]), brinde=brinde,
        grupo=grupos(i["ean"], i["descricao"]),
    ).drop(columns=["data_emissao"])
    return i.assign(produto=i["grupo"].map(mais_frequente(i, "grupo", "descricao")))


def resumo(itens: pd.DataFrame) -> pd.DataFrame:
    """Uma linha por produto (índice = grupo): quantas vezes comprou, quanto gastou e como o preço pago mudou
    (`variacao` = último preço ÷ primeiro − 1). Ordenado pelo gasto."""
    colunas = ["produto", "categoria", "compras", "lojas", "quantidade", "unidade", "gasto", "primeiro", "ultimo",
               "menor", "maior", "ultima", "precos", "variacao"]
    ordem = itens.dropna(subset=["preco"]).sort_values(["data", "nota_id", "seq"], kind="stable")
    if ordem.empty:
        return pd.DataFrame(columns=colunas)
    g = ordem.groupby("grupo", sort=False)
    r = g.agg(produto=("produto", "first"), compras=("preco", "size"), lojas=("loja", "nunique"),
              quantidade=("quantidade", "sum"), gasto=("pago", "sum"), primeiro=("preco", "first"),
              ultimo=("preco", "last"), menor=("preco", "min"), maior=("preco", "max"), ultima=("data", "max"))
    r["precos"] = g["preco"].agg(list)
    r["categoria"] = mais_frequente(ordem, "grupo", "categoria")
    r["unidade"] = mais_frequente(ordem, "grupo", "unidade")
    r["variacao"] = (r["ultimo"] / r["primeiro"].where(r["primeiro"] > 0) - 1).where(r["compras"] > 1)
    return r[colunas].sort_values("gasto", ascending=False)


def variacoes(tabela: pd.DataFrame, minimo: int = MINIMO_VARIACAO) -> dict[str, pd.DataFrame]:
    """Produtos comprados `minimo` vezes ou mais, separados entre os que subiram, caíram ou ficaram iguais."""
    com = tabela[tabela["compras"] >= minimo].dropna(subset=["variacao"])
    subiram = com[com["variacao"] > MESMO_PRECO].sort_values("variacao", ascending=False)
    cairam = com[com["variacao"] < -MESMO_PRECO].sort_values("variacao")
    return {"com_variacao": com, "subiram": subiram, "cairam": cairam,
            "iguais": len(com) - len(subiram) - len(cairam)}


def historico(itens: pd.DataFrame, grupos_: list[str], juntar: str = "") -> pd.DataFrame:
    """Todas as compras dos grupos (em todas as lojas, todo o histórico) e, se pedido, as de itens cujo nome
    contém `juntar` (cada loja escreve o nome de um jeito; carnes e pesados não têm código de barras)."""
    filtro = itens["grupo"].isin(list(grupos_))
    if juntar.strip():
        filtro |= itens["descricao"].str.contains(juntar.strip(), case=False, regex=False, na=False)
    return itens[filtro].dropna(subset=["preco"]).sort_values(["data", "nota_id", "seq"], kind="stable")


def painel(hist: pd.DataFrame) -> dict:
    """Números do histórico de preço: último (e variação desde o primeiro), menor, maior e totais."""
    primeiro, ultimo = hist.iloc[0], hist.iloc[-1]
    variacao = None
    if len(hist) > 1 and primeiro["preco"]:
        variacao = ultimo["preco"] / primeiro["preco"] - 1
        if abs(variacao) < MESMO_PRECO:
            variacao = None  # mesmo preço do começo: sem seta
    return {
        "unidade": mais_frequente(hist.assign(_k=0), "_k", "unidade").iloc[0].lower(),
        "primeiro": primeiro, "ultimo": ultimo, "variacao": variacao,
        "menor": hist.loc[hist["preco"].idxmin()], "maior": hist.loc[hist["preco"].idxmax()],
        "compras": len(hist), "quantidade": float(hist["quantidade"].sum()), "total": float(hist["pago"].sum()),
        # da loja onde mais comprou o produto para a que menos (empate: ordem alfabética, para a cor não pular)
        "lojas": hist.groupby("loja").size().sort_values(ascending=False, kind="stable").index.tolist(),
    }
