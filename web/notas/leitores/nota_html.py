"""Leitura da "Consulta da NF-e" (versão Completa) que o Nota Paraná mostra para cada nota.

A página tem abas (aba_nft_0 … aba_nft_7) e quase todo dado vem como <td><label/><span/></td>.
Os campos principais viram colunas; todo o resto vai inteiro para `detalhes`.
"""
from __future__ import annotations

import re
from typing import Optional, Union

from bs4 import BeautifulSoup, Tag

ABAS = {0: "NFe", 1: "Emitente", 2: "Destinatário", 3: "Produtos", 4: "Totais", 5: "Transporte",
        6: "Cobrança", 7: "Informações Adicionais"}


def _texto(el: Optional[Tag]) -> str:
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip() if el else ""


def numero(valor: Optional[str]) -> Optional[float]:
    """'1.234,56' -> 1234.56; 'R$ 0,11' -> 0.11; '--' / '' -> None. Aceita '0.00' (o troco vem assim)."""
    v = (valor or "").replace("R$", "").strip()
    if "," in v:
        v = v.replace(".", "").replace(",", ".")
    try:
        return float(v)
    except ValueError:
        return None


# Tabela tPag da NF-e, para quando a página mostra só o código do meio de pagamento.
MEIOS_PAGAMENTO = {
    "01": "Dinheiro", "02": "Cheque", "03": "Cartão de Crédito", "04": "Cartão de Débito", "05": "Crédito Loja",
    "10": "Vale Alimentação", "11": "Vale Refeição", "12": "Vale Presente", "13": "Vale Combustível",
    "15": "Boleto Bancário", "16": "Depósito Bancário", "17": "Pagamento Instantâneo (PIX)",
    "18": "Transferência bancária, Carteira Digital", "19": "Programa de fidelidade, Cashback, Crédito Virtual",
    "20": "PIX estático", "21": "Crédito em loja", "22": "Pagamento eletrônico não informado",
    "90": "Sem pagamento", "99": "Outros",
}


def _sem_codigo(valor: str) -> str:
    """'3 - Cartão de Crédito' -> 'Cartão de Crédito'; '4115200 - Maringa' -> 'Maringa'."""
    return re.sub(r"^\s*\w+\s+-\s+", "", valor or "").strip()


def campos_por_secao(container: Optional[Tag]) -> dict[str, dict[str, str]]:
    """{legenda do fieldset: {rótulo: valor}} para todas as células rótulo/valor do container."""
    secoes: dict[str, dict[str, str]] = {}
    if container is None:
        return secoes
    for td in container.find_all("td"):
        label, span = td.find("label", recursive=False), td.find("span", recursive=False)
        if label is None or span is None:
            continue
        fieldset = td.find_parent("fieldset")
        secao = _texto(fieldset.find("legend")) if fieldset else ""
        secoes.setdefault(secao, {})[_texto(label)] = _texto(span)
    return secoes


def _tabela_cabecalho(tabela: Optional[Tag]) -> list[dict[str, str]]:
    """Tabelas com os rótulos numa linha e os valores na(s) linha(s) seguinte(s)."""
    linhas, cabecalho = [], None
    for tr in tabela.find_all("tr") if tabela else []:
        labels = [_texto(l) for l in tr.find_all("label")]
        spans = [_texto(s) for s in tr.find_all("span")]
        if labels and not spans:
            cabecalho = labels
        elif spans and cabecalho:
            linhas.append(dict(zip(cabecalho, spans)))
    return linhas


def _data_iso(valor: str) -> Optional[str]:
    m = re.match(r"(\d{2})/(\d{2})/(\d{4})\D+(\d{2}:\d{2}(?::\d{2})?)", valor or "")
    return f"{m[3]}-{m[2]}-{m[1]}T{m[4]}" if m else None


def _itens(aba: Optional[Tag]) -> list[dict]:
    itens = []
    for resumo in aba.select("table.toggle") if aba else []:
        def celula(classe: str) -> str:
            return _texto(resumo.select_one(f"td.fixo-prod-serv-{classe} span"))

        detalhe = resumo.find_next_sibling("table", class_="toggable")
        secoes = campos_por_secao(detalhe)
        produto = secoes.pop("Dados dos Produtos e Serviços", {})
        itens.append(dict(
            codigo=produto.get("Código do Produto"),
            ean=produto.get("Código EAN Comercial"),
            descricao=celula("descricao"),
            ncm=produto.get("Código NCM"),
            cfop=produto.get("CFOP"),
            quantidade=numero(celula("qtd")),
            unidade=celula("uc"),
            valor_unitario=numero(produto.get("Valor unitário de comercialização")),
            valor_desconto=numero(produto.get("Valor do Desconto")),
            valor_total=numero(celula("vb")),
            detalhes={"Produto": produto, **secoes},
        ))
    return itens


