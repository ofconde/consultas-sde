"""API de consultas — listar/filtrar, detalle, editar gestión, alta manual, baja."""
import logging
from fastapi import APIRouter, Depends, HTTPException, Request, Body
from sqlalchemy import text

from db import engine, proximo_codigo, cuits_duplicados
from auth import require_login, require_coordinador, puede_editar
from models import GestionIn, ConsultaManualIn
from formatos import _dmy, _monto, _hora_local, _parse_monto, _parse_fecha
from constantes import grupo_de, _norm, ROL_COORDINADOR, GRUPOS_ACTIVOS

router = APIRouter(prefix="/api/consultas", tags=["consultas"])
log = logging.getLogger("consultas_sde.consultas")

# columnas de gestión que puede editar el técnico
_GESTION_COLS = [
    "tecnico", "departamento", "localidad_confirmada", "garantia", "linea",
    "programa", "arca_confirmado", "actividad_inscripta", "situacion_bcra",
    "estado", "observaciones", "informacion_extra", "genero",
]

# columnas de texto donde busca el buscador del panel — todos los campos de
# texto libre o identificador de la consulta, del solicitante y de la gestión.
_CAMPOS_BUSQUEDA = [
    "codigo", "nombre", "cuit", "mail", "telefono",
    "localidad", "localidad_confirmada", "destino", "actividad_economica",
    "como_se_entero", "observaciones", "informacion_extra",
    "departamento", "sector", "linea", "programa", "garantia",
    "tecnico", "estado", "situacion_arca", "arca_confirmado", "situacion_bcra",
]


def _fila_resumen(r):
    return {
        "id": r["id"],
        "codigo": r["codigo"],
        "fecha_recepcion": _dmy(r["fecha_recepcion"]),
        "nombre": r["nombre"],
        "cuit": r["cuit"],
        "situacion_arca": r["arca_confirmado"] or r["situacion_arca"],
        "arca_confirmado": r["arca_confirmado"],
        "localidad": r["localidad_confirmada"] or r["localidad"],
        "departamento": r["departamento"],
        "sector": r["sector"],
        "monto": _monto(r["monto_confirmado"] or r["monto"]),
        "monto_num": r["monto_confirmado"] or r["monto"] or 0,
        "linea": r["linea"],
        "programa": r["programa"],
        "destino": r["destino"],
        "tecnico": r["tecnico"],
        "estado": r["estado"],
        "grupo": grupo_de(r["estado"]),
        "n_acciones": r["n_acciones"],
        "ultima_accion": r["ultima_accion"],
        "ultima_accion_fecha": _dmy(r["ultima_accion_fecha"]),
    }


