# Consultas SDE — Seguimiento de consultas · UEP Santiago del Estero

Sistema web dedicado para el seguimiento de las consultas de crédito CFI que recibe la
Unidad de Enlace Provincial de Santiago del Estero. Reemplaza el Excel `NUEVAS CONSULTAS`.

- **Ingreso:** las respuestas del Microsoft Forms llegan por Power Automate (ver [POWER-AUTOMATE.md](POWER-AUTOMATE.md)).
- **Seguimiento:** cada consulta tiene estado, técnico responsable e historial de acciones ilimitado.
- **Informe:** página `/informe` con los KPIs de gestión (imprimible a PDF) para jefatura.

## Stack

FastAPI + PostgreSQL + HTML/JS servido por el propio servidor. Sin build de frontend.

## Módulos

| Archivo | Responsabilidad |
|---|---|
| `app.py` | FastAPI: rutas de páginas, login/logout, montaje de routers, startup |
| `db.py` | Engine, esquema (CREATE TABLE IF NOT EXISTS), seed de catálogos |
| `auth.py` | Usuarios, hash bcrypt, sesión por cookie firmada, guards de rol |
| `models.py` | Contratos Pydantic de la API |
| `formatos.py` | Fechas DD-MM-YYYY, montos ARS, hora local AR |
| `constantes.py` | Mapa estado→grupo, roles |
| `routers/` | `consultas`, `acciones`, `ingesta`, `informe`, `catalogos` |
| `templates/` | `login`, `panel`, `detalle`, `informe` |
| `importar_excel.py` | Migración RUN-ONCE del Excel a la base |
| `catalogos_seed.json` | Catálogos (departamento, localidad, estado, acción, …) extraídos del Excel |

## Correr localmente

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # completar DATABASE_URL, SDE_API_KEY, SECRET_KEY
uvicorn app:app --reload --port 8099
```

Abrir http://127.0.0.1:8099 . Usuarios iniciales (password `123456`, **cambiar**):
`omar` (coordinador) · `victor` (tecnico).

## Migración inicial (una sola vez)

```bash
DATABASE_URL=postgresql://... python importar_excel.py "ruta/al/FORMULARIO DE CONSULTA SANTIAGO DEL ESTERO.xlsx"
```

Carga la hoja `NUEVAS CONSULTAS` (consultas + historial de acciones) con `fuente='Histórico'`.
Es idempotente: dedupe por CUIT + fecha de recepción, re-correr no duplica.

## Deploy en Railway

1. Crear un servicio nuevo desde este repo + un PostgreSQL dedicado.
2. Variables de entorno: `DATABASE_URL` (la del Postgres del proyecto), `SDE_API_KEY`, `SECRET_KEY`.
3. Railway usa el `Procfile` (`uvicorn app:app`). El esquema y los catálogos se crean solos al arrancar.
4. Correr la migración una vez (desde local apuntando `DATABASE_URL` al Postgres de Railway, o por consola SSH).

## Roles

- **coordinador** (Omar): ve y edita todo, ve el informe, puede eliminar consultas.
- **tecnico** (Víctor, …): gestiona consultas y carga acciones.

## Formatos (regla CFI)

Fechas DD-MM-YYYY · montos "mil M / M" · timestamps guardados en UTC y mostrados en hora AR.
