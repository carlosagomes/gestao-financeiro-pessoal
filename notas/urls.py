from django.urls import path

from notas import views

app_name = "notas"
urlpatterns = [
    path("notas/", views.notas, name="notas"),
    path("notas/detalhe/", views.detalhe, name="detalhe"),
    path("notas/detalhe/compras/", views.detalhe_compras, name="detalhe_compras"),
    path("notas/nota/<int:pk>/", views.nota, name="nota"),
    path("notas/api/categoria/", views.api_categoria, name="api_categoria"),
    path("notas/api/regras/", views.api_regras, name="api_regras"),
    path("produtos/", views.produtos, name="produtos"),
    path("produtos/dados/", views.produtos_dados, name="produtos_dados"),
    path("produtos/historico/", views.produto, name="produto"),
]
