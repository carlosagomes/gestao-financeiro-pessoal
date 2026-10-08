"""Traz os dados do sistema antigo (Streamlit + SQLite) para a conta de um usuário.

  python manage.py importar_legado --email voce@exemplo.com --pasta ..          # cria a conta se não existir
  python manage.py importar_legado --email voce@exemplo.com --pasta .. --substituir

Lê <pasta>/data/financas.db, os arquivos de <pasta>/data (HTML das notas, PDFs da Sanepar e da Copel), as
senhas do <pasta>/.env (gravadas cifradas, nunca exibidas) e a sessão salva da Sanepar (.sessao/sanepar.json).
Se a conta for criada agora, ela ganha o módulo PJ, acesso ao admin e uma senha provisória gravada em
dados/acesso-inicial.txt (só o dono do arquivo lê). Troque a senha no primeiro acesso.
"""
from __future__ import annotations

import json
import os
import secrets
import sqlite3
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from dotenv import dotenv_values

from consumo.models import FaturaCopel, FaturaSanepar
from contas.models import Usuario
from financeiro.models import Banco, Categoria, HistoricoLancamento, Lancamento
from integracoes.models import COPEL, NOTAPARANA, SANEPAR, Credencial, SessaoServico, Tarefa
from notas.models import ItemNota, Nota, PagamentoNota, PeriodoNP, Placar, RegraCategoria
from pj.models import Informe

FUSO = timezone.get_default_timezone()


def _dec(valor, casas=2):
    if valor is None or valor == "":
        return None
    return Decimal(str(round(float(valor), casas)))


def _data(texto):
    return datetime.fromisoformat(texto).date() if texto else None


def _momento(texto):
    if not texto:
        return None
    valor = datetime.fromisoformat(texto)
    return timezone.make_aware(valor, FUSO) if timezone.is_naive(valor) else valor


def _json(texto):
    if not texto:
        return {}
    try:
        valor = json.loads(texto)
    except (TypeError, ValueError):
        return {"texto": texto}
    return valor if isinstance(valor, dict) else {"valor": valor}


def usuario_e_dono(email: str) -> bool:
    """A primeira conta criada no sistema é a do dono."""
    return not Usuario.objects.exclude(email=email).exists()


