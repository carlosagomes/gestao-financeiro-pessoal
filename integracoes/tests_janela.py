"""Janela remota de login: página, WebSocket e o login de ponta a ponta com um site falso (sem Copel/Sanepar).

  DJANGO_TEST_DB=test_gfp_janela .venv/bin/python manage.py test integracoes.tests_janela --noinput
"""
import asyncio
import json
from types import SimpleNamespace
from unittest import mock

from channels.db import database_sync_to_async
from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator
from django.contrib.auth.models import AnonymousUser
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from playwright.async_api import Error as PlaywrightError

from contas.models import Usuario
from integracoes import consumers, logins
from integracoes.models import COPEL, SANEPAR, Credencial, SessaoServico
from integracoes.routing import websocket_urlpatterns

SENHA = "senha-bem-forte-123"
CPF = "123.456.789-09"
SENHA_SITE = "senha-do-site-xyz"
COOKIE = "cookie-secreto-123"


def criar_usuario(email: str) -> Usuario:
    return Usuario.objects.create_user(email, SENHA, nome="Teste da Silva")


def criar_credencial(usuario, servico: str = COPEL) -> Credencial:
    credencial = Credencial(usuario=usuario, servico=servico)
    credencial.login = CPF
    credencial.senha = SENHA_SITE
    credencial.save()
    return credencial


class PaginaJanelaTest(TestCase):
    def setUp(self):
        self.usuario = criar_usuario("ana@exemplo.com")
        self.client.force_login(self.usuario)

    def test_exige_login(self):
        self.client.logout()
        resposta = self.client.get("/integracoes/copel/conectar/")
        self.assertRedirects(resposta, f"{reverse('contas:entrar')}?next=/integracoes/copel/conectar/",
                             fetch_redirect_response=False)

    def test_servico_sem_janela_404(self):
        for servico in ("xyz", "notaparana"):
            self.assertEqual(self.client.get(f"/integracoes/{servico}/conectar/").status_code, 404)

    def test_sem_credencial_manda_para_configuracoes(self):
        resposta = self.client.get(reverse("janela:conectar", args=[SANEPAR]))
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "Falta cadastrar o CPF e a senha da Sanepar")
        self.assertContains(resposta, f'href="{reverse("integracoes:configuracoes")}"')
        self.assertNotContains(resposta, "janela-canvas")

    def test_com_credencial_mostra_a_janela(self):
        criar_credencial(self.usuario, COPEL)
        resposta = self.client.get(reverse("janela:conectar", args=[COPEL]))
        self.assertContains(resposta, "Conectar Copel")
        self.assertContains(resposta, 'data-ws="/ws/navegador/copel/"')
        self.assertContains(resposta, "janela-canvas")
        self.assertContains(resposta, "Não sou um robô")
        self.assertContains(resposta, reverse("consumo:luz"))
        self.assertContains(resposta, "integracoes/janela.js")
        for segredo in (CPF, "12345678909", SENHA_SITE):
            self.assertNotContains(resposta, segredo)

    def test_sanepar_fala_do_codigo(self):
        criar_credencial(self.usuario, SANEPAR)
        resposta = self.client.get(reverse("janela:conectar", args=[SANEPAR]))
        self.assertContains(resposta, "e-mail ou celular")
        self.assertContains(resposta, reverse("consumo:agua"))

    def test_credencial_de_outro_usuario_nao_vale(self):
        criar_credencial(criar_usuario("bia@exemplo.com"), SANEPAR)
        resposta = self.client.get(reverse("janela:conectar", args=[SANEPAR]))
        self.assertContains(resposta, "Falta cadastrar")


