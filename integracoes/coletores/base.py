"""Peças comuns dos coletores: o log da tarefa, o navegador sem janela e as sessões salvas (cifradas)."""
from __future__ import annotations

import re
from contextlib import contextmanager
from typing import Callable, Iterator, Optional

from django.utils import timezone

from integracoes.models import NOMES_SERVICO, Credencial, SessaoServico, Tarefa


class ErroColeta(RuntimeError):
    """Erro esperado, com mensagem para a pessoa (vai para a tela sem traceback)."""


class SessaoExpirada(ErroColeta):
    pass


class LoginRecusado(ErroColeta):
    pass


class Registro:
    """O `log` entregue ao coletor: cada linha vai para Tarefa.log (com a hora) e a última vira o progresso.

    Os segredos registrados com `ocultar()` (CPF, senha) são trocados por ••• em tudo que é gravado."""
    LIMITE = 20_000
    INTERVALO_GRAVACAO_S = 1.0

    def __init__(self, tarefa: Optional[Tarefa] = None, eco: Optional[Callable[[str], None]] = None):
        self.tarefa = tarefa
        self.eco = eco
        self.linhas: list[str] = []
        self.segredos: set[str] = set()
        self._gravado_em = 0.0

    def ocultar(self, *valores: str) -> None:
        for valor in valores:
            for v in {valor or "", re.sub(r"\D", "", valor or "")}:
                if len(v) >= 4:
                    self.segredos.add(v)

    def limpar(self, texto: str) -> str:
        texto = str(texto)
        for segredo in sorted(self.segredos, key=len, reverse=True):
            texto = texto.replace(segredo, "•••")
        return texto

    def __call__(self, mensagem: str) -> None:
        linha = f"[{timezone.localtime():%H:%M:%S}] {self.limpar(mensagem)}"
        self.linhas.append(linha)
        if self.eco:
            self.eco(linha)
        self.gravar()

    @property
    def ultima(self) -> str:
        return self.linhas[-1][11:] if self.linhas else ""

    def texto(self) -> str:
        texto = "\n".join(self.linhas)
        if len(texto) > self.LIMITE:  # guarda o fim, que é onde está o erro
            texto = "… (início cortado)\n" + texto[-self.LIMITE:]
        return texto

    def gravar(self, forcar: bool = False) -> None:
        import time
        if self.tarefa is None:
            return
        agora = time.monotonic()
        if not forcar and agora - self._gravado_em < self.INTERVALO_GRAVACAO_S:
            return
        self._gravado_em = agora
        Tarefa.objects.filter(pk=self.tarefa.pk).update(log=self.texto(), progresso=self.ultima[:200])


def nome(servico: str) -> str:
    return NOMES_SERVICO.get(servico, servico)


def credencial(usuario, servico: str) -> Credencial:
    obj = Credencial.objects.filter(usuario=usuario, servico=servico).first()
    if obj is None:
        raise ErroColeta(f"Cadastre o CPF e a senha do {nome(servico)} em Configurações.")
    return obj


def sessao_salva(usuario, servico: str) -> Optional[SessaoServico]:
    return SessaoServico.objects.filter(usuario=usuario, servico=servico).first()


def salvar_sessao(usuario, servico: str, contexto) -> None:
    """Guarda os cookies (storage_state) cifrados para a próxima sincronização."""
    obj = sessao_salva(usuario, servico) or SessaoServico(usuario=usuario, servico=servico)
    obj.estado = contexto.storage_state()
    obj.expirada = False
    obj.save()


def marcar_expirada(usuario, servico: str) -> None:
    SessaoServico.objects.filter(usuario=usuario, servico=servico).update(expirada=True)


@contextmanager
def navegador(estado: Optional[dict] = None) -> Iterator:
    """Contexto do Chromium sem janela (Playwright, API síncrona), com os cookies salvos se houver."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        opcoes: dict = {"locale": "pt-BR", "timezone_id": "America/Sao_Paulo", "accept_downloads": True,
                        "viewport": {"width": 1366, "height": 900}}
        if estado:
            opcoes["storage_state"] = estado
        contexto = browser.new_context(**opcoes)
        try:
            yield contexto
        finally:
            contexto.close()
            browser.close()
