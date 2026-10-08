/* Notas Paraná e Produtos: tabelas (Tabulator) e o editor das regras de categorização.
   - #tabela-notas: notas do período (dados em <script id="notas-linhas">), categoria editável na célula.
   - [data-tabela-produtos]: todos os produtos, carregados do JSON em data-url; recriada a cada filtro (HTMX).
   - regrasCategoria (Alpine): lista de trecho -> categoria, salva tudo de uma vez. */
(function () {
  "use strict";

  const json = (id) => {
    const el = document.getElementById(id);
    return el ? JSON.parse(el.textContent) : null;
  };
  const escapar = (t) => String(t ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const moeda = (cell) => GFP.brl(cell.getValue());
  const estreita = () => matchMedia("(max-width: 640px)").matches;
  const data = (iso, hora) => {
    if (!iso) return "—";
    const texto = `${iso.slice(8, 10)}/${iso.slice(5, 7)}/${iso.slice(0, 4)}`;
    return hora && iso.length > 10 ? `${texto} <small>${iso.slice(11, 16)}</small>` : texto;
  };

  // ---------- Notas do período ----------
  function tabelaNotas() {
    const el = document.getElementById("tabela-notas");
    if (!el || el.dataset.pronto) return;
    el.dataset.pronto = "1";
    const categorias = json("notas-categorias") || [];
    const opcoes = [{ label: "↺ Automática (pela regra)", value: "" }, ...categorias.map((c) => ({ label: c, value: c }))];
    const tabela = new Tabulator(el, {
      data: json("notas-linhas") || [],
      index: "id",
      height: 520,
      layout: "fitColumns",
      placeholder: "Nenhuma nota no período",
      initialSort: [{ column: "data", dir: "desc" }],
      columns: [
        { title: "Data", field: "data", width: 140, formatter: (c) => data(c.getValue(), true) },
        {
          title: "Estabelecimento", field: "estabelecimento", minWidth: estreita() ? 170 : 220, widthGrow: 3,
          formatter: (c) => escapar(c.getValue()), tooltip: (e, c) => escapar(c.getValue()),
        },
        { title: "Cidade", field: "cidade", minWidth: 110 },
        { title: "Valor", field: "valor", hozAlign: "right", sorter: "number", formatter: moeda, width: 115 },
        { title: "Crédito", field: "credito", hozAlign: "right", sorter: "number", formatter: moeda, width: 100 },
        {
          title: "Categoria", field: "categoria", minWidth: 160, cssClass: "np-editavel",
          editor: "list",
          editorParams: { values: opcoes, autocomplete: true, freetext: true, allowEmpty: true, listOnEmpty: true,
                        itemFormatter: (rotulo) => escapar(rotulo) },
          formatter: (c) => {
            const d = c.getRow().getData();
            return escapar(d.categoria) + (d.auto ? ' <span class="icone np-icone-inline" title="Pela regra">auto_awesome</span>' : "");
          },
        },
        {
          title: "", field: "abrir", width: 46, headerSort: false, hozAlign: "center",
          formatter: () => '<span class="icone np-abrir" title="Ver a nota completa">receipt_long</span>',
        },
      ],
    });

    tabela.on("cellClick", (e, cell) => {
      if (cell.getField() === "categoria") return; // a célula da categoria abre o editor
      GFP.abrirModal(el.dataset.nota.replace("/0/", `/${cell.getRow().getData().id}/`), { tamanho: "medio" });
    });

    tabela.on("cellEdited", async (cell) => {
      const linha = cell.getRow();
      const nova = String(cell.getValue() ?? "").trim();
      try {
        const r = await GFP.api(el.dataset.api, { method: "POST", body: { id: linha.getData().id, categoria: nova } });
        linha.update({ categoria: r.categoria, auto: r.auto });
        if (!categorias.includes(r.categoria)) {
          categorias.push(r.categoria);
          opcoes.push({ label: r.categoria, value: r.categoria });
        }
        GFP.toast(r.auto ? `Voltou para a regra: ${r.categoria}` : `Categoria salva: ${r.categoria}`);
        window.dispatchEvent(new CustomEvent("categoria-salva"));
      } catch (erro) {
        cell.restoreOldValue();
        GFP.toast(erro.message, "erro");
      }
    });

    const busca = document.getElementById("notas-busca");
    busca?.addEventListener("input", () => {
      const termo = busca.value.trim().toLowerCase();
      if (!termo) return tabela.clearFilter();
      tabela.setFilter((d) => [d.estabelecimento, d.loja, d.cidade, d.categoria].join(" ").toLowerCase().includes(termo));
    });
  }

  // ---------- Todos os produtos ----------
  function variacao(cell) {
    const v = cell.getValue();
    if (v === null || v === undefined) return '<span class="texto-3">—</span>';
    const classe = v > 0.0005 ? "np-sobe" : v < -0.0005 ? "np-desce" : "texto-3";
    return `<span class="${classe}">${GFP.pct(v)}</span>`;
  }

  function tabelaProdutos(el) {
    if (el.dataset.pronto) return;
    el.dataset.pronto = "1";
    const numero = { hozAlign: "right", sorter: "number", sorterParams: { alignEmptyValues: "bottom" } };
    const tabela = new Tabulator(el, {
      ajaxURL: el.dataset.url,
      ajaxResponse: (url, params, resposta) => resposta.linhas,
      index: "grupo",
      height: 560,
      layout: "fitColumns",
      placeholder: "Nenhum produto",
      columnDefaults: { vertAlign: "middle", headerWordWrap: true },
      initialSort: [{ column: "gasto", dir: "desc" }],
      columns: [
        {
          title: "Produto", field: "produto", minWidth: estreita() ? 150 : 220, widthGrow: 3, frozen: true,
          formatter: (c) => escapar(c.getValue()), tooltip: (e, c) => escapar(c.getValue()),
        },
        // a cor segue o tema (currentColor) sem redesenhar a tabela
        { title: "Evolução", field: "precos", width: 96, headerSort: false, formatter: (c) => `<span class="np-spark">${GFP.sparkline(c.getValue(), 80, 24, "currentColor")}</span>` },
        { title: "Variação", field: "variacao", width: 96, formatter: variacao, ...numero, headerTooltip: "Último preço pago × primeiro, no período" },
        { title: "Último preço", field: "ultimo", width: 96, formatter: moeda, ...numero },
        { title: "Menor", field: "menor", width: 92, formatter: moeda, ...numero },
        { title: "Maior", field: "maior", width: 92, formatter: moeda, ...numero },
        { title: "Vezes", field: "compras", width: 78, ...numero },
        { title: "Lojas", field: "lojas", width: 76, ...numero },
        { title: "Gasto", field: "gasto", width: 104, formatter: moeda, ...numero },
        { title: "Categoria", field: "categoria", minWidth: 110, formatter: (c) => escapar(c.getValue()) },
        { title: "Última compra", field: "ultima", width: 100, formatter: (c) => data(c.getValue()) },
        { title: "Qtde", field: "quantidade", width: 72, formatter: (c) => GFP.numero(c.getValue()), ...numero },
        { title: "Un.", field: "unidade", width: 58 },
      ],
    });
    tabela.on("rowClick", (e, row) => GFP.abrirModal(`${el.dataset.historico}?grupo=${encodeURIComponent(row.getData().grupo)}`));
    tabela.on("dataLoadError", () => GFP.toast("Não deu para carregar os produtos. Tente de novo.", "erro"));
  }

  // URL dos filtros enxuta: sem campos vazios nem o período quando é o histórico todo (o padrão).
  document.addEventListener("htmx:configRequest", (ev) => {
    const form = ev.detail.elt;
    if (form.id !== "produtos-filtros") return;
    const dados = ev.detail.formData;
    const apagar = (nome) => (dados ? dados.delete(nome) : delete ev.detail.parameters[nome]);
    const valores = Object.fromEntries(dados ? dados.entries() : Object.entries(ev.detail.parameters));
    Object.keys(valores).filter((k) => valores[k] === "").forEach(apagar);
    const de = form.elements.de, ate = form.elements.ate;
    if (de && ate && de.selectedIndex === 0 && ate.selectedIndex === ate.options.length - 1) {
      apagar("de");
      apagar("ate");
    }
  });

  function iniciar() {
    tabelaNotas();
    document.querySelectorAll("[data-tabela-produtos]").forEach(tabelaProdutos);
  }

  document.addEventListener("DOMContentLoaded", iniciar);
  document.addEventListener("htmx:afterSettle", iniciar);
  // Filtros da página Produtos entram no histórico (hx-push-url): a cópia guardada não pode levar gráficos e
  // tabelas já montados, senão o "voltar" do navegador mostra uma imagem morta. Remonta ao restaurar.
  document.addEventListener("htmx:beforeHistorySave", () => {
    document.querySelectorAll(".grafico[data-pronto]").forEach((el) => {
      Plotly.purge(el);
      el.innerHTML = "";
      delete el.dataset.pronto;
    });
    document.querySelectorAll("[data-tabela-produtos][data-pronto]").forEach((el) => {
      const novo = document.createElement("div");
      novo.className = "np-tabela-produtos";
      Object.assign(novo.dataset, { tabelaProdutos: "", url: el.dataset.url, historico: el.dataset.historico });
      Tabulator.findTable(el)[0]?.destroy();
      el.replaceWith(novo);
    });
  });
  // A cópia guardada é tirada depois que o filtro já mudou: os campos voltam a refletir a URL restaurada.
  function filtrosDaUrl() {
    const form = document.getElementById("produtos-filtros");
    if (!form) return;
    const q = new URLSearchParams(location.search), campos = form.elements;
    campos.busca.value = q.get("busca") || "";
    campos.categoria.value = q.get("categoria") || "";
    campos.loja.value = q.get("loja") || "";
    campos.de.value = q.get("de") || campos.de.options[0]?.value;
    campos.ate.value = q.get("ate") || campos.ate.options[campos.ate.options.length - 1]?.value;
    campos.repetidos.checked = q.get("repetidos") === "1";
  }

  document.addEventListener("htmx:historyRestore", () => {
    filtrosDaUrl();
    GFP.renderizarGraficos(document);
    iniciar();
  });

  // ---------- Regras de categorização ----------
  document.addEventListener("alpine:init", () => {
    Alpine.data("regrasCategoria", () => ({
      aberto: false,
      filtro: "",
      salvando: false,
      regras: [],
      original: "",
      init() {
        this.regras = (json("notas-regras") || []).map((r, i) => ({ ...r, id: i }));
        this.original = this.assinatura();
        addEventListener("beforeunload", (ev) => { if (this.mudou) ev.preventDefault(); });
      },
      assinatura() {
        return JSON.stringify(this.regras.map((r) => [r.padrao.trim(), r.categoria.trim()]));
      },
      get mudou() {
        return this.assinatura() !== this.original;
      },
      get visiveis() {
        const termo = this.filtro.trim().toLowerCase();
        if (!termo) return this.regras;
        return this.regras.filter((r) => `${r.padrao} ${r.categoria}`.toLowerCase().includes(termo));
      },
      adicionar() {
        this.filtro = "";
        this.regras.unshift({ padrao: "", categoria: "", id: Date.now() });
        this.$nextTick(() => this.$refs.lista.querySelector(".np-regra:not(.np-regra-cabecalho) input")?.focus());
      },
      remover(regra) {
        this.regras = this.regras.filter((r) => r !== regra);
      },
      async salvar() {
        this.salvando = true;
        try {
          const corpo = { regras: this.regras.map(({ padrao, categoria }) => ({ padrao, categoria })) };
          const r = await GFP.api(this.$root.dataset.api, { method: "POST", body: corpo });
          this.original = this.assinatura();
          GFP.toast(`Regras salvas (${r.total}). Atualizando…`);
          setTimeout(() => location.reload(), 700);
        } catch (erro) {
          GFP.toast(erro.message, "erro");
        } finally {
          this.salvando = false;
        }
      },
    }));
  });
})();
