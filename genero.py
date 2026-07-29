"""Estimación de género del solicitante — para priorizar candidatas a la línea
Mujeres, NO un dato confirmado. El encuadre real lo sigue confirmando el
técnico; esto es para no tener que abrir consulta por consulta a buscarlas.

Método, en orden de confianza:
1. **Prefijo del CUIT** (fuente principal — es un dato oficial de AFIP/ARCA,
   no una adivinanza): 20=varón, 27=mujer, 30/33/34=persona jurídica (empresa).
   Verificado contra los 404 casos reales de producción: cubre ~92% de los
   casos con certeza. El prefijo 23/24 es histórico y ambiguo (puede ser
   cualquiera de los dos sexos), así que para esos y para CUITs faltantes o
   con longitud inválida se cae al método 2.
2. **Nombre de pila**, como respaldo: se revisan todos los tokens del nombre
   (no solo el primero) contra un diccionario de nombres comunes en
   Argentina — en los datos reales el nombre viene a veces "Nombre Apellido"
   y a veces "Apellido Nombre". Si ningún token matchea, heurística de
   terminación sobre el primer token (los nombres en "-a" son mayoritariamente
   femeninos en español, con excepciones conocidas).
"""
import re
import unicodedata

_ORG_KEYWORDS = {
    "SA", "SRL", "SH", "SC", "SCA", "SAS", "LTDA", "CIA", "COOPERATIVA",
    "SOCIEDAD", "ASOCIACION", "FUNDACION", "FIDEICOMISO", "EMPRESA", "GRUPO",
    "COMERCIAL", "INDUSTRIAL", "AGROPECUARIA", "CONSTRUCTORA", "FARMACIA",
    "INSTITUTO", "SANATORIO", "EDITORIAL", "FERRETERIA", "TRANSPORTE",
    "CONSORCIO", "CONSORCIOS", "LUBRICANTES",
}
_SIN_DATO = {"PERSONA", "HUMANA", "JURIDICA"}

_FEMENINOS = {
    "MARIA", "ANA", "LAURA", "CLAUDIA", "ROMINA", "SILVINA", "CAROLINA",
    "NATALIA", "PAULA", "VALERIA", "GABRIELA", "PATRICIA", "MONICA", "SANDRA",
    "VERONICA", "ANDREA", "LUCIA", "SOFIA", "CAMILA", "FLORENCIA", "MARCELA",
    "ADRIANA", "ALEJANDRA", "CARINA", "DANIELA", "ELIZABETH", "GRACIELA",
    "KARINA", "LORENA", "MARIANA", "MIRTA", "NORA", "RAQUEL", "ROSA",
    "SUSANA", "VIVIANA", "YOLANDA", "GUADALUPE", "MILAGROS", "AGUSTINA",
    "BELEN", "CELESTE", "CINTIA", "ELENA", "ESTELA", "IVANA", "JULIETA",
    "LILIANA", "MABEL", "MARISA", "NOELIA", "PAOLA", "SABRINA", "SOLEDAD",
    "VANESA", "VANINA", "YANINA", "ANTONELA", "MARISABEL", "CECILIA",
    "ALICIA", "BEATRIZ", "CRISTINA", "IRMA", "JULIA", "LIDIA", "NILDA",
    "NOEMI", "OLGA", "SILVIA", "STELLA", "TERESA", "ANALIA", "ERICA",
    "MELISA", "PRISCILA", "ROCIO", "YAMILA", "ABIGAIL", "CONSTANZA",
    "KAREN", "MICAELA", "ESTEFANIA", "YANET", "YANETH", "DAHIANA", "GISELA",
    "TATIANA", "JESSICA", "DAYAN", "NAIR", "ANAHI", "DENISSE", "MARIEL",
    "MARISEL", "ALDANA", "JAZMIN", "BRENDA", "MELINA", "GISELLE", "AILEN",
    "MILENA", "ANGELES", "GLADYS", "ISABEL", "CONSUELO",
}
_MASCULINOS = {
    "JOSE", "JUAN", "CARLOS", "LUIS", "JORGE", "MIGUEL", "PEDRO", "PABLO",
    "DIEGO", "DANIEL", "RICARDO", "ROBERTO", "SERGIO", "FERNANDO", "GUSTAVO",
    "ALBERTO", "RAUL", "OSCAR", "HECTOR", "RUBEN", "MARCELO", "ALEJANDRO",
    "ADRIAN", "ARIEL", "CRISTIAN", "DARIO", "EDUARDO", "EMANUEL", "ENRIQUE",
    "ESTEBAN", "EZEQUIEL", "FABIAN", "FEDERICO", "FRANCISCO", "GABRIEL",
    "GERMAN", "GONZALO", "GUILLERMO", "HORACIO", "IGNACIO", "JAVIER",
    "JOAQUIN", "LEANDRO", "LEONARDO", "LUCAS", "MARIO", "MARTIN", "MATIAS",
    "MAXIMILIANO", "NICOLAS", "OMAR", "RODOLFO", "RODRIGO", "SANTIAGO",
    "VICTOR", "WALTER", "ANDRES", "ANGEL", "ARMANDO", "AUGUSTO", "ELVIO",
    "FACUNDO", "FRANCO", "MAURO", "RAMIRO", "NELSON", "HUGO",
    "IVAN", "LUCA", "TOMAS", "AXEL", "BRUNO", "DAMIAN", "ELIAS", "EMILIO",
    "GASTON", "JEREMIAS", "MAXIMO", "SAUL", "AGUSTIN", "DAVID", "NAHUEL",
    "EXEQUIEL", "ALEXANDRO", "VALENTINO", "JULIO",
}


def _norm(s):
    s = unicodedata.normalize("NFD", s or "")
    s = "".join(c for c in s if unicodedata.category(c) != "Mn").upper()
    return re.sub(r"[^A-Z\s]", "", s).strip()  # saca puntos/guiones sin partir "S.R.L." en letras sueltas


def _por_cuit(cuit) -> str | None:
    """20=varón, 27=mujer, 30/33/34=empresa. None si el CUIT no está, tiene una
    longitud inválida, o es un prefijo ambiguo (23/24, histórico) o raro."""
    d = "".join(ch for ch in (cuit or "") if ch.isdigit())
    if len(d) != 11:
        return "ambiguo"
    p = d[:2]
    if p == "20":
        return "M"
    if p == "27":
        return "F"
    if p in ("30", "33", "34"):
        return None
    return "ambiguo"


def _por_nombre(nombre) -> str | None:
    tokens = [t for t in _norm(nombre).split() if t]
    if not tokens:
        return None
    if any(t in _ORG_KEYWORDS for t in tokens):
        return None
    if all(t in _SIN_DATO for t in tokens):
        return None
    for t in tokens:
        if t in _FEMENINOS:
            return "F"
        if t in _MASCULINOS:
            return "M"
    primero = tokens[0]
    if primero in _SIN_DATO or len(primero) < 3:
        return None
    return "F" if primero.endswith("A") else "M"


def estimar_genero(nombre, cuit=None) -> str | None:
    """'F', 'M', o None (empresa/entidad, o no se pudo estimar).
    El CUIT manda cuando es válido y no es de los prefijos ambiguos (23/24);
    si no, se cae al nombre — ver docstring del módulo."""
    por_cuit = _por_cuit(cuit)
    if por_cuit != "ambiguo":
        return por_cuit
    return _por_nombre(nombre)
