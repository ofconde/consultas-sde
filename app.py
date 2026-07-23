"""Sistema de Seguimiento de Consultas — UEP Santiago del Estero.

App FastAPI dedicada. Sirve el frontend y la API. Startup: crea esquema + seeds.
"""
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Response, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from db import init_db
from auth import (seed_usuarios, autenticar, crear_token, usuario_actual, COOKIE_NAME,
                  SESSION_MAX_AGE, rate_limit_excedido, registrar_intento_fallido, limpiar_intentos)
from routers import consultas, acciones, ingesta, informe, catalogos, usuarios

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("consultas_sde")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    seed_usuarios()
    yield


app = FastAPI(title="Consultas SDE", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


@app.exception_handler(Exception)
async def excepcion_no_controlada(request: Request, exc: Exception):
    log.exception("Error no controlado en %s %s", request.method, request.url.path)
    return JSONResponse({"detail": "Error interno del servidor."}, status_code=500)

app.include_router(consultas.router)
app.include_router(acciones.router)
app.include_router(ingesta.router)
app.include_router(informe.router)
app.include_router(catalogos.router)
app.include_router(usuarios.router)


# ── Páginas ──────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    if not usuario_actual(request):
        return RedirectResponse("/login")
    return RedirectResponse("/panel")


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if usuario_actual(request):
        return RedirectResponse("/panel")
    return templates.TemplateResponse("login.html", {"request": request})


@app.post("/login")
def login_post(request: Request, username: str = Form(...), password: str = Form(...)):
    if rate_limit_excedido(username):
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Demasiados intentos fallidos. Esperá unos minutos."},
            status_code=429,
        )
    u = autenticar(username, password)
    if not u:
        registrar_intento_fallido(username)
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Usuario o contraseña incorrectos."},
            status_code=401,
        )
    limpiar_intentos(username)
    resp = RedirectResponse("/panel", status_code=303)
    resp.set_cookie(COOKIE_NAME, crear_token(u["username"]),
                    httponly=True, samesite="lax", max_age=SESSION_MAX_AGE)
    return resp


@app.get("/logout")
def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(COOKIE_NAME)
    return resp


@app.get("/panel", response_class=HTMLResponse)
def panel(request: Request):
    u = usuario_actual(request)
    if not u:
        return RedirectResponse("/login")
    return templates.TemplateResponse("panel.html", {"request": request, "usuario": u})


@app.get("/consulta/{cid}", response_class=HTMLResponse)
def detalle_page(request: Request, cid: int):
    u = usuario_actual(request)
    if not u:
        return RedirectResponse("/login")
    return templates.TemplateResponse(
        "detalle.html", {"request": request, "usuario": u, "cid": cid})


@app.get("/informe", response_class=HTMLResponse)
def informe_page(request: Request):
    u = usuario_actual(request)
    if not u:
        return RedirectResponse("/login")
    return templates.TemplateResponse("informe.html", {"request": request, "usuario": u})


@app.get("/perfil", response_class=HTMLResponse)
def perfil_page(request: Request):
    u = usuario_actual(request)
    if not u:
        return RedirectResponse("/login")
    return templates.TemplateResponse("password.html", {"request": request, "usuario": u})


@app.get("/api/yo")
def yo(request: Request):
    u = usuario_actual(request)
    if not u:
        return JSONResponse({"error": "No autenticado"}, status_code=401)
    return u


@app.get("/health")
def health():
    return {"ok": True}
