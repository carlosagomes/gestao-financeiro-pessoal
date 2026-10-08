from django.urls import path

from contas import views

app_name = "contas"
urlpatterns = [
    path("entrar/", views.entrar, name="entrar"),
    path("cadastro/", views.cadastro, name="cadastro"),
    path("sair/", views.sair, name="sair"),
    path("perfil/", views.perfil, name="perfil"),
    path("perfil/excluir/", views.excluir_conta, name="excluir_conta"),
]
