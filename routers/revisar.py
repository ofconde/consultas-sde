"""Casos para revisar — chequeos de solo lectura para detectar casos que se pueden
haber quedado sin atender (de cualquier técnico). Nada de acá modifica datos.

Acceso restringido a UN usuario (no a un rol): es una herramienta personal del
coordinador, así que si algún día se crea otro admin no la ve automáticamente.

"Sin movimiento" se calcula con la última modificación de la consulta o la
última acción registrada, lo que sea más reciente. Es una aproximación: una
edición en lote puede hacer que un caso parezca activo cuando no lo está.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text

from db import engine
from auth import require_coordinador
from formatos import _monto

USUARIO_REVISION = "omar"
DIAS_AVANZADOS = 30   # casos avanzados (gestión/sede) sin movimiento
DIAS_INICIAL = 45     # "Consulta inicial" con acciones pero sin novedades

router = APIRouter(prefix="/api/revisar", tags=["revisar"])

# Estado general esperado según el estado de carpeta de Instancia Superior. Si no
# coincide, el estado general quedó atrasado y las estadísticas lo cuentan mal.
_GENERAL_ESPERADO = {
    "DESEMBOLSADO": "DESEMBOLSADO",
    "EN TRAMITE DE DESEMBOLSO": "EN TRAMITE DE DESEMBOLSO",
    "EN TRAMITE EN CFI SEDE": "INGRESADO EN CFI SEDE",
    "FIRMADO - EN TRAMITE PARA INGRESAR A CFI (DOCUMENTACION O SGR)": "EN TRAMITE DE DOCUMENTACION PARA ENTRAR A CFI SEDE",
    "ENVIADO A FIRMA DE REPRESENTANTE": "REMITIDO PARA FIRMA DE REPRESENTANTE",
    "DESISTIDO POR SOLICITANTE": "DESISTE DE TOMAR EL CRÉDITO",
    "RECHAZADO POR ANALISIS DE RIESGO": "NO ES FINANCIABLE",
}
_ESTADOS_AVANZADOS = (
    "COMPLETANDO DOCUMENTACION", "EN TRAMITE DE DOCUMENTACION PARA ENTRAR A CFI SEDE",
    "EN GESTION CON SGR O FONDO", "REMITIDO PARA FIRMA DE REPRESENTANTE", "INGRESADO EN CFI SEDE",
    "EN TRAMITE DE DESEMBOLSO", "DERIVADO A FONDO DE GARANTIA CFI", "HAY INTERÉS PERO NO SE DECIDE",
)
_ESTADOS_ABIERTOS = ("CONSULTA INICIAL",) + _ESTADOS_AVANZADOS + (
    "DERIVADO A MERCADO DE CAPITALES", "DERIVADO A OTRA PROVINCIA")

_BASE = """
    SELECT c.id, c.codigo, c.nombre, COALESCE(NULLIF(c.tecnico,''),'Sin asignar') AS tecnico,
           c.estado, c.estado_carpeta, COALESCE(c.instancia_superior, FALSE) AS en_is,
           c.detalle_estado, c.situacion_arca, c.arca_confirmado,
           COALESCE(NULLIF(c.monto_instancia_superior,0), NULLIF(c.monto_confirmado,0), c.monto) AS monto,
           COALESCE(a.n, 0) AS n_acc,
           EXTRACT(day FROM NOW() - GREATEST(c.updated_at, COALESCE(a.ult, c.updated_at)))::int AS dias
    FROM sde_consultas c
    LEFT JOIN (SELECT consulta_id, max(created_at) AS ult, count(*) AS n
               FROM sde_acciones GROUP BY consulta_id) a ON a.consulta_id = c.id
