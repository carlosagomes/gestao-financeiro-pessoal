"""Resumo (o ano mês a mês, como a aba Início da planilha) e Lançamentos (a planilha editável).

As telas leem por `consultas` e gravam por `servicos`, que registra no histórico toda alteração e exclusão.
"""
from __future__ import annotations

import json
from datetime import date
from decimal import Decimal, InvalidOperation
from itertools import accumulate

import plotly.graph_objects as go
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import urlencode
from django.views.decorators.http import require_GET, require_http_methods

from core import graficos
from core.formatos import MESES, MESES_CURTOS, brl, pct
from financeiro import consultas, servicos
from financeiro.models import ENTRADA, SAIDA, Banco, Categoria, HistoricoLancamento, Lancamento

ORIGEM_TABELA = "tabela de lançamentos"
LIMITES = {"banco": ("Banco", 60), "categoria": ("Categoria", 60), "descricao": ("Descrição", 255)}
VALOR_MAXIMO = Decimal("9999999999.99")
MAXIMO_IDS = 5000  # excluir/marcar em lote
NOMES_CAMPOS = {"data": "Data", "movimentacao": "Movimentação", "banco": "Banco", "categoria": "Categoria",
                "descricao": "Descrição", "valor": "Valor", "pago": "Pago", "criado": "Criado",
                "excluído": "Excluído"}


def _qtd(n: int, singular: str, plural: str) -> str:
    return f"{n} {singular if n == 1 else plural}"


def _ano(params) -> int:
    return consultas.inteiro(params.get("ano"), timezone.localdate().year, 1900, 2999)


# --- Resumo ----------------------------------------------------------------------------------

def _rgba(cor: str, alfa: float) -> str:
    return "rgba({},{},{},{})".format(*(int(cor[i:i + 2], 16) for i in (1, 3, 5)), alfa)


def _mini(valores, cor: str, tipo: str = "area") -> str:
    fig = graficos.mini([float(v) for v in valores], tipo, cor)
    if tipo == "area":
        fig.update_traces(fillcolor=_rgba(cor, .12))
    return graficos.para_json(fig)


def _grafico_mensal(meses: list[dict], altura: int) -> str:
    """Entrada e Saída paga lado a lado (o A pagar empilhado sobre a Saída) e a linha do Saldo."""
    numeros = [m["mes"] for m in meses]
    saidas = [float(m["saidas"]) for m in meses]
    fig = go.Figure()
    fig.add_bar(x=MESES_CURTOS, y=[float(m["entradas"]) for m in meses], name="Entrada", offsetgroup="entrada",
                marker=dict(color=graficos.ENTRADA, opacity=[.5 if m["status"] == "previsao" else 1 for m in meses]),
                customdata=numeros, hovertemplate="Entrada<br>R$ %{y:,.2f}<extra></extra>")
    fig.add_bar(x=MESES_CURTOS, y=saidas, name="Saída paga", offsetgroup="saida", marker_color=graficos.SAIDA,
                customdata=numeros, hovertemplate="Saída paga<br>R$ %{y:,.2f}<extra></extra>")
    if any(m["a_pagar"] for m in meses):
        fig.add_bar(x=MESES_CURTOS, y=[float(m["a_pagar"]) or None for m in meses], base=saidas, name="A pagar",
                    offsetgroup="saida", marker_color=graficos.A_PAGAR, customdata=numeros,
                    hovertemplate="A pagar<br>R$ %{y:,.2f}<extra></extra>")
    fig.add_scatter(x=MESES_CURTOS, y=[float(m["saldo"]) if m["n"] else None for m in meses], name="Saldo",
                    mode="lines+markers", line=dict(color=graficos.SALDO, width=2), marker=dict(size=8),
                    customdata=numeros, hovertemplate="Saldo<br>R$ %{y:,.2f}<extra></extra>")
    fig.update_layout(barmode="group")
    return graficos.para_json(graficos.estilizar(fig, altura))


