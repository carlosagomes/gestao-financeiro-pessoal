"""Notas Paraná (o consumo detalhado pelas notas, que não soma nas saídas) e Produtos (o preço de cada um).

Os modais seguem o padrão do sistema: o clique num gráfico abre a URL com ?alvo=<rótulo>. Quem abre um modal a
partir de outro passa ?voltar=<url do anterior>, e o modal novo mostra o botão Voltar.
"""
import json
from urllib.parse import urlencode

import pandas as pd
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_POST

from core import dados
from core.categorias import normalizar
from core.formatos import brl, pct, qtde
from core.graficos import mini, para_json
from notas import consultas, graficos, produtos as calc, servicos
from notas.models import Nota, Placar

PLACAR = [("Saldo disponível", "Saldo disponível", "account_balance_wallet"),
          ("Total de créditos", "Total de créditos", "savings"),
          ("Prêmios de sorteios", "Total de prêmios de sorteios", "emoji_events"),
          ("Notas recebidas", "Total de notas recebidas", "receipt_long")]
GRAOS = {"dia": "dia", "mes": "mês", "ano": "ano"}


def _url(nome: str, *args, **params) -> str:
    query = urlencode({k: v for k, v in params.items() if v not in (None, "")})
    return reverse(nome, args=args) + (f"?{query}" if query else "")


def _voltar(request) -> str:
    """URL do modal anterior (botão Voltar): só caminhos destas páginas, nunca outro site."""
    url = request.GET.get("voltar", "")
    if (url.startswith(("/notas/", "/produtos/")) and len(url) <= 2000 and "\\" not in url and ".." not in url
            and url_has_allowed_host_and_scheme(url, allowed_hosts=None)):
        return url
    return ""


def _placar(usuario):
    placar = Placar.objects.filter(usuario=usuario).first()
    if not placar or not placar.dados:
        return None, None
    return [(rotulo, placar.dados.get(chave) or "—", icone) for rotulo, chave, icone in PLACAR], placar.atualizado_em


# --- Notas Paraná -------------------------------------------------------------------------------

def notas(request):
    base = consultas.carregar(request.user)
    placar, placar_em = _placar(request.user)
    if base.notas.empty:
        return render(request, "notas/notas.html", {"vazio": True, "placar": placar, "placar_em": placar_em})

    n = base.notas
    periodo = consultas.periodo(request.GET, n["mes"].min(), n["mes"].max())
    filtro = n[periodo.contem(n["mes"])]
    pagamentos = base.pagamentos[periodo.contem(base.pagamentos["mes"])]
    cores = consultas.cores_categorias(n)
    por_mes = (filtro.groupby("mes")["valor_total"].sum()
               .reindex(pd.period_range(periodo.inicio, periodo.fim, freq="M"), fill_value=0))

    series = {
        "categoria": filtro.groupby("categoria")["valor_total"].sum().sort_values(ascending=False, kind="stable"),
        "loja": filtro.groupby("loja")["valor_total"].sum().sort_values(ascending=False, kind="stable").head(10),
        "forma": pagamentos.groupby("forma")["valor"].sum().sort_values(ascending=False, kind="stable"),
    }
    altura = max(graficos.altura_barras(len(s), 240, 28) for s in series.values())
    clicaveis = []
    for tipo, serie in series.items():
        fig = graficos.barras(serie, "clique para ver a linha do tempo", altura=altura, tamanho=26,
                              cores=cores if tipo == "categoria" else None)
        fig.update_xaxes(tickprefix="", nticks=3, tickangle=0)  # cartão estreito: sem "R$" no eixo (está no hover)
        clicaveis.append({"titulo": consultas.NOMES[tipo], "fig": para_json(fig), "altura": altura,
                          "clique": _url("notas:detalhe", tipo=tipo, de=periodo.de, ate=periodo.ate),
                          "vazio": serie.empty})

    total = float(filtro["valor_total"].sum())
    regras = dados.regras(request.user)
    linhas = [{"id": int(r.id), "data": r.data_emissao.isoformat(timespec="minutes"),
               "estabelecimento": r.emitente_nome or r.loja, "loja": r.loja,
               "cidade": consultas.cidade(r.emitente_municipio), "valor": round(r.valor_total, 2),
               "credito": None if pd.isna(r.credito) else round(r.credito, 2), "categoria": r.categoria,
               "auto": bool(r.categoria_auto)}
              for r in filtro.sort_values("data_emissao", ascending=False).itertuples()]
    return render(request, "notas/notas.html", {
        "periodo": periodo, "placar": placar, "placar_em": placar_em,
        "total": total, "quantidade": len(filtro), "ticket": total / len(filtro) if len(filtro) else 0,
        "creditos": float(filtro["credito"].fillna(0).sum()),
        "fig_mini": para_json(mini(por_mes.round(2).tolist(), "bar")),
        "fig_mes": para_json(graficos.consumo_por_mes(filtro, cores, periodo)),
        "clicaveis": clicaveis, "linhas": linhas, "categorias": consultas.categorias_conhecidas(n, regras),
        "regras": [{"padrao": p, "categoria": c} for p, c in sorted(regras.items(), key=lambda r: r[0].casefold())],
        "todo_periodo": {"de": str(periodo.meses[0]), "ate": str(periodo.meses[-1])},
    })


