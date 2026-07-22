"""Ingesta desde Microsoft Forms vía Power Automate.

PA dispara "cuando se envía una nueva respuesta" → HTTP POST acá con header X-API-Key.
Un solo formulario → mapeo fijo. Inserta la consulta como CONSULTA INICIAL, sin técnico.
"""
import os
from fastapi import APIRouter, HTTPException, Header
from sqlalchemy import text

from db import engine, proximo_codigo
from models import ConsultaIngesta
from formatos import _parse_monto, _parse_fecha

router = APIRouter(prefix="/api/ingesta", tags=["ingesta"])

_API_KEY = os.environ.get("SDE_API_KEY", "")


@router.post("/consulta")
def ingesta_consulta(payload: ConsultaIngesta, x_api_key: str = Header(default="")):
    if not _API_KEY or x_api_key != _API_KEY:
        raise HTTPException(401, "X-API-Key inválida")

    fecha = _parse_fecha(payload.fecha_recepcion)
    with engine.begin() as conn:
        # anti-duplicado suave: mismo CUIT + misma fecha de recepción
        if payload.cuit and fecha:
            dup = conn.execute(text("""
                SELECT codigo FROM sde_consultas
                WHERE cuit = :cuit AND fecha_recepcion::date = :fecha
                LIMIT 1
            """), {"cuit": payload.cuit, "fecha": fecha}).scalar()
            if dup:
                return {"ok": True, "duplicado": True, "codigo": dup}

        codigo = proximo_codigo()
        conn.execute(text("""
            INSERT INTO sde_consultas
                (codigo, fuente, fecha_recepcion, nombre, cuit, situacion_arca,
                 telefono, mail, localidad, actividad_economica, sector, monto,
                 destino, como_se_entero, genero, estado)
            VALUES
                (:codigo, 'Formulario', :fecha, :nombre, :cuit, :arca,
                 :tel, :mail, :localidad, :actividad, :sector, :monto,
                 :destino, :como, :genero, 'CONSULTA INICIAL')
        """), {
            "codigo": codigo, "fecha": payload.fecha_recepcion, "nombre": payload.nombre,
            "cuit": payload.cuit, "arca": payload.situacion_arca, "tel": payload.telefono,
            "mail": payload.mail, "localidad": payload.localidad,
            "actividad": payload.actividad_economica, "sector": payload.sector,
            "monto": _parse_monto(payload.monto), "destino": payload.destino,
            "como": payload.como_se_entero, "genero": payload.genero,
        })
    return {"ok": True, "codigo": codigo}