@router.get("")
def listar(request: Request, estado: str = "", tecnico: str = "",
           grupo: str = "", q: str = "", mios: bool = False, dups: bool = False,
           departamento: str = "", linea: str = "", programa: str = "", sector: str = "",
           situacion_arca: str = "", tipo_accion: str = "", sin_acciones: bool = False,
           fecha: str = "", usuario=Depends(require_login)):
    """Lista consultas con filtros. `q` busca en todos los campos de texto (nombre,
    CUIT, código, mail, teléfono, localidad, destino, observaciones, etc. — ver
    _CAMPOS_BUSQUEDA), así una consulta se encuentra sin saber en qué campo puntual
    quedó el dato que se recuerda.
    `mios=1` filtra las asignadas al técnico logueado (match sin acentos/mayúsculas).
    `dups=1` muestra solo consultas con CUIT duplicado, agrupadas por CUIT.
    `tipo_accion` filtra por el tipo de la ÚLTIMA acción registrada (no cualquiera del
    historial). `sin_acciones=1` filtra las que todavía no tienen ninguna acción cargada.
    `fecha` (YYYY-MM-DD) filtra por día de recepción — para ir revisando/depurando
    la base día por día."""
    where = ["1=1"]
    params = {}
    if estado:
        where.append("c.estado = :estado"); params["estado"] = estado
    if fecha:
        where.append("c.fecha_recepcion::date = :fecha"); params["fecha"] = fecha
    if tecnico:
        if tecnico == "__sin__":
            where.append("(c.tecnico IS NULL OR c.tecnico = '')")
        else:
            where.append("c.tecnico = :tecnico"); params["tecnico"] = tecnico
    if q:
        where.append("(" + " OR ".join(f"c.{col} ILIKE :q" for col in _CAMPOS_BUSQUEDA) + ")")
        params["q"] = f"%{q}%"
    if departamento:
        where.append("c.departamento = :departamento"); params["departamento"] = departamento
    if linea:
        where.append("c.linea = :linea"); params["linea"] = linea
    if programa:
        where.append("c.programa = :programa"); params["programa"] = programa
    if sector:
        where.append("c.sector = :sector"); params["sector"] = sector
    if situacion_arca:
        # mismo criterio que se muestra en el panel: gestión confirmada si existe,
        # si no lo que declaró el solicitante.
        where.append("COALESCE(NULLIF(c.arca_confirmado, ''), c.situacion_arca) = :situacion_arca")
        params["situacion_arca"] = situacion_arca
    if sin_acciones:
        where.append("NOT EXISTS (SELECT 1 FROM sde_acciones a WHERE a.consulta_id = c.id)")
    if tipo_accion:
        where.append("ua.accion = :tipo_accion"); params["tipo_accion"] = tipo_accion

    with engine.connect() as conn:
        dup_cuits = cuits_duplicados(conn)
        if dups:
            if not dup_cuits:
                return {"total": 0, "consultas": []}
            where.append("c.cuit = ANY(:dcuits)"); params["dcuits"] = list(dup_cuits)
            orden = "c.cuit, c.id"
        else:
            orden = "c.fecha_recepcion DESC NULLS LAST, c.id DESC"

        rows = conn.execute(text(f"""
            SELECT c.*,
                   (SELECT COUNT(*) FROM sde_acciones a WHERE a.consulta_id = c.id) AS n_acciones,
                   ua.accion AS ultima_accion, ua.fecha AS ultima_accion_fecha
            FROM sde_consultas c
            LEFT JOIN LATERAL (
                SELECT accion, fecha FROM sde_acciones a2
                WHERE a2.consulta_id = c.id
                ORDER BY a2.fecha DESC NULLS LAST, a2.id DESC
                LIMIT 1
            ) ua ON true
            WHERE {' AND '.join(where)}
            ORDER BY {orden}
        """), params).mappings().all()

    data = [_fila_resumen(r) for r in rows]
    for d in data:
        d["es_duplicado"] = d["cuit"] in dup_cuits
    if grupo:
        data = [d for d in data if d["grupo"] == grupo]
    if mios:
        yo = _norm(usuario["nombre"])
        data = [d for d in data if _norm(d["tecnico"]) == yo]
    return {"total": len(data), "consultas": data}


