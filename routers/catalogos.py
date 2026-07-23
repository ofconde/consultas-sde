"""API de catálogos — alimenta los dropdowns del frontend."""
from fastapi import APIRouter, Depends
from auth import require_login
from db import catalogo
from constantes import ESTADO_GRUPO, GRUPO_COLOR, GRUPOS

router = APIRouter(prefix="/api/catalogos", tags=["catalogos"])

_TIPOS = ["DEPARTAMENTO", "LOCALIDAD", "SECTOR", "SITUACION_AFIP",
          "GARANTIA", "LINEA", "PROGRAMA", "ESTADO", "ACCION"]


@router.get("")
def todos(_=Depends(require_login)):
    """Devuelve todos los catálogos en un solo dict."""
    return {tipo: catalogo(tipo) for tipo in _TIPOS}


@router.get("/estado-grupo")
def estado_grupo(_=Depends(require_login)):
    """Mapa estado→grupo y color por grupo — única fuente de verdad para el
    frontend (antes duplicado a mano en el JS de panel.html/detalle.html)."""
    return {
        "estado_grupo": ESTADO_GRUPO,
        "grupo_color": GRUPO_COLOR,
        "grupos": [{"clave": k, "label": lbl} for k, lbl in GRUPOS],
    }
