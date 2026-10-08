"""Copel (Agência Virtual, www.copel.com/avaweb): faturas de luz com a sessão da janela remota, sem janela.

O login tem reCAPTCHA do Google e a sessão dura poucos minutos: a pessoa entra pela janela remota
("Conectar" em Configurações) e esta sincronização roda logo em seguida. Sessão expirada = erro, nunca
tentativa de login daqui.
- historicoPagamento.jsf: referência, nº da fatura, situação, vencimento, pagamento e valor.
- O link "2 via" entrega o PDF (DANF3E) direto ou por um diálogo de download.
- NUNCA clicar em "Emitir 1ª via": a conta passa a ser considerada entregue e não é mais enviada.
"""
from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from consumo import servicos
from consumo.leitores.copel_pdf import ler_fatura
from consumo.models import FaturaCopel
from integracoes.coletores.base import SessaoExpirada, marcar_expirada, navegador, salvar_sessao, sessao_salva
from integracoes.models import COPEL

URL_DEBITOS = "https://www.copel.com/avaweb/paginas/consultaDebitos.jsf"
URL_HISTORICO = "https://www.copel.com/avaweb/paginas/historicoPagamento.jsf"
MSG_EXPIROU = "Sessão da Copel expirou: use Conectar em Configurações (a sessão dura poucos minutos)."
ROTULOS = ["Mês de referência", "Nr. da fatura", "Situação da fatura", "Origem", "Data de vencimento",
           "Data de pagamento", "Valor emitido (R$)"]
PRIMEIRA_VIA = re.compile(r"1\s*[ªaº°]\s*via|primeira\s*via", re.I)


class CliqueProibido(AssertionError):
    pass


def logado(url: str) -> bool:
    partes = urlparse(url)
    return (partes.hostname == "www.copel.com" and partes.path.startswith("/avaweb/")
            and "paginaLogin" not in partes.path)


def _iso(data: str) -> Optional[str]:
    m = re.fullmatch(r"(\d{2})/(\d{2})/(\d{4})", (data or "").strip())
    return f"{m[3]}-{m[2]}-{m[1]}" if m else None


def ler_historico(html: str) -> list[dict]:
    """Linhas do "Histórico de pagamento" (cada célula vem com o rótulo da coluna na frente)."""
    soup = BeautifulSoup(html, "html.parser")
    faturas = []
    for i, tr in enumerate(soup.select("[id$='dtListaHistoricoPagto_data'] > tr")):
        celulas = [td.get_text(" ", strip=True) for td in tr.find_all("td", recursive=False)]
        if len(celulas) < 7:
            continue
        v = [re.sub(rf"^{re.escape(r)}\s*", "", c).strip() for r, c in zip(ROTULOS, celulas)]
        if not re.fullmatch(r"\d{2}/\d{4}", v[0]):
            continue
        mes, ano = v[0].split("/")
        faturas.append(dict(linha=i, referencia=f"{ano}-{mes}", numero_fatura=v[1], situacao=v[2], origem=v[3],
                            vencimento=_iso(v[4]), data_pagamento=_iso(v[5]),
                            valor_total=float(v[6].replace(".", "").replace(",", "."))))
    return faturas


def clicar(locator) -> None:
    """Clique com trava: recusa qualquer coisa que pareça "1ª via" (texto, id ou título)."""
    descricao = " ".join(filter(None, [locator.inner_text(), locator.get_attribute("id"),
                                       locator.get_attribute("title"), locator.get_attribute("value")]))
    if PRIMEIRA_VIA.search(descricao) or "primeiravia" in descricao.lower():
        raise CliqueProibido("botão de 1ª via")
    locator.click()


