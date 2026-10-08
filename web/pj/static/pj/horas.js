/* Horas e informes PJ: o cálculo do informe ao vivo enquanto a pessoa digita (Alpine.js).
   Mesma regra de pj.models.Informe: cada parcela arredondada a centavos; NF = bruto − plano de saúde. */
(function () {
  "use strict";

  // "1.234,56", "1234.56" ou vazio -> número (vazio = 0; inválido = NaN)
  function numero(valor) {
    const texto = String(valor ?? "").trim().replace(/\s|R\$/g, "");
    if (!texto) return 0;
    const n = Number(texto.includes(",") ? texto.replace(/\./g, "").replace(",", ".") : texto);
    return Number.isFinite(n) && n >= 0 ? n : NaN;
  }

  // "181:39" -> 181,65 h; aceita também "181" e "181,5"
  function horas(texto) {
    const t = String(texto ?? "").trim();
    let m = /^(\d{1,4}):([0-5]\d)$/.exec(t);
    if (m) return Number(m[1]) + Number(m[2]) / 60;
    m = /^\d{1,4}(?:[.,]\d+)?$/.exec(t);
    return m ? Number(t.replace(",", ".")) : NaN;
  }

  const centavos = (v) => Math.round((v + Number.EPSILON) * 100) / 100;
  const brl = (v) => (Number.isFinite(v) ? GFP.brl(v) : "—");

  window.informePJ = function (inicial) {
    return {
      horas: inicial.horas, sobreaviso: inicial.sobreaviso, valor_hora: inicial.valor_hora, plano: inicial.plano,
      nf: inicial.nf, pct: Number(inicial.pct) || 1 / 3,
      horas_previsao: inicial.horas_previsao, valor_hora_previsao: inicial.valor_hora_previsao,
      plano_previsao: inicial.plano_previsao,
      brl,
      get horasValidas() { return Number.isFinite(horas(this.horas)); },
      get valorHora() { return numero(this.valor_hora); },
      get valorNormal() { return centavos(horas(this.horas) * numero(this.valor_hora)); },
      get valorSobreaviso() { return centavos(numero(this.sobreaviso) * numero(this.valor_hora) * this.pct); },
      get horaSobreaviso() { return centavos(numero(this.valor_hora) * this.pct); },
      get bruto() { return centavos(this.valorNormal + this.valorSobreaviso); },
      get nfCalculada() { return centavos(this.bruto - numero(this.plano)); },
      get nfInformada() { const v = numero(this.nf); return v > 0 ? v : null; },
      get diferencaNf() { return this.nfInformada === null ? null : centavos(this.nfInformada - this.nfCalculada); },
      get previsaoMensal() {
        return centavos(centavos(horas(this.horas_previsao) * numero(this.valor_hora_previsao)) - numero(this.plano_previsao));
      },
    };
  };
})();
