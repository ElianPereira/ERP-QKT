# CLAUDE.md

Referencia operativa para trabajar en ERP-QKT. Léela antes de explorar el
repo por tu cuenta — la mayoría de las preguntas de "¿cómo corro X?" o
"¿dónde vive Y?" ya están respondidas aquí.

## Reglas de respuesta

- Sé directo: ve al resultado, sin narrar "voy a hacer X" y luego "hice X".
  Una frase de estado basta si hace falta.
- No expliques conceptos básicos de Django/Python salvo que se pida.
- No leas archivos completos si un `grep`/`glob` dirigido resuelve la duda.
- **Nunca leas por defecto**: `*/migrations/*.py` (histórico, sin valor de
  arquitectura — lee `models.py` en su lugar), `graphify-out/`, `static/`,
  binarios/imágenes. Solo ábrelos si la tarea es literalmente sobre eso.
- Confía primero en este archivo y en `PROJECT_CONTEXT.md` antes de
  re-explorar algo que ya documentan.
- Ediciones quirúrgicas: no refactorices código no relacionado con la tarea.
- Responde en español. Commits, nombres de variables y código siguen el
  estilo ya existente en el repo (mezcla ES/EN, no lo normalices).
- No hagas `commit`/`push` sin que se pida explícitamente.

## Contexto técnico

**Stack**: Django 6 · PostgreSQL (prod, Railway) / SQLite (dev) · admin
Jazzmin (sin frontend SPA) · WeasyPrint (PDFs) · Cloudinary (storage) ·
django-anymail/Brevo (email) · Openpay vía REST directo, sin SDK · ruff +
pre-commit · CI en GitHub Actions.

**Comandos esenciales**:
| Acción | Comando |
|---|---|
| Test de una app | `python manage.py test <app>` |
| Test completo (= CI) | `python manage.py test comercial contabilidad airbnb facturacion nomina legal core_erp` |
| Lint | `ruff check .` / autofix: `ruff check --fix .` |
| Chequeo Django | `python manage.py check` |
| Detectar migraciones faltantes | `python manage.py makemigrations --check --dry-run` |
| Servidor local | requiere `.env` desde `.env.example` (`SECRET_KEY` sin default) |

**Estructura clave** (9 apps Django):
- `comercial/` — núcleo: cotizaciones, clientes, inventario, pagos, portal
  cliente, cotizador público. La más grande, con diferencia.
- `contabilidad/` — cuentas, pólizas (generadas por *signals*, nunca a mano
  desde `comercial`), conciliación bancaria.
- `airbnb/`, `nomina/`, `facturacion/`, `comunicacion/`, `reportes/` — un
  dominio cada una; `reportes/services/*.py` centraliza reportes PDF/Excel.
- `legal/` — versionado de documentos legales (SHA-256, una sola versión
  vigente por tipo), evidencia de consentimiento y bitácora ARCO.
- `core_erp/` — `settings.py`, `urls.py` raíz, rate limiting, `impuestos.py`
  (fuente única del IVA y las retenciones: fuera de ahí no debe existir
  ningún `0.16` / `1.16` / `0.0125`).

Detalle de modelos/rutas/convenciones completo → `PROJECT_CONTEXT.md`.
Hooks automáticos (ruff autofix, tests por app, confirmación en `.env` y
código de pagos) → `.claude/settings.json`.

## Planificación mediante GitHub Issues

Durante la fase de planificación, Claude debe limitarse a analizar el problema,
identificar dependencias, riesgos y archivos relevantes, y diseñar un plan
ejecutable. Debe publicar ese plan en un GitHub Issue usando la plantilla de
implementación, con alcance incluido y excluido, pasos concretos, criterios de
aceptación verificables y comandos de validación disponibles en el repositorio.

Claude no debe implementar código ni modificar archivos del producto durante
esta fase. La implementación comienza únicamente después de que el Issue tenga
un plan suficientemente preciso para que Codex pueda ejecutarlo; cualquier
incertidumbre o decisión que requiera intervención humana debe quedar explícita
en el Issue.

## Memoria

