"""Fila de sincronizações (modelo Tarefa) e o estado de cada serviço para as telas.

Quem enfileira: o botão "Sincronizar agora", o "Atualizar agora" do menu, a agenda diária do trabalhador
e a janela remota logo depois de um login (`enfileirar(usuario, servico, origem="login")`).
Nunca há duas tarefas na fila/rodando para o mesmo usuário e serviço.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from integracoes.models import COPEL, NOTAPARANA, SANEPAR, Credencial, SessaoServico, Tarefa

ATIVAS = (Tarefa.PENDENTE, Tarefa.RODANDO)
SINCRONIZAR, REPROCESSAR = "sincronizar", "reprocessar"
# A sessão da Copel morre em poucos minutos: só vale logo depois do login na janela remota.
VALIDADE_SESSAO_COPEL = timedelta(minutes=20)


def enfileirar(usuario, servico: str, origem: str = "manual", tipo: str = SINCRONIZAR) -> Tarefa:
    """Cria a tarefa (pendente) ou devolve a que já está na fila/rodando para o usuário e serviço.
    `tarefa.nova` diz se foi criada agora."""
    with transaction.atomic():
        # Trava a linha do usuário: duas chamadas ao mesmo tempo não criam duas tarefas.
        get_user_model().objects.select_for_update().filter(pk=usuario.pk).first()
        existente = Tarefa.objects.filter(usuario=usuario, servico=servico, status__in=ATIVAS).first()
        if existente:
            existente.nova = False
            return existente
        tarefa = Tarefa.objects.create(usuario=usuario, servico=servico, origem=origem, tipo=tipo)
    tarefa.nova = True
    return tarefa


def tarefa_ativa(usuario, servico: str) -> Tarefa | None:
    return Tarefa.objects.filter(usuario=usuario, servico=servico, status__in=ATIVAS).first()


def ultima_concluida(usuario, servico: str) -> Tarefa | None:
    return (Tarefa.objects.filter(usuario=usuario, servico=servico, status__in=(Tarefa.OK, Tarefa.ERRO))
            .order_by("-criada_em").first())


def sessao_valida(sessao: SessaoServico | None, servico: str, agora: datetime | None = None) -> bool:
    if sessao is None or sessao.expirada or not sessao.estado_cifrado:
        return False
    if servico == COPEL:
        return (agora or timezone.now()) - sessao.atualizado_em < VALIDADE_SESSAO_COPEL
    return True


def pode_agendar(usuario, servico: str, credencial: Credencial | None, sessao: SessaoServico | None) -> bool:
    """A agenda diária roda o Nota Paraná (CPF e senha) e a Sanepar (sessão salva). A Copel precisa do
    reCAPTCHA a cada vez, então nunca entra na agenda."""
    if servico == NOTAPARANA:
        return credencial is not None and credencial.ativo
    if servico == SANEPAR:
        return credencial is not None and sessao_valida(sessao, SANEPAR)
    return False


def inicio_do_dia(agora: datetime) -> datetime:
    local = timezone.localtime(agora)
    return local.replace(hour=0, minute=0, second=0, microsecond=0)


TENTATIVAS_AGENDA_DIA = 3
INTERVALO_TENTATIVAS = timedelta(hours=1)


def agendar(agora: datetime | None = None) -> list[Tarefa]:
    """Enfileira a atualização diária de quem chegou no horário escolhido (hora_atualizacao, horário de
    Brasília) e ainda não tem sincronização OK hoje. Depois de um erro tenta de novo no máximo
    TENTATIVAS_AGENDA_DIA vezes no dia, com uma hora de intervalo (insistir pode bloquear a conta no site)."""
    agora = agora or timezone.now()
    local = timezone.localtime(agora)
    hoje = inicio_do_dia(agora)
    criadas = []
    usuarios = get_user_model().objects.filter(is_active=True, atualizacao_automatica=True)
    for usuario in usuarios:
        if local.time() < usuario.hora_atualizacao:
            continue
        credenciais = {c.servico: c for c in Credencial.objects.filter(usuario=usuario)}
        sessoes = {s.servico: s for s in SessaoServico.objects.filter(usuario=usuario)}
        for servico in (NOTAPARANA, SANEPAR):
            if not pode_agendar(usuario, servico, credenciais.get(servico), sessoes.get(servico)):
                continue
            do_dia = Tarefa.objects.filter(usuario=usuario, servico=servico, criada_em__gte=hoje)
            if do_dia.filter(status__in=(Tarefa.OK, *ATIVAS)).exists():
                continue
            agendadas = do_dia.filter(origem="agenda")
            if agendadas.count() >= TENTATIVAS_AGENDA_DIA:
                continue
            if agendadas.filter(criada_em__gt=agora - INTERVALO_TENTATIVAS).exists():
                continue
            tarefa = enfileirar(usuario, servico, origem="agenda")
            if tarefa.nova:
                criadas.append(tarefa)
    return criadas


def reivindicar() -> Tarefa | None:
    """Pega a próxima tarefa pendente (a mais antiga) e marca como rodando. Vários trabalhadores podem
    rodar juntos: a linha travada por um é pulada pelos outros."""
    with transaction.atomic():
        tarefa = (Tarefa.objects.select_for_update(skip_locked=True).filter(status=Tarefa.PENDENTE)
                  .order_by("criada_em", "id").first())
        if tarefa is None:
            return None
        tarefa.status = Tarefa.RODANDO
        tarefa.iniciada_em = timezone.now()
        tarefa.progresso = "Começando…"
        tarefa.save(update_fields=["status", "iniciada_em", "progresso"])
    return tarefa


def recuperar_interrompidas(limite: timedelta = timedelta(hours=2)) -> int:
    """Tarefas "rodando" há mais que o limite (trabalhador morto no meio) viram erro e liberam a fila."""
    corte = timezone.now() - limite
    presas = Q(iniciada_em__lt=corte) | Q(iniciada_em__isnull=True, criada_em__lt=corte)
    return Tarefa.objects.filter(presas, status=Tarefa.RODANDO).update(
        status=Tarefa.ERRO, terminada_em=timezone.now(), mensagem="Interrompida: o trabalhador parou no meio.")


def arquivo_batimento() -> Path:
    """O trabalhador grava aqui o sinal de vida (pasta de dados, compartilhada com o site no Docker)."""
    return Path(settings.MEDIA_ROOT).parent / "trabalhador.json"


def ler_batimento() -> dict:
    try:
        return json.loads(arquivo_batimento().read_text())
    except (OSError, ValueError):
        return {}
