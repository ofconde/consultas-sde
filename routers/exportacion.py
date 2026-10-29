"""Exportación de solo lectura para SIGI.

SIGI trae de acá las consultas de Santiago del Estero (y las acciones de cada una) para
mostrarlas en su módulo Consultas, como las demás UEP. El flujo es de un solo sentido:
SDE es la fuente de verdad y SIGI nunca escribe de vuelta.

Se protege con SDE_EXPORT_KEY, una clave APARTE de SDE_API_KEY: la de la ingesta permite
crear consultas, esta solo permite leer. Si se filtra una, la otra no queda comprometida.

Contrato de salida (lo consume SIGI, no cambiar sin avisar):
  {"generado": ISO8601, "total": N, "consultas": [ {<campos de consulta>, "acciones": [...]} ]}
Las consultas marcadas REPETIDO no se exportan: son duplicados que el equipo ya descartó.
"""
import os
import secrets
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Header
from sqlalchemy import text

from db import engine

router = APIRouter(prefix="/api/export", tags=["export"])
log = logging.getLogger("consultas_sde.export")

_EXPORT_KEY = os.environ.get("SDE_EXPORT_KEY", "")

# Campos de consulta que viajan a SIGI. Las notas privadas del seguimiento, la auditoría
# y el detalle de instancia superior quedan acá: SIGI no los necesita.
_CAMPOS_CONSULTA = (
    "codigo", "estado", "fecha_recepcion", "nombre", "cuit", "situacion_arca", "telefono", "mail",
    "localidad", "actividad_economica", "sector", "monto", "destino", "como_se_entero", "tecnico",
    "departamento", "localidad_confirmada", "garantia", "linea", "programa", "arca_confirmado",
    "monto_confirmado", "actividad_inscripta", "situacion_bcra", "observaciones", "genero",
)


def _verificar_clave(x_api_key: str):
    if not _EXPORT_KEY or not secrets.compare_digest(x_api_key, _EXPORT_KEY):
        log.warning("Exportación rechazada: X-API-Key inválida")
        raise HTTPException(401, "X-API-Key inválida")


def _iso(valor):
    return valor.isoformat() if valor is not None else None


@router.get("/consultas")
def exportar_consultas(x_api_key: str = Header(default="")):
    _verificar_clave(x_api_key)
    columnas = ", ".join(f"c.{c}" for c in _CAMPOS_CONSULTA)
    with engine.connect() as conn:
        filas = conn.execute(text(f"""
            SELECT c.id, {columnas}, c.updated_at
            FROM sde_consultas c
            WHERE COALESCE(c.estado, '') <> 'REPETIDO'
            ORDER BY c.id
        """)).mappings().all()
        acciones = conn.execute(text("""
            SELECT a.id, a.consulta_id, a.fecha, a.accion, a.detalle, a.creado_por
            FROM sde_acciones a
            JOIN sde_consultas c ON c.id = a.consulta_id
            WHERE COALESCE(c.estado, '') <> 'REPETIDO'
            ORDER BY a.consulta_id, a.fecha, a.id
        """)).mappings().all()

    por_consulta = {}
    for a in acciones:
        por_consulta.setdefault(a["consulta_id"], []).append({
            "id": a["id"], "fecha": _iso(a["fecha"]), "accion": a["accion"],
            "detalle": a["detalle"] or "", "creado_por": a["creado_por"] or "",
        })

    consultas = []
    for r in filas:
        item = {c: r[c] for c in _CAMPOS_CONSULTA}
        item["fecha_recepcion"] = _iso(r["fecha_recepcion"])
        item["updated_at"] = _iso(r["updated_at"])
        item["acciones"] = por_consulta.get(r["id"], [])
        consultas.append(item)

    return {
        "generado": datetime.now(timezone.utc).isoformat(),
        "total": len(consultas),
        "consultas": consultas,
    }
