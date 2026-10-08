from django.urls import path

from integracoes import views

app_name = "integracoes"
urlpatterns = [
    path("configuracoes/", views.configuracoes, name="configuracoes"),
    path("configuracoes/servico/<str:servico>/", views.servico, name="servico"),
    path("configuracoes/historico/", views.historico, name="historico"),
    path("integracoes/status/", views.status, name="status"),
    path("integracoes/atualizar/", views.atualizar, name="atualizar"),
    path("integracoes/tarefa/<int:pk>/", views.tarefa, name="tarefa"),
    path("integracoes/<str:servico>/sincronizar/", views.sincronizar, name="sincronizar"),
]
