"""Notas Paraná e Produtos: páginas e modais, cálculo dos produtos (EAN, brinde, variação), edição de categoria e
regras, e isolamento entre usuários."""
import json
import math
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pandas as pd
from django.test import TestCase
from django.urls import reverse

from contas.models import Usuario
from core import dados
from notas import consultas, produtos as calc
from notas.models import ItemNota, Nota, PagamentoNota, Placar, RegraCategoria

SP = ZoneInfo("America/Sao_Paulo")
LEITE = "7891000000017"
IOGURTE = "7891000000024"


def criar_nota(usuario, chave, quando, nome, itens=(), pagamentos=(), fantasia="", categoria=""):
    """itens: (ean, descrição, quantidade, preço unitário, desconto); pagamentos: (forma, valor)."""
    total = sum(Decimal(q) * Decimal(p) - Decimal(d or 0) for _, _, q, p, d in itens) or Decimal("100")
    nota = Nota.objects.create(
        usuario=usuario, chave=chave, data_emissao=datetime(*quando, tzinfo=SP), emitente_nome=nome,
        emitente_fantasia=fantasia, emitente_municipio="MARINGA", emitente_uf="PR", emitente_cnpj="00.000.000/0001-00",
        emitente_endereco="Rua A, 1", numero="123", serie="1", valor_total=total, credito=Decimal("0.10"),
        categoria=categoria)
    for seq, (ean, descricao, qtd, preco, desconto) in enumerate(itens, 1):
        ItemNota.objects.create(nota=nota, seq=seq, ean=ean, descricao=descricao, quantidade=Decimal(qtd), unidade="UN",
                                valor_unitario=Decimal(preco), valor_desconto=Decimal(desconto) if desconto else None,
                                valor_total=Decimal(qtd) * Decimal(preco), codigo=str(seq))
    for seq, (forma, valor) in enumerate(pagamentos, 1):
        PagamentoNota.objects.create(nota=nota, seq=seq, forma=forma, valor=Decimal(valor))
    return nota


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        # As regras padrão (SUPERMERCADO/MERCADO -> Mercado, POSTO -> Combustível) vêm do cadastro.
        cls.ana = Usuario.objects.create_user("ana@teste.local", "senha-forte-123", nome="Ana")
        cls.bruno = Usuario.objects.create_user("bruno@teste.local", "senha-forte-123", nome="Bruno")
        cls.n1 = criar_nota(cls.ana, "1" * 44, (2026, 1, 10, 10, 0), "SUPERMERCADO ALFA LTDA",
                            [(LEITE, "LEITE X 1L", "2", "5.00", None), ("SEM GTIN", "ARROZ  TIPO 1", "1", "10.00", None)],
                            [("Cartão de Crédito", "20.00")])
        cls.n2 = criar_nota(cls.ana, "2" * 44, (2026, 3, 15, 18, 30), "BETA COMERCIO LTDA", fantasia="MERCADO BETA",
                            itens=[(LEITE, "LEITE X INTEGRAL 1L", "1", "5.50", None),
                                   (IOGURTE, "IOGURTE Y 170G", "1", "3.00", "2.99"),  # brinde: 99,7% de desconto
                                   ("", "arroz tipo 1", "1", "11.00", None)],
                            pagamentos=[("Dinheiro", "3.51"), ("Cartão de Débito", "13.00")])
        cls.n3 = criar_nota(cls.ana, "3" * 44, (2026, 5, 20, 9, 15), "SUPERMERCADO ALFA LTDA",
                            [(LEITE, "LEITE X 1L", "1", "6.00", "0.60")], [("Cartão de Crédito", "5.40")])
        cls.n4 = criar_nota(cls.ana, "4" * 44, (2026, 6, 1, 7, 0), "POSTO GAMA", [("", "GASOLINA C", "20", "6.00", None)],
                            [("Cartão de Crédito", "120.00")])
        cls.nb = criar_nota(cls.bruno, "9" * 44, (2026, 4, 2, 12, 0), "SUPERMERCADO DO BRUNO",
                            [(LEITE, "LEITE X 1L", "1", "4.00", None)], [("Pix", "4.00")])
        Placar.objects.create(usuario=cls.ana, dados={"Saldo disponível": "R$ 44,02", "Total de créditos": "R$ 1.282,12",
                                                      "Total de prêmios de sorteios": "R$ 90,00",
                                                      "Total de notas recebidas": "4"})

    def setUp(self):
        self.client.force_login(self.ana)

    def itens(self, usuario=None):
        usuario = usuario or self.ana
        return calc.preparar(dados.itens_df(usuario), dados.notas_df(usuario))


