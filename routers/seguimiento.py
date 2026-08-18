"""Seguimiento personal — casos que el coordinador decide vigilar de cerca, más
allá de a quién estén asignados. Distinto de "mis casos" (que filtra por
asignación de técnico): acá el criterio es 100% manual, se tilda caso por caso
desde el detalle, y admite una nota privada que solo ve quien la escribió.
"""
import logging
from fastapi import APIRouter, Depends, HTTPException, Body
from sqlalchemy import text

from db import engine
from auth import require_coordinador
from constantes import grupo_de
from formatos import _dmy, _monto

router = APIRouter(prefix="/api/seguimiento", tags=["seguimiento"])
log = logging.getLogger("consultas_sde.seguimiento")


@router.get("")
def listar(usuario=Depends(require_coordinador)):
    """Casos que sigue el usuario logueado, ordenados por última actividad real
    (el último cambio de gestión o la última acción cargada, lo que sea más
    reciente) — así arriba aparece lo que se movió hace poco, no lo más viejo
    que se empezó a seguir."""
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT s.id AS seguimiento_id, s.nota, s.created_at AS desde,
                   c.id, c.codigo, c.nombre, c.cuit, c.tecnico, c.estado,
                   c.monto_confirmado, c.monto, c.updated_at, ua.ultima_fecha
            FROM sde_seguimiento s
            JOIN sde_consultas c ON c.id = s.consulta_id
            LEFT JOIN LATERAL (
                SELECT MAX(created_at) AS ultima_fecha
                FROM sde_acciones a WHERE a.consulta_id = c.id
            ) ua ON true
            WHERE s.usuario = :u
            ORDER BY GREATEST(c.updated_at, COALESCE(ua.ultima_fecha, c.updated_at)) DESC
        """), {"u": usuario["username"]}).mappings().all()

    data = []
    for r in rows:
        ultima = max(v for v in (r["updated_at"], r["ultima_fecha"]) if v) if (r["updated_at"] or r["ultima_fecha"]) else None
        data.append({
            "seguimiento_id": r["seguimiento_id"],
            "id": r["id"],
            "codigo": r["codigo"],
            "nombre": r["nombre"],
            "cuit": r["cuit"],
            "tecnico": r["tecnico"],
            "estado": r["estado"],
            "grupo": grupo_de(r["estado"]),
            "monto": _monto(r["monto_confirmado"] or r["monto"]),
            "nota": r["nota"] or "",
            "ultima_actividad": _dmy(ultima) if ultima else None,
            "desde": _dmy(r["desde"]),
        })
    return {"total": len(data), "seguidos": data}


@router.get("/ids")
def mis_ids(usuario=Depends(require_coordinador)):
    """IDs de consulta que sigue el usuario logueado — liviano, para pintar la
    estrella llena/vacía en el detalle sin traer todo /api/seguimiento."""
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT consulta_id FROM sde_seguimiento WHERE usuario = :u"),
                            {"u": usuario["username"]}).all()
    return {"ids": [r[0] for r in rows]}


@router.post("/{cid}")
def seguir(cid: int, usuario=Depends(require_coordinador)):
    with engine.begin() as conn:
        existe = conn.execute(text("SELECT 1 FROM sde_consultas WHERE id = :id"), {"id": cid}).scalar()
        if not existe:
            raise HTTPException(404, "Consulta no encontrada")
        conn.execute(text("""
            INSERT INTO sde_seguimiento (consulta_id, usuario)
            VALUES (:cid, :u) ON CONFLICT (consulta_id, usuario) DO NOTHING
        """), {"cid": cid, "u": usuario["username"]})
    return {"ok": True, "siguiendo": True}


@router.delete("/{cid}")
def dejar_de_seguir(cid: int, usuario=Depends(require_coordinador)):
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM sde_seguimiento WHERE consulta_id = :cid AND usuario = :u"),
                     {"cid": cid, "u": usuario["username"]})
    return {"ok": True, "siguiendo": False}


@router.patch("/{cid}")
def editar_nota(cid: int, body: dict = Body(...), usuario=Depends(require_coordinador)):
    nota = (body.get("nota") or "").strip()
    with engine.begin() as conn:
        r = conn.execute(text("""
            UPDATE sde_seguimiento SET nota = :nota
            WHERE consulta_id = :cid AND usuario = :u RETURNING id
        """), {"nota": nota, "cid": cid, "u": usuario["username"]}).first()
    if not r:
        raise HTTPException(404, "No estás siguiendo esta consulta")
    return {"ok": True}
