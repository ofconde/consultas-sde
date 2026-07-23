# Ingesta desde Microsoft Forms vía Power Automate

El formulario de Microsoft Forms sigue existiendo y se sigue llenando igual. Sumamos un flujo de
Power Automate que, **por cada respuesta nueva**, además de caer al Excel, hace un POST al sistema.
Así el Excel y el sistema conviven hasta que decidamos cortar el Excel.

## El flujo (3 pasos)

1. **Disparador:** *Microsoft Forms → "Cuando se envía una respuesta nueva"* → elegir el formulario de Santiago.
2. **Acción:** *Microsoft Forms → "Obtener los detalles de la respuesta"* (usa el Id de la respuesta del paso 1).
3. **Acción:** *HTTP → "HTTP"* (o "Enviar una solicitud HTTP") con:
   - **Método:** `POST`
   - **URI:** `https://consultas-sde-production.up.railway.app/api/ingesta/consulta`
   - **Encabezados:**
     - `Content-Type`: `application/json`
     - `X-API-Key`: el valor de la variable `SDE_API_KEY` del servicio `consultas-sde` en Railway
       (Project → consultas-sde → Variables). No se documenta el valor acá; consultarlo ahí.
   - **Cuerpo (JSON):** ver abajo.

## Cuerpo JSON esperado

Mapear cada campo a la pregunta correspondiente del Forms (con el selector de contenido dinámico).
Todos son opcionales salvo que quieras exigirlos; el sistema tolera faltantes.

```json
{
  "fecha_recepcion": "@{triggerOutputs()?['body/responseDate']}",
  "nombre": "<Nombre completo de la persona humana o jurídica>",
  "cuit": "<Número de CUIT>",
  "situacion_arca": "<Situación ante ARCA/AFIP>",
  "telefono": "<Teléfono de contacto>",
  "mail": "<Mail de contacto>",
  "localidad": "<Localidad donde desarrolla la actividad>",
  "actividad_economica": "<Actividad que desarrolla / descripción>",
  "sector": "<Sector productivo>",
  "monto": "<Monto de financiamiento que solicita>",
  "destino": "<Destino del financiamiento>",
  "como_se_entero": "<Cómo tomó conocimiento de las líneas CFI>",
  "genero": "<si aplica>"
}
```

## Notas importantes

- **Formulario único → mapeo fijo.** A diferencia del intento multiprovincia que se abandonó
  (149 formularios distintos), acá es un solo formulario, así que el mapeo de preguntas es estable.
- Si el Forms tiene **varias ramas** (Mujeres / Verde / Cadena / Exportaciones) con preguntas
  equivalentes repetidas, usar en cada campo una expresión `coalesce(rama1, rama2, …)` para tomar
  la primera no vacía.
- **`monto`** puede venir como texto ("$ 16.000.000" o "16000000"); el sistema lo limpia solo.
- **`fecha_recepcion`** ideal en ISO (`responseDate` del disparador). El sistema también acepta
  DD/MM/YYYY.
- La consulta entra con estado **`CONSULTA INICIAL`** y **sin técnico asignado**. El técnico la
  toma y gestiona desde el panel.
- **Anti-duplicado:** si llega dos veces la misma respuesta (mismo CUIT + misma fecha), el sistema
  la ignora y responde `{"ok": true, "duplicado": true}`.

## Probar el endpoint (sin PA)

```bash
curl -X POST https://consultas-sde-production.up.railway.app/api/ingesta/consulta \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <SDE_API_KEY>" \
  -d '{"nombre":"PRUEBA SRL","cuit":"30999999999","fecha_recepcion":"2026-07-22","monto":"15000000"}'
```

Ya se probó en producción (22/07/2026): con la key correcta responde `{"ok": true, "codigo": "SDE-000257"}`;
con una key inválida responde 401. El endpoint está confirmado funcionando en vivo.
