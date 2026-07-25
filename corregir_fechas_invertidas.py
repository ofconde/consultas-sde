"""Corrige las fechas de acción que quedaron con día y mes invertidos (RUN-ONCE).

Origen del problema: en el Excel del que se migró, las fechas que el técnico tipeaba a
mano las interpretaba Excel como MM/DD/YYYY (formato de EE.UU.) en lugar de DD/MM/YYYY.
Cuando el día era <= 12 la invertía en silencio; cuando era > 12 no podía y dejaba la
celda como texto — por eso las dañadas tienen todas día <= 12. La migración copió los
valores tal cual, así que el error viajó al sistema.

Criterio para corregir (conservador): solo se toca una acción cuando la fecha guardada
es INCOHERENTE (anterior a la recepción de su consulta, o futura) y la invertida SÍ es
coherente. Si ambas son posibles, no se toca: no hay forma de saber cuál quiso poner.

Solo se corrigen consultas recibidas desde `--desde` (por defecto 01/06/2026, decidido con
Omar): el período anterior no se toca, es histórico que ya nadie va a revisar y donde el
criterio de "qué quiso poner el técnico" es menos confiable.

Uso:
    DATABASE_URL=postgresql://... python corregir_fechas_invertidas.py            # simulacro
    DATABASE_URL=postgresql://... python corregir_fechas_invertidas.py --aplicar
    DATABASE_URL=postgresql://... python corregir_fechas_invertidas.py --desde 2026-01-01
"""
import sys
from datetime import date, datetime

from sqlalchemy import text

from db import engine

DESDE_DEFECTO = date(2026, 6, 1)


def _invertir(f):
    """Cambia día por mes. None si no da una fecha válida (ej. día 29 no es un mes)."""
    try:
        return date(f.year, f.day, f.month)
    except ValueError:
        return None


def main(aplicar, desde):
    hoy = date.today()
    with engine.begin() as conn:
        filas = conn.execute(text("""
            SELECT a.id, a.fecha, a.accion, c.codigo, c.fecha_recepcion::date AS recep
            FROM sde_acciones a
            JOIN sde_consultas c ON c.id = a.consulta_id
            WHERE a.fecha IS NOT NULL AND c.fecha_recepcion IS NOT NULL
            ORDER BY c.codigo
        """)).mappings().all()

        corregir, ambiguas, irrecuperables, fuera_de_rango = [], 0, [], []
        for r in filas:
            f, recep = r["fecha"], r["recep"]
            inv = _invertir(f)
            actual_ok = recep <= f <= hoy
            inv_ok = inv is not None and recep <= inv <= hoy
            if actual_ok:
                if inv_ok and inv != f:
                    ambiguas += 1          # las dos sirven: no se toca
                continue
            if recep < desde:
                fuera_de_rango.append(r)   # anterior al corte: no se toca
                continue
            if inv_ok:
                corregir.append((r, inv))
            else:
                irrecuperables.append(r)

        print(f"Acciones con fecha analizadas: {len(filas)}")
        print(f"Corte: solo consultas recibidas desde {desde.strftime('%d-%m-%Y')}\n")
        print(f"  a corregir                    : {len(corregir)}")
        print(f"  ambiguas (no se tocan)        : {ambiguas}")
        print(f"  anteriores al corte (se dejan): {len(fuera_de_rango)}")
        print(f"  sin arreglo evidente          : {len(irrecuperables)}")

        if fuera_de_rango:
            print("\n  Quedan sin corregir por ser anteriores al corte:")
            for r in fuera_de_rango:
                print(f"    {r['codigo']}  guardada={r['fecha']}  recepción={r['recep']}")

        if irrecuperables:
            print("\n  Sin arreglo evidente (revisar a mano):")
            for r in irrecuperables:
                print(f"    {r['codigo']}  guardada={r['fecha']}  recepción={r['recep']}  {r['accion'][:34]}")

        if not aplicar:
            print("\n  Primeras a corregir (guardada -> corregida | recepción):")
            for r, inv in corregir[:12]:
                print(f"    {r['codigo']}  {r['fecha']} -> {inv}  | {r['recep']}  {r['accion'][:30]}")
            print("\nSIMULACRO — no se escribió nada. Correr con --aplicar para confirmar.")
            conn.rollback()
            return

        if corregir:
            conn.execute(
                text("UPDATE sde_acciones SET fecha = :f WHERE id = :id"),
                [{"f": inv, "id": r["id"]} for r, inv in corregir],
            )
        print(f"\n✓ Corregidas {len(corregir)} fechas de acción.")


if __name__ == "__main__":
    desde = DESDE_DEFECTO
    if "--desde" in sys.argv:
        desde = datetime.strptime(sys.argv[sys.argv.index("--desde") + 1], "%Y-%m-%d").date()
    main("--aplicar" in sys.argv, desde)
