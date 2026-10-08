"""Lançamentos manuais (a planilha mensal): entradas e saídas, banco, categoria e se já foi pago.

Regra da planilha original, mantida em todos os relatórios: Saída conta só o que está pago; o que falta pagar
aparece à parte ("A pagar"). Saldo = Entrada − Saída paga.
"""
from django.conf import settings
from django.db import models

ENTRADA = "Entrada"
SAIDA = "Saída"
MOVIMENTACOES = [(ENTRADA, ENTRADA), (SAIDA, SAIDA)]


class Categoria(models.Model):
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="categorias")
    nome = models.CharField(max_length=60)
    movimentacao = models.CharField(max_length=10, choices=MOVIMENTACOES, default=SAIDA)

    class Meta:
        ordering = ["movimentacao", "nome"]
        constraints = [models.UniqueConstraint(fields=["usuario", "nome"], name="categoria_unica_por_usuario")]

    def __str__(self):
        return self.nome


class Banco(models.Model):
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="bancos")
    nome = models.CharField(max_length=60)

    class Meta:
        ordering = ["nome"]
        constraints = [models.UniqueConstraint(fields=["usuario", "nome"], name="banco_unico_por_usuario")]

    def __str__(self):
        return self.nome


class Lancamento(models.Model):
    # Categoria e banco ficam como texto (como na planilha): renomear/apagar uma categoria não some com o
    # histórico, e as listas acima só alimentam as opções da tela.
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="lancamentos")
    data = models.DateField()
    movimentacao = models.CharField(max_length=10, choices=MOVIMENTACOES)
    banco = models.CharField(max_length=60, blank=True)
    categoria = models.CharField(max_length=60)
    descricao = models.CharField(max_length=255, blank=True)
    valor = models.DecimalField(max_digits=12, decimal_places=2)
    pago = models.BooleanField(default=False)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["data", "id"]
        indexes = [models.Index(fields=["usuario", "data"])]
        constraints = [models.CheckConstraint(condition=models.Q(valor__gte=0), name="lancamento_valor_positivo")]

    def __str__(self):
        return f"{self.data:%d/%m/%Y} {self.movimentacao} {self.categoria} {self.valor}"


class HistoricoLancamento(models.Model):
    """Toda alteração ou exclusão de lançamento, com a origem (tabela, botão, Sanepar, Copel...).
    O "Pago" é decisão do usuário: nada automático pode desfazer o que ele marcou sem deixar rastro aqui."""
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    lancamento_id = models.BigIntegerField(db_index=True)
    quando = models.DateTimeField(auto_now_add=True)
    campo = models.CharField(max_length=30)  # coluna alterada, "criado" ou "excluído"
    antes = models.TextField(null=True, blank=True)
    depois = models.TextField(null=True, blank=True)
    origem = models.CharField(max_length=60, blank=True)

    class Meta:
        ordering = ["-quando", "-id"]
