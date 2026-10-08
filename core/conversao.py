"""Converte os dicionários dos leitores/coletores (strings ISO, floats, JSON em texto) para os campos dos modelos."""
from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.db import models
from django.utils import timezone


def valor_campo(campo: models.Field, valor):
    if valor is None or (isinstance(valor, str) and valor.strip() == "" and not isinstance(campo, models.CharField)):
        return None
    if isinstance(campo, models.DecimalField):
        try:
            return Decimal(str(round(float(valor), campo.decimal_places)))
        except (TypeError, ValueError, InvalidOperation):
            return None
    if isinstance(campo, models.DateTimeField):
        if isinstance(valor, datetime):
            momento = valor
        else:
            momento = datetime.fromisoformat(str(valor))
        return timezone.make_aware(momento) if timezone.is_naive(momento) else momento
    if isinstance(campo, models.DateField):
        if isinstance(valor, datetime):
            return valor.date()
        if isinstance(valor, date):
            return valor
        return date.fromisoformat(str(valor)[:10])
    if isinstance(campo, (models.IntegerField, models.BigIntegerField)):
        return int(float(valor))
    if isinstance(campo, models.FloatField):
        return float(valor)
    if isinstance(campo, models.JSONField):
        if isinstance(valor, str):
            try:
                valor = json.loads(valor)
            except ValueError:
                return {"texto": valor}
        return valor if isinstance(valor, (dict, list)) else {"valor": valor}
    if isinstance(campo, models.CharField) or isinstance(campo, models.TextField):
        texto = "" if valor is None else str(valor)
        return texto[: campo.max_length] if getattr(campo, "max_length", None) else texto
    return valor


def campos_do_modelo(modelo: type[models.Model], dados: dict, ignorar: tuple[str, ...] = (),
                     manter_vazios: bool = True) -> dict:
    """Só as chaves que são campos do modelo, já convertidas. `manter_vazios=False` descarta os None
    (para atualizar sem apagar o que já estava salvo)."""
    saida = {}
    for campo in modelo._meta.concrete_fields:
        nome = campo.name
        if nome in ignorar or nome not in dados or isinstance(campo, (models.ForeignKey, models.FileField)):
            continue
        convertido = valor_campo(campo, dados[nome])
        if convertido is None and not manter_vazios:
            continue
        if convertido is None and isinstance(campo, (models.CharField, models.TextField)):
            convertido = ""
        saida[nome] = convertido
    return saida
