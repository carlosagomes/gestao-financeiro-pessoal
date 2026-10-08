"""Integrações: credenciais cifradas, Configurações, status do menu, fila, agenda, trabalhador e leitores."""
import base64
import json
import shutil
import tempfile
from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from consumo.models import FaturaCopel
from contas.models import Usuario
from integracoes import coletores, fila
from integracoes.coletores import copel, notaparana, sanepar
from integracoes.coletores.base import ErroColeta, Registro
from integracoes.models import COPEL, NOTAPARANA, SANEPAR, Credencial, SessaoServico, Tarefa
from notas.models import Nota

CPF, CPF_FORMATADO, SENHA = "52998224725", "529.982.247-25", "Segredo!Muito#Forte9"
LEGADO = Path(settings.BASE_DIR) / "testes_dados"  # exemplos reais, fora do git
NOTAS_HTML = LEGADO / "notas_html"
RECON = LEGADO / "recon"
SP = ZoneInfo("America/Sao_Paulo")
HX = {"HTTP_HX_REQUEST": "true"}


def criar_usuario(email="a@teste.local", **extra):
    return Usuario.objects.create_user(email=email, password="senha-forte-123", nome=email.split("@")[0], **extra)


def credencial(usuario, servico=NOTAPARANA, login=CPF, senha=SENHA, ativo=True):
    obj = Credencial(usuario=usuario, servico=servico, ativo=ativo)
    obj.login, obj.senha = login, senha
    obj.save()
    return obj


def sessao(usuario, servico=SANEPAR, expirada=False):
    obj = SessaoServico(usuario=usuario, servico=servico, expirada=expirada)
    obj.estado = {"cookies": [{"name": "s", "value": "x"}], "origins": []}
    obj.save()
    return obj


class MidiaTemporaria(TestCase):
    """Arquivos (HTML das notas, batimento do trabalhador) numa pasta temporária."""

    def setUp(self):
        super().setUp()
        self.pasta = Path(tempfile.mkdtemp())
        self.ajuste = override_settings(MEDIA_ROOT=self.pasta / "arquivos")
        self.ajuste.enable()

    def tearDown(self):
        self.ajuste.disable()
        shutil.rmtree(self.pasta, ignore_errors=True)
        super().tearDown()


class CredencialTests(TestCase):
    def test_cifra_e_decifra_sem_texto_puro_no_banco(self):
        u = criar_usuario()
        credencial(u)
        bruto = Credencial.objects.filter(usuario=u).values("login_cifrado", "senha_cifrada").get()
        self.assertNotIn(CPF, bruto["login_cifrado"])
        self.assertNotIn(SENHA, bruto["senha_cifrada"])
        obj = Credencial.objects.get(usuario=u)
        self.assertEqual(obj.login, CPF)
        self.assertEqual(obj.senha, SENHA)
        self.assertEqual(obj.login_mascarado, "529•••••25")

    def test_sessao_cifrada(self):
        u = criar_usuario()
        s = sessao(u)
        self.assertNotIn("cookies", SessaoServico.objects.get(pk=s.pk).estado_cifrado)
        self.assertEqual(SessaoServico.objects.get(pk=s.pk).estado["cookies"][0]["value"], "x")


