"""Segurança: isolamento entre usuários em TODAS as rotas, acesso sem login, cabeçalhos, admin e bloqueios.

O isolamento é uma varredura: a Ana tem dados marcados com SEGREDO em todas as tabelas, e o Bruno (outro
usuário, com o módulo PJ) chama cada rota do sistema, lendo e escrevendo, inclusive com os ids e nomes da Ana.
Nada da Ana pode aparecer nas respostas nem mudar no banco. Rota nova sem entrada em ROTAS faz o teste falhar:
inclua a rota aqui (com os parâmetros que apontam para os dados da Ana) ao criar uma página ou API.

  DJANGO_TEST_DB=test_gfp_core .venv/bin/python manage.py test core --noinput
"""
import json
import shutil
import tempfile
from datetime import datetime, time
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from django.core.cache import cache
from django.core.files.base import ContentFile
from django.forms.models import model_to_dict
from django.test import TestCase, override_settings
from django.urls import URLPattern, URLResolver, get_resolver, reverse

from consumo.models import FaturaCopel, FaturaSanepar
from contas.models import Usuario
from financeiro.models import Banco, Categoria, HistoricoLancamento, Lancamento
from integracoes.models import COPEL, NOTAPARANA, SANEPAR, Credencial, SessaoServico, Tarefa
from notas.models import ItemNota, Nota, PagamentoNota, PeriodoNP, Placar, RegraCategoria
from pj.models import Informe

SENHA = "senha-bem-forte-123"
SP = ZoneInfo("America/Sao_Paulo")
LEITE = "7891000000017"  # o mesmo produto nas notas dos dois: o histórico de preço não pode juntar os dois
EXCLUSIVO = "7891000000999"  # só a Ana comprou
CPF_ANA, CPF_BRUNO = "52998224725", "11144477735"
MEDIA = tempfile.mkdtemp(prefix="gfp-testes-")


def criar_nota(usuario, chave, quando, loja, itens, forma, categoria=""):
    total = sum(Decimal(preco) for _, _, preco in itens)
    nota = Nota.objects.create(usuario=usuario, chave=chave, data_emissao=datetime(*quando, tzinfo=SP),
                               emitente_nome=loja, emitente_municipio="MARINGA", emitente_uf="PR",
                               valor_total=total, credito=Decimal("0.10"), categoria=categoria)
    for seq, (ean, descricao, preco) in enumerate(itens, 1):
        ItemNota.objects.create(nota=nota, seq=seq, ean=ean, descricao=descricao, quantidade=1, unidade="UN",
                                valor_unitario=Decimal(preco), valor_total=Decimal(preco))
    PagamentoNota.objects.create(nota=nota, seq=1, forma=forma, valor=total)
    return nota


def credencial(usuario, servico, cpf):
    obj = Credencial(usuario=usuario, servico=servico)
    obj.login, obj.senha = cpf, f"senha-site-{usuario.pk}"
    obj.save()
    return obj


def sessao(usuario, servico):
    obj = SessaoServico(usuario=usuario, servico=servico)
    obj.estado = {"cookies": [{"name": "sessao", "value": f"cookie-{usuario.pk}"}], "origins": []}
    obj.save()
    return obj


