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

- 2026-08-14 — Órdenes 10/11/27 del backlog de seguridad
  (`SEC-AUTHN-001b/c`, `SEC-SESS-001`): bloqueo por cotización en el acceso
  al portal, fin de la creación implícita de `PortalCliente` y caducidad de
  90 días con regeneración desde el admin. **Precedida por una pérdida de
  trabajo real**: dos sesiones anteriores implementaron esto mismo dos veces
  en contenedores efímeros sin `git push` — los commits `3e9c6af` y
  `8351bf1` no existen en ningún remoto y no eran recuperables. La única
  vía es dejar el trabajo comprobadamente en `origin` antes de dar una tarea
  por terminada; un commit local en un entorno que puede reciclarse en
  cualquier momento no cuenta como hecho. El resumen de la sesión perdida
  sirvió como especificación, pero el código se escribió de nuevo contra el
  repo real, no copiado a ciegas — y una premisa suya era incorrecta: la
  creación automática de `PortalCliente` no vive en `ItemCotizacion.save()`
  sino en `Cotizacion.save()` (línea ~754), y ahí se quedó — es el flujo
  legítimo de alta, `portal_acceso` solo deja de duplicarlo. El bucket por
  cotización hashea el id con el mismo patrón que `_clave_usuario`, aunque
  un id secuencial no es secreto: es barato y mantiene un solo criterio para
  todo lo que entra a la tabla `qkt_cache`. `expira_en` se calcula en
  `PortalCliente.save()` solo si `not self.pk`, así que **regenerar desde el
  admin no puede reusar ese método**: la acción fija `expira_en` a mano
  (ahora + 90 días, no evento + 90), porque regenerar es extender desde hoy,
  no recalcular desde una fecha de evento que ya pasó. `getattr(cotizacion,
  'portal', None)` para leer el `OneToOneField` inverso sin `try/except`
  funciona porque Django hace que `RelatedObjectDoesNotExist` herede también
  de `AttributeError`, a propósito, para que `getattr`/`hasattr` funcionen
  con relaciones inversas — sin ese detalle el default de `getattr` nunca se
  activaría. De paso se eliminó por completo la herramienta de recuperación
  de Cloudinary (ver entrada de abajo): ya no tenía sentido mantenerla junto
  a código nuevo de seguridad del portal.
- 2026-08-12 — Migración al bucket privado desde el admin
  (`/admin/migrar-archivos-privados/`, `comercial/services_migracion_privada.py`),
  y **el orden en que se activa, que no es el intuitivo**. Definir
  `CLOUDFLARE_R2_PRIVATE_BUCKET_NAME` es lo último, no lo primero: en cuanto
  esa variable existe, los cuatro campos sensibles empiezan a leerse del
  bucket nuevo, y si todavía está vacío **los archivos existentes dejan de
  abrir** aunque sigan intactos en el público. Pasó en producción: la variable
  se había definido días antes apuntando a `qkt-media-2` y estuvo inerte hasta
  que el deploy del PR #194 trajo el código que la usa; a partir de ahí los
  estados de cuenta —que estaban en `qkt-media`— quedaron ilocalizables, y
  parte de lo que la página de recuperación contaba como "falta" era en
  realidad esto y no Cloudinary. El rollback es borrar la variable: sin ella
  `storage_privado()` cae al default y todo vuelve a su sitio, sin desplegar.
  Secuencia correcta: crear el bucket → definir la variable → migrar → verificar
  → borrar del público. El hueco entre el segundo y el tercer paso es
  inevitable porque el comando necesita la variable para saber a dónde copiar;
  hacerlos seguidos lo reduce a minutos. `migrar_archivos_privados` **copia y
  no mueve**, y conserva el `name` exacto: la BD guarda esa ruta y no se toca,
  por eso si el destino renombra se descarta la copia en vez de dejar el
  registro apuntando a la nada. El comando entró en el PR #194 sin tests; los
  14 de `comercial/test_migrar_archivos_privados.py` cubren ese hueco.
