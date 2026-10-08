"""Nota Paraná: login automático com CPF e senha (sem captcha) e importação incremental das notas.

O navegador só faz o login; a lista (JSON) e as notas (HTML) vêm por HTTP com a mesma sessão.
- O site aceita UMA sessão por vez: "mais de uma sessão ativa" -> /publico/sair e login de novo (isso
  derruba a sessão da pessoa no navegador dela).
- PERIGO: NotasFiscaisAjax com idDocFiscal REJEITA a nota no site. `_get` recusa essa chamada.
- Notas já importadas só têm o crédito atualizado ("A CALCULAR" -> valor).
"""
from __future__ import annotations

import json
import re
import time
from datetime import date
from typing import Optional
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from django.utils import timezone

from integracoes.coletores.base import (ErroColeta, LoginRecusado, credencial, navegador, salvar_sessao,
                                        sessao_salva)
from integracoes.models import NOTAPARANA, Credencial
from notas import servicos
from notas.leitores.nota_html import ler_nota, numero
from notas.models import Nota

BASE = "https://notaparana.pr.gov.br/nfprweb/"
URL_SAIR = BASE + "publico/sair"
PAUSA_S = 0.5  # entre uma nota e outra, para não sobrecarregar o site
ESPERA_LOGIN_S = 90
MESES_NP = {m: i for i, m in enumerate(
    ["JAN", "FEV", "MAR", "ABR", "MAI", "JUN", "JUL", "AGO", "SET", "OUT", "NOV", "DEZ"], 1)}


class SessaoPerdida(ErroColeta):
    pass


class ChamadaProibida(AssertionError):
    pass


# ---------- leitura (sem rede) ----------

def _inteiro(texto: str) -> Optional[int]:
    return int(texto) if texto.strip().isdigit() else None


def ler_extrato(html: str, hoje: Optional[date] = None) -> tuple[list[str], list[dict], dict]:
    """Da página Minhas Notas: períodos ('01/MM/AAAA'), resumo de cada mês e o "Meu placar"."""
    soup = BeautifulSoup(html, "html.parser")
    periodos, resumos = [], []
    # O período sai da 1ª coluna ("OUT/2026"): o atributo rel vem como "Thu Oct 01 00:00:00 BRT 2026".
    for tr in soup.select("#minhasnotas tr"):
        celulas = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
        data = re.fullmatch(r"([A-Z]{3})/(\d{4})", celulas[0]) if len(celulas) >= 5 else None
        if not data or data[1] not in MESES_NP:
            continue
        mes, ano = f"{MESES_NP[data[1]]:02d}", data[2]
        periodos.append(f"01/{mes}/{ano}")
        resumos.append(dict(periodo=f"{ano}-{mes}", total_notas=_inteiro(celulas[1]),
                            bilhetes=_inteiro(celulas[2]), valor_total=numero(celulas[3]), creditos=celulas[4]))
    if not periodos:  # plano B: do primeiro período informado pelo site até hoje
        m = re.search(r'paramIniPerido\s*:\s*"(\d{2})/(\d{2})/(\d{4})"', html)
        if not m:
            raise SessaoPerdida("Não encontrei a lista de períodos em Minhas Notas (a sessão caiu?).")
        hoje = hoje or timezone.localdate()
        ano, mes = int(m[3]), int(m[2])
        while (ano, mes) <= (hoje.year, hoje.month):
            periodos.append(f"01/{mes:02d}/{ano}")
            ano, mes = (ano + 1, 1) if mes == 12 else (ano, mes + 1)
    placar = {}
    rotulo = soup.find(string=re.compile("Total de notas recebidas"))
    if rotulo:
        for tr in rotulo.find_parent("table").find_all("tr"):
            celulas = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
            if len(celulas) == 2:
                placar[celulas[0].rstrip(":")] = celulas[1]
    return periodos, resumos, placar


def montar_nota(html: bytes, listagem: dict) -> tuple[dict, list[dict], list[dict]]:
    """Nota lida do HTML mais os dados da listagem (id no site, crédito)."""
    nota, itens, pagamentos = ler_nota(html)
    nota.update(
        id_doc_fiscal=listagem.get("idDocFiscal"),
        credito=numero(listagem.get("credito")),
        situacao_credito=listagem.get("situacao"),
        url=f"{BASE}NotaFiscalHtml?tpDoc={listagem.get('tpDoc')}&idDocFiscal={listagem.get('idDocFiscal')}",
    )
    nota["detalhes"]["Nota Paraná"] = listagem
    return nota, itens, pagamentos


