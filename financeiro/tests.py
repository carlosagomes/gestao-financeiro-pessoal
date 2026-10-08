import json
from datetime import date, datetime, timezone as tz
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from contas.models import Usuario
from financeiro import consultas, servicos
from financeiro.models import ENTRADA, SAIDA, Banco, Categoria, HistoricoLancamento, Lancamento
from notas.models import Nota

ANO = 2025  # ano passado inteiro: o status dos meses não depende de hoje


def lanc(usuario, dia: date, mov: str, valor, pago=False, categoria="Outros", descricao="", banco="NuBank"):
    return Lancamento.objects.create(usuario=usuario, data=dia, movimentacao=mov, valor=Decimal(str(valor)),
                                     pago=pago, categoria=categoria, descricao=descricao, banco=banco)


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.a = Usuario.objects.create_user("a@teste.local", "senha-a-123", nome="Ana Teste")
        cls.b = Usuario.objects.create_user("b@teste.local", "senha-b-123", nome="Bruno Teste")
        # A, 2025: jan com uma conta pendente, fev tudo pago, mar só a entrada (previsão)
        cls.salario_jan = lanc(cls.a, date(ANO, 1, 5), ENTRADA, 1000, True, "Salário", "Salário jan")
        cls.cartao_jan = lanc(cls.a, date(ANO, 1, 10), SAIDA, 300, True, "Cartão", "Fatura jan")
        cls.luz_jan = lanc(cls.a, date(ANO, 1, 31), SAIDA, 200, False, "Luz", "Conta de luz")
        lanc(cls.a, date(ANO, 2, 5), ENTRADA, 1000, False, "Salário")
        cls.cartao_fev = lanc(cls.a, date(ANO, 2, 10), SAIDA, 500, True, "Cartão", "Fatura fev")
        lanc(cls.a, date(ANO, 3, 5), ENTRADA, 800, False, "Salário", "Previsto")
        # B: valores que nunca podem aparecer para A
        cls.do_b = lanc(cls.b, date(ANO, 1, 7), ENTRADA, 99999, True, "Salário", "Segredo do B")
        lanc(cls.b, date(ANO, 1, 8), SAIDA, 5555, True, "Cartão", "Cartão do B")

    def setUp(self):
        self.client.force_login(self.a)

    def api(self, metodo, url, corpo=None):
        return getattr(self.client, metodo)(url, data=json.dumps(corpo) if corpo is not None else None,
                                            content_type="application/json")


class ResumoTest(Base):
    def test_regras_da_planilha(self):
        r = consultas.resumo_anual(self.a, ANO)
        t = r["total"]
        self.assertEqual(t["entradas"], Decimal("2800"))  # entrada conta tudo, paga ou não
        self.assertEqual(t["saidas"], Decimal("800"))  # saída só a paga
        self.assertEqual(t["a_pagar"], Decimal("200"))
        self.assertEqual(t["saldo"], Decimal("2000"))  # entrada − saída paga
        jan, fev, mar, abr = r["meses"][:4]
        self.assertEqual((jan["saidas"], jan["a_pagar"], jan["saldo"]), (Decimal("300"), Decimal("200"), Decimal("700")))
        self.assertEqual([m["status"] for m in (jan, fev, mar, abr)], ["pendente", "lancado", "previsao", ""])
        self.assertEqual(jan["pendentes"], 1)

    def test_mes_futuro_e_previsao(self):
        self.assertEqual(consultas.status_mes(ANO, 5, {"n": 2, "n_saidas": 1, "pendentes": 0}, date(ANO, 4, 1)),
                         "previsao")

    def test_notas_nunca_somam(self):
        Nota.objects.create(usuario=self.a, chave="1" * 44, data_emissao=datetime(ANO, 1, 15, tzinfo=tz.utc),
                            valor_total=Decimal("777.77"))
        self.assertEqual(consultas.resumo_anual(self.a, ANO)["total"]["saidas"], Decimal("800"))

    def test_pagina(self):
        r = self.client.get(reverse("financeiro:resumo"), {"ano": ANO})
        self.assertEqual(r.status_code, 200)
        for texto in ("R$ 2.800,00", "R$ 800,00", "R$ 2.000,00", "R$ 200,00", "TOTAL ANUAL", "Pendente (1)",
                      "Previsão", "Lançado", "Entradas × saídas", "Saídas por categoria"):
            self.assertContains(r, texto)
        self.assertNotContains(r, "99.999")
        self.assertNotContains(r, "2.025")  # ano sem separador de milhar (nem em URLs)

    def test_ano_sem_dados(self):
        r = self.client.get(reverse("financeiro:resumo"), {"ano": 2019})
        self.assertContains(r, "Nenhum lançamento em 2019")

    def test_modal_mes(self):
        r = self.client.get(reverse("financeiro:resumo_mes"), {"ano": ANO, "alvo": 1})
        self.assertContains(r, "Janeiro/2025")
        self.assertContains(r, "Fatura jan")
        self.assertContains(r, "Conta de luz")
        self.assertNotContains(r, "Segredo do B")
        self.assertEqual(self.client.get(reverse("financeiro:resumo_mes"), {"ano": ANO, "alvo": 13}).status_code, 404)

    def test_modal_categoria(self):
        r = self.client.get(reverse("financeiro:resumo_categoria"), {"ano": ANO, "alvo": "Cartão"})
        self.assertContains(r, "Fatura jan")
        self.assertContains(r, "Fatura fev")
        self.assertContains(r, "R$ 800,00")
        self.assertNotContains(r, "Cartão do B")
        self.assertContains(r, "busca=Cart%C3%A3o")

    def test_saidas_por_categoria_so_pagas(self):
        self.assertEqual(consultas.saidas_por_categoria(self.a, ANO), [("Cartão", Decimal("800.00"))])

    def test_exige_login(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse("financeiro:resumo")).status_code, 302)
        self.assertEqual(self.client.get(reverse("financeiro:api")).status_code, 302)