def baixar_segunda_via(page, linha: int) -> bytes:
    """Clica no "2 via" da linha. O PDF vem direto ou por um diálogo; nunca usa o botão de 1ª via."""
    from playwright.sync_api import Error as PlaywrightError

    link = page.locator(f"[id='formHistoricoPagto:dtListaHistoricoPagto:{linha}:j_idt82'], "
                        f"[id^='formHistoricoPagto:dtListaHistoricoPagto:{linha}:']:text-is('2 via')").first
    downloads = []
    page.once("download", lambda d: downloads.append(d))
    clicar(link)
    dialogo = page.locator("[id='frmModalSegundaVia:modalSegundaVia']")
    botao = dialogo.locator("a, button").filter(has_text=re.compile("Download|Baixar|PDF", re.I)).first
    for _ in range(90):  # até 45 s: o PDF chega direto ou o diálogo de 2ª via abre
        if downloads:
            return Path(downloads[0].path()).read_bytes()
        try:
            if dialogo.is_visible() and botao.count():
                with page.expect_download(timeout=45_000) as download:
                    clicar(botao)
                return Path(download.value.path()).read_bytes()
        except PlaywrightError:
            pass
        page.wait_for_timeout(500)
    raise RuntimeError("o link de 2ª via não entregou PDF nem abriu o diálogo de download")


def dados_pdf(pdf: bytes) -> dict:
    with tempfile.NamedTemporaryFile(suffix=".pdf") as arquivo:
        arquivo.write(pdf)
        arquivo.flush()
        return ler_fatura(Path(arquivo.name))


def sincronizar(usuario, log) -> dict:
    salva = sessao_salva(usuario, COPEL)
    if salva is None or not salva.estado_cifrado or salva.expirada:
        raise SessaoExpirada(MSG_EXPIROU)
    com_pdf = set(FaturaCopel.objects.filter(usuario=usuario).exclude(pdf="").values_list("numero_fatura", flat=True))
    novas = atualizadas = erros = 0
    with navegador(salva.estado) as contexto:
        page = contexto.new_page()
        log("Abrindo a Agência Virtual da Copel com a sessão da janela…")
        page.goto(URL_DEBITOS, wait_until="networkidle")
        if not logado(page.url):
            marcar_expirada(usuario, COPEL)
            raise SessaoExpirada(MSG_EXPIROU)
        page.goto(URL_HISTORICO, wait_until="networkidle")
        if not logado(page.url):
            marcar_expirada(usuario, COPEL)
            raise SessaoExpirada(MSG_EXPIROU)
        faturas = ler_historico(page.content())
        log(f"{len(faturas)} fatura(s) no histórico de pagamento; {len(com_pdf)} já com PDF")
        for f in faturas:
            linha = f.pop("linha")
            pdf = None
            if f["numero_fatura"] not in com_pdf:
                try:
                    baixado = baixar_segunda_via(page, linha)
                    page.goto(URL_HISTORICO, wait_until="networkidle")  # volta para a lista após o download
                    f.update(dados_pdf(baixado))
                    pdf = baixado
                    novas += 1
                except CliqueProibido:
                    raise
                except Exception as erro:  # sem o PDF, a linha da tabela ainda vale (valor, vencimento, situação)
                    erros += 1
                    log(f"  ! {f['referencia']} ({f['numero_fatura']}): {erro}")
                    page.goto(URL_HISTORICO, wait_until="networkidle")
                    if not logado(page.url):
                        marcar_expirada(usuario, COPEL)
                        raise SessaoExpirada(MSG_EXPIROU) from None
            else:
                atualizadas += 1
            servicos.salvar_fatura_copel(usuario, f, pdf=pdf)
            log(f"  {f['referencia']}: R$ {f['valor_total']:.2f}, vence {f['vencimento']}, {f['situacao']}"
                + (", PDF novo" if pdf else ""))
        salvar_sessao(usuario, COPEL, contexto)
    mudancas = servicos.conciliar_luz(usuario)
    for m in mudancas:
        log(f"  Luz de {m['mes']}: {m['antes']} -> {m['depois']} (fatura {m['referencia']}, {m['acao']})")
    log(f"Pronto: {novas} PDF(s) novo(s), {atualizadas} atualizada(s), {erros} erro(s)")
    return dict(novas=novas, atualizadas=atualizadas, erros=erros, conciliadas=len(mudancas))