class RegrasTest(SimpleTestCase):
    def test_enderecos_internos_bloqueados(self):
        for host in ("127.0.0.1", "localhost", "db", "10.0.0.5", "192.168.1.1", "169.254.169.254", "::1",
                     "[::1]", "nas.local", "", None):
            self.assertTrue(consumers._endereco_interno(host), host)
        for host in ("www.copel.com", "auth-cs.identidadedigital.pr.gov.br", "8.8.8.8"):
            self.assertFalse(consumers._endereco_interno(host), host)

    def test_dominios_do_servico(self):
        dominios = logins.LoginSanepar.dominios
        self.assertTrue(logins.host_permitido("clientes.sanepar.com.br", dominios))
        self.assertTrue(logins.host_permitido("auth-cs.identidadedigital.pr.gov.br", dominios))
        for host in ("evil-sanepar.com.br", "sanepar.com.br.exemplo.com", "www.copel.com", None):
            self.assertFalse(logins.host_permitido(host, dominios), host)

    def test_teclas_permitidas(self):
        self.assertEqual(consumers._tecla({"tecla": "Enter"}), "Enter")
        self.assertEqual(consumers._tecla({"tecla": "Tab", "modificadores": ["Shift", "Meta"]}), "Shift+Tab")
        self.assertEqual(consumers._tecla({"tecla": "A", "modificadores": ["Control"]}), "Control+a")
        for msg in ({"tecla": "a"}, {"tecla": "F12"}, {"tecla": "Control+Alt+Delete"}, {"tecla": 3}):
            self.assertIsNone(consumers._tecla(msg), msg)
        self.assertEqual(consumers._texto("12\n3\x00" + "x" * 500), "123" + "x" * (consumers.TEXTO_MAX - 3))

    def test_tamanho_pedido_pelo_cliente(self):
        self.assertEqual(consumers._tamanho({"query_string": b""}), consumers.TAMANHO_PADRAO)
        self.assertEqual(consumers._tamanho({"query_string": b"largura=390&altura=700"}), (390, 700))
        self.assertEqual(consumers._tamanho({"query_string": b"largura=10&altura=99999"}), (360, 900))
        self.assertEqual(consumers._tamanho({"query_string": b"largura=x&altura=1"}), consumers.TAMANHO_PADRAO)


PAGINA = """<!doctype html><html><body style="margin:0;font:16px sans-serif">
<button id="botao" style="position:absolute;left:0;top:0;width:200px;height:100px"
        onclick="window.cliques = (window.cliques || 0) + 1">Clique</button>
<input id="livre" style="position:absolute;left:0;top:120px;width:300px;height:40px">
<input id="cpf" style="position:absolute;left:0;top:180px;width:300px;height:40px">
<button id="entrar" style="position:absolute;left:0;top:240px;width:200px;height:100px"
        onclick="window.logado = true">Entrar</button>
</body></html>"""


class LoginFalso(logins.Login):
    """Site de mentira: o botão "Entrar" faz o login; um cookie faz o papel da sessão."""
    servico = COPEL
    nome = "Copel"
    campos = (("#cpf", "login"),)
    pagina_dados = "consumo:luz"

    async def preparar(self, page, credencial):
        await page.context.add_cookies([{"name": "sessao", "value": COOKIE, "url": "https://exemplo.com.br/"}])
        await page.set_content(PAGINA)
        await self.preencher(page, credencial)

    async def logado(self, page):
        return await page.evaluate("() => window.logado === true")


def e_estado(estado):
    return lambda m: isinstance(m, dict) and m.get("tipo") == "estado" and m.get("estado") == estado


def e_tipo(*tipos):
    return lambda m: isinstance(m, dict) and m.get("tipo") in tipos


def e_quadro(m):
    return isinstance(m, bytes)


