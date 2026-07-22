"""Modelos Pydantic — contratos de entrada de la API."""
from typing import Optional
from pydantic import BaseModel


class LoginIn(BaseModel):
    username: str
    password: str


class ConsultaIngesta(BaseModel):
    """Payload que manda Power Automate por cada respuesta nueva del MS Forms.
    Un solo formulario → mapeo de campos fijo. Todos opcionales por robustez."""
    fecha_recepcion:     Optional[str] = None
    nombre:              Optional[str] = None
    cuit:                Optional[str] = None
    situacion_arca:      Optional[str] = None
    telefono:            Optional[str] = None
    mail:                Optional[str] = None
    localidad:           Optional[str] = None
    actividad_economica: Optional[str] = None
    sector:              Optional[str] = None
    monto:               Optional[str] = None
    destino:             Optional[str] = None
    como_se_entero:      Optional[str] = None
    genero:              Optional[str] = None


class GestionIn(BaseModel):
    """Edición de los campos de gestión que confirma el técnico."""
    tecnico:              Optional[str] = None
    departamento:         Optional[str] = None
    localidad_confirmada: Optional[str] = None
    garantia:             Optional[str] = None
    linea:                Optional[str] = None
    programa:             Optional[str] = None
    arca_confirmado:      Optional[str] = None
    monto_confirmado:     Optional[str] = None
    actividad_inscripta:  Optional[str] = None
    situacion_bcra:       Optional[str] = None
    monto:                Optional[str] = None
    estado:               Optional[str] = None
    observaciones:        Optional[str] = None
    informacion_extra:    Optional[str] = None
    genero:               Optional[str] = None


class AccionIn(BaseModel):
    fecha:   Optional[str] = None
    accion:  str
    detalle: Optional[str] = ""
