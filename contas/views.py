"""Entrar, cadastrar, sair, perfil e exclusão da conta (LGPD: apaga todos os dados do usuário)."""
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, logout, update_session_auth_hash
from django.contrib.auth.forms import PasswordChangeForm
from django.core.cache import cache
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme, urlencode
from django.views.decorators.http import require_POST

from contas.forms import CadastroForm, EntrarForm, ExcluirContaForm, PerfilForm

# Contra força bruta, três contadores de falhas na janela de 15 min:
# - IP + e-mail: o caso comum (a pessoa errando a senha);
# - só o e-mail: ataque distribuído (muitos IPs na mesma conta);
# - só o IP: um IP testando muitas contas (senha comum em vários e-mails).
TENTATIVAS, TENTATIVAS_CONTA, TENTATIVAS_IP, JANELA_S = 8, 20, 30, 15 * 60
CADASTROS_POR_IP, JANELA_CADASTRO_S = 5, 60 * 60
MUITAS_TENTATIVAS = "Muitas tentativas. Espere 15 minutos e tente de novo."


def _ip(request) -> str:
    """O IP do cliente. Atrás do proxy (Caddy), o daphne --proxy-headers já põe o IP real em REMOTE_ADDR; ler o
    X-Forwarded-For aqui deixaria o cliente escolher o próprio IP e escapar do bloqueio."""
    return request.META.get("REMOTE_ADDR", "")


def _destino(request) -> str:
    proximo = request.POST.get("next") or request.GET.get("next")
    if proximo and url_has_allowed_host_and_scheme(proximo, {request.get_host()}, request.is_secure()):
        return proximo
    return settings.LOGIN_REDIRECT_URL


def _somar(chave: str, janela: int) -> None:
    cache.set(chave, cache.get(chave, 0) + 1, janela)


def entrar(request):
    if request.user.is_authenticated:
        return redirect(settings.LOGIN_REDIRECT_URL)
    form = EntrarForm(request.POST or None, request=request)
    bloqueado = False
    if request.method == "POST":
        ip, email = _ip(request), request.POST.get("email", "").strip().lower()
        chaves = {"par": f"login:{ip}:{email}", "conta": f"login-conta:{email}", "ip": f"login-ip:{ip}"}
        limites = {"par": TENTATIVAS, "conta": TENTATIVAS_CONTA, "ip": TENTATIVAS_IP}
        if any(cache.get(chaves[k], 0) >= limites[k] for k in chaves):
            bloqueado = True
            form.add_error(None, MUITAS_TENTATIVAS)
        elif form.is_valid():
            # O contador do IP não zera com um acerto: senão quem tem uma conta zeraria o bloqueio entrando nela.
            cache.delete_many([chaves["par"], chaves["conta"]])
            login(request, form.usuario)
            return redirect(_destino(request))
        else:
            for chave in chaves.values():
                _somar(chave, JANELA_S)
    return render(request, "contas/entrar.html", {"form": form, "bloqueado": bloqueado,
                                                  "next": request.GET.get("next", "")})


def entrar_admin(request, extra_context=None):
    """Login do /admin/: o mesmo do site (com o bloqueio contra força bruta), nunca o formulário próprio do admin.
    Quem já entrou e não é da equipe recebe 404: para essa pessoa o admin não existe."""
    if request.user.is_authenticated:
        if request.user.is_staff:
            return redirect("admin:index")
        raise Http404
    proximo = request.GET.get("next") or reverse("admin:index")
    return redirect(f"{reverse(settings.LOGIN_URL)}?{urlencode({'next': proximo})}")


def cadastro(request):
    if not settings.CADASTRO_ABERTO:
        raise Http404
    if request.user.is_authenticated:
        return redirect(settings.LOGIN_REDIRECT_URL)
    form = CadastroForm(request.POST or None)
    chave = f"cadastro-ip:{_ip(request)}"
    if request.method == "POST" and cache.get(chave, 0) >= CADASTROS_POR_IP:
        form.is_valid()  # mostra os erros dos campos junto com o aviso
        form.add_error(None, "Muitas contas criadas a partir desta rede. Tente de novo mais tarde.")
    elif request.method == "POST" and form.is_valid():
        _somar(chave, JANELA_CADASTRO_S)
        usuario = form.save()
        login(request, usuario, backend="django.contrib.auth.backends.ModelBackend")
        messages.success(request, f"Bem-vindo(a), {usuario.primeiro_nome}! Comece pelos lançamentos do mês ou "
                                  "conecte o Nota Paraná em Configurações.")
        return redirect(settings.LOGIN_REDIRECT_URL)
    return render(request, "contas/cadastro.html", {"form": form})


@require_POST
def sair(request):
    logout(request)
    return redirect(settings.LOGOUT_REDIRECT_URL)


def perfil(request):
    form = PerfilForm(request.POST or None, instance=request.user, prefix="perfil")
    senha = PasswordChangeForm(request.user, request.POST or None, prefix="senha")
    if request.method == "POST":
        if "salvar_perfil" in request.POST and form.is_valid():
            form.save()
            messages.success(request, "Perfil atualizado.")
            return redirect("contas:perfil")
        if "trocar_senha" in request.POST and senha.is_valid():
            senha.save()
            update_session_auth_hash(request, senha.user)
            messages.success(request, "Senha alterada.")
            return redirect("contas:perfil")
    for campo in senha.fields.values():
        campo.help_text = ""
    return render(request, "contas/perfil.html", {"form": form, "senha": senha,
                                                  "excluir": ExcluirContaForm(usuario=request.user)})


@require_POST
def excluir_conta(request):
    form = ExcluirContaForm(request.POST, usuario=request.user)
    if not form.is_valid():
        return render(request, "contas/perfil.html", {
            "form": PerfilForm(instance=request.user, prefix="perfil"),
            "senha": PasswordChangeForm(request.user, prefix="senha"), "excluir": form, "abrir_exclusao": True})
    usuario = request.user
    logout(request)
    usuario.delete()  # CASCADE: lançamentos, notas, faturas, credenciais, sessões, tarefas...
    messages.info(request, "Sua conta e todos os seus dados foram excluídos.")
    return redirect("contas:entrar")
