// Escapa texto libre antes de insertarlo vía innerHTML — evita XSS almacenado.
// Usar en TODO campo que venga de la DB (formulario público o carga de técnico)
// y se interpole en un template string destinado a innerHTML.
function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

// Fecha de hoy como YYYY-MM-DD para un <input type="date">, tomada del reloj local.
// No usar toISOString(): convierte a UTC, así que después de las 21:00 en Argentina
// devuelve la fecha del día siguiente.
function hoyISO() {
  const d = new Date();
  const p = n => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

// Montos en los inputs editables. Espejo de _parse_monto()/_monto() de formatos.py:
// si hay coma se descarta la parte decimal (formato AR) antes de quedarse con los
// dígitos — si no, concatenarlos ignorando la coma da un monto muy superior al real.
function montoNum(v) {
  if (v === null || v === undefined || v === "") return null;
  let s = String(v);
  const coma = s.lastIndexOf(",");
  if (coma !== -1) s = s.slice(0, coma);
  const d = s.replace(/\D/g, "");
  return d ? parseInt(d, 10) : null;
}

function montoFmt(v) {
  const n = montoNum(v);
  return n === null ? "" : n.toLocaleString("es-AR");
}

// Deja el campo formateado apenas el usuario sale de él, para que se lea igual que
// en las tablas. Mientras escribe no se interfiere.
function montoAutoFormato(sel) {
  const el = typeof sel === "string" ? document.querySelector(sel) : sel;
  if (el) el.addEventListener("blur", () => { el.value = montoFmt(el.value); });
}
