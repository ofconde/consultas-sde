"""Auditoría general del sistema.

Tres cosas viven acá:
1. Bajas de consultas (ya existía) — snapshot antes de un DELETE físico.
2. Registro de acciones — quién hizo qué método/ruta y cuándo (todo lo que
   cambia datos: POST/PUT/PATCH/DELETE). Se alimenta desde el middleware de
   app.py, más el login/logout que se registran explícitos porque pasan
   antes de que exista la cookie de sesión.
3. Actividad diaria por usuario — primera y última acción del día, para
   estimar cuánto usó el sistema. No es tiempo de conexión real (HTTP no
   tiene sesión persistente): es el tramo entre su primera y su última
   acción registrada ese día. Se rotula así en la UI, sin prometer precisión
   de reloj de conexión que no existe.
"""
from datetime import datetime, date
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Request
from sqlalchemy import text

from db import engine
from auth import require_coordinador
from formatos import _hora_local, _dmy, TZ_AR

router = APIRouter(prefix="/api/auditoria", tags=["auditoria"])

# Métodos que se consideran "una acción" (cambian datos). GET no se audita acá
# — la actividad diaria (abajo) ya cubre "el usuario estuvo usando el sistema"
# sin inundar la tabla con cada vista de página.
_METODOS_AUDITADOS = {"POST", "PUT", "PATCH", "DELETE"}


def registrar_accion(usuario: str, metodo: str, ruta: str, status_code=None, ip=None):
    """Inserta una fila en sde_auditoria. Nunca debe tirar — un fallo acá no
    puede voltear el request real que se está auditando."""
    try:
        with engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO sde_auditoria (usuario, metodo, ruta, status_code, ip)
                VALUES (:u, :m, :r, :s, :ip)
            """), {"u": usuario or "?", "m": metodo, "r": ruta, "s": status_code, "ip": ip})
    except Exception:
        pass


def registrar_actividad(usuario: str, metodo: str, ruta: str, status_code=None, ip=None):
    """Actualiza sde_actividad_diaria (siempre) y, si el método es mutante,
    también registra la acción en sde_auditoria. Punto único que llama tanto
    el middleware de requests autenticados como login/logout explícitos."""
    ahora = datetime.utcnow()
    # Agrupar por el día calendario en hora ARGENTINA, no UTC: entre las 21hs y
    # medianoche local ya es "mañana" en UTC, y una acción de las 22hs quedaría
    # separada en el día siguiente si se agrupara por fecha UTC (mismo bug ya
    # visto con otros timestamps del sistema, ver memoria del proyecto).
    hoy = ahora.replace(tzinfo=ZoneInfo("UTC")).astimezone(TZ_AR).date()
    try:
        with engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO sde_actividad_diaria (usuario, fecha, primera_actividad, ultima_actividad, cantidad_requests)
                VALUES (:u, :f, :a, :a, 1)
                ON CONFLICT (usuario, fecha) DO UPDATE SET
                    ultima_actividad = EXCLUDED.ultima_actividad,
                    cantidad_requests = sde_actividad_diaria.cantidad_requests + 1
            """), {"u": usuario or "?", "f": hoy, "a": ahora})
    except Exception:
        pass
    if metodo in _METODOS_AUDITADOS or metodo in ("LOGIN", "LOGOUT", "LOGIN_FALLIDO"):
        registrar_accion(usuario, metodo, ruta, status_code, ip)