def verificar_chamada(caminho: str, params: dict) -> None:
    """NotasFiscaisAjax com idDocFiscal REJEITA a nota no site: aqui só se consulta por período."""
    alvo = caminho.lower()
    if "notasfiscaisajax" in alvo and ("iddocfiscal" in alvo or any("iddocfiscal" in str(k).lower() for k in params)):
        raise ChamadaProibida("chamada que rejeita nota")


# ---------- site ----------

def _logado(url: str) -> bool:
    return urlparse(url).hostname == "notaparana.pr.gov.br"


def _sessao_duplicada(page) -> bool:
    return page.get_by_text("mais de uma sessão ativa").count() > 0


def _fazer_login(page, cpf: str, senha: str, log) -> None:
    from playwright.sync_api import Error as PlaywrightError

    if _logado(page.url):  # o login do Estado ainda estava ativo e redirecionou sozinho
        return
    log("Entrando no Nota Paraná com o CPF e a senha salvos…")
    page.fill("#attribute", cpf)
    page.fill("#password", senha)
    page.click("#authForm input[type=submit]")
    erro = page.locator("[id='error.message']")
    for _ in range(ESPERA_LOGIN_S * 2):
        if _logado(page.url):
            page.wait_for_load_state("networkidle")
            return
        try:
            if erro.is_visible():
                # Para na primeira recusa: insistir com senha errada pode bloquear a conta.
                raise LoginRecusado("Login recusado pelo Nota Paraná: " + erro.inner_text().strip()
                                    + " Confira o CPF e a senha em Configurações.")
        except PlaywrightError:  # página no meio de uma navegação
            pass
        page.wait_for_timeout(500)
    raise ErroColeta("Tempo esgotado esperando o login do Nota Paraná (o site pediu alguma verificação extra?).")


def _entrar(page, cpf: str, senha: str, log) -> None:
    page.goto(BASE, wait_until="networkidle")
    if not _logado(page.url):
        _fazer_login(page, cpf, senha, log)


def _get(req, caminho: str, params: dict) -> bytes:
    verificar_chamada(caminho, params)
    resposta = req.get(BASE + caminho, params=params, timeout=120_000)
    corpo = resposta.body()
    if "identidadedigital" in resposta.url or b"mais de uma sess" in corpo:
        raise SessaoPerdida("A sessão do Nota Paraná caiu (alguém entrou no site enquanto sincronizava?).")
    if not resposta.ok:
        raise ErroColeta(f"O Nota Paraná respondeu HTTP {resposta.status} em {caminho}.")
    return corpo


def listar_notas(req, periodo: str) -> list[dict]:
    for tentativa in range(2):  # o próprio site repete a consulta quando vem vazia
        corpo = _get(req, "NotasFiscaisAjax", {"periodo": periodo, "situacao": "1"})
        try:
            notas = json.loads(corpo.decode("latin-1"))
        except ValueError:
            raise SessaoPerdida(f"Resposta inesperada ao listar {periodo[3:]}.") from None
        if notas or tentativa:
            return notas
        time.sleep(1)
    return []


