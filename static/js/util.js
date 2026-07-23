// Escapa texto libre antes de insertarlo vía innerHTML — evita XSS almacenado.
// Usar en TODO campo que venga de la DB (formulario público o carga de técnico)
// y se interpole en un template string destinado a innerHTML.
function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}
