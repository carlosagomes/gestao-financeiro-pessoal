from django.urls import path

from financeiro import views

app_name = "financeiro"
urlpatterns = [
    path("", views.resumo, name="resumo"),
    path("resumo/mes/", views.resumo_mes, name="resumo_mes"),
    path("resumo/categoria/", views.resumo_categoria, name="resumo_categoria"),
    path("lancamentos/", views.lancamentos, name="lancamentos"),
    path("lancamentos/historico/", views.historico, name="historico"),
    path("lancamentos/cadastros/", views.cadastros, name="cadastros"),
    # API JSON da tabela (Tabulator)
    path("lancamentos/api/", views.api_lancamentos, name="api"),
    path("lancamentos/api/<int:pk>/", views.api_lancamento, name="api_item"),
    path("lancamentos/api/excluir/", views.api_excluir, name="api_excluir"),
    path("lancamentos/api/marcar-pagas/", views.api_marcar_pagas, name="api_marcar_pagas"),
    path("lancamentos/api/copiar-mes/", views.api_copiar_mes, name="api_copiar_mes"),
]
