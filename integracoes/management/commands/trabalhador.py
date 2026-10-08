"""Trabalhador: executa a fila de sincronizações (Tarefa) e a agenda diária de cada usuário.

  python manage.py trabalhador               # para sempre (serviço "trabalhador" do docker-compose)
  python manage.py trabalhador --uma-vez     # esvazia a fila uma vez e sai (testes, cron)
  python manage.py trabalhador --sem-agenda  # só a fila, sem enfileirar a atualização diária

Uma tarefa por vez. Os coletores (Playwright, API síncrona) rodam neste processo. Tarefas "rodando" há
mais de 2 h (processo morto no meio) viram erro. O batimento vai para <APP_DADOS>/trabalhador.json,
que a tela lê para saber se a fila está andando.
"""
from __future__ import annotations

import json
import os
import signal
import socket
import time
import traceback

from django.core.management.base import BaseCommand
from django.db import close_old_connections, connection
from django.utils import timezone

from integracoes import coletores, fila
from integracoes.coletores.base import ErroColeta, Registro
from integracoes.models import NOMES_SERVICO, Tarefa

INTERVALO_S = 5
AGENDA_S = 60
BATIMENTO_S = 10
LOG_VIVO_S = 30 * 60


def _parar(sinal, quadro):
    raise SystemExit(0)


def executar(tarefa: Tarefa, eco=None) -> Tarefa:
    """Roda o coletor da tarefa e grava o desfecho. Nunca deixa a tarefa "rodando"."""
    registro = Registro(tarefa, eco=eco)
    registro(f"{NOMES_SERVICO.get(tarefa.servico, tarefa.servico)}: {tarefa.tipo} ({tarefa.origem})")
    status, mensagem, resultado = Tarefa.ERRO, "", {}
    try:
        funcao = coletores.coletor(tarefa.servico, tarefa.tipo)
        resultado = funcao(tarefa.usuario, registro) or {}
        status = Tarefa.OK
        mensagem = _resumo(resultado)
    except ErroColeta as erro:
        mensagem = str(erro)
        registro(f"ERRO: {mensagem}")
    except BaseException as erro:  # inclui SystemExit/KeyboardInterrupt: registra e repassa depois
        parado = isinstance(erro, (SystemExit, KeyboardInterrupt))
        if parado:
            mensagem = "Interrompida: o trabalhador foi parado."
        else:  # só a 1ª linha na tela (o Playwright anexa o "call log"); o resto vai para o log
            mensagem = "Erro inesperado: " + ((str(erro).strip().splitlines() or [type(erro).__name__])[0])
        registro(f"ERRO: {mensagem}")
        for linha in traceback.format_exc().rstrip().splitlines()[-40:]:
            registro.linhas.append(registro.limpar(linha))
        if parado:
            _finalizar(tarefa, registro, status, mensagem, resultado)
            raise
    _finalizar(tarefa, registro, status, mensagem, resultado)
    return tarefa


def _resumo(resultado: dict) -> str:
    partes = []
    if "novas" in resultado:
        partes.append(f"{resultado['novas']} nova(s)")
    if resultado.get("atualizadas"):
        partes.append(f"{resultado['atualizadas']} atualizada(s)")
    if resultado.get("erros"):
        partes.append(f"{resultado['erros']} erro(s)")
    return ", ".join(partes)


def _finalizar(tarefa, registro, status, mensagem, resultado) -> None:
    tarefa.status = status
    tarefa.terminada_em = timezone.now()
    tarefa.mensagem = registro.limpar(mensagem)[:2000]
    tarefa.resultado = json.loads(json.dumps(resultado, default=str))
    tarefa.log = registro.texto()
    tarefa.progresso = registro.limpar(registro.ultima)[:200]
    tarefa.save(update_fields=["status", "terminada_em", "mensagem", "resultado", "log", "progresso"])


class Command(BaseCommand):
    help = "Executa a fila de sincronizações (Nota Paraná, Sanepar, Copel) e a atualização diária."

    def add_arguments(self, parser):
        parser.add_argument("--uma-vez", action="store_true", help="esvazia a fila uma vez e sai")
        parser.add_argument("--sem-agenda", action="store_true", help="não enfileira a atualização diária")
        parser.add_argument("--intervalo", type=float, default=INTERVALO_S, help="segundos entre consultas à fila")

    def handle(self, *args, **opcoes):
        # A API síncrona do Playwright mantém um event loop no processo; o ORM aqui é síncrono de verdade.
        os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
        try:
            anterior = signal.signal(signal.SIGTERM, _parar)
        except ValueError:  # fora da thread principal
            anterior = None
        self.uma_vez, self.agenda = opcoes["uma_vez"], not opcoes["sem_agenda"]
        self.tarefa_atual = None
        presas = fila.recuperar_interrompidas()
        self.log(f"Trabalhador no ar ({socket.gethostname()}, pid {os.getpid()})"
                 + (f"; {presas} tarefa(s) presa(s) marcada(s) como interrompida(s)" if presas else ""))
        try:
            self.laco(opcoes["intervalo"])
        finally:
            if anterior is not None:
                signal.signal(signal.SIGTERM, anterior)
            self.tarefa_atual = None
            self.bater()

    def laco(self, intervalo: float) -> None:
        ultima_agenda = ultimo_batimento = ultimo_log = 0.0
        while True:
            try:
                if not connection.in_atomic_block:  # nos testes tudo roda dentro de uma transação
                    close_old_connections()
                agora = time.monotonic()
                if agora - ultimo_batimento >= BATIMENTO_S:
                    self.bater()
                    ultimo_batimento = agora
                if self.agenda and agora - ultima_agenda >= AGENDA_S:
                    ultima_agenda = agora
                    fila.recuperar_interrompidas()
                    for tarefa in fila.agendar():
                        self.log(f"Agendada: {tarefa.get_servico_display()} (usuário {tarefa.usuario_id})")
                if agora - ultimo_log >= LOG_VIVO_S:
                    ultimo_log = agora
                    self.log(f"No ar; {Tarefa.objects.filter(status=Tarefa.PENDENTE).count()} tarefa(s) na fila")

                tarefa = fila.reivindicar()
                if tarefa:
                    self.tarefa_atual = tarefa
                    self.bater()
                    self.log(f"Tarefa {tarefa.pk}: {tarefa.get_servico_display()} {tarefa.tipo} "
                             f"(usuário {tarefa.usuario_id}, {tarefa.origem})")
                    executar(tarefa)
                    self.log(f"Tarefa {tarefa.pk}: {tarefa.status}"
                             + (f" ({tarefa.mensagem})" if tarefa.mensagem else ""))
                    self.tarefa_atual = None
                    continue
            except Exception as erro:  # banco fora do ar etc.: espera e tenta de novo
                if self.uma_vez:
                    raise
                self.log(f"Falha no laço: {erro}")
                time.sleep(30)
                continue
            if self.uma_vez:
                break
            time.sleep(intervalo)

    def bater(self) -> None:
        try:
            destino = fila.arquivo_batimento()
            destino.parent.mkdir(parents=True, exist_ok=True)
            destino.write_text(json.dumps({
                "visto_em": timezone.now().isoformat(timespec="seconds"), "host": socket.gethostname(),
                "pid": os.getpid(), "tarefa": self.tarefa_atual.pk if self.tarefa_atual else None}))
        except OSError as erro:
            self.log(f"Não consegui gravar o batimento: {erro}")

    def log(self, mensagem: str) -> None:
        self.stdout.write(f"[{timezone.localtime():%d/%m %H:%M:%S}] {mensagem}")
        self.stdout.flush()