def _parametros_detalhe(request) -> dict:
    tipo, alvo = request.GET.get("tipo"), request.GET.get("alvo", "")
    if tipo not in consultas.TIPOS or not alvo:
        raise Http404
    grao = request.GET.get("grao")
    return {"tipo": tipo, "alvo": alvo, "de": request.GET.get("de", ""), "ate": request.GET.get("ate", ""),
            "escopo": "tudo" if request.GET.get("escopo") == "tudo" else "pagina",
            "grao": grao if grao in GRAOS else "mes", "voltar": _voltar(request)}


def _linhas_detalhe(request, p: dict):
    """Base do usuário, as linhas do alvo no período escolhido, todas as do alvo e o período (início, fim)."""
    base = consultas.carregar(request.user)
    todas = consultas.linhas_do_alvo(base, p["tipo"], p["alvo"]) if not base.notas.empty else None
    if todas is None or todas.empty:
        raise Http404
    meses = base.notas["mes"]
    pagina = consultas.periodo({"de": p["de"], "ate": p["ate"]}, meses.min(), meses.max())
    inicio, fim = (meses.min(), meses.max()) if p["escopo"] == "tudo" else (pagina.inicio, pagina.fim)
    linhas = todas[(todas["mes"] >= inicio) & (todas["mes"] <= fim)]
    return base, linhas, todas, inicio, fim


def _aqui_detalhe(p: dict) -> str:
    """URL deste modal, para o Voltar dos modais abertos a partir dele (sem a cadeia inteira, se ficar longa)."""
    aqui = _url("notas:detalhe", **p)
    return aqui if len(aqui) <= 1500 else _url("notas:detalhe", **{**p, "voltar": ""})