class ConfiguracoesTests(TestCase):
    def setUp(self):
        self.u = criar_usuario()
        self.client.force_login(self.u)
        self.url = reverse("integracoes:configuracoes")

    def test_exige_login(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_pagina(self):
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        for texto in ("Nota Paraná", "Sanepar", "Copel", "/integracoes/sanepar/conectar/", "/integracoes/copel/conectar/",
                      "Histórico de sincronizações", "Atualização automática", reverse("contas:perfil")):
            self.assertContains(r, texto)
        self.assertNotContains(r, "/integracoes/notaparana/conectar/")

    def test_salvar_e_nunca_mostrar_cpf_nem_senha(self):
        r = self.client.post(self.url, {"servico": NOTAPARANA, "acao": "salvar", "login": CPF_FORMATADO, "senha": SENHA},
                             follow=True)
        self.assertContains(r, "salvas")
        obj = Credencial.objects.get(usuario=self.u, servico=NOTAPARANA)
        self.assertEqual((obj.login, obj.senha), (CPF, SENHA))
        for resposta in (r, self.client.get(self.url), self.client.get(reverse("integracoes:status")),
                         self.client.get(reverse("integracoes:servico", args=[NOTAPARANA]))):
            conteudo = resposta.content.decode()
            for segredo in (CPF, CPF_FORMATADO, SENHA, obj.login_cifrado, obj.senha_cifrada):
                self.assertNotIn(segredo, conteudo)
        self.assertContains(self.client.get(self.url), "529•••••25")

    def test_erro_de_validacao_nao_devolve_o_que_foi_digitado(self):
        r = self.client.post(self.url, {"servico": SANEPAR, "acao": "salvar", "login": "12345678900", "senha": SENHA})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "CPF inválido")
        self.assertNotContains(r, SENHA)
        self.assertNotContains(r, "12345678900")
        self.assertFalse(Credencial.objects.exists())

    def test_nova_credencial_exige_cpf_e_senha(self):
        r = self.client.post(self.url, {"servico": COPEL, "acao": "salvar", "login": "", "senha": ""})
        self.assertContains(r, "Informe o CPF")
        self.assertContains(r, "Informe a senha")

    def test_em_branco_mantem_o_atual(self):
        credencial(self.u)
        self.client.post(self.url, {"servico": NOTAPARANA, "acao": "salvar", "login": "", "senha": "nova-senha"})
        obj = Credencial.objects.get(usuario=self.u)
        self.assertEqual((obj.login, obj.senha), (CPF, "nova-senha"))
        r = self.client.post(self.url, {"servico": NOTAPARANA, "acao": "salvar", "login": "", "senha": ""}, follow=True)
        self.assertContains(r, "Nada mudou")

    def test_trocar_login_descarta_sessao_e_reativa(self):
        credencial(self.u, SANEPAR, ativo=False)
        sessao(self.u, SANEPAR)
        self.client.post(self.url, {"servico": SANEPAR, "acao": "salvar", "login": "11144477735", "senha": ""})
        obj = Credencial.objects.get(usuario=self.u, servico=SANEPAR)
        self.assertEqual(obj.login, "11144477735")
        self.assertTrue(obj.ativo)
        self.assertFalse(SessaoServico.objects.filter(usuario=self.u, servico=SANEPAR).exists())

    def test_remover(self):
        credencial(self.u, SANEPAR)
        sessao(self.u, SANEPAR)
        pendente = Tarefa.objects.create(usuario=self.u, servico=SANEPAR)
        outro = criar_usuario("b@teste.local")
        credencial(outro, SANEPAR)
        self.client.post(self.url, {"servico": SANEPAR, "acao": "remover"})
        self.assertFalse(Credencial.objects.filter(usuario=self.u).exists())
        self.assertFalse(SessaoServico.objects.filter(usuario=self.u).exists())
        self.assertEqual(Tarefa.objects.get(pk=pendente.pk).status, Tarefa.CANCELADA)
        self.assertTrue(Credencial.objects.filter(usuario=outro).exists())

    def test_servico_invalido(self):
        r = self.client.post(self.url, {"servico": "banco", "acao": "salvar", "login": CPF, "senha": SENHA})
        self.assertEqual(r.status_code, 404)

    def test_copel_proxima_conta(self):
        credencial(self.u, COPEL)
        passada = timezone.localdate() - timedelta(days=2)
        FaturaCopel.objects.create(usuario=self.u, numero_fatura="1", referencia="2026-09", valor_total=100,
                                   detalhes={"proxima_leitura": passada.isoformat()})
        r = self.client.get(self.url)
        self.assertContains(r, "A conta de outubro saiu")
        status = self.client.get(reverse("integracoes:status"))
        self.assertContains(status, f"Copel · conta de outubro saiu ({passada:%d/%m})")
        futura = timezone.localdate() + timedelta(days=9)
        FaturaCopel.objects.update(detalhes={"proxima_leitura": futura.isoformat()})
        status = self.client.get(reverse("integracoes:status"))
        self.assertContains(status, f"Copel · próxima conta {futura:%d/%m}")
        self.assertNotContains(status, "falta conectar")  # a sessão da Copel dura minutos: não é pendência


