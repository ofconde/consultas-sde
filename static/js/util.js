// Escapa texto libre antes de insertarlo vía innerHTML — evita XSS almacenado.
// Usar en TODO campo que venga de la DB (formulario público o carga de técnico)
// y se interpole en un template string destinado a innerHTML.
function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

// Copia texto al portapapeles y confirma en el propio botón. navigator.clipboard
// necesita contexto seguro (https o localhost); si no está, se cae al textarea
// temporal, que funciona en cualquier lado.
async function copiar(texto, btn) {
  const valor = String(texto ?? "").trim();
  if (!valor || valor === "—") return;
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(valor);
    } else {
      const ta = document.createElement("textarea");
      ta.value = valor;
      ta.style.cssText = "position:fixed;opacity:0";
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      ta.remove();
    }
    if (btn) {
      const previo = btn.textContent;
      btn.textContent = "✓";
      btn.classList.add("ok");
      setTimeout(() => { btn.textContent = previo; btn.classList.remove("ok"); }, 1200);
    }
  } catch (e) {
    alert("No se pudo copiar. Copialo a mano:\n\n" + valor);
  }
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

// Mide la altura real del topbar (varía según el contenido y la escala de la
// página) y la deja en --topbar-h. Un elemento sticky fuera de cualquier caja
// con overflow propio (ej. la barra de filtros del panel) se ancla debajo de
// él con `top:var(--topbar-h)`, en vez de competir por el mismo top:0 y
// quedar tapado (el topbar también es sticky, con más z-index).
function _fijarAlturaTopbar() {
  const tb = document.querySelector(".topbar");
  if (tb) document.documentElement.style.setProperty("--topbar-h", tb.getBoundingClientRect().height + "px");
}
document.addEventListener("DOMContentLoaded", () => {
  _fijarAlturaTopbar();
  let t;
  window.addEventListener("resize", () => { clearTimeout(t); t = setTimeout(_fijarAlturaTopbar, 150); });
});

// Mapea la situación 1-6 de la Central de Deudores del BCRA al mismo grupo de
// color que ya usan los badges de estado (g-EN_SEDE verde, g-INICIAL azul,
// g-INACTIVAS gris) — normal = verde, seguimiento especial = azul, de 3 en
// adelante ya es un problema para otorgar crédito.
function bcraGrupo(situacionMax) {
  if (situacionMax <= 1) return "EN_SEDE";
  if (situacionMax === 2) return "INICIAL";
  return "INACTIVAS";
}

// Arma el badge compacto de situación BCRA para una celda de tabla — mismo
// dato que el bloque completo del detalle, versión de una sola línea.
function bcraBadgeMini(d) {
  if (!d || d.sin_datos) return '<span class="muted">sin registro</span>';
  return `<span class="badge g-${bcraGrupo(d.situacion_max)}">${d.situacion_max} · ${escapeHtml(d.situacion_label)}</span>`;
}
