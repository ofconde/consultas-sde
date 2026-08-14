"""API de consultas — listar/filtrar, detalle, editar gestión, alta manual, baja."""
import logging
from fastapi import APIRouter, Depends, HTTPException, Request, Body
from sqlalchemy import text

from db import engine, proximo_codigo, cuits_duplicados
from auth import require_login, require_coordinador, puede_editar
from models import GestionIn, ConsultaManualIn, BulkGestionIn, BulkAccionIn
from formatos import _dmy, _monto, _hora_local, _parse_monto, _parse_fecha
from constantes import (grupo_de, _norm, ROL_COORDINADOR, GRUPOS_ACTIVOS, TOPE_LINEA, excede_tope,
                         ACCION_NO_FINANCIABLE, ESTADO_NO_FINANCIABLE, TOPES_FIANZA_TERCERO)
import genero as genero_mod

router = APIRouter(prefix="/api/consultas", tags=["consultas"])
log = logging.getLogger("consultas_sde.consultas")

# columnas de gestión que puede editar el técnico
_GESTION_COLS = [
    "tecnico", "departamento", "localidad_confirmada", "garantia", "linea",
    "programa", "arca_confirmado", "actividad_inscripta", "situacion_bcra",
    "estado", "observaciones", "informacion_extra", "genero",
]

