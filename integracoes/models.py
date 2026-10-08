"""Integrações com Nota Paraná, Sanepar e Copel: credenciais (cifradas), sessões salvas e a fila de tarefas.

Senhas e sessões NUNCA ficam em texto puro no banco: tudo passa por integracoes.cripto (Fernet, chave
APP_ENCRYPTION_KEY). Captcha e 2FA nunca são burlados: quando o site pede, o usuário resolve numa janela
remota (o navegador do servidor transmitido para a tela dele).
"""
from django.conf import settings
from django.db import models

from integracoes import cripto

NOTAPARANA, SANEPAR, COPEL = "notaparana", "sanepar", "copel"
SERVICOS = [(NOTAPARANA, "Nota Paraná"), (SANEPAR, "Sanepar"), (COPEL, "Copel")]
NOMES_SERVICO = dict(SERVICOS)


class Credencial(models.Model):
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="credenciais")
    servico = models.CharField(max_length=20, choices=SERVICOS)
    login_cifrado = models.TextField()
    senha_cifrada = models.TextField()
    ativo = models.BooleanField(default=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["usuario", "servico"], name="credencial_unica")]

    def __str__(self):
        return f"{self.get_servico_display()} de {self.usuario}"

    @property
    def login(self) -> str:
        return cripto.decifrar(self.login_cifrado)

    @login.setter
    def login(self, valor: str) -> None:
        self.login_cifrado = cripto.cifrar(valor)

    @property
    def senha(self) -> str:
        return cripto.decifrar(self.senha_cifrada)

    @senha.setter
    def senha(self, valor: str) -> None:
        self.senha_cifrada = cripto.cifrar(valor)

    @property
    def login_mascarado(self) -> str:
        login = self.login
        return f"{login[:3]}•••••{login[-2:]}" if len(login) > 5 else "•••••"


class SessaoServico(models.Model):
    """Cookies (storage_state do Playwright) de um site depois do login, para as próximas sincronizações."""
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="sessoes")
    servico = models.CharField(max_length=20, choices=SERVICOS)
    estado_cifrado = models.TextField()
    criada_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)
    expirada = models.BooleanField(default=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["usuario", "servico"], name="sessao_unica")]

    @property
    def estado(self) -> dict:
        import json
        return json.loads(cripto.decifrar(self.estado_cifrado))

    @estado.setter
    def estado(self, valor: dict) -> None:
        import json
        self.estado_cifrado = cripto.cifrar(json.dumps(valor))


class Tarefa(models.Model):
    """Fila de trabalho do `manage.py trabalhador` (sincronizações manuais e agendadas)."""
    PENDENTE, RODANDO, OK, ERRO, CANCELADA = "pendente", "rodando", "ok", "erro", "cancelada"
    STATUS = [(PENDENTE, "Na fila"), (RODANDO, "Rodando"), (OK, "Concluída"), (ERRO, "Erro"),
              (CANCELADA, "Cancelada")]

    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="tarefas")
    servico = models.CharField(max_length=20, choices=SERVICOS)
    tipo = models.CharField(max_length=30, default="sincronizar")  # sincronizar, reprocessar
    origem = models.CharField(max_length=20, default="manual")  # manual, agenda, login
    status = models.CharField(max_length=12, choices=STATUS, default=PENDENTE)
    criada_em = models.DateTimeField(auto_now_add=True)
    iniciada_em = models.DateTimeField(null=True, blank=True)
    terminada_em = models.DateTimeField(null=True, blank=True)
    progresso = models.CharField(max_length=200, blank=True)  # última linha de log, para a tela
    mensagem = models.TextField(blank=True)
    resultado = models.JSONField(default=dict, blank=True)  # contagens, placar...
    log = models.TextField(blank=True)

    class Meta:
        ordering = ["-criada_em"]
        indexes = [models.Index(fields=["status", "criada_em"]), models.Index(fields=["usuario", "servico"])]

    def __str__(self):
        return f"{self.get_servico_display()} {self.tipo} ({self.status})"
