"""Janela remota de login (captcha/2FA): o Chromium roda no servidor e a tela vai para o navegador da pessoa.

WebSocket ws/navegador/<servico>/ (com ?largura=&altura= o cliente pede outro tamanho, p.ex. no celular).
  servidor → cliente
    binário                                   um quadro JPEG da tela
    {"tipo": "tela", "largura", "altura"}     tamanho da página remota em px (quando muda)
    {"tipo": "estado", "estado", "mensagem"}  abrindo | ao_vivo | salvando
    {"tipo": "aviso", "mensagem"}             para um toast (alerta do site, endereço bloqueado...)
    {"tipo": "sucesso" | "erro" | "fechado"}  mensagem final, com "mensagem" (e "codigo" no erro); depois o
                                              servidor fecha o socket e o cliente não reconecta
  cliente → servidor (coordenadas em px da página remota)
    {"tipo": "clique", x, y, cliques?}   {"tipo": "pressionar" | "soltar" | "mover", x, y}
    {"tipo": "rolar", x, y, dx, dy}      {"tipo": "tecla", tecla, modificadores?}   {"tipo": "texto", texto}
    {"tipo": "recarregar"}               {"tipo": "fechar"}

Uma janela por usuário (dicionário deste processo). Outra conexão para o mesmo serviço assume a janela: é
assim que o cliente volta depois de uma queda (celular indo ler o código no e-mail) sem perder o captcha/2FA.
Para outro serviço, a anterior fecha. Sem ninguém conectado a janela espera GRACA_S e fecha. Limites:
LIMITE_TOTAL_S no total e LIMITE_INATIVO_S sem nenhuma ação da pessoa.
Nunca vão para o log: CPF, senha, quadros, texto digitado ou URLs.
"""
from __future__ import annotations

import asyncio
import base64
import importlib
import ipaddress
import json
import logging
import math
import time
import unicodedata
import weakref
from contextlib import suppress
from urllib.parse import parse_qs, urlparse

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from cryptography.fernet import InvalidToken
from django.conf import settings
from playwright.async_api import Error as PlaywrightError

from integracoes import logins
from integracoes.models import Credencial, SessaoServico

logger = logging.getLogger(__name__)

TAMANHO_PADRAO = (1100, 760)
LARGURAS, ALTURAS = (360, 1280), (480, 900)
CICLO_S = 1.0
LIMITE_TOTAL_S = 10 * 60
LIMITE_INATIVO_S = 3 * 60
AVISO_INATIVO_S = 30
GRACA_S = 60
QUADROS_POR_S = 12
QUALIDADE_JPEG = 60
TEXTO_MAX = 200
MAX_JANELAS_PADRAO = 4  # por processo; settings.JANELA_REMOTA_MAX muda
ARGS_CHROMIUM = ["--disable-dev-shm-usage"]

# Códigos de fechamento do WebSocket.
FIM, SEM_CREDENCIAL, TEMPO, OUTRA_ABA, FALHA, LOTADO = 4000, 4004, 4008, 4009, 4011, 4029
FINAIS = ("sucesso", "erro", "fechado")

TECLAS = {"Enter", "Backspace", "Tab", "Delete", "Escape", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight",
          "Home", "End", "PageUp", "PageDown"}
MODIFICADORES = ("Shift", "Control", "Alt")  # o Cmd do Mac chega como Control (o servidor é Linux)
ATALHOS = {"a", "z", "y"}  # com Control: selecionar tudo, desfazer, refazer

_janelas: dict[int, Janela] = {}
_travas: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def _trava() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    if loop not in _travas:
        _travas[loop] = asyncio.Lock()
    return _travas[loop]


def _limite() -> int:
    return getattr(settings, "JANELA_REMOTA_MAX", MAX_JANELAS_PADRAO)


async def fechar_todas() -> None:
    for janela in list(_janelas.values()):
        await janela.fechar()


def _erro(codigo: str, mensagem: str) -> dict:
    return {"tipo": "erro", "codigo": codigo, "mensagem": mensagem}


def _numero(valor, minimo: float = -1e6, maximo: float = 1e6) -> float | None:
    if isinstance(valor, bool) or not isinstance(valor, (int, float)) or not math.isfinite(valor):
        return None
    return min(max(float(valor), minimo), maximo)


