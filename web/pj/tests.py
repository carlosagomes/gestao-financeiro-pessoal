"""Módulo PJ: acesso só com pj_habilitado, cálculo do informe, conferência com o Salário e a previsão."""
from datetime import date
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from contas.models import Usuario
from financeiro.models import ENTRADA, HistoricoLancamento, Lancamento
from pj import servicos
from pj.models import Informe


def salario(usuario, data, valor, descricao="Pagamento PJ", pago=True, banco="NuBank"):
    return Lancamento.objects.create(usuario=usuario, data=data, movimentacao=ENTRADA, categoria="Salário",
                                     valor=Decimal(valor), descricao=descricao, pago=pago, banco=banco)


def informe(usuario, mes, horas=180.0, valor_hora="118.58", desconto="273.68", previsao=False, **extra):
    return Informe.objects.create(usuario=usuario, mes=mes, horas=horas, valor_hora=Decimal(valor_hora),
                                  desconto=Decimal(desconto), previsao=previsao, **extra)


class Base(TestCase):
    def setUp(self):
        self.pj = Usuario.objects.create_user("pj@teste.local", "senha-forte-123", nome="Pj Teste", pj_habilitado=True)
        self.comum = Usuario.objects.create_user("comum@teste.local", "senha-forte-123", nome="Comum Teste")
        self.client.force_login(self.pj)