def resumo(request):
    ano = _ano(request.GET)
    r = consultas.resumo_anual(request.user, ano)
    contexto = {"ano": ano, "anos": sorted(set(consultas.anos(request.user)) | {ano}), "r": r}
    if not r["tem_dados"]:
        return render(request, "financeiro/resumo.html", contexto)

    meses, total = r["meses"], r["total"]
    com_lancamento = [m for m in meses if m["n"]]
    fechados = [m for m in com_lancamento if m["status"] != "previsao"] or com_lancamento
    com_entrada = sum(1 for m in meses if m["n_entradas"])
    kpis = [
        dict(rotulo="Entradas", valor=brl(total["entradas"]), cor="verde", icone="south_west",
             detalhe=f"média de {brl(total['entradas'] / max(com_entrada, 1))} por mês",
             mini=_mini([m["entradas"] for m in com_lancamento], graficos.ENTRADA)),
        dict(rotulo="Saídas pagas", valor=brl(total["saidas"]), cor="vermelho", icone="north_east",
             detalhe=(f"{pct(float(total['saidas'] / total['entradas']), 0, sinal=False)} das entradas"
                      if total["entradas"] else "só o que já foi pago"),
             mini=_mini([m["saidas"] for m in fechados], graficos.SAIDA)),
        dict(rotulo="Saldo", valor=brl(total["saldo"]), cor="azul", icone="account_balance_wallet",
             detalhe="entradas − saídas pagas, acumulado",
             mini=_mini(list(accumulate(m["saldo"] for m in com_lancamento)), graficos.SALDO)),
        dict(rotulo="A pagar", valor=brl(total["a_pagar"]), cor="laranja", icone="schedule",
             detalhe=_qtd(total["pendentes"], "conta pendente", "contas pendentes") if total["pendentes"]
             else "nada pendente",
             mini=_mini([m["a_pagar"] for m in meses], graficos.A_PAGAR, "bar"),
             ajuda="Saídas lançadas e ainda não marcadas como pagas. Como na planilha, não entram na Saída."),
    ]
    categorias = consultas.saidas_por_categoria(request.user, ano)
    altura = max(360, 26 * len(categorias) + 50)
    contexto.update(
        kpis=kpis, altura=altura, fig_mensal=_grafico_mensal(meses, altura),
        fig_categorias=graficos.para_json(graficos.barras_horizontais(
            [c for c, _ in categorias], [float(v) for _, v in categorias], altura,
            dica="Clique para ver os lançamentos")) if categorias else "",
        url_mes=f"{reverse('financeiro:resumo_mes')}?ano={ano}",
        url_categoria=f"{reverse('financeiro:resumo_categoria')}?ano={ano}",
    )
    return render(request, "financeiro/resumo.html", contexto)


@require_GET
def resumo_mes(request):
    """Modal: os lançamentos de um mês, separados em entradas e saídas."""
    ano, mes = _ano(request.GET), consultas.inteiro(request.GET.get("alvo"), 0, 1, 12)
    if not mes:
        raise Http404
    lancamentos = Lancamento.objects.filter(usuario=request.user, data__year=ano, data__month=mes)
    t = consultas.totais(lancamentos)
    lista = list(lancamentos.order_by("data", "id"))
    return render(request, "financeiro/_modal_mes.html", {
        "titulo": f"{MESES[mes - 1]}/{ano}", "t": t,
        "status": consultas.status_mes(ano, mes, t, timezone.localdate()),
        "grupos": [("Entradas", [l for l in lista if l.movimentacao == ENTRADA], t["entradas"]),
                   ("Saídas", [l for l in lista if l.movimentacao == SAIDA], t["saidas"] + t["a_pagar"])],
        "url_lancamentos": f"{reverse('financeiro:lancamentos')}?ano={ano}&mes={mes}",
    })


