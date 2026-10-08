"""Água e Luz: páginas, isolamento entre usuários, PDF (download do dono, envio), conciliação e a regra do Pago."""
import json
import shutil
import tempfile
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from unittest import skipUnless

from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from consumo import servicos
from consumo.consultas import bandeiras, grupo_copel
from consumo.models import FaturaCopel, FaturaSanepar
from contas.models import Usuario
from financeiro import servicos as financeiro
from financeiro.models import SAIDA, HistoricoLancamento, Lancamento
from integracoes.models import Tarefa

DADOS = Path(__file__).resolve().parents[1] / "testes_dados"  # exemplos reais, fora do git
PDF_SANEPAR = DADOS / "sanepar_pdf" / "2026-09.pdf"
PDF_COPEL = DADOS / "copel_pdf" / "2026-09-20265710615352.pdf"          # pendente
PDF_COPEL_PAGA = DADOS / "copel_pdf" / "2026-08-20265392287166.pdf"     # "FATURA ARRECADADA"
MIDIA = tempfile.mkdtemp(prefix="gfp-teste-consumo-")


def lancar(usuario, data, categoria="Água", valor="100.00", pago=False, movimentacao=SAIDA, descricao=""):
    return Lancamento.objects.create(usuario=usuario, data=data, movimentacao=movimentacao, categoria=categoria,
                                     valor=Decimal(valor), pago=pago, descricao=descricao)


def sanepar(usuario, referencia, total="199.87", situacao="Paga", **extra):
    return FaturaSanepar.objects.create(usuario=usuario, referencia=referencia, valor_total=Decimal(total),
                                        situacao=situacao, **extra)


def copel(usuario, referencia, numero, total="460.64", situacao="Quitada", **extra):
    return FaturaCopel.objects.create(usuario=usuario, referencia=referencia, numero_fatura=numero,
                                      valor_total=Decimal(total), situacao=situacao, **extra)


@override_settings(MEDIA_ROOT=MIDIA)
class Base(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MIDIA, ignore_errors=True)

    def setUp(self):
        self.ana = Usuario.objects.create_user("ana@teste.local", "senha-forte-123", nome="Ana Teste")
        self.bia = Usuario.objects.create_user("bia@teste.local", "senha-forte-123", nome="Bia Teste")
        self.client.force_login(self.ana)

    def enviar(self, nome_url, conteudo: bytes, nome="conta.pdf", tipo="application/pdf"):
        return self.client.post(reverse(nome_url), {"pdf": SimpleUploadedFile(nome, conteudo, content_type=tipo)},
                                follow=True)

    def mensagens(self, resposta) -> str:
        return " | ".join(str(m) for m in resposta.context["messages"])


