from django import forms
from django.contrib.auth import authenticate, password_validation

from contas.models import Usuario


class EntrarForm(forms.Form):
    email = forms.EmailField(label="E-mail", widget=forms.EmailInput(attrs={"autocomplete": "email", "autofocus": True}))
    senha = forms.CharField(label="Senha", widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}))

    def __init__(self, *args, request=None, **kwargs):
        self.request = request
        self.usuario = None
        super().__init__(*args, **kwargs)

    def clean(self):
        dados = super().clean()
        if self.errors:
            return dados
        self.usuario = authenticate(self.request, email=dados["email"].lower(), password=dados["senha"])
        if self.usuario is None:
            raise forms.ValidationError("E-mail ou senha incorretos.")
        return dados


class CadastroForm(forms.ModelForm):
    senha = forms.CharField(label="Senha", widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
                            help_text="Pelo menos 10 caracteres; evite senhas comuns.")
    confirmar = forms.CharField(label="Confirme a senha", widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))
    aceite = forms.BooleanField(label="Concordo que meus dados financeiros ficam guardados nesta conta, cifrados quando "
                                      "forem senhas, e que posso excluí-los a qualquer momento.")

    class Meta:
        model = Usuario
        fields = ["nome", "email"]
        widgets = {"nome": forms.TextInput(attrs={"autocomplete": "name", "autofocus": True}),
                   "email": forms.EmailInput(attrs={"autocomplete": "email"})}

    def clean_email(self):
        email = self.cleaned_data["email"].lower()
        if Usuario.objects.filter(email=email).exists():
            raise forms.ValidationError("Já existe uma conta com esse e-mail.")
        return email

    def clean(self):
        dados = super().clean()
        if dados.get("senha") and dados.get("senha") != dados.get("confirmar"):
            self.add_error("confirmar", "As senhas não conferem.")
        elif dados.get("senha"):
            try:
                password_validation.validate_password(dados["senha"], Usuario(email=dados.get("email", ""),
                                                                              nome=dados.get("nome", "")))
            except forms.ValidationError as erro:
                self.add_error("senha", erro)
        return dados

    def save(self, commit=True):
        usuario = super().save(commit=False)
        usuario.set_password(self.cleaned_data["senha"])
        if commit:
            usuario.save()
        return usuario


class PerfilForm(forms.ModelForm):
    # O e-mail é o login: trocar pede a senha, para uma sessão esquecida aberta não tomar a conta.
    senha_atual = forms.CharField(label="Senha atual", required=False, strip=False,
                                  widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}),
                                  help_text="Só para trocar o e-mail.")

    class Meta:
        model = Usuario
        fields = ["nome", "email", "atualizacao_automatica", "hora_atualizacao"]
        widgets = {"hora_atualizacao": forms.TimeInput(attrs={"type": "time"}, format="%H:%M")}
        help_texts = {"hora_atualizacao": "Horário (de Brasília) em que as notas e contas são buscadas sozinhas."}

    def clean_email(self):
        email = self.cleaned_data["email"].lower()
        if Usuario.objects.filter(email=email).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("Já existe uma conta com esse e-mail.")
        return email

    def clean(self):
        # Roda antes de o ModelForm copiar os campos para a instância: self.instance ainda tem o e-mail salvo.
        dados = super().clean()
        email = dados.get("email")
        if email and email != self.instance.email and not self.instance.check_password(dados.get("senha_atual") or ""):
            self.add_error("senha_atual", "Digite a sua senha atual para trocar o e-mail.")
        return dados


class ExcluirContaForm(forms.Form):
    senha = forms.CharField(label="Sua senha", widget=forms.PasswordInput)
    confirmacao = forms.CharField(label='Digite "EXCLUIR" para confirmar')

    def __init__(self, *args, usuario=None, **kwargs):
        self.usuario = usuario
        super().__init__(*args, **kwargs)

    def clean(self):
        dados = super().clean()
        if not self.usuario.check_password(dados.get("senha", "")):
            self.add_error("senha", "Senha incorreta.")
        if dados.get("confirmacao", "").strip().upper() != "EXCLUIR":
            self.add_error("confirmacao", 'Digite EXCLUIR.')
        return dados