class SincronizarTests(TestCase):
    def setUp(self):
        self.a, self.b = criar_usuario("a@teste.local"), criar_usuario("b@teste.local")
        self.client.force_login(self.a)

    def _post(self, servico, **dados):
        return self.client.post(reverse("integracoes:sincronizar", args=[servico]), dados, **HX)

    def test_enfileira_com_toast(self):
        credencial(self.a)
        r = self._post(NOTAPARANA)
        self.assertEqual(r.status_code, 200)
        gatilho = json.loads(r["HX-Trigger"])
        self.assertIn("na fila", gatilho["toast"]["mensagem"])
        self.assertTrue(gatilho["sincronizou"])
        t = Tarefa.objects.get()
        self.assertEqual((t.usuario, t.servico, t.status, t.origem), (self.a, NOTAPARANA, Tarefa.PENDENTE, "manual"))
        r = self._post(NOTAPARANA)
        self.assertIn("Já existe", json.loads(r["HX-Trigger"])["toast"]["mensagem"])
        self.assertEqual(Tarefa.objects.count(), 1)

    def test_sem_credencial_ou_sessao(self):
        r = self._post(NOTAPARANA)
        self.assertEqual(json.loads(r["HX-Trigger"])["toast"]["tipo"], "erro")
        credencial(self.a, SANEPAR)
        sessao(self.a, SANEPAR, expirada=True)
        r = self._post(SANEPAR)
        self.assertIn("expirou", json.loads(r["HX-Trigger"])["toast"]["mensagem"])
        credencial(self.a, COPEL)
        r = self._post(COPEL)
        self.assertIn("Conectar", json.loads(r["HX-Trigger"])["toast"]["mensagem"])
        self.assertFalse(Tarefa.objects.exists())

    def test_copel_logo_depois_de_conectar(self):
        credencial(self.a, COPEL)
        sessao(self.a, COPEL)
        self._post(COPEL)
        self.assertTrue(Tarefa.objects.filter(servico=COPEL).exists())

    def test_reprocessar_so_nota_parana(self):
        credencial(self.a)
        self._post(NOTAPARANA, tipo="reprocessar")
        self.assertEqual(Tarefa.objects.get().tipo, "reprocessar")
        self.assertEqual(self._post(SANEPAR, tipo="reprocessar").status_code, 404)

    def test_servico_invalido_e_metodo(self):
        self.assertEqual(self._post("banco").status_code, 404)
        self.assertEqual(self.client.get(reverse("integracoes:sincronizar", args=[NOTAPARANA])).status_code, 405)

    def test_sem_htmx_redireciona(self):
        credencial(self.a)
        r = self.client.post(reverse("integracoes:sincronizar", args=[NOTAPARANA]))
        self.assertRedirects(r, reverse("integracoes:configuracoes"))

    def test_atualizar_agora(self):
        credencial(self.a)
        credencial(self.a, SANEPAR)
        sessao(self.a, SANEPAR)
        credencial(self.a, COPEL)
        sessao(self.a, COPEL)
        r = self.client.post(reverse("integracoes:atualizar"), **HX)
        self.assertIn("Nota Paraná e Sanepar", json.loads(r["HX-Trigger"])["toast"]["mensagem"])
        self.assertEqual(sorted(Tarefa.objects.values_list("servico", flat=True)), [NOTAPARANA, SANEPAR])
        status = self.client.get(reverse("integracoes:status"))
        self.assertContains(status, "disabled")
        self.assertContains(status, "na fila")

    def test_atualizar_sem_sanepar_expirada(self):
        credencial(self.a)
        credencial(self.a, SANEPAR)
        sessao(self.a, SANEPAR, expirada=True)
        self.client.post(reverse("integracoes:atualizar"), **HX)
        self.assertEqual(list(Tarefa.objects.values_list("servico", flat=True)), [NOTAPARANA])

    def test_isolamento(self):
        credencial(self.b)
        tarefa_b = Tarefa.objects.create(usuario=self.b, servico=NOTAPARANA, status=Tarefa.ERRO,
                                         mensagem="mensagem-secreta-de-b", log="log-de-b")
        # A não tem credencial: o pedido dele não usa a de B nem mexe na fila de B.
        r = self._post(NOTAPARANA)
        self.assertEqual(json.loads(r["HX-Trigger"])["toast"]["tipo"], "erro")
        self.assertEqual(Tarefa.objects.filter(usuario=self.b).count(), 1)
        self.assertEqual(self.client.get(reverse("integracoes:tarefa", args=[tarefa_b.pk])).status_code, 404)
        for url in (reverse("integracoes:configuracoes"), reverse("integracoes:historico"), reverse("integracoes:status"),
                    reverse("integracoes:servico", args=[NOTAPARANA])):
            self.assertNotContains(self.client.get(url), "mensagem-secreta-de-b")
        credencial(self.a)
        self._post(NOTAPARANA)
        self.assertEqual(Tarefa.objects.filter(usuario=self.a).count(), 1)
        self.assertEqual(Tarefa.objects.filter(usuario=self.b).count(), 1)

    def test_modal_da_tarefa(self):
        t = Tarefa.objects.create(usuario=self.a, servico=SANEPAR, status=Tarefa.OK, log="[07:00:00] linha do log",
                                  resultado={"novas": 1})
        r = self.client.get(reverse("integracoes:tarefa", args=[t.pk]))
        self.assertContains(r, "linha do log")

    def test_status_linhas(self):
        credencial(self.a)
        Tarefa.objects.create(usuario=self.a, servico=NOTAPARANA, status=Tarefa.OK, resultado={"novas": 2},
                              terminada_em=timezone.now())
        credencial(self.a, SANEPAR)
        sessao(self.a, SANEPAR, expirada=True)
        r = self.client.get(reverse("integracoes:status"))
        self.assertContains(r, "2 novas")
        self.assertContains(r, "Sanepar · sessão expirou")
        Credencial.objects.filter(usuario=self.a, servico=NOTAPARANA).update(ativo=False)
        self.assertContains(self.client.get(reverse("integracoes:status")), "senha recusada")

    def test_status_sem_conexoes(self):
        r = self.client.get(reverse("integracoes:status"))
        self.assertContains(r, "Nenhuma conexão")
        self.assertNotContains(r, "Atualizar agora")