def _tecla(msg: dict) -> str | None:
    tecla = msg.get("tecla")
    pedidos = msg.get("modificadores") if isinstance(msg.get("modificadores"), list) else []
    usados = [m for m in MODIFICADORES if m in pedidos]
    if tecla in TECLAS:
        return "+".join([*usados, tecla])
    if isinstance(tecla, str) and tecla.lower() in ATALHOS and "Control" in usados:
        return "+".join([*usados, tecla.lower()])
    return None


def _texto(valor) -> str:
    if not isinstance(valor, str):
        return ""
    return "".join(c for c in valor if unicodedata.category(c) != "Cc")[:TEXTO_MAX]


def _tamanho(scope) -> tuple[int, int]:
    pedido = parse_qs((scope.get("query_string") or b"").decode(errors="ignore"))
    try:
        largura, altura = int(pedido["largura"][0]), int(pedido["altura"][0])
    except (KeyError, IndexError, ValueError):
        return TAMANHO_PADRAO
    return min(max(largura, LARGURAS[0]), LARGURAS[1]), min(max(altura, ALTURAS[0]), ALTURAS[1])


def _endereco_interno(host: str | None) -> bool:
    """A janela nunca acessa a rede do servidor (banco, admin, metadados da nuvem...)."""
    host = (host or "").strip("[]").lower().rstrip(".")
    if not host or "." not in host and ":" not in host or host.endswith((".localhost", ".local", ".internal")):
        return True
    try:
        return not ipaddress.ip_address(host).is_global
    except ValueError:
        return False


@database_sync_to_async
def _carregar(usuario, servico: str):
    credencial = Credencial.objects.filter(usuario=usuario, servico=servico).first()
    if credencial is None:
        return None, None
    estado = None
    sessao = SessaoServico.objects.filter(usuario=usuario, servico=servico).first()
    if sessao is not None and sessao.estado_cifrado:
        try:
            estado = sessao.estado
        except (InvalidToken, ValueError):  # chave trocada ou dado corrompido: começa do zero
            estado = None
    return credencial, estado


@database_sync_to_async
def _salvar_sessao(usuario, servico: str, estado: dict) -> None:
    sessao = SessaoServico(usuario=usuario, servico=servico)
    sessao.estado = estado
    SessaoServico.objects.update_or_create(usuario=usuario, servico=servico, defaults={
        "estado_cifrado": sessao.estado_cifrado, "expirada": False})


@database_sync_to_async
def _enfileirar(usuario, servico: str):
    try:
        fila = importlib.import_module("integracoes.fila")
    except ImportError:
        logger.warning("integracoes.fila indisponível: a sincronização depois do login não foi enfileirada")
        return None
    try:
        return fila.enfileirar(usuario, servico, origem="login")
    except Exception as erro:  # a sessão já está salva; a pessoa ainda pode sincronizar pela tela
        logger.warning("Não enfileirou a sincronização de %s depois do login: %s", servico, type(erro).__name__)
        return None


