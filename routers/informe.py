"""Informe de gestión (el de los viernes) — KPIs computados server-side."""
from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy import text

from db import engine, cuits_duplicados
from auth import require_coordinador
from formatos import _monto, _dmy
from constantes import grupo_de, GRUPOS, GRUPOS_ACTIVOS

# "Situación de consultas" — desglose fino por estado (equivalente a la hoja
# `indicadores` del Excel viejo, sin la dimensión de rama/línea de formulario
# que ese Excel tenía y este sistema no: acá todo entra por un solo formulario).
_SITUACION_MAP = {
    "inicial":         {"CONSULTA INICIAL"},
    "repetidas":       {"REPETIDO"},
    "desistidas":      {"DESISTE DE TOMAR EL CRÉDITO"},
    "no_financiables": {"NO ES FINANCIABLE"},
    "pensando":        {"HAY INTERÉS PERO NO SE DECIDE"},
    "en_sgr_fondo":    {"EN GESTION CON SGR O FONDO"},
    "en_preparacion":  {"COMPLETANDO DOCUMENTACION"},
    "creditos":        {"INGRESADO EN CFI SEDE", "DERIVADO A FONDO DE GARANTIA CFI",
                         "DERIVADO A MERCADO DE CAPITALES", "DERIVADO A OTRA PROVINCIA"},
}


def _breakdown(conn, columna, etiqueta_vacia):
    """Cantidad + monto solicitado agrupado por una columna, ordenado por cantidad."""
    rows = conn.execute(text(f"""
        SELECT COALESCE(NULLIF({columna}, ''), :vacia) AS clave,
               COUNT(*) AS n, COALESCE(SUM(monto), 0) AS monto
        FROM sde_consultas GROUP BY 1 ORDER BY n DESC
    """), {"vacia": etiqueta_vacia}).mappings().all()
    return [{"clave": r["clave"], "n": r["n"],
             "monto": int(r["monto"]), "monto_fmt": _monto(r["monto"])} for r in rows]

router = APIRouter(prefix="/api/informe", tags=["informe"])


