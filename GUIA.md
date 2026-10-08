# Gestão Financeira Pessoal: guia de desenvolvimento

Sistema multiusuário (SaaS) de finanças pessoais. Ele substituiu o app Streamlit antigo, removido em 06/10/2026; o backup está em `dados/backups/`.

## Stack

- **Django 5.2 e PostgreSQL 16.** Em desenvolvimento o banco é o container `db` do `docker compose`, em `127.0.0.1:5433`; o `.env` já aponta para ele.
- **Telas:** templates Django, **HTMX** para partes parciais e o modal, **Alpine.js** para estado pequeno de UI, **Plotly.js** para gráficos (montados em Python), **Tabulator** para tabelas editáveis ou grandes.
- **Background:** `manage.py trabalhador`, uma fila no próprio banco (modelo `integracoes.Tarefa`).
- **Janela remota de login** (captcha/2FA): Channels (WebSocket) mais Playwright no servidor.
- **Python 3.11:** `.venv/bin/python`, na raiz do projeto.

## Regras que não se negociam

1. **Multiusuário:**
   - Toda consulta é filtrada pelo usuário logado: `Modelo.objects.filter(usuario=request.user)` ou `get_object_or_404(Modelo, pk=pk, usuario=request.user)`.
   - Para itens e pagamentos de notas, use `nota__usuario=request.user`.
   - Nunca confie em id vindo do navegador sem esse filtro.
   - Use os DataFrames de `core/dados.py`, que já filtram.
   - **Toda rota nova entra na varredura de `core/tests.py`** (`leituras` ou `escritas`), com parâmetros que apontam para os dados da Ana. O teste `test_todas_as_rotas_estao_na_varredura` falha se faltar.
2. **Login:** tudo exige login (middleware `core.middleware.ExigirLoginMiddleware`); as páginas públicas são só `/entrar/`, `/cadastro/` e `/saude/` (caminhos exatos). O `/admin/` entra pelo `/entrar/`, que tem o bloqueio contra força bruta; não volte a usar o formulário próprio do admin. O IP do cliente é o `REMOTE_ADDR` (o daphne já aplica o `X-Forwarded-For` do Caddy); nunca leia o `X-Forwarded-For` direto.
3. **Módulo PJ:** só para `request.user.pj_habilitado`. As views usam `@exige_pj` (`core/acesso.py`), que devolve 404.
4. **Regra da planilha:**
   - **Saída conta só o que está pago.**
   - O que falta pagar aparece à parte, como "A pagar".
   - Saldo = Entrada − Saída paga.
   - Entrada conta tudo.
5. **As notas do Nota Paraná nunca somam nas saídas.** Elas detalham o consumo, que já está na fatura do cartão.
6. **O "Pago" é do usuário.** Nada automático muda o Pago sem registrar em `HistoricoLancamento`, e nada "restaura" o Pago de outra fonte. Toda alteração ou exclusão de lançamento grava histórico com a `origem`.
7. **Segredos:**
   - As senhas dos sites ficam em `integracoes.Credencial`, cifradas com `integracoes/cripto.py`.
   - Nunca imprima, logue ou devolva senha ou CPF em texto puro (use `login_mascarado`).
   - Nunca leia o `.env` para mostrar valores.
8. **Captcha e 2FA:** nunca resolva nem burle. Quem resolve é a pessoa, na janela remota.
9. **Endpoints proibidos:**
   - Nunca chamar `NotasFiscaisAjax` com `idDocFiscal` no Nota Paraná: isso **rejeita** a nota.
   - Nunca clicar em "Emitir 1ª via" na Copel.
10. **Navegador:**
    - A CSP fica em `core/middleware.py`: só scripts, estilos, conexões e imagens deste site (mais as fontes do Google). Biblioteca ou recurso de outro domínio exige ajustar a CSP.
    - Texto que vem de fora (lojas, produtos, nomes digitados) nunca entra como HTML. No Tabulator, `formatter`, `tooltip` e `itemFormatter` de lista viram `innerHTML`: passe sempre por `escapar()`. O formatter padrão (sem `formatter`) já escapa.
    - No template, só o escape automático do Django; nada de `|safe` ou `mark_safe` com dado de usuário. Dados para o JS vão por `|json_script`.
11. **Textos da interface:** em português do Brasil, frases curtas e diretas. Valores em formato brasileiro: `brl`, `pct` e `qtde` de `core/formatos.py` e os filtros `|brl`, `|pct` e `|qtde`.

## Estrutura

```
config/        settings (tudo vem do .env), urls, asgi (HTTP + WebSocket)
core/          layout base, CSS/JS, componentes, formatos, gráficos, dados (DataFrames), categorias padrão,
               middleware de login, importar_legado, sessao_dev
contas/        Usuario (login = e-mail; pj_habilitado), cadastro/entrar/sair/perfil/excluir conta, admin
financeiro/    Categoria, Banco, Lancamento, HistoricoLancamento -> Resumo e Lançamentos
pj/            Informe (horas do mês) -> Horas e informes, previsão de recebimento
notas/         Nota, ItemNota, PagamentoNota, PeriodoNP, Placar, RegraCategoria -> Notas Paraná e Produtos
consumo/       FaturaSanepar, FaturaCopel -> Água e Luz (PDFs em FileField, servidos só por view com dono)
integracoes/   Credencial, SessaoServico, Tarefa; cripto; trabalhador; coletores (Nota Paraná/Sanepar/Copel);
               janela remota (Channels); Configurações; status do menu lateral
```

## Como fazer uma página