@require_GET
def resumo_categoria(request):
    """Modal: as saídas de uma categoria no ano, mês a mês."""
    ano, categoria = _ano(request.GET), request.GET.get("alvo", "").strip()
    lancamentos = Lancamento.objects.filter(usuario=request.user, data__year=ano, movimentacao=SAIDA,
                                            categoria=categoria)
    lista = list(lancamentos.order_by("data", "id"))
    pago, a_pagar = [0.0] * 12, [0.0] * 12
    grupos = {}
    for l in lista:
        (pago if l.pago else a_pagar)[l.data.month - 1] += float(l.valor)
        grupos.setdefault(l.data.month, []).append(l)
    t = consultas.totais(lancamentos)
    meses_pagos = sum(1 for v in pago if v)
    fig = go.Figure()
    fig.add_bar(x=MESES_CURTOS, y=pago, name="Paga", marker_color=graficos.SAIDA,
                hovertemplate="Paga<br>R$ %{y:,.2f}<extra></extra>")
    if any(a_pagar):
        fig.add_bar(x=MESES_CURTOS, y=a_pagar, name="A pagar", marker_color=graficos.A_PAGAR,
                    hovertemplate="A pagar<br>R$ %{y:,.2f}<extra></extra>")
    fig.update_layout(barmode="stack")
    return render(request, "financeiro/_modal_categoria.html", {
        "categoria": categoria, "ano": ano, "t": t, "fig": graficos.para_json(graficos.estilizar(fig, 260)),
        "media": t["saidas"] / meses_pagos if meses_pagos else None,
        "detalhes": {"pago": _qtd(t["n_pagas"], "pagamento", "pagamentos"),
                     "a_pagar": _qtd(t["pendentes"], "pendente", "pendentes") if t["pendentes"] else "nada pendente",
                     "media": f"em {_qtd(meses_pagos, 'mês', 'meses')} com pagamento"},
        "meses": [{"nome": MESES[m - 1], "itens": itens, "total": sum(l.valor for l in itens)}
                  for m, itens in grupos.items()],
        "url_lancamentos": f"{reverse('financeiro:lancamentos')}?"
                           + urlencode({"ano": ano, "mes": 0, "mov": SAIDA, "busca": categoria}),
    })


# --- Lançamentos -----------------------------------------------------------------------------

def _dados_tabela(usuario, f: consultas.Filtros) -> dict:
    lancamentos = consultas.filtrar(usuario, f)
    t = consultas.totais(lancamentos)
    return {"linhas": [consultas.linha(l) for l in lancamentos],
            "totais": {c: float(v) if isinstance(v, Decimal) else v for c, v in t.items()},
            "contexto": consultas.contexto(usuario, f), "opcoes": consultas.opcoes(usuario)}


def lancamentos(request):
    f = consultas.ler_filtros(request.GET)
    dados = _dados_tabela(request.user, f)
    t = dados["totais"]
    return render(request, "financeiro/lancamentos.html", {
        "f": f, "dados": dados, "t": t, "meses": list(enumerate(MESES, 1)),
        "anos": sorted(set(consultas.anos(request.user)) | {f.ano}),
        "titulo_tabela": f"{MESES[f.mes - 1]}/{f.ano}" if f.mes else f"Todos os meses de {f.ano}",
        "detalhes": {
            "entradas": _qtd(t["n_entradas"], "entrada", "entradas"),
            "saidas": _qtd(t["n_pagas"], "saída paga", "saídas pagas"),
            "a_pagar": _qtd(t["pendentes"], "conta pendente", "contas pendentes") if t["pendentes"]
            else "nada pendente",
        },
    })


def _erro(mensagem: str, status: int = 400, **extra) -> JsonResponse:
    return JsonResponse({"erro": mensagem, **extra}, status=status)


def _json(request) -> dict:
    try:
        dados = json.loads(request.body or b"{}")
    except (ValueError, UnicodeDecodeError):
        raise ValueError("Dados inválidos") from None
    if not isinstance(dados, dict):
        raise ValueError("Dados inválidos")
    return dados