@router.get("/resumen")
def resumen(_=Depends(require_login)):
    """KPIs operativos para el header del panel — accesible a cualquier usuario
    logueado (a diferencia de /api/informe, que es el informe de gestión de los
    viernes y es solo para el coordinador)."""
    with engine.connect() as conn:
        total = conn.execute(text("SELECT COUNT(*) FROM sde_consultas")).scalar() or 0
        por_estado = conn.execute(text("""
            SELECT COALESCE(estado, 'SIN ESTADO') AS estado, COUNT(*) AS n
            FROM sde_consultas GROUP BY estado ORDER BY n DESC
        """)).mappings().all()
        sin_asignar = conn.execute(text("""
            SELECT COUNT(*) FROM sde_consultas WHERE tecnico IS NULL OR tecnico = ''
        """)).scalar() or 0
        sin_acciones = conn.execute(text("""
            SELECT COUNT(*) FROM sde_consultas c
            WHERE NOT EXISTS (SELECT 1 FROM sde_acciones a WHERE a.consulta_id = c.id)
        """)).scalar() or 0
        nuevas_semana = conn.execute(text("""
            SELECT COUNT(*) FROM sde_consultas WHERE fecha_recepcion >= NOW() - INTERVAL '7 days'
        """)).scalar() or 0
        dup_cuits = cuits_duplicados(conn)
        duplicados = 0
        if dup_cuits:
            duplicados = conn.execute(text("""
                SELECT COUNT(*) FROM sde_consultas WHERE cuit = ANY(:dcuits)
            """), {"dcuits": list(dup_cuits)}).scalar() or 0
        sectores = conn.execute(text("""
            SELECT DISTINCT sector FROM sde_consultas
            WHERE sector IS NOT NULL AND sector <> '' ORDER BY sector
        """)).all()

    estados_out = [{"estado": r["estado"], "n": r["n"], "grupo": grupo_de(r["estado"])} for r in por_estado]
    activas = sum(r["n"] for r in estados_out if r["grupo"] in GRUPOS_ACTIVOS)

    return {
        "total": total, "activas": activas, "sin_asignar": sin_asignar,
        "sin_acciones": sin_acciones, "nuevas_semana": nuevas_semana, "duplicados": duplicados,
        "estados": estados_out,
        "sectores": [{"clave": r[0]} for r in sectores],
    }


@router.post("")
def crear_manual(payload: ConsultaManualIn, usuario=Depends(require_login)):
    """Alta manual de una consulta que llegó por un medio distinto al formulario
    (llamada, presencial, mail directo). Sin anti-duplicado automático: si genera
    un CUIT repetido, la vista de "Duplicados" del panel ya lo va a marcar."""
    nombre = payload.nombre.strip()
    if not nombre:
        raise HTTPException(422, "El nombre es obligatorio")
    fecha = _parse_fecha(payload.fecha_recepcion)
    with engine.begin() as conn:
        codigo = proximo_codigo()
        r = conn.execute(text("""
            INSERT INTO sde_consultas
                (codigo, fuente, fecha_recepcion, nombre, cuit, situacion_arca,
                 telefono, mail, localidad, actividad_economica, sector, monto,
                 destino, como_se_entero, genero, tecnico, departamento,
                 localidad_confirmada, garantia, linea, programa, arca_confirmado,
                 monto_confirmado, actividad_inscripta, situacion_bcra, estado,
                 observaciones, informacion_extra)
            VALUES
                (:codigo, 'Manual', :fecha, :nombre, :cuit, :arca,
                 :tel, :mail, :localidad, :actividad, :sector, :monto,
                 :destino, :como, :genero, :tecnico, :departamento,
                 :localidad_confirmada, :garantia, :linea, :programa, :arca_confirmado,
                 :monto_confirmado, :actividad_inscripta, :situacion_bcra, :estado,
                 :observaciones, :informacion_extra)
            RETURNING id
        """), {
            "codigo": codigo, "fecha": fecha, "nombre": nombre,
            "cuit": payload.cuit, "arca": payload.situacion_arca, "tel": payload.telefono,
            "mail": payload.mail, "localidad": payload.localidad,
            "actividad": payload.actividad_economica, "sector": payload.sector,
            "monto": _parse_monto(payload.monto), "destino": payload.destino,
            "como": payload.como_se_entero, "genero": payload.genero,
            "tecnico": payload.tecnico, "departamento": payload.departamento,
            "localidad_confirmada": payload.localidad_confirmada, "garantia": payload.garantia,
            "linea": payload.linea, "programa": payload.programa,
            "arca_confirmado": payload.arca_confirmado,
            "monto_confirmado": _parse_monto(payload.monto_confirmado),
            "actividad_inscripta": payload.actividad_inscripta,
            "situacion_bcra": payload.situacion_bcra,
            "estado": (payload.estado or "CONSULTA INICIAL"),
            "observaciones": payload.observaciones, "informacion_extra": payload.informacion_extra,
        }).first()
    log.info("Alta manual: codigo=%s id=%s por=%s", codigo, r[0], usuario["username"])
    return {"ok": True, "codigo": codigo, "id": r[0]}


