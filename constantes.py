"""Constantes de negocio del seguimiento de consultas."""
import unicodedata

# Mapa estado → grupo (para kanban e informe). Basado en la hoja `indicadores`
# del Excel de Santiago del Estero.
ESTADO_GRUPO = {
    "CONSULTA INICIAL":                 "INICIAL",
    "COMPLETANDO DOCUMENTACION":        "EN_GESTION",
    "EN GESTION CON SGR O FONDO":       "EN_GESTION",
    "HAY INTERÉS PERO NO SE DECIDE":    "EN_GESTION",
    "INGRESADO EN CFI SEDE":            "EN_SEDE",
    "DERIVADO A FONDO DE GARANTIA CFI": "EN_SEDE",
    "DERIVADO A MERCADO DE CAPITALES":  "EN_SEDE",
    "DERIVADO A OTRA PROVINCIA":        "EN_SEDE",
    "NO ES FINANCIABLE":                "INACTIVAS",
    "DESISTE DE TOMAR EL CRÉDITO":      "INACTIVAS",
    "REPETIDO":                         "INACTIVAS",
}

# Orden y etiquetas de los grupos para el informe/kanban
GRUPOS = [
    ("INICIAL",    "Consulta inicial"),
    ("EN_GESTION", "En gestión"),
    ("EN_SEDE",    "En sede / derivadas"),
    ("INACTIVAS",  "Inactivas"),
]

# Un grupo se considera "activo" (dentro del universo de trabajo) si no es INACTIVAS
GRUPOS_ACTIVOS = {"INICIAL", "EN_GESTION", "EN_SEDE"}

# Color por grupo — misma paleta que .g-INICIAL/.g-EN_GESTION/.g-EN_SEDE/.g-INACTIVAS
# de static/css/panel.css. Única fuente de verdad: antes vivía duplicado como hex
# hardcodeado en el JS de panel.html y detalle.html (routers/catalogos.py lo expone).
GRUPO_COLOR = {
    "INICIAL":    "#2f6fd0",
    "EN_GESTION": "#6c4bd0",
    "EN_SEDE":    "#1a9e57",
    "INACTIVAS":  "#8891a8",
}

ROL_COORDINADOR = "coordinador"
ROL_TECNICO = "tecnico"


def grupo_de(estado: str) -> str:
    return ESTADO_GRUPO.get((estado or "").strip().upper(), "INICIAL")


def _norm(s: str) -> str:
    """Normaliza para comparar nombres de técnico: sin acentos, mayúsculas, sin espacios extra."""
    s = unicodedata.normalize("NFD", s or "")
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return s.upper().strip()
