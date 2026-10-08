from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

from contas.views import entrar_admin
from core.views import saude

admin.site.site_header = admin.site.site_title = "Gestão Financeira Pessoal · Administração"
admin.site.index_title = "Administração"
admin.site.login = entrar_admin  # o admin usa o login do site, que tem o bloqueio contra força bruta

urlpatterns = [
    path("admin/", admin.site.urls),
    path("saude/", saude, name="saude"),
    path("favicon.ico", RedirectView.as_view(url="/static/favicon.svg", permanent=True)),
    path("", include("contas.urls")),
    path("", include("financeiro.urls")),
    path("pj/", include("pj.urls")),
    path("", include("notas.urls")),
    path("", include("consumo.urls")),
    path("", include("integracoes.urls")),
    path("", include("integracoes.urls_janela")),
]