@router.get("/{cid}")
def detalle(cid: int, _=Depends(require_login)):
    with engine.connect() as conn:
        c = conn.execute(text("SELECT * FROM sde_consultas WHERE id = :id"),
                         {"id": cid}).mappings().first()
        if not c:
            raise HTTPException(404, "Consulta no encontrada")
        acc = conn.execute(text("""
            SELECT * FROM sde_acciones WHERE consulta_id = :id
            ORDER BY fecha DESC NULLS LAST, id DESC
        """), {"id": cid}).mappings().all()

    consulta = dict(c)
    consulta["fecha_recepcion_fmt"] = _dmy(c["fecha_recepcion"])
    consulta["monto_fmt"] = _monto(c["monto"])
    consulta["monto_confirmado_fmt"] = _monto(c["monto_confirmado"])
    consulta["grupo"] = grupo_de(c["estado"])
    acciones = [{
        "id": a["id"], "fecha": _dmy(a["fecha"]), "accion": a["accion"],
        "detalle": a["detalle"], "creado_por": a["creado_por"],
        "created_at": _hora_local(a["created_at"]),
    } for a in acc]
    return {"consulta": consulta, "acciones": acciones}


@router.patch("/{cid}")
def editar_gestion(cid: int, body: GestionIn, usuario=Depends(require_login)):
    with engine.connect() as conn:
        actual = conn.execute(text("SELECT tecnico FROM sde_consultas WHERE id = :id"),
                              {"id": cid}).mappings().first()
    if not actual:
        raise HTTPException(404, "Consulta no encontrada")

    campos = body.model_dump(exclude_none=True)
    # El monto solicitado lo carga el propio interesado en el formulario público y a
    # veces llega con errores gruesos (se detectaron cargas de $150.000.000.000 contra
    # un límite de crédito de $500 M). Corregirlo se habilita a cualquier usuario,
    # esté la consulta asignada a quien esté — pero solo si es lo único que se toca.
    if set(campos) != {"monto"} and not puede_editar(usuario, actual["tecnico"]):
        raise HTTPException(403, "Esta consulta está asignada a otro técnico")
    if usuario["rol"] != ROL_COORDINADOR:
        # solo el coordinador reasigna: un técnico no cambia el campo `tecnico`
        campos.pop("tecnico", None)
    sets, params = [], {"id": cid}
    for col in _GESTION_COLS:
        if col in campos:
            sets.append(f"{col} = :{col}"); params[col] = campos[col]
    if "monto_confirmado" in campos:
        sets.append("monto_confirmado = :monto_confirmado")
        params["monto_confirmado"] = _parse_monto(campos["monto_confirmado"])
    if "monto" in campos:
        sets.append("monto = :monto")
        params["monto"] = _parse_monto(campos["monto"])
    if not sets:
        return {"ok": True, "sin_cambios": True}
    sets.append("updated_at = NOW()")
    with engine.begin() as conn:
        r = conn.execute(text(f"""
            UPDATE sde_consultas SET {', '.join(sets)} WHERE id = :id RETURNING id, codigo
        """), params).first()
    if not r:
        raise HTTPException(404, "Consulta no encontrada")
    if "monto" in params:
        # corregir un monto cambia un dato que vino del solicitante: queda registrado
        log.info("Monto corregido: codigo=%s nuevo=%s por=%s",
                 r[1], params["monto"], usuario["username"])
    return {"ok": True}


@router.delete("/{cid}")
def eliminar(cid: int, usuario=Depends(require_coordinador)):
    with engine.begin() as conn:
        r = conn.execute(text("DELETE FROM sde_consultas WHERE id = :id RETURNING id, codigo"),
                         {"id": cid}).first()
    if not r:
        raise HTTPException(404, "Consulta no encontrada")
    log.info("Consulta eliminada: id=%s codigo=%s por=%s", cid, r[1], usuario["username"])
    return {"ok": True}