def detalhe(request):
    """Modal da categoria, loja ou forma de pagamento: linha do tempo, rankings, produtos e compra por compra."""
    p = _parametros_detalhe(request)
    base, linhas, todas, inicio, fim = _linhas_detalhe(request, p)
    tipo = p["tipo"]
    filhos = _aqui_detalhe(p)
    periodo_texto = consultas.Periodo(inicio, fim, []).texto
    contexto = {"p": p, "titulo": f"{consultas.TIPOS[tipo]}: {p['alvo']}", "periodo_texto": periodo_texto,
                "grao_nome": GRAOS[p["grao"]], "vazio": linhas.empty,
                "ocultos": {k: p[k] for k in ("tipo", "alvo", "de", "ate", "voltar") if p[k]}}
    if linhas.empty:
        return render(request, "notas/_detalhe.html", contexto)

    total, compras = float(linhas["valor"].sum()), int(linhas["nota_id"].nunique())
    meses = (fim - inicio).n + 1
    series = {dim: consultas.ranking(dim, linhas, base.pagamentos) for dim in consultas.RANKINGS[tipo]}
    series = {dim: s if dim == "semana" else s.head(8) for dim, s in series.items()}
    altura = max(graficos.altura_barras(len(s), 180) for s in series.values())  # os dois cartões alinhados
    rankings = []
    for dim, serie in series.items():
        clique = "" if dim == "semana" else _url("notas:detalhe", tipo=dim, de=p["de"], ate=p["ate"],
                                                  escopo=p["escopo"], grao=p["grao"], voltar=filhos)
        cores = consultas.cores_categorias(base.notas) if dim == "categoria" else None  # as mesmas da página
        fig = graficos.barras(serie, "clique para abrir" if clique else "", altura=altura, cores=cores)
        rankings.append({"titulo": consultas.NOMES[dim], "altura": altura, "clique": clique, "fig": para_json(fig)})
    top = consultas.mais_comprados(linhas, base.itens)
    altura_produtos = graficos.altura_barras(len(top), 200)
    meses_lista, proximo = consultas.compra_por_compra(linhas, tipo, base.itens)
    contexto.update({
        "total": total, "compras": compras, "ticket": total / compras, "media_mes": total / meses,
        "compras_detalhe": f"última em {linhas['data'].max():%d/%m/%Y}",
        "meses_detalhe": f"{meses} {'mês' if meses == 1 else 'meses'}: {consultas.Periodo(inicio, fim, []).texto}",
        "fig_tempo": para_json(graficos.linha_do_tempo(linhas, todas, tipo, p["grao"], inicio, fim)),
        "rankings": rankings, "altura_produtos": altura_produtos,
        "fig_produtos": para_json(graficos.barras(top["pago"].set_axis(top["produto"]),
                                                  "clique para ver o histórico de preço", altura=altura_produtos,
                                                  chaves=top.index.tolist())) if len(top) else "",
        "clique_produto": _url("notas:produto", voltar=filhos),
        "meses_lista": meses_lista, "voltar_nota": filhos,
        "url_mais": _url("notas:detalhe_compras", **p, antes=proximo) if proximo else "",
    })
    return render(request, "notas/_detalhe.html", contexto)


def detalhe_compras(request):
    """Mais meses do "Compra por compra" (carregados sob demanda)."""
    p = _parametros_detalhe(request)
    antes = consultas.ler_mes(request.GET.get("antes"))
    base, linhas, _, _, _ = _linhas_detalhe(request, p)
    meses_lista, proximo = consultas.compra_por_compra(linhas, p["tipo"], base.itens, antes=antes)
    return render(request, "notas/_compras.html", {
        "meses_lista": meses_lista, "voltar_nota": _aqui_detalhe(p),
        "url_mais": _url("notas:detalhe_compras", **p, antes=proximo) if proximo else ""})


def nota(request, pk: int):
    """Modal da nota completa: emitente, chave, itens e pagamentos."""
    obj = get_object_or_404(Nota, pk=pk, usuario=request.user)
    itens = list(obj.itens.all())
    grupos = calc.grupos(pd.Series([i.ean for i in itens], dtype=object),
                         pd.Series([i.descricao for i in itens], dtype=object)).tolist()
    aqui = _url("notas:nota", pk, voltar=_voltar(request))
    linhas = [{"item": i, "pago": (i.valor_total or 0) - (i.valor_desconto or 0),
               "url": _url("notas:produto", grupo=g, voltar=aqui)} for i, g in zip(itens, grupos)]
    chave = " ".join(obj.chave[i:i + 4] for i in range(0, len(obj.chave), 4))
    cidade = "/".join(x for x in (obj.emitente_municipio, obj.emitente_uf) if x)
    endereco = obj.emitente_endereco or ""
    if obj.emitente_municipio and obj.emitente_municipio.casefold() not in endereco.casefold():
        endereco = " · ".join(x for x in (endereco, cidade) if x)
    return render(request, "notas/_nota.html", {
        "nota": obj, "linhas": linhas, "pagamentos": list(obj.pagamentos.all()), "chave": chave, "endereco": endereco,
        "categoria": servicos.categoria_efetiva(obj, dados.regras(request.user)), "voltar": _voltar(request),
        "desconto": sum((i.valor_desconto or 0) for i in itens)})


