from django.urls import path

from pj import views

app_name = "pj"
urlpatterns = [
    path("", views.horas, name="horas"),
]
