"""Admin das integrações: só leitura. Nunca mostra os valores cifrados, a senha nem o CPF inteiro."""
from django.contrib import admin

from integracoes.models import Credencial, SessaoServico, Tarefa


class SomenteLeitura(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(Credencial)
class CredencialAdmin(SomenteLeitura):
    list_display = ("usuario", "servico", "login_mascarado", "ativo", "atualizado_em")
    list_filter = ("servico", "ativo")
    search_fields = ("usuario__email",)
    fields = ("usuario", "servico", "login_mascarado", "ativo", "atualizado_em")
    readonly_fields = fields

    @admin.display(description="login")
    def login_mascarado(self, obj):
        return obj.login_mascarado


@admin.register(SessaoServico)
class SessaoServicoAdmin(SomenteLeitura):
    list_display = ("usuario", "servico", "expirada", "criada_em", "atualizado_em")
    list_filter = ("servico", "expirada")
    search_fields = ("usuario__email",)
    fields = ("usuario", "servico", "expirada", "criada_em", "atualizado_em")
    readonly_fields = fields


@admin.register(Tarefa)
class TarefaAdmin(SomenteLeitura):
    list_display = ("id", "usuario", "servico", "tipo", "origem", "status", "criada_em", "terminada_em", "mensagem_curta")
    list_filter = ("status", "servico", "origem", "tipo")
    search_fields = ("usuario__email", "mensagem")
    date_hierarchy = "criada_em"
    fields = ("usuario", "servico", "tipo", "origem", "status", "criada_em", "iniciada_em", "terminada_em",
              "progresso", "mensagem", "resultado", "log")
    readonly_fields = fields
    list_select_related = ("usuario",)

    @admin.display(description="mensagem")
    def mensagem_curta(self, obj):
        return (obj.mensagem or "")[:80]
