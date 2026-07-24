"""Gestión de usuarios — cambio de contraseña propia + administración (solo coordinador)."""
import logging
from fastapi import APIRouter, Depends, HTTPException, Body
from sqlalchemy import text

from db import engine
from auth import require_login, require_coordinador, autenticar, hash_password
from constantes import ROL_COORDINADOR, ROL_TECNICO

router = APIRouter(prefix="/api/usuarios", tags=["usuarios"])
log = logging.getLogger("consultas_sde.usuarios")


@router.post("/password")
def cambiar_password(actual: str = Body(...), nueva: str = Body(...),
                      usuario=Depends(require_login)):
    """Cada usuario cambia su propia contraseña (necesita la actual)."""
    if not autenticar(usuario["username"], actual):
        raise HTTPException(400, "La contraseña actual es incorrecta.")
    if len(nueva) < 6:
        raise HTTPException(400, "La nueva contraseña debe tener al menos 6 caracteres.")
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE sde_usuarios SET password_hash = :p WHERE username = :u
        """), {"p": hash_password(nueva), "u": usuario["username"]})
    return {"ok": True}


@router.get("")
def listar_usuarios(_=Depends(require_coordinador)):
    """Lista de usuarios del sistema — solo coordinador."""
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT username, nombre, rol, activo FROM sde_usuarios ORDER BY username
        """)).mappings().all()
    return [dict(r) for r in rows]


@router.post("")
def crear_usuario(username: str = Body(...), nombre: str = Body(...),
                   rol: str = Body(default=ROL_TECNICO), password: str = Body(...),
                   admin=Depends(require_coordinador)):
    """Alta de un usuario nuevo — por defecto técnico, salvo que se indique
    explícitamente coordinador (admin). Solo coordinador puede dar de alta."""
    username = username.strip().lower()
    nombre = nombre.strip()
    if not username or not nombre:
        raise HTTPException(422, "Usuario y nombre son obligatorios.")
    if not rol:
        rol = ROL_TECNICO
    if rol not in (ROL_COORDINADOR, ROL_TECNICO):
        raise HTTPException(422, "Rol inválido.")
    if len(password) < 6:
        raise HTTPException(422, "La contraseña debe tener al menos 6 caracteres.")
    with engine.begin() as conn:
        existe = conn.execute(text("SELECT 1 FROM sde_usuarios WHERE username = :u"),
                              {"u": username}).scalar()
        if existe:
            raise HTTPException(409, "Ya existe un usuario con ese nombre de usuario.")
        conn.execute(text("""
            INSERT INTO sde_usuarios (username, nombre, password_hash, rol)
            VALUES (:u, :n, :p, :r)
        """), {"u": username, "n": nombre, "p": hash_password(password), "r": rol})
    log.info("Usuario creado: %s (%s) por %s", username, rol, admin["username"])
    return {"ok": True}


@router.post("/{username}/resetear-password")
def resetear_password(username: str, nueva: str = Body(..., embed=True),
                       admin=Depends(require_coordinador)):
    """El coordinador blanquea la contraseña de cualquier usuario, sin necesitar la actual."""
    if len(nueva) < 6:
        raise HTTPException(422, "La contraseña debe tener al menos 6 caracteres.")
    with engine.begin() as conn:
        r = conn.execute(text("""
            UPDATE sde_usuarios SET password_hash = :p WHERE username = :u RETURNING username
        """), {"p": hash_password(nueva), "u": username}).first()
    if not r:
        raise HTTPException(404, "Usuario no encontrado.")
    log.info("Password reseteada: %s por %s", username, admin["username"])
    return {"ok": True}


@router.patch("/{username}")
def editar_usuario(username: str, activo: bool = Body(..., embed=True),
                    admin=Depends(require_coordinador)):
    """Activa/desactiva un usuario (ej. técnico que ya no trabaja más acá)."""
    with engine.begin() as conn:
        r = conn.execute(text("""
            UPDATE sde_usuarios SET activo = :a WHERE username = :u RETURNING username
        """), {"a": activo, "u": username}).first()
    if not r:
        raise HTTPException(404, "Usuario no encontrado.")
    log.info("Usuario %s: %s por %s", "activado" if activo else "desactivado",
             username, admin["username"])
    return {"ok": True}