class FilaTests(TestCase):
    def setUp(self):
        self.u = criar_usuario()

    def test_enfileirar_sem_duplicar(self):
        t1 = fila.enfileirar(self.u, NOTAPARANA, origem="login")
        t2 = fila.enfileirar(self.u, NOTAPARANA)
        self.assertTrue(t1.nova)
        self.assertFalse(t2.nova)
        self.assertEqual(t1.pk, t2.pk)
        self.assertEqual(t1.origem, "login")
        Tarefa.objects.filter(pk=t1.pk).update(status=Tarefa.RODANDO)
        self.assertEqual(fila.enfileirar(self.u, NOTAPARANA).pk, t1.pk)
        Tarefa.objects.filter(pk=t1.pk).update(status=Tarefa.OK)
        self.assertNotEqual(fila.enfileirar(self.u, NOTAPARANA).pk, t1.pk)
        self.assertTrue(fila.enfileirar(self.u, SANEPAR).nova)
        self.assertTrue(fila.enfileirar(criar_usuario("b@teste.local"), NOTAPARANA).nova)

    def test_reivindicar_mais_antiga(self):
        t1 = fila.enfileirar(self.u, NOTAPARANA)
        fila.enfileirar(self.u, SANEPAR)
        t = fila.reivindicar()
        self.assertEqual(t.pk, t1.pk)
        self.assertEqual(t.status, Tarefa.RODANDO)
        self.assertIsNotNone(t.iniciada_em)
        self.assertEqual(fila.reivindicar().servico, SANEPAR)
        self.assertIsNone(fila.reivindicar())

    def test_sessao_copel_vale_minutos(self):
        s = sessao(self.u, COPEL)
        self.assertTrue(fila.sessao_valida(s, COPEL))
        self.assertFalse(fila.sessao_valida(s, COPEL, agora=timezone.now() + timedelta(hours=1)))
        self.assertTrue(fila.sessao_valida(sessao(self.u, SANEPAR), SANEPAR, agora=timezone.now() + timedelta(days=5)))


