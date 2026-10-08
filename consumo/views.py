"""Água (Sanepar) e Luz (Copel): faturas, gráficos, conferência com os lançamentos, envio e download do PDF."""
import json

from django.contrib import messages
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from consumo import consultas, servicos
from consumo.models import FaturaCopel, FaturaSanepar
from core.formatos import brl, mes_rotulo

TAMANHO_MAXIMO_PDF = 10 * 1024 * 1024
TIPOS_PDF = ("application/pdf",)


# ---------- páginas ------------------------------------------------------------------------------------

@require_GET
def agua(request):
    return render(request, "consumo/agua.html", consultas.pagina_agua(request.user))


@require_GET
def luz(request):
    contexto = consultas.pagina_luz(request.user)
    proxima = servicos.proxima_conta_copel(request.user)
    if proxima:
        referencia, data = proxima
        importada = FaturaCopel.objects.filter(usuario=request.user, referencia=referencia).exists()
        atrasada = data <= timezone.localdate() and not importada
        contexto["proxima"] = {"referencia": referencia, "data": data, "atrasada": atrasada}
        if not atrasada:
            contexto["extra"] = f"Próxima conta em {data:%d/%m/%Y}, no dia da leitura"
    return render(request, "consumo/luz.html", contexto)


# ---------- ajustar lançamentos pelas faturas ----------------------------------------------------------

def _resumo_ajuste(mudancas: list[dict], categoria: str) -> str:
    feitas = [m for m in mudancas if m["acao"] in ("criado", "atualizado")]
    ignoradas = [m for m in mudancas if m["acao"].startswith("ignorado")]
    texto = (f"{len(feitas)} lançamento(s) de {categoria} ajustado(s)" if feitas
             else "Tudo já estava igual às faturas")
    if ignoradas:
        meses = ", ".join(mes_rotulo(m["mes"], curto=True) for m in ignoradas)
        texto += f". Não mexi em {meses}: há mais de um lançamento de {categoria} no mês"
    return texto + "."


def _ajustar(request, conciliar, categoria: str, tabela, template: str, destino: str):
    texto = _resumo_ajuste(conciliar(request.user), categoria)
    if not request.headers.get("HX-Request"):
        messages.success(request, texto)
        return redirect(destino)
    resposta = render(request, template, tabela(request.user))
    resposta["HX-Trigger"] = json.dumps({"toast": texto})
    return resposta


@require_POST
def agua_ajustar(request):
    return _ajustar(request, servicos.conciliar_agua, "Água", consultas.tabela_agua, "consumo/_faturas_agua.html",
                    "consumo:agua")


@require_POST
def luz_ajustar(request):
    return _ajustar(request, servicos.conciliar_luz, "Luz", consultas.tabela_luz, "consumo/_faturas_luz.html",
                    "consumo:luz")


# ---------- PDF: baixar (só o dono) e enviar -----------------------------------------------------------

def _baixar(fatura, nome: str):
    if not fatura.pdf:
        raise Http404
    try:
        arquivo = fatura.pdf.open("rb")
    except FileNotFoundError:
        raise Http404
    return FileResponse(arquivo, as_attachment=True, filename=nome, content_type="application/pdf")


@require_GET
def agua_pdf(request, pk: int):
    fatura = get_object_or_404(FaturaSanepar, pk=pk, usuario=request.user)
    return _baixar(fatura, f"sanepar-{fatura.referencia}.pdf")


@require_GET
def luz_pdf(request, pk: int):
    fatura = get_object_or_404(FaturaCopel, pk=pk, usuario=request.user)
    return _baixar(fatura, f"copel-{fatura.referencia}-{fatura.numero_fatura}.pdf")


def _pdf_enviado(request) -> bytes:
    arquivo = request.FILES.get("pdf")
    if not arquivo:
        raise servicos.PdfInvalido("Escolha o PDF da conta.")
    if arquivo.size > TAMANHO_MAXIMO_PDF:
        raise servicos.PdfInvalido("O arquivo passa de 10 MB. A conta em PDF costuma ter menos de 1 MB.")
    if arquivo.content_type not in TIPOS_PDF or not arquivo.name.lower().endswith(".pdf"):
        raise servicos.PdfInvalido("Envie um arquivo PDF.")
    return arquivo.read()


def _resumo_importacao(mudancas: list[dict], categoria: str) -> str:
    feitas = [m for m in mudancas if m["acao"] in ("criado", "atualizado")]
    if not feitas:
        return f"Nenhum lançamento de {categoria} precisou de ajuste."
    meses = ", ".join(mes_rotulo(m["mes"]).lower() for m in feitas)
    return f"Lançamento de {categoria} ajustado: {meses}."


def _enviar(request, servico: str, importar, categoria: str, destino: str):
    nomes = {"sanepar": "Sanepar", "copel": "Copel"}
    if request.method == "GET":
        return render(request, "consumo/_enviar_pdf.html", {
            "servico": servico, "nome": nomes[servico], "acao": request.path,
            "conta": "água" if servico == "sanepar" else "luz"})
    try:
        fatura, mudancas = importar(request.user, _pdf_enviado(request))
    except servicos.PdfInvalido as erro:
        messages.error(request, str(erro))
        return redirect(destino)
    consumo = (f"{fatura.consumo_m3} m³" if servico == "sanepar" and fatura.consumo_m3 is not None
               else f"{fatura.consumo_kwh:g} kWh" if servico == "copel" and fatura.consumo_kwh is not None else "")
    messages.success(request, f"Conta {fatura.referencia[5:]}/{fatura.referencia[:4]} da {nomes[servico]} importada: "
                              f"{brl(fatura.valor_total)}{' · ' + consumo if consumo else ''}. "
                              + _resumo_importacao(mudancas, categoria))
    return redirect(destino)


@require_http_methods(["GET", "POST"])
def agua_enviar(request):
    return _enviar(request, "sanepar", servicos.importar_pdf_sanepar, "Água", "consumo:agua")


@require_http_methods(["GET", "POST"])
def luz_enviar(request):
    return _enviar(request, "copel", servicos.importar_pdf_copel, "Luz", "consumo:luz")
