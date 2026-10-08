"""Página da janela remota: a tela do site (Sanepar, Copel) aberto no servidor, para a pessoa fazer o login."""
from django.http import Http404
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_GET

from integracoes import logins
from integracoes.models import Credencial


@require_GET
def conectar(request, servico):
    login = logins.obter(servico)
    if login is None:
        raise Http404
    credencial = Credencial.objects.filter(usuario=request.user, servico=servico).first()
    return render(request, "integracoes/janela.html", {
        "servico": servico,
        "site": login,
        "credencial": credencial,
        "ws_caminho": f"/ws/navegador/{servico}/",
        "url_dados": reverse(login.pagina_dados),
        "url_config": reverse("integracoes:configuracoes"),
    })