@override_settings(MEDIA_ROOT=MEDIA)
class IsolamentoEntreUsuariosTest(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    @classmethod
    def setUpTestData(cls):
        cls.ana = Usuario.objects.create_user("ana@teste.local", SENHA, nome="Ana", pj_habilitado=True)
        cls.bruno = Usuario.objects.create_user("bruno@teste.local", SENHA, nome="Bruno", pj_habilitado=True)
        ana, bruno = cls.ana, cls.bruno

        # --- dados da Ana: todos marcados (texto SEGREDO ou valores que só ela tem) ---
        Categoria.objects.create(usuario=ana, nome="Cat SEGREDO", movimentacao="Saída")
        Banco.objects.create(usuario=ana, nome="Banco SEGREDO")
        cls.lanc_ana = Lancamento.objects.create(
            usuario=ana, data="2026-03-05", movimentacao="Saída", banco="Banco SEGREDO", categoria="Cat SEGREDO",
            descricao="Compra SEGREDO", valor=Decimal("98765.43"), pago=False)
        HistoricoLancamento.objects.create(usuario=ana, lancamento_id=cls.lanc_ana.pk, campo="descricao",
                                           antes="SEGREDO antes", depois="Compra SEGREDO", origem="tela")
        RegraCategoria.objects.create(usuario=ana, padrao="LOJA SEGREDO", categoria="Cat SEGREDO")
        cls.nota_ana = criar_nota(ana, "1" * 44, (2026, 3, 10, 10, 0), "LOJA SEGREDO",
                                  [(LEITE, "LEITE SEGREDO 1L", "4321.11"), (EXCLUSIVO, "ITEM SEGREDO", "1.00")],
                                  "Forma SEGREDO")
        PeriodoNP.objects.create(usuario=ana, periodo="2026-03", total_notas=1, valor_total=Decimal("4322.11"),
                                 creditos="SEGREDO")
        Placar.objects.create(usuario=ana, dados={"Saldo disponível": "R$ 31.337,00 SEGREDO"})
        cls.agua_ana = FaturaSanepar.objects.create(usuario=ana, referencia="2026-03", matricula="SEGREDO",
                                                    valor_total=Decimal("4321.09"), consumo_m3=77, situacao="Paga")
        cls.agua_ana.pdf.save("2026-03.pdf", ContentFile(b"%PDF-1.4 SEGREDO"))
        cls.luz_ana = FaturaCopel.objects.create(usuario=ana, numero_fatura="SEGREDO1", referencia="2026-03",
                                                 valor_total=Decimal("3210.98"), consumo_kwh=555, situacao="Quitada")
        cls.luz_ana.pdf.save("2026-03.pdf", ContentFile(b"%PDF-1.4 SEGREDO"))
        Informe.objects.create(usuario=ana, mes="2026-03", horas=160, valor_hora=Decimal("777.77"),
                               observacao="SEGREDO")
        for servico in (NOTAPARANA, SANEPAR, COPEL):
            credencial(ana, servico, CPF_ANA)
            sessao(ana, servico)
        cls.tarefa_ana = Tarefa.objects.create(usuario=ana, servico=NOTAPARANA, status=Tarefa.OK,
                                               mensagem="SEGREDO feito", log="[10:00] SEGREDO no log",
                                               progresso="SEGREDO", resultado={"novas": 1})

        # --- dados do Bruno: o bastante para as páginas mostrarem tudo (gráficos, tabelas) ---
        Lancamento.objects.create(usuario=bruno, data="2026-03-07", movimentacao="Entrada", categoria="Salário",
                                  valor=Decimal("1000"))
        Lancamento.objects.create(usuario=bruno, data="2026-03-08", movimentacao="Saída", categoria="Luz",
                                  valor=Decimal("150"), pago=True)
        criar_nota(bruno, "9" * 44, (2026, 3, 12, 9, 0), "MERCADO DO BRUNO", [(LEITE, "LEITE 1L", "4.00")], "Pix")
        FaturaSanepar.objects.create(usuario=bruno, referencia="2026-04", valor_total=Decimal("80"), situacao="Paga")
        FaturaCopel.objects.create(usuario=bruno, numero_fatura="B1", referencia="2026-03", valor_total=Decimal("150"))
        credencial(bruno, NOTAPARANA, CPF_BRUNO)
        credencial(bruno, SANEPAR, CPF_BRUNO)
        sessao(bruno, SANEPAR)

        cls.mascarado_ana = Credencial.objects.filter(usuario=ana).first().login_mascarado

    def setUp(self):
        cache.clear()
        self.client.force_login(self.bruno)

    # ---------- o que é da Ana ----------
    def marcas(self) -> list[str]:
        return ["SEGREDO", "98.765,43", "98765.43", "31.337", "4.321,09", "3.210,98", "4321.11", "777,77",
                "124.443,20", "cookie-%d" % self.ana.pk, self.mascarado_ana, CPF_ANA]

    def foto_da_ana(self) -> dict:
        """Tudo da Ana no banco, para comparar antes e depois das escritas do Bruno."""
        def linhas(qs):
            return sorted((model_to_dict(o) | {"pk": o.pk} for o in qs), key=lambda d: d["pk"])
        ana = self.ana
        foto = {modelo.__name__: linhas(modelo.objects.filter(usuario=ana)) for modelo in (
            Categoria, Banco, Lancamento, HistoricoLancamento, RegraCategoria, Nota, PeriodoNP, Placar, FaturaSanepar,
            FaturaCopel, Informe, Credencial, SessaoServico, Tarefa)}
        foto["ItemNota"] = linhas(ItemNota.objects.filter(nota__usuario=ana))
        foto["PagamentoNota"] = linhas(PagamentoNota.objects.filter(nota__usuario=ana))
        foto["Usuario"] = linhas(Usuario.objects.filter(pk=ana.pk))
        return foto

    def conteudo(self, resposta) -> str:
        corpo = b"".join(resposta.streaming_content) if resposta.streaming else resposta.content
        return corpo.decode("utf-8", errors="replace")

    def sem_nada_da_ana(self, resposta, rotulo: str, enviado: dict | None = None) -> None:
        """Nenhuma marca da Ana na resposta, menos as que o próprio Bruno mandou (a busca volta na caixa de busca)."""
        texto = self.conteudo(resposta)
        cabecalhos = json.dumps(dict(resposta.headers), ensure_ascii=False)
        eco = json.dumps(enviado or {}, ensure_ascii=False)
        for marca in self.marcas():
            if marca in eco:
                continue
            self.assertNotIn(marca, texto, f"{rotulo}: a resposta mostra '{marca}', que é da Ana")
            self.assertNotIn(marca, cabecalhos, f"{rotulo}: um cabeçalho mostra '{marca}', que é da Ana")

    # ---------- as rotas ----------
    def leituras(self) -> list[tuple[str, str, dict, set]]:
        """(nome da rota, url, parâmetros, status aceitos). Os parâmetros apontam para os dados da Ana."""
        a, nota, agua, luz, tarefa = self.lanc_ana, self.nota_ana, self.agua_ana, self.luz_ana, self.tarefa_ana
        ok, nao_existe = {200}, {404}
        return [
            ("financeiro:resumo", reverse("financeiro:resumo"), {"ano": 2026}, ok),
            ("financeiro:resumo_mes", reverse("financeiro:resumo_mes"), {"ano": 2026, "alvo": 3}, ok),
            ("financeiro:resumo_categoria", reverse("financeiro:resumo_categoria"), {"ano": 2026, "alvo": "Cat SEGREDO"}, ok),
            ("financeiro:lancamentos", reverse("financeiro:lancamentos"), {"ano": 2026, "busca": "SEGREDO"}, ok),
            ("financeiro:lancamentos", reverse("financeiro:lancamentos"), {"ano": 2026, "mes": 3}, ok),
            ("financeiro:api", reverse("financeiro:api"), {"ano": 2026, "mes": 0}, ok),
            ("financeiro:historico", reverse("financeiro:historico"), {}, ok),
            ("financeiro:historico", reverse("financeiro:historico"), {"lancamento": a.pk}, ok),
            ("financeiro:cadastros", reverse("financeiro:cadastros"), {}, ok),
            ("pj:horas", reverse("pj:horas"), {"mes": "2026-03"}, ok),
            ("pj:horas", reverse("pj:horas"), {"mes": "2026-04"}, ok),
            ("notas:notas", reverse("notas:notas"), {}, ok),
            ("notas:detalhe", reverse("notas:detalhe"), {"tipo": "loja", "alvo": "LOJA SEGREDO"}, nao_existe),
            ("notas:detalhe", reverse("notas:detalhe"), {"tipo": "categoria", "alvo": "Cat SEGREDO"}, nao_existe),
            ("notas:detalhe", reverse("notas:detalhe"), {"tipo": "forma", "alvo": "Forma SEGREDO"}, nao_existe),
            ("notas:detalhe", reverse("notas:detalhe"), {"tipo": "loja", "alvo": "MERCADO DO BRUNO"}, ok),
            ("notas:detalhe_compras", reverse("notas:detalhe_compras"), {"tipo": "loja", "alvo": "LOJA SEGREDO"}, nao_existe),
            ("notas:nota", reverse("notas:nota", args=[nota.pk]), {}, nao_existe),
            ("notas:produtos", reverse("notas:produtos"), {}, ok),
            ("notas:produtos", reverse("notas:produtos"), {"busca": "SEGREDO"}, ok),
            ("notas:produtos_dados", reverse("notas:produtos_dados"), {}, ok),
            ("notas:produto", reverse("notas:produto"), {"grupo": f"ean:{EXCLUSIVO}"}, nao_existe),
            ("notas:produto", reverse("notas:produto"), {"grupo": f"ean:{LEITE}"}, ok),
            ("consumo:agua", reverse("consumo:agua"), {}, ok),
            ("consumo:luz", reverse("consumo:luz"), {}, ok),
            ("consumo:agua_pdf", reverse("consumo:agua_pdf", args=[agua.pk]), {}, nao_existe),
            ("consumo:luz_pdf", reverse("consumo:luz_pdf", args=[luz.pk]), {}, nao_existe),
            ("consumo:agua_enviar", reverse("consumo:agua_enviar"), {}, ok),
            ("consumo:luz_enviar", reverse("consumo:luz_enviar"), {}, ok),
            ("integracoes:configuracoes", reverse("integracoes:configuracoes"), {}, ok),
            *[("integracoes:servico", reverse("integracoes:servico", args=[s]), {}, ok)
              for s in (NOTAPARANA, SANEPAR, COPEL)],
            ("integracoes:historico", reverse("integracoes:historico"), {}, ok),
            ("integracoes:status", reverse("integracoes:status"), {}, ok),
            ("integracoes:tarefa", reverse("integracoes:tarefa", args=[tarefa.pk]), {}, nao_existe),
            *[("janela:conectar", reverse("janela:conectar", args=[s]), {}, ok) for s in (SANEPAR, COPEL)],
            ("contas:perfil", reverse("contas:perfil"), {}, ok),
        ]

    def escritas(self) -> list[tuple[str, str, str, dict]]:
        """(nome da rota, método, url, corpo). Tudo que o Bruno pode mandar mirando nos dados da Ana."""
        a, nota = self.lanc_ana, self.nota_ana
        item = reverse("financeiro:api_item", args=[a.pk])
        return [
            ("financeiro:api_item", "patch", item, {"pago": True, "valor": "1"}),
            ("financeiro:api_item", "delete", item, {}),
            ("financeiro:api_excluir", "json", reverse("financeiro:api_excluir"), {"ids": [a.pk]}),
            ("financeiro:api_marcar_pagas", "json", reverse("financeiro:api_marcar_pagas"), {"ids": [a.pk]}),
            ("financeiro:api_copiar_mes", "json", reverse("financeiro:api_copiar_mes"),
             {"ano": 2026, "mes": 4, "confirmar": True}),
            ("financeiro:api", "json", reverse("financeiro:api"),
             {"data": "2026-03-20", "movimentacao": "Saída", "categoria": "Cat SEGREDO", "valor": "1"}),
            ("financeiro:cadastros", "post", reverse("financeiro:cadastros"), {"tipo": "categoria", "nome": "Cat SEGREDO"}),
            ("financeiro:cadastros", "post", reverse("financeiro:cadastros"), {"tipo": "banco", "nome": "Banco SEGREDO"}),
            ("notas:api_categoria", "json", reverse("notas:api_categoria"), {"id": nota.pk, "categoria": "Invadida"}),
            ("notas:api_regras", "json", reverse("notas:api_regras"), {"regras": [{"padrao": "X", "categoria": "Y"}]}),
            ("consumo:agua_ajustar", "post", reverse("consumo:agua_ajustar"), {}),
            ("consumo:luz_ajustar", "post", reverse("consumo:luz_ajustar"), {}),
            ("consumo:agua_enviar", "post", reverse("consumo:agua_enviar"), {}),
            ("consumo:luz_enviar", "post", reverse("consumo:luz_enviar"), {}),
            ("pj:horas", "post", reverse("pj:horas"),
             {"mes": "2026-03", "horas": "10:00", "horas_sobreaviso": "0", "valor_hora": "1,00", "plano": "0"}),
            ("pj:horas", "post", reverse("pj:horas"),
             {"mes": "2026-03", "acao": "prever", "horas_previsao": "10:00", "valor_hora_previsao": "1,00"}),
            *[("integracoes:configuracoes", "post", reverse("integracoes:configuracoes"),
               {"servico": s, "acao": "remover"}) for s in (NOTAPARANA, SANEPAR, COPEL)],
            ("integracoes:configuracoes", "post", reverse("integracoes:configuracoes"),
             {"servico": COPEL, "login": CPF_BRUNO, "senha": "outra"}),
            *[("integracoes:sincronizar", "post", reverse("integracoes:sincronizar", args=[s]), {})
              for s in (NOTAPARANA, SANEPAR, COPEL)],
            ("integracoes:atualizar", "post", reverse("integracoes:atualizar"), {}),
            ("contas:perfil", "post", reverse("contas:perfil"),
             {"salvar_perfil": "1", "perfil-nome": "Bruno", "perfil-email": "ana@teste.local",
              "perfil-senha_atual": SENHA, "perfil-hora_atualizacao": "07:00"}),
            ("contas:excluir_conta", "post", reverse("contas:excluir_conta"), {"senha": "errada", "confirmacao": "EXCLUIR"}),
        ]

    # Rotas que não leem nem gravam dados de ninguém (ou que encerram a sessão), testadas em outros lugares.
    SEM_DADOS = {"saude", "contas:entrar", "contas:cadastro", "contas:sair", None}

    def enviar(self, metodo: str, url: str, corpo: dict):
        if metodo == "post":
            return self.client.post(url, corpo)
        if metodo == "json":
            return self.client.post(url, json.dumps(corpo), content_type="application/json")
        return getattr(self.client, metodo)(url, json.dumps(corpo), content_type="application/json")

    def test_todas_as_rotas_estao_na_varredura(self):
        def nomes(padroes, ns=None):
            for p in padroes:
                if isinstance(p, URLResolver):
                    if p.app_name != "admin":
                        yield from nomes(p.url_patterns, p.namespace or ns)
                elif isinstance(p, URLPattern):
                    yield f"{ns}:{p.name}" if ns and p.name else p.name
        cobertas = {n for n, *_ in self.leituras()} | {n for n, *_ in self.escritas()} | self.SEM_DADOS
        faltando = set(nomes(get_resolver().url_patterns)) - cobertas
        self.assertFalse(faltando, f"Rotas sem teste de isolamento (inclua em leituras/escritas): {faltando}")

    def test_leituras_nao_mostram_nada_de_outro_usuario(self):
        for nome, url, params, aceitos in self.leituras():
            with self.subTest(rota=nome, params=params):
                resposta = self.client.get(url, params)
                self.assertIn(resposta.status_code, aceitos, f"{nome} {params}")
                self.sem_nada_da_ana(resposta, f"{nome} {params}", params)

    def test_escritas_nao_alcancam_outro_usuario(self):
        antes = self.foto_da_ana()
        for nome, metodo, url, corpo in self.escritas():
            with self.subTest(rota=nome, metodo=metodo, corpo=corpo):
                resposta = self.enviar(metodo, url, corpo)
                self.assertLess(resposta.status_code, 500, f"{nome} {metodo}")
                self.sem_nada_da_ana(resposta, f"{nome} {metodo}", corpo)
        self.assertEqual(self.foto_da_ana(), antes, "uma escrita do Bruno mudou dados da Ana")
        # E o que o Bruno gravou continua só dele (nada da Ana foi copiado para ele).
        self.assertFalse(Lancamento.objects.filter(usuario=self.bruno, valor=Decimal("98765.43")).exists())
        self.assertEqual(Usuario.objects.get(pk=self.bruno.pk).email, "bruno@teste.local")  # e-mail já é da Ana

    def test_nome_igual_ao_de_outro_usuario_nao_revela_que_existe(self):
        for tipo, nome in (("categoria", "Cat SEGREDO"), ("banco", "Banco SEGREDO")):
            resposta = self.client.post(reverse("financeiro:cadastros"), {"tipo": tipo, "nome": nome})
            self.assertNotContains(resposta, "já existe")
        self.assertTrue(Categoria.objects.filter(usuario=self.bruno, nome="Cat SEGREDO").exists())
        self.assertTrue(Banco.objects.filter(usuario=self.bruno, nome="Banco SEGREDO").exists())

    def test_api_por_id_de_outro_usuario_e_404(self):
        url = reverse("financeiro:api_item", args=[self.lanc_ana.pk])
        self.assertEqual(self.enviar("patch", url, {"pago": True}).status_code, 404)
        self.assertEqual(self.client.delete(url).status_code, 404)
        r = self.enviar("json", reverse("financeiro:api_excluir"), {"ids": [self.lanc_ana.pk]})
        self.assertEqual(r.json()["excluidos"], 0)
        r = self.enviar("json", reverse("financeiro:api_marcar_pagas"), {"ids": [self.lanc_ana.pk]})
        self.assertEqual((r.json()["marcados"], r.json()["linhas"]), (0, []))
        r = self.enviar("json", reverse("notas:api_categoria"), {"id": self.nota_ana.pk, "categoria": "X"})
        self.assertEqual(r.status_code, 404)

    def test_sem_login_tudo_manda_para_o_login(self):
        self.client.logout()
        for nome, url, params, _ in self.leituras():
            with self.subTest(rota=nome, metodo="get"):
                resposta = self.client.get(url, params)
                self.assertEqual(resposta.status_code, 302, nome)
                self.assertTrue(resposta["Location"].startswith(reverse("contas:entrar")), nome)
        for nome, metodo, url, corpo in self.escritas():
            with self.subTest(rota=nome, metodo=metodo):
                resposta = self.enviar(metodo, url, corpo)
                self.assertEqual(resposta.status_code, 302, nome)
        self.assertEqual(self.foto_da_ana()["Lancamento"][0]["valor"], Decimal("98765.43"))


class AcessoTest(TestCase):
    def setUp(self):
        cache.clear()

    def test_caminho_parecido_com_pagina_publica_exige_login(self):
        for caminho in ("/entrar-nao-existe/", "/cadastrox/", "/saude-falsa/", "/staticx/"):
            resposta = self.client.get(caminho)
            self.assertEqual(resposta.status_code, 302, caminho)
            self.assertEqual(resposta["Location"], f"/entrar/?next={caminho.replace('/', '%2F')}")

    def test_next_vai_codificado(self):
        resposta = self.client.get("/notas/", {"x": "1"})
        self.assertEqual(resposta["Location"], "/entrar/?next=%2Fnotas%2F")

    def test_cabecalhos_de_seguranca(self):
        resposta = self.client.get(reverse("contas:entrar"))
        csp = resposta["Content-Security-Policy"]
        for trecho in ("default-src 'self'", "frame-ancestors 'none'", "object-src 'none'", "form-action 'self'",
                       "connect-src 'self' wss://testserver ws://testserver"):
            self.assertIn(trecho, csp)
        self.assertIn("camera=()", resposta["Permissions-Policy"])
        self.assertEqual(resposta["X-Frame-Options"], "DENY")
        self.assertEqual(resposta["X-Content-Type-Options"], "nosniff")

    def test_admin_entra_pelo_login_do_site(self):
        resposta = self.client.get("/admin/", follow=True)
        self.assertEqual(resposta.redirect_chain[-1][0], "/entrar/?next=%2Fadmin%2F")
        comum = Usuario.objects.create_user("comum@teste.local", SENHA, nome="Comum")
        self.client.force_login(comum)
        self.assertEqual(self.client.get("/admin/login/").status_code, 404)
        self.assertEqual(self.client.get("/admin/", follow=True).status_code, 404)
        equipe = Usuario.objects.create_superuser("equipe@teste.local", SENHA, nome="Equipe")
        self.client.force_login(equipe)
        self.assertEqual(self.client.get("/admin/").status_code, 200)
        self.assertRedirects(self.client.get("/admin/login/"), "/admin/")

    def test_admin_nao_aceita_senha_pelo_formulario_proprio(self):
        Usuario.objects.create_superuser("equipe@teste.local", SENHA, nome="Equipe")
        resposta = self.client.post("/admin/login/", {"username": "equipe@teste.local", "password": SENHA})
        self.assertEqual(resposta.status_code, 302)
        self.assertTrue(resposta["Location"].startswith("/entrar/"))
        self.assertNotIn("_auth_user_id", self.client.session)


class BloqueiosTest(TestCase):
    def setUp(self):
        cache.clear()
        self.usuario = Usuario.objects.create_user("alvo@teste.local", SENHA, nome="Alvo")

    def entrar(self, senha, ip="10.0.0.1", email="alvo@teste.local", **extra):
        return self.client.post(reverse("contas:entrar"), {"email": email, "senha": senha}, REMOTE_ADDR=ip, **extra)

    def test_x_forwarded_for_nao_escapa_do_bloqueio(self):
        for i in range(8):
            self.entrar("errada-errada", HTTP_X_FORWARDED_FOR=f"203.0.113.{i}")
        self.assertContains(self.entrar(SENHA, HTTP_X_FORWARDED_FOR="198.51.100.1"), "Muitas tentativas")

    def test_ataque_distribuido_na_mesma_conta(self):
        for i in range(20):
            self.entrar("errada-errada", ip=f"10.1.0.{i}")
        resposta = self.entrar(SENHA, ip="10.9.9.9")
        self.assertContains(resposta, "Muitas tentativas")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_um_ip_testando_muitas_contas(self):
        for i in range(30):
            self.entrar("senha-comum-123", email=f"conta{i}@teste.local")
        self.assertContains(self.entrar(SENHA), "Muitas tentativas")
        self.assertEqual(self.entrar(SENHA, ip="10.0.0.2").status_code, 302)  # de outro IP a pessoa entra

    def test_email_com_espacos_e_maiusculas_conta_como_o_mesmo(self):
        for i in range(8):
            email = (" " * (i % 3)) + ("ALVO@teste.local" if i % 2 else "alvo@teste.local")
            self.entrar("errada-errada", email=email)
        self.assertContains(self.entrar(SENHA), "Muitas tentativas")

    def test_limite_de_cadastros_por_ip(self):
        for i in range(5):
            self.client.post(reverse("contas:cadastro"), {
                "nome": f"Pessoa {i}", "email": f"p{i}@teste.local", "senha": SENHA, "confirmar": SENHA, "aceite": "on"},
                REMOTE_ADDR="10.2.0.1")
            self.client.logout()
        resposta = self.client.post(reverse("contas:cadastro"), {
            "nome": "Mais uma", "email": "p9@teste.local", "senha": SENHA, "confirmar": SENHA, "aceite": "on"},
            REMOTE_ADDR="10.2.0.1")
        self.assertContains(resposta, "Muitas contas criadas")
        self.assertFalse(Usuario.objects.filter(email="p9@teste.local").exists())


class PerfilSegurancaTest(TestCase):
    def setUp(self):
        self.usuario = Usuario.objects.create_user("eu@teste.local", SENHA, nome="Eu")
        self.client.force_login(self.usuario)

    def salvar(self, **campos):
        return self.client.post(reverse("contas:perfil"), {
            "salvar_perfil": "1", "perfil-nome": "Eu", "perfil-email": "eu@teste.local",
            "perfil-atualizacao_automatica": "on", "perfil-hora_atualizacao": "07:00",
            **{f"perfil-{c}": v for c, v in campos.items()}})

    def test_trocar_email_exige_a_senha(self):
        self.assertContains(self.salvar(email="novo@teste.local"), "Digite a sua senha atual")
        self.assertContains(self.salvar(email="novo@teste.local", senha_atual="errada"), "Digite a sua senha atual")
        self.assertEqual(Usuario.objects.get(pk=self.usuario.pk).email, "eu@teste.local")
        self.assertRedirects(self.salvar(email="novo@teste.local", senha_atual=SENHA), reverse("contas:perfil"))
        self.assertEqual(Usuario.objects.get(pk=self.usuario.pk).email, "novo@teste.local")

    def test_outros_campos_nao_pedem_senha(self):
        self.assertRedirects(self.salvar(nome="Outro Nome"), reverse("contas:perfil"))
        self.assertEqual(Usuario.objects.get(pk=self.usuario.pk).nome, "Outro Nome")
        self.assertEqual(Usuario.objects.get(pk=self.usuario.pk).hora_atualizacao, time(7, 0))

    def test_excluir_conta_apaga_os_arquivos_do_disco(self):
        with tempfile.TemporaryDirectory() as pasta, override_settings(MEDIA_ROOT=pasta):
            fatura = FaturaSanepar.objects.create(usuario=self.usuario, referencia="2026-01", valor_total=10)
            fatura.pdf.save("2026-01.pdf", ContentFile(b"%PDF-1.4"))
            outro = Usuario.objects.create_user("outro@teste.local", SENHA, nome="Outro")
            do_outro = FaturaSanepar.objects.create(usuario=outro, referencia="2026-01", valor_total=10)
            do_outro.pdf.save("2026-01.pdf", ContentFile(b"%PDF-1.4"))
            minha = Path(pasta) / "usuarios" / str(self.usuario.pk)
            self.assertTrue(minha.exists())
            self.assertEqual(oct(Path(fatura.pdf.path).stat().st_mode & 0o777), "0o600")
            with self.captureOnCommitCallbacks(execute=True):
                self.client.post(reverse("contas:excluir_conta"), {"senha": SENHA, "confirmacao": "EXCLUIR"})
            self.assertFalse(minha.exists())
            self.assertTrue(Path(do_outro.pdf.path).exists())  # os arquivos dos outros ficam

    def test_lote_de_ids_tem_limite(self):
        resposta = self.client.post(reverse("financeiro:api_excluir"), json.dumps({"ids": list(range(5001))}),
                                    content_type="application/json")
        self.assertEqual(resposta.status_code, 400)
