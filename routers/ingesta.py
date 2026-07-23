"""Ingesta desde Microsoft Forms vía Power Automate.

PA dispara "cuando se envía una nueva respuesta" → HTTP POST acá con header X-API-Key.
Un solo formulario → mapeo fijo. Inserta la consulta como CONSULTA INICIAL, sin técnico.

Dos endpoints:
- /consulta: payload ya curado con nuestros nombres de campo (uso manual/pruebas).
- /forms-santiago: recibe el JSON crudo de "Obtener los detalles de la respuesta" de
  MS Forms (claves hash opacas — el formulario tiene varias ramas y Power Automate no
  les puede dar un nombre estable en su selector de contenido dinámico, así que mapear
  campo por campo a mano en PA es frágil). Confirmado contra una respuesta real de
  prueba el 23/07/2026.
"""
import os
import secrets
import logging
from datetime import datetime
from fastapi import APIRouter, HTTPException, Header, Body
from sqlalchemy import text

from db import engine, proximo_codigo
from models import ConsultaIngesta
from formatos import _parse_monto, _parse_fecha

router = APIRouter(prefix="/api/ingesta", tags=["ingesta"])
log = logging.getLogger("consultas_sde.ingesta")

_API_KEY = os.environ.get("SDE_API_KEY", "")

# Claves internas (hash opacos) del formulario "FORMULARIO DE CONSULTA SANTIAGO DEL
# ESTERO" -> nuestros campos. El formulario es fijo (nadie lo edita sin avisar antes),
# así que este mapeo es estable mientras no cambie la estructura de preguntas.
_FORMS_FIELD_MAP = {
    "nombre":              "r756947cc0bf64ddfb7f25697417d72cc",
    "cuit":                "r8130cd0e80f6476fa15b02a2a60ab45d",
    "situacion_arca":      "rcf669b4c7992435f9ac8758d962661a8",
    "telefono":            "r849e46e4f95146468b47304ea3df783c",
    "mail":                "r18732c9c7f0c474ba32c530d2edfa40d",
    "localidad":           "rc583797d72124ee288989f79ea6246d6",
    "actividad_economica": "r09ab0abc692a4447ab81522ca30d8ddc",
    "sector":              "r632a3e4d2a6b4fdba38919ff39c4c0f5",
    "monto":               "r298d3ede4b2448bca47e5a0ea5949dd5",
    "destino":             "r9eedafcf52df430190ab3123fe7a52e0",
    "como_se_entero":      "r7ec0254950874b1984f5caae94477f4a",
}


def _verificar_api_key(x_api_key: str):
    if not _API_KEY or not secrets.compare_digest(x_api_key, _API_KEY):
        log.warning("Ingesta rechazada: X-API-Key inválida")
        raise HTTPException(401, "X-API-Key inválida")


def _insertar_consulta(payload: ConsultaIngesta) -> dict:
    """Lógica compartida de alta: anti-duplicado + INSERT. Usada por ambos endpoints."""
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
    log.info("Alta por ingesta: codigo=%s cuit=%s", codigo, payload.cuit)
    return {"ok": True, "codigo": codigo}


@router.post("/consulta")
def ingesta_consulta(payload: ConsultaIngesta, x_api_key: str = Header(default="")):
    """Ingesta con payload ya curado (nuestros nombres de campo). Uso manual/pruebas."""
    _verificar_api_key(x_api_key)
    return _insertar_consulta(payload)


@router.post("/forms-santiago")
def ingesta_forms_santiago(payload_pa: dict = Body(...), x_api_key: str = Header(default="")):
    """Ingesta directa desde Power Automate: recibe el JSON de 'Obtener los detalles
    de la respuesta' y lo traduce con _FORMS_FIELD_MAP. Tolera dos formas del body
    (según cómo Power Automate termine mandándolo): el objeto de respuestas directo
    ({"submitDate":..., "r756947...":...}) o envuelto en {"body": {...}, "statusCode":...,
    "headers":...} — si hay una clave "body" que es un dict, se usa esa."""
    _verificar_api_key(x_api_key)

    body = payload_pa.get("body") if isinstance(payload_pa.get("body"), dict) else payload_pa

    def _campo(nombre_interno: str):
        val = body.get(_FORMS_FIELD_MAP[nombre_interno])
        return val if val else None

    fecha_iso = None
    fecha_raw = body.get("submitDate")
    if fecha_raw:
        try:
            fecha_iso = datetime.strptime(fecha_raw, "%m/%d/%Y %I:%M:%S %p").strftime("%Y-%m-%d")
        except ValueError:
            log.warning("submitDate con formato inesperado: %r", fecha_raw)

    payload = ConsultaIngesta(
        fecha_recepcion=fecha_iso,
        nombre=_campo("nombre"),
        cuit=_campo("cuit"),
        situacion_arca=_campo("situacion_arca"),
        telefono=_campo("telefono"),
        mail=_campo("mail"),
        localidad=_campo("localidad"),
        actividad_economica=_campo("actividad_economica"),
        sector=_campo("sector"),
        monto=_campo("monto"),
        destino=_campo("destino"),
        como_se_entero=_campo("como_se_entero"),
    )
    return _insertar_consulta(payload)