def sincronizar(usuario, log) -> dict:
    cred = credencial(usuario, NOTAPARANA)
    cpf, senha = re.sub(r"\D", "", cred.login), cred.senha
    if hasattr(log, "ocultar"):
        log.ocultar(cred.login, senha)
    ja_importadas = servicos.chaves_importadas(usuario)
    novas = atualizadas = erros = 0
    salva = sessao_salva(usuario, NOTAPARANA)
    estado = salva.estado if salva and not salva.expirada else None
    with navegador(estado) as contexto:
        page = contexto.new_page()
        try:
            _entrar(page, cpf, senha, log)
            if _sessao_duplicada(page):
                log("O Nota Paraná só aceita uma sessão por vez: encerrando a outra (o seu navegador sai do site).")
                page.goto(URL_SAIR, wait_until="networkidle")
                _entrar(page, cpf, senha, log)
                if _sessao_duplicada(page):
                    raise ErroColeta("Não consegui encerrar a outra sessão do Nota Paraná.")
        except LoginRecusado:
            # Sem novas tentativas automáticas até a pessoa salvar a senha de novo.
            Credencial.objects.filter(pk=cred.pk).update(ativo=False)
            raise
        if not cred.ativo:
            Credencial.objects.filter(pk=cred.pk).update(ativo=True)
        salvar_sessao(usuario, NOTAPARANA, contexto)
        log("Logado. Lendo Minhas Notas…")
        req = contexto.request
        extrato = _get(req, "Extrato", {}).decode("latin-1")
        periodos, resumos, placar = ler_extrato(extrato)
        servicos.salvar_periodos(usuario, resumos)
        if placar:
            servicos.salvar_placar(usuario, placar)
        ordem = sorted(periodos, key=lambda p: (p[6:], p[3:5]))
        log(f"{len(periodos)} períodos ({ordem[0][3:]} a {ordem[-1][3:]}); {len(ja_importadas)} notas já salvas")

        for periodo in periodos:
            listagem = listar_notas(req, periodo)
            pendentes = []
            for nf in listagem:
                chave = nf.get("chaveAcesso")
                if chave in ja_importadas:
                    servicos.atualizar_credito(usuario, chave, numero(nf.get("credito")), nf.get("situacao"))
                    atualizadas += 1
                elif str(nf.get("tpDoc")) in ("1", "2"):  # 1 = NF-e, 2 = NFC-e
                    pendentes.append(nf)
                else:
                    log(f"  ignorando {nf.get('tpDocDescricao')} {nf.get('nrDoc')} (tipo sem página de consulta)")
            log(f"{periodo[3:]}: {len(listagem)} notas, {len(pendentes)} novas")

            for nf in pendentes:
                chave = nf["chaveAcesso"]
                try:
                    html = _get(req, "NotaFiscalHtml",
                                {"tpDoc": nf["tpDoc"], "idDocFiscal": nf["idDocFiscal"], "eCompleta": "true"})
                    nota, itens, pagamentos = montar_nota(html, nf)
                    servicos.salvar_nota(usuario, nota, itens, pagamentos, html=html)
                    ja_importadas.add(chave)
                    novas += 1
                    log(f"  + {nota['data_emissao'][:10]} {(nota['emitente_nome'] or '')[:40]} "
                        f"R$ {nota['valor_total']:.2f} ({nota['qtd_itens']} itens)")
                except SessaoPerdida:
                    raise
                except Exception as erro:  # uma nota com problema não para as outras
                    erros += 1
                    log(f"  ! nota {nf.get('nrDoc')} de {nf.get('formecedor')}: {erro}")
                time.sleep(PAUSA_S)
        salvar_sessao(usuario, NOTAPARANA, contexto)
    log(f"Pronto: {novas} nova(s), {atualizadas} com crédito atualizado, {erros} erro(s)")
    return dict(novas=novas, atualizadas=atualizadas, erros=erros, periodos=len(periodos), placar=placar)


def reprocessar(usuario, log) -> dict:
    """Relê o HTML salvo de cada nota (útil quando o leitor melhora), sem acessar o site.
    Crédito, id no site e categoria escolhida à mão continuam como estão."""
    ok = erros = 0
    for nota_db in Nota.objects.filter(usuario=usuario).exclude(html="").order_by("data_emissao"):
        try:
            with nota_db.html.open("rb") as arquivo:
                html = arquivo.read()
            nota, itens, pagamentos = ler_nota(html)
            nota.update(id_doc_fiscal=nota_db.id_doc_fiscal, credito=nota_db.credito,
                        situacao_credito=nota_db.situacao_credito, url=nota_db.url)
            nota["detalhes"]["Nota Paraná"] = (nota_db.detalhes or {}).get("Nota Paraná", {})
            servicos.salvar_nota(usuario, nota, itens, pagamentos)
            ok += 1
        except Exception as erro:
            erros += 1
            log(f"  ! {nota_db.chave}: {erro}")
    log(f"{ok} nota(s) relida(s), {erros} com erro")
    return dict(novas=0, atualizadas=ok, erros=erros)
