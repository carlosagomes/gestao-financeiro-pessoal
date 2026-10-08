"""Conta nova já nasce com as categorias, bancos e regras de categorização padrão; conta excluída leva junto os
arquivos dela (PDFs das contas, HTML das notas), que o CASCADE do banco não apaga."""
import shutil
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from core.categorias import BANCOS_PADRAO, CATEGORIAS_PADRAO, REGRAS_PADRAO


def pasta_do_usuario(usuario_id: int) -> Path:
    """Onde ficam os arquivos do usuário (os upload_to dos modelos usam usuarios/<id>/...)."""
    return Path(settings.MEDIA_ROOT) / "usuarios" / str(int(usuario_id))


@receiver(post_delete, sender=settings.AUTH_USER_MODEL)
def apagar_arquivos(sender, instance, **kwargs):
    pasta = pasta_do_usuario(instance.pk)
    transaction.on_commit(lambda: shutil.rmtree(pasta, ignore_errors=True))


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def semear_padroes(sender, instance, created, raw=False, **kwargs):
    if not created or raw:
        return
    from financeiro.models import Banco, Categoria
    from notas.models import RegraCategoria
    Categoria.objects.bulk_create(
        [Categoria(usuario=instance, nome=n, movimentacao=m) for m, nomes in CATEGORIAS_PADRAO.items() for n in nomes],
        ignore_conflicts=True)
    Banco.objects.bulk_create([Banco(usuario=instance, nome=b) for b in BANCOS_PADRAO], ignore_conflicts=True)
    RegraCategoria.objects.bulk_create(
        [RegraCategoria(usuario=instance, padrao=p, categoria=c) for p, c in REGRAS_PADRAO.items()],
        ignore_conflicts=True)
