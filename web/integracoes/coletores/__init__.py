"""Coletores: cada um é `função(usuario, log) -> dict` (contagens) e roda só no processo do trabalhador.

O registro liga (serviço, tipo da tarefa) ao coletor; os testes trocam as entradas por coletores falsos.
"""
from django.utils.module_loading import import_string

from integracoes.coletores.base import ErroColeta
from integracoes.models import COPEL, NOTAPARANA, SANEPAR

REGISTRO = {
    (NOTAPARANA, "sincronizar"): "integracoes.coletores.notaparana.sincronizar",
    (NOTAPARANA, "reprocessar"): "integracoes.coletores.notaparana.reprocessar",
    (SANEPAR, "sincronizar"): "integracoes.coletores.sanepar.sincronizar",
    (COPEL, "sincronizar"): "integracoes.coletores.copel.sincronizar",
}


def coletor(servico: str, tipo: str):
    alvo = REGISTRO.get((servico, tipo))
    if alvo is None:
        raise ErroColeta(f"Não sei fazer '{tipo}' para {servico}.")
    return import_string(alvo) if isinstance(alvo, str) else alvo