def _corpo_json(request):
    try:
        corpo = json.loads(request.body or b"{}")
    except (ValueError, UnicodeDecodeError):
        return None
    return corpo if isinstance(corpo, dict) else None


@require_POST
def api_categoria(request):
    """Troca a categoria de uma nota. Vazio = volta a usar as regras."""
    corpo = _corpo_json(request)
    try:
        nota_id = int(corpo["id"])
        categoria = str(corpo.get("categoria") or "").strip()
    except (TypeError, KeyError, ValueError):
        return JsonResponse({"erro": "Dados inválidos."}, status=400)
    if len(categoria) > 60:
        return JsonResponse({"erro": "Categoria muito longa (máximo 60 letras)."}, status=400)
    if not servicos.definir_categoria(request.user, nota_id, categoria):
        return JsonResponse({"erro": "Nota não encontrada."}, status=404)
    obj = Nota.objects.get(pk=nota_id, usuario=request.user)
    return JsonResponse({"ok": True, "categoria": servicos.categoria_efetiva(obj, dados.regras(request.user)),
                         "auto": not categoria})


@require_POST
def api_regras(request):
    """Troca todas as regras de categorização do usuário."""
    corpo = _corpo_json(request)
    lista = corpo.get("regras") if corpo else None
    if not isinstance(lista, list) or len(lista) > 2000:
        return JsonResponse({"erro": "Dados inválidos."}, status=400)
    regras, vistos = {}, set()
    for r in lista:
        if not isinstance(r, dict):
            return JsonResponse({"erro": "Dados inválidos."}, status=400)
        padrao, categoria = str(r.get("padrao") or "").strip(), str(r.get("categoria") or "").strip()
        if not padrao and not categoria:
            continue
        if not padrao or not categoria:
            return JsonResponse({"erro": "Preencha o trecho e a categoria de todas as regras."}, status=400)
        if len(padrao) > 80 or len(categoria) > 60:
            return JsonResponse({"erro": f"Regra muito longa: {padrao[:30]}"}, status=400)
        if normalizar(padrao) in vistos:
            return JsonResponse({"erro": f"O trecho \"{padrao}\" aparece mais de uma vez."}, status=400)
        vistos.add(normalizar(padrao))
        regras[padrao] = categoria
    servicos.substituir_regras(request.user, regras)
    return JsonResponse({"ok": True, "total": len(regras)})


# --- Produtos -----------------------------------------------------------------------------------

def produtos(request):
    base = consultas.carregar(request.user)
    itens = base.itens if not base.notas.empty else None
    if itens is None or itens.empty:
        return render(request, "notas/produtos.html", {"vazio": True})
    filtro = consultas.FiltroProdutos.ler(request.GET, itens)
    tabela = filtro.aplicar(itens)
    v = calc.variacoes(tabela)
    url_produto = reverse("notas:produto")
    contexto = {
        "filtro": filtro, "total": len(tabela), "vezes": int(tabela["compras"].sum()) if len(tabela) else 0,
        "gasto": float(tabela["gasto"].sum()) if len(tabela) else 0, "minimo": calc.MINIMO_VARIACAO,
        "caros": f"{len(v['subiram'])} de {len(v['com_variacao'])}",
        "caros_detalhe": f"{len(v['cairam'])} mais baratos · {v['iguais']} no mesmo preço",
        "caros_ajuda": (f"Entre os comprados {calc.MINIMO_VARIACAO} vezes ou mais: último preço pago × primeiro. "
                        f"{len(v['cairam'])} ficaram mais baratos e {v['iguais']} no mesmo preço."),
        "url_produto": url_produto, "url_dados": _url("notas:produtos_dados", **filtro.parametros),
        "categorias": sorted(itens["categoria"].dropna().unique(), key=str.casefold),
        "lojas": sorted(itens["loja"].dropna().unique(), key=str.casefold),
    }
    for chave, parte, cor in (("subiram", v["subiram"], graficos.ALTA), ("cairam", v["cairam"], graficos.QUEDA)):
        parte = parte.head(10)
        contexto[f"fig_{chave}"] = para_json(graficos.variacoes(parte, cor)) if len(parte) else ""
        contexto[f"altura_{chave}"] = graficos.altura_barras(len(parte), 160)
    parcial = request.headers.get("HX-Request") and request.headers.get("HX-Target") == "produtos-resultado"
    return render(request, "notas/_produtos_resultado.html" if parcial else "notas/produtos.html", contexto)


