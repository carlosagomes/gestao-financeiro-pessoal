"""Logins da janela remota: o que cada site precisa para a pessoa entrar pelo navegador do servidor.

Cada serviço diz onde a janela começa, quais campos preencher com o CPF e a senha cadastrados, para quais
domínios a página pode navegar e como saber que o login terminou (`logado`). Captcha e código de
verificação são SEMPRE da pessoa: aqui só preenchemos CPF/senha e, na Copel, enviamos o formulário depois
que ela marcou o reCAPTCHA (como o app antigo fazia).

Uma instância por janela (guarda o que já foi preenchido). Para o Nota Paraná, basta outra subclasse com
url_inicial "https://notaparana.pr.gov.br/nfprweb/", os campos da Central de Segurança (confira os seletores
no site) e logado pelo host notaparana.pr.gov.br, registrada em LOGINS.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page

from integracoes.models import COPEL, NOMES_SERVICO, SANEPAR

CARREGAR_MS = 60_000


def so_digitos(texto: str) -> str:
    return re.sub(r"\D", "", texto or "")


def host_permitido(host: str | None, dominios) -> bool:
    host = (host or "").lower().rstrip(".")
    return any(host == d or host.endswith("." + d) for d in dominios)


class Login:
    servico = ""
    nome = ""
    url_inicial = ""
    dominios: tuple[str, ...] = ()  # a página principal só navega para estes domínios (e subdomínios)
    campos: tuple[tuple[str, str], ...] = ()  # (seletor, "login" | "senha")
    reaproveitar_sessao = False  # abre com os cookies salvos (pode pular o captcha/2FA)
    pagina_dados = ""  # nome da URL com os dados do serviço (link depois do login)
    rotulo_dados = ""
    instrucoes: tuple[str, ...] = ()

    def __init__(self):
        self.preenchidos: set[str] = set()

    def valor(self, credencial, campo: str) -> str:
        return so_digitos(credencial.login) if campo == "login" else credencial.senha

    def navegou(self) -> None:
        """Página nova: os campos dela podem ser preenchidos de novo."""
        self.preenchidos.clear()

    async def preparar(self, page: Page, credencial) -> None:
        await page.goto(self.url_inicial, wait_until="load", timeout=CARREGAR_MS)
        await self.preencher(page, credencial)

    async def a_cada_ciclo(self, page: Page, credencial) -> None:
        """Chamado a cada segundo enquanto a janela está aberta e o login não terminou."""
        await self.preencher(page, credencial)

    async def preencher(self, page: Page, credencial) -> None:
        """Preenche cada campo uma vez por página, quando ele aparece vazio: se a pessoa apagar ou trocar o
        valor, ele não volta."""
        for seletor, campo in self.campos:
            if seletor in self.preenchidos:
                continue
            try:
                alvo = page.locator(seletor).first
                if not await alvo.is_visible():
                    continue
                self.preenchidos.add(seletor)
                valor = self.valor(credencial, campo)
                if valor and not await alvo.input_value():
                    await alvo.fill(valor, timeout=5_000)
            except PlaywrightError:  # página no meio de uma navegação
                pass

    async def logado(self, page: Page) -> bool:
        raise NotImplementedError


class LoginSanepar(Login):
    """Minha Sanepar: o login é na Central de Segurança do Paraná (captcha + código por e-mail/SMS)."""
    servico = SANEPAR
    nome = NOMES_SERVICO[SANEPAR]
    url_inicial = "https://clientes.sanepar.com.br/minha-conta"
    dominios = ("sanepar.com.br", "pr.gov.br", "acesso.gov.br")
    # Na Central, "#login" é o <body>: o CPF fica em #attribute_central (conferido em 06/10/2026).
    campos = (("input#attribute_central", "login"), ("input#password", "senha"))
    reaproveitar_sessao = True  # o login da Central pode continuar valendo
    pagina_dados = "consumo:agua"
    rotulo_dados = "Água"
    instrucoes = (
        "Seu CPF e sua senha já vêm preenchidos (opção “Central de Segurança”).",
        "Resolva o captcha e clique em Entrar.",
        "Digite o código que chegar no seu e-mail ou celular.",
        "Pronto: a janela fecha sozinha e as faturas começam a ser buscadas.",
    )
    BOTAO_ENTRAR = "#edit-openid-connect-client-central-login"  # "Entrar ou cadastrar"
    OPCAO_CENTRAL = "#btnCentral"  # entre as opções de login (gov.br, Google...), a do CPF e senha

    async def preparar(self, page: Page, credencial) -> None:
        await page.goto(self.url_inicial, wait_until="load", timeout=CARREGAR_MS)
        if await self.logado(page):
            return
        try:  # só abre o formulário; captcha, Entrar e código ficam com a pessoa
            await page.locator(self.BOTAO_ENTRAR).click(timeout=15_000)
            # Com a Central ainda logada (cookies salvos), o site volta direto para a Minha Sanepar.
            await page.wait_for_url(lambda url: "identidadedigital" in url or "entre-ou-cadastre-se" not in url,
                                    timeout=30_000)
            if "identidadedigital" in page.url:
                await page.locator(self.OPCAO_CENTRAL).click(timeout=15_000)
        except PlaywrightError:  # a pessoa clica
            pass

    async def a_cada_ciclo(self, page: Page, credencial) -> None:
        if "identidadedigital" in (urlparse(page.url).hostname or ""):  # os campos aparecem em etapas
            await self.preencher(page, credencial)

    async def logado(self, page: Page) -> bool:
        url = urlparse(page.url)
        if url.hostname != "clientes.sanepar.com.br" or url.path.startswith("/entre-ou-cadastre-se"):
            return False
        try:
            return await page.locator(self.BOTAO_ENTRAR).count() == 0
        except PlaywrightError:
            return False


class LoginCopel(Login):
    """Agência Virtual da Copel: reCAPTCHA v2. Depois do login a janela fecha na hora (nunca "1ª via")."""
    servico = COPEL
    nome = NOMES_SERVICO[COPEL]
    url_inicial = "https://www.copel.com/avaweb/paginas/consultaDebitos.jsf"
    dominios = ("copel.com",)
    CAMPO_CPF = "#formulario\\:numDoc"
    campos = ((CAMPO_CPF, "login"), ("#formulario\\:pass", "senha"))
    pagina_dados = "consumo:luz"
    rotulo_dados = "Luz"
    instrucoes = (
        "Seu CPF e sua senha já vêm preenchidos.",
        "Marque “Não sou um robô” (se ele não aparecer, clique em Entrar) e resolva as imagens, se pedir.",
        "O Entrar é automático: a janela fecha sozinha e as faturas começam a ser buscadas.",
    )
    # Sem widget na tela, getResponse() lança erro (a Copel só mostra o reCAPTCHA depois do primeiro Entrar).
    RECAPTCHA_OK = ("() => { try { return !!(window.grecaptcha && grecaptcha.getResponse().length); } "
                    "catch (e) { return false; } }")

    def __init__(self):
        super().__init__()
        self.enviado = False

    async def a_cada_ciclo(self, page: Page, credencial) -> None:
        await self.preencher(page, credencial)
        if "paginaLogin" not in urlparse(page.url).path:
            return
        try:
            # A pessoa resolveu o reCAPTCHA: o token aparece e o formulário vai uma vez só.
            resolvido = await page.evaluate(self.RECAPTCHA_OK)
            if resolvido and not self.enviado:
                self.enviado = True
                await page.locator("#formulario button[type=submit]").first.click(timeout=5_000)
            elif not resolvido:
                self.enviado = False  # expirou ou foi refeito
        except PlaywrightError:  # página no meio de uma navegação
            pass

    async def logado(self, page: Page) -> bool:
        url = urlparse(page.url)
        if not (url.hostname == "www.copel.com" and url.path.startswith("/avaweb/") and "paginaLogin" not in url.path):
            return False
        try:
            return await page.locator(self.CAMPO_CPF).count() == 0
        except PlaywrightError:
            return False


LOGINS: dict[str, type[Login]] = {SANEPAR: LoginSanepar, COPEL: LoginCopel}


def obter(servico: str) -> type[Login] | None:
    return LOGINS.get(servico)