- **View:** função simples. Página inteira estende `core/base.html` com os blocos `titulo`, `cabecalho`, `subtitulo`, `acoes`, `conteudo` e `scripts`.
- **Templates:** ficam em `<app>/templates/<app>/`, com `{% load gfp %}`.
- **Componentes (CSS em `static/css/app.css`):**
  - Cartão: `<div class="cartao">` com `<div class="cartao-cabecalho"><h2>Título</h2><div class="descricao">…</div><div class="acoes">…</div></div>`.
  - Grades: `.grade.g-2` / `.g-3` / `.g-4` / `.g-5` / `.g-3-2` / `.g-2-3`, todas responsivas.
  - KPI: `{% include "core/_kpi.html" with rotulo="Entradas" valor=total|brl cor="verde" icone="south_west" detalhe="…" mini=fig_json %}`. As cores são `verde`, `vermelho`, `azul` e `laranja`.
  - Botões: `.botao`, `.botao-primario`, `.botao-perigo`, `.botao-fantasma`, `.botao-pequeno`, `.botao-icone`.
  - Ícones: `{% icone "nome" %}` (Material Symbols Rounded).
  - Etiquetas: `<span class="etiqueta verde|vermelho|laranja|azul|roxo|cinza">`.
  - Avisos: `<div class="aviso info|sucesso|alerta|erro">{% icone "info" %}<div>…</div></div>`.
  - Tabelas: `<div class="tabela-rolagem"><table class="tabela">…`, com `td.valor` alinhado à direita e `tr.total` para a linha de total.
  - Formulários: `{% include "core/_campo.html" with campo=form.x %}`. Segmentado: `<div class="segmentado"><label><input type=radio name=g value=x checked><span>X</span></label>…</div>`.
  - Linha do tempo: `.lt-mes`, `.lt-item`, `.lt-dia`, `.lt-titulo`, `.lt-sub`, `.lt-valor`.
  - Estado vazio: `<div class="vazio">{% icone "inbox" %}Nada aqui ainda</div>`.
- **Gráficos:**
  - Monte a `go.Figure` em Python, passe por `core.graficos.estilizar(fig, altura)` (ou use `barras_horizontais`/`mini`) e mande para o template `core.graficos.para_json(fig)`.
  - No template: `{% grafico fig_json 340 %}`. Clicável: `{% grafico fig_json 340 clique="/notas/detalhe/?tipo=categoria" %}`. O clique abre o modal na URL com `&alvo=<rótulo clicado>` (ou `customdata[0]`, se existir).
  - Paleta categórica: `core.graficos.PALETA`, em ordem fixa e nunca reciclada; a 9ª série vira "Outros" com `CINZA_OUTROS`. Entrada/Saída/Saldo/A pagar usam as constantes `ENTRADA`, `SAIDA`, `SALDO` e `A_PAGAR`.
  - Barras: `barcornerradius` 4. Linhas com espessura 2 e marcadores de 8. Nunca dois eixos y.
  - Tema escuro e cores de texto/grade: quem resolve é `static/js/app.js`, não o Python.
- **Modal:**
  - `GFP.abrirModal(url)` (JS) ou `hx-get` com `hx-target="#modal-conteudo"`, mais `onclick="GFP.abrirModal(this.dataset.url)"`.
  - A view do modal devolve um parcial que estende `core/modal.html`, com os blocos `modal_titulo`, `modal_subtitulo` e `modal_corpo`.
  - Filtros dentro do modal: `hx-get` para a mesma URL com `hx-target="#modal-conteudo"` e `hx-include`.
- **Toast:** numa resposta HTMX use `response["HX-Trigger"] = json.dumps({"toast": "Salvo"})`; no JS, `GFP.toast("Salvo")`.
- **API JSON** (para Tabulator ou fetch):
  - `GFP.api(url, {method: "POST", body: {...}})` já manda o CSRF.
  - Na view, `json.loads(request.body)` e `JsonResponse`, sempre com filtro por usuário. Valide os dados (movimentação válida, valor >= 0, data ISO).
  - Use `@require_http_methods`.
- **Tabulator:**
  - `new Tabulator("#id", {...})`, carregado globalmente pelo `base.html`. Formate valores com `GFP.brl` e minigráficos com `GFP.sparkline(lista)`.
  - Use `layout: "fitColumns"` e `height` para tabelas grandes (virtual DOM).
- **DataFrames:** `core.dados.lancamentos_df(user)`, `notas_df(user)` (com `categoria` efetiva, `loja` e `mes`), `itens_df(user)` e `pagamentos_df(user)`.

## Rodando

```bash
docker compose up -d db                                  # PostgreSQL
.venv/bin/python manage.py migrate
.venv/bin/python manage.py runserver 8000                # http://localhost:8000
.venv/bin/python manage.py test                          # testes
.venv/bin/python manage.py sessao_dev --email EMAIL --saida /caminho/estado.json   # só DEBUG: login para prints
```

Os dados reais do dono estão na conta principal dele (nunca cite esse e-mail no código nem na documentação). Para testes automatizados, crie usuários próprios no `TestCase`, nunca use a conta real. Os PDFs, notas e páginas reais usados nos testes dos leitores ficam em `testes_dados/`, fora do git e do Docker; sem eles, esses testes são pulados. Para prints, use o `sessao_dev` mais Playwright com `storage_state`.

## Testes

Cada app tem `tests.py` com `django.test.TestCase`. Teste, no mínimo:
- O caminho feliz de cada view: status 200 e conteúdo principal.
- **Isolamento:** o usuário B não vê nem altera dados do usuário A (404).
- As regras de cálculo: Saída só paga, defasagem das contas, valor da NF do PJ.