- 2026-08-12 — Los 228 archivos que solo vivían en Cloudinary se dan
  **definitivamente por perdidos** (decisión del propietario: no vale la pena
  pagar la reactivación). Se eliminó toda la herramienta de recuperación que
  se había construido para ese fin —`comercial/services_recuperacion.py`, el
  comando `recuperar_archivos_cloudinary`, su vista de admin y sus tests—
  porque nunca podrá tener éxito sin reactivar la cuenta, y esa reactivación
  quedó descartada. La mayoría del daño es menor de lo que parecía: las
  cotizaciones y contratos los reemite el ERP, y los CFDI y estados de cuenta
  se rebajan del SAT y del banco; lo irrecuperable son las fotos de productos
  y landing y las identificaciones de ARCO. Dos hallazgos de esa investigación
  siguen valiendo la pena recordarlos si se vuelve a tocar R2 desde código:
  **(1)** con un token sin permiso de listado, `HeadObject` sobre una clave
  inexistente devuelve **403, no 404** —comportamiento documentado de S3: sin
  `ListBucket` el servicio no confirma la ausencia—, así que un `exists()`
  puede lanzar en vez de responder `False`. **(2)** el bucket público
  `qkt-media` nunca recibió contratos, cotizaciones, productos, nómina ni
  facturación en la migración a R2: solo tenía `estados_cuenta/` y
  `landing/`. La migración al bucket privado (`SEC-FILE-001a`, entrada de
  abajo) es una historia distinta y sí sigue en pie.