@router.get("/bajas")
def listar_bajas(usuario=Depends(require_coordinador)):
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT id, consulta_id, codigo, snapshot, eliminado_por, eliminado_en
            FROM sde_consultas_bajas ORDER BY eliminado_en DESC
        """)).mappings().all()
    return {"total": len(rows), "bajas": [{
        "id": r["id"],
        "consulta_id": r["consulta_id"],
        "codigo": r["codigo"],
        "nombre": r["snapshot"].get("nombre"),
        "cuit": r["snapshot"].get("cuit"),
        "estado": r["snapshot"].get("estado"),
        "tecnico": r["snapshot"].get("tecnico"),
        "eliminado_por": r["eliminado_por"],
        "eliminado_en": _hora_local(r["eliminado_en"]),
    } for r in rows]}


@router.get("/acciones")
def listar_acciones(
    usuario_filtro: str = "", metodo: str = "", desde: str = "", hasta: str = "",
    limit: int = 200, offset: int = 0,
    usuario=Depends(require_coordinador),
):
    """Registro de acciones (POST/PUT/PATCH/DELETE + login/logout), más reciente
    primero. Filtros opcionales por usuario, método y rango de fecha (DATE)."""
    where = ["1=1"]
    params = {"limit": min(limit, 500), "offset": max(offset, 0)}
    if usuario_filtro:
        where.append("usuario = :u")
        params["u"] = usuario_filtro
    if metodo:
        where.append("metodo = :m")
        params["m"] = metodo
    # Filtro de fecha en hora local (AR), no UTC — mismo criterio que
    # sde_actividad_diaria.fecha, para no mostrar una acción de las 22hs
    # locales bajo el día siguiente.
    if desde:
        where.append("(creado_en AT TIME ZONE 'UTC' AT TIME ZONE 'America/Argentina/Cordoba')::date >= :desde")
        params["desde"] = desde
    if hasta:
        where.append("(creado_en AT TIME ZONE 'UTC' AT TIME ZONE 'America/Argentina/Cordoba')::date <= :hasta")
        params["hasta"] = hasta
    sql = f"""
        SELECT id, usuario, metodo, ruta, status_code, ip, creado_en
        FROM sde_auditoria WHERE {" AND ".join(where)}
        ORDER BY creado_en DESC LIMIT :limit OFFSET :offset
    """
    with engine.connect() as conn:
        rows = conn.execute(text(sql), params).mappings().all()
        total = conn.execute(text(f"SELECT COUNT(*) FROM sde_auditoria WHERE {' AND '.join(where)}"), params).scalar()
    return {
        "total": total,
        "acciones": [{
            "id": r["id"], "usuario": r["usuario"], "metodo": r["metodo"], "ruta": r["ruta"],
            "status_code": r["status_code"], "ip": r["ip"], "cuando": _hora_local(r["creado_en"]),
        } for r in rows],
    }


@router.get("/conexiones")
def listar_conexiones(desde: str = "", hasta: str = "", usuario_filtro: str = "", usuario=Depends(require_coordinador)):
    """Actividad diaria por usuario: primera/última acción del día y cuántas
    acciones registró. La 'duración' es ultima - primera, mostrada como
    aproximación de uso — no es tiempo de conexión TCP real."""
    where = ["1=1"]
    params = {}
    if desde:
        where.append("fecha >= :desde")
        params["desde"] = desde
    if hasta:
        where.append("fecha <= :hasta")
        params["hasta"] = hasta
    if usuario_filtro:
        where.append("usuario = :u")
        params["u"] = usuario_filtro
    sql = f"""
        SELECT usuario, fecha, primera_actividad, ultima_actividad, cantidad_requests
        FROM sde_actividad_diaria WHERE {" AND ".join(where)}
        ORDER BY fecha DESC, usuario
    """
    with engine.connect() as conn:
        rows = conn.execute(text(sql), params).mappings().all()
    out = []
    for r in rows:
        dur = r["ultima_actividad"] - r["primera_actividad"]
        mins = int(dur.total_seconds() // 60)
        out.append({
            "usuario": r["usuario"],
            "fecha": _dmy(r["fecha"]),
            "primera_actividad": _hora_local(r["primera_actividad"]),
            "ultima_actividad": _hora_local(r["ultima_actividad"]),
            "duracion": f"{mins // 60}h {mins % 60:02d}m" if mins > 0 else "< 1m",
            "cantidad_requests": r["cantidad_requests"],
        })
    return {"total": len(out), "conexiones": out}


@router.get("/usuarios-conocidos")
def usuarios_conocidos(usuario=Depends(require_coordinador)):
    """Lista de usuarios que aparecen en la auditoría, para poblar el filtro
    sin depender de /api/usuarios (que podría no traer bajas históricas)."""
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT DISTINCT usuario FROM sde_actividad_diaria ORDER BY usuario"
        )).fetchall()
    return [r[0] for r in rows]
