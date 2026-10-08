"""Rotas WebSocket (Channels): a janela remota do navegador para os logins com captcha."""
from django.urls import re_path

from integracoes import consumers

websocket_urlpatterns = [
    re_path(r"^ws/navegador/(?P<servico>[a-z]+)/$", consumers.NavegadorConsumer.as_asgi()),
]
