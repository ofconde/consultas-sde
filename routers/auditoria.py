"""Auditoría de bajas de consultas — ver db.py (sde_consultas_bajas) y el DELETE
en routers/consultas.py, que graba el snapshot antes de borrar."""
from fastapi import APIRouter, Depends
from sqlalchemy import text

from db import engine
from auth import require_coordinador
from formatos import _hora_local

router = APIRouter(prefix="/api/auditoria", tags=["auditoria"])


@router.get("/bajas")
def listar_bajas(usuario=Depends(require_coordinador)):
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT id, consulta_id, codigo, snapshot, eliminado_por, eliminado_en
            FROM sde_consultas_bajas ORDER BY eliminado_en DESC
        """)).mappings().all()
    return {"total": len(rows), "bajas": [{
        "id": r["id"],
        "consulta_id": r["consulta_id"],
        "codigo": r["codigo"],
        "nombre": r["snapshot"].get("nombre"),
        "cuit": r["snapshot"].get("cuit"),
        "estado": r["snapshot"].get("estado"),
        "tecnico": r["snapshot"].get("tecnico"),
        "eliminado_por": r["eliminado_por"],
        "eliminado_en": _hora_local(r["eliminado_en"]),
    } for r in rows]}
