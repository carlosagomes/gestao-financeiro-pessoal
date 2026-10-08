"""Usuários: o login é o e-mail. Cada usuário só enxerga os próprios dados (todas as tabelas têm `usuario`)."""
from datetime import time

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.utils import timezone


class UsuarioManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, email, password=None, **extra):
        if not email:
            raise ValueError("Informe o e-mail")
        usuario = self.model(email=self.normalize_email(email).lower(), **extra)
        usuario.set_password(password)
        usuario.save(using=self._db)
        return usuario

    def create_superuser(self, email, password=None, **extra):
        extra.update(is_staff=True, is_superuser=True)
        return self.create_user(email, password, **extra)


class Usuario(AbstractBaseUser, PermissionsMixin):
    email = models.EmailField("e-mail", unique=True)
    nome = models.CharField(max_length=120)
    is_active = models.BooleanField("ativo", default=True)
    is_staff = models.BooleanField("acessa o admin", default=False)
    date_joined = models.DateTimeField("cadastrado em", default=timezone.now)

    # Módulos opcionais: o menu e as páginas só aparecem para quem tem o módulo ligado (pelo admin).
    pj_habilitado = models.BooleanField(
        "módulo PJ", default=False,
        help_text="Horas, informes e previsão de recebimento PJ. Só aparece para quem tiver isto marcado.")
    # Preferências
    hora_atualizacao = models.TimeField("atualização automática diária", default=time(7, 0))
    atualizacao_automatica = models.BooleanField("atualizar sozinho todo dia", default=True)

    objects = UsuarioManager()
    USERNAME_FIELD = "email"
    EMAIL_FIELD = "email"
    REQUIRED_FIELDS = ["nome"]

    class Meta:
        verbose_name = "usuário"
        verbose_name_plural = "usuários"

    def __str__(self):
        return f"{self.nome} <{self.email}>"

    @property
    def primeiro_nome(self) -> str:
        return (self.nome or self.email).split()[0]

    @property
    def iniciais(self) -> str:
        partes = (self.nome or self.email).split()
        return "".join(p[0] for p in partes[:2]).upper()
