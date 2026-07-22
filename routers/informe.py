"""Informe de gestión (el de los viernes) — KPIs computados server-side."""
from fastapi import APIRouter, Depends
from sqlalchemy import text

from db import engine
from auth import require_login
from constantes import grupo_de, GRUPOS, GRUPOS_ACTIVOS

router = APIRouter(prefix="/api/informe", tags=["informe"])


@router.get("")
def informe(_=Depends(require_login)):
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

    # agrupar estados en grupos
    grupos_cnt = {g[0]: 0 for g in GRUPOS}
    estados_out = []
    for r in por_estado:
        g = grupo_de(r["estado"])
        grupos_cnt[g] = grupos_cnt.get(g, 0) + r["n"]
        estados_out.append({"estado": r["estado"], "n": r["n"], "grupo": g})

    activas = sum(v for k, v in grupos_cnt.items() if k in GRUPOS_ACTIVOS)
    grupos_out = [{"clave": k, "label": lbl, "n": grupos_cnt.get(k, 0)} for k, lbl in GRUPOS]

    return {
        "total": total,
        "activas": activas,
        "sin_asignar": sin_asignar,
        "sin_acciones": sin_acciones,
        "total_acciones": total_acciones,
        "acciones_por_consulta": round(total_acciones / total, 2) if total else 0,
        "nuevas_semana": nuevas_semana,
        "acciones_semana": acciones_semana,
        "grupos": grupos_out,
        "estados": estados_out,
        "tecnicos": [{"tecnico": r["tecnico"], "n": r["n"]} for r in por_tecnico],
    }