# datos que carga el propio solicitante en el formulario público — a diferencia
# de _GESTION_COLS, solo el coordinador los puede corregir (ver editar_gestion):
# un técnico no debería poder cambiar el nombre o el CUIT de un solicitante.
_SOLICITANTE_COLS = [
    "nombre", "cuit", "telefono", "mail", "localidad", "actividad_economica",
    "sector", "destino", "como_se_entero", "situacion_arca",
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


def _condicion_fianza_tercero(alias="c"):
    """Condición SQL (+ params) para candidatos a gestión por fianza de tercero:
    monotributo A/B/C con el monto EFECTIVO por debajo del tope que esa
    categoría puede cubrir con fianza (TOPES_FIANZA_TERCERO en constantes.py).
    Compartida entre `listar()` (el filtro del panel) y `resumen()` (el contador
    del botón) para no repetir el mismo SQL dos veces."""
    p = f"{alias}." if alias else ""
    ors, params = [], {}
    for i, (cat, tope) in enumerate(TOPES_FIANZA_TERCERO.items()):
        ors.append(f"(COALESCE(NULLIF({p}arca_confirmado, ''), {p}situacion_arca) = :fz_arca_{i} "
                   f"AND COALESCE(NULLIF({p}monto_confirmado, 0), {p}monto) <= :fz_tope_{i})")
        params[f"fz_arca_{i}"] = cat
        params[f"fz_tope_{i}"] = tope
    return "(" + " OR ".join(ors) + ")", params


def _fila_resumen(r):
    return {
        "id": r["id"],
        "codigo": r["codigo"],
        "fecha_recepcion": _dmy(r["fecha_recepcion"]),
        "nombre": r["nombre"],
        "cuit": r["cuit"],
        "mail": r["mail"],
        "situacion_arca": r["arca_confirmado"] or r["situacion_arca"],
        "arca_confirmado": r["arca_confirmado"],
        "localidad": r["localidad_confirmada"] or r["localidad"],
        "departamento": r["departamento"],
        "sector": r["sector"],
        "monto": _monto(r["monto_confirmado"] or r["monto"]),
        "monto_num": r["monto_confirmado"] or r["monto"] or 0,
        # El formulario público no valida el monto: una carga de más pasa derecho.
        # Se marca en el listado para que el técnico lo revise, no se corrige solo.
        "monto_excede": excede_tope(r["monto_confirmado"] or r["monto"]),
        "linea": r["linea"],
        "programa": r["programa"],
        "destino": r["destino"],
        "actividad_economica": r["actividad_economica"],
        "tecnico": r["tecnico"],
        "estado": r["estado"],
        "grupo": grupo_de(r["estado"]),
        "n_acciones": r["n_acciones"],
        "ultima_accion": r["ultima_accion"],
        "ultima_accion_fecha": _dmy(r["ultima_accion_fecha"]),
        # 'F'/'M'/None — heurística para priorizar candidatas a la línea Mujeres,
        # no un dato confirmado (ver genero.py). Si el campo genero llegara a
        # cargarse alguna vez a mano, ese manda por sobre la estimación.
        "genero_estimado": (
            "F" if (r["genero"] or "").strip().upper().startswith("F")
            else "M" if (r["genero"] or "").strip().upper().startswith("M")
            else genero_mod.estimar_genero(r["nombre"], r["cuit"])
        ),
    }


@router.get("")
def listar(request: Request, estado: str = "", tecnico: str = "",
           grupo: str = "", q: str = "", mios: bool = False, dups: bool = False,
           departamento: str = "", linea: str = "", programa: str = "", sector: str = "",
           situacion_arca: str = "", tipo_accion: str = "", sin_acciones: bool = False,
           fecha: str = "", genero: str = "", monto_excedido: bool = False,
           apartadas: bool = False, fianza_tercero: bool = False, usuario=Depends(require_login)):
    """Lista consultas con filtros. `q` busca en todos los campos de texto (nombre,
    CUIT, código, mail, teléfono, localidad, destino, observaciones, etc. — ver
    _CAMPOS_BUSQUEDA), así una consulta se encuentra sin saber en qué campo puntual
    quedó el dato que se recuerda.
    `mios=1` filtra las asignadas al técnico logueado (match sin acentos/mayúsculas).
    `dups=1` muestra solo consultas con CUIT duplicado, agrupadas por CUIT.
    `tipo_accion` filtra por el tipo de la ÚLTIMA acción registrada (no cualquiera del
    historial). `sin_acciones=1` filtra las que todavía no tienen ninguna acción cargada.
    `fecha` (YYYY-MM-DD) filtra por día de recepción — para ir revisando/depurando
    la base día por día.
    `apartadas=1` muestra SOLO las NO ES FINANCIABLE. Sin este flag, esas consultas
    quedan fuera del listado por defecto (pidió Omar que no ensucien la vista diaria) —
    salvo que se las pida explícitamente con `estado=NO ES FINANCIABLE` (para no romper
    el botón de estado ya existente ni una URL vieja que las tuviera filtradas), o que
    haya una búsqueda de texto (`q`): encontrar por nombre/CUIT/mail/teléfono es una
    búsqueda puntual, no el barrido diario — esconder un resultado que sí matchea solo
    porque el caso quedó marcado como no financiable es más confuso que mostrarlo (con
    su badge de estado bien visible, así se nota igual que está apartada).
    `fianza_tercero=1` filtra candidatos a gestión por fianza de tercero (monotributo
    A/B/C con el monto dentro de lo que esa categoría puede cubrir — ver
    `_condicion_fianza_tercero`)."""
    where = ["1=1"]
    params = {}
    if apartadas:
        where.append("c.estado = :estado_apartada")
        params["estado_apartada"] = "NO ES FINANCIABLE"
    else:
        if estado:
            where.append("c.estado = :estado"); params["estado"] = estado
        if estado != "NO ES FINANCIABLE" and not q:
            where.append("c.estado IS DISTINCT FROM :estado_oculto")
            params["estado_oculto"] = "NO ES FINANCIABLE"
    if fianza_tercero:
        cond, p2 = _condicion_fianza_tercero()
        where.append(cond); params.update(p2)
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
    if monto_excedido:
        # mismo monto efectivo que se muestra en la columna Monto del panel
        where.append("COALESCE(NULLIF(c.monto_confirmado, 0), c.monto) > :tope")
        params["tope"] = TOPE_LINEA
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
    if genero:
        data = [d for d in data if d["genero_estimado"] == genero]
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
        montos_excedidos = conn.execute(text("""
            SELECT COUNT(*) FROM sde_consultas
            WHERE COALESCE(NULLIF(monto_confirmado, 0), monto) > :tope
        """), {"tope": TOPE_LINEA}).scalar() or 0
        cond_fz, params_fz = _condicion_fianza_tercero(alias="")
        fianza_tercero = conn.execute(text(f"""
            SELECT COUNT(*) FROM sde_consultas WHERE {cond_fz}
        """), params_fz).scalar() or 0
        sectores = conn.execute(text("""
            SELECT DISTINCT sector FROM sde_consultas
            WHERE sector IS NOT NULL AND sector <> '' ORDER BY sector
        """)).all()
        # candidatas a la línea Mujeres — mismo cálculo que el filtro del panel
        # (ver genero.py), acá solo para el contador del botón.
        personas = conn.execute(text("SELECT nombre, cuit, genero FROM sde_consultas")).all()

    estados_out = [{"estado": r["estado"], "n": r["n"], "grupo": grupo_de(r["estado"])} for r in por_estado]
    activas = sum(r["n"] for r in estados_out if r["grupo"] in GRUPOS_ACTIVOS)
    posibles_mujeres = sum(
        1 for nombre, cuit, g in personas
        if (g or "").strip().upper().startswith("F")
        or (not g and genero_mod.estimar_genero(nombre, cuit) == "F")
    )

    return {
        "total": total, "activas": activas, "sin_asignar": sin_asignar,
        "sin_acciones": sin_acciones, "nuevas_semana": nuevas_semana, "duplicados": duplicados,
        "posibles_mujeres": posibles_mujeres,
        "montos_excedidos": montos_excedidos,
        "tope_linea_fmt": _monto(TOPE_LINEA),
        "fianza_tercero": fianza_tercero,
        "estados": estados_out,
        "sectores": [{"clave": r[0]} for r in sectores],
    }


def _filas_permitidas(conn, usuario, ids):
    """Trae {id: tecnico} de los ids pedidos y separa cuáles puede tocar este
    usuario (misma regla que la edición fila por fila: coordinador todo, técnico
    solo lo suyo o sin asignar). Devuelve (ids_permitidos, ids_omitidos) — un
    técnico que selecciona casos de otro no rompe el lote entero, esas filas
    simplemente se listan como omitidas para que el panel se lo muestre."""
    filas = conn.execute(text("SELECT id, tecnico FROM sde_consultas WHERE id = ANY(:ids)"),
                          {"ids": ids}).mappings().all()
    encontrados = {r["id"]: r["tecnico"] for r in filas}
    permitidos = [i for i, tec in encontrados.items() if puede_editar(usuario, tec)]
    omitidos = [i for i in ids if i not in encontrados or i not in permitidos]
    return permitidos, omitidos


# IMPORTANTE: estas dos rutas van antes de "/{cid}" — si quedaran después,
# "bulk" haría match del path pattern de {cid} (que es int) y fallaría con 422
# en vez de llegar acá (mismo motivo por el que "/resumen" ya está antes).
@router.patch("/bulk")
def editar_gestion_bulk(body: BulkGestionIn, usuario=Depends(require_login)):
    """Asigna técnico y/o estado a un lote de consultas de una sola vez — para
    cuando entran varios casos parecidos juntos (ej. N consultas sin ARCA activo
    que se descartan todas con el mismo estado) y cargarlos de a uno es ruido."""
    campos = {}
    if body.tecnico is not None:
        campos["tecnico"] = body.tecnico
    if body.estado is not None:
        campos["estado"] = body.estado
    if body.arca_confirmado is not None:
        campos["arca_confirmado"] = body.arca_confirmado
    if body.linea is not None:
        campos["linea"] = body.linea
    if body.programa is not None:
        campos["programa"] = body.programa
    if not campos:
        raise HTTPException(422, "Nada para actualizar")
    # Reasignar a otro técnico sigue siendo privilegio del coordinador — mismo
    # criterio que editar_gestion(), acá aplicado a todo el lote de una vez
    # (el valor de "tecnico" es el mismo para las N filas, no hay por-fila).
    if usuario["rol"] != ROL_COORDINADOR and "tecnico" in campos:
        if _norm(campos["tecnico"]) != _norm(usuario["nombre"]):
            campos.pop("tecnico")
    if not campos:
        raise HTTPException(403, "Un técnico solo puede autoasignarse consultas")

    with engine.begin() as conn:
        aplicar_ids, omitidos = _filas_permitidas(conn, usuario, body.ids)
        if aplicar_ids:
            sets = ", ".join(f"{c} = :{c}" for c in campos)
            conn.execute(text(f"""
                UPDATE sde_consultas SET {sets}, updated_at = NOW() WHERE id = ANY(:ids)
            """), {**campos, "ids": aplicar_ids})
    log.info("Edición en lote: ids=%s campos=%s por=%s", aplicar_ids, list(campos), usuario["username"])
    return {"ok": True, "aplicados": len(aplicar_ids), "omitidos": omitidos}


@router.post("/bulk/acciones")
def crear_accion_bulk(body: BulkAccionIn, usuario=Depends(require_login)):
    """Carga la misma acción de seguimiento en un lote de consultas de una sola vez."""
    if not body.accion or not body.accion.strip():
        raise HTTPException(422, "La acción es obligatoria")
    fecha = _parse_fecha(body.fecha)
    detalle = (body.detalle or "").strip()
    accion = body.accion.strip()
    with engine.begin() as conn:
        aplicar_ids, omitidos = _filas_permitidas(conn, usuario, body.ids)
        for cid in aplicar_ids:
            conn.execute(text("""
                INSERT INTO sde_acciones (consulta_id, fecha, accion, detalle, creado_por)
                VALUES (:cid, :fecha, :accion, :detalle, :por)
            """), {"cid": cid, "fecha": fecha, "accion": accion,
                   "detalle": detalle, "por": usuario["nombre"]})
        # Mismo criterio que el alta individual (routers/acciones.py): esta acción
        # puntual sincroniza el estado sola, para que la consulta no siga colgada
        # en el listado normal por depender de que alguien toque el campo aparte.
        if accion.upper() == ACCION_NO_FINANCIABLE and aplicar_ids:
            conn.execute(text("""
                UPDATE sde_consultas SET estado = :estado, updated_at = NOW()
                WHERE id = ANY(:ids) AND estado IS DISTINCT FROM :estado
            """), {"ids": aplicar_ids, "estado": ESTADO_NO_FINANCIABLE})
    log.info("Acción en lote: ids=%s accion=%s por=%s", aplicar_ids, accion, usuario["username"])
    return {"ok": True, "aplicados": len(aplicar_ids), "omitidos": omitidos}


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
    # Los datos que llegaron con el formulario (nombre, CUIT, teléfono, actividad,
    # destino, etc.) los puede corregir SOLO el coordinador — un técnico gestiona
    # una consulta, pero no debería poder cambiar la identidad del solicitante.
    if set(campos) & set(_SOLICITANTE_COLS) and usuario["rol"] != ROL_COORDINADOR:
        raise HTTPException(403, "Solo el coordinador puede editar los datos del solicitante")
    # El monto solicitado lo carga el propio interesado en el formulario público y a
    # veces llega con errores gruesos (se detectaron cargas de $150.000.000.000 contra
    # un límite de crédito de $500 M). Corregirlo se habilita a cualquier usuario,
    # esté la consulta asignada a quien esté — pero solo si es lo único que se toca.
    if set(campos) != {"monto"} and not puede_editar(usuario, actual["tecnico"]):
        raise HTTPException(403, "Esta consulta está asignada a otro técnico")
    if usuario["rol"] != ROL_COORDINADOR and "tecnico" in campos:
        # Un técnico puede auto-asignarse una consulta (tomar un caso sin asignar,
        # o simplemente reconfirmar que es suyo al guardar el resto de la gestión).
        # Lo que no puede es reasignarla a un técnico DISTINTO — eso sigue siendo
        # privilegio del coordinador. Antes se descartaba el campo entero sin
        # avisar, así que un técnico que se autoasignaba un caso guardaba bien el
        # resto del formulario pero la asignación nunca quedaba, en silencio.
        if _norm(campos["tecnico"]) != _norm(usuario["nombre"]):
            campos.pop("tecnico")
    sets, params = [], {"id": cid}
    for col in _GESTION_COLS + _SOLICITANTE_COLS:
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
