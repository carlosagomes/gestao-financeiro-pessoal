from django.urls import path

from consumo import views

app_name = "consumo"
urlpatterns = [
    path("agua/", views.agua, name="agua"),
    path("agua/ajustar/", views.agua_ajustar, name="agua_ajustar"),
    path("agua/enviar/", views.agua_enviar, name="agua_enviar"),
    path("agua/<int:pk>/pdf/", views.agua_pdf, name="agua_pdf"),
    path("luz/", views.luz, name="luz"),
    path("luz/ajustar/", views.luz_ajustar, name="luz_ajustar"),
    path("luz/enviar/", views.luz_enviar, name="luz_enviar"),
    path("luz/<int:pk>/pdf/", views.luz_pdf, name="luz_pdf"),
]
