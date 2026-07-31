"""Informe de gestión — KPIs computados server-side.

Alimenta dos consumidores con distinto recorte del mismo JSON:
- `/informe` (pantalla de Estadísticas): lo llama sin parámetros y usa todo,
  incluidas las secciones por técnico.
- `/informe/pdf` (informe institucional imprimible): lo llama con `desde`/`hasta`
  e ignora las secciones por técnico — es un informe de la demanda, no del equipo.

Sin `desde`/`hasta` el endpoint se comporta exactamente como antes de que existiera
el informe PDF: todo el histórico, y la serie diaria acotada a los últimos 90 días.
"""
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text

from db import engine, cuits_duplicados
from auth import require_login
from formatos import _monto, _dmy, _parse_fecha
from constantes import grupo_de, GRUPOS, GRUPOS_ACTIVOS, TOPE_LINEA

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

# Monto efectivo: el confirmado por el técnico manda sobre el declarado por el
# solicitante. Mismo criterio que muestra el panel — así el aviso de "fuera de tope"
# del informe y la marca del listado señalan siempre las mismas consultas.
_MONTO_EFECTIVO = "COALESCE(NULLIF(monto_confirmado, 0), monto)"

# Cortes del histograma de montos, alineados a los tramos reales de las líneas CFI.
_TRAMOS_MONTO = [
    ("Hasta $10 M",      None,         10_000_000),
    ("$10 M a $50 M",    10_000_000,   50_000_000),
    ("$50 M a $100 M",   50_000_000,   100_000_000),
    ("$100 M a $500 M",  100_000_000,  500_000_000),
    ("Más de $500 M",    500_000_000,  None),
]

# A partir de acá una serie diaria son barras de 1px ilegibles: se agrupa por semana.
_DIAS_MAX_SERIE_DIARIA = 120

# Etiqueta para los campos que confirma la UEP durante la gestión (línea, programa,
# departamento). Una consulta reciente todavía sin encuadrar no es un dato faltante:
# es una etapa del circuito, y el informe tiene que leerse en esos términos.
_PENDIENTE_UEP = "En espera de asignación"

# Situación ARCA a informar: manda la confirmada por el técnico sobre la declarada
# por el solicitante (mismo criterio que el panel). EXENTO y NO INSCRIPTO van en un
# solo renglón: para el encuadre crediticio son la misma situación —el solicitante no
# tiene una inscripción activa que le permita facturar— y separados fragmentan la
# lectura en dos ítems chicos que dicen lo mismo.
_ARCA_EFECTIVA = "COALESCE(NULLIF(arca_confirmado, ''), situacion_arca)"
_ARCA_AGRUPADA = f"""
    CASE WHEN UPPER(TRIM({_ARCA_EFECTIVA})) IN ('EXENTO', 'NO INSCRIPTO')
         THEN 'EXENTO / NO INSCRIPTO'
         ELSE {_ARCA_EFECTIVA} END
"""

router = APIRouter(prefix="/api/informe", tags=["informe"])


def _condiciones_rango(desde, hasta, alias=""):
    """Condiciones SQL para acotar por fecha de recepción. Lista vacía si no hay rango."""
    p = f"{alias}." if alias else ""
    cond = []
    if desde:
        cond.append(f"{p}fecha_recepcion::date >= :desde")
    if hasta:
        cond.append(f"{p}fecha_recepcion::date <= :hasta")
    return cond


def _where(*condiciones):
    """Arma el WHERE a partir de condiciones sueltas, ignorando las vacías."""
    partes = [c for c in condiciones if c]
    return ("WHERE " + " AND ".join(partes)) if partes else ""


def _breakdown(conn, columna, etiqueta_vacia, rango, params):
    """Cantidad + monto solicitado agrupado por una columna, ordenado por cantidad."""
    rows = conn.execute(text(f"""
        SELECT COALESCE(NULLIF({columna}, ''), :vacia) AS clave,
               COUNT(*) AS n, COALESCE(SUM(monto), 0) AS monto
        FROM sde_consultas
        {_where(*rango)}
        GROUP BY 1 ORDER BY n DESC
    """), {**params, "vacia": etiqueta_vacia}).mappings().all()
    return [{"clave": r["clave"], "n": r["n"],
             "monto": int(r["monto"]), "monto_fmt": _monto(r["monto"])} for r in rows]


