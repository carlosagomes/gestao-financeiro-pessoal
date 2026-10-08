"""Janela remota de login (captcha/2FA resolvidos pela pessoa): páginas HTTP. O WebSocket fica em routing.py."""
from django.urls import path

from integracoes import janela_views

app_name = "janela"
urlpatterns = [
    path("integracoes/<str:servico>/conectar/", janela_views.conectar, name="conectar"),
]