class AgendaTests(TestCase):
    def setUp(self):
        from datetime import time
        self.u = criar_usuario(hora_atualizacao=time(7, 0))
        credencial(self.u)
        credencial(self.u, SANEPAR)
        sessao(self.u, SANEPAR)
        credencial(self.u, COPEL)
        sessao(self.u, COPEL)

    def agendar(self, hora, minuto=0, dia=6):
        agora = datetime(2026, 10, dia, hora, minuto, tzinfo=SP)
        with mock.patch("django.utils.timezone.now", return_value=agora):
            return sorted(t.servico for t in fila.agendar(agora))

    def test_antes_e_depois_do_horario(self):
        self.assertEqual(self.agendar(6, 59), [])
        self.assertEqual(self.agendar(7, 0), [NOTAPARANA, SANEPAR])  # Copel nunca
        self.assertTrue(all(t.origem == "agenda" for t in Tarefa.objects.all()))
        self.assertEqual(self.agendar(7, 5), [])  # já na fila

    def test_ok_hoje_nao_repete_e_amanha_sim(self):
        self.agendar(7, 0)
        Tarefa.objects.update(status=Tarefa.OK)
        self.assertEqual(self.agendar(15, 0), [])
        self.assertEqual(self.agendar(6, 0, dia=7), [])
        self.assertEqual(self.agendar(7, 1, dia=7), [NOTAPARANA, SANEPAR])

    def test_erro_tenta_de_novo_com_intervalo_e_limite(self):
        self.agendar(7, 0)
        Tarefa.objects.update(status=Tarefa.ERRO)
        self.assertEqual(self.agendar(7, 30), [])  # menos de 1 h
        self.assertEqual(self.agendar(8, 1), [NOTAPARANA, SANEPAR])
        Tarefa.objects.update(status=Tarefa.ERRO)
        self.assertEqual(self.agendar(9, 2), [NOTAPARANA, SANEPAR])
        Tarefa.objects.update(status=Tarefa.ERRO)
        self.assertEqual(self.agendar(10, 3), [])  # 3 tentativas no dia

    def test_manual_ok_hoje_conta(self):
        with mock.patch("django.utils.timezone.now", return_value=datetime(2026, 10, 6, 6, 0, tzinfo=SP)):
            Tarefa.objects.create(usuario=self.u, servico=NOTAPARANA, status=Tarefa.OK, origem="manual")
        self.assertEqual(self.agendar(7, 0), [SANEPAR])

    def test_quem_fica_de_fora(self):
        Credencial.objects.filter(usuario=self.u, servico=NOTAPARANA).update(ativo=False)  # senha recusada
        SessaoServico.objects.filter(usuario=self.u, servico=SANEPAR).update(expirada=True)
        self.assertEqual(self.agendar(8, 0), [])
        outro = criar_usuario("b@teste.local", atualizacao_automatica=False)
        credencial(outro)
        inativo = criar_usuario("c@teste.local", is_active=False)
        credencial(inativo)
        self.assertEqual(self.agendar(9, 0), [])


