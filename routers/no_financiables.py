"""No financiables — auditoría de solo lectura de las consultas marcadas «NO ES
FINANCIABLE», para detectar las que se descartaron sin un motivo que lo sostenga.
Nada de acá modifica datos.

Cada caso se clasifica con reglas fijas (`clasificar`) contra los criterios de
elegibilidad de las líneas del CFI. La clasificación es ORIENTATIVA: se hace sobre
el texto libre de actividad/destino y sobre el género estimado (ver genero.py),
así que sirve para ordenar qué mirar primero, no para cambiar un estado sin abrir
el caso.

Acceso restringido al mismo usuario que «Casos para revisar».
"""
import re

from fastapi import APIRouter, Depends
from sqlalchemy import text

from db import engine
from formatos import _monto
import genero as genero_mod
from routers.revisar import require_revisor, _categoria

router = APIRouter(prefix="/api/no-financiables", tags=["no-financiables"])

MONTO_MINIMO = 4_000_000     # por debajo no hay línea que lo financie
MONTO_MAXIMO = 200_000_000   # por encima, hay que reencuadrar el monto

# Grupos en el orden en que se muestran: primero lo que más probablemente es un
# error de clasificación, al final lo que está bien rechazado.
GRUPOS = [
    ("casfog", "alta", "CASFOG los aprobó y figuran como no financiables",
     "El fondo de garantía ya aprobó el caso: no debería estar descartado."),
    ("mujer_comercio", "alta", "Mujeres con comercio o servicios",
     "Un comercio o servicio de una mujer se financia por la línea Mujeres (el comercio puro solo se descarta cuando el titular es varón)."),
    ("productivo", "alta", "Actividad productiva sin un motivo que lo sostenga",
     "La actividad parece productiva y no hay BCRA 5, ARCA ni monto fuera de rango que justifique el rechazo."),
    ("info_pendiente", "media", "Marcados no financiables pero quedaron esperando información",
     "La última gestión fue enviar información o pedir datos, no comunicar el rechazo."),
    ("monto_alto", "media", "Monto por encima del tope",
     f"Piden más de {_monto(MONTO_MAXIMO)}: puede reencuadrarse en un monto menor."),
    ("sociedad", "media", "Sociedades comerciales",
     "Si la mayoría de los socios o la dirección son mujeres, entran por la línea Mujeres; si no, no hay línea de Competitividad."),
    ("dudosa", "media", "Actividad a confirmar",
     "No se puede saber por el texto si la actividad es productiva: hay que abrir el caso."),
    ("sin_datos", "media", "Sin datos para juzgar",
     "No tienen actividad ni destino cargados, así que el rechazo no se puede sostener ni discutir."),
    ("varon_comercio", "baja", "Comercio puro con titular varón",
     "Competitividad lo excluye por no ser productivo y la línea Mujeres no aplica. Alternativa: Triple Impacto, que acepta comercio."),
    ("regularizable", "baja", "Recuperables: faltan trámites en ARCA o con el CUIT",
     "Hoy no se pueden financiar, pero con el alta de impuestos, domicilio fiscal o CUIT regularizados volverían a ser consultas viables."),
    ("firme", "baja", "Rechazo firme",
     "BCRA en situación 5 o monto por debajo del mínimo."),
]
_GRUPO_INFO = {g[0]: g for g in GRUPOS}

# Raíces de palabras (sin tildes, en minúscula) que marcan una actividad productiva.
_PRODUCTIVO = (
    "fabric", "elabor", "producci", "productor", "produce", "panific", "panader", "pastel", "confecc", "torner", "metal",
    "carpint", "herrer", "cerrajer", "soldad", "aserr", "cria ", "criadero", "granja", "avicola",
    "porcin", "caprin", "apicul", "miel", "agricol", "siembra", "cultivo", "ganad", "tambo",
    "frigorif", "huevo", "queso", "dulce", "conserva", "imprent", "impresion", "grafica", "sublimaci",
    "textil", "costura", "ceramica", "artesan", "estampa", "bordad", "taller", "transformacion de",
)
_COMERCIO = (
    "venta", "comercio", "comercial", "negocio", "kiosco", "almacen", "tienda", "revend",
    "repuesto", "farmacia", "mercaderia", "distribu", "mayorista", "minorista", "accesorio",
    "ropa", "indumentaria", "cosmetic", "perfumer", "libreria", "papeleria", "emprendimiento",
)
_MARCAS_PENDIENTE = re.compile(r"^\s*(SE ENVIA INFORMACION|SE SOLICITA MAS INFORMACION|NO SE PUEDE CONTACTAR)", re.I)
_ARCA_PENDIENTE = re.compile(
    r"alta\s*(de\s*)?imp|no\s+registra\s+imp|domicilio\s+fiscal|\bcuit\b|no\s+(est[aá]\s+)?inscri", re.I)


def _sin_tildes(s):
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", (s or "").lower()) if unicodedata.category(c) != "Mn")


def _situacion_bcra(texto):
    """Número de situación (1–5) de la Central de Deudores, o None."""
    m = re.match(r"\s*(\d)", texto or "")
    return int(m.group(1)) if m else None


