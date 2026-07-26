"""Central de Deudores del BCRA — consulta de situación crediticia por CUIT.

API pública del Banco Central (`api.bcra.gob.ar`), sin credenciales. Nada que ver con
la API del PEI: es otro organismo y otro dominio.

Se usa la biblioteca estándar a propósito — el proyecto no tiene `requests` ni `httpx`
y no vale sumar una dependencia para un solo GET. A diferencia del sistema principal,
acá NO se desactiva la verificación SSL: se probó y el certificado del BCRA valida bien.

La situación va del 1 al 6; de 3 en adelante ya es un problema para otorgar crédito.
"""
import json
import logging
import urllib.error
import urllib.request

log = logging.getLogger("consultas_sde.bcra")

_BASE = "https://api.bcra.gob.ar/centraldedeudores/v1.0/Deudas"
_TIMEOUT = 12

SIT_LABEL = {
    1: "Normal",
    2: "Seguimiento especial",
    3: "Con problemas",
    4: "Alto riesgo",
    5: "Irrecuperable",
    6: "Irrecuperable (disp. técnica)",
}


def limpiar_cuit(cuit) -> str:
    """Deja solo los dígitos. Devuelve '' si no queda un CUIT/CUIL plausible."""
    d = "".join(ch for ch in str(cuit or "") if ch.isdigit())
    return d if len(d) in (10, 11) else ""


def _get(path):
    req = urllib.request.Request(
        _BASE + path,
        headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None          # el CUIT no figura en la central
        raise


def consultar(cuit_limpio: str) -> dict:
    """Devuelve la situación consolidada del CUIT. `sin_datos` si no figura."""
    data = _get(f"/{cuit_limpio}")
    if data is None:
        return {"sin_datos": True}

    res = data.get("results", {}) or {}
    periodos = res.get("periodos") or []
    actual = periodos[0] if periodos else {}

    situacion_max = 1
    alertas, entidades = [], []
    for ent in (actual.get("entidades") or []):
        s = ent.get("situacion") or 1
        situacion_max = max(situacion_max, s)
        flags = []
        if ent.get("procesoJud"):            flags.append("Proceso judicial")
        if ent.get("situacionJuridica"):     flags.append("Situación jurídica")
        if ent.get("refinanciaciones"):      flags.append("Refinanciación")
        if ent.get("recategorizacionOblig"): flags.append("Recategorización obligatoria")
        dias = ent.get("diasAtrasoPago") or 0
        if dias:
            flags.append(f"{dias} días de atraso")
        if flags:
            alertas.append(f"{ent.get('entidad', '')}: {', '.join(flags)}")
        entidades.append({
            "entidad": ent.get("entidad", ""),
            "situacion": s,
            "sit_label": SIT_LABEL.get(s, str(s)),
            "monto": ent.get("monto") or 0,
            "dias_atraso": dias,
        })

    # Cheques rechazados: es un dato aparte y no debe tumbar la consulta principal.
    cheques = []
    try:
        ch = _get(f"/ChequesRechazados/{cuit_limpio}")
        for p in ((ch or {}).get("results", {}) or {}).get("periodos") or []:
            for e in (p.get("entidades") or []):
                for c in (e.get("detalle") or []):
                    cheques.append({
                        "entidad": e.get("entidad", ""),
                        "fecha": c.get("fechaRechazo"),
                        "monto": c.get("monto"),
                        "motivo": c.get("motivoRechazo"),
                    })
    except Exception as e:
        log.warning("Cheques rechazados no disponibles para %s: %s", cuit_limpio, e)

    return {
        "sin_datos": False,
        "denominacion": res.get("denominacion", ""),
        "situacion_max": situacion_max,
        "situacion_label": SIT_LABEL.get(situacion_max, str(situacion_max)),
        "periodo": actual.get("periodo", ""),
        "alertas": alertas,
        "entidades": entidades,
        "cheques": cheques,
    }
