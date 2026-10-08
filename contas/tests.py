from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from contas.models import Usuario
from financeiro.models import Banco, Categoria, Lancamento
from notas.models import RegraCategoria

SENHA = "senha-bem-forte-123"


class CadastroEntrarTest(TestCase):
    def setUp(self):
        cache.clear()

    def test_paginas_publicas_e_protegidas(self):
        self.assertEqual(self.client.get(reverse("contas:entrar")).status_code, 200)
        self.assertEqual(self.client.get(reverse("contas:cadastro")).status_code, 200)
        self.assertEqual(self.client.get("/saude/").status_code, 200)
        resposta = self.client.get(reverse("financeiro:lancamentos"))
        self.assertRedirects(resposta, f"{reverse('contas:entrar')}?next=/lancamentos/", fetch_redirect_response=False)

    def test_htmx_sem_login_recebe_redirect_do_htmx(self):
        resposta = self.client.get(reverse("integracoes:status"), HTTP_HX_REQUEST="true")
        self.assertEqual(resposta.status_code, 204)
        self.assertEqual(resposta["HX-Redirect"], reverse("contas:entrar"))

    def test_cadastro_cria_conta_com_padroes_e_entra(self):
        resposta = self.client.post(reverse("contas:cadastro"), {
            "nome": "Ana Souza", "email": "Ana@Exemplo.com", "senha": SENHA, "confirmar": SENHA, "aceite": "on"})
        self.assertRedirects(resposta, reverse("financeiro:resumo"), fetch_redirect_response=False)
        usuario = Usuario.objects.get(email="ana@exemplo.com")
        self.assertFalse(usuario.pj_habilitado)
        self.assertFalse(usuario.is_staff)
        self.assertTrue(Categoria.objects.filter(usuario=usuario, nome="Luz").exists())
        self.assertTrue(Banco.objects.filter(usuario=usuario).exists())
        self.assertTrue(RegraCategoria.objects.filter(usuario=usuario, padrao="SUPERMERCADO").exists())
        self.assertEqual(self.client.get(reverse("contas:perfil")).status_code, 200)

    def test_cadastro_recusa_senha_fraca_email_repetido_e_sem_aceite(self):
        Usuario.objects.create_user("ja@existe.com", SENHA, nome="Já")
        resposta = self.client.post(reverse("contas:cadastro"), {
            "nome": "X", "email": "ja@existe.com", "senha": "123", "confirmar": "123"})
        form = resposta.context["form"]
        self.assertIn("email", form.errors)
        self.assertIn("senha", form.errors)
        self.assertIn("aceite", form.errors)

    @override_settings(CADASTRO_ABERTO=False)
    def test_cadastro_fechado(self):
        self.assertEqual(self.client.get(reverse("contas:cadastro")).status_code, 404)

    def test_entrar_e_sair(self):
        Usuario.objects.create_user("b@exemplo.com", SENHA, nome="Bia")
        resposta = self.client.post(reverse("contas:entrar"), {"email": "B@exemplo.com", "senha": SENHA})
        self.assertRedirects(resposta, reverse("financeiro:resumo"), fetch_redirect_response=False)
        self.client.post(reverse("contas:sair"))
        self.assertEqual(self.client.get(reverse("financeiro:lancamentos")).status_code, 302)

    def test_entrar_respeita_next_apenas_local(self):
        Usuario.objects.create_user("c@exemplo.com", SENHA, nome="Caio")
        resposta = self.client.post(reverse("contas:entrar") + "?next=https://malicioso.com/",
                                    {"email": "c@exemplo.com", "senha": SENHA, "next": "https://malicioso.com/"})
        self.assertRedirects(resposta, reverse("financeiro:resumo"), fetch_redirect_response=False)

    def test_bloqueio_apos_muitas_tentativas(self):
        Usuario.objects.create_user("d@exemplo.com", SENHA, nome="Duda")
        for _ in range(8):
            self.client.post(reverse("contas:entrar"), {"email": "d@exemplo.com", "senha": "errada-errada"})
        resposta = self.client.post(reverse("contas:entrar"), {"email": "d@exemplo.com", "senha": SENHA})
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "Muitas tentativas")


class PerfilTest(TestCase):
    def setUp(self):
        self.usuario = Usuario.objects.create_user("e@exemplo.com", SENHA, nome="Edu")
        self.client.force_login(self.usuario)

    def test_menu_pj_so_para_quem_tem_o_modulo(self):
        self.assertNotContains(self.client.get(reverse("contas:perfil")), "Horas e informes")
        self.assertEqual(self.client.get(reverse("pj:horas")).status_code, 404)
        self.usuario.pj_habilitado = True
        self.usuario.save()
        self.assertContains(self.client.get(reverse("contas:perfil")), "Horas e informes")

    def test_trocar_senha_mantem_sessao(self):
        nova = "outra-senha-forte-456"
        self.client.post(reverse("contas:perfil"), {
            "trocar_senha": "1", "senha-old_password": SENHA, "senha-new_password1": nova, "senha-new_password2": nova})
        self.usuario.refresh_from_db()
        self.assertTrue(self.usuario.check_password(nova))
        self.assertEqual(self.client.get(reverse("contas:perfil")).status_code, 200)

    def test_excluir_conta_apaga_tudo(self):
        Lancamento.objects.create(usuario=self.usuario, data="2026-01-05", movimentacao="Saída", categoria="Luz",
                                  valor=10)
        resposta = self.client.post(reverse("contas:excluir_conta"), {"senha": SENHA, "confirmacao": "excluir"})
        self.assertRedirects(resposta, reverse("contas:entrar"), fetch_redirect_response=False)
        self.assertFalse(Usuario.objects.filter(email="e@exemplo.com").exists())
        self.assertFalse(Lancamento.objects.exists())

    def test_excluir_conta_exige_senha(self):
        self.client.post(reverse("contas:excluir_conta"), {"senha": "errada", "confirmacao": "EXCLUIR"})
        self.assertTrue(Usuario.objects.filter(email="e@exemplo.com").exists())
