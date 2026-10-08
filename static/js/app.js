/* Gestão Financeira Pessoal: comportamento comum a todas as páginas.
   - Gráficos: <div class="grafico" data-figura='{plotly json}' data-clique="/url">, gerados por {% grafico %}.
   - Modal: GFP.abrirModal(url) carrega a URL (HTML parcial) no <dialog id="modal">.
   - Toasts: GFP.toast(msg) ou cabeçalho HX-Trigger: {"toast": "mensagem"} numa resposta HTMX.
   - API JSON: GFP.api(url, {method, body}) já com o token CSRF. */
(function () {
  "use strict";

  const css = (nome) => getComputedStyle(document.documentElement).getPropertyValue(nome).trim();
  const csrf = () => document.querySelector("meta[name=csrf-token]")?.content || "";

  // Paleta categórica validada (claro → escuro): a cor segue a série; no tema escuro troca pelo par.
  const PALETA_ESCURA = {
    "#2a78d6": "#3987e5", "#eb6834": "#d95926", "#1baf7a": "#199e70", "#eda100": "#c98500",
    "#e87ba4": "#d55181", "#008300": "#008300", "#4a3aa7": "#9085e9", "#e34948": "#e66767",
    // cores de significado (Entrada, Saída, Saldo, A pagar)
    "#137333": "#4cc27a", "#b3261e": "#f2766b", "#1a56db": "#7aa7ff", "#b45309": "#f0a24a",
  };
  const escuro = () => {
    const tema = document.documentElement.dataset.tema;
    return tema === "escuro" || (!tema && matchMedia("(prefers-color-scheme: dark)").matches);
  };

  function trocarCores(valor) {
    if (typeof valor === "string") return PALETA_ESCURA[valor.toLowerCase()] || valor;
    if (Array.isArray(valor)) return valor.map(trocarCores);
    return valor;
  }

  function aplicarTema(figura) {
    const texto = css("--grafico-texto"), grade = css("--grafico-grade"), fundo = css("--superficie");
    const l = figura.layout || (figura.layout = {});
    l.font = Object.assign({ family: "Inter, system-ui, sans-serif", size: 12 }, l.font, { color: texto });
    l.paper_bgcolor = "rgba(0,0,0,0)";
    l.plot_bgcolor = "rgba(0,0,0,0)";
    l.hoverlabel = Object.assign({ bgcolor: css("--superficie"), bordercolor: css("--borda-forte"), font: { color: css("--texto") } }, l.hoverlabel);
    for (const chave of Object.keys(l)) {
      if (/^[xy]axis\d*$/.test(chave)) {
        l[chave].gridcolor = grade;
        l[chave].zerolinecolor = grade;
        l[chave].linecolor = grade;
      }
    }
    if (l.legend) l.legend.font = Object.assign({}, l.legend.font, { color: texto });
    for (const traco of figura.data || []) {
      if (traco.type === "bar" && traco.marker) {
        traco.marker.line = Object.assign({}, traco.marker.line, { color: fundo });
      }
      if (escuro()) {
        if (traco.marker?.color) traco.marker.color = trocarCores(traco.marker.color);
        if (traco.line?.color) traco.line.color = trocarCores(traco.line.color);
        if (traco.fillcolor) traco.fillcolor = trocarCores(traco.fillcolor);
      }
    }
    return figura;
  }

  // Monitores grandes: gráficos mais altos, para acompanhar a largura (os minigráficos dos cartões não mudam).
  const fatorAltura = () => (innerWidth >= 2400 ? 1.4 : innerWidth >= 1800 ? 1.2 : 1);

  function renderizarGraficos(raiz) {
    (raiz || document).querySelectorAll(".grafico[data-figura]").forEach((el) => {
      if (el.dataset.pronto) return;
      el.dataset.pronto = "1";
      const figura = aplicarTema(JSON.parse(el.dataset.figura));
      const altura = figura.layout.height;
      if (altura >= 200) {
        figura.layout.height = Math.round(altura * fatorAltura());
        el.style.minHeight = figura.layout.height + "px";
      }
      const config = { displayModeBar: false, responsive: true };
      Plotly.newPlot(el, figura.data, figura.layout, config).then(() => {
        const url = el.dataset.clique;
        if (!url) return;
        el.classList.add("clicavel");
        el.on("plotly_click", (ev) => {
          const p = ev.points[0];
          let alvo = p.customdata;
          if (Array.isArray(alvo)) alvo = alvo[0];
          if (alvo === undefined || alvo === null) alvo = p.data.orientation === "h" ? p.y : p.x;
          GFP.abrirModal(url + (url.includes("?") ? "&" : "?") + "alvo=" + encodeURIComponent(alvo));
        });
      });
    });
  }

  function redesenharGraficos() {
    document.querySelectorAll(".grafico[data-pronto]").forEach((el) => {
      delete el.dataset.pronto;
      Plotly.purge(el);
    });
    renderizarGraficos(document);
  }

  // ---------- modal ----------
  const modal = () => document.getElementById("modal");

  function abrirModal(url, opcoes = {}) {
    const dlg = modal();
    const corpo = document.getElementById("modal-conteudo");
    dlg.classList.toggle("medio", opcoes.tamanho === "medio");
    corpo.innerHTML = '<div class="carregando"><span class="icone">progress_activity</span></div>';
    if (!dlg.open) dlg.showModal();
    return htmx.ajax("GET", url, { target: "#modal-conteudo", swap: "innerHTML" });
  }

  function fecharModal() {
    const dlg = modal();
    if (dlg?.open) dlg.close();
  }

  // ---------- toasts ----------
  function toast(mensagem, tipo = "") {
    const caixa = document.getElementById("toasts");
    const el = document.createElement("div");
    el.className = "toast " + tipo;
    el.innerHTML = '<span class="icone"></span><span></span>';
    el.firstChild.textContent = tipo === "erro" ? "error" : "check_circle";
    el.lastChild.textContent = mensagem;
    caixa.appendChild(el);
    setTimeout(() => el.remove(), 4500);
  }

  // ---------- API ----------
  async function api(url, opcoes = {}) {
    const resposta = await fetch(url, {
      method: opcoes.method || "GET",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf(), "X-Requested-With": "fetch" },
      body: opcoes.body !== undefined ? JSON.stringify(opcoes.body) : undefined,
      credentials: "same-origin",
    });
    let dados = null;
    try { dados = await resposta.json(); } catch (e) { /* sem corpo */ }
    if (!resposta.ok) {
      const erro = new Error(dados?.erro || `Erro ${resposta.status}`);
      erro.dados = dados;
      throw erro;
    }
    return dados;
  }

  // ---------- formatos ----------
  const fmtMoeda = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" });
  const fmtNumero = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 3 });
  const brl = (v) => (v === null || v === undefined || v === "" ? "—" : fmtMoeda.format(Number(v)));
  const numero = (v) => (v === null || v === undefined || v === "" ? "—" : fmtNumero.format(Number(v)));
  const pct = (v) => (v === null || v === undefined ? "—" : (v > 0 ? "+" : "") + (v * 100).toFixed(1).replace(".", ",") + "%");

  // Minigráfico SVG (linha) para células de tabela.
  function sparkline(valores, largura = 90, altura = 26, cor) {
    const v = (valores || []).filter((x) => x !== null && !isNaN(x));
    if (v.length < 2) return "";
    const min = Math.min(...v), max = Math.max(...v), faixa = max - min || 1;
    const pts = v.map((x, i) => `${(i / (v.length - 1)) * (largura - 4) + 2},${altura - 3 - ((x - min) / faixa) * (altura - 6)}`);
    const c = cor || css("--primaria");
    return `<svg width="${largura}" height="${altura}" viewBox="0 0 ${largura} ${altura}" aria-hidden="true"><polyline fill="none" stroke="${c}" stroke-width="1.6" stroke-linejoin="round" stroke-linecap="round" points="${pts.join(" ")}"/></svg>`;
  }

  // ---------- tema ----------
  function definirTema(tema) {
    if (tema) document.documentElement.dataset.tema = tema;
    else delete document.documentElement.dataset.tema;
    try { tema ? localStorage.setItem("tema", tema) : localStorage.removeItem("tema"); } catch (e) { /* privado */ }
    redesenharGraficos();
  }

  function alternarTema() {
    definirTema(escuro() ? "claro" : "escuro");
  }

  // ---------- inicialização ----------
  document.addEventListener("DOMContentLoaded", () => {
    renderizarGraficos(document);
    const topo = document.querySelector(".topo");
    if (topo) addEventListener("scroll", () => topo.classList.toggle("rolado", scrollY > 4), { passive: true });
    modal()?.addEventListener("click", (ev) => { if (ev.target === modal()) fecharModal(); });
    modal()?.addEventListener("close", () => { document.getElementById("modal-conteudo").innerHTML = ""; });
    // Janela arrastada para outro monitor ou redimensionada: redesenha quando muda a faixa de altura.
    let fator = fatorAltura(), espera;
    addEventListener("resize", () => {
      clearTimeout(espera);
      espera = setTimeout(() => { if (fatorAltura() !== fator) { fator = fatorAltura(); redesenharGraficos(); } }, 250);
    });
    matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
      if (!document.documentElement.dataset.tema) redesenharGraficos();
    });
  });

  document.addEventListener("htmx:configRequest", (ev) => { ev.detail.headers["X-CSRFToken"] = csrf(); });
  document.addEventListener("htmx:afterSettle", (ev) => renderizarGraficos(ev.target));
  document.addEventListener("htmx:responseError", (ev) => {
    toast(ev.detail.xhr.status === 403 ? "Sem permissão para isso." : "Algo deu errado. Tente de novo.", "erro");
  });
  document.addEventListener("toast", (ev) => toast(ev.detail.value || ev.detail.mensagem || "Pronto", ev.detail.tipo));
  document.addEventListener("fecharModal", fecharModal);

  window.GFP = { abrirModal, fecharModal, toast, api, brl, numero, pct, sparkline, renderizarGraficos, alternarTema, definirTema, css };
})();