Registro de decisiones técnicas y errores resueltos. Formato:
`FECHA — decisión/error → resolución o estado`. Agrega una línea nueva
arriba cada vez que se resuelva algo no obvio; no borres entradas viejas
salvo que queden obsoletas.

- 2026-08-08 — `.github/workflows/ai-review-merge.yml`: el job "Review,
  correct and merge" fallaba en el paso de `claude-code-action` con
  `Unable to get ACTIONS_ID_TOKEN_REQUEST_URL env variable`, pese a que el
  job ya declaraba `id-token: write`. La causa: sin `github_token`
  explícito, la acción intenta autenticarse por OIDC contra la GitHub App
  "Claude" (`github.com/apps/claude`), y ese intercambio fallaba en el
  runner. Se evitó el flujo OIDC por completo pasándole el token que ya
  emite `identity-token` (la GitHub App propia del repo, `AI_APP_CLIENT_ID`)
  ampliado con `permission-contents: read` y `permission-issues: read`
  además del `pull-requests: read` que ya tenía.
- 2026-08-08 — `manage.py corregir_polizas_airbnb_iva`: el signal
  `sincronizar_poliza_pago_airbnb` se degrada en silencio (cuenta contable
  faltante/inactiva, unidad de negocio inactiva) en vez de lanzar. El
  comando cancelaba la póliza original y confiaba en que el signal siempre
  reexpidiera una nueva — si el signal se degradaba, el pago quedaba sin
  ninguna póliza `APLICADA` (peor que el descuadre que el comando arregla)
  y aun así se reportaba como éxito. Ahora verifica que exista una póliza
  vigente con el IVA trasladado registrado tras el signal; si no, lanza
  `CommandError` y la transacción completa revierte.
- 2026-08-07 — Conciliación de depósitos de Airbnb (`ConciliacionDepositosService`,
  `/admin/airbnb/conciliacion-depositos/`): Airbnb junta en un solo payout las
  reservas que liquida el mismo día, así que el banco trae **un abono por
  payout**, no uno por reserva —por eso la conciliación suma primero por
  `payout_id`—. El abono cae días después de la fecha del payout (en el CSV
  real de marzo, cinco): se empareja por referencia si el banco conservó el id
  y, si no, por importe exacto dentro de una ventana de −1/+10 días, sin
  reutilizar un movimiento ya asignado. Es solo reporte, no escribe nada: con
  cargar el estado de cuenta que faltaba vuelve a cuadrar. Cierra el Issue #134.
  Si dos abonos encajan igual de bien **no adivina**: marca el depósito como
  AMBIGUO y quien concilia elige; la decisión se guarda en `DepositoConciliado`
  y manda sobre el automático. El emparejamiento por importe corre en varias
  pasadas, porque cada asignación inequívoca puede desambiguar a otra.
- 2026-08-07 — `manage.py corregir_polizas_airbnb_iva` (simula por defecto,
  `--aplicar` escribe): reexpide las pólizas de Airbnb anteriores al arreglo
  del asiento, que cargaban el depósito completo a bancos pero no registraban
  el IVA trasladado y por eso descuadraban justo por ese importe. **Cancela y
  reexpide, no reversa**: la operación con Airbnb nunca cambió, lo que estaba
  mal era la captura, y un ajuste de una sola línea es imposible porque la
  contrapartida del IVA ya está en bancos desde el asiento original. La póliza
  cancelada conserva sus movimientos y queda fuera de saldos y reportes, que
  en todo el ERP solo suman `estado='APLICADA'`. Se asienta en el período
  original a propósito: el descuadre nació ahí y las cifras declaradas
  —ingreso, retenciones, depósito— no cambian.
- 2026-08-07 — Póliza de `PagoAirbnb`: el signal ahora **sincroniza** en vez
  de solo crear. Al actualizar un pago (reimportar el CSV corrige montos) la
  póliza se regenera en sitio —mismo folio y misma auditoría, movimientos
  reescritos—; un pago que deja de estar PAGADO recibe póliza de reversión
  con `origen='AJUSTE'` (nunca se borra nada) y si vuelve a PAGADO se emite
  la reactivación que la compensa. De paso se corrigió un asiento que no
  cuadraba: faltaba el **IVA trasladado al HABER** —el depósito de Airbnb lo
  incluye—, y un pago cuyo neto no cuadra con la fórmula ya no se aplica: se
  queda en BORRADOR con aviso en el log.