class PaginasTest(Base):
    def test_pagina_notas(self):
        r = self.client.get(reverse("notas:notas"))
        self.assertContains(r, "Consumo por mês")
        self.assertContains(r, "não</b> somam nas saídas")
        self.assertContains(r, "R$ 1.282,12")  # placar
        self.assertContains(r, "Regras de categorização")
        self.assertContains(r, "SUPERMERCADO ALFA LTDA")  # tabela (JSON embutido)
        self.assertContains(r, "/notas/detalhe/?tipo=categoria")  # gráficos clicáveis

    def test_pagina_notas_com_periodo(self):
        r = self.client.get(reverse("notas:notas"), {"de": "2026-05", "ate": "2026-06"})
        self.assertEqual(r.context["quantidade"], 2)
        self.assertAlmostEqual(r.context["total"], 125.40)

    def test_detalhe_categoria_loja_forma(self):
        url = reverse("notas:detalhe")
        r = self.client.get(url, {"tipo": "categoria", "alvo": "Mercado"})
        self.assertContains(r, "Categoria: Mercado")
        self.assertContains(r, "O que mais comprei")
        self.assertContains(r, "Compra por compra")
        self.assertEqual(r.context["compras"], 3)
        self.assertEqual([x["titulo"] for x in r.context["rankings"]], ["Onde mais gastei", "Como paguei"])
        for grao in ("dia", "mes", "ano"):
            r = self.client.get(url, {"tipo": "loja", "alvo": "MERCADO BETA", "grao": grao, "escopo": "tudo"})
            self.assertContains(r, "Loja: MERCADO BETA")
        r = self.client.get(url, {"tipo": "loja", "alvo": "MERCADO BETA"})
        self.assertEqual([x["titulo"] for x in r.context["rankings"]], ["Como paguei", "Dia da semana"])
        # por forma de pagamento vale o valor pago naquela forma, não o total da nota
        r = self.client.get(url, {"tipo": "forma", "alvo": "Dinheiro"})
        self.assertContains(r, "Forma de pagamento: Dinheiro")
        self.assertAlmostEqual(r.context["total"], 3.51)
        self.assertContains(r, "parte da nota de R$ 16,51")

    def test_detalhe_periodo_sem_compras(self):
        r = self.client.get(reverse("notas:detalhe"), {"tipo": "loja", "alvo": "POSTO GAMA", "de": "2026-01", "ate": "2026-02"})
        self.assertContains(r, "Nenhuma compra")

    def test_detalhe_compras_sob_demanda(self):
        meses, proximo = consultas.compra_por_compra(consultas.carregar(self.ana).compras, "categoria", self.itens(), limite=1)
        self.assertEqual(len(meses), 1)
        self.assertEqual(meses[0]["rotulo"], "Junho 2026")
        self.assertEqual(proximo, "2026-05")
        r = self.client.get(reverse("notas:detalhe_compras"), {"tipo": "categoria", "alvo": "Mercado", "antes": "2026-04"})
        self.assertContains(r, "Março 2026")
        self.assertNotContains(r, "Maio 2026")

    def test_detalhe_sem_alvo_ou_tipo_invalido(self):
        url = reverse("notas:detalhe")
        self.assertEqual(self.client.get(url, {"tipo": "categoria"}).status_code, 404)
        self.assertEqual(self.client.get(url, {"tipo": "x", "alvo": "Mercado"}).status_code, 404)
        self.assertEqual(self.client.get(url, {"tipo": "categoria", "alvo": "Não existe"}).status_code, 404)

    def test_nota_completa(self):
        r = self.client.get(reverse("notas:nota", args=[self.n2.pk]))
        self.assertContains(r, "MERCADO BETA")
        self.assertContains(r, "BETA COMERCIO LTDA")
        self.assertContains(r, "IOGURTE Y 170G")
        self.assertContains(r, "Dinheiro")
        self.assertContains(r, "2222 2222")  # chave em blocos de 4
        self.assertContains(r, "/produtos/historico/?grupo=ean%3A" + IOGURTE)

    def test_pagina_produtos(self):
        r = self.client.get(reverse("notas:produtos"))
        self.assertContains(r, "Todos os produtos")
        self.assertEqual(r.context["total"], 4)  # leite (3 compras, 2 lojas), arroz (2 nomes), iogurte, gasolina
        self.assertEqual(r.context["vezes"], 7)
        self.assertEqual(r.context["caros"], "1 de 1")
        # filtros chegam também como parcial (HTMX)
        r = self.client.get(reverse("notas:produtos"), {"busca": "leite"}, HTTP_HX_REQUEST="true",
                            HTTP_HX_TARGET="produtos-resultado")
        self.assertTemplateUsed(r, "notas/_produtos_resultado.html")
        self.assertTemplateNotUsed(r, "core/base.html")
        self.assertEqual(r.context["total"], 1)

    def test_dados_dos_produtos(self):
        r = self.client.get(reverse("notas:produtos_dados"))
        linhas = {l["grupo"]: l for l in r.json()["linhas"]}
        leite = linhas[f"ean:{LEITE}"]
        self.assertEqual((leite["compras"], leite["lojas"], leite["produto"]), (3, 2, "LEITE X 1L"))
        self.assertEqual(leite["precos"], [5.0, 5.5, 5.4])
        self.assertAlmostEqual(leite["variacao"], 0.08)
        r = self.client.get(reverse("notas:produtos_dados"), {"repetidos": "1", "loja": "SUPERMERCADO ALFA LTDA"})
        self.assertEqual([l["produto"] for l in r.json()["linhas"]], ["LEITE X 1L"])

    def test_historico_de_preco(self):
        url = reverse("notas:produto")
        r = self.client.get(url, {"grupo": f"ean:{LEITE}"})
        self.assertContains(r, "Histórico de preço")
        self.assertContains(r, "Preço pago ao longo do tempo")
        self.assertContains(r, "Último preço em cada loja")
        self.assertEqual(r.context["comprado"], "3 vezes")
        self.assertEqual(r.context["delta_ultimo"], "▲ 8,0% desde 01/2026")
        self.assertEqual(r.context["classe_ultimo"], "sobe-ruim")
        # o clique do gráfico manda ?alvo=; "juntar" acrescenta itens pelo nome
        r = self.client.get(url, {"alvo": f"ean:{LEITE}", "juntar": "iogurte"})
        self.assertEqual(r.context["comprado"], "4 vezes")
        self.assertEqual(self.client.get(url, {"grupo": "ean:000"}).status_code, 404)

    def test_voltar_so_aceita_caminhos_do_sistema(self):
        url = reverse("notas:produto")
        r = self.client.get(url, {"grupo": f"ean:{LEITE}", "voltar": "https://exemplo.com/notas/"})
        self.assertNotContains(r, "np-voltar")
        r = self.client.get(url, {"grupo": f"ean:{LEITE}", "voltar": "//exemplo.com/notas/"})
        self.assertNotContains(r, "np-voltar")
        r = self.client.get(url, {"grupo": f"ean:{LEITE}", "voltar": "/notas/detalhe/?tipo=categoria&alvo=Mercado"})
        self.assertContains(r, "np-voltar")

    def test_usuario_sem_notas(self):
        self.client.force_login(Usuario.objects.create_user("nova@teste.local", "senha-forte-123", nome="Nova"))
        self.assertContains(self.client.get(reverse("notas:notas")), "Nenhuma nota importada ainda")
        self.assertContains(self.client.get(reverse("notas:produtos")), "Nenhum produto ainda")
        self.assertEqual(self.client.get(reverse("notas:produtos_dados")).json(), {"linhas": []})
        self.assertEqual(self.client.get(reverse("notas:detalhe"), {"tipo": "categoria", "alvo": "Mercado"}).status_code, 404)

    def test_exige_login(self):
        self.client.logout()
        r = self.client.get(reverse("notas:notas"))
        self.assertEqual(r.status_code, 302)


