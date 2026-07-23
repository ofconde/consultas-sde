"""Autenticación — usuarios, hash de password, sesión por cookie firmada y guards de rol.

Sesión simple: cookie firmada (itsdangerous) con el username, con expiración validada
server-side (no solo en la cookie del navegador). Dos roles:
- coordinador: ve y edita todo, asigna técnicos, ve el informe, importa.
- tecnico: gestiona consultas y carga acciones, solo las suyas (ver puede_editar()).
"""
import os
import time
import logging
import bcrypt
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from fastapi import Request, HTTPException, Depends
from sqlalchemy import text

from db import engine
from constantes import ROL_COORDINADOR, ROL_TECNICO, _norm

log = logging.getLogger("consultas_sde.auth")

_SECRET = os.environ.get("SECRET_KEY", "dev-secret-cambiar")
_serializer = URLSafeTimedSerializer(_SECRET, salt="sde-session")
COOKIE_NAME = "sde_session"
SESSION_MAX_AGE = 60 * 60 * 12  # 12 horas, validado server-side en cada request

# Rate limiting de login en memoria: {username: [timestamps de intentos fallidos]}.
# Alcanza para un server de un solo proceso (Railway); se resetea si el proceso reinicia.
_INTENTOS_FALLIDOS = {}
_MAX_INTENTOS = 5
_VENTANA_SEGUNDOS = 5 * 60


def rate_limit_excedido(username: str) -> bool:
    ahora = time.time()
    key = (username or "").strip().lower()
    intentos = [t for t in _INTENTOS_FALLIDOS.get(key, []) if ahora - t < _VENTANA_SEGUNDOS]
    _INTENTOS_FALLIDOS[key] = intentos
    return len(intentos) >= _MAX_INTENTOS


def registrar_intento_fallido(username: str):
    key = (username or "").strip().lower()
    _INTENTOS_FALLIDOS.setdefault(key, []).append(time.time())


def limpiar_intentos(username: str):
    _INTENTOS_FALLIDOS.pop((username or "").strip().lower(), None)


def hash_password(plano: str) -> str:
    # bcrypt trunca a 72 bytes; lo hacemos explícito para evitar el error
    return bcrypt.hashpw(plano.encode("utf-8")[:72], bcrypt.gensalt()).decode("utf-8")


def verificar_password(plano: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plano.encode("utf-8")[:72], hashed.encode("utf-8"))
    except Exception:
        return False


def crear_token(username: str) -> str:
    return _serializer.dumps({"u": username})


def leer_token(token: str):
    try:
        data = _serializer.loads(token, max_age=SESSION_MAX_AGE)
        return data.get("u")
    except (BadSignature, SignatureExpired):
        return None


def seed_usuarios():
    """Crea los usuarios iniciales si no existen. Password inicial: 123456 (cambiar)."""
    iniciales = [
        ("omar",   "Omar Conde",    ROL_COORDINADOR),
        ("victor", "Víctor Suárez", ROL_TECNICO),
    ]
    with engine.begin() as conn:
        for username, nombre, rol in iniciales:
            existe = conn.execute(
                text("SELECT 1 FROM sde_usuarios WHERE username = :u"),
                {"u": username},
            ).scalar()
            if not existe:
                conn.execute(text("""
                    INSERT INTO sde_usuarios (username, nombre, password_hash, rol)
                    VALUES (:u, :n, :p, :r)
                """), {"u": username, "n": nombre, "p": hash_password("123456"), "r": rol})


def autenticar(username: str, password: str):
    """Devuelve el dict del usuario si las credenciales son válidas, si no None."""
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT username, nombre, password_hash, rol, activo
            FROM sde_usuarios WHERE username = :u
        """), {"u": (username or "").strip().lower()}).mappings().first()
    if not row or not row["activo"] or not verificar_password(password, row["password_hash"]):
        log.info("Login fallido: username=%r", username)
        return None
    log.info("Login OK: username=%r", username)
    return {"username": row["username"], "nombre": row["nombre"], "rol": row["rol"]}


def usuario_actual(request: Request):
    """Lee la cookie de sesión y devuelve el dict del usuario, o None."""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    username = leer_token(token)
    if not username:
        return None
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT username, nombre, rol, activo
            FROM sde_usuarios WHERE username = :u
        """), {"u": username}).mappings().first()
    if not row or not row["activo"]:
        return None
    return {"username": row["username"], "nombre": row["nombre"], "rol": row["rol"]}


def require_login(request: Request):
    u = usuario_actual(request)
    if not u:
        raise HTTPException(status_code=401, detail="No autenticado")
    return u


def require_coordinador(request: Request):
    u = usuario_actual(request)
    if not u:
        raise HTTPException(status_code=401, detail="No autenticado")
    if u["rol"] != ROL_COORDINADOR:
        raise HTTPException(status_code=403, detail="Requiere rol coordinador")
    return u


def puede_editar(usuario: dict, tecnico_actual) -> bool:
    """Un coordinador edita/borra cualquier cosa. Un técnico solo si la consulta
    está sin asignar o asignada a él mismo (comparación normalizada, sin acentos)."""
    if usuario["rol"] == ROL_COORDINADOR:
        return True
    if not tecnico_actual or not str(tecnico_actual).strip():
        return True
    return _norm(tecnico_actual) == _norm(usuario["nombre"])
