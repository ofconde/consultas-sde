"""API de situación crediticia (Central de Deudores del BCRA), con caché."""
import json
import logging
import urllib.error

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text

import bcra
from db import engine
from auth import require_login

router = APIRouter(prefix="/api/bcra", tags=["bcra"])
log = logging.getLogger("consultas_sde.bcra")

# El BCRA actualiza la central una vez por mes; 7 días de caché es de sobra y
# evita repreguntar cada vez que se abre la misma consulta.
_CACHE_DIAS = 7


@router.get("/{cuit}")
def situacion(cuit: str, _=Depends(require_login)):
    limpio = bcra.limpiar_cuit(cuit)
    if not limpio:
        raise HTTPException(400, "CUIT inválido")

    with engine.connect() as conn:
        row = conn.execute(text(f"""
            SELECT payload FROM sde_bcra_cache
            WHERE cuit = :c AND consultado_en > NOW() - INTERVAL '{_CACHE_DIAS} days'
        """), {"c": limpio}).first()
    if row:
        return {**row[0], "cuit": limpio, "origen": "cache"}

    try:
        data = bcra.consultar(limpio)
    except urllib.error.HTTPError as e:
        if e.code == 429:
            # El BCRA rate-limitea agresivamente por IP (confirmado: ráfagas de
            # ~10 consultas casi simultáneas ya lo disparan). El panel pide esto
            # en secuencia con pausa entre pedidos por esto mismo — si aun así
            # llega acá, es que hay demasiadas consultas juntas en este momento
            # (varios usuarios a la vez, o una ráfaga real).
            log.warning("BCRA rate-limit (429) para %s", limpio)
            raise HTTPException(429, "Límite de consultas al BCRA alcanzado. Probá de nuevo en un momento.")
        log.warning("BCRA no disponible para %s: HTTP %s", limpio, e.code)
        raise HTTPException(502, "El BCRA no respondió. Probá de nuevo en un rato.")
    except Exception as e:
        # Que el BCRA esté caído no puede romper la pantalla de la consulta.
        log.warning("BCRA no disponible para %s: %s: %s", limpio, type(e).__name__, e)
        raise HTTPException(502, "El BCRA no respondió. Probá de nuevo en un rato.")

    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO sde_bcra_cache (cuit, denominacion, situacion_max, periodo, payload)
            VALUES (:c, :d, :s, :p, CAST(:pl AS JSONB))
            ON CONFLICT (cuit) DO UPDATE SET
                denominacion = EXCLUDED.denominacion,
                situacion_max = EXCLUDED.situacion_max,
                periodo = EXCLUDED.periodo,
                payload = EXCLUDED.payload,
                consultado_en = NOW()
        """), {
            "c": limpio, "d": data.get("denominacion"),
            "s": data.get("situacion_max"), "p": data.get("periodo"),
            "pl": json.dumps(data),
        })
    return {**data, "cuit": limpio, "origen": "bcra"}
