"""Categorias iniciais de cada usuário novo e categorização automática das notas pelo nome da loja."""
from __future__ import annotations

import unicodedata

ENTRADA = "Entrada"
SAIDA = "Saída"
OUTROS = "Outros"

# Ponto de partida para os lançamentos (dá para criar outras na tela de Lançamentos).
CATEGORIAS_PADRAO = {
    ENTRADA: ["Salário", "Outras entradas"],
    SAIDA: ["Imposto", "INSS", "Contadora", "Prestação Casa", "Luz", "Água", "Gás", "Internet",
            "Cartão", "IPVA", "Educação", "Saúde", OUTROS],
}
BANCOS_PADRAO = ["NuBank", "Itaú", "XP"]

# Categorias das notas do Nota Paraná. Elas detalham o consumo; não somam nas saídas,
# porque essas compras já estão dentro da fatura do cartão / do débito.
# Trecho do nome do estabelecimento (sem acento, em maiúsculas) -> categoria.
REGRAS_PADRAO = {
    "SUPERMERCADO": "Mercado", "MERCADO": "Mercado", "ATACAD": "Mercado", "MUFFATO": "Mercado",
    "CONDOR": "Mercado", "ANGELONI": "Mercado", "ASSAI": "Mercado", "CARREFOUR": "Mercado",
    "VAREJAO": "Mercado", "HORTIFRUTI": "Mercado", "ACOUGUE": "Mercado", "DISTRIBUICAO": "Mercado",
    "POSTO": "Combustível", "COMBUSTIV": "Combustível",
    "DROGA": "Farmácia", "FARMA": "Farmácia", "PANVEL": "Farmácia", "NISSEI": "Farmácia",
    "RESTAURANTE": "Alimentação fora", "LANCHON": "Alimentação fora", "PIZZ": "Alimentação fora",
    "BURGER": "Alimentação fora", "PADARIA": "Alimentação fora", "PANIFICADORA": "Alimentação fora",
    "GELATERIA": "Alimentação fora", "SORVET": "Alimentação fora", "FOOD SERVICE": "Alimentação fora",
    "BATISTA & IZEPE": "Mercado", "PARAISO LOJA": "Mercado", "LIV UP": "Mercado",
    "COMB.": "Combustível", "MEDICAMENTOS": "Farmácia",
    "HABANERO": "Alimentação fora", "KENTUCKY": "Alimentação fora", "PARMEGGIO": "Alimentação fora",
    "PIRATAS DA MADRUGADA": "Alimentação fora", "FRANGO NO BALDE": "Alimentação fora",
    "ESFIHARIA": "Alimentação fora", "LINDT": "Alimentação fora",
    "MATERIAIS DE CONSTRU": "Casa", "LEROY": "Casa", "MADEIREIRA": "Casa", "TINTAS": "Casa",
    "FERRAGENS": "Casa", "UTILIDADES DOMESTICAS": "Casa", "CASA CHINA": "Casa", "DESCARTAVEIS": "Casa",
    "GAZIN": "Casa",
    "HAVAN": "Compras", "AMERICANAS": "Compras", "AMAZON": "Compras", "RI HAPPY": "Compras",
    "BRINQUEDOS": "Compras", "FESTAS": "Compras",
    "RENNER": "Vestuário", "RIACHUELO": "Vestuário", "CALCADOS": "Vestuário",
    "OTICA": "Saúde", "BOTICARIO": "Beleza", "PERFUMARIA": "Beleza",
    "AUTO PECAS": "Carro", "COBASI": "Pet", "PETZ": "Pet",
}


def normalizar(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode()
    return sem_acento.upper()


def categorizar(nome_estabelecimento: str, regras: dict[str, str]) -> str:
    """A regra de trecho mais longo vence (ex.: 'FOOD SERVICE' antes de 'FOOD').
    Passe razão social + nome fantasia: "BAEZA E CRUZ LTDA" só se revela pelo "SUPERMERCADO PARAISO"."""
    alvo = normalizar(nome_estabelecimento)
    for padrao in sorted(regras, key=len, reverse=True):
        if normalizar(padrao) in alvo:
            return regras[padrao]
    return OUTROS