class Janela:
    """Um Chromium no servidor, com a página de login de um serviço, transmitido para quem estiver conectado."""

    def __init__(self, usuario, servico: str, credencial, estado_salvo: dict | None, tamanho: tuple[int, int]):
        self.usuario, self.servico, self.credencial = usuario, servico, credencial
        self.login = logins.obter(servico)()
        self.estado_salvo = estado_salvo if self.login.reaproveitar_sessao else None
        self.largura, self.altura = tamanho
        self.consumidor: NavegadorConsumer | None = None
        self.estado, self.mensagem_estado = "abrindo", f"Abrindo o site da {self.login.nome}…"
        self.inicio = self.ultima_acao = time.monotonic()
        self.sozinha_desde: float | None = None
        self.avisou_inatividade = False
        self.page = None
        self.tela: tuple[int, int] | None = None
        self.ultimo_quadro: bytes | None = None
        self._quadro: dict | None = None
        self._acks: list = []
        self._novo_quadro = asyncio.Event()
        self._parar = asyncio.Event()
        self.navegador = None
        self.tarefa: asyncio.Task | None = None

    # ---------- ciclo de vida ----------
    def iniciar(self) -> None:
        self.tarefa = asyncio.create_task(self._executar())

    async def fechar(self, mensagem: dict | None = None, codigo: int = FIM) -> None:
        """Fecha por fora (botão Fechar, outra janela). Fechar o navegador faz qualquer chamada pendente do
        Playwright falhar na hora; cancelar a tarefa no meio de uma chamada deixaria erros soltos no log."""
        if mensagem or self.consumidor is not None:
            await self._finalizar(mensagem or {"tipo": "fechado", "mensagem": "Janela fechada."}, codigo)
        self._parar.set()
        if self.navegador is not None:
            with suppress(Exception):
                await self.navegador.close()
        tarefa = self.tarefa
        if tarefa is not None and not tarefa.done() and tarefa is not asyncio.current_task():
            await asyncio.wait({tarefa}, timeout=15)
            if not tarefa.done():
                tarefa.cancel()
                await asyncio.wait({tarefa})
        if _janelas.get(self.usuario.pk) is self:
            del _janelas[self.usuario.pk]

    async def _executar(self) -> None:
        from playwright.async_api import async_playwright

        logger.info("Janela remota aberta: usuário %s, %s", self.usuario.pk, self.servico)
        try:
            async with async_playwright() as p:
                self.navegador = await p.chromium.launch(headless=True, args=ARGS_CHROMIUM)
                try:
                    if not self._parar.is_set():
                        await self._sessao(self.navegador)
                finally:
                    with suppress(Exception):
                        await self.navegador.close()
        except asyncio.CancelledError:
            raise
        except Exception as erro:
            if self._parar.is_set():  # fechada por fora no meio de uma chamada: não é falha
                return
            logger.warning("Janela remota de %s (usuário %s) falhou: %s", self.servico, self.usuario.pk,
                           type(erro).__name__)
            await self._finalizar(_erro("falha", "A janela parou de responder. Tente de novo em instantes."), FALHA)
        finally:
            self.page = self.navegador = None
            if _janelas.get(self.usuario.pk) is self:
                del _janelas[self.usuario.pk]
            logger.info("Janela remota fechada: usuário %s, %s (%s)", self.usuario.pk, self.servico, self.estado)

    async def _sessao(self, navegador) -> None:
        # Sem janela o Chromium se anuncia "HeadlessChrome" e alguns sites recusam; quem opera é uma pessoa.
        versao = await (await navegador.new_browser_cdp_session()).send("Browser.getVersion")
        opcoes: dict = {"viewport": {"width": self.largura, "height": self.altura}, "locale": "pt-BR",
                        "timezone_id": "America/Sao_Paulo", "accept_downloads": False, "service_workers": "block",
                        "user_agent": versao["userAgent"].replace("HeadlessChrome", "Chrome")}
        if self.estado_salvo:
            opcoes["storage_state"] = self.estado_salvo
        contexto = await navegador.new_context(**opcoes)
        await contexto.route("**/*", self._filtrar)
        page = self.page = await contexto.new_page()
        contexto.on("page", self._nova_aba)
        page.on("dialog", self._dialogo)
        page.on("framenavigated", self._navegou)
        cdp = await contexto.new_cdp_session(page)
        cdp.on("Page.screencastFrame", self._receber_quadro)
        await cdp.send("Page.startScreencast", {"format": "jpeg", "quality": QUALIDADE_JPEG, "maxWidth": self.largura,
                                                "maxHeight": self.altura, "everyNthFrame": 1})
        envio = asyncio.create_task(self._enviar_quadros(cdp))
        try:
            try:
                await self.login.preparar(page, self.credencial)
            except PlaywrightError:  # site lento: a pessoa ainda pode recarregar
                await self._enviar({"tipo": "aviso", "mensagem": f"O site da {self.login.nome} demorou para abrir. "
                                    "Se a tela não aparecer, use Recarregar."})
            await self._mudar_estado("ao_vivo", "Ao vivo")
            await self._ciclo(contexto, page)
        finally:
            self._parar.set()
            self._novo_quadro.set()
            await asyncio.wait({envio}, timeout=5)
            envio.cancel()

    async def _ciclo(self, contexto, page) -> None:
        while True:
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._parar.wait(), CICLO_S)
            if self._parar.is_set():
                return
            agora = time.monotonic()
            if agora - self.inicio > LIMITE_TOTAL_S:
                return await self._finalizar(_erro("tempo", f"Passaram {LIMITE_TOTAL_S // 60} minutos e o login não "
                                                   "terminou. Abra a janela de novo quando quiser."), TEMPO)
            parada = agora - self.ultima_acao
            if parada > LIMITE_INATIVO_S:
                return await self._finalizar(_erro("inativo", "A janela fechou por falta de atividade. "
                                                   "Abra de novo quando quiser."), TEMPO)
            if parada > LIMITE_INATIVO_S - AVISO_INATIVO_S and not self.avisou_inatividade:
                self.avisou_inatividade = True
                await self._enviar({"tipo": "aviso", "mensagem": "Sem atividade: a janela fecha em "
                                    f"{AVISO_INATIVO_S} segundos."})
            if self.consumidor is None and self.sozinha_desde and agora - self.sozinha_desde > GRACA_S:
                return
            if await self._logado(page) and await self._concluir(contexto, page):
                return
            with suppress(PlaywrightError):
                await self.login.a_cada_ciclo(page, self.credencial)

    async def _logado(self, page) -> bool:
        try:
            return await self.login.logado(page)
        except PlaywrightError:
            return False

    async def _concluir(self, contexto, page) -> bool:
        """Login feito: guarda os cookies (cifrados), põe a sincronização na fila e fecha. Daqui em diante
        nenhuma entrada da pessoa chega ao site (na Copel, nada de clicar em "1ª via")."""
        await self._mudar_estado("salvando", "Login feito! Salvando a sessão…")
        with suppress(PlaywrightError):
            await page.wait_for_load_state("networkidle", timeout=15_000)
        if not await self._logado(page):
            await self._mudar_estado("ao_vivo", "Ao vivo")
            return False
        await _salvar_sessao(self.usuario, self.servico, await contexto.storage_state())
        tarefa = await _enfileirar(self.usuario, self.servico)
        logger.info("Login remoto concluído: usuário %s, %s", self.usuario.pk, self.servico)
        mensagem = "Conectado! Buscando suas faturas…" if tarefa else "Conectado! A sessão foi salva."
        await self._finalizar({"tipo": "sucesso", "mensagem": mensagem, "tarefa": getattr(tarefa, "pk", None)}, FIM)
        return True

    # ---------- quem está olhando ----------
    async def anexar(self, consumidor: NavegadorConsumer) -> None:
        antigo, self.consumidor = self.consumidor, consumidor
        self.sozinha_desde = None
        self.ultima_acao, self.avisou_inatividade = time.monotonic(), False
        if antigo is not None and antigo is not consumidor:
            await antigo.encerrar(_erro("outra_aba", "Esta janela foi aberta em outra aba."), OUTRA_ABA)
        await self._enviar({"tipo": "estado", "estado": self.estado, "mensagem": self.mensagem_estado})
        if self.tela:
            await self._enviar({"tipo": "tela", "largura": self.tela[0], "altura": self.tela[1]})
        if self.ultimo_quadro:
            await consumidor.enviar_binario(self.ultimo_quadro)

    def desanexar(self, consumidor: NavegadorConsumer) -> None:
        if self.consumidor is consumidor:
            self.consumidor = None
            self.sozinha_desde = time.monotonic()

    async def _enviar(self, mensagem: dict) -> None:
        if self.consumidor is not None:
            await self.consumidor.enviar_json(mensagem)

    async def _mudar_estado(self, estado: str, mensagem: str) -> None:
        self.estado, self.mensagem_estado = estado, mensagem
        await self._enviar({"tipo": "estado", "estado": estado, "mensagem": mensagem})

    async def _finalizar(self, mensagem: dict, codigo: int) -> None:
        self.estado = mensagem["tipo"]
        consumidor, self.consumidor = self.consumidor, None
        if consumidor is not None:
            await consumidor.encerrar(mensagem, codigo)

    # ---------- tela ----------
    def _receber_quadro(self, params: dict) -> None:
        self._acks.append(params.get("sessionId"))
        self._quadro = params
        self._novo_quadro.set()

    async def _enviar_quadros(self, cdp) -> None:
        """Manda só o quadro mais novo, no máximo QUADROS_POR_S por segundo. A confirmação (ack) vai depois do
        envio, então o Chromium não produz quadros mais rápido do que a conexão leva."""
        intervalo = 1 / QUADROS_POR_S
        while True:
            await self._novo_quadro.wait()
            self._novo_quadro.clear()
            if self._parar.is_set():
                return
            comeco = time.monotonic()
            params, self._quadro = self._quadro, None
            if params:
                self.ultimo_quadro = base64.b64decode(params["data"])
                meta = params.get("metadata") or {}
                tela = (round(meta.get("deviceWidth") or self.largura), round(meta.get("deviceHeight") or self.altura))
                if tela != self.tela:
                    self.tela = tela
                    await self._enviar({"tipo": "tela", "largura": tela[0], "altura": tela[1]})
                if self.consumidor is not None:
                    await self.consumidor.enviar_binario(self.ultimo_quadro)
            await asyncio.sleep(max(0.0, intervalo - (time.monotonic() - comeco)))
            acks, self._acks = self._acks, []
            for sessao in acks:
                try:
                    await cdp.send("Page.screencastFrameAck", {"sessionId": sessao})
                except PlaywrightError:
                    return

    # ---------- entradas da pessoa ----------
    def _ponto(self, msg: dict) -> tuple[float, float] | None:
        largura, altura = self.tela or (self.largura, self.altura)
        x, y = _numero(msg.get("x")), _numero(msg.get("y"))
        if x is None or y is None:
            return None
        return min(max(x, 0), largura - 1), min(max(y, 0), altura - 1)

    async def entrada(self, msg: dict) -> None:
        page = self.page
        if page is None or self.estado not in ("abrindo", "ao_vivo"):
            return
        self.ultima_acao, self.avisou_inatividade = time.monotonic(), False
        tipo = msg.get("tipo")
        try:
            if tipo in ("clique", "pressionar", "soltar", "mover", "rolar"):
                ponto = self._ponto(msg)
                if ponto is None:
                    return
                await page.mouse.move(*ponto)
                if tipo == "clique":  # cliques=2 é o segundo clique de um duplo (o primeiro já veio)
                    cliques = int(_numero(msg.get("cliques"), 1, 3) or 1)
                    await page.mouse.down(click_count=cliques)
                    await page.mouse.up(click_count=cliques)
                elif tipo == "pressionar":
                    await page.mouse.down()
                elif tipo == "soltar":
                    await page.mouse.up()
                elif tipo == "rolar":
                    await page.mouse.wheel(_numero(msg.get("dx"), -3000, 3000) or 0,
                                           _numero(msg.get("dy"), -3000, 3000) or 0)
            elif tipo == "tecla":
                combinacao = _tecla(msg)
                if combinacao:
                    await page.keyboard.press(combinacao)
            elif tipo == "texto":
                texto = _texto(msg.get("texto"))
                if texto:
                    await page.keyboard.type(texto)
            elif tipo == "recarregar":
                await page.reload(wait_until="commit", timeout=30_000)
        except PlaywrightError:  # página no meio de uma navegação
            pass

    # ---------- eventos do navegador ----------
    def _permitido(self, request) -> tuple[bool, bool]:
        """(pode seguir, é uma navegação da página principal)"""
        url = urlparse(request.url)
        if url.scheme not in ("http", "https") or _endereco_interno(url.hostname):
            return False, False
        if request.is_navigation_request() and request.frame.parent_frame is None:
            return logins.host_permitido(url.hostname, self.login.dominios), True
        return True, False

    async def _filtrar(self, route, request) -> None:
        """A página principal só navega pelos domínios do serviço; nada acessa endereços internos."""
        try:
            permitido, principal = self._permitido(request)
        except Exception:
            permitido, principal = False, False
        try:
            if permitido:
                return await route.continue_()
            await route.abort("blockedbyclient")
        except PlaywrightError:
            return
        if principal:
            await self._enviar({"tipo": "aviso", "mensagem": f"Esta janela só abre o site da {self.login.nome}."})

    async def _nova_aba(self, aba) -> None:
        """O navegador remoto tem uma aba só: o que o site abriria em outra aba abre na mesma."""
        if aba is self.page:
            return
        url = ""
        with suppress(PlaywrightError):
            await aba.wait_for_load_state("domcontentloaded", timeout=10_000)
            url = aba.url
        with suppress(PlaywrightError):
            await aba.close()
        if self.page is not None and url.startswith("http") and logins.host_permitido(urlparse(url).hostname,
                                                                                      self.login.dominios):
            await self._enviar({"tipo": "aviso", "mensagem": "O site abriu outra aba; ela foi aberta aqui."})
            with suppress(PlaywrightError):
                await self.page.goto(url, wait_until="commit", timeout=30_000)

    async def _dialogo(self, dialogo) -> None:
        texto = " ".join((dialogo.message or "").split())[:300]
        if texto:
            await self._enviar({"tipo": "aviso", "mensagem": f"O site avisou: {texto}"})
        with suppress(PlaywrightError):
            if dialogo.type in ("alert", "beforeunload"):
                await dialogo.accept()
            else:
                await dialogo.dismiss()

    def _navegou(self, frame) -> None:
        if self.page is not None and frame is self.page.main_frame:
            self.login.navegou()


