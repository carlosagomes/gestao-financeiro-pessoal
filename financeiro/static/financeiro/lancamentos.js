/* Lançamentos: a planilha editável (Tabulator).
   Cada edição grava na hora (PATCH na API) e o servidor registra o histórico; se ele recusar, a célula volta
   ao valor anterior. Os filtros ficam na URL (?ano=&mes=&mov=&situacao=&busca=) para dar para compartilhar. */
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const elTabela = $("grade-lancamentos");
  const form = $("filtros");
  if (!elTabela || !form || typeof Tabulator === "undefined") return;

  const API = elTabela.dataset.api;
  let dados = JSON.parse($("dados-lancamentos").textContent);

  const plural = (n, um, varios) => `${n.toLocaleString("pt-BR")} ${n === 1 ? um : varios}`;
  const dataBR = (iso) => (iso ? `${iso.slice(8, 10)}/${iso.slice(5, 7)}/${iso.slice(0, 4)}` : "");
  const escapar = (t) => String(t ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const pendente = (l) => l.movimentacao === "Saída" && !l.pago;
  const ordem = (a, b) => a.localeCompare(b, "pt-BR", { sensitivity: "base" });

  // Valor digitado como no Brasil ("1.234,56", "12,5", "R$ 80") ou com ponto decimal ("12.5").
  // Texto que não é número segue como está: o servidor recusa e a célula volta.
  function numeroBR(valor) {
    if (typeof valor === "number") return valor;
    let t = String(valor ?? "").replace(/R\$|\s/g, "");
    if (t.includes(",")) t = t.replace(/\./g, "").replace(",", ".");
    else if (/^\d{1,3}(\.\d{3})+$/.test(t)) t = t.replace(/\./g, "");
    const n = Number(t);
    return t !== "" && Number.isFinite(n) ? n : valor;
  }

  function categoriasDe(movimentacao) {
    const c = dados.opcoes.categorias;
    return c[movimentacao] || [...new Set([...c["Entrada"], ...c["Saída"]])].sort(ordem);
  }

  // ---------- tabela ----------
  const tabela = new Tabulator(elTabela, {
    data: dados.linhas,
    index: "id",
    layout: "fitColumns",
    maxHeight: "70vh",
    placeholder: "Nenhum lançamento com estes filtros",
    columnDefaults: { headerSortTristate: true },
    columns: [
      { formatter: "rowSelection", titleFormatter: "rowSelection", hozAlign: "center", headerHozAlign: "center",
        headerSort: false, width: 44, minWidth: 44, resizable: false, cssClass: "col-selecao" },
      { title: "Data", field: "data", editor: "date", sorter: "string", width: 118, minWidth: 112,
        formatter: (c) => dataBR(c.getValue()) },
      { title: "Movimentação", field: "movimentacao", width: 136, minWidth: 128, editor: "list",
        editorParams: { values: ["Entrada", "Saída"] },
        formatter: (c) => `<span class="etiqueta ${c.getValue() === "Entrada" ? "verde" : "vermelho"}">${escapar(c.getValue())}</span>` },
      { title: "Banco", field: "banco", minWidth: 110, widthGrow: 1, editor: "list",
        editorParams: () => ({ values: dados.opcoes.bancos, autocomplete: true, freetext: true, allowEmpty: true,
                               listOnEmpty: true, placeholderEmpty: "Nenhum banco com esse nome", itemFormatter: escapar }) },
      { title: "Categoria", field: "categoria", minWidth: 140, widthGrow: 1.3, editor: "list",
        editorParams: (cell) => ({ values: categoriasDe(cell.getRow().getData().movimentacao), autocomplete: true,
                                   listOnEmpty: true, placeholderEmpty: "Crie em Categorias e bancos", itemFormatter: escapar }) },
      { title: "Descrição", field: "descricao", minWidth: 200, widthGrow: 2.6, editor: "input" },
      { title: "Valor", field: "valor", minWidth: 124, width: 136, hozAlign: "right", headerHozAlign: "right",
        sorter: "number", editor: "input", editorParams: { selectContents: true }, mutatorEdit: numeroBR,
        formatter: (c) => {
          const l = c.getRow().getData();
          const cor = l.movimentacao === "Entrada" ? "cor-entrada" : l.pago ? "cor-saida" : "cor-pagar";
          return `<span class="${cor}">${GFP.brl(c.getValue())}</span>`;
        } },
      { title: "Pago", field: "pago", width: 76, minWidth: 70, hozAlign: "center", headerHozAlign: "center",
        sorter: "boolean", formatter: "tickCross",
        formatterParams: {
          tickElement: '<span class="icone pago-sim" title="Pago: clique para desmarcar">check_circle</span>',
          crossElement: '<span class="icone pago-nao" title="Não pago: clique para marcar">radio_button_unchecked</span>',
        },
        cellClick: (ev, cell) => cell.setValue(!cell.getValue()) },
    ],
  });

  // ---------- salvar edições ----------
  async function salvar(cell) {
    const campo = cell.getField();
    const linha = cell.getRow();
    const valor = cell.getValue() ?? "";
    if (String(valor) === String(cell.getOldValue() ?? "")) return;
    const el = cell.getElement();
    el.classList.add("salvando");
    try {
      const r = await GFP.api(`${API}${linha.getData().id}/`, { method: "PATCH", body: { [campo]: valor } });
      await linha.update(r.linha);
      linha.reformat();
      if (r.mudou.length) {
        GFP.toast(campo === "pago" ? (r.linha.pago ? "Marcado como pago" : "Marcado como não pago") : "Salvo");
      }
    } catch (erro) {
      cell.restoreOldValue();
      linha.reformat();
      GFP.toast(erro.message, "erro");
    } finally {
      el.classList.remove("salvando");  // a célula pode ter sido redesenhada: usa o elemento guardado
      atualizarTela();
    }
  }
  tabela.on("cellEdited", salvar);
  tabela.on("rowSelectionChanged", () => atualizarBotoes());

  // ---------- totais e botões (do que está na tela) ----------
  function kpi(nome, valor, detalhe) {
    const el = document.querySelector(`[data-kpi="${nome}"]`);
    el.querySelector(".kpi-valor").textContent = GFP.brl(Math.round(valor * 100) / 100);
    el.querySelector(".kpi-detalhe").textContent = detalhe;
  }

  function atualizarTela() {
    const linhas = tabela.getData();
    let entradas = 0, pagas = 0, aPagar = 0, nEntradas = 0, nPagas = 0, nPendentes = 0;
    for (const l of linhas) {
      const v = Number(l.valor) || 0;
      if (l.movimentacao === "Entrada") { entradas += v; nEntradas++; }
      else if (l.pago) { pagas += v; nPagas++; }
      else { aPagar += v; nPendentes++; }
    }
    kpi("entradas", entradas, plural(nEntradas, "entrada", "entradas"));
    kpi("saidas", pagas, plural(nPagas, "saída paga", "saídas pagas"));
    kpi("a_pagar", aPagar, nPendentes ? plural(nPendentes, "conta pendente", "contas pendentes") : "nada pendente");
    kpi("saldo", entradas - pagas, "entradas − saídas pagas");
    $("contagem").textContent = plural(linhas.length, "lançamento", "lançamentos");
    atualizarBotoes();
  }

  function rotular(botao, texto, dica) {
    botao.querySelector(".rotulo-botao").textContent = texto;
    botao.title = dica || "";
  }

  function paraMarcar() {
    const selecionadas = tabela.getSelectedData();
    return { selecionadas: selecionadas.length, ids: (selecionadas.length ? selecionadas : tabela.getData()).filter(pendente).map((l) => l.id) };
  }

  function atualizarBotoes() {
    const { selecionadas, ids } = paraMarcar();
    const bMarcar = $("b-marcar");
    bMarcar.classList.toggle("oculto", !ids.length);
    rotular(bMarcar, `Marcar ${ids.length} como paga${ids.length === 1 ? "" : "s"}`,
      selecionadas ? "As saídas não pagas entre as linhas marcadas" : "Todas as saídas não pagas da tabela");

    const bExcluir = $("b-excluir");
    bExcluir.disabled = !selecionadas;
    rotular(bExcluir, selecionadas ? `Excluir ${selecionadas}` : "Excluir selecionados");

    const ctx = dados.contexto, bCopiar = $("b-copiar");
    bCopiar.disabled = !ctx.mes || !ctx.anterior?.qtd;
    if (!ctx.mes) rotular(bCopiar, "Copiar mês anterior", "Escolha um mês");
    else rotular(bCopiar, `Copiar ${ctx.anterior.nome}`, ctx.anterior.qtd
      ? `Cria neste mês os ${ctx.anterior.qtd} lançamentos de ${ctx.anterior.nome}, todos como não pagos`
      : `${ctx.anterior.nome} não tem lançamentos`);
  }

  function atualizarTitulo() {
    const ano = form.ano.value;
    const mes = form.mes.options[form.mes.selectedIndex];
    $("titulo-tabela").textContent = form.mes.value === "0" ? `Todos os meses de ${ano}` : `${mes.text}/${ano}`;
  }

  // ---------- filtros ----------
  let pedido = 0;
  async function carregar() {
    const params = new URLSearchParams(new FormData(form));
    history.replaceState(null, "", `${location.pathname}?${params}`);
    const meu = ++pedido;
    elTabela.classList.add("atualizando");
    try {
      const r = await GFP.api(`${API}?${params}`);
      if (meu !== pedido) return;
      dados = r;
      await tabela.replaceData(r.linhas);
      tabela.deselectRow();
      atualizarTitulo();
      atualizarTela();
    } catch (erro) {
      GFP.toast(erro.message, "erro");
    } finally {
      if (meu === pedido) elTabela.classList.remove("atualizando");
    }
  }

  let espera;
  form.addEventListener("change", (ev) => { if (ev.target.name !== "busca") carregar(); });
  form.addEventListener("submit", (ev) => { ev.preventDefault(); clearTimeout(espera); carregar(); });
  form.busca.addEventListener("input", () => { clearTimeout(espera); espera = setTimeout(carregar, 300); });

  // ---------- ações ----------
  $("b-adicionar").addEventListener("click", async () => {
    try {
      const r = await GFP.api(API, { method: "POST", body: dados.contexto.padrao });
      const linha = await tabela.addRow(r.linha);
      atualizarTela();
      await tabela.scrollToRow(linha, "nearest", false).catch(() => {});
      linha.getCell("descricao").edit(true);
      GFP.toast("Lançamento criado: preencha a descrição e o valor");
    } catch (erro) {
      GFP.toast(erro.message, "erro");
    }
  });

  $("b-excluir").addEventListener("click", async () => {
    const ids = tabela.getSelectedData().map((l) => l.id);
    if (!ids.length || !confirm(`Excluir ${plural(ids.length, "lançamento", "lançamentos")}? O histórico guarda o que foi excluído.`)) return;
    try {
      const r = await GFP.api(`${API}excluir/`, { method: "POST", body: { ids } });
      await tabela.deleteRow(ids);
      atualizarTela();
      GFP.toast(plural(r.excluidos, "lançamento excluído", "lançamentos excluídos"));
    } catch (erro) {
      GFP.toast(erro.message, "erro");
    }
  });

  $("b-marcar").addEventListener("click", async () => {
    const { ids } = paraMarcar();
    if (!ids.length || !confirm(`Marcar ${plural(ids.length, "saída", "saídas")} como paga${ids.length === 1 ? "" : "s"}?`)) return;
    try {
      const r = await GFP.api(`${API}marcar-pagas/`, { method: "POST", body: { ids } });
      await tabela.updateData(r.linhas);
      r.linhas.forEach((l) => { const linha = tabela.getRow(l.id); if (linha) linha.reformat(); });
      tabela.deselectRow();
      atualizarTela();
      GFP.toast(plural(r.marcados, "saída marcada como paga", "saídas marcadas como pagas"));
    } catch (erro) {
      GFP.toast(erro.message, "erro");
    }
  });

  $("b-copiar").addEventListener("click", async () => {
    const ctx = dados.contexto;
    const destino = form.mes.options[form.mes.selectedIndex].text.toLowerCase();
    const corpo = { ano: ctx.ano, mes: ctx.mes, confirmar: false };
    if (ctx.qtd_mes) {
      if (!confirm(`${destino[0].toUpperCase() + destino.slice(1)} já tem ${plural(ctx.qtd_mes, "lançamento", "lançamentos")}. ` +
                   `Copiar mesmo assim os ${ctx.anterior.qtd} de ${ctx.anterior.nome}? Eles entram como não pagos.`)) return;
      corpo.confirmar = true;
    }
    try {
      let r;
      try {
        r = await GFP.api(`${API}copiar-mes/`, { method: "POST", body: corpo });
      } catch (erro) {
        if (!erro.dados?.precisa_confirmar || !confirm(`${erro.message}. Copiar mesmo assim?`)) throw erro;
        r = await GFP.api(`${API}copiar-mes/`, { method: "POST", body: { ...corpo, confirmar: true } });
      }
      await carregar();
      GFP.toast(`${plural(r.copiados, "lançamento copiado", "lançamentos copiados")} de ${ctx.anterior.nome}`);
    } catch (erro) {
      if (erro.dados?.precisa_confirmar) return;
      GFP.toast(erro.message, "erro");
    }
  });

  // Nova categoria ou banco (modal): as listas das colunas passam a ter a opção nova.
  document.addEventListener("opcoesMudaram", (ev) => {
    dados.opcoes = { categorias: ev.detail.categorias, bancos: ev.detail.bancos };
  });

  tabela.on("tableBuilt", () => { atualizarTela(); });
})();
