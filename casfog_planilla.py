"""Sincronización con la planilla oficial de CASFOG (Google Sheets).

La planilla es del fondo (Región NEA, Serie 2); nosotros solo la leemos. Está
compartida como «cualquiera con el enlace puede ver», así que se baja como CSV
sin credenciales. Si el fondo cambia el permiso o la estructura, la
sincronización falla y se registra el error, pero NO se pisa lo que ya estaba
cargado (ver el mínimo de filas en `sincronizar`).

Solo se guardan las columnas que usa el sistema (nombre, CUIT, resultado,
monto aprobado, estado, línea, plazo, observaciones, fecha de monetización);
los teléfonos y mails de las pymes de la planilla no se copian.
"""
import csv
import io
import logging
import re
import unicodedata
import urllib.request

from sqlalchemy import text

from db import engine

log = logging.getLogger("consultas_sde.casfog")

SPREADSHEET_ID = "1ZcZY_y6ASpTnUPnz-m9DnUsT4O32igrxdnxJ0bikyCs"
GID_SANTIAGO = "1633450703"          # pestaña «SANTIAGO DEL ESTERO»
URL_CSV = (f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/export"
           f"?format=csv&gid={GID_SANTIAGO}")
URL_VER = f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/edit?gid={GID_SANTIAGO}"
MINIMO_FILAS = 20                    # menos que esto = la planilla cambió o vino vacía

_LETRAS_TECNICO = {"O", "V", "S"}    # inicial del técnico en la primera columna


def _norm(s):
    s = unicodedata.normalize("NFD", (s or "").upper())
    return "".join(c for c in s if unicodedata.category(c) != "Mn").strip()


def _digitos(s):
    return re.sub(r"\D", "", s or "")


def _monto(s):
    d = _digitos(s)
    return int(d) if d else None


def clasificar(resultado):
    """A aprobado · D desaprobado · R en revisión · S sin resultado · P cualquier
    otro texto (el fondo pide algo: ventas, F1272, comprobantes, confirmar monto…)."""
    t = _norm(resultado)
    if not t:
        return "S"
    if t.startswith("APROB"):
        return "A"
    if t.startswith("DES"):
        return "D"
    if "REVISION" in t:
        return "R"
    return "P"


def parsear(texto_csv):
    """CSV de la pestaña → lista de dicts, uno por solicitud con CUIT."""
    filas = list(csv.reader(io.StringIO(texto_csv)))
    cab = next((i for i, f in enumerate(filas) if any(_norm(c) == "CUIT" for c in f)), None)
    if cab is None:
        raise ValueError("No se encontró la fila de encabezados (columna CUIT)")
    cols = {}
    for j, c in enumerate(filas[cab]):
        n = _norm(c)
        if n.startswith("NOMBRE"):
            cols["nombre"] = j
        elif n == "CUIT":
            cols["cuit"] = j
        elif n.startswith("(1)"):
            cols["resultado"] = j
        elif n.startswith("(2)"):
            cols["monto"] = j
        elif n == "PLAZO":
            cols["plazo"] = j
        elif n == "LINEA":
            cols["linea"] = j
        elif n.startswith("(3)"):
            cols["obs"] = j
        elif n.startswith("FECHA DE MONETIZ"):
            cols["monetizacion"] = j
    faltan = {"nombre", "cuit", "resultado", "monto"} - set(cols)
    if faltan:
        raise ValueError(f"Faltan columnas en la planilla: {sorted(faltan)}")

    def celda(f, k):
        j = cols.get(k)
        return f[j].strip() if j is not None and j < len(f) else ""

    out = []
    for n, f in enumerate(filas[cab + 1:], start=1):
        cuit, nombre = _digitos(celda(f, "cuit")), celda(f, "nombre")
        if not cuit or not nombre:
            continue
        a = (f[0].strip() if f else "")
        resultado = celda(f, "resultado")
        out.append({
            "fila": n, "cuit": cuit, "nombre": nombre,
            "resultado": clasificar(resultado), "resultado_texto": resultado,
            "monto_aprobado": _monto(celda(f, "monto")),
            "estado_planilla": "" if a.upper() in _LETRAS_TECNICO else a,
            "tecnico_letra": a.upper() if a.upper() in _LETRAS_TECNICO else "",
            "linea": celda(f, "linea"), "plazo": celda(f, "plazo"),
            "observacion": celda(f, "obs"), "fecha_monetizacion": celda(f, "monetizacion"),
        })
    return out