@override_settings(JANELA_REMOTA_MAX=4)
class JanelaRemotaTest(TransactionTestCase):
    """O consumidor de verdade, com Chromium de verdade, apontado para LoginFalso."""

    def setUp(self):
        self.usuario = criar_usuario("ana@exemplo.com")
        for alvo in (mock.patch.dict(logins.LOGINS, {COPEL: LoginFalso}), mock.patch.object(consumers, "CICLO_S", 0.2)):
            alvo.start()
            self.addCleanup(alvo.stop)

    def comunicador(self, usuario, servico=COPEL):
        comunicador = WebsocketCommunicator(URLRouter(websocket_urlpatterns), f"/ws/navegador/{servico}/")
        comunicador.scope["user"] = usuario
        return comunicador

    async def esperar(self, comunicador, condicao, limite=20.0):
        """Lê o que o servidor mandar até `condicao` aceitar (dict de JSON ou bytes de um quadro)."""
        loop = asyncio.get_running_loop()
        prazo = loop.time() + limite
        while True:
            saida = await comunicador.receive_output(timeout=max(0.1, prazo - loop.time()))
            if saida["type"] == "websocket.close":
                msg = {"tipo": "_fechou", "code": saida.get("code")}
            elif saida.get("bytes") is not None:
                msg = saida["bytes"]
            else:
                msg = json.loads(saida["text"])
            if condicao(msg):
                return msg

    async def ate(self, funcao, esperado, limite=10.0):
        valor = None
        for _ in range(int(limite / 0.1)):
            valor = await funcao()
            if valor == esperado:
                return
            await asyncio.sleep(0.1)
        self.fail(f"esperava {esperado!r}, ficou {valor!r}")

    async def test_sem_login_recusa(self):
        conectado, _ = await self.comunicador(AnonymousUser()).connect()
        self.assertFalse(conectado)

    async def test_servico_invalido_recusa(self):
        conectado, _ = await self.comunicador(self.usuario, "xyz").connect()
        self.assertFalse(conectado)

    async def test_sem_credencial_avisa_e_fecha(self):
        comunicador = self.comunicador(self.usuario)
        conectado, _ = await comunicador.connect()
        self.assertTrue(conectado)
        msg = await comunicador.receive_json_from(timeout=5)
        self.assertEqual((msg["tipo"], msg["codigo"]), ("erro", "sem_credencial"))
        self.assertEqual(await comunicador.receive_output(timeout=5), {"type": "websocket.close", "code": 4004})
        self.assertNotIn(self.usuario.pk, consumers._janelas)

    @override_settings(JANELA_REMOTA_MAX=0)
    async def test_limite_de_janelas(self):
        await database_sync_to_async(criar_credencial)(self.usuario)
        comunicador = self.comunicador(self.usuario)
        await comunicador.connect()
        msg = await comunicador.receive_json_from(timeout=5)
        self.assertEqual(msg["codigo"], "lotado")
        self.assertNotIn(self.usuario.pk, consumers._janelas)

    async def test_quadros_clique_e_teclado(self):
        await database_sync_to_async(criar_credencial)(self.usuario)
        comunicador = self.comunicador(self.usuario)
        try:
            conectado, _ = await comunicador.connect()
            self.assertTrue(conectado)
            await self.esperar(comunicador, e_estado("ao_vivo"))
            quadro = await self.esperar(comunicador, e_quadro)
            self.assertTrue(quadro.startswith(b"\xff\xd8"))  # JPEG
            janela = consumers._janelas[self.usuario.pk]
            self.assertEqual(janela.tela, consumers.TAMANHO_PADRAO)
            page = janela.page
            self.assertEqual(await page.input_value("#cpf"), "12345678909")  # CPF já preenchido, só dígitos

            await comunicador.send_json_to({"tipo": "clique", "x": 100, "y": 50})
            await self.ate(lambda: page.evaluate("window.cliques || 0"), 1)
            await comunicador.send_json_to({"tipo": "clique", "x": 100, "y": 140})  # foca #livre
            await comunicador.send_json_to({"tipo": "texto", "texto": "abç"})
            await comunicador.send_json_to({"tipo": "tecla", "tecla": "Backspace"})
            await comunicador.send_json_to({"tipo": "tecla", "tecla": "F12"})  # fora da lista: ignorada
            await comunicador.send_json_to({"tipo": "clique", "x": "x", "y": None})  # inválido: ignorado
            await self.ate(lambda: page.input_value("#livre"), "ab")

            # A página principal só navega pelos domínios do serviço (LoginFalso não tem nenhum).
            with self.assertRaises(PlaywrightError):
                await page.goto("https://exemplo.com.br/")
            aviso = await self.esperar(comunicador, e_tipo("aviso"))
            self.assertIn("só abre o site da Copel", aviso["mensagem"])

            await comunicador.send_json_to({"tipo": "fechar"})
            await self.esperar(comunicador, e_tipo("fechado"))
            self.assertEqual((await self.esperar(comunicador, e_tipo("_fechou")))["code"], 4000)
            await self.ate(lambda: asyncio.sleep(0, self.usuario.pk in consumers._janelas), False)
            self.assertIsNone(janela.page)  # navegador fechado
        finally:
            await consumers.fechar_todas()
            await comunicador.disconnect()

    async def test_login_salva_sessao_cifrada_e_enfileira(self):
        def preparar():
            criar_credencial(self.usuario)
            antiga = SessaoServico(usuario=self.usuario, servico=COPEL, expirada=True)
            antiga.estado = {"cookies": [], "origins": []}
            antiga.save()
        await database_sync_to_async(preparar)()
        comunicador = self.comunicador(self.usuario)
        try:
            with mock.patch("integracoes.fila.enfileirar", return_value=SimpleNamespace(pk=77)) as enfileirar:
                await comunicador.connect()
                await self.esperar(comunicador, e_estado("ao_vivo"))
                await comunicador.send_json_to({"tipo": "clique", "x": 100, "y": 290})  # "Entrar"
                msg = await self.esperar(comunicador, e_tipo("sucesso", "erro"), limite=30)
                self.assertEqual(msg, {"tipo": "sucesso", "mensagem": "Conectado! Buscando suas faturas…",
                                       "tarefa": 77})
                self.assertEqual((await self.esperar(comunicador, e_tipo("_fechou")))["code"], 4000)
            enfileirar.assert_called_once()
            (usuario, servico), nomeados = enfileirar.call_args
            self.assertEqual((usuario.pk, servico, nomeados), (self.usuario.pk, COPEL, {"origem": "login"}))

            sessao = await database_sync_to_async(SessaoServico.objects.get)(usuario=self.usuario, servico=COPEL)
            self.assertFalse(sessao.expirada)
            self.assertNotIn(COOKIE, sessao.estado_cifrado)
            self.assertIn(COOKIE, [c["value"] for c in sessao.estado["cookies"]])
            await self.ate(lambda: asyncio.sleep(0, self.usuario.pk in consumers._janelas), False)
        finally:
            await consumers.fechar_todas()
            await comunicador.disconnect()

    async def test_outra_aba_assume_a_janela(self):
        await database_sync_to_async(criar_credencial)(self.usuario)
        primeira, segunda = self.comunicador(self.usuario), self.comunicador(self.usuario)
        try:
            await primeira.connect()
            await self.esperar(primeira, e_quadro)
            janela = consumers._janelas[self.usuario.pk]
            await segunda.connect()
            msg = await self.esperar(primeira, e_tipo("erro"))
            self.assertEqual(msg["codigo"], "outra_aba")
            await self.esperar(segunda, e_tipo("estado"))
            await self.esperar(segunda, e_quadro)  # recebe a última tela na hora
            self.assertIs(consumers._janelas[self.usuario.pk], janela)  # mesmo navegador, captcha preservado
            await segunda.send_json_to({"tipo": "fechar"})
            await self.esperar(segunda, e_tipo("fechado"))
        finally:
            await consumers.fechar_todas()
            await primeira.disconnect()
            await segunda.disconnect()

    async def test_outro_usuario_nunca_pega_a_janela(self):
        """Cada pessoa tem a própria janela: a de outro usuário não é reaproveitada nem derrubada, e cada uma
        abre com a credencial do próprio dono."""
        outro = await database_sync_to_async(criar_usuario)("bruno@exemplo.com")
        await database_sync_to_async(criar_credencial)(self.usuario)
        cred_outro = await database_sync_to_async(criar_credencial)(outro)
        da_ana, do_bruno = self.comunicador(self.usuario), self.comunicador(outro)
        try:
            await da_ana.connect()
            await self.esperar(da_ana, e_quadro)
            janela_ana = consumers._janelas[self.usuario.pk]
            await do_bruno.connect()
            await self.esperar(do_bruno, e_quadro)
            janela_bruno = consumers._janelas[outro.pk]
            self.assertIsNot(janela_bruno, janela_ana)
            self.assertEqual(janela_bruno.credencial.pk, cred_outro.pk)
            self.assertIs(janela_ana.consumidor.janela, janela_ana)  # a Ana continua ligada à dela
            self.assertIs(consumers._janelas[self.usuario.pk], janela_ana)
            await do_bruno.send_json_to({"tipo": "fechar"})
            await self.esperar(do_bruno, e_tipo("fechado"))
            await da_ana.send_json_to({"tipo": "clique", "x": 100, "y": 50})  # a da Ana segue viva
            await self.ate(lambda: janela_ana.page.evaluate("window.cliques || 0"), 1)
        finally:
            await consumers.fechar_todas()
            await da_ana.disconnect()
            await do_bruno.disconnect()

    async def test_fecha_por_inatividade(self):
        await database_sync_to_async(criar_credencial)(self.usuario)
        comunicador = self.comunicador(self.usuario)
        try:
            with (mock.patch.object(consumers, "LIMITE_INATIVO_S", 2.0),
                  mock.patch.object(consumers, "AVISO_INATIVO_S", 1)):
                await comunicador.connect()
                aviso = await self.esperar(comunicador, e_tipo("aviso"))
                self.assertIn("a janela fecha em", aviso["mensagem"])
                msg = await self.esperar(comunicador, e_tipo("erro"))
                self.assertEqual(msg["codigo"], "inativo")
                self.assertEqual((await self.esperar(comunicador, e_tipo("_fechou")))["code"], 4008)
        finally:
            await consumers.fechar_todas()
            await comunicador.disconnect()