class NavegadorConsumer(AsyncWebsocketConsumer):
    """Liga o WebSocket da pessoa à janela dela (cria, reata ou recusa)."""
    janela: Janela | None = None

    async def connect(self):
        usuario = self.scope.get("user")
        servico = self.scope["url_route"]["kwargs"].get("servico")
        classe = logins.obter(servico)
        if not getattr(usuario, "is_authenticated", False) or classe is None:
            await self.close()
            return
        await self.accept()
        credencial, estado = await _carregar(usuario, servico)
        if credencial is None:
            return await self.encerrar(_erro("sem_credencial", f"Cadastre o CPF e a senha da {classe.nome} "
                                             "em Configurações."), SEM_CREDENCIAL)
        async with _trava():
            janela = _janelas.get(usuario.pk)
            if janela is not None and (janela.servico != servico or janela.estado in FINAIS):
                aviso = None
                if janela.estado not in FINAIS:
                    aviso = _erro("outra_janela", f"Você abriu a conexão da {classe.nome}; esta janela foi fechada.")
                await janela.fechar(aviso, OUTRA_ABA)
                janela = None
            if janela is None:
                if len(_janelas) >= _limite():
                    return await self.encerrar(_erro("lotado", "Muitas janelas abertas agora. Tente de novo em "
                                                     "alguns minutos."), LOTADO)
                janela = Janela(usuario, servico, credencial, estado, _tamanho(self.scope))
                _janelas[usuario.pk] = janela
                janela.iniciar()
            self.janela = janela
            await janela.anexar(self)

    async def disconnect(self, code):
        if self.janela is not None:
            self.janela.desanexar(self)
            self.janela = None

    async def receive(self, text_data=None, bytes_data=None):
        janela = self.janela
        if janela is None or janela.consumidor is not self or not text_data or len(text_data) > 4096:
            return
        try:
            msg = json.loads(text_data)
        except ValueError:
            return
        if not isinstance(msg, dict):
            return
        if msg.get("tipo") == "fechar":
            if janela.estado != "salvando":  # login já feito: a mensagem de sucesso chega em seguida
                await janela.fechar({"tipo": "fechado", "mensagem": "Janela fechada."}, FIM)
        else:
            await janela.entrada(msg)

    async def enviar_json(self, mensagem: dict) -> None:
        with suppress(Exception):
            await self.send(text_data=json.dumps(mensagem, ensure_ascii=False))

    async def enviar_binario(self, dados: bytes) -> None:
        with suppress(Exception):
            await self.send(bytes_data=dados)

    async def encerrar(self, mensagem: dict, codigo: int) -> None:
        self.janela = None
        await self.enviar_json(mensagem)
        with suppress(Exception):
            await self.close(code=codigo)