"""


def require_revisor(usuario=Depends(require_coordinador)):
    if usuario["username"] != USUARIO_REVISION:
        raise HTTPException(403, "Sin acceso")
    return usuario


def _categoria(r):
    """Categoría fiscal efectiva: la que confirmó el técnico si la hay (salvo
    «no se puede verificar», que no informa nada), si no la que declaró el
    solicitante en el formulario. Devuelve (rótulo, clave de filtro, fuente,
    declarada si difiere de la confirmada)."""
    declarada = (r["situacion_arca"] or "").strip().upper()
    confirmada = (r["arca_confirmado"] or "").strip().upper()
    if confirmada and confirmada != "NO SE PUEDE VERIFICAR":
        efectiva, fuente = confirmada, "confirmada"
    else:
        efectiva, fuente = declarada, "declarada"
    if efectiva.startswith("MONOTRIBUTO "):
        clave = "MONO_" + efectiva.split()[-1]
    elif efectiva == "RESPONSABLE INSCRIPTO":
        clave = "RI"
    elif efectiva == "EXENTO":
        clave = "EXENTO"
    elif efectiva:
        clave = "OTRO"
    else:
        clave = "SIN"
    difiere = fuente == "confirmada" and bool(declarada) and declarada != efectiva
    return (efectiva.title() if efectiva else "Sin dato"), clave, fuente, (declarada.title() if difiere else "")


def _caso(r, nota=""):
    categoria, cat_clave, cat_fuente, cat_declarada = _categoria(r)
    return {
        "categoria": categoria, "cat_clave": cat_clave,
        "cat_fuente": cat_fuente, "cat_declarada": cat_declarada,
        "id": r["id"], "codigo": r["codigo"], "nombre": r["nombre"] or "—", "tecnico": r["tecnico"],
        "estado": r["estado"], "estado_carpeta": r["estado_carpeta"] or "",
        "monto": int(r["monto"] or 0), "monto_fmt": _monto(r["monto"]),
        "dias": r["dias"], "nota": nota,
    }


@router.get("")
def revisar(usuario=Depends(require_revisor)):
    chequeos = []
    with engine.connect() as conn:
        def q(sql, **p):
            return conn.execute(text(sql), p).mappings().all()

        # 1) Aprobados por CASFOG que nadie siguió: siguen en consulta inicial,
        #    fuera de Instancia Superior y sin marca de trámite.
        filas = q(f"""
            SELECT b.*, k.monto_aprobado FROM sde_casfog_ok k
            JOIN LATERAL (SELECT * FROM ({_BASE}) x WHERE x.id = (SELECT id FROM sde_consultas sc WHERE sc.cuit = k.cuit
                          ORDER BY fecha_recepcion DESC, id DESC LIMIT 1)) b ON TRUE
            WHERE b.estado = 'CONSULTA INICIAL' AND NOT b.en_is AND k.estado_tramite IS NULL
            ORDER BY k.monto_aprobado DESC NULLS LAST""")
        chequeos.append({
            "id": "casfog_sin_atender", "gravedad": "alta",
            "titulo": "Aprobados por CASFOG sin atender",
            "descripcion": "CASFOG los aprobó pero siguen en «Consulta inicial», fuera de Instancia Superior y sin marca de trámite en OK CASFOG.",
            "casos": [_caso(r, f"CASFOG aprobó {_monto(r['monto_aprobado'])}" if r["monto_aprobado"] is not None else "Aprobado por CASFOG") for r in filas]})

        # 2) Aprobados por CASFOG que figuran como no financiable / desistido.
        filas = q(f"""
            SELECT b.*, k.monto_aprobado FROM sde_casfog_ok k
            JOIN LATERAL (SELECT * FROM ({_BASE}) x WHERE x.id = (SELECT id FROM sde_consultas sc WHERE sc.cuit = k.cuit
                          ORDER BY fecha_recepcion DESC, id DESC LIMIT 1)) b ON TRUE
            WHERE b.estado IN ('NO ES FINANCIABLE', 'DESISTE DE TOMAR EL CRÉDITO') AND k.estado_tramite IS NULL
            ORDER BY k.monto_aprobado DESC NULLS LAST""")
        chequeos.append({
            "id": "casfog_dudoso", "gravedad": "media",
            "titulo": "Aprobados por CASFOG marcados como no financiable o desistido",
            "descripcion": "Figuran con ese estado en la consulta y sin marca en OK CASFOG: confirmar que sea correcto.",
            "casos": [_caso(r, f"CASFOG aprobó {_monto(r['monto_aprobado'])} · estado: {r['estado']}") for r in filas]})

        # 3) Estado general atrasado respecto al estado de carpeta (Instancia Superior).
        filas = q(f"SELECT * FROM ({_BASE}) b WHERE b.en_is AND b.estado_carpeta IS NOT NULL ORDER BY b.dias DESC")
        desfasados = []
        for r in filas:
            esperado = _GENERAL_ESPERADO.get(r["estado_carpeta"])
            if esperado and r["estado"] != esperado:
                desfasados.append(_caso(r, f"Carpeta: {r['estado_carpeta']} · General: {r['estado']}"))
        chequeos.append({
            "id": "estado_desfasado", "gravedad": "media",
            "titulo": "Estado general atrasado respecto a la carpeta",
            "descripcion": "En Instancia Superior el estado de carpeta avanzó pero el estado general quedó atrás; las estadísticas e informes leen el general.",
            "casos": desfasados})

        # 4) Avanzados fuera de Instancia Superior, sin movimiento.
        filas = q(f"""SELECT * FROM ({_BASE}) b
                      WHERE NOT b.en_is AND b.estado = ANY(:est) AND b.dias >= :d ORDER BY b.dias DESC""",
                  est=list(_ESTADOS_AVANZADOS), d=DIAS_AVANZADOS)
        chequeos.append({
            "id": "avanzados_quietos", "gravedad": "alta",
            "titulo": f"Casos avanzados sin movimiento hace {DIAS_AVANZADOS} días o más",
            "descripcion": "En gestión o en sede, todavía fuera de Instancia Superior.",
            "casos": [_caso(r, f"{r['n_acc']} acciones registradas") for r in filas]})

        # 5) Abiertos sin técnico asignado.
        filas = q(f"""SELECT * FROM ({_BASE}) b
                      WHERE b.tecnico = 'Sin asignar' AND b.estado = ANY(:est) ORDER BY b.dias DESC""",
                  est=list(_ESTADOS_ABIERTOS))
        chequeos.append({
            "id": "sin_tecnico", "gravedad": "media",
            "titulo": "Casos abiertos sin técnico asignado",
            "descripcion": "Nadie es responsable de estos casos.",
            "casos": [_caso(r) for r in filas]})

        # 6) Consulta inicial sin ninguna acción.
        filas = q(f"""SELECT * FROM ({_BASE}) b
                      WHERE b.estado = 'CONSULTA INICIAL' AND b.n_acc = 0 ORDER BY b.dias DESC""")
        chequeos.append({
            "id": "inicial_sin_accion", "gravedad": "media",
            "titulo": "Consultas iniciales sin ninguna acción registrada",
            "descripcion": "Nunca se registró un contacto o gestión.",
            "casos": [_caso(r) for r in filas]})

        # 7) Consulta inicial con acciones pero sin novedades hace mucho.
        filas = q(f"""SELECT * FROM ({_BASE}) b
                      WHERE b.estado = 'CONSULTA INICIAL' AND b.n_acc > 0 AND b.dias >= :d ORDER BY b.dias DESC""",
                  d=DIAS_INICIAL)
        chequeos.append({
            "id": "inicial_quietos", "gravedad": "baja",
            "titulo": f"Consultas iniciales sin novedades hace {DIAS_INICIAL} días o más",
            "descripcion": "Tienen acciones registradas, pero la última es vieja: ¿esperan respuesta del solicitante o quedaron colgadas?",
            "casos": [_caso(r, f"{r['n_acc']} acciones registradas") for r in filas]})

        # 8) Instancia Superior incompleta.
        filas = q(f"""SELECT * FROM ({_BASE}) b WHERE b.en_is AND (b.estado_carpeta IS NULL OR
                      (b.estado_carpeta <> 'DESEMBOLSADO' AND COALESCE(trim(b.detalle_estado), '') = ''))
                      ORDER BY b.dias DESC""")
        chequeos.append({
            "id": "is_incompleta", "gravedad": "baja",
            "titulo": "Instancia Superior sin clasificar o sin detalle",
            "descripcion": "Sin estado de carpeta, o sin detalle de estado (salvo los ya desembolsados).",
            "casos": [_caso(r, "Sin estado de carpeta" if r["estado_carpeta"] is None else "Sin detalle de estado") for r in filas]})

    distintos = {c["id"] for ch in chequeos for c in ch["casos"]}
    return {
        "dias_avanzados": DIAS_AVANZADOS, "dias_inicial": DIAS_INICIAL,
        "casos_distintos": len(distintos),
        "tecnicos": sorted({c["tecnico"] for ch in chequeos for c in ch["casos"]}),
        "chequeos": chequeos,
    }
