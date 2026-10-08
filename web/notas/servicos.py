"""Gravação das notas vindas do coletor do Nota Paraná (ou do reprocessamento do HTML salvo)."""
from __future__ import annotations

from django.core.files.base import ContentFile
from django.db import transaction

from core.categorias import categorizar
from core.conversao import campos_do_modelo
from notas.models import ItemNota, Nota, PagamentoNota, PeriodoNP, Placar, RegraCategoria


@transaction.atomic
def salvar_nota(usuario, nota: dict, itens: list[dict] = (), pagamentos: list[dict] = (),
                html: bytes | None = None) -> tuple[Nota, bool]:
    """Insere ou atualiza a nota (nunca mexe na categoria escolhida à mão) e troca itens e pagamentos.
    Devolve (nota, criada)."""
    dados = campos_do_modelo(Nota, nota, ignorar=("id", "usuario", "categoria", "importado_em"))
    obj, criada = Nota.objects.update_or_create(usuario=usuario, chave=dados.pop("chave"), defaults=dados)
    if html:
        if obj.html:
            obj.html.delete(save=False)
        obj.html.save(f"{obj.chave}.html", ContentFile(html), save=True)
    obj.itens.all().delete()
    ItemNota.objects.bulk_create([ItemNota(nota=obj, seq=i, **campos_do_modelo(ItemNota, item, ignorar=("id", "nota", "seq")))
                                  for i, item in enumerate(itens, 1)])
    obj.pagamentos.all().delete()
    PagamentoNota.objects.bulk_create([
        PagamentoNota(nota=obj, seq=i, **campos_do_modelo(PagamentoNota, p, ignorar=("id", "nota", "seq")))
        for i, p in enumerate(pagamentos, 1)])
    return obj, criada


def chaves_importadas(usuario) -> set[str]:
    return set(Nota.objects.filter(usuario=usuario).values_list("chave", flat=True))


def atualizar_credito(usuario, chave: str, credito, situacao: str) -> None:
    """O crédito de notas antigas muda com o tempo ("A CALCULAR" -> "CALCULADO")."""
    dados = campos_do_modelo(Nota, {"credito": credito, "situacao_credito": situacao})
    Nota.objects.filter(usuario=usuario, chave=chave).update(**dados)


def salvar_periodos(usuario, periodos: list[dict]) -> None:
    for p in periodos:
        dados = campos_do_modelo(PeriodoNP, p, ignorar=("id", "usuario", "atualizado_em"))
        PeriodoNP.objects.update_or_create(usuario=usuario, periodo=dados.pop("periodo"), defaults=dados)


def salvar_placar(usuario, dados: dict) -> None:
    Placar.objects.update_or_create(usuario=usuario, defaults={"dados": dados})


def categoria_efetiva(nota: Nota, regras: dict[str, str]) -> str:
    """A categoria escolhida à mão ou, sem ela, a das regras (a mesma conta de core.dados.notas_df)."""
    return nota.categoria or categorizar(f"{nota.emitente_nome or ''} {nota.emitente_fantasia or ''}", regras)


def definir_categoria(usuario, nota_id: int, categoria: str) -> int:
    return Nota.objects.filter(usuario=usuario, id=nota_id).update(categoria=(categoria or "").strip())


@transaction.atomic
def substituir_regras(usuario, regras: dict[str, str]) -> None:
    RegraCategoria.objects.filter(usuario=usuario).delete()
    RegraCategoria.objects.bulk_create([RegraCategoria(usuario=usuario, padrao=p.strip(), categoria=c.strip())
                                        for p, c in regras.items() if p.strip() and c.strip()])