@router.get("")
def informe(desde: str = "", hasta: str = "", _=Depends(require_login)):
    """KPIs del informe. `desde`/`hasta` (YYYY-MM-DD o DD-MM-YYYY) acotan por fecha
    de recepción; sin ellos se toma todo el histórico."""
    d = _parse_fecha(desde)
    h = _parse_fecha(hasta)
    if d and h and d > h:
        raise HTTPException(422, "La fecha 'desde' es posterior a la fecha 'hasta'")

    rango = _condiciones_rango(d, h)
    rango_c = _condiciones_rango(d, h, alias="c")
    params = {}
    if d:
        params["desde"] = d
    if h:
        params["hasta"] = h

    with engine.connect() as conn:
        total = conn.execute(text(f"""
            SELECT COUNT(*) FROM sde_consultas {_where(*rango)}
        """), params).scalar() or 0

        por_estado = conn.execute(text(f"""
            SELECT COALESCE(estado, 'SIN ESTADO') AS estado, COUNT(*) AS n
            FROM sde_consultas {_where(*rango)} GROUP BY estado ORDER BY n DESC
        """), params).mappings().all()

        por_tecnico = conn.execute(text(f"""
            SELECT COALESCE(NULLIF(tecnico, ''), '— Sin asignar') AS tecnico, COUNT(*) AS n
            FROM sde_consultas {_where(*rango)} GROUP BY 1 ORDER BY n DESC
        """), params).mappings().all()

        sin_asignar = conn.execute(text(f"""
            SELECT COUNT(*) FROM sde_consultas
            {_where("(tecnico IS NULL OR tecnico = '')", *rango)}
        """), params).scalar() or 0

        sin_acciones = conn.execute(text(f"""
            SELECT COUNT(*) FROM sde_consultas c
            {_where("NOT EXISTS (SELECT 1 FROM sde_acciones a WHERE a.consulta_id = c.id)", *rango_c)}
        """), params).scalar() or 0

        # Las acciones se acotan por la consulta a la que pertenecen, no por su propia
        # fecha: el informe mide la actividad sobre las consultas del período.
        total_acciones = conn.execute(text(f"""
            SELECT COUNT(*) FROM sde_acciones a
            WHERE EXISTS (SELECT 1 FROM sde_consultas c
                          {_where("c.id = a.consulta_id", *rango_c)})
        """), params).scalar() or 0

        # consultas involucradas en un CUIT duplicado
        dup_cuits = cuits_duplicados(conn)
        duplicados = 0
        if dup_cuits:
            duplicados = conn.execute(text(f"""
                SELECT COUNT(*) FROM sde_consultas
                {_where("cuit = ANY(:dcuits)", *rango)}
            """), {**params, "dcuits": list(dup_cuits)}).scalar() or 0

        # montos solicitados. La mediana va además del promedio porque es el
        # estadístico honesto acá: una sola carga alta corre el promedio de lugar.
        m = conn.execute(text(f"""
            SELECT COALESCE(SUM(monto),0) total, COALESCE(ROUND(AVG(monto)),0) prom,
                   COALESCE(MAX(monto),0) maximo, COUNT(monto) con_monto,
                   COALESCE(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY monto),0) mediana,
                   COUNT(*) FILTER (WHERE {_MONTO_EFECTIVO} > {TOPE_LINEA}) fuera_de_tope
            FROM sde_consultas {_where(*rango)}
        """), params).mappings().first()

        filtros_tramo = ", ".join(
            f"COUNT(*) FILTER (WHERE {' AND '.join(filter(None, [f'monto > {lo}' if lo else None, f'monto <= {hi}' if hi else None]))}) AS t{i}"
            for i, (_lbl, lo, hi) in enumerate(_TRAMOS_MONTO)
        )
        tr = conn.execute(text(f"""
            SELECT {filtros_tramo} FROM sde_consultas
            {_where("monto IS NOT NULL", *rango)}
        """), params).mappings().first()

        # breakdowns por sector / línea / programa (de la pestaña INFORME del Excel)
        por_sector = _breakdown(conn, "sector", "(sin sector)", rango, params)
        # Línea y programa los confirma la UEP durante la gestión, no vienen del
        # formulario: una consulta sin encuadrar todavía no es un dato faltante,
        # es una etapa del circuito. La etiqueta lo dice en esos términos.
        por_linea = _breakdown(conn, "linea", _PENDIENTE_UEP, rango, params)
        por_programa = _breakdown(conn, "programa", _PENDIENTE_UEP, rango, params)
        por_arca = _breakdown(conn, _ARCA_AGRUPADA, "(sin dato)", rango, params)
        por_departamento = _breakdown(conn, "departamento", _PENDIENTE_UEP, rango, params)
        por_origen = _breakdown(conn, "como_se_entero", "(sin dato)", rango, params)

        primera_fecha = conn.execute(text(
            "SELECT MIN(fecha_recepcion)::date FROM sde_consultas WHERE fecha_recepcion IS NOT NULL"
        )).scalar()

        # movimiento de la semana (últimos 7 días). Relativo a NOW(), así que no
        # acompaña el rango elegido: lo usa la pantalla de Estadísticas, el informe
        # PDF muestra en su lugar el promedio diario del período.
        nuevas_semana = conn.execute(text("""
            SELECT COUNT(*) FROM sde_consultas
            WHERE fecha_recepcion >= NOW() - INTERVAL '7 days'
        """)).scalar() or 0
        acciones_semana = conn.execute(text("""
            SELECT COUNT(*) FROM sde_acciones
            WHERE created_at >= NOW() - INTERVAL '7 days'
        """)).scalar() or 0

        # Serie temporal. Se rellenan con generate_series los períodos sin consultas:
        # si se devolvieran solo los que tienen datos, el gráfico comprimiría los
        # huecos y un fin de semana muerto se vería como actividad.
        serie_desde = d or (date.today() - timedelta(days=89))
        serie_hasta = h or date.today()
        dias_serie = (serie_hasta - serie_desde).days + 1
        granularidad = "semana" if dias_serie > _DIAS_MAX_SERIE_DIARIA else "dia"
        p_serie = {"sdesde": serie_desde, "shasta": serie_hasta}
        # CAST(:x AS date) y no :x::date — SQLAlchemy no sabe parsear un bindparam
        # pegado al operador :: de Postgres y lo toma como parte del nombre.
        if granularidad == "dia":
            por_dia = conn.execute(text("""
                SELECT d::date AS dia, COUNT(c.id) AS n
                FROM generate_series(CAST(:sdesde AS date), CAST(:shasta AS date), '1 day') d
                LEFT JOIN sde_consultas c ON c.fecha_recepcion::date = d::date
                GROUP BY 1 ORDER BY 1
            """), p_serie).mappings().all()
        else:
            por_dia = conn.execute(text("""
                SELECT d::date AS dia, COUNT(c.id) AS n
                FROM generate_series(date_trunc('week', CAST(:sdesde AS date)),
                                     CAST(:shasta AS date), '7 days') d
                LEFT JOIN sde_consultas c
                  ON date_trunc('week', c.fecha_recepcion)::date = d::date
                 AND c.fecha_recepcion::date BETWEEN CAST(:sdesde AS date) AND CAST(:shasta AS date)
                GROUP BY 1 ORDER BY 1
            """), p_serie).mappings().all()

        # backlog de "consulta inicial" (sin trabajar todavía) por técnico
        inicial_por_tecnico = conn.execute(text(f"""
            SELECT COALESCE(NULLIF(tecnico, ''), '— Sin asignar') AS tecnico, COUNT(*) AS n
            FROM sde_consultas {_where("estado = 'CONSULTA INICIAL'", *rango)}
            GROUP BY 1 ORDER BY n DESC
        """), params).mappings().all()

        # consultas sin ninguna acción cargada, por técnico (a quién pertenecen)
        sin_acciones_por_tecnico = conn.execute(text(f"""
            SELECT COALESCE(NULLIF(c.tecnico, ''), '— Sin asignar') AS tecnico, COUNT(*) AS n
            FROM sde_consultas c
            {_where("NOT EXISTS (SELECT 1 FROM sde_acciones a WHERE a.consulta_id = c.id)", *rango_c)}
            GROUP BY 1 ORDER BY n DESC
        """), params).mappings().all()

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
        # el único que sigue el rango elegido — es el que muestra el informe PDF
        "periodo": round(total / max(1, dias_serie), 1),
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
            "mediana": int(m["mediana"]), "mediana_fmt": _monto(m["mediana"]),
            "con_monto": m["con_monto"],
            "fuera_de_tope": m["fuera_de_tope"],
            "tope_linea_fmt": _monto(TOPE_LINEA),
            "tramos": [{"label": lbl, "n": tr[f"t{i}"]}
                       for i, (lbl, _lo, _hi) in enumerate(_TRAMOS_MONTO)],
        },
        "sectores": por_sector,
        "lineas": por_linea,
        "programas": por_programa,
        "situacion_arca": por_arca,
        "departamentos": por_departamento,
        "origenes": por_origen,
        "promedio_diario": promedio_diario,
        "situacion": situacion,
        "periodo": {
            "desde": serie_desde.isoformat(), "hasta": serie_hasta.isoformat(),
            "desde_fmt": _dmy(serie_desde), "hasta_fmt": _dmy(serie_hasta),
            "dias": dias_serie,
            "acotado": bool(d or h),
            "primera_consulta": primera_fecha.isoformat() if primera_fecha else None,
        },
        "granularidad": granularidad,
        "por_dia": [{"dia": r["dia"].isoformat(), "dia_fmt": _dmy(r["dia"]), "n": r["n"]}
                    for r in por_dia],
        "inicial_por_tecnico": [{"tecnico": r["tecnico"], "n": r["n"]} for r in inicial_por_tecnico],
        "sin_acciones_por_tecnico": [{"tecnico": r["tecnico"], "n": r["n"]} for r in sin_acciones_por_tecnico],
    }
