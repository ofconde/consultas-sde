"""Modelos Pydantic — contratos de entrada de la API."""
from typing import List, Optional
from pydantic import BaseModel, Field

_TXT = Field(default=None, max_length=2000)


class ConsultaIngesta(BaseModel):
    """Payload que manda Power Automate por cada respuesta nueva del MS Forms.
    Un solo formulario → mapeo de campos fijo. Todos opcionales por robustez."""
    fecha_recepcion:     Optional[str] = _TXT
    nombre:              Optional[str] = _TXT
    cuit:                Optional[str] = _TXT
    situacion_arca:      Optional[str] = _TXT
    telefono:            Optional[str] = _TXT
    mail:                Optional[str] = _TXT
    localidad:           Optional[str] = _TXT
    actividad_economica: Optional[str] = _TXT
    sector:               Optional[str] = _TXT
    monto:                Optional[str] = _TXT
    destino:              Optional[str] = _TXT
    como_se_entero:       Optional[str] = _TXT
    genero:               Optional[str] = _TXT


class GestionIn(BaseModel):
    """Edición de los campos de gestión que confirma el técnico."""
    tecnico:              Optional[str] = _TXT
    departamento:         Optional[str] = _TXT
    localidad_confirmada: Optional[str] = _TXT
    garantia:             Optional[str] = _TXT
    linea:                Optional[str] = _TXT
    programa:             Optional[str] = _TXT
    arca_confirmado:      Optional[str] = _TXT
    monto_confirmado:     Optional[str] = _TXT
    actividad_inscripta:  Optional[str] = _TXT
    situacion_bcra:       Optional[str] = _TXT
    monto:                Optional[str] = _TXT
    estado:               Optional[str] = _TXT
    observaciones:        Optional[str] = _TXT
    informacion_extra:    Optional[str] = _TXT
    genero:               Optional[str] = _TXT


class ConsultaManualIn(ConsultaIngesta, GestionIn):
    """Alta manual de una consulta que llegó por un medio distinto al formulario
    (llamada telefónica, presencial, mail directo, etc.). Combina los campos del
    solicitante con los de gestión en un solo alta — a diferencia de la ingesta
    automática, acá el técnico ya conoce y confirma todo de una."""
    nombre: str = Field(max_length=2000)


class AccionIn(BaseModel):
    fecha:   Optional[str] = _TXT
    accion:  str = Field(max_length=2000)
    detalle: Optional[str] = Field(default="", max_length=2000)


class BulkGestionIn(BaseModel):
    """Asignación en lote: mismo técnico y/o estado para un grupo de consultas
    (ej. 15 casos sin ARCA activo que llegan juntos y se descartan de una)."""
    ids:            List[int] = Field(min_length=1, max_length=500)
    tecnico:        Optional[str] = _TXT
    estado:         Optional[str] = _TXT
    arca_confirmado: Optional[str] = _TXT
    linea:          Optional[str] = _TXT


class BulkAccionIn(BaseModel):
    """Misma acción de seguimiento cargada a un grupo de consultas de una sola vez."""
    ids:     List[int] = Field(min_length=1, max_length=500)
    fecha:   Optional[str] = _TXT
    accion:  str = Field(max_length=2000)
    detalle: Optional[str] = Field(default="", max_length=2000)
