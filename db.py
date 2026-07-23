"""Base de datos — engine, esquema (idempotente) y seed de catálogos.

DB PostgreSQL dedicada de Santiago del Estero. Todo el esquema se crea con
CREATE TABLE IF NOT EXISTS al arrancar, así el deploy en Railway se autoconfigura.
"""
import os
import json
import pathlib
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()

_RAW_URL = os.environ.get("DATABASE_URL", "")
# Railway a veces entrega postgres:// ; SQLAlchemy quiere postgresql://
if _RAW_URL.startswith("postgres://"):
    _RAW_URL = _RAW_URL.replace("postgres://", "postgresql://", 1)

# Fallback local para desarrollo sin Postgres arriba
if not _RAW_URL:
    _RAW_URL = "postgresql://localhost:5432/consultas_sde"

engine = create_engine(_RAW_URL, pool_pre_ping=True, pool_recycle=1800)

_SEED_PATH = pathlib.Path(__file__).parent / "catalogos_seed.json"


def init_db():
    """Crea las tablas si no existen y siembra los catálogos."""
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS sde_usuarios (
                id            SERIAL PRIMARY KEY,
                username      TEXT UNIQUE NOT NULL,
                nombre        TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                rol           TEXT NOT NULL DEFAULT 'tecnico',
                activo        BOOLEAN DEFAULT TRUE,
                created_at    TIMESTAMP DEFAULT NOW()
            )
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS sde_consultas (
                id                   SERIAL PRIMARY KEY,
                codigo               TEXT UNIQUE NOT NULL,
                fuente               TEXT DEFAULT 'Formulario',
                fecha_recepcion      TIMESTAMP,
                -- datos del solicitante (del formulario)
                nombre               TEXT,
                cuit                 TEXT,
                situacion_arca       TEXT,
                telefono             TEXT,
                mail                 TEXT,
                localidad            TEXT,
                actividad_economica  TEXT,
                sector               TEXT,
                monto                BIGINT,
                destino              TEXT,
                como_se_entero       TEXT,
                -- gestión (confirma el técnico)
                tecnico              TEXT,
                departamento         TEXT,
                localidad_confirmada TEXT,
                garantia             TEXT,
                linea                TEXT,
                programa             TEXT,
                arca_confirmado      TEXT,
                monto_confirmado     BIGINT,
                actividad_inscripta  TEXT,
                situacion_bcra       TEXT,
                estado               TEXT DEFAULT 'CONSULTA INICIAL',
                observaciones        TEXT,
                informacion_extra    TEXT,
                genero               TEXT,
                created_at           TIMESTAMP DEFAULT NOW(),
                updated_at           TIMESTAMP DEFAULT NOW()
            )
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS sde_acciones (
                id          SERIAL PRIMARY KEY,
                consulta_id INT NOT NULL REFERENCES sde_consultas(id) ON DELETE CASCADE,
                fecha       DATE,
                accion      TEXT NOT NULL,
                detalle     TEXT DEFAULT '',
                creado_por  TEXT DEFAULT '',
                created_at  TIMESTAMP DEFAULT NOW()
            )
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_sde_acciones_consulta
            ON sde_acciones (consulta_id)
        """))

        # Índices sobre las columnas más filtradas/ordenadas del panel. Baratos de
        # crear ahora (tabla chica); preparan el crecimiento futuro sin costo hoy.
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_sde_consultas_estado ON sde_consultas (estado)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_sde_consultas_tecnico ON sde_consultas (tecnico)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_sde_consultas_cuit ON sde_consultas (cuit)"))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_sde_consultas_fecha_recepcion
            ON sde_consultas (fecha_recepcion)
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS sde_catalogos (
                id     SERIAL PRIMARY KEY,
                tipo   TEXT NOT NULL,
                valor  TEXT NOT NULL,
                orden  INT  DEFAULT 0,
                activo BOOLEAN DEFAULT TRUE,
                UNIQUE (tipo, valor)
            )
        """))

        # Secuencia para los códigos SDE-NNNNNN. Reemplaza un SELECT COUNT(*) que,
        # fuera de transacción, podía generar el mismo código dos veces si llegaban
        # dos altas concurrentes (ej. Power Automate reintentando un webhook).
        # setval() es idempotente: nunca retrocede, arranca desde el máximo código
        # ya existente (importante para no colisionar con las 256 filas migradas).
        conn.execute(text("CREATE SEQUENCE IF NOT EXISTS sde_consultas_codigo_seq"))
        conn.execute(text("""
            SELECT setval('sde_consultas_codigo_seq',
                GREATEST(
                    (SELECT COALESCE(MAX(CAST(SUBSTRING(codigo FROM 5) AS INT)), 0) FROM sde_consultas),
                    (SELECT last_value FROM sde_consultas_codigo_seq)
                ))
        """))

    _seed_catalogos()


def _seed_catalogos():
    """Carga los catálogos desde catalogos_seed.json (solo inserta lo que falta)."""
    if not _SEED_PATH.exists():
        return
    data = json.loads(_SEED_PATH.read_text(encoding="utf-8"))
    with engine.begin() as conn:
        for tipo, valores in data.items():
            for orden, valor in enumerate(valores, 1):
                conn.execute(text("""
                    INSERT INTO sde_catalogos (tipo, valor, orden)
                    VALUES (:tipo, :valor, :orden)
                    ON CONFLICT (tipo, valor) DO NOTHING
                """), {"tipo": tipo, "valor": valor, "orden": orden})


def catalogo(tipo: str):
    """Devuelve la lista de valores activos de un catálogo, ordenada."""
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT valor FROM sde_catalogos
            WHERE tipo = :tipo AND activo = TRUE
            ORDER BY orden, valor
        """), {"tipo": tipo}).fetchall()
    return [r[0] for r in rows]


def proximo_codigo():
    """Genera el próximo código correlativo SDE-000001, atómico entre conexiones
    concurrentes (usa la secuencia de Postgres, no un SELECT COUNT(*) fuera de
    transacción)."""
    with engine.begin() as conn:
        n = conn.execute(text("SELECT nextval('sde_consultas_codigo_seq')")).scalar()
    return f"SDE-{n:06d}"


def cuits_duplicados(conn):
    """CUITs (no vacíos) que aparecen en más de una consulta. Función compartida
    entre routers/consultas.py (marcar filas DUP) y routers/informe.py (contador)."""
    rows = conn.execute(text("""
        SELECT cuit FROM sde_consultas
        WHERE cuit IS NOT NULL AND cuit <> ''
        GROUP BY cuit HAVING COUNT(*) > 1
    """)).fetchall()
    return {r[0] for r in rows}
