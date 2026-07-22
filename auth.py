"""Autenticación — usuarios, hash de password, sesión por cookie firmada y guards de rol.

Sesión simple: cookie firmada (itsdangerous) con el username. Dos roles:
- coordinador: ve y edita todo, asigna técnicos, ve el informe, importa.
- tecnico: gestiona consultas y carga acciones.
"""
import os
import bcrypt
from itsdangerous import URLSafeSerializer, BadSignature
from fastapi import Request, HTTPException, Depends
from sqlalchemy import text

from db import engine
from constantes import ROL_COORDINADOR, ROL_TECNICO

_SECRET = os.environ.get("SECRET_KEY", "dev-secret-cambiar")
_serializer = URLSafeSerializer(_SECRET, salt="sde-session")
COOKIE_NAME = "sde_session"


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
        data = _serializer.loads(token)
        return data.get("u")
    except BadSignature:
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
    if not row or not row["activo"]:
        return None
    if not verificar_password(password, row["password_hash"]):
        return None
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