class TrabalhadorTests(MidiaTemporaria):
    def setUp(self):
        super().setUp()
        self.u = criar_usuario()

    def rodar(self, registro: dict):
        saida = StringIO()
        with mock.patch.dict(coletores.REGISTRO, registro):
            call_command("trabalhador", "--uma-vez", "--sem-agenda", stdout=saida)
        return saida.getvalue()

    def test_ok_erro_e_inesperado(self):
        def ok(usuario, log):
            log.ocultar(CPF_FORMATADO, SENHA)
            log(f"entrando com {CPF} e {SENHA}")
            return {"novas": 2, "atualizadas": 5, "erros": 0}

        def esperado(usuario, log):
            raise ErroColeta("Sessão da Sanepar expirou: use Conectar em Configurações.")

        def inesperado(usuario, log):
            raise ValueError("quebrou\nCall log: detalhes")

        t_ok = fila.enfileirar(self.u, NOTAPARANA)
        t_sessao = fila.enfileirar(self.u, SANEPAR)
        t_bug = fila.enfileirar(self.u, COPEL)
        saida = self.rodar({(NOTAPARANA, "sincronizar"): ok, (SANEPAR, "sincronizar"): esperado,
                            (COPEL, "sincronizar"): inesperado})
        t_ok.refresh_from_db(), t_sessao.refresh_from_db(), t_bug.refresh_from_db()
        self.assertEqual(t_ok.status, Tarefa.OK)
        self.assertEqual(t_ok.resultado["novas"], 2)
        self.assertEqual(t_ok.mensagem, "2 nova(s), 5 atualizada(s)")
        self.assertIsNotNone(t_ok.terminada_em)
        self.assertIn("entrando com ••• e •••", t_ok.log)
        for segredo in (CPF, SENHA):
            self.assertNotIn(segredo, t_ok.log + t_ok.progresso + t_ok.mensagem + saida)
        self.assertEqual(t_sessao.status, Tarefa.ERRO)
        self.assertEqual(t_sessao.mensagem, "Sessão da Sanepar expirou: use Conectar em Configurações.")
        self.assertNotIn("Traceback", t_sessao.log)
        self.assertEqual(t_bug.status, Tarefa.ERRO)
        self.assertEqual(t_bug.mensagem, "Erro inesperado: quebrou")
        self.assertIn("Traceback", t_bug.log)
        self.assertFalse(Tarefa.objects.filter(status__in=fila.ATIVAS).exists())
        self.assertTrue((self.pasta / "trabalhador.json").exists())

    def test_tarefas_presas_viram_erro(self):
        presa = Tarefa.objects.create(usuario=self.u, servico=NOTAPARANA, status=Tarefa.RODANDO,
                                      iniciada_em=timezone.now() - timedelta(hours=3))
        recente = Tarefa.objects.create(usuario=self.u, servico=SANEPAR, status=Tarefa.RODANDO,
                                        iniciada_em=timezone.now() - timedelta(minutes=10))
        self.rodar({})
        presa.refresh_from_db(), recente.refresh_from_db()
        self.assertEqual(presa.status, Tarefa.ERRO)
        self.assertIn("Interrompida", presa.mensagem)
        self.assertEqual(recente.status, Tarefa.RODANDO)

    def test_log_grande_cortado(self):
        def falante(usuario, log):
            for i in range(3000):
                log(f"linha {i:05d} " + "x" * 20)
            return {"novas": 0}

        t = fila.enfileirar(self.u, NOTAPARANA)
        self.rodar({(NOTAPARANA, "sincronizar"): falante})
        t.refresh_from_db()
        self.assertLessEqual(len(t.log), Registro.LIMITE + 40)
        self.assertIn("linha 02999", t.log)
        self.assertIn("início cortado", t.log)

    def test_tipo_desconhecido(self):
        t = Tarefa.objects.create(usuario=self.u, servico=SANEPAR, tipo="reprocessar")
        self.rodar({})
        t.refresh_from_db()
        self.assertEqual(t.status, Tarefa.ERRO)


