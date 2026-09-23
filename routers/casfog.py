"""OK CASFOG — casos aprobados por el fondo de garantía CASFOG, consolidados a
mano desde las planillas sueltas que van llegando (hoy no hay un formato único
ni un import automático). Solo para coordinador/admin.

El monto aprobado y la fuente/alerta se cargan una vez por CUIT en
sde_casfog_ok; el monto SOLICITADO nunca sale de ahí — se lee en vivo de
sde_consultas, porque el de esas planillas es un genérico ($50.000.000 fijo)
que no refleja lo que la consulta pidió realmente.
"""
from fastapi import APIRouter, Depends
from sqlalchemy import text

from db import engine
from auth import require_coordinador
from formatos import _monto

router = APIRouter(prefix="/api/casfog-ok", tags=["casfog"])


@router.get("")
def listar(usuario=Depends(require_coordinador)):
    with engine.connect() as conn:
        # LATERAL en vez de un JOIN directo: un mismo CUIT puede tener más de
        # una consulta cargada (reingresos), y un JOIN plano duplicaría la fila
        # de OK CASFOG una vez por cada una — acá se toma solo la más reciente.
        rows = conn.execute(text("""
            SELECT k.cuit, k.monto_aprobado, k.fuente, k.alerta,
                   c.id AS consulta_id, c.codigo, c.nombre, c.mail, c.telefono,
                   COALESCE(NULLIF(c.actividad_economica, ''), c.actividad_inscripta) AS actividad,
                   c.destino, c.estado, c.situacion_arca, c.tecnico,
                   COALESCE(NULLIF(c.monto_confirmado, 0), c.monto) AS monto_solicitado
            FROM sde_casfog_ok k
            LEFT JOIN LATERAL (
                SELECT * FROM sde_consultas sc WHERE sc.cuit = k.cuit
                ORDER BY sc.fecha_recepcion DESC NULLS LAST, sc.id DESC
                LIMIT 1
            ) c ON true
            ORDER BY k.alerta DESC NULLS LAST, c.nombre NULLS LAST
        """)).mappings().all()

    casos = [{
        "cuit": r["cuit"],
        "consulta_id": r["consulta_id"],
        "codigo": r["codigo"],
        "nombre": r["nombre"] or "— sin match en Consultas SDE —",
        "monto_solicitado": int(r["monto_solicitado"] or 0) if r["consulta_id"] else None,
        "monto_solicitado_fmt": _monto(r["monto_solicitado"]) if r["consulta_id"] else "—",
        "monto_aprobado": r["monto_aprobado"],
        "monto_aprobado_fmt": _monto(r["monto_aprobado"]) if r["monto_aprobado"] is not None else None,
        "fuente": r["fuente"] or "—",
        "alerta": r["alerta"] or "",
        "mail": r["mail"] or "—",
        "telefono": r["telefono"] or "—",
        "actividad": r["actividad"] or "—",
        "destino": r["destino"] or "—",
        "estado": r["estado"] or "—",
        "situacion_arca": r["situacion_arca"] or "—",
        "tecnico": r["tecnico"] or "Sin asignar",
    } for r in rows]

    con_match = [c for c in casos if c["consulta_id"]]
    return {
        "total": len(casos),
        "con_alerta": sum(1 for c in casos if c["alerta"]),
        "monto_solicitado_total_fmt": _monto(sum(c["monto_solicitado"] for c in con_match)),
        "monto_aprobado_total_fmt": _monto(sum(c["monto_aprobado"] or 0 for c in casos)),
        "casos": casos,
    }
