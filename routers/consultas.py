"""API de consultas — listar/filtrar, detalle, editar gestión, alta manual, baja."""
import unicodedata
from fastapi import APIRouter, Depends, HTTPException, Request, Body
from sqlalchemy import text

from db import engine, proximo_codigo
from auth import require_login, require_coordinador
from models import GestionIn
from formatos import _dmy, _monto, _hora_local, _parse_monto
from constantes import grupo_de, ROL_COORDINADOR

router = APIRouter(prefix="/api/consultas", tags=["consultas"])

# columnas de gestión que puede editar el técnico
_GESTION_COLS = [
    "tecnico", "departamento", "localidad_confirmada", "garantia", "linea",
    "programa", "arca_confirmado", "actividad_inscripta", "situacion_bcra",
    "estado", "observaciones", "informacion_extra", "genero",
]


def _norm(s: str) -> str:
    """Normaliza para comparar técnicos: sin acentos, mayúsculas, sin espacios extra."""
    s = unicodedata.normalize("NFD", s or "")
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return s.upper().strip()


def _fila_resumen(r):
    return {
        "id": r["id"],
        "codigo": r["codigo"],
        "fecha_recepcion": _dmy(r["fecha_recepcion"]),
        "nombre": r["nombre"],
        "cuit": r["cuit"],
        "localidad": r["localidad_confirmada"] or r["localidad"],
        "departamento": r["departamento"],
        "sector": r["sector"],
        "monto": _monto(r["monto_confirmado"] or r["monto"]),
        "monto_num": r["monto_confirmado"] or r["monto"] or 0,
        "linea": r["linea"],
        "programa": r["programa"],
        "tecnico": r["tecnico"],
        "estado": r["estado"],
        "grupo": grupo_de(r["estado"]),
        "n_acciones": r["n_acciones"],
    }


@router.get("")
def listar(request: Request, estado: str = "", tecnico: str = "",
           grupo: str = "", q: str = "", mios: bool = False,
           usuario=Depends(require_login)):
    """Lista consultas con filtros. `q` busca por nombre o CUIT.
    `mios=1` filtra las asignadas al técnico logueado (match sin acentos/mayúsculas)."""
    where = ["1=1"]
    params = {}
    if estado:
        where.append("c.estado = :estado"); params["estado"] = estado
    if tecnico:
        if tecnico == "__sin__":
            where.append("(c.tecnico IS NULL OR c.tecnico = '')")
        else:
            where.append("c.tecnico = :tecnico"); params["tecnico"] = tecnico
    if q:
        where.append("(c.nombre ILIKE :q OR c.cuit ILIKE :q)"); params["q"] = f"%{q}%"

    with engine.connect() as conn:
        rows = conn.execute(text(f"""
            SELECT c.*,
                   (SELECT COUNT(*) FROM sde_acciones a WHERE a.consulta_id = c.id) AS n_acciones
            FROM sde_consultas c
            WHERE {' AND '.join(where)}
            ORDER BY c.fecha_recepcion DESC NULLS LAST, c.id DESC
        """), params).mappings().all()

    data = [_fila_resumen(r) for r in rows]
    if grupo:
        data = [d for d in data if d["grupo"] == grupo]
    if mios:
        yo = _norm(usuario["nombre"])
        data = [d for d in data if _norm(d["tecnico"]) == yo]
    return {"total": len(data), "consultas": data}


@router.get("/{cid}")
def detalle(cid: int, _=Depends(require_login)):
    with engine.connect() as conn:
        c = conn.execute(text("SELECT * FROM sde_consultas WHERE id = :id"),
                         {"id": cid}).mappings().first()
        if not c:
            raise HTTPException(404, "Consulta no encontrada")
        acc = conn.execute(text("""
            SELECT * FROM sde_acciones WHERE consulta_id = :id
            ORDER BY fecha DESC NULLS LAST, id DESC
        """), {"id": cid}).mappings().all()

    consulta = dict(c)
    consulta["fecha_recepcion_fmt"] = _dmy(c["fecha_recepcion"])
    consulta["monto_fmt"] = _monto(c["monto"])
    consulta["monto_confirmado_fmt"] = _monto(c["monto_confirmado"])
    consulta["grupo"] = grupo_de(c["estado"])
    acciones = [{
        "id": a["id"], "fecha": _dmy(a["fecha"]), "accion": a["accion"],
        "detalle": a["detalle"], "creado_por": a["creado_por"],
        "created_at": _hora_local(a["created_at"]),
    } for a in acc]
    return {"consulta": consulta, "acciones": acciones}


@router.patch("/{cid}")
def editar_gestion(cid: int, body: GestionIn, usuario=Depends(require_login)):
    campos = body.model_dump(exclude_none=True)
    sets, params = [], {"id": cid}
    for col in _GESTION_COLS:
        if col in campos:
            sets.append(f"{col} = :{col}"); params[col] = campos[col]
    if "monto_confirmado" in campos:
        sets.append("monto_confirmado = :monto_confirmado")
        params["monto_confirmado"] = _parse_monto(campos["monto_confirmado"])
    if not sets:
        return {"ok": True, "sin_cambios": True}
    sets.append("updated_at = NOW()")
    with engine.begin() as conn:
        r = conn.execute(text(f"""
            UPDATE sde_consultas SET {', '.join(sets)} WHERE id = :id RETURNING id
        """), params).first()
    if not r:
        raise HTTPException(404, "Consulta no encontrada")
    return {"ok": True}


@router.delete("/{cid}")
def eliminar(cid: int, _=Depends(require_coordinador)):
    with engine.begin() as conn:
        r = conn.execute(text("DELETE FROM sde_consultas WHERE id = :id RETURNING id"),
                         {"id": cid}).first()
    if not r:
        raise HTTPException(404, "Consulta no encontrada")
    return {"ok": True}