class LancamentosTest(Base):
    def test_pagina(self):
        r = self.client.get(reverse("financeiro:lancamentos"), {"ano": ANO, "mes": 1})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Janeiro/2025")
        self.assertContains(r, "R$ 1.000,00")  # entradas
        self.assertContains(r, "R$ 300,00")  # saídas pagas
        self.assertContains(r, "Fatura jan")  # dados iniciais da tabela
        self.assertNotContains(r, "Segredo do B")

    def test_filtros_padrao_mes_atual(self):
        f = consultas.ler_filtros({})
        hoje = date.today()
        self.assertEqual((f.ano, f.mes, f.movimentacao, f.situacao), (hoje.year, hoje.month, "", ""))
        self.assertEqual(consultas.ler_filtros({"mes": "0", "mov": "x", "situacao": "y"}).mes, 0)

    def test_api_lista_filtros_e_totais(self):
        url = reverse("financeiro:api")
        r = self.client.get(url, {"ano": ANO, "mes": 0, "mov": SAIDA, "situacao": "a-pagar"}).json()
        self.assertEqual([l["id"] for l in r["linhas"]], [self.luz_jan.id])
        self.assertEqual(r["totais"]["a_pagar"], 200.0)
        r = self.client.get(url, {"ano": ANO, "mes": 0, "busca": "fatura"}).json()
        self.assertEqual({l["id"] for l in r["linhas"]}, {self.cartao_jan.id, self.cartao_fev.id})
        r = self.client.get(url, {"ano": ANO, "mes": 1}).json()
        self.assertEqual(r["totais"]["entradas"], 1000.0)
        self.assertEqual(r["totais"]["saidas"], 300.0)
        self.assertEqual(r["totais"]["saldo"], 700.0)
        self.assertEqual(r["contexto"]["padrao"]["data"], f"{ANO}-01-05")
        self.assertEqual(r["contexto"]["anterior"]["nome"], "dezembro")
        self.assertIn("Luz", r["opcoes"]["categorias"][SAIDA])

    def test_api_criar(self):
        r = self.api("post", reverse("financeiro:api"), {"data": f"{ANO}-04-05", "movimentacao": SAIDA,
                                                         "banco": "Itaú", "categoria": "Outros", "valor": "12,5",
                                                         "descricao": " Nova ", "pago": False})
        self.assertEqual(r.status_code, 201)
        novo = Lancamento.objects.get(pk=r.json()["linha"]["id"])
        self.assertEqual((novo.usuario, novo.valor, novo.descricao, novo.pago), (self.a, Decimal("12.50"), "Nova", False))
        h = HistoricoLancamento.objects.get(lancamento_id=novo.id)
        self.assertEqual((h.campo, h.origem), ("criado", "tabela de lançamentos"))

    def test_api_criar_valida(self):
        base = {"data": f"{ANO}-04-05", "movimentacao": SAIDA, "categoria": "Outros", "valor": 10}
        for troca, mensagem in [({"valor": -1}, "negativo"), ({"valor": "abc"}, "Valor inválido"),
                                ({"valor": "NaN"}, "Valor inválido"), ({"valor": 1e12}, "alto demais"),
                                ({"data": "31/02/2025"}, "Data inválida"), ({"movimentacao": "Outra"}, "Movimentação"),
                                ({"categoria": ""}, "categoria"), ({"descricao": "x" * 300}, "Descrição")]:
            r = self.api("post", reverse("financeiro:api"), {**base, **troca})
            self.assertEqual(r.status_code, 400, troca)
            self.assertIn(mensagem, r.json()["erro"])
        r = self.client.post(reverse("financeiro:api"), data="{não é json", content_type="application/json")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(Lancamento.objects.filter(usuario=self.a).count(), 6)

    def test_api_atualizar_grava_historico(self):
        url = reverse("financeiro:api_item", args=[self.luz_jan.id])
        r = self.api("patch", url, {"valor": 250, "pago": True})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(sorted(r.json()["mudou"]), ["pago", "valor"])
        self.luz_jan.refresh_from_db()
        self.assertEqual((self.luz_jan.valor, self.luz_jan.pago), (Decimal("250.00"), True))
        historico = {h.campo: (h.antes, h.depois, h.origem) for h in HistoricoLancamento.objects.filter(lancamento_id=self.luz_jan.id)}
        self.assertEqual(historico["valor"], ("200.00", "250.00", "tabela de lançamentos"))
        self.assertEqual(historico["pago"], ("não", "sim", "tabela de lançamentos"))
        # sem mudança: nada novo no histórico
        r = self.api("patch", url, {"valor": "250.00"})
        self.assertEqual(r.json()["mudou"], [])
        self.assertEqual(HistoricoLancamento.objects.filter(lancamento_id=self.luz_jan.id).count(), 2)

    def test_api_atualizar_invalido_nao_muda(self):
        url = reverse("financeiro:api_item", args=[self.luz_jan.id])
        r = self.api("patch", url, {"valor": -5, "descricao": "não pode gravar"})
        self.assertEqual(r.status_code, 400)
        self.luz_jan.refresh_from_db()
        self.assertEqual((self.luz_jan.valor, self.luz_jan.descricao), (Decimal("200.00"), "Conta de luz"))
        self.assertFalse(HistoricoLancamento.objects.exists())

    def test_api_excluir(self):
        r = self.client.delete(reverse("financeiro:api_item", args=[self.luz_jan.id]))
        self.assertEqual(r.json(), {"excluidos": 1})
        r = self.api("post", reverse("financeiro:api_excluir"), {"ids": [self.cartao_jan.id, self.cartao_fev.id]})
        self.assertEqual(r.json(), {"excluidos": 2})
        self.assertFalse(Lancamento.objects.filter(id__in=[self.luz_jan.id, self.cartao_jan.id, self.cartao_fev.id]).exists())
        self.assertEqual(HistoricoLancamento.objects.filter(campo="excluído", origem="tabela de lançamentos").count(), 3)
        self.assertEqual(self.api("post", reverse("financeiro:api_excluir"), {"ids": ["x"]}).status_code, 400)
        self.assertEqual(self.api("post", reverse("financeiro:api_excluir"), {"ids": []}).status_code, 400)

    def test_api_marcar_pagas(self):
        r = self.api("post", reverse("financeiro:api_marcar_pagas"), {"ids": [self.luz_jan.id, self.cartao_jan.id]})
        self.assertEqual(r.json()["marcados"], 1)  # a do cartão já estava paga
        self.luz_jan.refresh_from_db()
        self.assertTrue(self.luz_jan.pago)
        h = HistoricoLancamento.objects.get(lancamento_id=self.luz_jan.id)
        self.assertEqual((h.campo, h.origem), ("pago", "botão marcar como pagas"))

    def test_api_copiar_mes(self):
        url = reverse("financeiro:api_copiar_mes")
        # abril está vazio: copia março (1 lançamento) sem perguntar
        r = self.api("post", url, {"ano": ANO, "mes": 4})
        self.assertEqual((r.status_code, r.json()["copiados"]), (201, 1))
        # fevereiro já tem lançamentos: pede confirmação antes
        r = self.api("post", url, {"ano": ANO, "mes": 2})
        self.assertEqual(r.status_code, 409)
        self.assertTrue(r.json()["precisa_confirmar"])
        r = self.api("post", url, {"ano": ANO, "mes": 2, "confirmar": True})
        self.assertEqual(r.json()["copiados"], 3)
        copiados = Lancamento.objects.filter(usuario=self.a, data__month=2, descricao__in=["Salário jan", "Fatura jan", "Conta de luz"])
        self.assertEqual(copiados.count(), 3)
        self.assertFalse(copiados.filter(pago=True).exists())  # entram todos como não pagos
        self.assertEqual(copiados.get(descricao="Conta de luz").data, date(ANO, 2, 28))  # 31/01 -> 28/02
        self.assertEqual(HistoricoLancamento.objects.filter(origem="copiar mês", campo="criado").count(), 4)
        # mês anterior vazio
        self.assertEqual(self.api("post", url, {"ano": ANO, "mes": 7}).status_code, 400)
        self.assertEqual(self.api("post", url, {"ano": ANO, "mes": 0}).status_code, 400)

    def test_copiar_mes_virada_de_ano(self):
        lanc(self.a, date(ANO, 12, 31), SAIDA, 10, True, "Outros", "Dezembro")
        novos = servicos.copiar_mes(self.a, ANO + 1, 1)
        self.assertEqual([(n.data, n.pago) for n in novos], [(date(ANO + 1, 1, 31), False)])

    def test_metodos(self):
        self.assertEqual(self.client.get(reverse("financeiro:api_excluir")).status_code, 405)
        self.assertEqual(self.client.get(reverse("financeiro:api_item", args=[self.luz_jan.id])).status_code, 405)

    def test_cadastros(self):
        url = reverse("financeiro:cadastros")
        self.assertEqual(self.client.get(url).status_code, 200)
        r = self.client.post(url, {"tipo": "categoria", "nome": "  Pets  ", "movimentacao": SAIDA})
        self.assertIn("opcoesMudaram", json.loads(r["HX-Trigger"]))
        self.assertTrue(Categoria.objects.filter(usuario=self.a, nome="Pets", movimentacao=SAIDA).exists())
        r = self.client.post(url, {"tipo": "categoria", "nome": "pets", "movimentacao": SAIDA})
        self.assertContains(r, "já existe")
        self.assertNotIn("HX-Trigger", r)
        self.client.post(url, {"tipo": "banco", "nome": "Banco Novo"})
        self.assertTrue(Banco.objects.filter(usuario=self.a, nome="Banco Novo").exists())
        self.assertIn("Pets", consultas.opcoes(self.a)["categorias"][SAIDA])
        self.assertNotIn("Pets", consultas.opcoes(self.b)["categorias"][SAIDA])

    def test_historico_modal(self):
        self.api("patch", reverse("financeiro:api_item", args=[self.luz_jan.id]), {"valor": 210})
        servicos.excluir_lancamentos(self.a, [self.cartao_jan.id], origem="tabela de lançamentos")
        r = self.client.get(reverse("financeiro:historico"))
        self.assertContains(r, "R$ 210,00")
        self.assertContains(r, "tabela de lançamentos")
        self.assertContains(r, "10/01/2025 · Saída · Cartão · Fatura jan · R$ 300,00")
        self.assertContains(r, "(excluído)")


