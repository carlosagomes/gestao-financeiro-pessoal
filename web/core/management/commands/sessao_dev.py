"""Só em desenvolvimento (DEBUG): grava um storage_state do Playwright já logado como o usuário, para tirar
prints das páginas sem digitar senha.  python manage.py sessao_dev --email x@y --saida /tmp/estado.json --porta 8000"""
import json

from django.conf import settings
from django.contrib.auth import BACKEND_SESSION_KEY, HASH_SESSION_KEY, SESSION_KEY
from django.contrib.sessions.backends.db import SessionStore
from django.core.management.base import BaseCommand, CommandError

from contas.models import Usuario


class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument("--email", required=True)
        parser.add_argument("--saida", required=True)
        parser.add_argument("--porta", type=int, default=8000)

    def handle(self, *args, **o):
        if not settings.DEBUG:
            raise CommandError("Só com DJANGO_DEBUG=1")
        usuario = Usuario.objects.get(email=o["email"].lower())
        sessao = SessionStore()
        sessao[SESSION_KEY] = str(usuario.pk)
        sessao[BACKEND_SESSION_KEY] = "django.contrib.auth.backends.ModelBackend"
        sessao[HASH_SESSION_KEY] = usuario.get_session_auth_hash()
        sessao.create()
        estado = {"cookies": [{"name": settings.SESSION_COOKIE_NAME, "value": sessao.session_key, "domain": "localhost",
                               "path": "/", "httpOnly": True, "secure": False, "sameSite": "Lax", "expires": -1}],
                  "origins": []}
        with open(o["saida"], "w") as f:
            json.dump(estado, f)
        self.stdout.write(f"storage_state gravado; abra http://localhost:{o['porta']}/")
