"""Configurações (credenciais, conexões, histórico), o status do menu lateral e os pedidos de sincronização.

Senha e CPF nunca voltam para a tela: os campos vêm sempre vazios e o login aparece só mascarado.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta

from django import forms
from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from consumo.servicos import proxima_conta_copel
from core.formatos import MESES
from integracoes import fila
from integracoes.models import COPEL, NOMES_SERVICO, NOTAPARANA, SANEPAR, SERVICOS, Credencial, SessaoServico, Tarefa

ORDEM = [s for s, _ in SERVICOS]
ICONES = {NOTAPARANA: "receipt_long", SANEPAR: "water_drop", COPEL: "bolt"}
EXPLICACOES = {
    NOTAPARANA: "Entra sozinho com CPF e senha (o site não tem captcha). O Nota Paraná aceita uma sessão por vez: "
                "sincronizar encerra a sua sessão aberta no navegador.",
    SANEPAR: "O login tem captcha e código por e-mail/SMS: resolva uma vez em Conectar. Depois a sessão salva é "
             "usada todo dia, sem você, até expirar.",
    COPEL: "O login pede o reCAPTCHA toda vez e a sessão dura poucos minutos. A conta é mensal: use Conectar "
           "quando a conta nova sair; as faturas são baixadas logo em seguida.",
}
ROTULO_LOGIN = {NOTAPARANA: "CPF", SANEPAR: "CPF", COPEL: "CPF do titular"}
ROTULO_ORIGEM = {"manual": "Manual", "agenda": "Automática", "login": "Após conectar", "sistema antigo": "Sistema antigo"}
COR_STATUS = {Tarefa.PENDENTE: "azul", Tarefa.RODANDO: "laranja", Tarefa.OK: "verde", Tarefa.ERRO: "vermelho",
              Tarefa.CANCELADA: "cinza"}
BATIMENTO_VELHO = timedelta(minutes=2)


# ---------- estado de cada serviço ----------

def quando(momento) -> str:
    if not momento:
        return ""
    local = timezone.localtime(momento)
    hoje = timezone.localdate()
    if local.date() == hoje:
        return f"hoje {local:%H:%M}"
    if local.date() == hoje - timedelta(days=1):
        return f"ontem {local:%H:%M}"
    return f"{local:%d/%m %H:%M}"


def contagem(tarefa: Tarefa | None, chave: str = "novas") -> int | None:
    """Os registros importados do sistema antigo usam notas_novas/notas_atualizadas."""
    if tarefa is None or not tarefa.resultado:
        return None
    valor = tarefa.resultado.get(chave, tarefa.resultado.get(f"notas_{chave}"))
    return int(valor) if isinstance(valor, (int, float)) else None


def _proxima_copel(usuario) -> dict | None:
    proxima = proxima_conta_copel(usuario)
    if not proxima:
        return None
    ref, data = proxima
    return {"referencia": ref, "data": data, "mes": MESES[int(ref[5:]) - 1].lower(),
            "saiu": data <= timezone.localdate()}


def estado_servico(usuario, servico: str, credencial=None, sessao=None, ativa=None, ultima=None,
                   buscar: bool = True) -> dict:
    if buscar:
        credencial = Credencial.objects.filter(usuario=usuario, servico=servico).first()
        sessao = SessaoServico.objects.filter(usuario=usuario, servico=servico).first()
        ativa = fila.tarefa_ativa(usuario, servico)
        ultima = fila.ultima_concluida(usuario, servico)
    valida = fila.sessao_valida(sessao, servico)
    if credencial is None:
        situacao, rotulo, cor = "sem_credencial", "Sem credencial", "cinza"
    elif servico == NOTAPARANA:
        situacao, rotulo, cor = (("conectado", "Conectado", "verde") if credencial.ativo
                                 else ("recusada", "Senha recusada", "vermelho"))
    elif sessao is None:
        situacao, rotulo, cor = "falta_conectar", "Falta conectar", "laranja"
    elif valida:
        situacao, rotulo, cor = "conectado", "Conectado", "verde"
    else:
        situacao, rotulo, cor = "expirada", "Sessão expirada", "laranja" if servico == COPEL else "vermelho"
    pode_sincronizar = (credencial is not None if servico == NOTAPARANA else valida) and ativa is None
    estado = dict(
        servico=servico, nome=NOMES_SERVICO[servico], icone=ICONES[servico], explicacao=EXPLICACOES[servico],
        rotulo_login=ROTULO_LOGIN[servico], credencial=credencial,
        login_mascarado=credencial.login_mascarado if credencial else "",
        sessao=sessao, sessao_valida=valida, situacao=situacao, situacao_rotulo=rotulo, situacao_cor=cor,
        ativa=ativa, ultima=ultima, ultima_quando=quando(ultima.terminada_em or ultima.criada_em) if ultima else "",
        novas=contagem(ultima), atualizadas=contagem(ultima, "atualizadas"), erros=contagem(ultima, "erros"),
        pode_sincronizar=pode_sincronizar, conectar_url=f"/integracoes/{servico}/conectar/",
        tem_janela=servico in (SANEPAR, COPEL),
    )
    if servico == COPEL:
        estado["proxima"] = _proxima_copel(usuario)
    return estado


def estados(usuario) -> list[dict]:
    credenciais = {c.servico: c for c in Credencial.objects.filter(usuario=usuario)}
    sessoes = {s.servico: s for s in SessaoServico.objects.filter(usuario=usuario)}
    ativas = {t.servico: t for t in Tarefa.objects.filter(usuario=usuario, status__in=fila.ATIVAS)}
    return [estado_servico(usuario, s, credenciais.get(s), sessoes.get(s), ativas.get(s),
                           fila.ultima_concluida(usuario, s), buscar=False) for s in ORDEM]


def trabalhador_parado() -> bool:
    """Há tarefa esperando há mais de 3 min e o trabalhador não dá sinal de vida."""
    antiga = Tarefa.objects.filter(status=Tarefa.PENDENTE, criada_em__lt=timezone.now() - timedelta(minutes=3))
    if not antiga.exists() or Tarefa.objects.filter(status=Tarefa.RODANDO).exists():
        return False
    visto = fila.ler_batimento().get("visto_em")
    try:
        return not visto or timezone.now() - datetime.fromisoformat(visto) > BATIMENTO_VELHO
    except ValueError:
        return True


# ---------- formulário de credencial ----------

def _cpf_valido(cpf: str) -> bool:
    if len(cpf) != 11 or cpf == cpf[0] * 11:
        return False
    for n in (9, 10):
        soma = sum(int(cpf[i]) * (n + 1 - i) for i in range(n))
        if (soma * 10 % 11) % 10 != int(cpf[n]):
            return False
    return True


class CredencialForm(forms.Form):
    login = forms.CharField(required=False, max_length=30)
    senha = forms.CharField(required=False, max_length=200, strip=False)

    def __init__(self, *args, existente: Credencial | None = None, **kwargs):
        self.existente = existente
        super().__init__(*args, **kwargs)

    def clean_login(self):
        login = re.sub(r"\D", "", self.cleaned_data.get("login") or "")
        if not login:
            if self.existente is None:
                raise forms.ValidationError("Informe o CPF.")
            return ""
        if len(login) == 14:  # CNPJ (conta de empresa)
            return login
        if not _cpf_valido(login):
            raise forms.ValidationError("CPF inválido.")
        return login

    def clean_senha(self):
        senha = self.cleaned_data.get("senha") or ""
        if not senha and self.existente is None:
            raise forms.ValidationError("Informe a senha.")
        return senha

    def salvar(self, usuario, servico: str) -> tuple[Credencial, bool]:
        """Devolve (credencial, trocou_login). Trocar o login descarta a sessão salva da conta antiga."""
        obj = self.existente or Credencial(usuario=usuario, servico=servico)
        trocou = False
        if self.cleaned_data["login"]:
            trocou = self.existente is not None and self.existente.login != self.cleaned_data["login"]
            obj.login = self.cleaned_data["login"]
        if self.cleaned_data["senha"]:
            obj.senha = self.cleaned_data["senha"]
        obj.ativo = True
        obj.save()
        if trocou:
            SessaoServico.objects.filter(usuario=usuario, servico=servico).delete()
        return obj, trocou


# ---------- páginas ----------

def _servico_valido(servico: str) -> str:
    if servico not in NOMES_SERVICO:
        raise Http404
    return servico


@require_http_methods(["GET", "POST"])
def configuracoes(request):
    erros_form: dict[str, CredencialForm] = {}
    if request.method == "POST":
        servico = _servico_valido(request.POST.get("servico", ""))
        nome = NOMES_SERVICO[servico]
        existente = Credencial.objects.filter(usuario=request.user, servico=servico).first()
        if request.POST.get("acao") == "remover":
            Credencial.objects.filter(usuario=request.user, servico=servico).delete()
            SessaoServico.objects.filter(usuario=request.user, servico=servico).delete()
            Tarefa.objects.filter(usuario=request.user, servico=servico, status=Tarefa.PENDENTE).update(
                status=Tarefa.CANCELADA, terminada_em=timezone.now(), mensagem="Credencial removida.")
            messages.success(request, f"Credencial e sessão do {nome} removidas.")
            return redirect("integracoes:configuracoes")
        form = CredencialForm(request.POST, existente=existente)
        if form.is_valid():
            if existente and not form.cleaned_data["login"] and not form.cleaned_data["senha"]:
                messages.info(request, f"Nada mudou no {nome}.")
            else:
                _, trocou = form.salvar(request.user, servico)
                aviso = " A sessão da conta antiga foi descartada." if trocou else ""
                proximo = ("Use Sincronizar agora para buscar as notas." if servico == NOTAPARANA
                           else "Agora use Conectar para resolver o captcha.")
                messages.success(request, f"Credenciais do {nome} salvas, cifradas.{aviso} {proximo}")
            return redirect("integracoes:configuracoes")
        erros_form[servico] = form

    usuario = request.user
    servicos = estados(usuario)
    for s in servicos:
        s["form"] = erros_form.get(s["servico"])
    tarefas = Tarefa.objects.filter(usuario=usuario).order_by("-criada_em")[:30]
    return render(request, "integracoes/configuracoes.html", {
        "servicos": servicos, "tarefas": _linhas_historico(tarefas), "tem_ativa": any(s["ativa"] for s in servicos),
        "parado": trabalhador_parado(),
    })


def _linhas_historico(tarefas) -> list[dict]:
    linhas = []
    for t in tarefas:
        duracao = ""
        if t.iniciada_em and t.terminada_em:
            segundos = int((t.terminada_em - t.iniciada_em).total_seconds())
            duracao = f"{segundos // 60} min {segundos % 60:02d} s" if segundos >= 60 else f"{segundos} s"
        linhas.append(dict(tarefa=t, quando=quando(t.criada_em), cor=COR_STATUS.get(t.status, "cinza"),
                           origem=ROTULO_ORIGEM.get(t.origem, t.origem), duracao=duracao,
                           novas=contagem(t), atualizadas=contagem(t, "atualizadas"), erros=contagem(t, "erros")))
    return linhas


@require_GET
def servico(request, servico):
    """Parte de cima do cartão de um serviço (situação, última sincronização, botões); recarrega sozinha."""
    estado = estado_servico(request.user, _servico_valido(servico))
    return render(request, "integracoes/_servico.html", {"s": estado})


@require_GET
def historico(request):
    tarefas = Tarefa.objects.filter(usuario=request.user).order_by("-criada_em")[:30]
    ativa = Tarefa.objects.filter(usuario=request.user, status__in=fila.ATIVAS).exists()
    return render(request, "integracoes/_historico.html", {"tarefas": _linhas_historico(tarefas), "tem_ativa": ativa})


@require_GET
def tarefa(request, pk):
    obj = get_object_or_404(Tarefa, pk=pk, usuario=request.user)
    return render(request, "integracoes/_tarefa.html", {"t": obj, "quando": quando(obj.criada_em),
                                                        "cor": COR_STATUS.get(obj.status, "cinza"),
                                                        "origem": ROTULO_ORIGEM.get(obj.origem, obj.origem)})


# ---------- menu lateral ----------

def _linha_status(s: dict) -> dict | None:
    """Uma linha curta por serviço: (classe do ponto, texto, título com o detalhe)."""
    nome, ativa, ultima = s["nome"], s["ativa"], s["ultima"]
    if ativa:
        return {"ponto": "rodando", "texto": f"{nome} · {'atualizando…' if ativa.status == Tarefa.RODANDO else 'na fila'}",
                "titulo": ativa.progresso}
    if s["servico"] == COPEL:  # sessão de minutos: o que importa é a data da próxima conta
        p = s.get("proxima")
        if p and p["saiu"] and s["credencial"]:
            return {"ponto": "erro", "texto": f"Copel · conta de {p['mes']} saiu ({p['data']:%d/%m})",
                    "titulo": "Use Conectar em Configurações para baixar a conta nova.", "link": True}
        if ultima is not None and ultima.status == Tarefa.ERRO and "expirou" not in ultima.mensagem:
            return {"ponto": "erro", "texto": f"Copel · falhou {s['ultima_quando']}", "titulo": ultima.mensagem,
                    "link": True}
        if p:
            return {"ponto": "ok" if ultima is not None and ultima.status == Tarefa.OK else "",
                    "texto": f"Copel · próxima conta {p['data']:%d/%m}", "titulo": ultima.mensagem if ultima else ""}
        if s["credencial"] is None:
            return None
        return {"ponto": "", "texto": "Copel · conecte quando a conta sair", "titulo": "", "link": True}
    if s["situacao"] == "recusada":
        return {"ponto": "erro", "texto": f"{nome} · senha recusada", "titulo": "Atualize a senha em Configurações.",
                "link": True}
    if s["situacao"] == "expirada" and s["servico"] == SANEPAR:
        return {"ponto": "erro", "texto": "Sanepar · sessão expirou", "titulo": "Use Conectar em Configurações.",
                "link": True}
    if s["situacao"] == "falta_conectar":
        return {"ponto": "", "texto": f"{nome} · falta conectar", "titulo": "Use Conectar em Configurações.",
                "link": True}
    if ultima is None:
        if s["credencial"] is None:
            return None
        return {"ponto": "", "texto": f"{nome} · ainda não sincronizado", "titulo": ""}
    if ultima.status == Tarefa.ERRO:
        return {"ponto": "erro", "texto": f"{nome} · falhou {s['ultima_quando']}", "titulo": ultima.mensagem,
                "link": True}
    texto = f"{nome} · {s['ultima_quando']}"
    if s["servico"] == NOTAPARANA and s["novas"] is not None:
        texto += f" · {s['novas']} nova{'s' if s['novas'] != 1 else ''}"
    return {"ponto": "ok", "texto": texto, "titulo": ultima.mensagem}


@require_GET
def status(request):
    servicos = estados(request.user)
    linhas = [l for l in map(_linha_status, servicos) if l]
    automaticos = [s for s in servicos if s["servico"] in (NOTAPARANA, SANEPAR)]
    ocupado = any(s["ativa"] for s in automaticos)
    disponivel = any(s["pode_sincronizar"] for s in automaticos)
    return render(request, "integracoes/_status.html", {"linhas": linhas, "ocupado": ocupado,
                                                        "disponivel": disponivel or ocupado})


# ---------- pedidos de sincronização ----------

def _responder(request, mensagem: str, erro: bool = False, mudou: bool = True) -> HttpResponse:
    if not request.headers.get("HX-Request"):
        (messages.error if erro else messages.success)(request, mensagem)
        return redirect("integracoes:configuracoes")
    resposta = HttpResponse("")
    gatilhos = {"toast": {"mensagem": mensagem, "tipo": "erro" if erro else ""}}
    if mudou:
        gatilhos["sincronizou"] = True
    resposta["HX-Trigger"] = json.dumps(gatilhos)
    return resposta


def _impedimento(usuario, servico: str, tipo: str) -> str | None:
    """Por que não dá para sincronizar agora (ou None)."""
    nome = NOMES_SERVICO[servico]
    if servico == NOTAPARANA:
        if tipo == fila.SINCRONIZAR and not Credencial.objects.filter(usuario=usuario, servico=servico).exists():
            return "Cadastre o CPF e a senha do Nota Paraná primeiro."
        return None
    sessao = SessaoServico.objects.filter(usuario=usuario, servico=servico).first()
    if fila.sessao_valida(sessao, servico):
        return None
    if servico == COPEL:
        return "A sessão da Copel dura poucos minutos: use Conectar (resolver captcha) para baixar as contas."
    if sessao is None:
        return f"Conecte a {nome} primeiro: use Conectar (resolver captcha)."
    return f"Sessão da {nome} expirou: use Conectar (resolver captcha)."


@require_POST
def sincronizar(request, servico):
    servico = _servico_valido(servico)
    tipo = request.POST.get("tipo") or fila.SINCRONIZAR
    if tipo not in (fila.SINCRONIZAR, fila.REPROCESSAR) or (tipo == fila.REPROCESSAR and servico != NOTAPARANA):
        raise Http404
    nome = NOMES_SERVICO[servico]
    problema = _impedimento(request.user, servico, tipo)
    if problema:
        return _responder(request, problema, erro=True, mudou=False)
    tarefa = fila.enfileirar(request.user, servico, origem="manual", tipo=tipo)
    if not tarefa.nova:
        return _responder(request, f"Já existe uma sincronização do {nome} {tarefa.get_status_display().lower()}.")
    texto = "Releitura das notas salvas na fila." if tipo == fila.REPROCESSAR else f"Sincronização do {nome} na fila."
    return _responder(request, texto)


@require_POST
def atualizar(request):
    """O "Atualizar agora" do menu: Nota Paraná e, se a sessão estiver valendo, Sanepar."""
    criadas, ja = [], []
    for servico in (NOTAPARANA, SANEPAR):
        if _impedimento(request.user, servico, fila.SINCRONIZAR):
            continue
        tarefa = fila.enfileirar(request.user, servico, origem="manual")
        (criadas if tarefa.nova else ja).append(NOMES_SERVICO[servico])
    if criadas:
        return _responder(request, "Atualizando " + " e ".join(criadas) + ".")
    if ja:
        return _responder(request, "A atualização já está em andamento.")
    return _responder(request, "Nada para atualizar: conecte o Nota Paraná em Configurações.", erro=True, mudou=False)