@router.get("")
def informe(_=Depends(require_coordinador)):
    with engine.connect() as conn:
        total = conn.execute(text("SELECT COUNT(*) FROM sde_consultas")).scalar() or 0

        por_estado = conn.execute(text("""
            SELECT COALESCE(estado, 'SIN ESTADO') AS estado, COUNT(*) AS n
            FROM sde_consultas GROUP BY estado ORDER BY n DESC
        """)).mappings().all()

        por_tecnico = conn.execute(text("""
            SELECT COALESCE(NULLIF(tecnico, ''), '— Sin asignar') AS tecnico, COUNT(*) AS n
            FROM sde_consultas GROUP BY 1 ORDER BY n DESC
        """)).mappings().all()

        sin_asignar = conn.execute(text("""
            SELECT COUNT(*) FROM sde_consultas WHERE tecnico IS NULL OR tecnico = ''
        """)).scalar() or 0

        sin_acciones = conn.execute(text("""
            SELECT COUNT(*) FROM sde_consultas c
            WHERE NOT EXISTS (SELECT 1 FROM sde_acciones a WHERE a.consulta_id = c.id)
        """)).scalar() or 0

        total_acciones = conn.execute(text("SELECT COUNT(*) FROM sde_acciones")).scalar() or 0

        # consultas involucradas en un CUIT duplicado
        dup_cuits = cuits_duplicados(conn)
        duplicados = 0
        if dup_cuits:
            duplicados = conn.execute(text("""
                SELECT COUNT(*) FROM sde_consultas WHERE cuit = ANY(:dcuits)
            """), {"dcuits": list(dup_cuits)}).scalar() or 0

        # montos solicitados
        m = conn.execute(text("""
            SELECT COALESCE(SUM(monto),0) total, COALESCE(ROUND(AVG(monto)),0) prom,
                   COALESCE(MAX(monto),0) maximo, COUNT(monto) con_monto
            FROM sde_consultas
        """)).mappings().first()

        # breakdowns por sector / línea / programa (de la pestaña INFORME del Excel)
        por_sector = _breakdown(conn, "sector", "(sin sector)")
        por_linea = _breakdown(conn, "linea", "(sin línea)")
        por_programa = _breakdown(conn, "programa", "(sin programa)")
        # situación ARCA: gestión confirmada por el técnico si existe, si no lo que
        # declaró el solicitante — mismo criterio que se muestra en el panel.
        por_arca = _breakdown(conn, "COALESCE(NULLIF(arca_confirmado, ''), situacion_arca)",
                               "(sin dato)")

        primera_fecha = conn.execute(text(
            "SELECT MIN(fecha_recepcion)::date FROM sde_consultas WHERE fecha_recepcion IS NOT NULL"
        )).scalar()

        # movimiento de la semana (últimos 7 días)
        # nuevas = por fecha de recepción (cuándo llegó la consulta), no por fecha de carga
        nuevas_semana = conn.execute(text("""
            SELECT COUNT(*) FROM sde_consultas
            WHERE fecha_recepcion >= NOW() - INTERVAL '7 days'
        """)).scalar() or 0
        acciones_semana = conn.execute(text("""
            SELECT COUNT(*) FROM sde_acciones
            WHERE created_at >= NOW() - INTERVAL '7 days'
        """)).scalar() or 0

        # consultas por día, últimos 90 días. Se rellenan con generate_series los días
        # sin consultas: si se devolvieran solo los días con datos, el gráfico
        # comprimiría los huecos y un fin de semana muerto se vería como actividad.
        por_dia = conn.execute(text("""
            SELECT d::date AS dia, COUNT(c.id) AS n
            FROM generate_series(CURRENT_DATE - INTERVAL '89 days', CURRENT_DATE, '1 day') d
            LEFT JOIN sde_consultas c ON c.fecha_recepcion::date = d::date
            GROUP BY 1 ORDER BY 1
        """)).mappings().all()

        # backlog de "consulta inicial" (sin trabajar todavía) por técnico
        inicial_por_tecnico = conn.execute(text("""
            SELECT COALESCE(NULLIF(tecnico, ''), '— Sin asignar') AS tecnico, COUNT(*) AS n
            FROM sde_consultas WHERE estado = 'CONSULTA INICIAL'
            GROUP BY 1 ORDER BY n DESC
        """)).mappings().all()

        # consultas sin ninguna acción cargada, por técnico (a quién pertenecen)
        sin_acciones_por_tecnico = conn.execute(text("""
            SELECT COALESCE(NULLIF(c.tecnico, ''), '— Sin asignar') AS tecnico, COUNT(*) AS n
            FROM sde_consultas c
            WHERE NOT EXISTS (SELECT 1 FROM sde_acciones a WHERE a.consulta_id = c.id)
            GROUP BY 1 ORDER BY n DESC
        """)).mappings().all()

    # agrupar estados en grupos
    grupos_cnt = {g[0]: 0 for g in GRUPOS}
    estados_out = []
    for r in por_estado:
        g = grupo_de(r["estado"])
        grupos_cnt[g] = grupos_cnt.get(g, 0) + r["n"]
        estados_out.append({"estado": r["estado"], "n": r["n"], "grupo": g})

    # promedio diario en 3 ventanas: todo el período (desde la primera consulta
    # cargada) y dos recortes recientes, reusando el mismo por_dia del gráfico
    # para no repetir la consulta.
    dias_historico = max(1, (date.today() - primera_fecha).days + 1) if primera_fecha else 1
    n_ultimos_30 = sum(r["n"] for r in por_dia[-30:])
    n_ultimos_7 = sum(r["n"] for r in por_dia[-7:])
    promedio_diario = {
        "historico": round(total / dias_historico, 1),
        "ultimos_30": round(n_ultimos_30 / 30, 1),
        "ultimos_7": round(n_ultimos_7 / 7, 1),
        "dias_historico": dias_historico,
    }

    activas = sum(v for k, v in grupos_cnt.items() if k in GRUPOS_ACTIVOS)
    grupos_out = [{"clave": k, "label": lbl, "n": grupos_cnt.get(k, 0)} for k, lbl in GRUPOS]

    situacion = {clave: 0 for clave in _SITUACION_MAP}
    for r in por_estado:
        for clave, estados in _SITUACION_MAP.items():
            if r["estado"] in estados:
                situacion[clave] += r["n"]
    situacion["consultas"] = total
    situacion["activas_pct"] = round(activas / total * 100) if total else 0

    return {
        "total": total,
        "activas": activas,
        "sin_asignar": sin_asignar,
        "sin_acciones": sin_acciones,
        "duplicados": duplicados,
        "total_acciones": total_acciones,
        "acciones_por_consulta": round(total_acciones / total, 2) if total else 0,
        "nuevas_semana": nuevas_semana,
        "acciones_semana": acciones_semana,
        "grupos": grupos_out,
        "estados": estados_out,
        "tecnicos": [{"tecnico": r["tecnico"], "n": r["n"]} for r in por_tecnico],
        "montos": {
            "total": int(m["total"]), "total_fmt": _monto(m["total"]),
            "promedio_fmt": _monto(m["prom"]), "maximo_fmt": _monto(m["maximo"]),
            "con_monto": m["con_monto"],
        },
        "sectores": por_sector,
        "lineas": por_linea,
        "programas": por_programa,
        "situacion_arca": por_arca,
        "promedio_diario": promedio_diario,
        "situacion": situacion,
        "por_dia": [{"dia": r["dia"].isoformat(), "dia_fmt": _dmy(r["dia"]), "n": r["n"]}
                    for r in por_dia],
        "inicial_por_tecnico": [{"tecnico": r["tecnico"], "n": r["n"]} for r in inicial_por_tecnico],
        "sin_acciones_por_tecnico": [{"tecnico": r["tecnico"], "n": r["n"]} for r in sin_acciones_por_tecnico],
    }