class CalculoProdutosTest(Base):
    def test_mesmo_ean_em_lojas_diferentes_e_um_produto(self):
        itens = self.itens()
        leite = itens[itens["grupo"] == f"ean:{LEITE}"]
        self.assertEqual(len(leite), 3)
        self.assertEqual(leite["loja"].nunique(), 2)
        self.assertEqual(set(leite["produto"]), {"LEITE X 1L"})  # o nome mais frequente

    def test_sem_ean_junta_pelo_nome_normalizado(self):
        itens = self.itens()
        arroz = itens[itens["descricao"].str.contains("arroz", case=False)]
        self.assertEqual(arroz["grupo"].unique().tolist(), ["nome:ARROZ TIPO 1"])

    def test_ean_invalido_nao_agrupa(self):
        grupos = calc.grupos(pd.Series(["0000000000000", "123", "SEM GTIN", LEITE]),
                             pd.Series(["a", "b", "c", "d"]))
        self.assertEqual(grupos.tolist(), ["nome:A", "nome:B", "nome:C", f"ean:{LEITE}"])

    def test_preco_pago_por_unidade_com_desconto(self):
        itens = self.itens()
        terceiro = itens[(itens["grupo"] == f"ean:{LEITE}") & (itens["nota_id"] == self.n3.pk)].iloc[0]
        self.assertAlmostEqual(terceiro["preco"], 5.40)  # (6,00 − 0,60) / 1
        primeiro = itens[(itens["grupo"] == f"ean:{LEITE}") & (itens["nota_id"] == self.n1.pk)].iloc[0]
        self.assertAlmostEqual(primeiro["preco"], 5.00)  # 10,00 / 2

    def test_brinde_usa_preco_de_etiqueta(self):
        iogurte = self.itens().query("grupo == @g", local_dict={"g": f"ean:{IOGURTE}"}).iloc[0]
        self.assertTrue(iogurte["brinde"])
        self.assertAlmostEqual(iogurte["preco"], 3.00)
        self.assertAlmostEqual(iogurte["pago"], 0.01)  # o gasto continua sendo o que foi pago

    def test_variacao_e_resumo(self):
        tabela = calc.resumo(self.itens())
        leite = tabela.loc[f"ean:{LEITE}"]
        self.assertEqual((leite["primeiro"], leite["ultimo"], leite["menor"], leite["maior"]), (5.0, 5.4, 5.0, 5.5))
        self.assertAlmostEqual(leite["variacao"], 0.08)
        self.assertAlmostEqual(leite["gasto"], 10 + 5.5 + 5.4)
        self.assertTrue(math.isnan(tabela.loc[f"ean:{IOGURTE}", "variacao"]))  # uma compra só
        v = calc.variacoes(tabela)
        self.assertEqual(v["subiram"].index.tolist(), [f"ean:{LEITE}"])  # o arroz (2 compras) fica de fora
        self.assertEqual((len(v["cairam"]), v["iguais"]), (0, 0))

    def test_periodo_limitado_aos_meses_com_dados(self):
        primeiro, ultimo = pd.Period("2025-08", "M"), pd.Period("2026-10", "M")
        p = consultas.periodo({}, primeiro, ultimo)
        self.assertEqual((p.de, p.ate, p.n_meses), ("2025-11", "2026-10", 12))
        p = consultas.periodo({"de": "2026-09", "ate": "2020-01"}, primeiro, ultimo)
        self.assertEqual((p.de, p.ate), ("2025-08", "2026-09"))
        p = consultas.periodo({"de": "lixo", "ate": "2026-13"}, primeiro, ultimo, padrao=None)
        self.assertEqual((p.de, p.ate), ("2025-08", "2026-10"))