- 2026-08-05 — **Openpay está EN PRODUCCIÓN.** Primer cobro real verificado
  ($1.00, autorización 186823). `OPENPAY_MODE=production` y las cuatro
  variables de Railway con las llaves de producción; el resto lo conmuta ese
  flag solo (URL de la API, `setSandboxMode` del JS, dominio de las fichas
  PDF). Webhook dado de alta y verificado en el dashboard de producción —que
  es distinto al de sandbox y no hereda nada— en
  `/pagos/openpay/webhook/` con "Todos los eventos" y autenticación básica.
  Al registrarlo, el dashboard devolvió `undefined : undefined` sin llegar a
  llamar al servidor; se resolvió recargando la página, no era problema de la
  contraseña. El código de verificación se lee de los Deploy Logs, donde
  `openpay_webhook_view` lo emite como `warning` a propósito.
- 2026-08-03 — Certificación de Openpay cerrada: las cinco observaciones del
  técnico quedaron atendidas (documentos legales enlazados, logotipos
  oficiales, motivo explícito de rechazo en los logs del servidor, 3D Secure
  probado, ficha de efectivo/SPEI completa).
  Soporte Openpay: **(55) 97 55 35 59** · **soporte@openpay.mx**.
- 2026-08-03 — Rutas de los recibos PDF de Openpay: `/spei-pdf/{merchant}/
  {id de la transacción}` pero `/paynet-pdf/{merchant}/{payment_method.
  reference}`. No llevan el mismo identificador y nada en el nombre del
  parámetro lo delata; mandar el id en la de paynet devuelve error. Además el
  cargo `store` tope a $29,999.99 y el `due_date` a 30 días.
- 2026-07-31 — Módulo `legal`: los documentos se sirven desde BD con
  versionado e integridad SHA-256. Las rutas `/aviso-de-privacidad/` y
  `/terminos-y-condiciones/` se conservaron para no romper enlaces ya
  publicados. Un documento con marcadores `[CONFIRMAR:]`/`[PENDIENTE:]` no
  se puede publicar (candado en `save()`, en `clean()` y en el seed).
- 2026-07-31 — Precios con IVA incluido (art. 7 BIS LFPC). `core_erp/
  impuestos.py` es la fuente única; había **siete** implementaciones del 16%,
  no cuatro. Hallazgos: `calcular_totales()` no redondeaba (lo hacía Django
  con ROUND_HALF_EVEN al guardar); el empate `.005` es **inalcanzable** para
  el IVA con base de 2 decimales (`1.6N` siempre par) pero **sí alcanzable**
  para la retención de ISR de personas morales. Correr
  `manage.py auditar_precios_iva` en producción para medir el impacto.
- 2026-07-31 — `desglosar()` no puede cumplir `iva == round(base*tasa)` exacto
  para ~14% de los importes (la función base→total salta valores). Se permite
  1 centavo de holgura en el IVA, que es la tolerancia real del SAT.

- 2026-07-26 — Se evaluó instalar `claude-mem` (memoria de terceros vía
  hooks) → descartado: guarda datos en `~/.claude-mem` (no en el repo, no
  es memoria realmente compartida) e instala runtimes extra (Bun, `uv`) en
  la máquina de cada quien. Ver hilo de la sesión si se reconsidera.
- 2026-07-26 — Bug detectado, **sin corregir**: `comercial/admin.py` usa
  `get_object_or_404` en 4 sitios (líneas 757, 763, 774, 815) sin
  importarlo — `NameError` en runtime al aplicar/revertir descuentos o
  generar contrato desde el admin.
- 2026-07-26 — Se configuraron hooks, `.mcp.json`, este archivo, el skill
  `create-migration` y los subagentes `security-reviewer`/`code-reviewer`
  (ver PR #111).