def clasificar(c):
    """Devuelve (grupo, motivo) para un caso. `c` es un dict con:
    genero ('F'/'M'/None), monto, cat_clave y categoria (ver revisar._categoria),
    bcra (texto), actividad, destino, observaciones, ultima_accion (texto) y
    casfog_aprobado (bool). Función pura: mismas entradas, misma salida."""
    texto = _sin_tildes(f"{c['actividad']} {c['destino']}")
    productiva = any(k in texto for k in _PRODUCTIVO)
    comercial = (not productiva) and any(k in texto for k in _COMERCIO)
    monto = c["monto"] or 0
    sit = _situacion_bcra(c["bcra"])
    genero = c["genero"]
    pendiente = bool(_MARCAS_PENDIENTE.match(c["ultima_accion"] or "")) and "no financiable" not in (c["ultima_accion"] or "").lower()

    if c["casfog_aprobado"]:
        return "casfog", "Aprobado por CASFOG"
    if sit == 5:
        return "firme", "BCRA situación 5"
    if 0 < monto < MONTO_MINIMO:
        return "firme", f"Monto {_monto(monto)}, menor al mínimo ({_monto(MONTO_MINIMO)})"
    if (c["cat_clave"] in ("OTRO", "SIN") and "no inscripto" in _sin_tildes(c["categoria"]).replace("-", " ")) \
            or _ARCA_PENDIENTE.search(c["observaciones"] or ""):
        return "regularizable", "ARCA / CUIT: " + ((c["observaciones"] or c["categoria"]).strip()[:90] or "sin alta")
    if productiva:
        if monto > MONTO_MAXIMO:
            return "monto_alto", f"Productivo, pide {_monto(monto)}"
        return "productivo", "Actividad productiva" + (f" · BCRA {sit}" if sit and sit > 1 else "")
    if comercial:
        if genero == "M":
            return "varon_comercio", "Comercio puro, titular varón"
        if genero == "F":
            return "mujer_comercio", "Comercio de una mujer: línea Mujeres"
        return "sociedad", "Comercio de una sociedad"
    if not (c["actividad"] or "").strip() and not (c["destino"] or "").strip():
        return ("info_pendiente", "Última gestión: " + c["ultima_accion"][:70]) if pendiente else ("sin_datos", "Sin actividad ni destino")
    if pendiente:
        return "info_pendiente", "Última gestión: " + c["ultima_accion"][:70]
    if monto > MONTO_MAXIMO:
        return "monto_alto", f"Pide {_monto(monto)}"
    if genero == "F":
        return "mujer_comercio", "Servicios de una mujer: línea Mujeres"
    return "dudosa", "Actividad no clasificable por texto"


_SQL = """
    SELECT c.id, c.codigo, c.nombre, c.cuit, COALESCE(NULLIF(c.tecnico,''),'Sin asignar') AS tecnico,
           c.genero, c.situacion_arca, c.arca_confirmado, c.situacion_bcra,
           COALESCE(NULLIF(c.actividad_economica,''), c.actividad_inscripta, '') AS actividad,
           COALESCE(c.destino,'') AS destino, COALESCE(c.observaciones,'') AS observaciones,
           COALESCE(NULLIF(c.monto_confirmado,0), c.monto) AS monto,
           COALESCE(u.accion || CASE WHEN COALESCE(u.detalle,'') <> '' THEN ': ' || u.detalle ELSE '' END, '') AS ultima_accion,
           (EXISTS (SELECT 1 FROM sde_casfog_ok k WHERE k.cuit = c.cuit)
            OR EXISTS (SELECT 1 FROM sde_casfog_oficial o WHERE o.consulta_id = c.id AND o.resultado = 'A')) AS casfog_aprobado
    FROM sde_consultas c
    LEFT JOIN LATERAL (SELECT accion, detalle FROM sde_acciones a WHERE a.consulta_id = c.id
                       ORDER BY a.created_at DESC, a.id DESC LIMIT 1) u ON TRUE
    WHERE c.estado = 'NO ES FINANCIABLE'
    ORDER BY c.nombre
"""


@router.get("")
def listar(usuario=Depends(require_revisor)):
    with engine.connect() as conn:
        filas = conn.execute(text(_SQL)).mappings().all()

    casos = []
    for r in filas:
        genero = (r["genero"] or "").strip().upper()[:1] or genero_mod.estimar_genero(r["nombre"], r["cuit"])
        categoria, cat_clave, _fuente, _decl = _categoria(r)
        grupo, motivo = clasificar({
            "genero": genero if genero in ("F", "M") else None, "monto": int(r["monto"] or 0),
            "cat_clave": cat_clave, "categoria": categoria, "bcra": r["situacion_bcra"],
            "actividad": r["actividad"], "destino": r["destino"], "observaciones": r["observaciones"],
            "ultima_accion": r["ultima_accion"], "casfog_aprobado": r["casfog_aprobado"],
        })
        casos.append({
            "id": r["id"], "codigo": r["codigo"], "nombre": r["nombre"] or "—", "tecnico": r["tecnico"],
            "genero": genero if genero in ("F", "M") else "—", "categoria": categoria,
            "bcra": (r["situacion_bcra"] or "—").strip() or "—",
            "actividad": r["actividad"] or "—", "destino": r["destino"] or "—",
            "monto": int(r["monto"] or 0), "monto_fmt": _monto(r["monto"]),
            "ultima_accion": r["ultima_accion"], "grupo": grupo, "motivo": motivo,
        })

    return {
        "total": len(casos),
        "tecnicos": sorted({c["tecnico"] for c in casos}),
        "grupos": [{"id": g[0], "gravedad": g[1], "titulo": g[2], "descripcion": g[3]} for g in GRUPOS],
        "casos": casos,
    }