class EdicaoTest(Base):
    def post(self, nome, corpo):
        return self.client.post(reverse(nome), json.dumps(corpo), content_type="application/json")

    def test_categoria_manual_e_volta_para_a_regra(self):
        r = self.post("notas:api_categoria", {"id": self.n1.pk, "categoria": "Feira"})
        self.assertEqual(r.json(), {"ok": True, "categoria": "Feira", "auto": False})
        self.n1.refresh_from_db()
        self.assertEqual(self.n1.categoria, "Feira")
        notas = dados.notas_df(self.ana).set_index("id")
        self.assertEqual(notas.at[self.n1.pk, "categoria"], "Feira")  # a manual vence a regra
        r = self.post("notas:api_categoria", {"id": self.n1.pk, "categoria": ""})
        self.assertEqual(r.json(), {"ok": True, "categoria": "Mercado", "auto": True})

    def test_categoria_valida_dados(self):
        self.assertEqual(self.client.post(reverse("notas:api_categoria"), "{x", content_type="application/json").status_code, 400)
        self.assertEqual(self.post("notas:api_categoria", {"categoria": "X"}).status_code, 400)
        self.assertEqual(self.post("notas:api_categoria", {"id": self.n1.pk, "categoria": "x" * 61}).status_code, 400)
        self.assertEqual(self.client.get(reverse("notas:api_categoria")).status_code, 405)

    def test_salvar_regras(self):
        r = self.post("notas:api_regras", {"regras": [{"padrao": " BETA ", "categoria": "Feira"},
                                                      {"padrao": "ALFA", "categoria": "Atacado"},
                                                      {"padrao": "", "categoria": ""}]})
        self.assertEqual(r.json(), {"ok": True, "total": 2})
        self.assertEqual(dados.regras(self.ana), {"BETA": "Feira", "ALFA": "Atacado"})
        categorias = dados.notas_df(self.ana).set_index("id")["categoria"]
        self.assertEqual(categorias[self.n2.pk], "Feira")
        self.assertEqual(categorias[self.n4.pk], "Outros")  # POSTO saiu das regras

    def test_regras_invalidas_nao_mudam_nada(self):
        antes = dados.regras(self.ana)
        self.assertEqual(self.post("notas:api_regras", {"regras": [{"padrao": "X", "categoria": ""}]}).status_code, 400)
        r = self.post("notas:api_regras", {"regras": [{"padrao": "Posto", "categoria": "A"}, {"padrao": "POSTO", "categoria": "B"}]})
        self.assertEqual(r.status_code, 400)
        self.assertIn("mais de uma vez", r.json()["erro"])
        self.assertEqual(self.post("notas:api_regras", {"regras": "x"}).status_code, 400)
        self.assertEqual(dados.regras(self.ana), antes)


