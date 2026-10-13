"""Listado de créditos aprobados que entregó la gobernación, cruzado en vivo con
la consulta real de cada expediente (estado de carpeta, monto del sistema,
técnico) y con el detalle del estado que se va cargando a mano.

El listado en sí (nro, proyecto, monto, línea, fecha de firma) es el de la nota
de la gobernación y no se edita; lo que se edita es `detalle_estado`, que vive en
la consulta y se guarda con el PATCH genérico de /api/consultas/{id}.
"""
from fastapi import APIRouter, Depends
from sqlalchemy import text

from db import engine
from auth import require_login
from formatos import _monto, _dmy
from constantes import ESTADOS_CARPETA, ESTADO_CARPETA_COLOR, SITUACIONES_TRAMITE, SITUACION_TRAMITE_COLOR

router = APIRouter(prefix="/api/listado-gobernacion", tags=["gobernacion"])


@router.get("")
def listar(usuario=Depends(require_login)):
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT g.nro, g.solicitante, g.proyecto, g.monto, g.linea, g.fecha_firma, g.nota, g.anulado, g.omitir_informe,
                   c.id AS consulta_id, c.codigo, c.tecnico, c.instancia_superior,
                   c.estado_carpeta, c.detalle_estado, c.situacion_tramite, c.firmado,
                   COALESCE(NULLIF(c.monto_instancia_superior, 0), NULLIF(c.monto_confirmado, 0), c.monto) AS monto_sistema
            FROM sde_listado_gobernacion g
            LEFT JOIN sde_consultas c ON c.id = g.consulta_id
            ORDER BY g.nro
        """)).mappings().all()

    casos = []
    for r in rows:
        sistema = int(r["monto_sistema"] or 0) if r["consulta_id"] else None
        casos.append({
            "nro": r["nro"], "solicitante": r["solicitante"], "proyecto": r["proyecto"] or "",
            "monto": r["monto"], "monto_fmt": _monto(r["monto"]),
            "linea": r["linea"] or "—",
            "fecha_firma": _dmy(r["fecha_firma"]) if r["fecha_firma"] else "—",
            "nota": r["nota"] or "",
            "anulado": bool(r["anulado"]),
            "omitir_informe": bool(r["omitir_informe"]),
            "consulta_id": r["consulta_id"], "codigo": r["codigo"],
            "tecnico": r["tecnico"] or "Sin asignar" if r["consulta_id"] else "—",
            "en_instancia_superior": bool(r["instancia_superior"]),
            "estado_carpeta": r["estado_carpeta"] or "",
            "detalle_estado": r["detalle_estado"] or "",
            "situacion_tramite": r["situacion_tramite"] or "",
            "monto_sistema": sistema,
            "monto_sistema_fmt": _monto(sistema) if sistema is not None else "—",
            "monto_difiere": sistema is not None and r["monto"] is not None and sistema != r["monto"],
        })
    return {
        "total": len(casos),
        "monto_total_fmt": _monto(sum(c["monto"] or 0 for c in casos)),
        "estados": ESTADOS_CARPETA,
        "estado_carpeta_color": ESTADO_CARPETA_COLOR,
        "situaciones": SITUACIONES_TRAMITE,
        "situacion_color": SITUACION_TRAMITE_COLOR,
        "casos": casos,
    }