class NotaParanaLeitoresTests(MidiaTemporaria):
    NFCE = "41250801525323001648650730009103091110028790"
    NFE = "41251247909523000119550010000202551021252381"

    def setUp(self):
        super().setUp()
        if not NOTAS_HTML.exists():
            self.skipTest("sem testes_dados/notas_html")
        self.u = criar_usuario()

    def ler(self, chave):
        html = (NOTAS_HTML / f"{chave}.html").read_bytes()
        listagem = json.loads((NOTAS_HTML / f"{chave}.json").read_text(encoding="utf-8"))
        return html, listagem

    def test_montar_nota_nfce_e_nfe(self):
        for chave in (self.NFCE, self.NFE):
            html, listagem = self.ler(chave)
            nota, itens, pagamentos = notaparana.montar_nota(html, listagem)
            self.assertEqual(nota["chave"], chave)
            self.assertEqual(nota["id_doc_fiscal"], listagem["idDocFiscal"])
            self.assertIn("NotaFiscalHtml?tpDoc=", nota["url"])
            self.assertEqual(nota["detalhes"]["Nota Paraná"], listagem)
            self.assertTrue(itens)
            self.assertNotIn("ă", json.dumps(nota["detalhes"], ensure_ascii=False))  # encoding da NF-e

    def test_salvar_e_reprocessar_sem_o_site(self):
        from notas import servicos
        html, listagem = self.ler(self.NFCE)
        nota, itens, pagamentos = notaparana.montar_nota(html, listagem)
        nota["credito"] = 1.23
        obj, criada = servicos.salvar_nota(self.u, nota, itens, pagamentos, html=html)
        self.assertTrue(criada and obj.html)
        Nota.objects.filter(pk=obj.pk).update(categoria="Mercado")
        ItemNota = obj.itens.model
        ItemNota.objects.filter(nota=obj).delete()
        linhas = []
        resultado = notaparana.reprocessar(self.u, linhas.append)
        self.assertEqual((resultado["atualizadas"], resultado["erros"]), (1, 0))
        obj.refresh_from_db()
        self.assertEqual(obj.itens.count(), len(itens))
        self.assertEqual(obj.categoria, "Mercado")
        self.assertEqual(float(obj.credito), 1.23)

    def test_chamada_que_rejeita_nota_e_bloqueada(self):
        for caminho, params in (("NotasFiscaisAjax", {"idDocFiscal": "1"}), ("NotasFiscaisAjax", {"IDDOCFISCAL": 1}),
                                ("NotasFiscaisAjax?idDocFiscal=1", {})):
            with self.assertRaises(AssertionError):
                notaparana.verificar_chamada(caminho, params)
        req = mock.Mock()
        with self.assertRaises(AssertionError):
            notaparana._get(req, "NotasFiscaisAjax", {"periodo": "01/10/2026", "idDocFiscal": "9"})
        req.get.assert_not_called()
        notaparana.verificar_chamada("NotasFiscaisAjax", {"periodo": "01/10/2026", "situacao": "1"})
        notaparana.verificar_chamada("NotaFiscalHtml", {"tpDoc": 2, "idDocFiscal": "9", "eCompleta": "true"})

    def test_extrato(self):
        arquivo = RECON / "ultimo-extrato.html"
        if not arquivo.exists():
            self.skipTest("sem testes_dados/recon/ultimo-extrato.html")
        periodos, resumos, placar = notaparana.ler_extrato(arquivo.read_text(encoding="utf-8"))
        self.assertTrue(periodos)
        self.assertEqual(len(periodos), len(resumos))
        self.assertRegex(periodos[0], r"^01/\d{2}/\d{4}$")
        self.assertRegex(resumos[0]["periodo"], r"^\d{4}-\d{2}$")

    def test_extrato_plano_b(self):
        html = '<script>var x = {paramIniPerido : "01/11/2025"};</script>'
        periodos, resumos, placar = notaparana.ler_extrato(html, hoje=datetime(2026, 2, 10).date())
        self.assertEqual(periodos, ["01/11/2025", "01/12/2025", "01/01/2026", "01/02/2026"])
        with self.assertRaises(ErroColeta):
            notaparana.ler_extrato("<html>login</html>")