def _validar(dados: dict) -> dict:
    """Confere os campos antes de `servicos.normalizar`, com mensagens para a pessoa (ValueError)."""
    campos = {c: dados[c] for c in servicos.CAMPOS if c in dados}
    if "data" in campos:
        try:
            campos["data"] = date.fromisoformat(str(campos["data"])[:10])
        except ValueError:
            raise ValueError("Data inválida") from None
    if "valor" in campos:
        try:
            valor = Decimal(str(campos["valor"]).strip().replace(",", "."))
        except InvalidOperation:
            raise ValueError("Valor inválido") from None
        if not valor.is_finite():
            raise ValueError("Valor inválido")
        if valor > VALOR_MAXIMO:
            raise ValueError("Valor alto demais")
        campos["valor"] = valor
    for campo, (nome, limite) in LIMITES.items():
        if campo in campos and len(str(campos[campo] or "").strip()) > limite:
            raise ValueError(f"{nome}: no máximo {limite} caracteres")
    return campos


def _ids(dados: dict) -> list[int]:
    ids = dados.get("ids")
    if not isinstance(ids, list) or not ids:
        raise ValueError("Escolha pelo menos um lançamento")
    if len(ids) > MAXIMO_IDS:
        raise ValueError(f"No máximo {MAXIMO_IDS} lançamentos de uma vez")
    try:
        return [int(i) for i in ids]
    except (TypeError, ValueError):
        raise ValueError("Lista de lançamentos inválida") from None


@require_http_methods(["GET", "POST"])
def api_lancamentos(request):
    """GET: linhas da tabela com os filtros da tela. POST: cria um lançamento."""
    if request.method == "GET":
        return JsonResponse(_dados_tabela(request.user, consultas.ler_filtros(request.GET)))
    try:
        lancamento = servicos.criar_lancamento(request.user, _validar(_json(request)), origem=ORIGEM_TABELA)
    except ValueError as e:
        return _erro(str(e))
    return JsonResponse({"linha": consultas.linha(lancamento)}, status=201)


@require_http_methods(["PATCH", "DELETE"])
def api_lancamento(request, pk: int):
    lancamento = get_object_or_404(Lancamento, pk=pk, usuario=request.user)
    if request.method == "DELETE":
        return JsonResponse({"excluidos": servicos.excluir_lancamentos(request.user, [lancamento.id],
                                                                      origem=ORIGEM_TABELA)})
    try:
        mudou = servicos.atualizar_lancamento(lancamento, _validar(_json(request)), origem=ORIGEM_TABELA)
    except ValueError as e:
        return _erro(str(e))
    return JsonResponse({"linha": consultas.linha(lancamento), "mudou": sorted(mudou)})


@require_http_methods(["POST"])
def api_excluir(request):
    try:
        ids = _ids(_json(request))
    except ValueError as e:
        return _erro(str(e))
    return JsonResponse({"excluidos": servicos.excluir_lancamentos(request.user, ids, origem=ORIGEM_TABELA)})


@require_http_methods(["POST"])
def api_marcar_pagas(request):
    try:
        ids = _ids(_json(request))
    except ValueError as e:
        return _erro(str(e))
    marcados = servicos.marcar_pagos(request.user, ids)
    linhas = [consultas.linha(l) for l in Lancamento.objects.filter(usuario=request.user, id__in=ids)]
    return JsonResponse({"marcados": marcados, "linhas": linhas})


@require_http_methods(["POST"])
def api_copiar_mes(request):
    """Copia o mês anterior para o mês escolhido (não pagos). Se o mês já tem lançamentos, pede confirmação."""
    try:
        dados = _json(request)
    except ValueError as e:
        return _erro(str(e))
    ano = consultas.inteiro(dados.get("ano"), 0, 1900, 2999)
    mes = consultas.inteiro(dados.get("mes"), 0, 1, 12)
    if not ano or not mes:
        return _erro("Escolha um mês")
    ano_ant, mes_ant = consultas.mes_anterior(ano, mes)
    do_usuario = Lancamento.objects.filter(usuario=request.user)
    if not do_usuario.filter(data__year=ano_ant, data__month=mes_ant).exists():
        return _erro(f"{MESES[mes_ant - 1]}/{ano_ant} não tem lançamentos para copiar")
    existentes = do_usuario.filter(data__year=ano, data__month=mes).count()
    if existentes and dados.get("confirmar") is not True:
        return _erro(f"{MESES[mes - 1]} já tem {_qtd(existentes, 'lançamento', 'lançamentos')}", 409,
                     precisa_confirmar=True, existentes=existentes)
    return JsonResponse({"copiados": len(servicos.copiar_mes(request.user, ano, mes))}, status=201)


