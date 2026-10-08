"""Consultas das páginas Notas Paraná e Produtos sobre os DataFrames de `core.dados` (já só do usuário).

`compras` tem uma linha por nota (forma = a de maior valor) e `pagamentos` uma por forma de pagamento da nota,
com o valor pago nela; as duas têm as colunas nota_id, data, mes, loja, categoria, forma e valor.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import pandas as pd

from core import dados
from core.categorias import OUTROS
from core.formatos import MESES, MESES_CURTOS, brl
from core.graficos import PALETA
from notas import produtos

NAO_INFORMADO = "Não informado"
TIPOS = {"categoria": "Categoria", "loja": "Loja", "forma": "Forma de pagamento"}
NOMES = {"categoria": "Por categoria", "loja": "Onde mais gastei", "forma": "Como paguei", "semana": "Dia da semana"}
# No detalhe, as barras da linha do tempo se dividem por uma dimensão e há dois rankings.
DIVISAO = {"categoria": "loja", "loja": "forma", "forma": "loja"}
RANKINGS = {"categoria": ("loja", "forma"), "loja": ("forma", "semana"), "forma": ("categoria", "loja")}
SEMANA = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"]
DIAS = ["Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado", "Domingo"]
COMPRAS_POR_VEZ = 120  # "Compra por compra" carrega meses inteiros até passar disso; o resto vem sob demanda


# --- período ------------------------------------------------------------------------------------

def ler_mes(texto: str | None) -> pd.Period | None:
    if texto and re.fullmatch(r"\d{4}-\d{2}", texto) and 1 <= int(texto[5:]) <= 12:
        return pd.Period(texto, freq="M")
    return None


def rotulo_mes(mes: pd.Period, curto: bool = True) -> str:
    return f"{MESES_CURTOS[mes.month - 1]}/{mes.year % 100:02d}" if curto else f"{MESES[mes.month - 1]} {mes.year}"


@dataclass
class Periodo:
    inicio: pd.Period
    fim: pd.Period
    meses: list[pd.Period]  # todos os meses com dados (opções dos seletores)

    @property
    def de(self) -> str:
        return str(self.inicio)

    @property
    def ate(self) -> str:
        return str(self.fim)

    @property
    def n_meses(self) -> int:
        return (self.fim - self.inicio).n + 1

    @property
    def texto(self) -> str:
        inicio, fim = self.inicio.strftime("%m/%Y"), self.fim.strftime("%m/%Y")
        return f"{inicio} a {fim}" if inicio != fim else inicio

    @property
    def opcoes(self) -> list[tuple[str, str]]:
        return [(str(m), rotulo_mes(m)) for m in self.meses]

    def contem(self, mes: pd.Series) -> pd.Series:
        return (mes >= self.inicio) & (mes <= self.fim)


def periodo(get, primeiro: pd.Period, ultimo: pd.Period, padrao: int | None = 12) -> Periodo:
    """Período dos filtros ?de=AAAA-MM&ate=AAAA-MM, limitado aos meses com dados.
    Sem filtro: os últimos `padrao` meses (None = tudo)."""
    fim = ler_mes(get.get("ate")) or ultimo
    inicio = ler_mes(get.get("de")) or (max(primeiro, fim - (padrao - 1)) if padrao else primeiro)
    inicio, fim = (min(max(m, primeiro), ultimo) for m in (inicio, fim))
    if inicio > fim:
        inicio, fim = fim, inicio
    return Periodo(inicio, fim, list(pd.period_range(primeiro, ultimo, freq="M")))


# --- notas, compras e pagamentos ------------------------------------------------------------------

@dataclass
class Base:
    notas: pd.DataFrame
    compras: pd.DataFrame
    pagamentos: pd.DataFrame
    _itens: pd.DataFrame | None = None
    usuario: object = None

    @property
    def itens(self) -> pd.DataFrame:
        """Itens já com grupo e preço pago (carregados só quando alguém pede)."""
        if self._itens is None:
            self._itens = produtos.preparar(dados.itens_df(self.usuario), self.notas)
        return self._itens


def carregar(usuario) -> Base:
    notas = dados.notas_df(usuario)
    pags = dados.pagamentos_df(usuario)
    pags = pags.assign(forma=pags["forma"].fillna("").str.strip().replace("", NAO_INFORMADO),
                       valor=pags["valor"].fillna(0.0))
    principal = pags.sort_values("valor", kind="stable").groupby("nota_id")["forma"].last()
    compras = pd.DataFrame({
        "nota_id": notas["id"], "data": notas["data_emissao"], "mes": notas["mes"], "loja": notas["loja"],
        "categoria": notas["categoria"], "forma": notas["id"].map(principal).fillna(NAO_INFORMADO),
        "valor": notas["valor_total"],
    })
    pagamentos = pags[["nota_id", "forma", "valor"]].merge(
        compras.drop(columns=["forma"]).rename(columns={"valor": "total_nota"}), on="nota_id")
    return Base(notas, compras, pagamentos, usuario=usuario)


def cores_categorias(notas: pd.DataFrame, quantas: int = 7) -> dict[str, str]:
    """Cor fixa das categorias que mais pesam no histórico todo: trocar o período não repinta."""
    ranking = (notas[notas["categoria"] != OUTROS].groupby("categoria")["valor_total"].sum()
               .sort_values(ascending=False, kind="stable"))
    return dict(zip(ranking.index[:quantas], PALETA))


def categorias_conhecidas(notas: pd.DataFrame, regras: dict[str, str]) -> list[str]:
    return sorted(set(regras.values()) | set(notas["categoria"]) | {OUTROS}, key=str.casefold)


MINUSCULAS = {"de", "da", "do", "das", "dos", "e"}


def cidade(texto: str) -> str:
    """"MARINGA" e "Maringa" são a mesma cidade: mostra com iniciais maiúsculas ("São José dos Pinhais")."""
    palavras = (texto or "").lower().split()
    return " ".join(p if i and p in MINUSCULAS else p.capitalize() for i, p in enumerate(palavras))


# --- detalhe (categoria, loja ou forma de pagamento) -------------------------------------------------

def linhas_do_alvo(base: Base, tipo: str, alvo: str) -> pd.DataFrame:
    """Por forma de pagamento conta o valor pago naquela forma (uma nota pode ter sido paga com duas)."""
    todas = base.pagamentos if tipo == "forma" else base.compras
    return todas[todas[tipo] == alvo]


def ranking(dim: str, linhas: pd.DataFrame, pagamentos: pd.DataFrame) -> pd.Series:
    if dim == "semana":  # na ordem da semana, não do valor
        return linhas.groupby(linhas["data"].dt.weekday)["valor"].sum().reindex(range(7), fill_value=0).set_axis(DIAS)
    if dim == "forma":
        serie = pagamentos[pagamentos["nota_id"].isin(linhas["nota_id"])].groupby("forma")["valor"].sum()
    else:
        serie = linhas.groupby(dim)["valor"].sum()
    return serie.sort_values(ascending=False, kind="stable")


def mais_comprados(linhas: pd.DataFrame, itens: pd.DataFrame, quantos: int = 10) -> pd.DataFrame:
    """Produtos das compras pelo valor pago (total − desconto). Índice = grupo do produto."""
    daqui = itens[itens["nota_id"].isin(linhas["nota_id"])]
    if daqui.empty:
        return pd.DataFrame(columns=["produto", "pago"])
    soma = daqui.groupby("grupo")["pago"].sum().sort_values(ascending=False, kind="stable").head(quantos)
    nomes = daqui.drop_duplicates("grupo").set_index("grupo")["produto"]
    return pd.DataFrame({"produto": nomes.reindex(soma.index), "pago": soma})


def compra_por_compra(linhas: pd.DataFrame, tipo: str, itens: pd.DataFrame, antes: pd.Period | None = None,
                      limite: int = COMPRAS_POR_VEZ) -> tuple[list[dict], str | None]:
    """Meses (do mais recente para o mais antigo) com as compras de cada um e os principais produtos.
    Carrega meses inteiros até passar de `limite` compras; devolve também o mês seguinte a carregar (ou None)."""
    linhas = linhas.sort_values("data", ascending=False, kind="stable")
    if antes is not None:
        linhas = linhas[linhas["mes"] < antes]
    if linhas.empty:
        return [], None
    contagem = linhas.groupby("mes", sort=False).size()
    acumulado = contagem.cumsum()
    corte = int((acumulado < limite).sum()) + 1  # meses até passar do limite (inclusive)
    meses_agora = contagem.index[:corte]
    proximo = str(contagem.index[corte]) if corte < len(contagem) else None
    parte = linhas[linhas["mes"].isin(meses_agora)]

    daqui = itens[itens["nota_id"].isin(parte["nota_id"])].sort_values("valor_total", ascending=False, kind="stable")
    nomes = daqui.groupby("nota_id")["descricao"].agg(list)
    resultado = []
    for mes, grupo in parte.groupby("mes", sort=False):
        compras = []
        for c in grupo.itertuples():
            lista = nomes.get(c.nota_id, [])
            resumo = ", ".join(lista[:3]) + (f" e mais {len(lista) - 3}" if len(lista) > 3 else "")
            if tipo == "loja":
                titulo = (f"{len(lista)} itens" if len(lista) != 1 else "1 item") if lista else "Compra"
            else:
                titulo = c.loja
            detalhe = [c.categoria if tipo != "categoria" else "", c.forma if tipo != "forma" else ""]
            if tipo == "forma" and c.total_nota - c.valor > 0.009:
                detalhe.append(f"parte da nota de {brl(c.total_nota)}")
            compras.append({"nota_id": int(c.nota_id), "dia": f"{c.data:%d/%m}", "semana": SEMANA[c.data.weekday()],
                            "hora": f"{c.data:%H:%M}", "titulo": titulo, "detalhe": " · ".join(d for d in detalhe if d),
                            "produtos": resumo, "valor": float(c.valor)})
        resultado.append({"rotulo": rotulo_mes(mes, curto=False), "compras": compras, "n": len(grupo),
                          "total": float(grupo["valor"].sum())})
    return resultado, proximo


# --- produtos -------------------------------------------------------------------------------------

@dataclass
class FiltroProdutos:
    busca: str
    categoria: str
    loja: str
    repetidos: bool
    periodo: Periodo

    @classmethod
    def ler(cls, get, itens: pd.DataFrame) -> "FiltroProdutos":
        meses = itens["data"].dt.to_period("M")
        return cls(busca=(get.get("busca") or "").strip()[:80], categoria=get.get("categoria") or "",
                   loja=get.get("loja") or "", repetidos=get.get("repetidos") in {"1", "on", "true"},
                   periodo=periodo(get, meses.min(), meses.max(), padrao=None))

    def aplicar(self, itens: pd.DataFrame) -> pd.DataFrame:
        filtro = itens[self.periodo.contem(itens["data"].dt.to_period("M"))]
        if self.categoria:
            filtro = filtro[filtro["categoria"] == self.categoria]
        if self.loja:
            filtro = filtro[filtro["loja"] == self.loja]
        if self.busca:
            filtro = filtro[filtro["descricao"].str.contains(self.busca, case=False, regex=False, na=False)
                            | filtro["produto"].str.contains(self.busca, case=False, regex=False, na=False)]
        tabela = produtos.resumo(filtro)
        return tabela[tabela["compras"] > 1] if self.repetidos else tabela

    @property
    def parametros(self) -> dict[str, str]:
        p = {"busca": self.busca, "categoria": self.categoria, "loja": self.loja, "de": self.periodo.de,
             "ate": self.periodo.ate, "repetidos": "1" if self.repetidos else ""}
        return {k: v for k, v in p.items() if v}


def linhas_produtos(tabela: pd.DataFrame, pontos: int = 40) -> list[dict]:
    """Linhas da tabela "Todos os produtos" (JSON do Tabulator). `precos` leva só os últimos pontos: é o
    minigráfico da evolução."""
    linhas = []
    for grupo, r in zip(tabela.index, tabela.itertuples(index=False)):
        linhas.append({
            "grupo": grupo, "produto": r.produto, "precos": [round(p, 2) for p in r.precos[-pontos:]],
            "variacao": None if pd.isna(r.variacao) else round(float(r.variacao), 4),
            "ultimo": round(r.ultimo, 2), "menor": round(r.menor, 2), "maior": round(r.maior, 2),
            "compras": int(r.compras), "lojas": int(r.lojas), "gasto": round(r.gasto, 2), "categoria": r.categoria,
            "ultima": f"{r.ultima:%Y-%m-%d}", "quantidade": round(float(r.quantidade), 3), "unidade": r.unidade,
        })
    return linhas
