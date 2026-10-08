"""Módulo PJ (opcional, ligado por usuário): informe mensal de horas da prestação de serviço.

Valor da NF = horas × valor hora + horas de sobreaviso × valor hora × pct_sobreaviso (1/3) − desconto
(plano de saúde). O informe do mês M é pago como o "Salário" do próprio mês M.
"""
from django.conf import settings
from django.db import models


class Informe(models.Model):
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="informes")
    mes = models.CharField(max_length=7)  # AAAA-MM
    horas = models.FloatField(default=0)  # horas trabalhadas (o informe vem em h:mm)
    horas_sobreaviso = models.FloatField(default=0)
    valor_hora = models.DecimalField(max_digits=10, decimal_places=2)
    pct_sobreaviso = models.FloatField(default=1 / 3)
    desconto = models.DecimalField(max_digits=10, decimal_places=2, default=0)  # plano de saúde
    valor_nf = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)  # o que veio no informe
    previsao = models.BooleanField(default=False, help_text="Mês futuro estimado (ainda sem informe)")
    observacao = models.CharField(max_length=200, blank=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["mes"]
        constraints = [models.UniqueConstraint(fields=["usuario", "mes"], name="informe_unico_por_mes")]

    def __str__(self):
        return f"Informe {self.mes}"

    @property
    def valor_normal(self) -> float:
        return round(self.horas * float(self.valor_hora), 2)

    @property
    def valor_sobreaviso(self) -> float:
        return round(self.horas_sobreaviso * float(self.valor_hora) * self.pct_sobreaviso, 2)

    @property
    def total_bruto(self) -> float:
        return round(self.valor_normal + self.valor_sobreaviso, 2)

    @property
    def total_calculado(self) -> float:
        return round(self.total_bruto - float(self.desconto), 2)