def descargar():
    req = urllib.request.Request(URL_CSV, headers={"User-Agent": "consultas-sde"})
    with urllib.request.urlopen(req, timeout=40) as r:
        return r.read().decode("utf-8")


# Resuelve la consulta de cada CUIT: coincidencia exacta en dígitos (el CUIT de la
# base a veces tiene guiones) o, si el de la base quedó truncado a 10 dígitos, por prefijo.
_ENLACE = """
    COALESCE(
      (SELECT id FROM sde_consultas WHERE regexp_replace(cuit,'\\D','','g') = :cuit
        ORDER BY fecha_recepcion DESC, id DESC LIMIT 1),
      (SELECT id FROM sde_consultas WHERE length(regexp_replace(cuit,'\\D','','g')) = 10
          AND :cuit LIKE regexp_replace(cuit,'\\D','','g') || '%'
        ORDER BY fecha_recepcion DESC, id DESC LIMIT 1))
"""


def sincronizar(origen="manual"):
    """Baja la planilla y reemplaza el espejo sde_casfog_oficial. Devuelve un
    resumen; ante cualquier problema registra el error y deja intacto lo anterior."""
    try:
        filas = parsear(descargar())
        if len(filas) < MINIMO_FILAS:
            raise ValueError(f"La planilla trajo solo {len(filas)} filas (mínimo {MINIMO_FILAS}); no se actualiza")
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM sde_casfog_oficial"))
            for f in filas:
                conn.execute(text(f"""
                    INSERT INTO sde_casfog_oficial
                      (fila, cuit, nombre, resultado, resultado_texto, monto_aprobado, estado_planilla,
                       tecnico_letra, linea, plazo, observacion, fecha_monetizacion, consulta_id)
                    VALUES (:fila, :cuit, :nombre, :resultado, :resultado_texto, :monto_aprobado,
                            NULLIF(:estado_planilla,''), NULLIF(:tecnico_letra,''), NULLIF(:linea,''), NULLIF(:plazo,''),
                            NULLIF(:observacion,''), NULLIF(:fecha_monetizacion,''), {_ENLACE})
                """), f)
            conn.execute(text("INSERT INTO sde_casfog_sync (ok, filas, origen) VALUES (TRUE, :n, :o)"),
                         {"n": len(filas), "o": origen})
        log.info("Sincronización con la planilla de CASFOG OK: %s filas (%s)", len(filas), origen)
        return {"ok": True, "filas": len(filas)}
    except Exception as e:                                  # noqa: BLE001 — se registra y se informa
        log.exception("Falló la sincronización con la planilla de CASFOG")
        try:
            with engine.begin() as conn:
                conn.execute(text("INSERT INTO sde_casfog_sync (ok, error, origen) VALUES (FALSE, :e, :o)"),
                             {"e": str(e)[:500], "o": origen})
        except Exception:                                   # noqa: BLE001
            pass
        return {"ok": False, "error": str(e)}


def ultima_sincronizacion():
    with engine.connect() as conn:
        r = conn.execute(text("""
            SELECT ejecutado_en, ok, filas, error FROM sde_casfog_sync ORDER BY id DESC LIMIT 1
        """)).mappings().first()
        ok = conn.execute(text("""
            SELECT ejecutado_en, filas, EXTRACT(EPOCH FROM NOW() - ejecutado_en) AS edad_s
            FROM sde_casfog_sync WHERE ok ORDER BY id DESC LIMIT 1
        """)).mappings().first()
    return {"ultima": dict(r) if r else None, "ultima_ok": dict(ok) if ok else None}