@require_http_methods(["GET", "POST"])
def cadastros(request):
    """Modal: nova categoria e novo banco (as opções das colunas da tabela)."""
    erros, valores, criado = {}, {}, ""
    if request.method == "POST":
        tipo = request.POST.get("tipo")
        nome = " ".join(request.POST.get("nome", "").split())
        valores[tipo] = nome
        modelo = {"categoria": Categoria, "banco": Banco}.get(tipo)
        movimentacao = request.POST.get("movimentacao", SAIDA)
        if modelo is None:
            return _erro("Cadastro inválido")
        if not nome:
            erros[tipo] = "Digite o nome"
        elif len(nome) > 60:
            erros[tipo] = "No máximo 60 caracteres"
        elif tipo == "categoria" and movimentacao not in (ENTRADA, SAIDA):
            erros[tipo] = "Escolha Entrada ou Saída"
        elif modelo.objects.filter(usuario=request.user, nome__iexact=nome).exists():
            erros[tipo] = f"“{nome}” já existe"
        else:
            extra = {"movimentacao": movimentacao} if tipo == "categoria" else {}
            modelo.objects.create(usuario=request.user, nome=nome, **extra)
            criado = f"Categoria “{nome}” criada" if tipo == "categoria" else f"Banco “{nome}” criado"
            valores = {}
    categorias = Categoria.objects.filter(usuario=request.user)
    resposta = render(request, "financeiro/_modal_cadastros.html", {
        "erros": erros, "valores": valores, "movimentacao": request.POST.get("movimentacao", SAIDA),
        "saidas": [c.nome for c in categorias if c.movimentacao == SAIDA],
        "entradas": [c.nome for c in categorias if c.movimentacao == ENTRADA],
        "bancos": Banco.objects.filter(usuario=request.user).values_list("nome", flat=True),
    })
    if criado:
        resposta["HX-Trigger"] = json.dumps({"toast": criado, "opcoesMudaram": consultas.opcoes(request.user)})
    return resposta


def _legivel(campo: str, texto: str | None) -> str:
    if texto is None or texto == "":
        return "—"
    if campo in ("criado", "excluído"):  # "2026-09-05 · Cartão · 100.00" (servicos): data e valor no formato daqui
        partes = texto.split(" · ")
        return " · ".join([_legivel("data", partes[0]), *partes[1:-1], _legivel("valor", partes[-1])]
                          if len(partes) > 1 else partes)
    try:
        if campo == "valor":
            return brl(Decimal(texto))
        if campo == "data":
            return date.fromisoformat(texto).strftime("%d/%m/%Y")
    except (InvalidOperation, ValueError):
        pass
    return texto


@require_GET
def historico(request):
    """Modal: as últimas 200 alterações (ou as de um lançamento, com ?lancamento=id)."""
    registros = HistoricoLancamento.objects.filter(usuario=request.user)
    lancamento_id = consultas.inteiro(request.GET.get("lancamento"), 0, 1, 2 ** 62)
    if lancamento_id:
        registros = registros.filter(lancamento_id=lancamento_id)
    registros = list(registros[:200])
    atuais = Lancamento.objects.filter(usuario=request.user, id__in={h.lancamento_id for h in registros})
    atuais = {l.id: l for l in atuais}
    return render(request, "financeiro/_modal_historico.html", {"linhas": [
        {"h": h, "lancamento": atuais.get(h.lancamento_id), "campo": NOMES_CAMPOS.get(h.campo, h.campo),
         "antes": _legivel(h.campo, h.antes), "depois": _legivel(h.campo, h.depois)} for h in registros
    ]})