- 2026-08-12 — Auditoría de seguridad (Issue #190, PR #194) y corrección de
  los dos hallazgos que resultaron explotables. **XSS almacenado no
  autenticado con toma de sesión de staff**: `json.dumps` **no escapa `<`,
  `>` ni `&`** —solo comillas y barras—, así que el patrón
  `{{ x|safe }}` sobre un `json.dumps` dentro de un `<script>` es inyectable
  en cuanto el dato viene del usuario. En `/admin/calendario/` el dato venía
  del cotizador **público**: un POST anónimo a `/cotizar/enviar/` con
  `</script><script>…` en el nombre quedaba persistido en `nombre_evento` y
  se ejecutaba en el navegador de cualquier staff que abriera el calendario.
  Confirmado ejecutándolo, no leyéndolo. Se corrigió con `|json_script`, que
  sí escapa esos tres caracteres y además mete el JSON en un
  `<script type="application/json">` no ejecutable; el contexto pasa la
  lista **sin serializar** (el filtro serializa, y con `DjangoJSONEncoder`,
  así que las fechas siguen funcionando). Mismo cambio en los dos dashboards
  por higiene. Ojo al tocarlos: los tests que hacían
  `json.loads(response.context['chart_labels'])` ahora reciben una lista.
  **Feed iCal abierto**: `generar_ical_eventos` hacía `if token_esperado:`
  sobre un `config(..., default='')` — sin la variable, la validación entera
  se saltaba (fail-open, y documentado como "retrocompatibilidad"). En
  producción `ICAL_PUBLIC_TOKEN` **no estaba definida**, así que el `.ics`
  se descargaba sin autenticación con nombre de cliente, evento, asistentes
  y fecha de cada cotización confirmada. Ahora es fail-closed (403 sin
  token) y el `.ics` solo lleva `COT-NNN`: el feed existe para que Airbnb
  bloquee la fecha, no para publicar la cartera. Al eliminar el texto libre
  desaparece de paso la inyección CRLF del RFC 5545. El token se lee de
  `settings.ICAL_PUBLIC_TOKEN` y no con `config()` dentro de la vista,
  siguiendo la convención ya escrita en `settings.py` para las variables de
  WhatsApp: una sola fuente y `override_settings` en los tests. **Contener
  la fuga no requirió desplegar**: bastó definir la variable en Railway y
  añadir `?token=…` a la URL registrada en Airbnb — sin eso, Airbnb deja de
  sincronizar en silencio. Quedan pendientes 46 tareas del backlog, entre
  ellas verificar si el bucket R2 sirve las identificaciones ARCO por URL
  sin firma (`querystring_auth: False`) y si existen respaldos probados de
  PostgreSQL.
- 2026-08-11 — Rate limiting compartido entre workers + bloqueo de fuerza
  bruta en `/admin/login/` (Issue #179, PR #180). `LocMemCache` (default de
  Django) vive por proceso y `gunicorn --workers 2` (Dockerfile) lo hacía
  inútil: cada worker tenía sus propios contadores de rate limit y su propio
  candado anti-doble-cobro de Openpay
  (`comercial/views_openpay.py:pago_openpay_en_curso:`). Se cambió a
  `django.core.cache.backends.db.DatabaseCache` (tabla `qkt_cache`, creada
  por migración `comercial/migrations/0069_tabla_cache.py` con
  `createcachetable`, idempotente); no se provisionó Redis por no tener
  infraestructura nueva que mantener en Railway. `/admin/login/` ahora
  bloquea por intentos fallidos (IP y usuario, buckets independientes;
  `ADMIN_LOGIN_VENTANA`/`_MAX_INTENTOS_IP`/`_MAX_INTENTOS_USUARIO`),
  interceptando la ruta antes de `admin.site.urls` con el mismo patrón que
  ya usaba `custom_logout`. La revisión automática del PR (`ai-review-merge.yml`)
  encontró 4 hallazgos bloqueantes reales que se corrigieron a mano tras dos
  fallos consecutivos del paso "Review with Claude" (`is_error:true`,
  `total_cost_usd:0`, `num_turns:1` — fallo a nivel de API, no de config):
  **(1)** `_contar()` usaba `cache.incr()`, cuyo `set()` interno sin timeout
  hacía que el bucket cayera al `TIMEOUT` global (3600s) en vez de los
  `window*2` fijados por el `add()` inicial — un bucket 30x más longevo es
  candidato temprano al cull por orden lexicográfico de `cache_key`, y
  `pago_openpay_en_curso:` ordena antes que `rl:`, así que el candado de
  Openpay habría sido el primero en perderse; ahora cada escritura fija su
  propio timeout sin pasar por `incr()`. **(2)** `limpiar_intentos_login()`
  borraba también el bucket de IP en un login exitoso: cualquiera con una
  credencial válida podía alternar fallos contra otros usuarios (password
  spraying) con un login propio correcto desde la misma IP y anular el
  límite por IP; ahora solo se limpia el bucket del usuario que autenticó,
  el de IP expira por ventana. **(3)** faltaban las 4 variables nuevas en
  `.env.example`. **(4)** la rama `RedisCache` en `settings.py` era código
  muerto que rompía el arranque si alguien definía `REDIS_URL` en Railway
  (`redis` no está en `requirements.txt`); se eliminó en vez de añadir la
  dependencia fuera de alcance.
- 2026-08-10 — Notificaciones transaccionales unificadas (Issue #181).
  `comunicacion/services.py` es el único transporte (email + WhatsApp texto +
  plantilla) y `services_notificaciones.py` la única capa de negocio; el
  cotizador, los signals y el cron solo la llaman. Hallazgos que no eran
  obvios: **había dos comandos de recordatorios**, y el de `comercial`
  (`enviar_recordatorios_pagos`) ya mandaba WhatsApp con otro calendario y
  otra tabla — se consolidó en `comunicacion.enviar_recordatorios` y el viejo
  quedó como shim que delega, porque el Cron de Railway lo invoca por ese
  nombre y su config vive fuera del repo. El calendario ahora es la unión de
  ambos (`DIAS_AVISO = (3, 0, -1)`). **`transaction.on_commit()` va en los
  signals, no en el cotizador**: `cotizador_enviar` corre en autocommit (sin
  `atomic()` ni `ATOMIC_REQUESTS`), así que ahí el callback se ejecuta de
  inmediato y solo aparentaría una garantía inexistente. La idempotencia es
  `clave_idempotencia` única + INSERT antes de enviar; el `transaction.atomic()`
  alrededor **no es opcional**: en PostgreSQL un `IntegrityError` sin savepoint
  aborta la transacción envolvente y tumba el request, y en SQLite no se
  reproduce. Los parámetros de plantilla se aplanan con `texto_plano_wa()`
  porque Meta rechaza saltos de línea, tabuladores y >4 espacios seguidos
  dentro de una variable, y el `$` vive en el cuerpo aprobado, no en el
  parámetro. Dos variables distintas para dos roles: `WA_NUMERO_NEGOCIO`
  (destino humano de la alerta interna, **debe diferir del emisor** o Meta
  responde 131021) y `WA_NUMERO_CONTACTO_PUBLICO` (el `wa.me` que ve el
  cliente); sin fallback hardcodeado en ninguna de las dos. De paso:
  `normalizar_telefono_wa()` ya no fabrica un prefijo para números de 8 dígitos
  —mandaba el mensaje a un desconocido— y el comando usa `timezone.localdate()`
  en vez de `now().date()`, que devolvía la fecha **UTC** y corría los
  recordatorios un día entre las 18:00 y la medianoche de Mérida.
- 2026-08-09 — Carga masiva de imágenes de la landing
  (`/admin/comercial/imagenlanding/carga-masiva/`, Issue #173): sube N
  archivos de golpe con sección/categoría/enfoque comunes y continúa el
  `orden` desde el máximo de esa sección, procesando por nombre de archivo.
  Cada archivo pasa por un `ModelForm`, no por `objects.create(imagen=f)`:
  `create()` se salta la validación del `ImageField` y un PDF renombrado
  acabaría en el bucket rompiendo la página con una imagen muerta. El
  `alt_text` queda vacío a propósito — autogenerarlo desde el nombre del
  archivo no describe nada y ensucia el SEO. **El límite de 100 archivos por
  request (`DATA_UPLOAD_MAX_NUMBER_FILES`, default de Django 6) no se sube**:
  es superficie de DoS en todo el sitio; se captura `TooManyFilesSent` y se
  pide subir por tandas. La acción hermana desactiva (nunca borra) los
  registros cuyo archivo ya no está en el storage — es acción sobre
  selección, no columna de `list_display`, porque `exists()` es una petición
  de red por fila. Tests con `InMemoryStorage` vía `override_settings`, que
  es el patrón del repo para probar storage sin tocar un bucket real (ver
  `comercial/test_migrar_archivos_privados.py`).
- 2026-08-09 — `ai-implement.yml`/`ai-review-merge.yml`: varios pasos usan
  `fromJSON(steps.X.outputs.structured_output)` en su `if:`/`env:` sin
  proteger contra que ese step nunca haya corrido (modo Codex/Claude en vez
  de Combinado, donde "Plan with Claude" ni se ejecuta) o haya fallado sin
  producir salida (`error_max_turns`). `fromJSON('')` revienta la
  compilación del template de **todo el job** con `The template is not
  valid`, un error que apunta a una línea sin problema real y oculta la
  causa (turnos agotados, herramientas denegadas). Se protegieron las siete
  ocurrencias con `fromJSON(... || '{}')`. Detectado al intentar disparar
  Codex en modo "Solo Codex" sobre los Issues #168/#169.
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
- 2026-07-26 — Bug detectado: `comercial/admin.py` usaba `get_object_or_404`
  en 4 sitios sin importarlo — `NameError` en runtime al aplicar/revertir
  descuentos o generar contrato desde el admin. **Corregido** en `1607c1e`
  (import añadido en la línea 7); `ruff check --select F821 .` pasa limpio
  en todo el repo. Entrada conservada como histórico.
- 2026-07-26 — Se configuraron hooks, `.mcp.json`, este archivo, el skill
  `create-migration` y los subagentes `security-reviewer`/`code-reviewer`
  (ver PR #111).
