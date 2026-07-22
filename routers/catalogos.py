"""API de catálogos — alimenta los dropdowns del frontend."""
from fastapi import APIRouter, Depends
from auth import require_login
from db import catalogo

router = APIRouter(prefix="/api/catalogos", tags=["catalogos"])

_TIPOS = ["DEPARTAMENTO", "LOCALIDAD", "SECTOR", "SITUACION_AFIP",
          "GARANTIA", "LINEA", "PROGRAMA", "ESTADO", "ACCION"]


@router.get("")
def todos(_=Depends(require_login)):
    """Devuelve todos los catálogos en un solo dict."""
    return {tipo: catalogo(tipo) for tipo in _TIPOS}