class AcessoTest(Base):
    def test_pagina_do_usuario_pj(self):
        r = self.client.get(reverse("pj:horas"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Informes × Salário recebido")
        self.assertContains(r, "Previsão até dezembro")

    def test_404_sem_modulo_pj(self):
        self.client.force_login(self.comum)
        self.assertEqual(self.client.get(reverse("pj:horas")).status_code, 404)
        r = self.client.post(reverse("pj:horas"), {"mes": "2026-05", "horas": "10:00", "valor_hora": "100"})
        self.assertEqual(r.status_code, 404)
        self.assertFalse(Informe.objects.exists())

    def test_nao_ve_informes_de_outro_usuario(self):
        outro = Usuario.objects.create_user("outro@teste.local", "senha-forte-123", nome="Outro", pj_habilitado=True)
        informe(outro, "2026-05", valor_nf=Decimal("98765.43"))
        r = self.client.get(reverse("pj:horas") + "?mes=2026-05")
        self.assertNotContains(r, "98.765,43")
        self.assertIsNone(r.context["informe"])


class CalculoTest(Base):
    def test_valor_da_nf_do_informe(self):
        horas = servicos.ler_hhmm("181:39")
        self.assertAlmostEqual(horas, 181.65)
        i = Informe(horas=horas, horas_sobreaviso=102, valor_hora=Decimal("118.58"), desconto=Decimal("273.68"))
        self.assertEqual(i.valor_normal, 21540.06)
        self.assertEqual(i.valor_sobreaviso, 4031.72)
        self.assertEqual(i.total_bruto, 25571.78)
        self.assertEqual(i.total_calculado, 25298.10)

    def test_hhmm(self):
        self.assertEqual(servicos.hhmm(181.65), "181:39")
        self.assertEqual(servicos.hhmm(184.233333), "184:14")
        self.assertEqual(servicos.ler_hhmm("168"), 168)
        self.assertEqual(servicos.ler_hhmm("170,5"), 170.5)
        for invalido in ("18:75", "abc", "", "1:2:3"):
            with self.assertRaises(ValueError):
                servicos.ler_hhmm(invalido)

    def test_salvar_informe_pela_tela(self):
        r = self.client.post(reverse("pj:horas"), {
            "mes": "2026-05", "acao": "salvar", "horas": "181:39", "horas_sobreaviso": "102", "valor_hora": "118,58",
            "plano": "273,68", "valor_nf": ""}, follow=True)
        self.assertContains(r, "valor da NF R$ 25.298,10")
        i = Informe.objects.get(usuario=self.pj, mes="2026-05")
        self.assertEqual((i.horas, i.horas_sobreaviso, i.valor_hora, i.desconto, i.valor_nf, i.previsao),
                         (181.65, 102.0, Decimal("118.58"), Decimal("273.68"), None, False))
        self.assertEqual(i.observacao, "Informe: 181:39 h")

    def test_salvar_com_horas_invalidas(self):
        r = self.client.post(reverse("pj:horas"), {"mes": "2026-05", "acao": "salvar", "horas": "181h39",
                                                   "valor_hora": "118,58"})
        self.assertEqual(r.status_code, 400)
        self.assertContains(r, "Use o formato h:mm", status_code=400)
        self.assertContains(r, 'value="181h39"', status_code=400)
        self.assertFalse(Informe.objects.exists())

    def test_salvar_tira_a_marca_de_previsao(self):
        informe(self.pj, "2026-11", 168, previsao=True)
        self.client.post(reverse("pj:horas"), {"mes": "2026-11", "acao": "salvar", "horas": "170:00",
                                               "horas_sobreaviso": "0", "valor_hora": "118.58", "plano": "273.68",
                                               "valor_nf": "19.884,92"})
        i = Informe.objects.get(usuario=self.pj, mes="2026-11")
        self.assertFalse(i.previsao)
        self.assertEqual(i.valor_nf, Decimal("19884.92"))

    def test_conferencia_com_salario(self):
        informe(self.pj, "2026-05", 181.65, horas_sobreaviso=102, valor_nf=Decimal("25298.10"))
        informe(self.pj, "2026-06", 177.65, horas_sobreaviso=47, desconto="499.39")  # sem valor do informe
        salario(self.pj, date(2026, 5, 5), "25298.10")
        salario(self.pj, date(2026, 6, 5), "22000.00")
        linhas = {l["informe"].mes: l for l in servicos.informes_x_salario(self.pj)}
        self.assertTrue(linhas["2026-05"]["confere"])
        self.assertFalse(linhas["2026-06"]["nf_do_informe"])
        self.assertEqual(linhas["2026-06"]["valor_nf"], 22424.10)
        self.assertEqual(linhas["2026-06"]["diferenca_texto"], "−R$ 424,10")
        r = self.client.get(reverse("pj:horas"))
        self.assertContains(r, "Confere")


class PrevisaoTest(Base):
    HOJE = date(2026, 10, 6)

    def test_previsao_nunca_sobrescreve_salario_de_verdade(self):
        informe(self.pj, "2026-09", 172.35)
        real = salario(self.pj, date(2026, 11, 5), "20000.00", "Pagamento Cliente PJ")
        antiga = salario(self.pj, date(2026, 12, 5), "1000.00", "Pagamento Cliente PJ · PREVISÃO 160 h", pago=False)
        r = servicos.prever_ate_dezembro(self.pj, 168, Decimal("118.58"), Decimal("273.68"), self.HOJE)
        self.assertEqual(r["meses"], ["2026-10", "2026-11", "2026-12"])
        self.assertEqual((r["criados"], r["atualizados"], r["mantidos"]), (1, 1, ["2026-11"]))

        self.assertTrue(all(Informe.objects.get(usuario=self.pj, mes=m).previsao for m in r["meses"]))
        self.assertEqual(Informe.objects.get(usuario=self.pj, mes="2026-10").total_calculado, 19647.76)

        outubro = Lancamento.objects.get(usuario=self.pj, data__month=10, categoria="Salário")
        self.assertEqual((outubro.data, outubro.movimentacao, outubro.valor, outubro.pago, outubro.descricao,
                          outubro.banco),
                         (date(2026, 10, 5), ENTRADA, Decimal("19647.76"), False, "Pagamento PJ · PREVISÃO 168 h",
                          "NuBank"))
        real.refresh_from_db()
        self.assertEqual((real.valor, real.descricao, real.pago), (Decimal("20000.00"), "Pagamento Cliente PJ", True))
        self.assertEqual(Lancamento.objects.filter(usuario=self.pj, data__month=11).count(), 1)
        antiga.refresh_from_db()
        self.assertEqual((antiga.valor, antiga.descricao, antiga.pago),
                         (Decimal("19647.76"), "Pagamento Cliente PJ · PREVISÃO 168 h", False))
        self.assertTrue(HistoricoLancamento.objects.filter(lancamento_id=antiga.id, origem="previsão PJ").exists())

        # de novo, com os mesmos números: nada muda
        r = servicos.prever_ate_dezembro(self.pj, 168, Decimal("118.58"), Decimal("273.68"), self.HOJE)
        self.assertEqual((r["criados"], r["atualizados"]), (0, 0))

    def test_mes_com_informe_de_verdade_fica_de_fora(self):
        informe(self.pj, "2026-10", 175, valor_nf=Decimal("20000"))
        r = servicos.prever_ate_dezembro(self.pj, 168, Decimal("118.58"), Decimal("0"), self.HOJE)
        self.assertEqual(r["meses"], ["2026-11", "2026-12"])
        self.assertEqual(r["com_informe"], ["2026-10"])
        self.assertFalse(Informe.objects.get(mes="2026-10").previsao)
        self.assertFalse(Lancamento.objects.filter(data__month=10).exists())

    def test_previsao_pela_tela_marca_linhas(self):
        informe(self.pj, "2026-01", 180)
        r = self.client.post(reverse("pj:horas"), {"acao": "prever", "horas_previsao": "168",
                                                   "valor_hora_previsao": "118,58", "plano_previsao": "273,68"},
                             follow=True)
        self.assertContains(r, "Previsão gravada para")
        self.assertContains(r, '<span class="etiqueta roxo">Previsão</span>')
        r = self.client.post(reverse("pj:horas"), {"acao": "prever", "horas_previsao": "x", "valor_hora_previsao": "1"})
        self.assertEqual(r.status_code, 400)

    def test_previsao_isolada_por_usuario(self):
        outro = Usuario.objects.create_user("outro@teste.local", "senha-forte-123", nome="Outro", pj_habilitado=True)
        salario(outro, date(2026, 12, 5), "1000.00", "Pagamento PJ · PREVISÃO 100 h", pago=False)
        servicos.prever_ate_dezembro(self.pj, 168, Decimal("118.58"), Decimal("0"), self.HOJE)
        self.assertEqual(Lancamento.objects.get(usuario=outro).valor, Decimal("1000.00"))
