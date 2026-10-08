"""Sanepar (clientes.sanepar.com.br): faturas de água com a sessão salva, sem janela.

O login da Central de Segurança tem captcha e código por e-mail/SMS: quem resolve é a pessoa, na janela
remota ("Conectar" em Configurações). Aqui só se usa a sessão salva; se ela expirou, a tarefa termina
com erro e a sessão fica marcada como expirada (nunca se tenta logar daqui).
- O formulário /segunda-de-conta-paga responde por AJAX (Drupal): o PDF vem em base64 no DownloadFileCommand.
- Mês ainda não emitido: o site devolve o PDF da última conta paga, por isso a referência é conferida.
- A fatura em aberto (não paga) vem da API latest-invoice.
"""
from __future__ import annotations

import base64
import re
from typing import Optional
from urllib.parse import urlparse

from django.utils import timezone

from consumo import servicos
from consumo.leitores.sanepar_pdf import ler_fatura, texto_pdf
from consumo.models import FaturaSanepar
from integracoes.coletores.base import SessaoExpirada, marcar_expirada, navegador, salvar_sessao, sessao_salva
from integracoes.models import SANEPAR

URL_CONTA = "https://clientes.sanepar.com.br/minha-conta"
URL_2VIA = "https://clientes.sanepar.com.br/segunda-de-conta-paga"
URL_ULTIMA = "https://clientes.sanepar.com.br/api/sanepar-financial-status/latest-invoice"
MSG_EXPIROU = "Sessão da Sanepar expirou: use Conectar em Configurações."
MSG_SEM_SESSAO = "A Sanepar ainda não foi conectada: use Conectar em Configurações."


class AindaNaoEmitida(Exception):
    """Para o mês sem fatura emitida o site devolve o PDF da última conta paga."""


def logado(url: str) -> bool:
    partes = urlparse(url)
    return partes.hostname == "clientes.sanepar.com.br" and not partes.path.startswith("/entre-ou-cadastre-se")


def pdf_do_ajax(comandos: list[dict]) -> Optional[bytes]:
    """Resposta do Drupal: com fatura, um DownloadFileCommand com o PDF em base64; sem fatura, outra coisa."""
    for comando in comandos or []:
        if comando.get("command") == "DownloadFileCommand":
            return base64.b64decode(comando["file_url"])
    return None


def fatura_do_pdf(pdf: bytes, ref: str) -> dict:
    fatura = ler_fatura(texto_pdf(pdf))
    if fatura["referencia"] != ref:
        raise AindaNaoEmitida(ref)
    return fatura


def meses_a_buscar(anos: list[str], com_pdf: set[str], hoje: str) -> list[tuple[str, str]]:
    """Primeira vez: tudo que o site oferece. Depois: só os meses após a última fatura com PDF."""
    a_partir_de = max(com_pdf) if com_pdf else ""
    meses = []
    for ano in sorted(a for a in anos if a):
        for mes in (f"{m:02d}" for m in range(1, 13)):
            ref = f"{ano}-{mes}"
            if ref in com_pdf or ref <= a_partir_de or ref > hoje:
                continue
            meses.append((ano, mes))
    return meses


def baixar_pdf(page, ano: str, mes: str) -> Optional[bytes]:
    page.select_option("select[name=year]", ano)
    page.wait_for_timeout(600)
    page.select_option("select[name=month]", mes)
    with page.expect_response(lambda r: "ajax_form=1" in r.url, timeout=90_000) as resposta:
        page.locator("input[name=op]").click()
    comandos = resposta.value.json()
    page.wait_for_timeout(800)  # o Drupal redesenha o formulário
    return pdf_do_ajax(comandos)


def _referencias_com_pdf(usuario) -> set[str]:
    return set(FaturaSanepar.objects.filter(usuario=usuario).exclude(pdf="").values_list("referencia", flat=True))


def sincronizar(usuario, log) -> dict:
    salva = sessao_salva(usuario, SANEPAR)
    if salva is None or not salva.estado_cifrado:
        raise SessaoExpirada(MSG_SEM_SESSAO)
    if salva.expirada:
        raise SessaoExpirada(MSG_EXPIROU)
    com_pdf = _referencias_com_pdf(usuario)
    hoje = f"{timezone.localdate():%Y-%m}"
    novas = erros = 0
    with navegador(salva.estado) as contexto:
        page = contexto.new_page()
        log("Abrindo Minha Sanepar com a sessão salva…")
        page.goto(URL_CONTA, wait_until="networkidle")
        if not logado(page.url):
            marcar_expirada(usuario, SANEPAR)
            raise SessaoExpirada(MSG_EXPIROU)
        salvar_sessao(usuario, SANEPAR, contexto)

        page.goto(URL_2VIA, wait_until="networkidle")
        anos = page.eval_on_selector_all("select[name=year] option", "os => os.map(o => o.value)")
        meses = meses_a_buscar(anos, com_pdf, hoje)
        log(f"{len(com_pdf)} fatura(s) já salvas; {len(meses)} mês(es) para conferir")
        for ano, mes in meses:
            ref = f"{ano}-{mes}"
            try:
                pdf = baixar_pdf(page, ano, mes)
                if pdf is None:
                    log(f"  {ref}: sem fatura")
                    continue
                f = fatura_do_pdf(pdf, ref)
                servicos.salvar_fatura_sanepar(usuario, f, pdf=pdf, nome_pdf=f"{ref}.pdf")
                novas += 1
                log(f"  + {ref}: R$ {f['valor_total']:.2f}, {f['consumo_m3']} m³, vence {f['vencimento']}, {f['situacao']}")
            except AindaNaoEmitida:
                log(f"  {ref}: ainda não emitida")
            except Exception as erro:  # um mês com problema não para os outros
                erros += 1
                log(f"  ! {ref}: {erro}")
                page.goto(URL_2VIA, wait_until="networkidle")

        # A fatura do mês ainda não paga não está na "Segunda Via de Conta Paga": vem da API da home.
        ultima = (contexto.request.get(URL_ULTIMA).json() or {}).get("data") or {}
        ref_api = re.match(r"(\d{2})/(\d{4})", (ultima.get("raw_data") or {}).get("referencia", "") or "")
        if ref_api and ultima.get("status") != "paid":
            ref = f"{ref_api[2]}-{ref_api[1]}"
            if ref not in _referencias_com_pdf(usuario):
                servicos.salvar_fatura_sanepar(usuario, dict(referencia=ref, valor_total=ultima.get("total"),
                                                             vencimento=ultima.get("due_date"), situacao="Em aberto",
                                                             detalhes={"api": ultima}))
                log(f"  ~ {ref}: R$ {ultima.get('total')} em aberto (vence {ultima.get('due_date')})")
        salvar_sessao(usuario, SANEPAR, contexto)
    completadas = servicos.completar_consumo_sanepar(usuario)
    if completadas:
        log(f"{completadas} fatura(s) antiga(s) ganharam o consumo pelo histórico")
    mudancas = servicos.conciliar_agua(usuario)
    for m in mudancas:
        log(f"  Água de {m['mes']}: {m['antes']} -> {m['depois']} (fatura {m['referencia']}, {m['acao']})")
    log(f"Pronto: {novas} fatura(s) nova(s), {erros} erro(s)")
    return dict(novas=novas, erros=erros, conciliadas=len(mudancas))
