"""Notas do Nota Paraná: cada nota com itens e formas de pagamento, as regras de categoria e o placar.

As notas DETALHAM o consumo e nunca somam nas saídas: essas compras já estão na fatura do cartão/débito.
"""
from django.conf import settings
from django.db import models


def caminho_html(instancia, nome):
    return f"usuarios/{instancia.usuario_id}/notas/{nome}"


class Nota(models.Model):
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notas")
    chave = models.CharField(max_length=44)  # chave de acesso
    id_doc_fiscal = models.CharField(max_length=40, blank=True)  # id da nota no Nota Paraná
    modelo = models.CharField(max_length=4, blank=True)  # 65 = NFC-e, 55 = NF-e
    numero = models.CharField(max_length=20, blank=True)
    serie = models.CharField(max_length=10, blank=True)
    data_emissao = models.DateTimeField()
    protocolo = models.CharField(max_length=60, blank=True)
    situacao = models.CharField(max_length=40, blank=True)
    emitente_cnpj = models.CharField(max_length=20, blank=True)
    emitente_nome = models.CharField(max_length=200, blank=True)
    emitente_fantasia = models.CharField(max_length=200, blank=True)
    emitente_ie = models.CharField(max_length=30, blank=True)
    emitente_endereco = models.TextField(blank=True)
    emitente_municipio = models.CharField(max_length=80, blank=True)
    emitente_uf = models.CharField(max_length=2, blank=True)
    qtd_itens = models.IntegerField(null=True, blank=True)
    valor_produtos = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    valor_desconto = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    valor_total = models.DecimalField(max_digits=12, decimal_places=2)
    valor_tributos = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    credito = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    situacao_credito = models.CharField(max_length=80, blank=True)
    categoria = models.CharField(max_length=60, blank=True)  # escolhida à mão; vazio = usa as regras
    detalhes = models.JSONField(default=dict, blank=True)  # todos os demais campos da página da nota
    url = models.TextField(blank=True)
    html = models.FileField(upload_to=caminho_html, blank=True)  # cópia da página, para reprocessar sem o site
    importado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["data_emissao"]
        indexes = [models.Index(fields=["usuario", "data_emissao"])]
        constraints = [models.UniqueConstraint(fields=["usuario", "chave"], name="nota_unica_por_usuario")]

    def __str__(self):
        return f"{self.data_emissao:%d/%m/%Y} {self.loja} {self.valor_total}"

    @property
    def loja(self) -> str:
        return (self.emitente_fantasia or self.emitente_nome or "")[:32]


class ItemNota(models.Model):
    nota = models.ForeignKey(Nota, on_delete=models.CASCADE, related_name="itens")
    seq = models.IntegerField()
    codigo = models.CharField(max_length=60, blank=True)
    ean = models.CharField(max_length=20, blank=True)  # código de barras: o mesmo produto em lojas diferentes
    descricao = models.CharField(max_length=200, blank=True)
    ncm = models.CharField(max_length=12, blank=True)
    cfop = models.CharField(max_length=6, blank=True)
    quantidade = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    unidade = models.CharField(max_length=10, blank=True)
    valor_unitario = models.DecimalField(max_digits=16, decimal_places=6, null=True, blank=True)
    valor_desconto = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    valor_total = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)  # bruto
    detalhes = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["nota", "seq"]
        constraints = [models.UniqueConstraint(fields=["nota", "seq"], name="item_unico_por_nota")]


class PagamentoNota(models.Model):
    nota = models.ForeignKey(Nota, on_delete=models.CASCADE, related_name="pagamentos")
    seq = models.IntegerField()
    forma = models.CharField(max_length=80, blank=True)
    bandeira = models.CharField(max_length=40, blank=True)
    valor = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    detalhes = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["nota", "seq"]
        constraints = [models.UniqueConstraint(fields=["nota", "seq"], name="pagamento_unico_por_nota")]


class PeriodoNP(models.Model):
    """Resumo mensal mostrado pelo Nota Paraná (inclui bilhetes, que não vêm nas notas)."""
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="periodos_np")
    periodo = models.CharField(max_length=7)  # AAAA-MM
    total_notas = models.IntegerField(null=True, blank=True)
    bilhetes = models.IntegerField(null=True, blank=True)
    valor_total = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    creditos = models.CharField(max_length=60, blank=True)  # valor ou "A CALCULAR EM ..."
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["periodo"]
        constraints = [models.UniqueConstraint(fields=["usuario", "periodo"], name="periodo_unico_por_usuario")]


class Placar(models.Model):
    """"Meu placar" do Nota Paraná (saldo de créditos, prêmios...) da última sincronização."""
    usuario = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="placar_np")
    dados = models.JSONField(default=dict)
    atualizado_em = models.DateTimeField(auto_now=True)


class RegraCategoria(models.Model):
    """Trecho do nome do estabelecimento -> categoria da nota (o trecho mais longo vence)."""
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="regras_categoria")
    padrao = models.CharField(max_length=80)
    categoria = models.CharField(max_length=60)

    class Meta:
        ordering = ["padrao"]
        constraints = [models.UniqueConstraint(fields=["usuario", "padrao"], name="regra_unica_por_usuario")]
