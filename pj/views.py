"""Horas e informes PJ: o informe do mês (cálculo ao vivo), a conferência com o Salário e a previsão até dezembro."""
import re
from decimal import Decimal

from django.contrib import messages
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from core.acesso import exige_pj
from core.formatos import brl, mes_de, mes_rotulo, somar_meses
from pj import servicos
from pj.models import Informe

HORAS_PADRAO, HORAS_PREVISAO = "180:00", 168


def _mes(texto: str | None, padrao: str) -> str:
    if texto and re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", texto):
        return texto
    return padrao


def _decimal_tela(valor) -> str:
    """Decimal/float para o campo de texto (vírgula decimal, sem milhar: '118,58')."""
    return "" if valor is None else f"{float(valor):.2f}".replace(".", ",")


def _previa(valores: dict, pct: float) -> Informe:
    """Informe só em memória com os valores do formulário: os cartões já chegam calculados (o Alpine recalcula).
    O que não der para ler conta como zero."""
    def ler(funcao, *args):
        try:
            return funcao(*args) or 0
        except ValueError:
            return 0
    return Informe(horas=ler(servicos.ler_hhmm, valores["horas"]), pct_sobreaviso=pct,
                   horas_sobreaviso=float(ler(servicos.numero, valores["horas_sobreaviso"], "", False)),
                   valor_hora=Decimal(ler(servicos.numero, valores["valor_hora"], "", False)),
                   desconto=Decimal(ler(servicos.numero, valores["plano"], "", False)))


def _url_mes(mes: str) -> str:
    return f"{reverse('pj:horas')}?mes={mes}"


@exige_pj
@require_http_methods(["GET", "POST"])
def horas(request):
    hoje = timezone.localdate()
    mes = _mes(request.POST.get("mes") or request.GET.get("mes"), mes_de(hoje))
    informe = Informe.objects.filter(usuario=request.user, mes=mes).first()
    base = servicos.base_previsao(request.user)
    erro_informe = erro_previsao = None

    if request.method == "POST" and request.POST.get("acao") == "prever":
        try:
            horas_mes = servicos.ler_hhmm(request.POST.get("horas_previsao", ""))
            valor_hora = servicos.numero(request.POST.get("valor_hora_previsao"), "o valor hora", positivo=True)
            plano = servicos.numero(request.POST.get("plano_previsao"), "o plano de saúde", obrigatorio=False)
        except ValueError as erro:
            erro_previsao = str(erro)
        else:
            r = servicos.prever_ate_dezembro(request.user, horas_mes, valor_hora, plano or Decimal("0"), hoje)
            if r["meses"]:
                partes = [f"Previsão gravada para {', '.join(mes_rotulo(m, curto=True) for m in r['meses'])}"]
                lancamentos = r["criados"] + r["atualizados"]
                partes.append(f"{lancamentos} lançamento(s) de Salário previsto(s)" if lancamentos
                              else "os lançamentos de Salário já estavam iguais")
                if r["mantidos"]:
                    partes.append("Salário já lançado mantido em " + ", ".join(mes_rotulo(m, curto=True)
                                                                               for m in r["mantidos"]))
                messages.success(request, "; ".join(partes) + ".")
            else:
                messages.info(request, "Todos os meses até dezembro já têm informe. Nada para prever.")
            return redirect(_url_mes(mes))
    elif request.method == "POST":
        try:
            informe = servicos.salvar_informe(
                request.user, mes, request.POST.get("horas", ""), request.POST.get("horas_sobreaviso"),
                request.POST.get("valor_hora"), request.POST.get("plano"), request.POST.get("valor_nf"))
        except ValueError as erro:
            erro_informe = str(erro)
        else:
            messages.success(request, f"Informe de {mes_rotulo(mes).lower()} salvo: valor da NF "
                                      f"{brl(servicos.valor_nf(informe))}.")
            return redirect(_url_mes(mes))

    # Valores do formulário: o que foi enviado (com erro), o informe do mês ou, num mês novo, os do último informe.
    if erro_informe:
        valores = {c: request.POST.get(c, "") for c in ("horas", "horas_sobreaviso", "valor_hora", "plano", "valor_nf")}
    elif informe:
        valores = {"horas": servicos.hhmm(informe.horas), "horas_sobreaviso": f"{informe.horas_sobreaviso:g}",
                   "valor_hora": _decimal_tela(informe.valor_hora), "plano": _decimal_tela(informe.desconto),
                   "valor_nf": _decimal_tela(informe.valor_nf)}
    else:
        valores = {"horas": HORAS_PADRAO, "horas_sobreaviso": "0",
                   "valor_hora": _decimal_tela(base.valor_hora) if base else "",
                   "plano": _decimal_tela(base.desconto) if base else "0.00", "valor_nf": ""}
    pct = informe.pct_sobreaviso if informe else (base.pct_sobreaviso if base else 1 / 3)

    reais = set(Informe.objects.filter(usuario=request.user, previsao=False).values_list("mes", flat=True))
    previsao = {
        "meses": [m for m in servicos.meses_previsao(hoje) if m not in reais],
        "horas": request.POST.get("horas_previsao", HORAS_PREVISAO) if erro_previsao else HORAS_PREVISAO,
        "valor_hora": (request.POST.get("valor_hora_previsao", "") if erro_previsao
                       else _decimal_tela(base.valor_hora) if base else ""),
        "plano": (request.POST.get("plano_previsao", "") if erro_previsao
                  else _decimal_tela(base.desconto) if base else "0.00"),
        "erro": erro_previsao,
    }
    return render(request, "pj/horas.html", {
        "mes": mes, "mes_anterior": somar_meses(mes, -1), "mes_seguinte": somar_meses(mes, 1),
        "informe": informe, "novo": informe is None, "valores": valores, "pct": pct, "erro_informe": erro_informe,
        "previa": _previa(valores, pct),
        "linhas": servicos.informes_x_salario(request.user), "previsao": previsao,
        "inicial": {"horas": valores["horas"], "sobreaviso": valores["horas_sobreaviso"],
                    "valor_hora": valores["valor_hora"], "plano": valores["plano"], "nf": valores["valor_nf"],
                    "pct": pct, "horas_previsao": str(previsao["horas"]),
                    "valor_hora_previsao": previsao["valor_hora"], "plano_previsao": previsao["plano"]},
    }, status=400 if erro_informe or erro_previsao else 200)
