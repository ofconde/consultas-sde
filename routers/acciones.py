"""API de acciones — historial de seguimiento (ilimitado) por consulta."""
import logging
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text

from db import engine
from auth import require_login, puede_editar
from models import AccionIn
from formatos import _parse_fecha, _dmy, _hora_local

router = APIRouter(prefix="/api/consultas/{cid}/acciones", tags=["acciones"])
log = logging.getLogger("consultas_sde.acciones")


def _consulta(conn, cid: int):
    """Devuelve la fila (con `tecnico`) o None si la consulta no existe."""
    return conn.execute(text("SELECT tecnico FROM sde_consultas WHERE id = :id"),
                        {"id": cid}).mappings().first()


@router.get("")
def listar(cid: int, _=Depends(require_login)):
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT * FROM sde_acciones WHERE consulta_id = :id
            ORDER BY fecha DESC NULLS LAST, id DESC
        """), {"id": cid}).mappings().all()
    return [{
        "id": r["id"], "fecha": _dmy(r["fecha"]), "accion": r["accion"],
        "detalle": r["detalle"], "creado_por": r["creado_por"],
        "created_at": _hora_local(r["created_at"]),
    } for r in rows]


@router.post("")
def crear(cid: int, body: AccionIn, usuario=Depends(require_login)):
    if not body.accion or not body.accion.strip():
        raise HTTPException(422, "La acción es obligatoria")
    fecha = _parse_fecha(body.fecha)
    with engine.begin() as conn:
        consulta = _consulta(conn, cid)
        if not consulta:
            raise HTTPException(404, "Consulta no encontrada")
        if not puede_editar(usuario, consulta["tecnico"]):
            raise HTTPException(403, "Esta consulta está asignada a otro técnico")
        conn.execute(text("""
            INSERT INTO sde_acciones (consulta_id, fecha, accion, detalle, creado_por)
            VALUES (:cid, :fecha, :accion, :detalle, :por)
        """), {"cid": cid, "fecha": fecha, "accion": body.accion.strip(),
               "detalle": (body.detalle or "").strip(), "por": usuario["nombre"]})
    return {"ok": True}


@router.delete("/{aid}")
def eliminar(cid: int, aid: int, usuario=Depends(require_login)):
    with engine.begin() as conn:
        consulta = _consulta(conn, cid)
        if not consulta:
            raise HTTPException(404, "Consulta no encontrada")
        if not puede_editar(usuario, consulta["tecnico"]):
            raise HTTPException(403, "Esta consulta está asignada a otro técnico")
        r = conn.execute(text("""
            DELETE FROM sde_acciones WHERE id = :aid AND consulta_id = :cid RETURNING id
        """), {"aid": aid, "cid": cid}).first()
    if not r:
        raise HTTPException(404, "Acción no encontrada")
    log.info("Acción eliminada: id=%s consulta_id=%s por=%s", aid, cid, usuario["username"])
    return {"ok": True}
