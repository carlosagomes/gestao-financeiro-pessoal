/* Janela remota de login: mostra a tela do Chromium do servidor (quadros JPEG pelo WebSocket) e devolve
   cliques, rolagem e teclado. O protocolo está descrito em integracoes/consumers.py.
   O teclado passa por um campo invisível (#janela-teclado): assim acentos, colar e teclado de celular funcionam. */
(function () {
  "use strict";

  const raiz = document.getElementById("janela");
  if (!raiz) return;
  const $ = (id) => document.getElementById(id);
  const canvas = $("janela-canvas"), ctx = canvas.getContext("2d");
  const tela = $("janela-tela"), teclado = $("janela-teclado"), status = $("janela-status"), dica = $("janela-dica");
  const cobertura = $("janela-cobertura"), coberturaTexto = $("janela-cobertura-texto");
  const cartao = $("janela-cartao"), fim = $("janela-fim");
  const botaoFechar = $("janela-fechar"), botaoRecarregar = $("janela-recarregar"), botaoReabrir = $("janela-reabrir");

  const ESPECIAIS = new Set(["Enter", "Backspace", "Tab", "Delete", "Escape", "ArrowUp", "ArrowDown", "ArrowLeft",
    "ArrowRight", "Home", "End", "PageUp", "PageDown"]);
  const ROTULOS = { conectando: "Conectando…", reconectando: "Reconectando…", abrindo: "Abrindo o site…", ao_vivo: "Ao vivo",
    salvando: "Salvando…", sucesso: "Conectado", erro: "Não conectou", fechado: "Fechada" };
  const MAX_TENTATIVAS = 6;
  const INTERVALO_MOVER_MS = 60;

  let ws = null, terminou = false, tentativas = 0, timerReconexao = null, estadoAtual = "conectando";
  let largura = canvas.width, altura = canvas.height, temQuadro = false, desenhando = false, quadroPendente = null;
  let textoPendente = "", timerTexto = null;
  let moverPendente = null, timerMover = null, ultimoMover = 0, rolagem = null;
  let pressionado = null, ultimoClique = { t: 0, x: 0, y: 0, n: 0 };

  // ---------- conexão ----------
  function endereco() {
    const protocolo = location.protocol === "https:" ? "wss:" : "ws:";
    let url = `${protocolo}//${location.host}${raiz.dataset.ws}`;
    const disponivel = tela.clientWidth - 12;
    if (disponivel < 760) { // celular: pede uma página remota do tamanho da tela
      const l = Math.max(360, Math.min(1280, Math.round(disponivel)));
      const a = Math.max(480, Math.min(900, Math.round(innerHeight - 160)));
      url += `?largura=${l}&altura=${a}`;
    }
    return url;
  }

  function conectar() {
    clearTimeout(timerReconexao);
    terminou = false;
    mostrarStatus(tentativas ? "reconectando" : "conectando");
    if (!temQuadro) cobrir("Abrindo o navegador…");
    ws = new WebSocket(endereco());
    ws.binaryType = "arraybuffer";
    ws.onopen = () => { tentativas = 0; botoes(true); };
    ws.onmessage = (ev) => (typeof ev.data === "string" ? receber(JSON.parse(ev.data)) : desenhar(ev.data));
    ws.onclose = () => {
      ws = null;
      botoes(false);
      if (terminou) return;
      if (tentativas >= MAX_TENTATIVAS) {
        finalizar("erro", "Sem conexão com o servidor", "Confira sua internet e abra a janela de novo.");
        return;
      }
      tentativas += 1;
      mostrarStatus("reconectando");
      cobrir("A conexão caiu. Reconectando…");
      timerReconexao = setTimeout(conectar, Math.min(1000 * 2 ** (tentativas - 1), 8000));
    };
  }

  function reabrir() {
    fim.classList.add("oculto");
    cartao.classList.remove("oculto");
    temQuadro = false;
    tentativas = 0;
    conectar();
  }

  function enviar(msg) {
    if (msg.tipo !== "texto") despejarTexto(); // mantém a ordem: o texto digitado antes vai primeiro
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(msg));
  }

  function receber(m) {
    switch (m.tipo) {
      case "tela":
        largura = m.largura;
        altura = m.altura;
        ajustar();
        break;
      case "estado":
        mostrarStatus(m.estado, m.mensagem);
        break;
      case "aviso":
        if (window.GFP) GFP.toast(m.mensagem);
        break;
      case "sucesso":
        finalizar("sucesso", m.mensagem, "Pode sair desta página: a busca continua e o andamento aparece no menu lateral.");
        document.body.dispatchEvent(new CustomEvent("sincronizou")); // atualiza o status do menu lateral
        break;
      case "erro":
        finalizar("erro", m.codigo === "outra_aba" ? "Janela aberta em outra aba" : "Não deu para conectar", m.mensagem, m.codigo);
        break;
      case "fechado":
        finalizar("fechado", "Janela fechada", "Abra de novo quando quiser fazer o login.");
        break;
    }
  }

  // ---------- estado na tela ----------
  function mostrarStatus(estado, mensagem) {
    estadoAtual = estado;
    status.dataset.estado = estado;
    status.querySelector(".rotulo").textContent = ROTULOS[estado] || estado;
    status.title = mensagem || "";
    if (estado === "salvando") cobrir(mensagem || "Salvando…");
    else if ((estado === "ao_vivo" || estado === "abrindo") && temQuadro) descobrir();
  }

  function cobrir(texto) {
    coberturaTexto.textContent = texto;
    cobertura.classList.remove("oculto");
  }

  function descobrir() {
    cobertura.classList.add("oculto");
  }

  function botoes(ativos) {
    botaoFechar.disabled = !ativos;
    botaoRecarregar.disabled = !ativos;
  }

  function finalizar(tipo, titulo, texto, codigo) {
    terminou = true;
    clearTimeout(timerReconexao);
    mostrarStatus(tipo);
    cartao.classList.add("oculto");
    fim.dataset.tipo = tipo;
    $("janela-fim-icone").firstElementChild.textContent = { sucesso: "check_circle", erro: "error", fechado: "cancel" }[tipo];
    $("janela-fim-titulo").textContent = titulo;
    $("janela-fim-texto").textContent = texto || "";
    $("janela-ir-dados").classList.toggle("oculto", tipo !== "sucesso");
    botaoReabrir.classList.toggle("oculto", tipo === "sucesso");
    botaoReabrir.lastChild.textContent = codigo === "outra_aba" ? " Usar nesta aba" : " Abrir de novo";
    fim.classList.remove("oculto");
    if (ws) { try { ws.close(); } catch (e) { /* já fechado */ } }
  }

  // ---------- quadros ----------
  function decodificar(dados) {
    const blob = new Blob([dados], { type: "image/jpeg" });
    if (window.createImageBitmap) return createImageBitmap(blob);
    return new Promise((ok, falha) => {
      const img = new Image(), url = URL.createObjectURL(blob);
      img.onload = () => { URL.revokeObjectURL(url); ok(img); };
      img.onerror = falha;
      img.src = url;
    });
  }

  async function desenhar(dados) {
    if (desenhando) { quadroPendente = dados; return; } // decodificação lenta: fica só o mais novo
    desenhando = true;
    try {
      const imagem = await decodificar(dados);
      if (canvas.width !== imagem.width || canvas.height !== imagem.height) {
        canvas.width = imagem.width;
        canvas.height = imagem.height;
        ajustar();
      }
      ctx.drawImage(imagem, 0, 0);
      if (imagem.close) imagem.close();
      temQuadro = true;
      if (estadoAtual !== "salvando" && estadoAtual !== "reconectando") descobrir();
    } catch (e) { /* quadro com defeito: espera o próximo */ }
    desenhando = false;
    if (quadroPendente) { const proximo = quadroPendente; quadroPendente = null; desenhar(proximo); }
  }

  // Cabe na largura e na altura da janela do navegador, sem distorcer e sem passar do tamanho real.
  function ajustar() {
    const estilo = getComputedStyle(tela);
    const larguraUtil = tela.clientWidth - parseFloat(estilo.paddingLeft) - parseFloat(estilo.paddingRight);
    const alturaUtil = Math.max(300, innerHeight - 110);
    const escala = Math.min(1, larguraUtil / largura, alturaUtil / altura);
    canvas.style.width = Math.floor(largura * escala) + "px";
    canvas.style.height = Math.floor(altura * escala) + "px";
  }

  // ---------- mouse e toque ----------
  function ponto(ev) {
    const r = canvas.getBoundingClientRect();
    return { x: Math.round((ev.clientX - r.left) * largura / r.width), y: Math.round((ev.clientY - r.top) * altura / r.height) };
  }

  function mover(p) {
    moverPendente = p;
    if (!timerMover) timerMover = setTimeout(despejarMover, Math.max(0, INTERVALO_MOVER_MS - (performance.now() - ultimoMover)));
  }

  function despejarMover() {
    clearTimeout(timerMover);
    timerMover = null;
    if (!moverPendente) return;
    const p = moverPendente;
    moverPendente = null;
    ultimoMover = performance.now();
    enviar({ tipo: "mover", x: p.x, y: p.y });
  }

  function rolar(p, dx, dy) {
    if (!rolagem) {
      rolagem = { x: p.x, y: p.y, dx: 0, dy: 0 };
      setTimeout(() => {
        const r = rolagem;
        rolagem = null;
        enviar({ tipo: "rolar", x: r.x, y: r.y, dx: Math.round(r.dx), dy: Math.round(r.dy) });
      }, 50);
    }
    rolagem.x = p.x;
    rolagem.y = p.y;
    rolagem.dx += dx;
    rolagem.dy += dy;
  }

  canvas.addEventListener("pointerdown", (ev) => {
    if (ev.button !== 0) return;
    ev.preventDefault();
    const toque = ev.pointerType !== "mouse";
    if (!toque) focarTeclado(); // no celular o teclado só abre pelo botão "Teclado"
    pressionado = { ...ponto(ev), cx: ev.clientX, cy: ev.clientY, ux: ev.clientX, uy: ev.clientY, arrastando: false, toque };
    if (canvas.setPointerCapture) canvas.setPointerCapture(ev.pointerId);
  });

  canvas.addEventListener("pointermove", (ev) => {
    const p = ponto(ev);
    if (!pressionado) { if (ev.pointerType === "mouse") mover(p); return; }
    if (!pressionado.arrastando && Math.hypot(ev.clientX - pressionado.cx, ev.clientY - pressionado.cy) < 6) return;
    if (pressionado.toque) { // arrastar o dedo rola a página remota
      const f = largura / canvas.getBoundingClientRect().width;
      rolar(p, (pressionado.ux - ev.clientX) * f, (pressionado.uy - ev.clientY) * f);
      Object.assign(pressionado, { ux: ev.clientX, uy: ev.clientY, arrastando: true });
      return;
    }
    if (!pressionado.arrastando) {
      pressionado.arrastando = true;
      enviar({ tipo: "pressionar", x: pressionado.x, y: pressionado.y });
    }
    mover(p);
  });

  canvas.addEventListener("pointerup", (ev) => {
    if (!pressionado) return;
    const p = ponto(ev), feito = pressionado;
    pressionado = null;
    if (feito.arrastando) {
      if (!feito.toque) { despejarMover(); enviar({ tipo: "soltar", x: p.x, y: p.y }); }
      return;
    }
    const agora = performance.now();
    const perto = Math.abs(feito.x - ultimoClique.x) < 6 && Math.abs(feito.y - ultimoClique.y) < 6;
    const n = agora - ultimoClique.t < 450 && perto ? Math.min(ultimoClique.n + 1, 3) : 1;
    ultimoClique = { t: agora, x: feito.x, y: feito.y, n };
    enviar({ tipo: "clique", x: feito.x, y: feito.y, cliques: n });
  });

  canvas.addEventListener("pointercancel", () => { pressionado = null; });
  canvas.addEventListener("contextmenu", (ev) => ev.preventDefault());
  canvas.addEventListener("wheel", (ev) => {
    ev.preventDefault();
    const f = ev.deltaMode === 1 ? 16 : ev.deltaMode === 2 ? altura : 1;
    rolar(ponto(ev), ev.deltaX * f, ev.deltaY * f);
  }, { passive: false });

  // ---------- teclado ----------
  function focarTeclado() {
    teclado.focus({ preventScroll: true });
  }

  function enviarTexto(texto) {
    textoPendente += texto;
    clearTimeout(timerTexto);
    timerTexto = setTimeout(despejarTexto, 30);
  }

  function despejarTexto() {
    clearTimeout(timerTexto);
    if (!textoPendente) return;
    const texto = textoPendente;
    textoPendente = "";
    for (let i = 0; i < texto.length; i += 200) enviar({ tipo: "texto", texto: texto.slice(i, i + 200) });
  }

  function despejarCampo() {
    if (!teclado.value) return;
    enviarTexto(teclado.value);
    teclado.value = "";
  }

  teclado.addEventListener("keydown", (ev) => {
    if (ev.isComposing || ev.keyCode === 229) return;
    const ctrl = ev.ctrlKey || ev.metaKey;
    const modificadores = [];
    if (ev.shiftKey) modificadores.push("Shift");
    if (ctrl) modificadores.push("Control");
    if (ev.altKey) modificadores.push("Alt");
    if (ESPECIAIS.has(ev.key)) {
      ev.preventDefault();
      despejarCampo();
      enviar({ tipo: "tecla", tecla: ev.key, modificadores });
    } else if (ctrl && !ev.altKey && ["a", "z", "y"].includes(ev.key.toLowerCase())) {
      ev.preventDefault();
      enviar({ tipo: "tecla", tecla: ev.key.toLowerCase(), modificadores });
    }
    // Letras, acentos e colar chegam pelo evento "input".
  });
  teclado.addEventListener("beforeinput", (ev) => { // teclado de celular: apagar com o campo vazio
    if (ev.inputType === "deleteContentBackward" && !teclado.value) {
      ev.preventDefault();
      enviar({ tipo: "tecla", tecla: "Backspace" });
    }
  });
  teclado.addEventListener("input", (ev) => { if (!ev.isComposing) despejarCampo(); });
  teclado.addEventListener("compositionend", despejarCampo);
  teclado.addEventListener("focus", () => {
    tela.classList.add("focada");
    dica.textContent = "Digitando no site";
    dica.classList.add("digitando");
  });
  teclado.addEventListener("blur", () => {
    tela.classList.remove("focada");
    dica.textContent = "Clique na tela para digitar";
    dica.classList.remove("digitando");
  });

  // ---------- botões e ciclo da página ----------
  botaoFechar.addEventListener("click", () => {
    if (ws && ws.readyState === WebSocket.OPEN) enviar({ tipo: "fechar" });
    else finalizar("fechado", "Janela fechada", "Abra de novo quando quiser fazer o login.");
  });
  botaoRecarregar.addEventListener("click", () => enviar({ tipo: "recarregar" }));
  botaoReabrir.addEventListener("click", reabrir);
  $("janela-teclado-botao").addEventListener("click", focarTeclado);
  addEventListener("resize", ajustar);
  // Voltando ao app (celular, depois de ler o código no e-mail): reconecta na hora, sem esperar o intervalo.
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible" && !ws && !terminou) { tentativas = Math.min(tentativas, 1); conectar(); }
  });
  addEventListener("pageshow", (ev) => { if (ev.persisted && !ws && !terminou) conectar(); });

  ajustar();
  conectar();
})();