class ConsumoLeitoresTests(TestCase):
    def test_copel_historico(self):
        arquivo = RECON / "copel-historicoPagamento.html"
        if not arquivo.exists():
            self.skipTest("sem testes_dados/recon/copel-historicoPagamento.html")
        faturas = copel.ler_historico(arquivo.read_text(encoding="utf-8"))
        self.assertEqual(len(faturas), 9)
        primeira = faturas[0]
        self.assertEqual(primeira["linha"], 0)
        self.assertRegex(primeira["referencia"], r"^\d{4}-\d{2}$")
        self.assertRegex(primeira["numero_fatura"], r"^\d+$")
        self.assertIn(primeira["situacao"], ("Pendente", "Quitada"))
        self.assertIsInstance(primeira["valor_total"], float)
        self.assertRegex(primeira["vencimento"], r"^\d{4}-\d{2}-\d{2}$")

    def test_copel_nunca_clica_em_primeira_via(self):
        class Botao:
            def __init__(self, texto, id_=""):
                self.texto, self.id, self.clicado = texto, id_, False

            def inner_text(self):
                return self.texto

            def get_attribute(self, nome):
                return self.id if nome == "id" else None

            def click(self):
                self.clicado = True

        for perigoso in (Botao("Emitir 1ª via"), Botao("Primeira via"), Botao("Gerar", "btnGerarPrimeiraVia"),
                         Botao("1a via")):
            with self.assertRaises(AssertionError):
                copel.clicar(perigoso)
            self.assertFalse(perigoso.clicado)
        seguro = Botao("2 via", "formHistoricoPagto:dtListaHistoricoPagto:0:j_idt82")
        copel.clicar(seguro)
        self.assertTrue(seguro.clicado)

    def test_sanepar_meses_e_ajax(self):
        com_pdf = {f"2025-{m:02d}" for m in range(7, 13)} | {f"2026-{m:02d}" for m in range(1, 10)}
        self.assertEqual(sanepar.meses_a_buscar(["", "2025", "2026"], com_pdf, "2026-10"), [("2026", "10")])
        self.assertEqual(len(sanepar.meses_a_buscar(["2025", "2026"], set(), "2026-10")), 22)
        pdf = base64.b64encode(b"%PDF-1.4 teste").decode()
        self.assertEqual(sanepar.pdf_do_ajax([{"command": "insert"}, {"command": "DownloadFileCommand", "file_url": pdf}]),
                         b"%PDF-1.4 teste")
        self.assertIsNone(sanepar.pdf_do_ajax([{"command": "insert", "data": "Nenhum dado"}]))
        self.assertTrue(sanepar.logado("https://clientes.sanepar.com.br/minha-conta"))
        self.assertFalse(sanepar.logado("https://clientes.sanepar.com.br/entre-ou-cadastre-se?x=1"))
        self.assertFalse(sanepar.logado("https://auth-cs.identidadedigital.pr.gov.br/centralautenticacao/"))
        self.assertFalse(copel.logado("https://www.copel.com/avaweb/paginaLogin/login.jsf"))
        self.assertTrue(copel.logado("https://www.copel.com/avaweb/paginas/historicoPagamento.jsf"))

    def test_sanepar_pdf_de_outro_mes(self):
        arquivo = RECON / "sanepar-2026-09.pdf"
        if not arquivo.exists():
            self.skipTest("sem PDF de exemplo")
        pdf = arquivo.read_bytes()
        self.assertEqual(sanepar.fatura_do_pdf(pdf, "2026-09")["referencia"], "2026-09")
        with self.assertRaises(sanepar.AindaNaoEmitida):
            sanepar.fatura_do_pdf(pdf, "2026-10")

    def test_coletores_sem_sessao_nao_abrem_navegador(self):
        u = criar_usuario()
        with mock.patch.object(sanepar, "navegador") as nav, mock.patch.object(copel, "navegador") as nav2, \
                mock.patch.object(notaparana, "navegador") as nav3:
            with self.assertRaises(ErroColeta):
                sanepar.sincronizar(u, print)
            sessao(u, SANEPAR, expirada=True)
            with self.assertRaisesMessage(ErroColeta, "Sessão da Sanepar expirou"):
                sanepar.sincronizar(u, print)
            with self.assertRaisesMessage(ErroColeta, "Sessão da Copel expirou"):
                copel.sincronizar(u, print)
            with self.assertRaisesMessage(ErroColeta, "Cadastre o CPF"):
                notaparana.sincronizar(u, print)
        for n in (nav, nav2, nav3):
            n.assert_not_called()
