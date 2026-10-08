"""Controle de acesso por módulo: o módulo PJ só existe para quem tem `pj_habilitado`."""
from functools import wraps

from django.http import Http404


def exige_pj(view):
    """404 (e não 403) para quem não tem o módulo: a página nem "existe" para os outros usuários."""
    @wraps(view)
    def envolvida(request, *args, **kwargs):
        if not getattr(request.user, "pj_habilitado", False):
            raise Http404
        return view(request, *args, **kwargs)
    return envolvida