@require_GET
def produtos_dados(request):
    """JSON da tabela "Todos os produtos" (mesmos filtros da página)."""
    base = consultas.carregar(request.user)
    if base.notas.empty:
        return JsonResponse({"linhas": []})
    itens = base.itens
    if itens.empty:
        return JsonResponse({"linhas": []})
    tabela = consultas.FiltroProdutos.ler(request.GET, itens).aplicar(itens)
    return JsonResponse({"linhas": consultas.linhas_produtos(tabela)})


def produto(request):
    """Modal "Histórico de preço": todas as compras do produto, em todas as lojas, todo o histórico."""
    grupo = request.GET.get("grupo") or request.GET.get("alvo") or ""
    juntar = (request.GET.get("juntar") or "").strip()[:60]
    base = consultas.carregar(request.user)
    if base.notas.empty or not grupo:
        raise Http404
    itens = base.itens
    deste = itens[itens["grupo"] == grupo]
    if deste.empty:
        raise Http404
    hist = calc.historico(itens, [grupo], juntar)
    painel = calc.painel(hist)
    cores = graficos.cores_lojas(painel["lojas"])
    variacao, primeiro, ultimo = painel["variacao"], painel["primeiro"], painel["ultimo"]
    delta = (f"{'▲' if variacao > 0 else '▼'} {pct(abs(variacao), sinal=False)} desde {primeiro['data']:%m/%Y}"
             if variacao is not None else "")
    compras = hist.sort_values(["data", "nota_id", "seq"], ascending=False, kind="stable")
    compras = compras.astype(object).where(compras.notna(), None)
    unidade, menor, maior = painel["unidade"], painel["menor"], painel["maior"]
    return render(request, "notas/_produto.html", {
        "ultimo_valor": f"{brl(ultimo['preco'])}/{unidade}",
        "ultimo_detalhe": f"{ultimo['data']:%d/%m/%Y} · {ultimo['loja']}",
        "comprado": f"{painel['compras']} {'vez' if painel['compras'] == 1 else 'vezes'}",
        "comprado_detalhe": f"{qtde(painel['quantidade'])} {unidade} · {brl(painel['total'])} no total",
        "menor_detalhe": f"{menor['data']:%d/%m/%Y} · {menor['loja']}",
        "maior_detalhe": f"{maior['data']:%d/%m/%Y} · {maior['loja']}",
        "grupo": grupo, "juntar": juntar, "voltar": _voltar(request), "nome": deste["produto"].iloc[0],
        "ean": grupo[4:] if grupo.startswith("ean:") else "",
        "categoria": calc.mais_frequente(hist.assign(_k=0), "_k", "categoria").iloc[0],
        "n_lojas": len(painel["lojas"]), "painel": painel,
        # nomes próprios: "delta" no contexto vazaria para todos os {% include "core/_kpi.html" %}
        "delta_ultimo": delta, "classe_ultimo": "sobe-ruim" if variacao and variacao > 0 else "desce-bom",
        "fig_preco": para_json(graficos.preco_no_tempo(hist, painel["lojas"], cores)),
        "fig_lojas": para_json(graficos.ultimo_por_loja(hist, cores)),
        "altura_lojas": graficos.altura_barras(len(painel["lojas"]), 130, 34),
        "compras": compras.to_dict("records"),
        "aqui": _url("notas:produto", grupo=grupo, juntar=juntar, voltar=_voltar(request)),
    })
