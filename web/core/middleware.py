"""Tudo exige login, menos as páginas públicas (entrar, cadastro, saúde, estáticos), e toda resposta leva os
cabeçalhos de segurança do navegador."""
from django.conf import settings
from django.core.exceptions import DisallowedHost
from django.http import HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.http import urlencode

# Caminhos exatos (com a barra final das URLs): "/entrar" sozinho como prefixo liberaria "/entrar-qualquer-coisa".
PUBLICAS = ("/entrar/", "/cadastro/", "/saude/", "/favicon.ico")
PREFIXOS_PUBLICOS = ("/static/", "/admin/")  # o admin tem o próprio controle (só equipe) e entra pelo /entrar/


class ExigirLoginMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        caminho = request.path
        if (not request.user.is_authenticated and caminho not in PUBLICAS
                and not caminho.startswith(PREFIXOS_PUBLICOS)):
            if request.headers.get("HX-Request"):  # HTMX: recarrega a página inteira no login
                resposta = HttpResponse(status=204)
                resposta["HX-Redirect"] = reverse(settings.LOGIN_URL)
                return resposta
            return redirect(f"{reverse(settings.LOGIN_URL)}?{urlencode({'next': caminho})}")
        return self.get_response(request)


# Scripts e estilos só deste site (e as fontes do Google). 'unsafe-inline'/'unsafe-eval' ficam porque os templates
# usam onclick e o Alpine avalia expressões; o ganho está no resto: nenhum script de fora, nenhum envio de dados
# para outro endereço (connect/img/form), nada de <base>, <object> nem a página dentro de um frame.
CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self' 'unsafe-inline' 'unsafe-eval'",
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
    "font-src 'self' https://fonts.gstatic.com",
    "img-src 'self' data: blob:",
    "connect-src 'self' {websocket}",
    "frame-src 'none'",
    "frame-ancestors 'none'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
])
PERMISSOES = "accelerometer=(), camera=(), geolocation=(), gyroscope=(), magnetometer=(), microphone=(), payment=(), usb=()"


class CabecalhosSegurancaMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        resposta = self.get_response(request)
        # A janela remota usa WebSocket no mesmo endereço; nem todo navegador aceita ws:// como 'self'.
        try:
            host = request.get_host()  # já validado contra ALLOWED_HOSTS
            websocket = f"wss://{host} ws://{host}"
        except DisallowedHost:
            websocket = ""
        resposta.setdefault("Content-Security-Policy", CSP.format(websocket=websocket))
        resposta.setdefault("Permissions-Policy", PERMISSOES)
        return resposta