class Command(BaseCommand):
    help = "Importa os dados do sistema antigo (SQLite) para a conta de um usuário."

    def add_arguments(self, parser):
        parser.add_argument("--email", required=True)
        parser.add_argument("--nome", default="")
        parser.add_argument("--pasta", default="..", help="pasta do sistema antigo (com data/ e .env)")
        parser.add_argument("--substituir", action="store_true", help="apaga os dados da conta antes de importar")
        parser.add_argument("--sem-senhas", action="store_true", help="não copia as senhas do .env antigo")

    def handle(self, *args, **opts):
        pasta = Path(opts["pasta"]).resolve()
        banco = pasta / "data" / "financas.db"
        if not banco.exists():
            raise CommandError(f"Não achei {banco}")
        con = sqlite3.connect(f"file:{banco}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row

        usuario, criado = self._usuario(opts["email"], opts["nome"])
        with transaction.atomic():
            if opts["substituir"]:
                self._limpar(usuario)
            elif usuario.lancamentos.exists() or usuario.notas.exists():
                raise CommandError("A conta já tem dados. Use --substituir para apagar e importar de novo.")
            self._financeiro(con, usuario)
            self._pj(con, usuario)
            self._notas(con, usuario, pasta)
            self._consumo(con, usuario, pasta)
            self._historico_sync(con, usuario)
        if not opts["sem_senhas"]:
            self._credenciais(usuario, pasta)
        con.close()
        self.stdout.write(self.style.SUCCESS(f"Importado para {usuario.email}."))
        if criado:
            self.stdout.write("Senha provisória gravada em dados/acesso-inicial*.txt: troque no primeiro acesso.")

    # ------------------------------------------------------------------------------------------
    def _usuario(self, email, nome):
        email = email.lower().strip()
        usuario = Usuario.objects.filter(email=email).first()
        if usuario:
            return usuario, False
        senha = secrets.token_urlsafe(14)
        dono = usuario_e_dono(email)
        usuario = Usuario.objects.create_user(email, senha, nome=nome or " ".join(p.capitalize() for p in email.split("@")[0].replace("_", ".").split(".")),
                                              is_staff=dono, is_superuser=dono, pj_habilitado=True)
        nome_arquivo = "acesso-inicial.txt" if dono else f"acesso-inicial-{email.split('@')[0]}.txt"
        arquivo = Path(settings.MEDIA_ROOT).parent / nome_arquivo
        arquivo.parent.mkdir(parents=True, exist_ok=True)
        arquivo.write_text(f"Gestão Financeira Pessoal\nE-mail: {email}\nSenha provisória: {senha}\n"
                           "Troque em Perfil > Trocar senha e apague este arquivo.\n")
        os.chmod(arquivo, 0o600)
        return usuario, True

    def _limpar(self, usuario):
        for modelo in (Lancamento, HistoricoLancamento, Categoria, Banco, Informe, Nota, PeriodoNP, RegraCategoria,
                       FaturaSanepar, FaturaCopel, Tarefa):
            modelo.objects.filter(usuario=usuario).delete()
        Placar.objects.filter(usuario=usuario).delete()

    def _financeiro(self, con, usuario):
        Categoria.objects.filter(usuario=usuario).delete()
        Banco.objects.filter(usuario=usuario).delete()
        Categoria.objects.bulk_create([Categoria(usuario=usuario, nome=r["nome"], movimentacao=r["movimentacao"])
                                       for r in con.execute("SELECT * FROM categorias")])
        Banco.objects.bulk_create([Banco(usuario=usuario, nome=r["nome"]) for r in con.execute("SELECT * FROM bancos")])
        mapa = {}
        for r in con.execute("SELECT * FROM lancamentos ORDER BY id"):
            novo = Lancamento.objects.create(
                usuario=usuario, data=_data(r["data"]), movimentacao=r["movimentacao"], banco=r["banco"] or "",
                categoria=r["categoria"], descricao=r["descricao"] or "", valor=_dec(r["valor"]), pago=bool(r["pago"]))
            mapa[r["id"]] = novo.id
        # Categorias usadas nos lançamentos e que não estavam na lista (a tela antiga juntava as duas).
        usadas = {(c, m) for c, m in Lancamento.objects.filter(usuario=usuario).values_list("categoria", "movimentacao")}
        existentes = set(Categoria.objects.filter(usuario=usuario).values_list("nome", flat=True))
        Categoria.objects.bulk_create([Categoria(usuario=usuario, nome=c, movimentacao=m)
                                       for c, m in sorted(usadas) if c not in existentes], ignore_conflicts=True)
        HistoricoLancamento.objects.bulk_create([
            HistoricoLancamento(usuario=usuario, lancamento_id=mapa.get(r["lancamento_id"], r["lancamento_id"]),
                                campo=r["campo"], antes=r["antes"], depois=r["depois"], origem=r["origem"] or "")
            for r in con.execute("SELECT * FROM historico_lancamentos ORDER BY id")])
        self.stdout.write(f"  lançamentos: {len(mapa)}")

    def _pj(self, con, usuario):
        hoje = timezone.localdate().strftime("%Y-%m")
        Informe.objects.bulk_create([
            Informe(usuario=usuario, mes=r["mes"], horas=r["horas_previstas"] or 0,
                    horas_sobreaviso=r["horas_sobreaviso"] or 0, valor_hora=_dec(r["valor_hora"]),
                    pct_sobreaviso=r["pct_sobreaviso"] or 1 / 3, desconto=_dec(r["desconto"]) or 0,
                    valor_nf=_dec(r["valor_nf"]), observacao=r["observacao"] or "",
                    previsao=r["mes"] > hoje or "PREVIS" in (r["observacao"] or "").upper())
            for r in con.execute("SELECT * FROM horas ORDER BY mes")])
        self.stdout.write(f"  informes PJ: {Informe.objects.filter(usuario=usuario).count()}")

    def _notas(self, con, usuario, pasta):
        RegraCategoria.objects.filter(usuario=usuario).delete()
        RegraCategoria.objects.bulk_create([RegraCategoria(usuario=usuario, padrao=r["padrao"], categoria=r["categoria"])
                                            for r in con.execute("SELECT * FROM regras_categoria")])
        itens = {}
        for r in con.execute("SELECT * FROM itens_nota ORDER BY chave, seq"):
            itens.setdefault(r["chave"], []).append(r)
        pagamentos = {}
        for r in con.execute("SELECT * FROM pagamentos_nota ORDER BY chave, seq"):
            pagamentos.setdefault(r["chave"], []).append(r)
        pasta_html = pasta / "data" / "notas_html"
        total = 0
        for r in con.execute("SELECT * FROM notas ORDER BY data_emissao"):
            nota = Nota(
                usuario=usuario, chave=r["chave"], id_doc_fiscal=r["id_doc_fiscal"] or "", modelo=r["modelo"] or "",
                numero=r["numero"] or "", serie=r["serie"] or "", data_emissao=_momento(r["data_emissao"]),
                protocolo=r["protocolo"] or "", situacao=r["situacao"] or "", emitente_cnpj=r["emitente_cnpj"] or "",
                emitente_nome=r["emitente_nome"] or "", emitente_fantasia=r["emitente_fantasia"] or "",
                emitente_ie=r["emitente_ie"] or "", emitente_endereco=r["emitente_endereco"] or "",
                emitente_municipio=r["emitente_municipio"] or "", emitente_uf=(r["emitente_uf"] or "")[:2],
                qtd_itens=r["qtd_itens"], valor_produtos=_dec(r["valor_produtos"]),
                valor_desconto=_dec(r["valor_desconto"]), valor_total=_dec(r["valor_total"]),
                valor_tributos=_dec(r["valor_tributos"]), credito=_dec(r["credito"]),
                situacao_credito=r["situacao_credito"] or "", categoria=r["categoria"] or "",
                detalhes=_json(r["detalhes"]), url=r["url"] or "")
            html = pasta_html / f"{r['chave']}.html"
            if html.exists():
                nota.html.save(html.name, ContentFile(html.read_bytes()), save=False)
            nota.save()
            ItemNota.objects.bulk_create([ItemNota(
                nota=nota, seq=i["seq"], codigo=i["codigo"] or "", ean=i["ean"] or "", descricao=i["descricao"] or "",
                ncm=i["ncm"] or "", cfop=i["cfop"] or "", quantidade=_dec(i["quantidade"], 4), unidade=i["unidade"] or "",
                valor_unitario=_dec(i["valor_unitario"], 6), valor_desconto=_dec(i["valor_desconto"]),
                valor_total=_dec(i["valor_total"]), detalhes=_json(i["detalhes"])) for i in itens.get(r["chave"], [])])
            PagamentoNota.objects.bulk_create([PagamentoNota(
                nota=nota, seq=p["seq"], forma=p["forma"] or "", bandeira=p["bandeira"] or "", valor=_dec(p["valor"]),
                detalhes=_json(p["detalhes"])) for p in pagamentos.get(r["chave"], [])])
            total += 1
        PeriodoNP.objects.bulk_create([PeriodoNP(
            usuario=usuario, periodo=r["periodo"], total_notas=r["total_notas"], bilhetes=r["bilhetes"],
            valor_total=_dec(r["valor_total"]), creditos=r["creditos"] or "") for r in con.execute("SELECT * FROM periodos_np")])
        placar = con.execute("SELECT placar FROM sincronizacoes WHERE placar IS NOT NULL AND fonte = 'notaparana' "
                             "ORDER BY id DESC LIMIT 1").fetchone()
        if placar:
            Placar.objects.update_or_create(usuario=usuario, defaults={"dados": _json(placar[0])})
        self.stdout.write(f"  notas: {total} (itens {ItemNota.objects.filter(nota__usuario=usuario).count()})")

    def _consumo(self, con, usuario, pasta):
        for r in con.execute("SELECT * FROM faturas_sanepar ORDER BY referencia"):
            fatura = FaturaSanepar(
                usuario=usuario, referencia=r["referencia"], matricula=r["matricula"] or "", vencimento=_data(r["vencimento"]),
                valor_total=_dec(r["valor_total"]), valor_agua=_dec(r["valor_agua"]), valor_esgoto=_dec(r["valor_esgoto"]),
                valor_servicos=_dec(r["valor_servicos"]), consumo_m3=r["consumo_m3"], leitura_anterior=r["leitura_anterior"],
                leitura_atual=r["leitura_atual"], data_leitura=_data(r["data_leitura"]),
                proxima_leitura=_data(r["proxima_leitura"]), dias=r["dias"], tributos=_dec(r["tributos"]),
                situacao=r["situacao"] or "", detalhes=_json(r["detalhes"]))
            self._anexar(fatura, pasta, r["pdf"])
            fatura.save()
        for r in con.execute("SELECT * FROM faturas_copel ORDER BY referencia"):
            fatura = FaturaCopel(
                usuario=usuario, numero_fatura=r["numero_fatura"], referencia=r["referencia"], situacao=r["situacao"] or "",
                origem=r["origem"] or "", vencimento=_data(r["vencimento"]), data_pagamento=_data(r["data_pagamento"]),
                valor_total=_dec(r["valor_total"]), consumo_kwh=r["consumo_kwh"], leitura_anterior=r["leitura_anterior"],
                leitura_atual=r["leitura_atual"], data_leitura=_data(r["data_leitura"]), dias=r["dias"],
                bandeira=r["bandeira"] or "", detalhes=_json(r["detalhes"]))
            self._anexar(fatura, pasta, r["pdf"])
            fatura.save()
        self.stdout.write(f"  faturas: Sanepar {usuario.faturas_sanepar.count()}, Copel {usuario.faturas_copel.count()}")

    @staticmethod
    def _anexar(fatura, pasta, caminho):
        if caminho and (arquivo := pasta / caminho).exists():
            fatura.pdf.save(arquivo.name, ContentFile(arquivo.read_bytes()), save=False)

    def _historico_sync(self, con, usuario):
        fontes = {"notaparana": NOTAPARANA, "sanepar": SANEPAR, "copel": COPEL}
        for r in con.execute("SELECT * FROM sincronizacoes ORDER BY id"):
            status = {"ok": Tarefa.OK, "erro": Tarefa.ERRO}.get(r["status"], Tarefa.CANCELADA)
            tarefa = Tarefa.objects.create(
                usuario=usuario, servico=fontes.get(r["fonte"], NOTAPARANA), origem="sistema antigo", status=status,
                iniciada_em=_momento(r["inicio"]), terminada_em=_momento(r["fim"]), mensagem=r["mensagem"] or "",
                resultado={"notas_novas": r["notas_novas"], "notas_atualizadas": r["notas_atualizadas"],
                           "erros": r["erros"]})
            Tarefa.objects.filter(pk=tarefa.pk).update(criada_em=_momento(r["inicio"]) or timezone.now())

    def _credenciais(self, usuario, pasta):
        """Senhas do .env antigo -> credenciais cifradas. Nada é impresso."""
        env = dotenv_values(pasta / ".env") if (pasta / ".env").exists() else {}
        copiadas = []
        for servico, prefixo in ((NOTAPARANA, "NOTAPARANA"), (SANEPAR, "SANEPAR"), (COPEL, "COPEL")):
            login, senha = env.get(f"{prefixo}_CPF"), env.get(f"{prefixo}_SENHA")
            if login and senha:
                credencial, _ = Credencial.objects.get_or_create(usuario=usuario, servico=servico,
                                                                 defaults={"login_cifrado": "", "senha_cifrada": ""})
                credencial.login, credencial.senha = login, senha
                credencial.save()
                copiadas.append(servico)
        sessao = pasta / ".sessao" / "sanepar.json"
        if sessao.exists():
            obj, _ = SessaoServico.objects.get_or_create(usuario=usuario, servico=SANEPAR,
                                                         defaults={"estado_cifrado": ""})
            obj.estado = json.loads(sessao.read_text())
            obj.expirada = False
            obj.save()
            copiadas.append("sessão da Sanepar")
        self.stdout.write(f"  credenciais cifradas: {', '.join(copiadas) or 'nenhuma'}")
