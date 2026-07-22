"""Helpers de formato — obligatorios en todo el sistema CFI.

- Fechas: DD-MM-YYYY (nunca otro orden).
- Montos ARS: "mil M" para >= 1e9, "M" para >= 1e6 (nunca "B/billones").
- Timestamps: se guardan en UTC; convertir a hora local antes de mostrar.
"""
from datetime import datetime, date
from zoneinfo import ZoneInfo

TZ_AR = ZoneInfo("America/Argentina/Cordoba")


def _dmy(valor) -> str:
    """Devuelve una fecha como DD-MM-YYYY. Acepta date/datetime/str/None."""
    if valor is None or valor == "":
        return ""
    if isinstance(valor, datetime):
        return valor.strftime("%d-%m-%Y")
    if isinstance(valor, date):
        return valor.strftime("%d-%m-%Y")
    s = str(valor).strip()
    # intentar parsear formatos ISO comunes
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(s[:len(fmt) + 2], fmt).strftime("%d-%m-%Y")
        except ValueError:
            continue
    return s


def _hora_local(ts) -> str:
    """Convierte un timestamp UTC a hora local AR → 'DD-MM-YYYY HH:MM'."""
    if ts is None:
        return ""
    if isinstance(ts, str):
        try:
            ts = datetime.fromisoformat(ts)
        except ValueError:
            return ts
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=ZoneInfo("UTC"))
    return ts.astimezone(TZ_AR).strftime("%d-%m-%Y %H:%M")


def _monto(valor) -> str:
    """Formatea un monto en pesos: 'mil M' >= 1e9, 'M' >= 1e6, si no con separador de miles."""
    if valor is None or valor == "":
        return "—"
    try:
        n = float(valor)
    except (TypeError, ValueError):
        return str(valor)
    if n >= 1_000_000_000:
        return f"$ {n / 1_000_000_000:,.2f}".replace(",", "@").replace(".", ",").replace("@", ".") + " mil M"
    if n >= 1_000_000:
        return f"$ {n / 1_000_000:,.1f}".replace(",", "@").replace(".", ",").replace("@", ".") + " M"
    return "$ " + f"{n:,.0f}".replace(",", ".")


def _parse_monto(valor):
    """Limpia un monto que llega como texto ('$ 16.000.000', '16000000') → int o None."""
    if valor is None or valor == "":
        return None
    s = "".join(ch for ch in str(valor) if ch.isdigit())
    return int(s) if s else None


def _parse_fecha(valor):
    """Parsea una fecha que llega en cualquier formato razonable → date o None."""
    if valor is None or valor == "":
        return None
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    s = str(valor).strip()
    for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d/%m/%y", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s[:len(fmt) + 2], fmt).date()
        except ValueError:
            continue
    return None
