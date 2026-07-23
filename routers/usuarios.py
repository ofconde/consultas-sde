"""Gestión de la propia cuenta — cambio de contraseña."""
from fastapi import APIRouter, Depends, HTTPException, Body
from sqlalchemy import text

from db import engine
from auth import require_login, autenticar, hash_password

router = APIRouter(prefix="/api/usuarios", tags=["usuarios"])


@router.post("/password")
def cambiar_password(actual: str = Body(...), nueva: str = Body(...),
                      usuario=Depends(require_login)):
    if not autenticar(usuario["username"], actual):
        raise HTTPException(400, "La contraseña actual es incorrecta.")
    if len(nueva) < 8:
        raise HTTPException(400, "La nueva contraseña debe tener al menos 8 caracteres.")
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE sde_usuarios SET password_hash = :p WHERE username = :u
        """), {"p": hash_password(nueva), "u": usuario["username"]})
    return {"ok": True}