class IsolamentoTest(Base):
    """Bruno não vê nem altera nada da Ana."""

    def setUp(self):
        self.client.force_login(self.bruno)

    def test_nao_ve_notas_da_ana(self):
        r = self.client.get(reverse("notas:notas"))
        self.assertContains(r, "SUPERMERCADO DO BRUNO")
        self.assertNotContains(r, "SUPERMERCADO ALFA")
        self.assertNotContains(r, "R$ 1.282,12")  # placar da Ana
        self.assertEqual(self.client.get(reverse("notas:nota", args=[self.n1.pk])).status_code, 404)
        url = reverse("notas:detalhe")
        self.assertEqual(self.client.get(url, {"tipo": "loja", "alvo": "MERCADO BETA"}).status_code, 404)
        self.assertEqual(self.client.get(url, {"tipo": "forma", "alvo": "Dinheiro"}).status_code, 404)
        r = self.client.get(url, {"tipo": "categoria", "alvo": "Mercado"})
        self.assertEqual(r.context["compras"], 1)
        self.assertNotContains(r, "ALFA")

    def test_nao_ve_produtos_da_ana(self):
        linhas = self.client.get(reverse("notas:produtos_dados")).json()["linhas"]
        self.assertEqual([(l["produto"], l["compras"]) for l in linhas], [("LEITE X 1L", 1)])
        self.assertEqual(self.client.get(reverse("notas:produto"), {"grupo": f"ean:{IOGURTE}"}).status_code, 404)
        r = self.client.get(reverse("notas:produto"), {"grupo": f"ean:{LEITE}", "juntar": "arroz"})
        self.assertEqual(r.context["comprado"], "1 vez")  # o mesmo EAN, mas só as compras do Bruno

    def test_nao_altera_categoria_nem_regras_da_ana(self):
        r = self.client.post(reverse("notas:api_categoria"), json.dumps({"id": self.n1.pk, "categoria": "Hackeado"}),
                             content_type="application/json")
        self.assertEqual(r.status_code, 404)
        self.n1.refresh_from_db()
        self.assertEqual(self.n1.categoria, "")
        antes = dados.regras(self.ana)
        self.client.post(reverse("notas:api_regras"), json.dumps({"regras": [{"padrao": "X", "categoria": "Y"}]}),
                         content_type="application/json")
        self.assertEqual(dados.regras(self.ana), antes)
        self.assertEqual(dados.regras(self.bruno), {"X": "Y"})
        self.assertEqual(RegraCategoria.objects.filter(usuario=self.ana).count(), len(antes))