class IsolamentoTest(Base):
    """B nunca vê nem altera nada de A."""

    def setUp(self):
        self.client.force_login(self.b)

    def test_nao_altera_nem_exclui(self):
        url = reverse("financeiro:api_item", args=[self.luz_jan.id])
        self.assertEqual(self.api("patch", url, {"pago": True}).status_code, 404)
        self.assertEqual(self.client.delete(url).status_code, 404)
        r = self.api("post", reverse("financeiro:api_excluir"), {"ids": [self.luz_jan.id, self.cartao_jan.id]})
        self.assertEqual(r.json()["excluidos"], 0)
        r = self.api("post", reverse("financeiro:api_marcar_pagas"), {"ids": [self.luz_jan.id]})
        self.assertEqual((r.json()["marcados"], r.json()["linhas"]), (0, []))
        self.luz_jan.refresh_from_db()
        self.assertFalse(self.luz_jan.pago)
        self.assertEqual(Lancamento.objects.filter(usuario=self.a).count(), 6)
        self.assertFalse(HistoricoLancamento.objects.exists())

    def test_nao_ve(self):
        r = self.client.get(reverse("financeiro:api"), {"ano": ANO, "mes": 0}).json()
        self.assertEqual({l["descricao"] for l in r["linhas"]}, {"Segredo do B", "Cartão do B"})
        r = self.client.get(reverse("financeiro:resumo"), {"ano": ANO})
        self.assertContains(r, "R$ 99.999,00")
        self.assertNotContains(r, "R$ 2.800,00")
        r = self.client.get(reverse("financeiro:resumo_mes"), {"ano": ANO, "alvo": 1})
        self.assertNotContains(r, "Fatura jan")
        r = self.client.get(reverse("financeiro:resumo_categoria"), {"ano": ANO, "alvo": "Luz"})
        self.assertNotContains(r, "Conta de luz")
        r = self.client.get(reverse("financeiro:lancamentos"), {"ano": ANO, "mes": 0})
        self.assertNotContains(r, "Fatura jan")
        # histórico de A não aparece para B
        servicos.atualizar_lancamento(self.luz_jan, {"descricao": "Mudança da Ana"}, origem="teste")
        self.assertNotContains(self.client.get(reverse("financeiro:historico")), "Mudança da Ana")

    def test_copiar_mes_so_do_proprio_usuario(self):
        r = self.api("post", reverse("financeiro:api_copiar_mes"), {"ano": ANO, "mes": 2})
        self.assertEqual(r.json()["copiados"], 2)  # só os 2 lançamentos de janeiro do B
        self.assertEqual(Lancamento.objects.filter(usuario=self.a, data__month=2).count(), 2)
