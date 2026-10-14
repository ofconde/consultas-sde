"""Listado de créditos aprobados que entregó la gobernación, cruzado en vivo con
la consulta real de cada expediente (estado de carpeta, monto del sistema,
técnico) y con el detalle del estado que se va cargando a mano.

El listado en sí (nro, proyecto, monto, línea, fecha de firma) es el de la nota
de la gobernación y no se edita; lo que se edita es `detalle_estado`, que vive en
la consulta y se guarda con el PATCH genérico de /api/consultas/{id}.
"""
import re
from datetime import datetime
from io import BytesIO
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import Response
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from sqlalchemy import text

from db import engine
from auth import require_login
from formatos import _monto, _dmy
from constantes import ESTADOS_CARPETA, ESTADO_CARPETA_COLOR, SITUACIONES_TRAMITE, SITUACION_TRAMITE_COLOR

router = APIRouter(prefix="/api/listado-gobernacion", tags=["gobernacion"])


@router.get("")
def listar(usuario=Depends(require_login)):
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT g.nro, g.solicitante, g.proyecto, g.monto, g.linea, g.fecha_firma, g.nota, g.anulado, g.omitir_informe, g.motivo_inversion, g.contacto_nombre,
                   c.id AS consulta_id, c.codigo, c.tecnico, c.instancia_superior,
                   c.estado_carpeta, c.detalle_estado, c.situacion_tramite, c.firmado,
                   COALESCE(NULLIF(c.monto_instancia_superior, 0), NULLIF(c.monto_confirmado, 0), c.monto) AS monto_sistema
            FROM sde_listado_gobernacion g
            LEFT JOIN sde_consultas c ON c.id = g.consulta_id
            ORDER BY g.nro
        """)).mappings().all()

    casos = []
    for r in rows:
        sistema = int(r["monto_sistema"] or 0) if r["consulta_id"] else None
        casos.append({
            "nro": r["nro"], "solicitante": r["solicitante"], "proyecto": r["proyecto"] or "",
            "monto": r["monto"], "monto_fmt": _monto(r["monto"]),
            "linea": r["linea"] or "—",
            "fecha_firma": _dmy(r["fecha_firma"]) if r["fecha_firma"] else "—",
            "nota": r["nota"] or "",
            "anulado": bool(r["anulado"]),
            "omitir_informe": bool(r["omitir_informe"]),
            "motivo_inversion": r["motivo_inversion"] or "",
            "contacto_nombre": r["contacto_nombre"] or "",
            "consulta_id": r["consulta_id"], "codigo": r["codigo"],
            "tecnico": r["tecnico"] or "Sin asignar" if r["consulta_id"] else "—",
            "en_instancia_superior": bool(r["instancia_superior"]),
            "estado_carpeta": r["estado_carpeta"] or "",
            "detalle_estado": r["detalle_estado"] or "",
            "situacion_tramite": r["situacion_tramite"] or "",
            "monto_sistema": sistema,
            "monto_sistema_fmt": _monto(sistema) if sistema is not None else "—",
            "monto_difiere": sistema is not None and r["monto"] is not None and sistema != r["monto"],
        })
    return {
        "total": len(casos),
        "monto_total_fmt": _monto(sum(c["monto"] or 0 for c in casos)),
        "estados": ESTADOS_CARPETA,
        "estado_carpeta_color": ESTADO_CARPETA_COLOR,
        "situaciones": SITUACIONES_TRAMITE,
        "situacion_color": SITUACION_TRAMITE_COLOR,
        "casos": casos,
    }


# Estados que usa la gobernación (los que definió el área superior): todo lo que
# está firmado y en trámite —incluido CFI Sede y lo recién enviado a firma— se
# muestra como un solo estado. Los estados de salida conservan su nombre.
_FIRMADO = "FIRMADO - EN TRAMITE PARA INGRESAR A CFI (DOCUMENTACION O SGR)"
_AGRUPA_EN_FIRMADO = {"EN TRAMITE EN CFI SEDE", "ENVIADO A FIRMA DE REPRESENTANTE"}
_ORDEN = ["DESEMBOLSADO", "EN TRAMITE DE DESEMBOLSO", _FIRMADO,
          "DESISTIDO POR SOLICITANTE", "RECHAZADO POR ANALISIS DE RIESGO", ""]
_ROTULO = {"DESEMBOLSADO": "Desembolsado", "EN TRAMITE DE DESEMBOLSO": "En trámite de desembolso",
           _FIRMADO: "Firmado - En trámite de CFI",
           "DESISTIDO POR SOLICITANTE": "Desistido por el solicitante",
           "RECHAZADO POR ANALISIS DE RIESGO": "Rechazado por análisis de riesgo", "": "Sin clasificar"}
_FONDO = {"DESEMBOLSADO": "E3F5EA", "EN TRAMITE DE DESEMBOLSO": "EAF7F0", _FIRMADO: "FBF3DC"}


def _grupo(estado):
    return _FIRMADO if estado in _AGRUPA_EN_FIRMADO else (estado or "")


@router.get("/excel")
def exportar_excel(usuario=Depends(require_login)):
    """Excel para entregar a la gobernación: mismos expedientes que el informe
    imprimible (sin los reemplazados ni los omitidos), con el estado, su detalle
    y los datos de contacto para llamar al solicitante. Sin técnicos."""
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT g.nro, g.solicitante, g.contacto_nombre, g.monto, g.motivo_inversion, c.estado_carpeta, c.detalle_estado,
                   COALESCE(c.telefono, '') AS telefono, COALESCE(c.mail, '') AS mail
            FROM sde_listado_gobernacion g
            LEFT JOIN sde_consultas c ON c.id = g.consulta_id
            WHERE NOT COALESCE(g.anulado, FALSE) AND NOT COALESCE(g.omitir_informe, FALSE)
        """)).mappings().all()
    rows = sorted(rows, key=lambda r: (_ORDEN.index(_grupo(r["estado_carpeta"])) if _grupo(r["estado_carpeta"]) in _ORDEN else len(_ORDEN), r["nro"]))

    wb = Workbook()
    ws = wb.active
    ws.title = "Créditos aprobados"
    fuente = "Arial"
    encabezado = ["N°", "Solicitante", "Persona de contacto", "Teléfono", "Mail", "Monto", "Motivo de la inversión", "Estado", "Detalle del estado"]
    ws.append(encabezado)
    for i in range(1, len(encabezado) + 1):
        c = ws.cell(1, i)
        c.font = Font(name=fuente, bold=True, color="FFFFFF", size=10)
        c.fill = PatternFill("solid", fgColor="1C2443")
        c.alignment = Alignment(vertical="center", wrap_text=True)
    borde = Border(*(Side(style="thin", color="D9DCE3"),) * 4)
    for n, r in enumerate(rows, start=2):
        est = _grupo(r["estado_carpeta"])
        detalle = (r["detalle_estado"] or "").strip()
        if re.match(r"^desembolsad[oa]s?\.?$", detalle, re.I):
            detalle = ""  # repite el estado: no aporta
        ws.append([r["nro"], r["solicitante"], r["contacto_nombre"] or "", (r["telefono"] or "").strip(),
                   (r["mail"] or "").strip(), r["monto"], r["motivo_inversion"] or "", _ROTULO.get(est, est), detalle])
        for i in range(1, 10):
            x = ws.cell(n, i)
            x.font = Font(name=fuente, size=10)
            x.border = borde
            x.alignment = Alignment(vertical="top", wrap_text=i in (2, 3, 5, 7, 8, 9))
        if est in _FONDO:
            ws.cell(n, 8).fill = PatternFill("solid", fgColor=_FONDO[est])
        ws.cell(n, 4).number_format = "@"
        ws.cell(n, 6).number_format = "$#,##0"
    ultima = len(rows) + 1
    total = ultima + 1
    ws.cell(total, 1, f"=COUNTA(B2:B{ultima})")
    ws.cell(total, 2, "TOTAL")
    ws.cell(total, 6, f"=SUM(F2:F{ultima})")
    ws.cell(total, 6).number_format = "$#,##0"
    for i in range(1, 10):
        x = ws.cell(total, i)
        x.font = Font(name=fuente, bold=True, size=10)
        x.fill = PatternFill("solid", fgColor="EEF1F7")
        x.border = borde
    for col, ancho in zip("ABCDEFGHI", [6, 34, 24, 20, 36, 16, 48, 34, 60]):
        ws.column_dimensions[col].width = ancho
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:I{ultima}"

    # Hoja "Resumen" (como la armó el área superior): cantidad y monto por estado,
    # con fórmulas sobre la hoja de datos para que se mantenga al corregir algo.
    rs = wb.create_sheet("Resumen", 0)
    rs.append(["Estado", "Cantidad", "Suma de Monto"])
    estados = [e for e in _ORDEN if any(_grupo(r["estado_carpeta"]) == e for r in rows)]
    for k, e in enumerate(estados, start=2):
        rotulo = _ROTULO[e]
        rs.cell(k, 1, rotulo)
        rs.cell(k, 2, f"=COUNTIF('Créditos aprobados'!$H$2:$H${ultima},A{k})")
        rs.cell(k, 3, f"=SUMIF('Créditos aprobados'!$H$2:$H${ultima},A{k},'Créditos aprobados'!$F$2:$F${ultima})")
    fin = len(estados) + 2
    rs.cell(fin, 1, "Total general")
    rs.cell(fin, 2, f"=SUM(B2:B{fin - 1})")
    rs.cell(fin, 3, f"=SUM(C2:C{fin - 1})")
    for fila in rs.iter_rows(min_row=1, max_row=fin, max_col=3):
        for x in fila:
            x.font = Font(name=fuente, size=10, bold=(x.row in (1, fin)))
            x.border = borde
            if x.row == 1:
                x.font = Font(name=fuente, size=10, bold=True, color="FFFFFF")
                x.fill = PatternFill("solid", fgColor="1C2443")
            if x.column == 3 and x.row > 1:
                x.number_format = "$#,##0"
    for col, ancho in zip("ABC", [38, 12, 20]):
        rs.column_dimensions[col].width = ancho

    buf = BytesIO()
    wb.save(buf)
    hoy = datetime.now(ZoneInfo("America/Argentina/Buenos_Aires")).strftime("%d-%m-%Y")
    nombre = f"Creditos aprobados - estado y contactos ({hoy}).xlsx"
    return Response(
        buf.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{nombre}"'},
    )


@router.patch("/{nro}")
def editar_contacto(nro: int, body: dict = Body(...), usuario=Depends(require_login)):
    """Persona de contacto del renglón (el nombre de quien atiende cuando el
    solicitante es una empresa). Vacío la borra."""
    contacto = (body.get("contacto_nombre") or "").strip()[:200]
    with engine.begin() as conn:
        r = conn.execute(text("""
            UPDATE sde_listado_gobernacion SET contacto_nombre = :c WHERE nro = :n RETURNING id
        """), {"c": contacto or None, "n": nro}).first()
    if not r:
        raise HTTPException(404, "Renglón no encontrado")
    return {"ok": True}
