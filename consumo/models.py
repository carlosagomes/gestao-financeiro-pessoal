"""Contas da casa: água (Sanepar) e luz (Copel), lidas dos PDFs das faturas.

Vínculo com os lançamentos: a fatura da Sanepar de referência M+1 é o lançamento de Água do mês M
(defasagem 1); a da Copel de referência M é o lançamento de Luz do mês M (defasagem 0).
"""
from django.conf import settings
from django.db import models


def pdf_sanepar(instancia, nome):
    return f"usuarios/{instancia.usuario_id}/sanepar/{nome}"


def pdf_copel(instancia, nome):
    return f"usuarios/{instancia.usuario_id}/copel/{nome}"


class FaturaSanepar(models.Model):
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="faturas_sanepar")
    referencia = models.CharField(max_length=7)  # AAAA-MM
    matricula = models.CharField(max_length=30, blank=True)
    vencimento = models.DateField(null=True, blank=True)
    valor_total = models.DecimalField(max_digits=10, decimal_places=2)
    valor_agua = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    valor_esgoto = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    valor_servicos = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    consumo_m3 = models.IntegerField(null=True, blank=True)
    leitura_anterior = models.IntegerField(null=True, blank=True)
    leitura_atual = models.IntegerField(null=True, blank=True)
    data_leitura = models.DateField(null=True, blank=True)
    proxima_leitura = models.DateField(null=True, blank=True)
    dias = models.IntegerField(null=True, blank=True)
    tributos = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    situacao = models.CharField(max_length=30, blank=True)  # Paga / Em aberto
    detalhes = models.JSONField(default=dict, blank=True)  # faixas de consumo, histórico do gráfico
    pdf = models.FileField(upload_to=pdf_sanepar, blank=True)
    importado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["referencia"]
        constraints = [models.UniqueConstraint(fields=["usuario", "referencia"], name="sanepar_ref_unica")]


class FaturaCopel(models.Model):
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="faturas_copel")
    numero_fatura = models.CharField(max_length=30)
    referencia = models.CharField(max_length=7)  # AAAA-MM (mês de consumo)
    situacao = models.CharField(max_length=30, blank=True)  # Quitada / Pendente
    origem = models.CharField(max_length=60, blank=True)
    vencimento = models.DateField(null=True, blank=True)
    data_pagamento = models.DateField(null=True, blank=True)
    valor_total = models.DecimalField(max_digits=10, decimal_places=2)
    consumo_kwh = models.FloatField(null=True, blank=True)
    leitura_anterior = models.FloatField(null=True, blank=True)
    leitura_atual = models.FloatField(null=True, blank=True)
    data_leitura = models.DateField(null=True, blank=True)
    dias = models.IntegerField(null=True, blank=True)
    bandeira = models.CharField(max_length=80, blank=True)
    detalhes = models.JSONField(default=dict, blank=True)  # itens, tributos, histórico, próxima leitura
    pdf = models.FileField(upload_to=pdf_copel, blank=True)
    importado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["referencia", "numero_fatura"]
        constraints = [models.UniqueConstraint(fields=["usuario", "numero_fatura"], name="copel_fatura_unica")]