def _pagamentos(aba: Optional[Tag]) -> list[dict]:
    """Colunas lidas pelo cabeçalho: a NFC-e tem 4 (com "Descrição do Meio"), a NF-e tem 3."""
    pagamentos = []
    cabecalho = [_texto(l).rstrip(".") for l in aba.select("table.prod-serv-header label")] if aba else []
    for resumo in aba.select("table.toggle") if aba else []:
        detalhes = dict(zip(cabecalho, (_texto(s) for s in resumo.find_all("span"))))
        if "Meio de Pagamento" not in detalhes:
            continue
        for linha in _tabela_cabecalho(resumo.find_next_sibling("table", class_="toggable")):
            detalhes.update(linha)
        pagamentos.append(dict(
            forma=MEIOS_PAGAMENTO.get(_sem_codigo(detalhes["Meio de Pagamento"]).zfill(2),
                                      _sem_codigo(detalhes["Meio de Pagamento"])),
            bandeira=_sem_codigo(detalhes.get("Bandeira da operadora", "")) or None,
            valor=numero(detalhes.get("Valor do Pagamento")),
            detalhes=detalhes,
        ))
    return pagamentos


def ler_nota(html: Union[str, bytes]) -> tuple[dict, list[dict], list[dict]]:
    """Devolve (nota, itens, pagamentos) prontos para `financas.db.salvar_nota`."""
    if isinstance(html, bytes):
        # O site responde em ISO-8859-1; deixar o BeautifulSoup adivinhar troca "ã" por "ă" nas NF-e.
        try:
            html = html.decode("utf-8")
        except UnicodeDecodeError:
            html = html.decode("cp1252", errors="replace")
    soup = BeautifulSoup(html, "html.parser")
    abas = {i: soup.find(id=f"aba_nft_{i}") for i in ABAS}
    if abas[0] is None:
        raise ValueError("a página não é a consulta completa da NF-e")

    geral = campos_por_secao(soup.find("div", class_="GeralXslt")).get("Dados Gerais", {})
    secoes = {nome: campos_por_secao(abas[i]) for i, nome in ABAS.items() if i not in (3, 6)}
    dados = secoes["NFe"].get("Dados da NF-e", {})
    emitente = secoes["Emitente"].get("Dados do Emitente", {})
    totais = {k: v for secao in secoes["Totais"].values() for k, v in secao.items()}

    legenda_situacao = next((_texto(l) for l in abas[0].find_all("legend") if "Situação Atual" in _texto(l)), "")
    situacao = re.search(r"Situação Atual:\s*([^(]+)", legenda_situacao)
    eventos_tab = next((l.find_parent("fieldset").find("table") for l in abas[0].find_all("legend")
                        if "Situação Atual" in _texto(l)), None)
    protocolo = soup.find(id="nProt")

    municipio = _sem_codigo(emitente.get("Município", ""))
    uf = emitente.get("UF", "")
    endereco = ", ".join(p for p in [
        re.sub(r"[\s,]+$", "", emitente.get("Endereço", "")),
        emitente.get("Bairro / Distrito", ""),
        f"{municipio}/{uf}" if municipio else "",
        f"CEP {emitente['CEP']}" if emitente.get("CEP") else "",
    ] if p)

    itens = _itens(abas[3])
    pagamentos = _pagamentos(abas[6])
    nota = dict(
        chave=re.sub(r"\D", "", geral.get("Chave de Acesso", "")),
        modelo=dados.get("Modelo"),
        numero=dados.get("Número") or geral.get("Número"),
        serie=dados.get("Série"),
        data_emissao=_data_iso(dados.get("Data de Emissão", "")),
        protocolo=protocolo.get("value") if protocolo else None,
        situacao=situacao[1].strip() if situacao else None,
        emitente_cnpj=emitente.get("CNPJ"),
        emitente_nome=emitente.get("Nome / Razão Social"),
        emitente_fantasia=emitente.get("Nome Fantasia") or None,
        emitente_ie=emitente.get("Inscrição Estadual"),
        emitente_endereco=endereco,
        emitente_municipio=municipio,
        emitente_uf=uf,
        qtd_itens=len(itens),
        valor_produtos=numero(totais.get("Valor Total dos Produtos")),
        valor_desconto=numero(totais.get("Valor Total dos Descontos")),
        valor_total=numero(totais.get("Valor Total da NFe") or dados.get("Valor Total da Nota Fiscal")),
        valor_tributos=numero(totais.get("Valor Aproximado dos Tributos")),
        detalhes={
            "Dados Gerais": geral,
            **secoes,
            "Cobrança": campos_por_secao(abas[6]),
            "Eventos": _tabela_cabecalho(eventos_tab),
        },
    )
    if len(nota["chave"]) != 44 or not nota["data_emissao"] or nota["valor_total"] is None:
        raise ValueError(f"nota incompleta: chave={nota['chave']!r} data={nota['data_emissao']!r}")
    return nota, itens, pagamentos
