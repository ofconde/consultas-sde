"""Constantes de negocio del seguimiento de consultas."""
import unicodedata

# Mapa estado → grupo (para kanban e informe). Basado en la hoja `indicadores`
# del Excel de Santiago del Estero.
ESTADO_GRUPO = {
    "CONSULTA INICIAL":                 "INICIAL",
    "COMPLETANDO DOCUMENTACION":        "EN_GESTION",
    "EN TRAMITE DE DOCUMENTACION PARA ENTRAR A CFI SEDE": "EN_GESTION",
    "EN GESTION CON SGR O FONDO":       "EN_GESTION",
    "HAY INTERÉS PERO NO SE DECIDE":    "EN_GESTION",
    "REMITIDO PARA FIRMA DE REPRESENTANTE": "EN_GESTION",
    "INGRESADO EN CFI SEDE":            "EN_SEDE",
    "EN TRAMITE DE DESEMBOLSO":         "EN_SEDE",
    "DERIVADO A FONDO DE GARANTIA CFI": "EN_SEDE",
    "DERIVADO A MERCADO DE CAPITALES":  "EN_SEDE",
    "DERIVADO A OTRA PROVINCIA":        "EN_SEDE",
    "NO ES FINANCIABLE":                "INACTIVAS",
    "DESISTE DE TOMAR EL CRÉDITO":      "INACTIVAS",
    "REPETIDO":                         "INACTIVAS",
    # Grupo propio, no EN_SEDE ni INACTIVAS: es el desenlace positivo del
    # circuito completo (plata efectivamente entregada), no un paso
    # intermedio en sede ni un caso que se descartó. Mezclarlo con
    # cualquiera de los otros le ocultaría a jefatura cuántos casos
    # terminaron realmente desembolsados.
    "DESEMBOLSADO":                     "DESEMBOLSADAS",
}

# Orden y etiquetas de los grupos para el informe/kanban. "Analizadas — no
# continúan" (antes "Inactivas"): son casos que SÍ se trabajaron (se evaluaron
# y no eran financiables, el solicitante desistió, o era repetido) — "Inactivas"
# se leía como que nadie las tocó, y en un informe que sube a jefatura eso
# transmite lo contrario de lo que pasó.
GRUPOS = [
    ("INICIAL",       "Consulta inicial"),
    ("EN_GESTION",    "En gestión"),
    ("EN_SEDE",       "En sede / derivadas"),
    ("DESEMBOLSADAS", "Desembolsadas"),
    ("INACTIVAS",     "Analizadas — no continúan"),
]

# Un grupo se considera "activo" (dentro del universo de trabajo, con
# seguimiento pendiente) si no es INACTIVAS ni DESEMBOLSADAS — un caso
# desembolsado ya terminó su circuito, igual que uno inactivo, solo que
# con el desenlace opuesto.
GRUPOS_ACTIVOS = {"INICIAL", "EN_GESTION", "EN_SEDE"}

# Color por grupo — misma paleta que .g-INICIAL/.g-EN_GESTION/.g-EN_SEDE/.g-INACTIVAS
# de static/css/panel.css. Única fuente de verdad: antes vivía duplicado como hex
# hardcodeado en el JS de panel.html y detalle.html (routers/catalogos.py lo expone).
# DESEMBOLSADAS reusa --amber de panel.css (ya existe en la paleta del sitio,
# no se inventa un color nuevo) para distinguirse del verde de EN_SEDE.
GRUPO_COLOR = {
    "INICIAL":       "#2f6fd0",
    "EN_GESTION":    "#6c4bd0",
    "EN_SEDE":       "#1a9e57",
    "DESEMBOLSADAS": "#d98a00",
    "INACTIVAS":     "#8891a8",
}

ROL_COORDINADOR = "coordinador"
ROL_TECNICO = "tecnico"

# Monto máximo financiable de las líneas de crédito CFI. No se usa para filtrar ni
# corregir nada automáticamente: el formulario público no valida el monto que declara
# el solicitante, así que una carga equivocada de más (o un pedido genuinamente fuera
# de tope) tiene que ser visible para que el técnico lo revise. Lo consumen el panel
# (marca la fila) y el informe (avisa antes de publicar un total inflado).
TOPE_LINEA = 500_000_000


def excede_tope(monto) -> bool:
    """True si el monto supera el máximo financiable. Recibe el monto EFECTIVO
    (confirmado si el técnico ya lo verificó, si no el declarado)."""
    return bool(monto) and monto > TOPE_LINEA


# Cuando se carga esta acción, el campo `estado` de la consulta se pone en
# sincronía sola (ver routers/acciones.py y crear_accion_bulk en
# routers/consultas.py). Antes eran dos campos independientes: se detectaron 40
# consultas con esta acción cargada pero el estado seguía en CONSULTA INICIAL —
# el técnico avisaba al solicitante que no era financiable y se olvidaba de
# reflejarlo en el estado, así que la consulta seguía "viva" en el panel.
ACCION_NO_FINANCIABLE = "SE INFORMA QUE NO ES FINANCIABLE"
ESTADO_NO_FINANCIABLE = "NO ES FINANCIABLE"

# Candidatos a gestión por fianza de tercero (30/07 con Omar): un monotributo
# A/B/C no puede ser analizado por una SGR, pero sí puede acceder al crédito con
# una fianza de tercero — siempre que el monto pedido entre dentro de lo que esa
# categoría puede cubrir. Por eso el filtro no es solo "monotributo A/B/C", es
# "A/B/C Y el monto efectivo por debajo de su propio tope" — un monotributo B
# que pide $50 M no es candidato, la fianza tampoco le va a alcanzar.
TOPES_FIANZA_TERCERO = {
    "MONOTRIBUTO A": 6_000_000,
    "MONOTRIBUTO B": 8_000_000,
    "MONOTRIBUTO C": 10_000_000,
}


def grupo_de(estado: str) -> str:
    return ESTADO_GRUPO.get((estado or "").strip().upper(), "INICIAL")


def _norm(s: str) -> str:
    """Normaliza para comparar nombres de técnico: sin acentos, mayúsculas, sin espacios extra."""
    s = unicodedata.normalize("NFD", s or "")
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return s.upper().strip()
