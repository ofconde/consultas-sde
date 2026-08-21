"""Clasificación de texto libre del "destino del financiamiento" en categorías
de motivo de inversión (maquinaria, obra civil, capital de trabajo, etc.).

Nace del informe especial de agro (21/08/2026): Omar pidió ver de qué se trata
la inversión, no solo el monto. Queda acá — no en routers/informe.py — porque
es lógica de negocio reutilizable por cualquier informe que la necesite, no
solo el institucional.
"""
import unicodedata


def _sin_acentos(s):
    s = unicodedata.normalize("NFD", s or "")
    return "".join(c for c in s if unicodedata.category(c) != "Mn").lower()


# Primera categoría que matchea, en este orden de prioridad — un motivo que
# menciona "maquinaria y obra civil" cae en Maquinaria, que suele ser el
# componente principal cuando aparecen juntos.
CATEGORIAS_MOTIVO = [
    ("Maquinaria / equipamiento", ["maquinari", "equipo", "equipamiento", "tractor",
     "cosechadora", "sembradora", "enrolladora", "herramienta", "implemento",
     "segadora", "acondicionadora", "sistema de riego"]),
    ("Animales / hacienda", ["animal", "ganado", "vacuno", "reproductor", "vientre",
     "cria de", "terneros", "vacas", "caprino", "porcino", "aves", "colmena"]),
    ("Obra civil / infraestructura", ["obra civil", "ampliaci", "construcci", "galpon",
     "instalacion", "silo"]),
    ("Vehículo / rodado", ["vehiculo", "rodado", "camioneta", "camion", "utilitario",
     "trailer"]),
    ("Riego", ["riego"]),
    ("Tecnología", ["dron", "tecnolog", "panel solar", "energia solar", "gps"]),
    ("Capital de trabajo / insumos", ["capital de trabajo", "insumo", "materia prima"]),
]

ORDEN_CATEGORIAS = [c for c, _ in CATEGORIAS_MOTIVO] + ["Otro", "Sin dato"]


def clasificar_motivo(texto: str) -> str:
    if not texto or not texto.strip():
        return "Sin dato"
    t = _sin_acentos(texto)
    for etiqueta, palabras in CATEGORIAS_MOTIVO:
        if any(p in t for p in palabras):
            return etiqueta
    return "Otro"