class PaginasTest(Base):
    def test_paginas_vazias(self):
        for nome in ("consumo:agua", "consumo:luz"):
            r = self.client.get(reverse(nome))
            self.assertEqual(r.status_code, 200)
            self.assertContains(r, "Enviar PDF da conta")

    def test_agua_com_faturas_e_conferencia(self):
        sanepar(self.ana, "2026-08", "217.04", valor_agua=Decimal("120.58"), valor_esgoto=Decimal("96.46"),
                valor_servicos=Decimal("0"), consumo_m3=16, leitura_anterior=103, leitura_atual=119,
                vencimento=date(2026, 8, 23))
        sanepar(self.ana, "2026-09", "199.87", consumo_m3=15, vencimento=date(2026, 9, 23))
        lancar(self.ana, date(2026, 7, 5), valor="217.04")   # ref 08 -> julho: confere
        lancar(self.ana, date(2026, 8, 5), valor="150.00")   # ref 09 -> agosto: diferente
        Tarefa.objects.create(usuario=self.ana, servico="sanepar", status=Tarefa.OK,
                              terminada_em="2026-10-06T10:00:00+00:00")
        r = self.client.get(reverse("consumo:agua"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Faturas e lançamentos de Água")
        self.assertContains(r, "103 → 119")
        self.assertContains(r, "Busca automática: última em 06/10/2026")
        linhas = r.context["linhas"]
        self.assertEqual([l["fatura"].referencia for l in linhas], ["2026-09", "2026-08"])
        self.assertEqual(linhas[1]["lancamento"]["mes"], "2026-07")
        self.assertTrue(linhas[1]["lancamento"]["confere"])
        self.assertFalse(linhas[0]["lancamento"]["confere"])
        self.assertEqual(r.context["kpis"]["consumo_medio"], "15,5 m³")

    def test_luz_com_faturas(self):
        copel(self.ana, "2026-09", "1", consumo_kwh=390, bandeira="Amarela:18/08-16/09", detalhes={
            "itens": [{"item": "ENERGIA ELET CONSUMO", "valor": 200}, {"item": "ENERGIA ELET USO SISTEMA", "valor": 200},
                      {"item": "ENERGIA CONS. B.AMARELA", "valor": 12.2}, {"item": "CONT ILUMIN PUBLICA MUNICIPIO",
                                                                         "valor": 48.44}],
            "tributos": {"ICMS": {"valor": 78.31}, "PIS": {"valor": 4.83}, "COFINS": {"valor": 22.19}}})
        lancar(self.ana, date(2026, 9, 5), "Luz", "460.64")
        r = self.client.get(reverse("consumo:luz"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Do que é feita a conta")
        self.assertContains(r, "R$ 105,33")  # impostos
        self.assertTrue(r.context["linhas"][0]["lancamento"]["confere"])
        self.assertEqual(grupo_copel("ENERGIA CONS. B.AMARELA"), "Bandeira tarifária")
        self.assertEqual(grupo_copel("MULTA"), "Outros")
        self.assertEqual([b["cor"] for b in bandeiras("Amarela:22/12-31/12 Verde:01/01-15/01")], ["laranja", "verde"])

    def test_aviso_da_proxima_conta_da_copel(self):
        hoje = timezone.localdate()
        copel(self.ana, "2026-08", "1", data_leitura=hoje - timedelta(days=40),
              detalhes={"proxima_leitura": (hoje - timedelta(days=2)).isoformat()})
        r = self.client.get(reverse("consumo:luz"))
        self.assertContains(r, "e ainda não foi importada")
        self.assertContains(r, "A conta de setembro/2026 saiu em")
        FaturaCopel.objects.update(detalhes={"proxima_leitura": (hoje + timedelta(days=10)).isoformat()})
        r = self.client.get(reverse("consumo:luz"))
        self.assertNotContains(r, "e ainda não foi importada")
        self.assertContains(r, f"Próxima conta em {hoje + timedelta(days=10):%d/%m/%Y}")

    def test_usuario_nao_ve_faturas_de_outro(self):
        sanepar(self.bia, "2026-09", "987.65")
        copel(self.bia, "2026-09", "999", "876.54")
        self.assertNotContains(self.client.get(reverse("consumo:agua")), "987,65")
        self.assertNotContains(self.client.get(reverse("consumo:luz")), "876,54")


class DownloadTest(Base):
    def test_dono_baixa_e_outro_usuario_recebe_404(self):
        fatura = sanepar(self.ana, "2026-09")
        fatura.pdf.save("2026-09.pdf", ContentFile(b"%PDF-1.4 teste"))
        luz = copel(self.ana, "2026-09", "123")
        luz.pdf.save("2026-09-123.pdf", ContentFile(b"%PDF-1.4 luz"))

        r = self.client.get(reverse("consumo:agua_pdf", args=[fatura.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Content-Type"], "application/pdf")
        self.assertIn('attachment; filename="sanepar-2026-09.pdf"', r["Content-Disposition"])
        self.assertEqual(b"".join(r.streaming_content), b"%PDF-1.4 teste")
        self.assertIn("attachment", self.client.get(reverse("consumo:luz_pdf", args=[luz.pk]))["Content-Disposition"])

        self.client.force_login(self.bia)
        self.assertEqual(self.client.get(reverse("consumo:agua_pdf", args=[fatura.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("consumo:luz_pdf", args=[luz.pk])).status_code, 404)

    def test_fatura_sem_pdf_404(self):
        fatura = sanepar(self.ana, "2026-09")
        self.assertEqual(self.client.get(reverse("consumo:agua_pdf", args=[fatura.pk])).status_code, 404)


class EnvioTest(Base):
    def test_modal_de_envio(self):
        r = self.client.get(reverse("consumo:agua_enviar"))
        self.assertContains(r, 'type="file"')
        self.assertContains(r, 'enctype="multipart/form-data"')

    def test_recusa_o_que_nao_e_pdf(self):
        r = self.enviar("consumo:agua_enviar", b"%PDF falso", nome="conta.txt", tipo="text/plain")
        self.assertIn("Envie um arquivo PDF", self.mensagens(r))
        r = self.enviar("consumo:luz_enviar", b"so texto, sem cabecalho de PDF")
        self.assertIn("não é um PDF", self.mensagens(r))
        r = self.enviar("consumo:agua_enviar", b"%PDF-1.4 quebrado")
        self.assertIn("Não consegui abrir este PDF", self.mensagens(r))
        r = self.client.post(reverse("consumo:agua_enviar"), follow=True)
        self.assertIn("Escolha o PDF", self.mensagens(r))
        self.assertFalse(FaturaSanepar.objects.exists())
        self.assertFalse(FaturaCopel.objects.exists())

    def test_recusa_pdf_acima_de_10_mb(self):
        r = self.enviar("consumo:agua_enviar", b"%PDF" + b"0" * (10 * 1024 * 1024))
        self.assertIn("10 MB", self.mensagens(r))

    @skipUnless(PDF_SANEPAR.exists() and PDF_COPEL.exists(), "PDFs reais ausentes")
    def test_pdf_de_outro_servico(self):
        r = self.enviar("consumo:agua_enviar", PDF_COPEL.read_bytes())
        self.assertIn("não parece ser uma conta da Sanepar", self.mensagens(r))
        r = self.enviar("consumo:luz_enviar", PDF_SANEPAR.read_bytes())
        self.assertIn("não parece ser uma conta da Copel", self.mensagens(r))

    @skipUnless(PDF_SANEPAR.exists(), "PDF real da Sanepar ausente")
    def test_envio_sanepar_grava_e_concilia(self):
        agosto = lancar(self.ana, date(2026, 8, 5), valor="300.00")  # ref 09/2026 -> agosto
        r = self.enviar("consumo:agua_enviar", PDF_SANEPAR.read_bytes())
        self.assertIn("Conta 09/2026 da Sanepar importada: R$ 199,87 · 15 m³", self.mensagens(r))
        fatura = FaturaSanepar.objects.get(usuario=self.ana)
        self.assertEqual((fatura.referencia, fatura.valor_total, fatura.situacao, fatura.matricula),
                         ("2026-09", Decimal("199.87"), "Paga", "4130.4928"))
        self.assertTrue(fatura.pdf.name.startswith(f"usuarios/{self.ana.pk}/sanepar/"))
        agosto.refresh_from_db()
        self.assertEqual(agosto.valor, Decimal("199.87"))
        self.assertTrue(agosto.pago)  # fatura paga marca o pago
        self.assertTrue(HistoricoLancamento.objects.filter(lancamento_id=agosto.id, origem="Sanepar").exists())
        # a outra usuária não ganhou nada
        self.assertFalse(FaturaSanepar.objects.filter(usuario=self.bia).exists())

    @skipUnless(PDF_SANEPAR.exists(), "PDF real da Sanepar ausente")
    def test_envio_sanepar_de_outra_matricula_recusado(self):
        sanepar(self.ana, "2026-09", matricula="9999.0000")
        with self.assertRaisesMessage(servicos.PdfInvalido, "matrícula 4130.4928"):
            servicos.importar_pdf_sanepar(self.ana, PDF_SANEPAR.read_bytes())

    @skipUnless(PDF_COPEL.exists() and PDF_COPEL_PAGA.exists(), "PDFs reais da Copel ausentes")
    def test_envio_copel(self):
        setembro = lancar(self.ana, date(2026, 9, 10), "Luz", "400.00")
        r = self.enviar("consumo:luz_enviar", PDF_COPEL.read_bytes())
        self.assertIn("Conta 09/2026 da Copel importada: R$ 460,64 · 390 kWh", self.mensagens(r))
        fatura = FaturaCopel.objects.get(usuario=self.ana)
        self.assertEqual((fatura.numero_fatura, fatura.referencia, fatura.vencimento, fatura.situacao),
                         ("20265710615352", "2026-09", date(2026, 10, 15), "Pendente"))
        self.assertEqual(fatura.consumo_kwh, 390)
        self.assertEqual(fatura.detalhes["proxima_leitura"], "2026-10-16")
        setembro.refresh_from_db()
        self.assertEqual(setembro.valor, Decimal("460.64"))  # mesmo mês (defasagem 0)
        self.assertFalse(setembro.pago)                       # fatura pendente não marca pago

        servicos.importar_pdf_copel(self.ana, PDF_COPEL_PAGA.read_bytes())
        self.assertEqual(FaturaCopel.objects.get(numero_fatura="20265392287166").situacao, "Quitada")

    @skipUnless(PDF_COPEL.exists(), "PDF real da Copel ausente")
    def test_pdf_antigo_nao_desfaz_quitacao(self):
        copel(self.ana, "2026-09", "20265710615352", situacao="Quitada", data_pagamento=date(2026, 10, 5))
        fatura, _ = servicos.importar_pdf_copel(self.ana, PDF_COPEL.read_bytes())
        self.assertEqual((fatura.situacao, fatura.data_pagamento), ("Quitada", date(2026, 10, 5)))
        self.assertEqual(FaturaCopel.objects.filter(usuario=self.ana).count(), 1)


class ConciliacaoTest(Base):
    def test_defasagem_sanepar_m_mais_1_e_copel_mesmo_mes(self):
        marco_agua = lancar(self.ana, date(2026, 3, 5), "Água", "10.00")
        abril_agua = lancar(self.ana, date(2026, 4, 5), "Água", "10.00")
        marco_luz = lancar(self.ana, date(2026, 3, 5), "Luz", "10.00")
        sanepar(self.ana, "2026-04", "216.64")   # -> março
        copel(self.ana, "2026-03", "1", "541.99")  # -> março
        servicos.conciliar_agua(self.ana)
        servicos.conciliar_luz(self.ana)
        for l in (marco_agua, abril_agua, marco_luz):
            l.refresh_from_db()
        self.assertEqual(marco_agua.valor, Decimal("216.64"))
        self.assertEqual(abril_agua.valor, Decimal("10.00"))
        self.assertEqual(marco_luz.valor, Decimal("541.99"))

    def test_regra_do_pago(self):
        # fatura em aberto nunca desmarca um pago
        pago = lancar(self.ana, date(2026, 5, 5), "Água", "10.00", pago=True)
        sanepar(self.ana, "2026-06", "199.26", situacao="Em aberto")
        # o usuário desmarcou o pago na tela: a fatura paga não marca de novo
        decidido = lancar(self.ana, date(2026, 6, 5), "Água", "10.00", pago=True)
        financeiro.atualizar_lancamento(decidido, {"pago": False}, origem="tabela de lançamentos")
        sanepar(self.ana, "2026-07", "182.79", situacao="Paga")
        servicos.conciliar_agua(self.ana)
        pago.refresh_from_db()
        decidido.refresh_from_db()
        self.assertTrue(pago.pago)
        self.assertEqual(pago.valor, Decimal("199.26"))
        self.assertFalse(decidido.pago)
        self.assertEqual(decidido.valor, Decimal("182.79"))

    def test_botao_ajustar_htmx(self):
        lancar(self.ana, date(2026, 8, 5), "Água", "300.00")
        sanepar(self.ana, "2026-09", "199.87")
        url = reverse("consumo:agua_ajustar")
        r = self.client.post(url, HTTP_HX_REQUEST="true")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(json.loads(r["HX-Trigger"])["toast"], "1 lançamento(s) de Água ajustado(s).")
        self.assertContains(r, 'id="faturas-agua"')
        r = self.client.post(url, HTTP_HX_REQUEST="true")
        self.assertEqual(json.loads(r["HX-Trigger"])["toast"], "Tudo já estava igual às faturas.")
        self.assertEqual(self.client.get(url).status_code, 405)
        r = self.client.post(reverse("consumo:luz_ajustar"), HTTP_HX_REQUEST="true")
        self.assertContains(r, 'id="faturas-luz"')

    def test_ajustar_so_mexe_no_proprio_usuario(self):
        da_bia = lancar(self.bia, date(2026, 8, 5), "Água", "300.00")
        sanepar(self.bia, "2026-09", "199.87")
        self.client.post(reverse("consumo:agua_ajustar"), HTTP_HX_REQUEST="true")
        da_bia.refresh_from_db()
        self.assertEqual(da_bia.valor, Decimal("300.00"))

    def test_ajuste_ignorado_com_dois_lancamentos(self):
        lancar(self.ana, date(2026, 8, 5), "Água", "100.00")
        lancar(self.ana, date(2026, 8, 6), "Água", "100.00")
        sanepar(self.ana, "2026-09", "199.87")
        r = self.client.post(reverse("consumo:agua_ajustar"), HTTP_HX_REQUEST="true")
        self.assertIn("Não mexi em Ago/26", json.loads(r["HX-Trigger"])["toast"])
        self.assertEqual(set(Lancamento.objects.values_list("valor", flat=True)), {Decimal("100.00")})


class LeitoresTest(TestCase):
    """Os leitores com os PDFs reais (testes_dados/)."""

    @skipUnless((DADOS / "sanepar_pdf").exists(), "PDFs reais da Sanepar ausentes")
    def test_todos_os_pdfs_da_sanepar(self):
        from consumo.leitores import sanepar_pdf
        for caminho in sorted((DADOS / "sanepar_pdf").glob("*.pdf")):
            with self.subTest(caminho.name):
                dados = sanepar_pdf.ler_fatura(sanepar_pdf.texto_pdf(caminho.read_bytes()))
                self.assertEqual(dados["referencia"], caminho.stem)
                self.assertGreater(dados["valor_total"], 0)

    @skipUnless((DADOS / "copel_pdf").exists(), "PDFs reais da Copel ausentes")
    def test_todos_os_pdfs_da_copel(self):
        for caminho in sorted((DADOS / "copel_pdf").glob("*.pdf")):
            with self.subTest(caminho.name):
                dados = servicos.ler_pdf_copel(caminho.read_bytes())
                self.assertEqual(f"{dados['referencia']}-{dados['numero_fatura']}", caminho.stem)
                self.assertAlmostEqual(sum(i["valor"] for i in dados["detalhes"]["itens"]), dados["valor_total"], 2)
                self.assertIn(dados["situacao"], ("Quitada", "Pendente"))
