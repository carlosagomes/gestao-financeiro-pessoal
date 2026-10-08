from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from contas.models import Usuario


@admin.register(Usuario)
class UsuarioAdmin(UserAdmin):
    ordering = ["email"]
    list_display = ["email", "nome", "pj_habilitado", "is_active", "is_staff", "date_joined", "last_login"]
    list_filter = ["pj_habilitado", "is_active", "is_staff"]
    list_editable = ["pj_habilitado"]
    search_fields = ["email", "nome"]
    fieldsets = [
        (None, {"fields": ["email", "nome", "password"]}),
        ("Módulos", {"fields": ["pj_habilitado"]}),
        ("Preferências", {"fields": ["atualizacao_automatica", "hora_atualizacao"]}),
        ("Acesso", {"fields": ["is_active", "is_staff", "is_superuser", "groups", "user_permissions"]}),
        ("Datas", {"fields": ["last_login", "date_joined"]}),
    ]
    add_fieldsets = [(None, {"classes": ["wide"], "fields": ["email", "nome", "password1", "password2"]})]
