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
| Test completo (= CI) | `python manage.py test comercial contabilidad airbnb facturacion nomina legal core_erp comunicacion reportes` |
| Lint | `ruff check .` / autofix: `ruff check --fix .` |
| Chequeo Django | `python manage.py check` |
| Detectar migraciones faltantes | `python manage.py makemigrations --check --dry-run` |
| Servidor local | requiere `.env` desde `.env.example` (`SECRET_KEY` sin default) |
| Regenerar `requirements.lock` (tras tocar `requirements.txt`) | `pip-compile requirements.txt --output-file=requirements.lock --resolver=backtracking` |

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

**Ya no se usa Codex** (decisión del propietario, 2026-08-17 — ver Memoria).
Quien implementa, tras planificar, es Claude directamente: no hay hand-off a
otro ejecutor ni fase separada de "esperar a que alguien más lo tome".

Para cambios grandes o que tocan varios dominios (legal, contable, producto,
varias apps), sigue valiendo la pena investigar y publicar el plan en un
GitHub Issue con la plantilla de implementación (alcance incluido/excluido,
pasos concretos, criterios de aceptación verificables, comandos de
validación) **antes** de tocar código — no como gate para otro ejecutor, sino
porque ordena el trabajo, dejó decisiones no obvias por escrito y le da al
propietario un punto donde comentar/corregir antes de que el cambio esté
hecho. Cualquier incertidumbre o decisión que requiera intervención humana
va explícita ahí (o se resuelve directo con el propietario, lo que sea más
rápido) antes de implementar — no se inventa a discreción.

Para cambios chicos/acotados, implementar directo sigue siendo válido, como
ya se ha hecho varias veces (ver Memoria) — el Issue es una herramienta para
cuando el tamaño del cambio la justifica, no un trámite obligatorio.

## Memoria

Registro de decisiones técnicas y errores resueltos. Formato:
`FECHA — decisión/error → resolución o estado`. Agrega una línea nueva
arriba cada vez que se resuelva algo no obvio; no borres entradas viejas
salvo que queden obsoletas.

- 2026-08-26 — **`DEBUG=True` estaba activo en producción (Railway)**,
  confirmado con una captura real del propietario: el 404 del enlace de
  guía de Pasadía mostraba la página técnica de Django (listado completo de
  `URLconf`, vista que lo generó, método de la petición) — eso **solo
  aparece con `DEBUG=True`**, no es el 404 genérico. El propietario ya lo
  corrigió en las variables de Railway (`DEBUG=False`, que además es el
  default de `core_erp/settings.py` si la variable no existiera). Mientras
  estuvo activo, cualquier error de la app pudo haber expuesto rutas
  internas y, en un 500 real, también variables de entorno y fragmentos de
  código fuente a cualquier visitante — no hay forma de auditar desde el
  repo cuánto tiempo estuvo así ni si alguien más lo vio, porque es
  configuración de Railway, no algo que quede en el historial de git.
  `ci.yml` ya corre `manage.py check --deploy --fail-level WARNING` con
  `DEBUG=False` (orden de seguridad previa), pero eso valida el código, no
  las variables de entorno reales de Railway — no hay gate automático
  posible desde este repo contra un valor mal puesto ahí.
- 2026-08-26 — Páginas de error propias (400/403/404/500) con la identidad
  visual del portal, en vez de la página técnica/genérica de Django que veía
  el propietario (reportado con un enlace de guía de Pasadía con fecha ya
  pasada — ese caso concreto es un 404 real de `_portal_vigente_o_404`, no
  un bug). `templates/400.html`/`403.html`/`404.html`/`500.html` extienden
  `templates/errores/_base.html` (header amarillo, tarjeta blanca, mismo
  patrón que `portal/acceso.html`) — Django las detecta solas por nombre
  exacto en el `DIRS` de `TEMPLATES` (`templates/`), sin necesitar
  `handler404`/`handler500` en `urls.py`. El `500.html` se renderiza **sin
  contexto de request** (`django.views.defaults.server_error` llama
  `template.render()` sin argumentos, a diferencia de 400/403/404 que sí
  reciben `request` y sus context processors) — nada de `{% csrf_token %}`
  ni de settings vía context processor ahí; el WhatsApp del footer usa el
  mismo número público ya hardcodeado en `templates/landing/*.html`
  (`529994457178`), no `settings.WA_NUMERO_CONTACTO_PUBLICO`, porque ese
  no llega sin contexto. Verificado con
  `get_template(name).render({})` para las cuatro (mismo camino que usa
  Django internamente) y con `manage.py test` completo. **Nota para el
  propietario, no resuelta aquí**: si lo que viste era la pantalla
  amarilla/beige con el traceback técnico (no un 404/500 genérico), eso es
  la página de depuración de Django, que solo aparece con `DEBUG=True` —
  confirma que esa variable en Railway está en `False` en producción, o
  cualquier caída expone rutas de archivos y variables de entorno al
  público.
- De paso, se corrigió el 500 documentado y pendiente de la sesión anterior
  en `configurar_plantilla_barra` (`/admin/comercial/configurar-plantilla-
  barra/`): la plantilla `admin/comercial/configurar_plantilla_barra.html`
  nunca había existido en el repo — la vista revienta con
  `TemplateDoesNotExist` en cuanto se le da GET/POST real, bug ya
  documentado como tal en `comercial/test_permisos_grupos.py` (con la
  vista excluida de `test_ninguna_vista_protegida_da_403` a propósito).
  Se creó la plantilla con el patrón `admin/base_site.html` + Jazzmin ya
  usado en `carga_masiva_imagenes.html` (selects de insumo/proporción por
  categoría de barra, agrupados por `GRUPO_CONFIG`) y se quitó la exclusión
  del test.
- 2026-08-25 — El cron de recordatorios al contador (`enviar_recordatorios_
  contador`) ahora también reintenta las solicitudes `PENDIENTE` (pedido
  directo del propietario tras dudar si el cron cubría ese caso). Antes esas
  solicitudes —el envío automático al registrar el pago falló en los dos
  canales— se quedaban ahí para siempre salvo intervención manual desde el
  admin: el cron original solo miraba `ENVIADA`. Ahora, en cada corrida,
  primero reintenta cada `PENDIENTE` llamando exactamente a
  `enviar_solicitud_al_contador()` (la misma función que usa el envío
  automático original) y solo después procesa los recordatorios de cadencia
  de las `ENVIADA`. **Sin cadencia de días para el reintento** (a propósito,
  a diferencia de los recordatorios 3/7/14): mientras siga `PENDIENTE` es
  porque nunca se mandó de verdad, así que se reintenta en cada corrida sin
  esperar ningún número de días — no hay riesgo de bucle infinito porque el
  Cron Job de Railway define la cadencia real (una corrida por día), y en
  cuanto un canal tiene éxito la solicitud pasa a `ENVIADA` y sale de esta
  rama para siempre. Se mantuvo en el mismo comando/archivo en vez de crear
  uno nuevo para no obligar a dar de alta un segundo Cron Job en Railway.
- 2026-08-25 — Línea de negocio (RFC emisor) visible en la solicitud de
  factura al contador (pedido directo del propietario, sin Issue previo —
  cambio acotado, seguimiento del punto anterior). La empresa opera bajo dos
  RFC propios y el contador no tenía forma de saber cuál usar al timbrar sin
  preguntar: `PECE010202IA0` para Quinta Ko'ox Tanil (Eventos, Pasadías y
  Hospedajes Directos) y `CERU580518QZ5` para ingresos directos de Airbnb —
  este mapeo **ya existía** como `RFC_UNIDAD_MAP` en
  `comercial/services.py` (lo usa `Compra.save()` para detectar la unidad de
  negocio del RFC receptor de un XML de compra); en vez de duplicar los dos
  RFC como strings nuevos, `SolicitudFactura` reusa ese mismo diccionario
  invertido (`_RFC_POR_LINEA_NEGOCIO` en `facturacion/models.py`) para no
  tener dos fuentes de verdad del mismo dato. Campo nuevo
  `SolicitudFactura.linea_negocio` (choices `QUINTA`/`AIRBNB`, migración
  `0008`, default `QUINTA`) + property `rfc_emisor` que resuelve el RFC a
  partir de esa clave. **Por qué el default es QUINTA y por qué la migración
  no necesitó una corrida de datos aparte**: hoy el signal
  (`facturacion/signals.py::crear_solicitud_factura_desde_pago`) es la única
  vía automática de creación, y siempre nace de una `Cotizacion` de
  Evento/Pasadía/Hospedaje/Arrendamiento — reservas directas de la Quinta,
  nunca de Airbnb (que no pasa por `Cotizacion`/`Pago`, vive aparte en
  `airbnb.PagoAirbnb` y **no** genera `SolicitudFactura` en absoluto
  todavía) — así que el default `QUINTA` con el `AddField` normal de Django
  ya deja bien las ~decenas de solicitudes existentes sin tocar nada a mano.
  El campo se dejó editable en el admin (con `AIRBNB` como opción) para el
  día en que alguien capture una solicitud manual de un ingreso de Airbnb —
  hoy no hay ningún flujo automático que la cree así, pero el campo ya
  soporta el caso sin más cambios de modelo. Se hizo visible en los tres
  lugares que lee el contador: **(1)** el PDF (`solicitud_pdf.html`) lleva
  un banner de color justo bajo el título, verde para Quinta / rojo Airbnb
  (mismo color que usa el badge del admin), con el RFC emisor en grande —
  no se mezcló con la tarjeta de datos fiscales del *cliente* (RFC/razón
  social del receptor) porque son dos RFC distintos y mezclarlos en la misma
  tarjeta confundiría más de lo que aclara. **(2)** el email
  (`get_datos_para_contador()`, que arma tanto el cuerpo del correo como el
  texto de `get_whatsapp_url()`) gana una línea nueva justo después del
  encabezado, antes de los datos fiscales del cliente, y el asunto del
  correo también lleva la línea de negocio al final. **(3)** el WhatsApp:
  el mensaje `document` directo (sin plantilla aprobada) lleva la línea de
  negocio en el caption; la plantilla de Meta (`WA_TEMPLATE_SOLICITUD_
  FACTURA`, todavía sin someter — ver entrada de abajo) pasa de 2 a 3
  variables (`{{3}}` = línea de negocio corta, "Quinta Ko'ox Tanil" o
  "Airbnb") — como el texto aún no se ha sometido a Meta, no hay
  compatibilidad que romper; `docs/whatsapp_plantilla_solicitud_factura.md`
  se actualizó con el texto de las 3 variables antes de que el propietario
  lo suba al Business Manager.
- 2026-08-25 — Automatización del envío de solicitudes de factura al
  contador + simulador de pago (pedido directo del propietario, sin Issue
  previo — cambio acotado). **(1)** `facturacion/admin.py` tenía duplicada
  toda la lógica de generar el PDF y hablar con Meta/Brevo dentro de las
  vistas de los botones — se extrajo tal cual a `facturacion/services.py`
  (`generar_pdf_solicitud`, `enviar_solicitud_por_email`,
  `enviar_solicitud_por_whatsapp`, `enviar_solicitud_al_contador`), sin
  cambiar ningún comportamiento, para poder reusarla desde el signal y el
  cron nuevos sin copiar el código una tercera vez. **(2)**
  `facturacion/signals.py::crear_solicitud_factura_desde_pago` ahora manda
  la solicitud sola (email + WhatsApp) con `transaction.on_commit()` justo
  después de crearla — ya no hace falta entrar al admin y darle a los
  botones. Cada canal se intenta por separado y ninguno lanza: si solo uno
  de los dos tiene éxito, igual se marca `ENVIADA` con ese método; si los
  dos fallan, se queda en `PENDIENTE` con el detalle en el log, sin tumbar
  el guardado del `Pago` que la originó (mismo criterio que el resto del
  ERP: nunca bloquear la operación real por un canal de aviso caído). El
  WhatsApp al contador sigue siendo un mensaje tipo `document` directo, no
  una plantilla aprobada — fuera de la ventana de 24h de Meta puede fallar
  silenciosamente (auditado en el log, no en la cara del usuario); se
  aceptó ese riesgo a propósito en vez de bloquear la automatización
  esperando someter una plantilla nueva a aprobación, decisión explícita
  del propietario vía `AskUserQuestion`. **(3)** Cron nuevo
  `enviar_recordatorios_contador` (mismo patrón que `enviar_recordatorios`/
  `enviar_guias`): recuerda a los 3/7/14 días de `fecha_envio` mientras la
  solicitud siga `ENVIADA` sin llegar a `FACTURADA`. Campo nuevo
  `SolicitudFactura.ultimo_recordatorio_enviado` (migración `0007`) para no
  reescribir `fecha_envio`/`estado` en cada recordatorio ni duplicar el
  mismo aviso si el comando corre dos veces el mismo día — deliberadamente
  **no** reusa el envío automático del punto 2 (que sí llama
  `marcar_enviada`), porque eso habría corrido el reloj de la cadencia cada
  vez que se manda un recordatorio. Falta dar de alta este Cron Job en
  Railway (fuera del repo, mismo patrón que los otros dos). **(4)**
  "Pagos borrador" — el pedido original era que un `Pago` de prueba tuviera
  "el mismo funcionamiento menos póliza y solicitud de factura". Investigar
  antes de tocar código encontró que `total_pagado()`/`saldo_pendiente()`
  alimentan sin ningún filtro el portal del cliente, el checkout público de
  Openpay, el cron `cerrar_cotizaciones` (cierra automático al llegar a
  saldo cero) y el Excel de cierre mensual a Dirección — un Pago de prueba
  con ese diseño se le habría mostrado al cliente real en su portal y
  podría haber cerrado una cotización real sola. Se lo planteé al
  propietario con esos riesgos explícitos (`AskUserQuestion`) y se optó por
  no guardar nada: **simulador** nuevo en `/admin/comercial/pago/simular/`
  (botón "Simular pago" en el changelist de `PagoAdmin`, mismo patrón que
  "Subir XML" de Compras) — busca una cotización real, captura un monto
  hipotético, y calcula saldo antes/después, mínimo a pagar, desglose
  fiscal proporcional y si alcanzaría a confirmar/cerrar la cotización,
  reusando `Pago(...).full_clean()` sobre una instancia **en memoria, nunca
  guardada** para la validación real (mismo `clean()` que un Pago de
  verdad, sin duplicar esa lógica) — cero riesgo de contaminar el portal,
  Openpay, el cierre automático o el Excel de Dirección porque no se
  escribe nada en la base de datos.
- 2026-08-22 — Dos correcciones tras la primera prueba en producción de la
  guía pre-evento (Issue #234, entrada de abajo): **(1)** `normalizar_telefono_wa()`
  (`comunicacion/services.py`, compartida por TODO WhatsApp del sistema —
  cotización, pago, recordatorio, alerta interna y ahora guía) anteponía un
  `1` extra a los celulares mexicanos (`521XXXXXXXXXX`). Ese `1` fue un
  requisito real de la Graph API en algún momento, pero Meta dejó de
  exigirlo — mandar con él hoy no da error (la API responde 200) pero el
  mensaje no le llega al número correcto. Confirmado con una prueba real: un
  WhatsApp a `5219992689400` no llegó, a `529992689400` sí. La función pasa
  a devolver `52XXXXXXXXXX` (12 dígitos, sin el `1`) como forma canónica; una
  entrada que ya venga con el `1` de más (dato heredado) se normaliza igual,
  no se deja pasar tal cual. Como esta función es compartida, el fix aplica
  a **todos** los canales de WhatsApp del ERP, no solo a la guía — no hay
  forma de arreglarlo solo para un caso sin duplicar la función. **(2)** El
  enlace de Google Maps de la guía usaba `_maps_url()`, que armaba una URL
  de **búsqueda genérica** a partir del texto del domicilio
  (`google.com/maps/search/?q=<domicilio>`) — no apuntaba a la ficha real
  del negocio en Maps. Se reemplazó por `MAPS_URL_QKT`, la URL real de la
  ficha de "Quinta Ko'ox Tanil" en Google Maps (confirmada por el
  propietario, con reseñas y fotos), fija en vez de armada por código.
  Ambos bugs se encontraron mandando una cotización de prueba real
  (`estado='CONFIRMADA'`, `fecha_evento` = hoy+3, teléfono/correo del propio
  propietario) y corriendo el cron `enviar_guias` manualmente desde Railway
  ("Run now") — no en tests: los tests con números `555...` mockeados no
  habrían detectado ninguno de los dos porque nunca comparan contra un
  número o una URL reales. Detalle no obvio de la prueba: el primer intento
  falló por variables de entorno faltantes en el servicio del cron
  (`CLOUDFLARE_R2_ACCOUNT_ID` y `EMAIL_FROM_RESERVAS`, no copiadas al
  duplicar el servicio de `enviar_recordatorios` en Railway) — eso dejó dos
  `ComunicacionCliente` en estado `FALLIDO` con la `clave_idempotencia` ya
  reservada, así que la siguiente corrida (ya con las variables corregidas)
  no reintentó nada hasta borrar esos dos registros a mano: el diseño de
  idempotencia de este módulo reserva la clave **antes** de intentar el
  envío, así que un fallo de configuración también bloquea reintentos, no
  solo un envío exitoso.
- 2026-08-22 — Guía pre-evento automática (Issue #234): 3 días antes de la
  `fecha_evento` de una `Cotizacion` **confirmada** de tipo Evento, Pasadía u
  Hospedaje, el cron nuevo `comunicacion.enviar_guias` manda un email corto
  con el PDF de la guía adjunto y un WhatsApp con enlace de descarga —
  Arrendamiento de Mobiliario no aplica (no tiene un sitio físico al que
  llegar) y Airbnb queda fuera por construcción (`ReservaAirbnb` no tiene
  relación con `Cotizacion`). El PDF real —uno por tipo de servicio, ya
  armado por el propietario— se sube desde `comercial.GuiaTipoServicio` en el
  admin; no se generó ningún contenido de "qué llevar" en código, vive
  íntegro en el PDF. **Por qué WhatsApp no lleva el PDF adjunto**: Meta exige
  una plantilla tipo "documento" para adjuntar un archivo fuera de la ventana
  de 24 h, más lenta de aprobar que una de solo texto — en su lugar manda un
  enlace a `portal_descargar_guia` (`/mi-evento/<token>/guia.pdf`), protegido
  por el token del portal igual que `portal_descargar_contrato`, con
  `@rate_limit` como el resto de descargas del portal (convención no
  opcional en este repo, ver Issue #190). `remitente_por_tipo()` gana
  `'EVENTO_PROXIMO': 'EMAIL_FROM_RESERVAS'` — es contenido cliente-facing
  antes del evento, mismo remitente que `COTIZACION`, no `EMAIL_FROM_
  NOTIFICACIONES` (reservado para alertas internas). El tipo `EVENTO_PROXIMO`
  ya existía declarado en `ComunicacionCliente.TIPO_CHOICES` desde el diseño
  original (Issue #181) pero nunca se había cableado a ningún código — es la
  primera vez que se usa. La clave de idempotencia (`f"guia:{cotizacion.pk}:
  canal"`) **no lleva fecha de ejecución**, a diferencia de
  `notificar_recordatorio()`: la guía se manda una sola vez por cotización,
  no se repite en varios cortes, así que no hace falta distinguir corridas
  de días distintos. Si no hay `GuiaTipoServicio` configurada para el tipo de
  servicio de una cotización elegible, el comando no truena ni manda nada
  roto al cliente: se salta esa cotización y avisa al equipo interno vía
  `alertar_equipo_email()` (mismo mecanismo que otras alertas operativas) —
  cubierto con test dedicado (`test_sin_guia_configurada_no_rompe_el_comando`).
  Dos tareas externas, ninguna bloqueante para el email: dar de alta el Cron
  Job `enviar_guias` en Railway (vive fuera del repo, mismo patrón que
  `enviar_recordatorios`, Issue #181) y someter `WA_TEMPLATE_GUIA` a
  aprobación de Meta Business Manager — sin ella el WhatsApp queda `FALLIDO`
  y auditado sin bloquear el email, verificado con test
  (`test_sin_whatsapp_configurado_el_email_igual_se_manda`). El propio Issue
  #234 se planificó y luego se implementó en la misma sesión —conversación
  directa con el propietario vía `AskUserQuestion` para resolver alcance
  (Hospedaje sí, Arrendamiento no), canal (email + WhatsApp), calendario (un
  solo aviso, 3 días antes), filtro de estado (solo `CONFIRMADA`) y el hecho
  de que el contenido extenso vive en PDFs que el propietario ya tiene armados,
  no en texto libre dentro del mensaje— siguiendo el flujo vigente desde el
  Issue #231 (Claude implementa directo, sin hand-off a Codex).
- 2026-08-19 — Nueva línea de negocio Hospedaje (Issue #230), implementada
  directo por Claude sin pasar por Codex (decisión del propietario del
  2026-08-17, ver entrada de abajo). Reservas de estancia corta —
  habitaciones **dentro de la Quinta**, no el inmueble "Honey Sea House" que
  también mencionan los documentos legales— contratadas directamente (no
  Airbnb), bajo el mismo RFC que Evento/Pasadía (`PECE010202IA0`, `QUINTA`).
  **Decisiones confirmadas por el propietario antes de tocar código** (las
  tres vía `AskUserQuestion`, no adivinadas): check-in/check-out **14:00/10:00**
  (coincide con `terminos_v2.1.md`/`reglamento_v1.1.md` ya vigentes — no hizo
  falta versionar ningún documento legal); exclusividad de fechas **a nivel
  de toda la quinta**, no por habitación (una reserva de Hospedaje bloquea
  el rango completo para Airbnb/Evento/Pasadía/Arrendamiento/otro Hospedaje,
  y viceversa — sin modelar inventario por unidad); y **solo habitaciones de
  la Quinta**, Honey Sea House queda fuera de alcance.
  **Modelo**: `Cotizacion.fecha_salida` (checkout, exclusiva, mismo criterio
  que `ReservaAirbnb.fecha_fin`), propiedad `noches` y método `rango_ocupado()`
  — este último es la fuente única que usa `airbnb.validacion_fechas` para
  comparar rangos, así un servicio de un día y uno de varias noches se
  comparan con la misma aritmética de traslape (`fecha_evento__lt=fin AND
  (fecha_salida__gt=inicio OR (fecha_salida__isnull=True AND
  fecha_evento__gte=inicio))` — exacto, sin necesidad de refinar en Python).
  `verificar_disponibilidad_fecha`/`obtener_fechas_bloqueadas` (antes de un
  solo día) pasan a ser wrappers de `verificar_disponibilidad_rango`, así
  Evento/Pasadía/Arrendamiento cruzan contra Hospedaje (y viceversa) sin que
  sus llamadores actuales noten diferencia de firma ni de mensajes.
  **Bug preexistente encontrado y corregido de paso** (no una vulnerabilidad
  nueva, dead code): `comercial/admin.py::CotizacionAdmin.save_model`
  llamaba a `validar_fecha_disponible` — función que **no existe** en
  `airbnb/validacion_fechas.py` — dentro de un `except ImportError: pass`
  que tragaba el error en silencio. Confirmado que esto **no era una
  vulnerabilidad real**: `Cotizacion.clean()` (que sí llama a la función
  correcta) ya corre automáticamente vía `ModelForm.full_clean()` antes de
  que `save_model` se ejecute en el flujo estándar del admin, así que la
  validación real siempre existió por ese otro camino; el bloque roto de
  `admin.py` era redundante y se eliminó en vez de arreglarse (una sola
  fuente de verdad).
  **Selector de habitaciones — NO usa una línea base automática** como
  Evento/Pasadía: el propietario aclaró a media implementación que solo hay
  dos habitaciones reales (Ka'an, Otoch) y el cliente debe poder elegir una
  o ambas. Se descartó `rol_cotizador='BASE_HOSPEDAJE'` (una sola línea
  automática) y en su lugar cada habitación es un `Producto` con
  `rol_cotizador='HABITACION_HOSPEDAJE'` (selección múltiple, cantidad =
  noches); `Producto.cotizador_hospedaje` quedó con el mismo significado que
  `cotizador_evento`/`cotizador_pasadia` (extra normal disponible para ese
  servicio, ej. desayuno), no para marcar habitaciones — evitar esa
  confusión de significado fue el motivo del primer intento fallido (los
  tests fallaban porque la validación de "selecciona al menos una
  habitación" filtraba por el campo equivocado). El propietario no necesita
  dar de alta "Otoch" antes del deploy — lo hace él en el admin después,
  mismo patrón que ya se usó para `rol_cotizador` en el PR #225.
  **Horas siempre en 12h con a.m./p.m.** (pedido explícito, "no queremos
  confusiones"): `core_erp/horarios.py::formato_hora_ampm()` es la fuente
  única (no usa `strftime('%I:%M %p')`, que depende del locale del
  servidor), usada por el cotizador, `ContratoService._fmt_hora` (afecta
  también Evento/Pasadía/Arrendamiento, no solo Hospedaje — corrige de paso
  un "10:00 — 19:00" en 24h que quedaba en la nota de Pasadía del
  cotizador) y el filtro de plantilla `hora_ampm` (`comercial/templatetags/
  qkt_horarios.py`) para el portal.
  **Precios siempre con IVA incluido**: mismo criterio que ya usa
  `api_paquetes_cotizador` — encontrado y corregido un bug de IVA doble en
  el primer borrador del mockup (verificado también en Playwright: 3 noches
  × $904.80 = $2,714.40 por habitación, suma exacta sin reconversión).
  `DIAS_PAGO_TOTAL['HOSPEDAJE'] = 7` (igual que Pasadía, alineado con el
  corte de 0% de reembolso de `politica_cancelacion_v2.0.md` §8) — propuesto
  por Claude, no instruido explícitamente, documentado como tal en el Issue.
  Verificado de punta a punta con navegador real (Playwright sobre
  `runserver`, no solo tests): flujo completo con 2 habitaciones × 3 noches
  crea la Cotizacion con `fecha_salida`/`noches` correctos, el portal
  muestra check-in/check-out en a.m./p.m., el calendario admin pinta el
  rango completo (`_construir_eventos_calendario`) y el contrato PDF se
  genera sin errores. Tests en `comercial/test_hospedaje.py` (30), incluida
  la exclusividad bidireccional en las 5 combinaciones (Hospedaje↔Airbnb,
  ↔Evento, ↔Pasadía, ↔Arrendamiento, ↔otro Hospedaje con traslape a mitad
  de estancia) y que una Cotizacion `BORRADOR` nunca bloquea, en ninguna
  dirección.
- 2026-08-17 — Se deja de usar Codex por completo (decisión del propietario,
  categórica en la segunda mención: "ya de plano no lo vamos a utilizar para
  nada, no me gustó, se quedó muy corto"). Ver "Planificación mediante
  GitHub Issues" arriba: Claude implementa directo tras planificar, ya no
  hay hand-off. Surgió durante el diseño de la línea de negocio Hospedaje
  (Issue #230): ese Issue se escribió bajo el flujo viejo (plan sin
  implementar, para que Codex lo tomara) y se queda como estaba de
  referencia/bitácora, pero su ejecución la hace Claude, no Codex.
- 2026-08-17 — `USE_THOUSAND_SEPARATOR = True` (settings.py) formatea con coma
  **cualquier entero** que se imprima crudo en una plantilla (`{{ anio }}` →
  "2,026"), no solo montos. El único lugar del ERP donde esto pasaba de verdad
  eran los selects de año armados a mano en `airbnb/`: tres orígenes en Python
  (`PagoAirbnbAdmin.changelist_view`, `reporte_pagos_airbnb`,
  `conciliacion_depositos_airbnb`) pasan `anio`/`año` como `int` a 4 plantillas
  (`pagoairbnb/change_list.html`, `conciliacion_depositos.html`,
  `reporte_pagos.html`, `reporte_fiscal_airbnb.html`). El daño real no era solo
  visual: en el PDF fiscal el folio salía `RPT-AIRBNB-2,0267` en vez de
  `RPT-AIRBNB-202607` — un documento fiscal con folio corrupto. El `list_filter`
  nativo de Django (ej. `ConciliacionBancariaAdmin` con `'anio'`) **no tiene este
  bug**: `AllValuesFieldListFilter.choices()` ya hace `str(val)` antes de
  renderizar, así que solo los años armados a mano con `{% for a in anios %}`
  estaban expuestos. Fix: `{{ anio|stringformat:"d" }}` en cada uno de los 8
  sitios — fuerza texto plano vía `%d` de Python, inmune a la localización de
  números; no se tocó `USE_THOUSAND_SEPARATOR` porque ahí sí se necesita para
  los montos en pesos en todo el resto del ERP. Grep de auditoría que confirma
  que no queda ninguno suelto: `grep -rnoE "\{\{[^}]*\}\}" --include=*.html . |
  grep -E "año|anio" | grep -v "stringformat\|date:"`. Verificado con Playwright
  contra el servidor real (no solo el código): el `<option>` del filtro de año
  renderiza "2026" limpio en `value` y en texto, y el PDF fiscal generado con
  WeasyPrint (extraído con `pdfplumber`, ya que `response.body()` de Playwright
  al navegar directo a un PDF devuelve el HTML del visor de Chromium, no los
  bytes — hay que usar `request.new_context()` en su lugar) trae
  `RPT-AIRBNB-202607`.
- 2026-08-17 — Mínimo a pagar por tipo de servicio y cercanía de la fecha
  (pedido del propietario tras el fix del cotizador). `monto_minimo_pago_detalle()`
  (`comercial/models.py`) exigía **50% siempre** en el primer pago; ahora
  `requiere_pago_total_detalle()` corre **antes** que el plan de pagos y que
  ese 50%, y obliga al saldo completo cuando: el servicio es
  `ARRENDAMIENTO` (siempre, sin importar los días), o faltan menos días que
  su umbral — `Cotizacion.DIAS_PAGO_TOTAL` = 15 para `EVENTO`, 7 para
  `PASADIA`; una fecha ya pasada también entra. Devuelve `saldo_pendiente()`,
  no `precio_final`: si el cliente ya abonó algo, el mínimo es lo que falta,
  no el total otra vez. **Manda sobre el plan de pagos a propósito**: un plan
  con parcialidades posteriores a la fecha del evento dejaría pasar un abono
  parcial justo cuando ya no hay margen para cobrar el resto. No hizo falta
  tocar el portal ni Openpay: `views_openpay.py::pagar_openpay` valida contra
  `monto_minimo_pago()` y `templates/portal/evento.html` prellena el input con
  ese mismo mínimo (`data-min`), así que los dos heredan la regla. **Bug
  preexistente que había que corregir para que la regla funcionara**:
  `cotizador_enviar` **nunca guardaba `tipo_servicio`** al construir la
  `Cotizacion`, así que toda solicitud del cotizador web quedaba como
  `EVENTO` (el default del campo) — la pasadía nunca habría usado su umbral
  de 7 días, y de paso los descuentos acotados por `tipos_servicio`
  (`services_descuentos.py:90`) se evaluaban contra el tipo equivocado desde
  siempre. De paso, `CotizadorEnviarForm.servicio` pasa de `CharField(max_length=20)`
  a `ChoiceField` con los tres códigos: ese valor ahora se guarda tal cual en
  un campo de `max_length=15`, y un string libre de 20 caracteres habría
  reventado con `DataError` en Postgres. Verificado renderizando el portal
  real (no solo el método): evento a 60 días → `data-min` 5,800 de 11,600;
  evento a 14 días, pasadía a 6 días y arrendamiento a 200 días → 11,600.
  Tests en `comercial/test_monto_minimo_pago.py` (20).
- 2026-08-17 — Cotizador público: la línea base del servicio (el
  arrendamiento de la quinta) se agrega sola y ya no depende de cómo esté
  escrito el nombre del producto. **El bug real reportado por el
  propietario**: una solicitud de PASADÍA creaba la cotización **en cero**.
  `_lineas_cotizador()` buscaba el producto con
  `_buscar_producto_por_nombre('Pastadía')` (typo con **t**, presente
  también en `nombre_evento`) y como fallback `'Pasadia'` sin acento — pero
  `nombre__icontains` se traduce a un **LIKE, insensible a mayúsculas y
  SENSIBLE a acentos** en SQLite y en PostgreSQL, así que ninguna de las dos
  encontraba el producto real, `'Paquete Pasadía QKT'`, y `_agregar_item()`
  se salía en silencio con su `if not producto: return None`. Confirmado
  contra el catálogo real del admin. **Lo que había detrás y explica el
  resto de los síntomas**: ni `Paquete Esencial QKT` ni `Paquete Pasadía
  QKT` tienen `es_paquete=True` (los dos salen como SIMPLE en el admin), así
  que `api_paquetes_cotizador` —que filtra `es_paquete=True`— devolvía
  **cero** paquetes para los dos servicios: el camino "Elegir paquete" era
  un callejón sin salida que dejaba seguir sin seleccionar nada, y al mismo
  tiempo `api_productos_cotizador` —que solo excluía `es_paquete=True`— los
  ofrecía **como extras**, con lo que un cliente podía marcarlos y pagarlos
  dos veces. La solución no es más búsqueda por nombre: `Producto` gana
  `rol_cotizador` (`BASE_EVENTO`/`BASE_PASADIA`/`HORA_EXTRA`, migración
  0072), que es la fuente de verdad; la búsqueda por nombre —ahora sin
  acentos, `comercial/roles_cotizador.py::normalizar`— queda solo como red
  de seguridad, y si no aparece por ninguna vía se emite un
  `logger.warning` en vez de crear la cotización vacía en silencio. Los
  productos con rol quedan fuera de los extras (`api_productos_cotizador`)
  **y** se filtran otra vez al componer las líneas, porque los `extras_ids`
  llegan del cliente y nada impide mandarlos a mano. La migración siembra
  los tres roles por nombre normalizado, así que producción no necesita que
  nadie marque nada en el admin tras el deploy (el helper vive en
  `comercial/roles_cotizador.py` y lo comparten migración, vistas y tests;
  si cambiara, lo peor que pasa es que una BD nueva arranque sin marcar,
  el campo es opcional). En el front: la pasadía **ya no muestra la
  bifurcación paquete/personalizado** —no tiene paquetes que elegir y su
  base va siempre—, entra directo a los extras con una nota de lo que ya
  incluye; para evento/arrendamiento, si `api_paquetes_cotizador` devuelve
  cero paquetes se cae solo a personalizado en vez de dejar el callejón sin
  salida. `api_total_cotizador` ahora devuelve `conceptos` (las mismas
  descripciones que se van a cobrar) y el resumen las lista: el cliente ve
  la línea base que él no marcó. De paso: horario/horas de la pasadía y las
  6 horas base del evento pasan a constantes
  (`HORA_INICIO_PASADIA`/`HORA_FIN_PASADIA`/`HORAS_PASADIA`/`HORAS_BASE_EVENTO`)
  en vez de literales repartidos, `api_total_cotizador` fuerza 9 horas para
  pasadía (antes un `horas=99` en la query string le habría exhibido horas
  extra que la cotización real no cobra), el `tipo` de evento viaja al
  cálculo del total acotado a `TIPO_EVENTO_CHOICES` para que el concepto
  exhibido diga lo mismo que el que se guarda, y se corrigió el docstring de
  `api_paquetes_cotizador`, que prometía un filtro por número de personas
  que **no existe** (`Producto` no tiene rango de personas; el parámetro
  `personas` que manda el navegador se ignora — es el hueco que ya estaba
  documentado en la entrada de las órdenes 29-30). Verificado con navegador
  real (Playwright sobre el `runserver`, no solo tests): pasadía sin
  seleccionar nada llega al resumen con **$1,500.00** y el concepto
  "Paquete Pasadía QKT (11 Pax, 10:00-19:00)", y un evento de 8 horas con
  $6,496.00 = Paquete Esencial + 2 horas extra, cotización creada en la BD
  con esos mismos items. Tests en `comercial/test_cotizador_lineas.py` (18).
- 2026-08-21 — Orden 42 (`SEC-AUTHN-002`) mergeada a `main` tras revisión y
  prueba real del propietario (ver entrada del 2026-08-17 más abajo para el
  detalle técnico completo — esta entrada solo cierra lo que quedaba
  pendiente ahí). Verificado en un ambiente de Railway aislado (duplicado
  de `production`, apuntado a esta rama, borrado después de la prueba): el
  QR de `/admin/2fa/activar/` escaneó correctamente con una app de
  autenticación real, el código de 6 dígitos se aceptó, y un segundo login
  completo (usuario+contraseña+código TOTP) funcionó — el riesgo que
  mantenía el PR en draft (nunca se había visto el flujo de escaneo real en
  un navegador) queda cerrado. De paso, la sección del admin de `django_otp`
  se renombra a "Autenticación (2FA)" y se ordena justo después de
  Usuarios/Grupos (`core_erp/apps.py::QktAuthConfig.ready()`, vía
  `apps.get_app_config('otp_totp').verbose_name = ...`), para que quede
  visualmente junto a "Autenticación y Usuarios" en vez de su nombre
  autogenerado "Otp_Totp". **Intento descartado antes de esto**: un proxy
  de `TOTPDevice` con `Meta.app_label='auth'` (para fusionarlo en la misma
  sección, no solo ponerlo al lado) — `makemigrations --check --dry-run` lo
  detecta como cambio pendiente y Django solo sabe escribir esa migración
  dentro de `django/contrib/auth/migrations/` (resuelve la carpeta de
  migraciones por `app_label`, no por dónde se define la clase), que es
  parte del paquete instalado de Django, no del repo — inviable sin
  vendorizar migraciones de Django o redirigir `MIGRATION_MODULES['auth']`
  entero (arriesgar las migraciones reales de `auth` por una mejora
  cosmética de menú). El renombrado de sección logra el mismo resultado
  visual práctico (agrupado junto a Auth) sin ninguno de esos riesgos.
  Orden 52 (`SEC-DOC-001`) también resuelta el mismo día:
  ver la entrada original de abajo para el detalle del runbook; los 3
  `[CONFIRMAR:]` los respondió el propietario directamente (canal de aviso:
  `pereiraelian18@gmail.com`/9992689400 a Elián Pereira; umbral de aviso a
  clientes: solo datos fiscales o de pago; quién redacta/aprueba: Elián
  Pereira) y quedaron escritos en `docs/security/RUNBOOK_INCIDENTES.md`.
- 2026-08-17 — Orden 52 del backlog de seguridad (`SEC-DOC-001`),
  `docs/security/RUNBOOK_INCIDENTES.md` — **borrador de Dev al momento de
  escribir esta entrada** (ver arriba, 2026-08-21, para el cierre real con
  las respuestas del propietario): la propia orden lo marca `Propietario +
  Dev`, así que se escribió con todo lo que Dev puede definir desde el
  código y la infraestructura ya documentada en el repo, y se dejaron
  exactamente 3 puntos con `[CONFIRMAR:]` en vez de inventar una respuesta:
  a quién avisar y por qué canal (bloqueado por la orden 13, `NV-07`, que
  sigue sin resolver), el umbral de severidad para notificar a clientes, y
  quién redacta/aprueba ese aviso — son decisiones de negocio, no huecos
  técnicos. El contenido
  que sí se pudo escribir con certeza salió de grepear el propio repo, no
  de una plantilla genérica de runbook: la tabla de rotación de
  credenciales (§5) lista las 10 credenciales reales con su variable
  exacta de Railway (`OPENPAY_PRIVATE_KEY`, `WA_CLOUD_API_TOKEN`,
  `CLOUDFLARE_R2_PRIVATE_ACCESS_KEY_ID`, etc., sacadas de los `config(...)`
  de `settings.py`) y el efecto colateral real de rotar cada una — no una
  lista de "credenciales típicas". El paso de "revocar todas las sesiones
  activas" (§3.1) tuvo que investigarse porque no es obvio en Django:
  `manage.py clearsessions` **no sirve** para esto (solo borra sesiones ya
  expiradas), hace falta `Session.objects.all().delete()` directo sobre la
  tabla `django_session` (el `SESSION_ENGINE` de este proyecto es el
  default de Django, backend de base de datos — confirmado que no hay
  override en `settings.py`) o, más agresivo, rotar `SECRET_KEY` en
  Railway (invalida la firma de todas las cookies de sesión existentes sin
  tocar la base de datos). El documento referencia activamente el propio
  historial de incidentes reales del proyecto en vez de hablar en
  abstracto: el feed iCal sin `ICAL_PUBLIC_TOKEN` (orden 5) como ejemplo
  de contención "definir una variable en Railway sin esperar deploy", y la
  auditoría de secretos en el historial completo de git (`gitleaks
  --log-opts="--all"`, orden 33) como el proceso ya existente para medir
  el alcance real de un secreto commiteado. También apunta explícitamente
  a la orden 42 (TOTP): si esa orden ya está mergeada al momento de un
  incidente de cuenta comprometida, borrar solo la cuenta o su contraseña
  no basta —hay que borrar también el `TOTPDevice` de esa cuenta, o un
  atacante que recupere la contraseña vieja más adelante seguiría teniendo
  un segundo factor válido—.
- 2026-08-17 — Orden 42 del backlog de seguridad (`SEC-AUTHN-002`), **código
  y tests listos pero SIN mergear a propósito** — es el único de todo este
  backlog donde se rompió el patrón "implementar → validar → mergear" de
  las demás órdenes, deliberadamente. `django-otp` (`django_otp`,
  `django_otp.plugins.otp_totp`) + `SuperuserTOTPGateMiddleware`
  (`core_erp/middleware.py`, justo después de
  `django_otp.middleware.OTPMiddleware` en `MIDDLEWARE`): cualquier
  superusuario autenticado (`is_superuser`, no `is_staff` a secas — Ventas/
  Contabilidad/Nómina quedan fuera, tal como pide la orden) cuya sesión no
  haya pasado por `django_otp.login()` es redirigido antes de llegar a
  *cualquier* vista de `/admin/` — a `totp_activar_view`
  (`core_erp/views_totp.py`) si no tiene ningún `TOTPDevice` confirmado
  (genera uno nuevo sin confirmar, muestra el QR con `qrcode` + el secreto
  en texto para alta manual, y solo lo confirma tras un código válido), o a
  `totp_verificar_view` si ya tiene uno (solo pide el código). Ambas rutas
  (`/admin/2fa/activar/`, `/admin/2fa/verificar/`) están en la lista de
  exención del middleware junto con `/admin/logout/`, y van *antes* de
  `path('admin/', admin.site.urls)` en `urls.py` para que el resolver las
  encuentre primero. **Por qué es auto-servicio y no requiere que alguien
  provisione el dispositivo de antemano**: la alternativa (bloquear sin
  ruta de alta) dejaría a Dirección fuera de su propio ERP en el primer
  login tras el deploy — el criterio de aceptación del backlog ("no
  completa el login sin dispositivo") se cumple igual porque un
  superusuario sin TOTP nunca llega a ninguna vista real de negocio, solo
  a la pantalla de alta, que es el patrón estándar de cualquier MFA
  obligatorio (Google, GitHub, AWS funcionan igual). **Error de API
  encontrado explorando `django-otp` antes de escribir el middleware**:
  `django_otp.devices_for_user()` no devuelve un queryset (`.exists()`
  revienta con `AttributeError: 'generator' object has no attribute
  'exists'`) — es un generador (`yield from` sobre cada modelo de
  dispositivo registrado), hay que consumirlo con `any(...)`. **El
  hallazgo real de esta orden, y la razón de no mergearla sin más**: la
  primera corrida de la suite completa tras activar el gate rompió **45
  tests + 7 errores** en 13 clases repartidas en 11 archivos de 6 apps
  (`contabilidad`, `comercial`, `airbnb`, `facturacion`, `legal`,
  `core_erp`) — todo test que hacía `force_login(superusuario)` y luego
  pegaba contra una URL de `/admin/` (que es la enorme mayoría de los
  tests de acciones de admin de este backlog, incluidas casi todas las
  órdenes 48 y 14-18) dejó de recibir 200 y empezó a recibir un 302 al
  gate. Nada de eso era un bug: es exactamente el comportamiento que pide
  la orden, aplicado también a `force_login`, que crea una sesión
  autenticada real — igual que la tendría un atacante con una cookie de
  sesión robada, el escenario exacto que la orden busca cerrar. La
  corrección no fue relajar el middleware: se agregó
  `core_erp/test_utils.py::login_superuser_con_totp(client, usuario)` —
  crea (o reutiliza) un `TOTPDevice` confirmado y escribe
  `DEVICE_ID_SESSION_KEY` (`'otp_device_id'`) directo en la sesión del
  test client, replicando lo que hace `django_otp.login()` sin tener que
  simular el flujo real de QR en cada test — y se reemplazó
  `force_login()` por este helper en los 13 sitios reales que lo
  necesitaban (uno de ellos, `PermisosSuperusuarioTest` en
  `comercial/test_permisos_grupos.py`, no había aparecido en el primer
  barrido de errores de la suite y se encontró en una segunda auditoría
  manual antes de dar por cerrada la lista — corregido igual, preventivo).
  Suite completa corrida de nuevo tras el fix: 655/655 verdes (641 previos
  + 14 nuevos de `core_erp/test_totp.py`), `ruff check .`,
  `manage.py check` y `manage.py check --deploy --fail-level WARNING`
  (con `DEBUG=False`, replicando el job `security` de CI) limpios.
  **Por qué se documenta esto en vez de simplemente mergear como las
  demás 15 órdenes de esta sesión**: el propio blast radius descubierto
  —45+7 fallos con solo activar el gate, en tests que ya llevaban meses en
  el repo— es la prueba de que este es el cambio de mayor alcance real de
  todo el backlog sobre el flujo de login, la única superficie que **toda
  persona con acceso al ERP** usa en cada sesión de trabajo, y afecta
  directamente a la cuenta de mayor privilegio (Dirección). Esta sesión no
  tiene forma de probar interactivamente el flujo real de escaneo de QR
  con una app de autenticación de verdad (Google Authenticator, Authy) en
  un navegador — solo la mecánica server-side vía el test client. El
  riesgo concreto si algo estuviera mal y se desplegara sin aviso: la
  cuenta de Dirección queda fuera de `/admin/` en el primer login tras el
  deploy hasta completar el alta (que sí debería funcionar según el
  diseño y los tests, pero nadie lo ha visto funcionar en un navegador
  real). Por eso el PR de esta orden se deja fuera del patrón de
  auto-mergear apenas CI está en verde que se siguió en el resto de esta
  sesión: queda abierto, con el backlog marcado ⚠️ (no ✅), a la espera de
  que el propietario lo revise —y probablemente lo pruebe en un entorno
  de verdad— antes de mergear a `main`.
- 2026-08-17 — Orden 51 del backlog de seguridad (`SEC-TEST-001`):
  `core_erp/test_regresion_seguridad.py`. **No se reinventó lo que ya
  existía**: antes de escribir una sola línea se auditó qué órdenes 1-18
  ya tenían test dedicado (grep de los IDs `SEC-XXX` contra `**/test*.py`)
  — las cinco categorías que pide la orden (autorización cruzada, XSS,
  CSRF, cabeceras, expiración de sesión) resultaron **casi todas** ya
  cubiertas por tests escritos junto con cada corrección: XSS
  (`airbnb/test_seguridad.py::CalendarioAdminXssTest`), autorización
  cruzada (`comercial/test_permisos_grupos.py`, órdenes 14-18), CSRF
  (`comercial/test_cotizador_seguridad.py`), y la mitad de "cabeceras"
  (`core_erp/test_referrer_policy.py`, `core_erp/test_middleware.py`). El
  archivo nuevo abre con un índice explícito de dónde vive cada uno —para
  que "¿qué prueba SEC-AUTHZ-001c?" tenga una respuesta de un solo lugar,
  sin tener que grepear IDs por el repo cada vez— y **solo añade** tests a
  los dos huecos reales que esa auditoría encontró. **(1) Cabeceras**:
  `PublicSecurityHeadersMiddleware` (`core_erp/middleware.py`, CSP +
  Permissions-Policy en páginas públicas) no tenía ningún test —una
  regresión que quitara la CSP bloqueante de `/`, `/cotizar/*` o `/api/*`
  habría pasado inadvertida—; tests nuevos cubren la CSP bloqueante por
  default, el flag `PUBLIC_CSP_REPORT_ONLY` cambiando a la cabecera
  Report-Only, `PUBLIC_CSP_ENABLED=False` no mandando ninguna, y
  Permissions-Policy en toda respuesta (pública y de `/admin/`). **(2)
  Expiración de sesión**: cero tests sobre `SESSION_COOKIE_AGE`/
  `SESSION_IDLE_TIMEOUT`/`SESSION_SAVE_EVERY_REQUEST`/
  `SESSION_EXPIRE_AT_BROWSER_CLOSE` (`settings.py`) pese a que el propio
  comentario del archivo documenta la intención exacta (idle timeout de
  30 min, no expiración absoluta). **Error descubierto escribiendo el
  primer intento de test**: comprobar el `max-age` de la cookie
  `sessionid` de la respuesta falla con `ValueError` al hacer
  `int('')` — no es un bug del test, es que `SESSION_EXPIRE_AT_BROWSER_CLOSE
  = True` hace que Django mande la cookie **sin** `max-age`/`Expires`
  (cookie de sesión de navegador, a propósito, documentado en los propios
  docs de Django: "browser-length cookie"), así que el timeout de
  inactividad real no vive en la cookie sino del lado del servidor. El
  test correcto usa `self.client.session.get_expiry_age()` (que sí lee
  `SESSION_COOKIE_AGE` internamente) en vez de parsear la cookie. También
  se probó el invariante `SESSION_COOKIE_AGE == SESSION_IDLE_TIMEOUT`
  (nunca deberían desalinearse, son la misma variable por diseño) y que
  `core_erp/context_processors.py::session_idle` —el valor que lee el
  auto-logout de JS en el navegador— reporta exactamente los mismos
  minutos que el backend, para que aviso y expiración real no se
  desincronicen. Suite completa corrida dos veces (localmente antes del
  PR y en CI) para confirmar que los 9 tests nuevos son deterministas, no
  solo que pasan una vez.
- 2026-08-17 — Orden 48 del backlog de seguridad (`SEC-BIZ-002`):
  `confirmar_accion_destructiva` (`core_erp/admin_utils.py`) envuelve una
  acción de admin (`def accion(self, request, queryset)`) con una página de
  confirmación intermedia — **mismo patrón que el propio `delete_selected`
  de Django**, no uno inventado: la selección viaja en campos ocultos
  (`_selected_action` por cada pk, más `action` con el nombre) y el
  decorador solo deja pasar a la función real si el POST de vuelta trae
  `confirmar=si`. La plantilla compartida
  (`templates/admin/confirmar_accion_destructiva.html`) es una versión
  simplificada de `admin/delete_selected_confirmation.html` (la de Django,
  leída directo del paquete instalado para replicar el patrón exacto:
  `ModelAdmin.response_action()` ya sabe devolver directo cualquier
  `HttpResponseBase` que la acción retorne en vez de redirigir al
  changelist — por eso una `TemplateResponse` desde dentro de la acción
  funciona sin tocar nada más). **Por qué server-side y no un simple
  `confirm()` de JavaScript** (que ya existía como patrón suelto en
  `cerrar_historico_contable.html`): el riesgo que describe la orden es
  "una sesión secuestrada opera sin fricción" — un atacante con la cookie
  de sesión (no necesariamente con acceso al navegador real) puede
  scriptear un POST directo saltándose cualquier `onclick`/`confirm()` de
  JS, que nunca llega a ejecutarse fuera de un navegador real. El gate de
  servidor exige un segundo viaje de ida y vuelta real (con su propio CSRF
  token), que sí sube el costo de automatizar el ataque. Se aplicó a las
  10 acciones de mayor impacto identificadas en una auditoría dirigida del
  repo (no solo las que "sonaban" destructivas): `aplicar_polizas`,
  `cancelar_polizas`, `aprobar_regularizacion` y `aplicar_saldo` en
  `contabilidad/admin.py`; `regenerar_token`, `registrar_reembolso`,
  `reembolsar_en_openpay` y `borrar_transacciones_de_prueba` en
  `comercial/admin.py`; `marcar_canceladas` en `facturacion/admin.py`;
  `publicar_version` en `legal/admin.py`. **No se tocaron** el
  `delete_selected` estándar de Django (ya trae su propia confirmación de
  fábrica) ni las acciones de menor impacto que la misma auditoría separó
  aparte (`marcar_como_pagado` de nómina, `marcar_resuelto`/
  `marcar_ignorado` de conflictos de calendario en airbnb,
  `desactivar_sin_archivo` de imágenes de landing): son reversibles o de
  bajo riesgo, y añadir fricción ahí sería alcance no pedido. **Detalle
  que rompió tests existentes, no un bug del feature**: varios tests ya
  posteaban directo a estas acciones esperando que se ejecutaran de
  inmediato (`test_seguridad_portal.py`, `test_limpiar_transacciones_
  openpay.py`, `contabilidad/tests.py`) — se les agregó `'confirmar':
  'si'` al payload del POST, exactamente lo que ahora hace falta para
  pasar el gate; no cambió ninguna aserción de fondo, cada test sigue
  probando lo mismo que antes. Test nuevo por cada acción envuelta
  confirmando las dos mitades del contrato: un POST sin `confirmar=si` no
  cambia nada en la BD y muestra la página de confirmación, y un segundo
  POST con `confirmar=si` sí ejecuta — más una prueba de la mecánica del
  decorador en aislado (`core_erp/test_admin_utils.py`, sin tocar la base
  de datos ni un modelo real) que nombra el escenario del backlog
  explícitamente: `test_un_post_directo_sin_pasar_por_la_confirmacion_
  no_tiene_efecto`. `reembolsar_en_openpay` (el que mueve dinero real
  contra la API de Openpay) se probó igual sin necesidad de mockear la
  API: el gate intercepta *antes* de que la función original —la que
  llamaría a `reembolsar_cargo_openpay`— se ejecute siquiera, así que
  probar "sin confirmar no pasa nada" no requiere simular la respuesta de
  Openpay en absoluto.
- 2026-08-17 — Remitente de email por tipo (Issue #221): dominio propio
  `quintakooxtanil.com` verificado en Brevo (SPF/DKIM) y `reservas@`/
  `pagos@`/`notificaciones@quintakooxtanil.com` dados de alta como
  remitentes, con Cloudflare Email Routing reenviando las respuestas al
  Gmail del negocio. `comunicacion/services.py::remitente_por_tipo()`
  centraliza el mapeo: `COTIZACION` → `EMAIL_FROM_RESERVAS`;
  `CONFIRMACION_PAGO`/`REEMBOLSO`/`RECORDATORIO_PAGO` → `EMAIL_FROM_PAGOS`;
  cualquier otro tipo (incluido `'OTRO'`, que son siempre alertas internas,
  nunca al cliente) cae a `EMAIL_FROM_NOTIFICACIONES` por default explícito
  del `dict.get()`, así que un tipo nuevo que se agregue a
  `ComunicacionCliente.TIPO_CHOICES` sin tocar este mapeo no queda sin
  remitente. El email al contador en `facturacion/admin.py::enviar_email_view()`
  también usa `EMAIL_FROM_NOTIFICACIONES` — es correo interno/operativo
  aunque el asunto diga "factura", el cliente nunca lo recibe. Las tres
  variables nuevas caen a `DEFAULT_FROM_EMAIL` si no están configuradas, a
  propósito: el código se desplegó antes de que Codex pudiera implementarlo
  (se quedó sin crédito en OpenAI, `stream disconnected ... no credits
  remaining` en el job `ai-implement.yml` del Issue #221 — lo implementé yo
  directo, a pedido explícito del propietario, saltándose el flujo normal
  de planificar-en-Issue-y-dejar-que-Codex-implemente), así que el
  fallback evita que production mande correos con un remitente no
  autenticado si las variables de Railway se configuran en el orden
  incorrecto.

- 2026-08-17 — Orden 50 del backlog de seguridad (`SEC-XSS-003`):
  `DocumentoLegal.render_html()` (`legal/models.py`) sanitiza con `nh3`
  antes de devolver el HTML que `legal/documento.html:155` sirve con
  `{{ cuerpo|safe }}` a un público **no autenticado**
  (`/aviso-de-privacidad/`, `/terminos-y-condiciones/`,
  `/politica-de-cancelacion/`). **Por qué hacía falta pese a que
  `contenido_md` hoy solo lo edita un superusuario**: `python-markdown`
  no tiene `safe_mode` desde hace varias versiones — cualquier HTML crudo
  embebido en el Markdown fuente (`<script>...`) pasa intacto al HTML de
  salida — y la extensión `attr_list` (ya activa, para poder anclar ids a
  encabezados) permite adjuntar **atributos arbitrarios** a cualquier
  elemento con la sintaxis `{: onclick="..."}`, incluidos manejadores de
  evento. Es defensa en profundidad ante una cuenta comprometida con
  acceso al admin de `legal`, no una vulnerabilidad hoy explotable por
  alguien sin esas credenciales. Se eligió `nh3` (bindings de Python sobre
  `ammonia`, el sanitizador de Mozilla en Rust) en vez de `bleach`: cero
  dependencias transitivas (`bleach` arrastra `html5lib`/`six`/
  `webencodings`), activamente mantenido, y su lista blanca **por
  defecto** ya cubre exactamente las etiquetas que estos documentos usan
  (`h1`-`h6`, `p`, `strong`, `em`, `a`, `ul`/`ol`/`li`, `table` y afines,
  `blockquote`, `code`, `hr`) sin tener que declarar una lista propia —
  confirmado corriendo `nh3.clean()` contra la salida real de
  `markdown.markdown()` para los tres documentos ya publicados
  (aviso de privacidad, términos, política de cancelación) sin que se
  perdiera ninguna etiqueta legítima. Como beneficio adicional (no
  buscado, pero verificado), `nh3` por defecto añade
  `rel="noopener noreferrer"` a los enlaces que sanitiza. Tests nuevos en
  `legal/tests/test_legal.py::RenderHtmlSanitizadoTest`: `<script>`
  embebido en el Markdown fuente se elimina, un manejador de evento
  inyectado vía `attr_list` (`{: onclick="..."}`) se elimina, una URL
  `javascript:` en un enlace se elimina, las etiquetas legítimas
  (encabezados, negritas, enlaces, tablas) sobreviven intactas, y la
  página pública real no sirve el payload inyectado. **Detalle del test
  de la página pública**: no se puede afirmar "la respuesta no contiene
  `<script`" a secas — la propia plantilla `documento.html` trae un
  `<script>` legítimo al final (el que envuelve las tablas anchas para
  scroll en móvil) — el test verifica que el payload específico
  inyectado (`alert('xss-inyectado')`) no sobreviva, no la ausencia total
  de la etiqueta.
- 2026-08-17 — Orden 47 del backlog de seguridad (`SEC-BIZ-001`, replay del
  webhook de Openpay): **ya estaba cubierta por código preexistente, sin
  relación con esta sesión de seguridad** — mismo patrón que las órdenes 41
  y 49, donde la premisa del backlog no reproducía tal cual contra el
  código real. `OpenpayTransaccion.openpay_id` ya es `unique=True,
  db_index=True` (comercial/models.py:1939) y `procesar_webhook_openpay()`
  ya corta en seco con `if registro.procesado: return registro`
  (comercial/services_openpay.py:709) antes de generar ningún efecto
  adicional. El propio `AUDITORIA_SEGURIDAD.md` (que originó este backlog)
  ya lo reconocía explícitamente en el hallazgo original: "la idempotencia
  por `openpay_id` ... hace que un reenvío converja al mismo estado, que es
  la mitigación efectiva. Se documenta como riesgo residual, no como
  vulnerabilidad explotable con el diseño actual." Y ya existía un test que
  prueba el criterio de aceptación literal del backlog ("un payload
  repetido no genera efectos adicionales") con conteos antes/después, no
  solo un chequeo de estado:
  `comercial/test_openpay.py::ProcesarWebhookIdempotenciaTest::test_no_duplica_pago_con_mismo_openpay_id`
  manda el mismo payload dos veces seguidas (`# simula reintento de
  Openpay`) y verifica `OpenpayTransaccion.objects.filter(...).count() ==
  1` y `Pago.objects.filter(...).count() == 1`. **Por qué no hace falta
  `transaction.atomic()` extra alrededor del `get_or_create`**: el
  `get_or_create()` de Django ya envuelve internamente su propio `create()`
  en un `atomic()` y, si el `INSERT` choca contra el `unique=True` por una
  petición concurrente (dos entregas del mismo webhook llegando a la vez a
  dos workers de gunicorn), atrapa el `IntegrityError` y vuelve a consultar
  la fila que ganó la carrera — no hay ventana real de duplicado ni con
  Postgres en producción. Se descartó ir más allá (ej. guardar un
  "identificador de evento" separado del id de transacción): Openpay no
  expone en sus payloads reales un id de evento distinto al de la
  transacción (confirmado contra los payloads reales ya documentados en
  este archivo para el evento `VERIFICATION`), así que "identificador de
  evento" y "openpay_id" son la misma cosa en este proveedor — no hay un
  campo adicional que registrar. Sin cambios de código; solo se cerró en
  `docs/security/BACKLOG_SEGURIDAD.md` con la referencia al test que ya
  cubre el criterio de aceptación.
- 2026-08-17 — Orden 45 del backlog de seguridad (`SEC-LOG-002`):
  `CorrelationIdMiddleware` (`core_erp/middleware.py`), primero en
  `MIDDLEWARE` — así cubre el log de cualquier middleware/vista/señal
  posterior, incluido `SecurityMiddleware`. Genera un ID corto
  (`uuid4().hex[:12]`) por request y **nunca confía en uno que mande el
  cliente**: aceptar `X-Correlation-ID` de entrada permitiría inyectar
  valores arbitrarios en el log o que un cliente reutilice el mismo ID
  entre peticiones distintas para confundir la correlación. Se guarda en
  un `contextvars.ContextVar` (no `threading.local()`): con gunicorn en
  modo sync (WSGI) da igual, pero es la forma correcta si el proyecto
  adopta async más adelante, donde varias tareas pueden compartir hilo
  sin heredarse el contexto entre sí. El `.set()`/`.reset(token)` va en
  `try/finally` alrededor de `get_response()` para no dejar el ID de un
  request filtrándose al siguiente que atienda el mismo worker. Un
  `logging.Filter` (`CorrelationIdFilter`, cableado en
  `LOGGING['handlers']['console']['filters']`) inyecta `record.
  correlation_id` leyendo el mismo `ContextVar` — así cualquier código
  que solo tiene acceso a un logger (señales, servicios, sin `request` a
  la mano) también queda agrupado, no solo lo que loguean las vistas.
  **Bug preexistente encontrado de paso, invisible hasta este cambio**:
  al añadir un formatter propio (`con_correlation_id`) al handler
  `console`, las líneas de log empezaron a salir **duplicadas** — una con
  el formato nuevo y otra sin formatear. Causa: `dj_database_url.config()`
  (llamado a nivel de módulo en `settings.py`, antes de que Django
  aplique su propio `LOGGING` vía `dictConfig()`) hace un
  `logging.warning()` de nivel de módulo cuando no hay `DATABASE_URL`
  —cierto en dev sin Postgres—, y ese `logging.warning()` suelto dispara
  el `basicConfig()` implícito de Python, que deja un `StreamHandler` sin
  formatter propio pegado directo al logger **raíz**. Antes de este
  cambio el duplicado ya existía pero era invisible: el handler de
  `'django'` tampoco tenía formatter custom, así que las dos copias se
  veían idénticas. Se corrigió con `'propagate': False` en la entrada
  `'django'` de `LOGGING['loggers']` (ya lo tenía `'django.security'`
  desde la orden 36) — sin eso, cualquier mensaje que llegue a `'django'`
  sigue subiendo al logger raíz sin importar cuántos handlers propios
  tenga. Verificado con `runserver` real (`curl -sSI` contra el server:
  la cabecera `X-Correlation-ID` aparece, y el log dejó de duplicarse).
  **Detalle de `runserver` que no es bug**: la línea de acceso
  `django.server: "GET / HTTP/1.1" 200 ...` la emite
  `WSGIRequestHandler.log_message()` **después** de que la respuesta ya
  se generó y el `try/finally` del middleware ya hizo `reset()` — esa
  línea en particular sale con `[-]` en vez de un ID real. Es un
  artefacto propio de `runserver` (gunicorn en producción no pasa su
  access log por el módulo `logging` de Python); el log que sí importa
  —el que se emite **durante** el procesamiento del request, como el 403
  de `AuthorizationAuditMiddleware`— sí lleva el ID correcto, confirmado
  en el test nuevo. **Test no trivial**: `assertLogs()` de Django instala
  su propio handler temporal en el logger y **no aplica los filtros del
  handler real** (`filters` vive en el handler, no en el logger), así que
  `logs.records[0].correlation_id` con `assertLogs` normal lanza
  `AttributeError` — no es que el filtro no funcione, es que el test no
  lo estaba ejercitando. Se probó en su lugar con un `logging.Handler` de
  prueba propio, con `CorrelationIdFilter()` añadido a mano
  (`core_erp/test_middleware.py::CorrelationIdMiddlewareTest`), que sí
  replica el cableado real de producción (filtro a nivel de handler) y
  compara el `correlation_id` del registro capturado contra la cabecera
  `X-Correlation-ID` de la respuesta.
- 2026-08-16 — Orden 44 del backlog de seguridad (`SEC-DOS-001`):
  `calendario_unificado` consultaba **todo** el histórico de
  cotizaciones/reservas/asignaciones en cada carga de página, sin ningún
  filtro de fecha. FullCalendar además recibía todo embebido de una vez
  vía `|json_script`, así que la página crecía sin límite conforme
  crecía el histórico. Se separó en dos vistas: `calendario_unificado`
  (la página, ya sin datos de eventos) y `calendario_unificado_eventos`
  (endpoint JSON nuevo, `admin/airbnb/calendario/eventos/`) que exige
  `start`/`end` (YYYY-MM-DD, fin exclusivo) y responde 400 sin ellos —
  nunca cae de vuelta a "traer todo" por default. FullCalendar pide ese
  endpoint vía `events: function(fetchInfo, ...)`, con el rango que
  tenga visible en cada momento (mes/semana actual, o el que se navegue
  después), en vez de recibir el array completo al cargar. Las
  reservas de Airbnb se filtran por **traslape** de rango
  (`fecha_inicio__lt=fin, fecha_fin__gte=inicio`), no por fecha de
  inicio exacta, para no perder una reserva larga que empezó antes del
  rango pedido pero sigue vigente dentro de él. **Efecto colateral
  bueno en seguridad**: el vector de XSS original (SEC-XSS-001, un
  `</script>` en el nombre de cliente/evento rompiendo el bloque
  `<script>` de la página) deja de aplicar a los datos de eventos por
  completo — ya no viajan embebidos en HTML en ningún momento, solo
  como respuesta `application/json` de una API que el navegador nunca
  interpreta como marcado ejecutable. Los 3 tests de
  `CalendarioAdminXssTest` en `airbnb/test_seguridad.py` se
  reescribieron para probar el endpoint nuevo en vez del HTML de la
  página (que ya no tiene nada que probar ahí); se añadió
  `CalendarioEventosRangoTest` para confirmar que un evento **fuera**
  del rango pedido no se incluye (no basta con que "funcione", tiene
  que filtrar de verdad) y que falta `start`/`end` da 400.
- 2026-08-16 — Orden 43 del backlog de seguridad (`SEC-CFG-004`): usuario
  de sistema `appuser` sin privilegios en el `Dockerfile`. Orden de las
  capas importa: `chown -R appuser:appuser /app` va **después** de
  `COPY . .` y de `collectstatic` (ambos corren como root y dejan archivos
  de root, incluida `STATIC_ROOT=/app/staticfiles`), y `USER appuser` va
  al final, justo antes del `CMD` — así el proceso de gunicorn arranca sin
  privilegios pero puede leer todo lo que necesita. `--system` en
  `useradd`/`groupadd` evita crear un usuario interactivo (home, shell de
  login) que no hace falta en un contenedor. **No se pudo validar con un
  `docker build` real**: el daemon de Docker sí pudo arrancarse en este
  entorno (`dockerd` manual, algo que en sesiones anteriores no era
  posible), pero las descargas de capas desde `production.cloudfront.docker.com`
  (el CDN de Docker Hub) devuelven `403 Forbidden` a través del proxy de
  la sesión — mismo patrón que bloquea `api.github.com` para repos fuera
  del scope de esta sesión. Validado en su lugar simulando la secuencia
  `chown -R` + `useradd --system` + lectura del archivo por el usuario sin
  privilegios directamente en este filesystem (fuera de Docker): confirma
  que el usuario nuevo puede leer archivos que antes pertenecían a root
  tras el `chown`. El puerto de gunicorn (`${PORT:-8080}`) es un puerto
  sin privilegios (>1024), así que no hace falta ninguna capability extra
  para que `appuser` pueda hacer bind.
- 2026-08-16 — Orden 46 del backlog de seguridad (`SEC-CI-002`):
  `actions/checkout@v4` y `actions/setup-python@v5` en `ci.yml` fijadas
  por SHA (`11d5960a326750d5838078e36cf38b85af677262` y
  `a26af69be951a213d495a4c3e4e4022e16d87065` respectivamente, con el tag
  original como comentario `# v4`/`# v5` — mismo patrón que ya usaban
  `ai-review-merge.yml`/`ai-implement.yml`). Los SHA se sacaron con
  `git ls-remote https://github.com/<owner>/<repo>.git <tag>` (no
  `curl api.github.com`: ese endpoint está bloqueado para repos fuera del
  scope de esta sesión — `git ls-remote` no pasa por esa puerta y no hace
  falta el repo "adjunto" para leerlo). Los dos tags son ligeros, no
  anotados (`git ls-remote --tags` no mostró la línea `^{}` de
  dereferencia), así que el SHA que devuelve la referencia **es** el
  commit real, usable directo para pinnear una Action. Los otros dos
  workflows (`ai-review-merge.yml`, `ai-implement.yml`) ya tenían **todas**
  sus acciones fijadas por SHA desde antes — `ci.yml` era el único
  workflow con tags flotantes en el repo, y su `actions/setup-python@v5`
  resolvió al **mismo SHA exacto** que ya usaban los otros dos, buena señal
  de que ambos apuntan a la misma versión real.
- 2026-08-16 — Orden 49 del backlog de seguridad (`SEC-CFG-005`):
  `MEDIA_ROOT = BASE_DIR / 'media'` en `settings.py`. **El hallazgo del
  backlog no reproduce como crash**: Django trae `MEDIA_ROOT = ''` como
  default global (`django/conf/global_settings.py`), así que
  `settings.MEDIA_ROOT` nunca lanzaba `AttributeError` como sugería el
  backlog — sirve (o falla en servir) desde el directorio de trabajo
  actual, en vez de reventar. Confirmado con una petición real a
  `/media/algo.jpg` antes del cambio: 404, no 500. Se corrigió de todas
  formas porque depender del default vacío de Django es frágil e
  incorrecto (sirve desde donde sea que arrancó el proceso, no desde una
  ruta real del proyecto) y es exactamente el tipo de cosa que un day
  cualquiera se vuelve un problema real. **Detalle no obvio para el test**:
  Django's `manage.py test` fuerza `settings.DEBUG = False` durante toda
  la suite (`DiscoverRunner.setup_test_environment()`, salvo pasarle
  `--debug-mode`) — sin importar qué diga el `DEBUG` del entorno al
  arrancar. El `if not DEBUG:` de `settings.py` (SECURE_SSL_REDIRECT,
  etc.) sí respeta el valor real del entorno porque se evalúa al importar
  el módulo de settings, **antes** de que el test runner pise
  `settings.DEBUG` — pero cualquier código que lea `settings.DEBUG` en
  vivo durante un test (como el `if settings.DEBUG:` de
  `core_erp/urls.py` que añade el patrón de `/media/`) ve `False` sin
  importar el `DEBUG=True` que se exporte antes de correr los tests. Por
  eso el test de esta orden llama directo a `django.views.static.serve()`
  (la vista real que `urls.py` conecta) en vez de pegarle a `/media/` con
  el test client — así prueba el código real sin depender de si el
  patrón de URL quedó cableado o no.
- 2026-08-16 — Orden 41 del backlog de seguridad (`SEC-CFG-003`):
  `SECURE_REFERRER_POLICY = 'strict-origin-when-cross-origin'` en
  `settings.py`, fuera del bloque `if not DEBUG` (es una cabecera de
  respuesta sin efecto funcional, sirve tenerla también en local para
  poder probarla). Dato no obvio: Django 6 ya trae por defecto
  `'same-origin'` para este setting — **más estricto** que lo que pide
  el backlog (`same-origin` no manda absolutamente nada en peticiones
  cross-origin; `strict-origin-when-cross-origin` sí manda el origen,
  sin el path). El token del portal vive en el *path*
  (`/mi-evento/<token>/`), así que en ninguno de los dos casos se filtra
  — esta orden no cerraba una fuga activa, hacía explícita una política
  que Django ya aplicaba implícitamente (a prueba de que una futura
  versión de Django cambie su propio default sin que nadie se entere).
  Tests en `core_erp/test_referrer_policy.py`. **Encontrado y corregido de
  paso un bug real en el fix de la sesión anterior** (el que arregló
  `test_portal_descargar_cotizacion_bloquea_tras_diez_peticiones`, ver
  entrada del PR #211 más abajo): en `comercial/test_rate_limit_publico.py`
  el ancla del reloj (`_INICIO_VENTANA`) se calculaba **una sola vez, a
  nivel de módulo**, en vez de en cada test como sí hacían correctamente
  `airbnb/test_seguridad.py` y `nomina/tests.py`. Funcionaba en aislado
  (el módulo se importa segundos antes de que el test corra, así que el
  valor congelado coincide con el reloj real) pero **rompía por completo**
  el rate limiting al correr la suite completa: si el módulo se importa al
  arrancar los 593 tests y este archivo corre varios minutos después,
  `django.core.cache.backends.base.BaseCache.get_backend_timeout()` —que
  también usa el mismo `time.time()` global parcheado, porque
  `core_erp/ratelimit.py` hace `import time` y el `patch()` mockea el
  módulo real, no una copia— calcula la expiración de la entrada como
  `valor_congelado_viejo + timeout`, un valor ya pasado respecto al reloj
  real con el que luego se compara al leer. Resultado: la entrada nace
  "expirada", el conteo nunca pasa de 1, y el límite jamás se alcanza — no
  es la ventana cruzándose (el síntoma original), es el contador vuelto a
  cero en cada petición. Reproducido de forma aislada y determinista con
  `time_module.time = Mock(return_value=1970-algo)` antes de tocar el
  código: `cache.add()` devuelve `True` pero el `cache.get()` inmediatamente
  después ya da `None`. Corregido moviendo el cálculo a una función
  (`_inicio_ventana()`) invocada justo antes de cada `with patch(...)`, en
  vez de una constante de módulo — mismo patrón que ya usaba
  `core_erp/test_ratelimit.py` desde el principio, que por eso nunca tuvo
  este problema. Verificado corriendo la suite completa (593 tests, ~5 min)
  dos veces seguidas tras el fix, no solo el archivo en aislado (que ya
  "pasaba" antes con el bug presente, y por eso no lo había detectado la
  vez anterior).
- 2026-08-16 — Orden 34 del backlog de seguridad (`SEC-DEP-001`): builds
  reproducibles con `requirements.lock`. `requirements.txt` sigue siendo
  la lista de dependencias directas (sin versión exacta), pero deja de
  instalarse directamente: `Dockerfile`, `ci.yml` (ambos jobs) y los dos
  workflows de automatización con IA (`ai-review-merge.yml`,
  `ai-implement.yml`) instalan desde `requirements.lock`, generado con
  `pip-compile --resolver=backtracking` (146 líneas, todo el árbol de
  dependencias transitivas fijado). `pip-audit` en el job `security`
  también pasa a auditar el lock, no el `.txt` — antes auditaba rangos
  sueltos (`Django>=6.0`) en vez de la versión exacta que realmente se
  instala. **Hallazgo antes de tocar nada**: el `Dockerfile` usa
  `python:3.13-slim` pero el resto del proyecto (CI, `pyproject.toml`
  `target-version`) es Python 3.12 — inconsistencia preexistente, no
  introducida por esta orden. Se decidió generar el lock con 3.12 (la
  versión que de verdad fija el proyecto) y no tocar la imagen base del
  `Dockerfile` de paso, que sería alcance no pedido sobre un archivo que
  afecta producción directamente. Verificado que la instalación completa
  desde el lock funciona igual bajo ambas versiones antes de confiar en
  eso: instalación limpia en un venv nuevo con Python 3.12 y otra con
  3.13 (la que probablemente esté usando Railway), las dos con
  `manage.py check` limpio. Confirmado el criterio de aceptación
  literal ("dos builds del mismo commit producen el mismo `pip freeze`"):
  correr `pip-compile` dos veces seguidas produce un archivo byte-a-byte
  idéntico. No se pudo probar el `Dockerfile` con un build real (`docker
  build`) porque este entorno no tiene el daemon de Docker corriendo
  (`/var/run/docker.sock` no existe) — la validación se hizo instalando
  el lock en venvs limpios de 3.12 y 3.13 en su lugar, que cubre la parte
  que de verdad cambia (qué se instala), no la capa de Docker en sí.
- 2026-08-16 — Orden 36 del backlog de seguridad (`SEC-LOG-001`): logger
  `django.security` declarado explícitamente en `LOGGING`
  (`core_erp/settings.py`) + `core_erp/middleware.py::AuthorizationAuditMiddleware`
  nuevo, registrado justo después de `AuthenticationMiddleware` en
  `MIDDLEWARE`. **Por qué un middleware y no tocar cada vista**: hay tres
  formas de llegar a un 403 en el repo (`raise PermissionDenied` ad hoc en
  3 sitios, `@permission_required(raise_exception=True)` en decenas de
  vistas, y en teoría un `HttpResponseForbidden` manual) — instrumentar
  cada una por separado se habría quedado corto en cuanto alguien añadiera
  la siguiente vista protegida. El middleware inspecciona la **respuesta**
  (`response.status_code == 403`) después de `get_response()`, no la
  excepción: cubre las tres formas por igual sin importar cómo se generó
  el 403, con el mismo patrón (post-respuesta, no `process_exception`) que
  ya usa `PublicSecurityHeadersMiddleware` en el mismo archivo.
  `propagate=False` en la entrada de `django.security` es necesario: sin
  eso, cualquier mensaje logueado ahí (el nuestro, o los que Django ya trae
  de fábrica como `django.security.DisallowedHost`/`django.security.csrf`)
  se duplicaría también en el logger `django`, que ya tiene el mismo
  handler de consola y lo recibiría igual por herencia. El criterio de
  aceptación del backlog (una petición con `Host` inválido produce una
  línea identificable) en realidad **ya lo cumplía Django solo** —
  `django.security.DisallowedHost` es un logger de fábrica que hereda del
  padre `django`, que ya tenía handler—; declarar `django.security`
  explícito no lo activa, lo hace *filtrable* aparte del resto del tráfico
  y a prueba de que alguien cambie la config de `django` sin darse cuenta
  de que también silenciaría la seguridad. Tests nuevos en
  `core_erp/test_middleware.py`: un 403 real (`importar_historico_view`
  sin superusuario) deja `usuario=<username> ruta=<path>` en el log; una
  respuesta 200 no genera ninguna línea (`assertNoLogs`); y el caso
  `Host` inválido del propio criterio de aceptación, con
  `override_settings(ALLOWED_HOSTS=[...])`.
- 2026-08-16 — Orden 33 del backlog de seguridad (`SEC-SECRET-002`):
  auditoría de secretos sobre el **historial completo** del repositorio,
  no solo el árbol de trabajo actual (eso es el gate continuo de la orden
  32). El checkout de esta sesión venía shallow (`fetch-depth` implícito
  de 1), así que `gitleaks detect` con `--log-opts="--all"` habría
  escaneado un solo commit sin avisar del problema — hizo falta
  `git fetch --unshallow` primero (256 commits visibles pasaron a 829)
  para que el escaneo fuera real. Resultado: **793 commits escaneados, cero
  hallazgos** — no hay ninguna credencial que rotar. Mismo binario y mismo
  checksum verificado que ya usa el gate de CI (orden 32, `gitleaks
  8.21.2`), corrido localmente porque esta orden es una auditoría puntual,
  no algo que deba repetirse en cada PR.
- 2026-08-16 — Orden 32 del backlog de seguridad (`SEC-CI-001d`): `gitleaks`
  detecta secretos commiteados en el job `security` de `ci.yml`. Se
  descartó la GitHub Action oficial (`gitleaks/gitleaks-action`): exige
  `GITLEAKS_LICENSE` en repos privados/de organización, y este repo lo es.
  En su lugar se instala el binario del release oficial directo (versión
  fijada `8.21.2`, no `latest`) y se verifica su `sha256sum` contra el
  `checksums.txt` que publica el propio release antes de ejecutarlo — mismo
  nivel de rigor que ya se aplicaría a cualquier binario de terceros que
  entra al pipeline. Se probó localmente contra el repo completo antes de
  activarlo como gate: `gitleaks detect --no-git --source .` no encontró
  nada (`exit 0`), y una prueba deliberada con un secreto de Stripe
  fabricado sí lo detectó y devolvió `exit 1` — confirmando que el gate
  bloquea de verdad y no es un placebo. `--no-git` escanea el árbol de
  trabajo tal como queda el checkout (no el historial de git): con
  `actions/checkout@v4` en modo shallow (`fetch-depth: 1`, el default), un
  escaneo en modo git solo vería el último commit y se perdería secretos
  introducidos en commits anteriores del mismo PR antes de un squash-merge;
  escanear el árbol completo es más simple y determinista, y cubre el
  criterio de aceptación real ("un PR con una clave... falla el CI"). El
  escaneo del **historial** completo (detectar un secreto que se subió y
  luego se borró) es la orden 33 (`SEC-SECRET-002`), una auditoría puntual
  aparte, no parte de este gate continuo.
- 2026-08-15 — Orden 31 del backlog de seguridad (`SEC-CI-001c`): ruleset
  `S` (flake8-bandit) de ruff activado. Marcó 84 hallazgos; se revisaron
  **todos** a mano, uno por uno, antes de decidir arreglar vs. ignorar —
  ninguno se descartó a ciegas. **(1)** `S106`/`S105`/`S107` (posible
  contraseña hardcodeada, 38) eran en su totalidad credenciales de prueba
  en archivos de test (`create_user(password=...)`, tokens simulados de
  webhook) salvo una: `nomina/services.py::JIBBLE_TOKEN_URL`, un falso
  positivo del regex de bandit sobre la palabra "TOKEN" en el nombre de una
  URL. Los de test se ignoran por patrón de archivo
  (`**/tests.py`/`**/test_*.py`/`**/tests/*.py`) en
  `[tool.ruff.lint.per-file-ignores]`; el de `nomina/services.py` lleva un
  `# noqa: S105` puntual — así un secreto real fuera de esos patrones sigue
  bloqueando el CI. **(2)** `S110` (`try`/`except`/`pass`, 13) son parseo o
  consultas best-effort con fallback silencioso a un default sensato
  (disponibilidad, horarios de Jibble, fechas, lookups opcionales) — mismo
  patrón ya aceptado en la orden 25/29 para los `except Exception:`
  genéricos; se ignora la regla completa con la justificación en
  `pyproject.toml` en vez de forzar logging en 13 sitios que no esconden
  ningún control de seguridad. **(3)** `S308` (uso de `mark_safe`, 28) se
  revisó **cada llamada** individualmente: 27 interpolan solo choices
  (`get_FOO_display()`), números (folios, montos, fechas), o HTML generado
  por el propio Django (`render_to_string`, el `render()` de un widget) —
  ninguno alcanzable con texto libre de un formulario — y llevan
  `# noqa: S308` puntual con esa razón. La excepción real:
  `comercial/admin.py::badge_cotizador` interpolaba `obj.icono`
  (`CharField` libre de hasta 10 caracteres, editable por cualquier staff
  con permiso sobre `Producto`) sin escapar dentro de un f-string envuelto
  en `mark_safe` — mismo patrón de fondo que el XSS de `SEC-XSS-001`
  (Issue #190), aunque con impacto mucho menor por el límite de 10
  caracteres. Corregido cambiando a `format_html`, que sí escapa los
  argumentos. **(4)** `S314` (parseo de XML sin protección contra XXE, 3):
  `comercial/models.py` (`Compra.save()`, dos parseos) y
  `comercial/services.py::procesar_compra_desde_xml` leen el XML del CFDI
  que sube quien captura una compra — viene de un proveedor externo, no es
  dato confiable. Se agregó `defusedxml` a `requirements.txt` (dependencia
  nueva, pura, sin transitivas, mantenida, hecha exactamente para esto) y
  se cambió el import de `xml.etree.ElementTree` a
  `defusedxml.ElementTree` en los tres sitios — mismo API
  (`.parse()`/`.fromstring()`/`ParseError`), cero cambios de lógica.
  **(5)** `S608` (SQL con f-string, 2): ambos en
  `core_erp/test_ratelimit.py`, interpolando el nombre de tabla que sale de
  `settings.CACHES` (no de entrada externa) con el valor real parametrizado
  vía `%s` — `# noqa: S608` puntual con la razón. Los 84 hallazgos
  originales se cerraron sin dejar ninguno "silenciado a ciegas": cada
  ignore/noqa en `pyproject.toml` documenta por qué, y los dos hallazgos
  genuinos (el XSS de 10 caracteres y el XXE del CFDI) se corrigieron en
  vez de justificarse.
- 2026-08-15 — Órdenes 29-30 del backlog de seguridad (`SEC-CI-001a/b`): el
  gate de lint en CI ahora bloquea de verdad. `ruff check .` marcaba 631
  errores (contando desde cero, el conteo de "555" que traía el backlog
  quedó desactualizado por los PRs recientes) y el paso de lint en `ci.yml`
  tenía `continue-on-error: true` desde siempre, así que nunca frenó nada.
  El autofix normal (`ruff check --fix`) resolvió 497 sin tocar
  comportamiento (imports, `W292`, `F541`, `F811` y la mayoría de
  espacios en blanco al final de línea). Quedaron tres tipos que ruff no
  auto-corrige: **(1)** 94 líneas con `E701`/`E702` (sentencias compuestas
  en una sola línea con `;` o `if x: y`), casi todas en `comercial/admin.py`
  (ModelAdmin/Inline terse), `services.py`, `models.py` y `views.py` —
  reformateadas con un script propio (no `ruff format`/`black`: eso habría
  cambiado comillas, imports multilínea y otro estilo no relacionado con
  esta tarea) que usa `ast.parse` sobre cada línea aislada para ubicar con
  exactitud dónde cortar (el punto y coma o los dos puntos de la cabecera
  compuesta) sin regenerar texto — cada corte es una porción literal del
  código original. Verificado con la red de seguridad real: `ast.dump()`
  del archivo completo antes y después es **idéntico** (ignorando
  posiciones), así que el AST no cambió, solo el formato. **(2)** Los 7
  `E722` (`except:` sin tipo) — los 6 de `nomina/views.py` son parseo
  defensivo de Excel/pandas con fallback (horas, fechas, horarios de
  Jibble) y el de `comercial/models.py:1216` es el parseo de un XML de CFDI
  con fallback a `impuestos.iva_de()`; todos pasan a `except Exception:`
  sin cambiar el fallback. **(3)** `--unsafe-fixes` resolvió el resto de
  espacios en blanco; los 5 `F841` (variable sin usar) que quedaban se
  revisaron uno por uno antes de borrar la línea completa (no solo
  aceptar el fix de ruff a ciegas) para confirmar que ninguna asignación
  tenía efectos secundarios — los cinco eran expresiones puras
  (`strftime`, `Decimal('0.00')`, un `int()` con `try/except` cuyo
  resultado nunca se leía, una clave de diccionario, un `str().strip()`).
  El caso de `comercial/views_cotizador.py::api_paquetes_cotizador` es
  el más interesante: parseaba `personas` de la query string pero
  **nunca la usaba para filtrar nada**, pese a que el docstring de la
  función promete "filtrados por servicio y rango de personas" — es un
  hueco de funcionalidad preexistente, no algo que esta tarea de lint deba
  implementar; se dejó documentado aquí en vez de inventar el filtro de
  paso. Con `ruff check .` en cero, se quitó el `continue-on-error` del
  paso de lint en `ci.yml`.
- 2026-08-15 — Arranque de la contabilidad del ERP en una fecha
  (`cerrar_historico_contable`, `/admin/contabilidad/reportes/cerrar-historico/`).
  El propietario confirmó que **los periodos anteriores a julio ya los cerró el
  contador fuera del ERP**: las pólizas previas del sistema no son los libros,
  son captura parcial, y lo único que hacían era arrastrar descuadres a la
  conciliación bancaria (la `diferencia_arrastrada` de −$226,730.45 que motivó
  todo esto). **Cancela, no borra**, y esa es la decisión de fondo: una póliza
  `CANCELADA` conserva movimientos, folio y auditoría pero queda fuera de todo
  saldo y todo reporte, porque en el ERP entero solo suma `estado='APLICADA'`.
  Borrarlas sí sería destructivo — están ligadas por `content_type` a pagos,
  compras, recibos de nómina y pagos de Airbnb que siguen vivos, y los
  `MovimientoContable` desaparecerían. Cancela también los **BORRADOR**
  anteriores al corte: si se quedaran vivos, alguien podría aplicarlos después y
  volver a meter movimiento en un periodo ya cerrado. Es de `is_superuser`
  (Dirección) y simula por defecto, en las dos vías. **Ojo con los signals**: si
  se reguarda un `Pago`/`Compra`/`ReciboNomina`/`PagoAirbnb` anterior al corte,
  su signal reexpide la póliza con la fecha vieja y vuelve a colarse en el
  histórico — no se bloqueó porque sería alcance no pedido, pero es el hueco
  conocido de este mecanismo. El paso que **no** hace el cierre y sin el cual el
  ERP cree que las cuentas arrancan en cero: capturar el saldo de apertura
  certificado en `/admin/contabilidad/saldoapertura/`. Las plantillas de admin
  one-shot del repo (`migrar_archivos_privados.html`) usan paneles con fondo
  claro (`#e3f2fd`, `#fff3cd`) que en el tema oscuro de Jazzmin dejan el texto
  ilegible; aquí se usó fondo translúcido + borde izquierdo de color, como en
  `static/contabilidad/conciliacion.css`.
- 2026-08-15 — Orden 25 del backlog de seguridad (`SEC-INFO-001`): las tres
  vistas públicas que devolvían `str(e)` crudo en el cuerpo de un 500 —
  `api_disponibilidad_fecha`/`api_fechas_ocupadas` (`comercial/views_cotizador.py`)
  y `webhook_sync_jibble` (`nomina/views.py`)— ahora responden un mensaje
  genérico y el detalle va a `logger.exception()`. **No se tocó** el
  `except JibbleAPIError` de `webhook_sync_jibble` (línea previa al
  `except Exception` corregido): ese mensaje ya es controlado por el propio
  servicio, no una excepción interna cruda, y tampoco se tocó
  `sync_jibble_view` en el admin (usa `messages.error(request, f"Error
  inesperado: {e}")`) porque esa vista exige `staff_member_required` +
  `nomina.change_recibonomina` — quien la ve ya es una cuenta interna
  autorizada, no el público anónimo que el backlog señalaba. Tests nuevos
  con `assertLogs` para confirmar las dos mitades del criterio de
  aceptación a la vez (el texto no está en la respuesta HTTP, sí está en el
  log): `CotizadorApisErrorGenericoTest` en
  `comercial/test_cotizador_seguridad.py` y
  `WebhookSyncJibbleErrorGenericoTest` en `nomina/tests.py`.
- 2026-08-14 — Órdenes 23-24 del backlog de seguridad (`SEC-CSRF-001`,
  `SEC-VAL-001`): `cotizador_enviar` deja de ser `@csrf_exempt` y su
  validación manual (5 `if` sueltos) se sustituyó por
  `comercial/forms_cotizador.py::CotizadorEnviarForm`. **CSRF**: el
  formulario público (`comercial/templates/cotizador/index.html`) no tenía
  ningún `<form>`, es una SPA de un solo `<body>` con pasos en JS — se
  renderiza `{% csrf_token %}` suelto justo tras abrir `<body>` (deja la
  cookie puesta igual que dentro de un `<form>`) y el JS lo lee de
  `document.querySelector('[name=csrfmiddlewaretoken]').value` para mandarlo
  como header `X-CSRFToken` en el `fetch` a `/cotizar/enviar/`, porque el
  body va como JSON y Django no busca el token en un JSON body, solo en el
  header o en un campo de formulario. **Validación**: todos los campos del
  form quedan `required=False` a nivel de `Field` a propósito — los 5
  chequeos de "obligatorio" originales siguen viviendo en `clean()`, con el
  mismo texto exacto de siempre (`test_cotizador_rechaza_la_solicitud_sin_consentimiento`
  en `test_enlaces_legales.py` compara el string "Aviso de Privacidad"
  literal). Lo que el form añade encima: `tipo_evento` y `como_nos_encontro`
  —antes texto libre que alimentaba `nombre_evento` sin ninguna
  restricción— pasan a `ChoiceField` con las mismas opciones que ya ofrece
  el `<select>`/los chips del HTML; `notas` gana un `max_length=300`. Los
  campos fiscales (`rfc`, `razon_social`, `cp_fiscal`) y `nombre` sí llevan
  `max_length` pero generosos, iguales o por encima del `max_length` del
  modelo `Cliente` — no son un cambio de comportamiento (antes se truncaban
  en silencio con `[:N]` antes de guardar), son una red de seguridad para
  el caso real de un `nombre` de más de 200 caracteres, que antes llegaba
  intacto hasta `Cliente.objects.create()` y en Postgres habría reventado
  con `DataError` en vez de un 400 limpio. `personas`, `hora_inicio`,
  `hora_fin`, `email`, `extras_ids` y `finalidades` se dejaron sin
  restricciones nuevas: ya se defienden solos (parseo con `try/except` que
  cae a un default, o validación propia más abajo en
  `get_or_create_cliente_desde_canal`) y el backlog no los señalaba. Tests
  nuevos en `comercial/test_cotizador_seguridad.py`: CSRF con
  `Client(enforce_csrf_checks=True)` (sin token → 403; con la cookie real de
  visitar `/cotizar/` → pasa) y las choices/longitud del form. El cliente de
  pruebas por defecto de Django (`self.client`) desactiva la verificación de
  CSRF sin que haga falta tocar nada, así que ningún test existente se rompió
  al quitar `@csrf_exempt`.
- 2026-08-14 — Órdenes 19-21 del backlog de seguridad (`SEC-RL-001a/b/c`):
  `@rate_limit` en las vistas públicas que no lo tenían — descargas del
  portal (~10/min), 5 APIs del cotizador (~60/min) y ambos webhooks más el
  feed iCal (~120/min). El decorador ya existía (`core_erp/ratelimit.py`,
  usado desde el Issue #179 en login y desde entonces en
  `cotizador_enviar` y las vistas de pago de Openpay); esta tarea era
  aplicarlo donde faltaba, sin tocar la lógica de las vistas. Un detalle no
  obvio para los tests: `rate_limit` cuenta **antes** de ejecutar el cuerpo
  de la vista, así que ni siquiera hace falta simular datos válidos —agotar
  el cupo con peticiones que fallan por otra razón (401 sin Basic Auth en
  el webhook de Openpay, 403 sin token en el iCal, 500 sin
  `NOMINA_CRON_TOKEN` en el de Jibble, 404 sin contrato en el portal) igual
  deja la petición número 11/61/121 en 429. Cada vista lleva su propia
  `key` de bucket (no una compartida): agotar `portal_evento` no debe tocar
  el cupo de `portal_descargar_plan`, verificado en
  `test_cada_vista_tiene_su_propio_cupo`. Tests nuevos repartidos por app —
  `comercial/test_rate_limit_publico.py` (portal + cotizador + webhook
  Openpay), `nomina/tests.py` (webhook Jibble, antes vacío) y una clase
  añadida a `airbnb/test_seguridad.py` (feed iCal) — porque el rate
  limiting de cada webhook/feed vive en la app dueña de esa vista, a
  diferencia de la orden 14-18 que sí tuvo que centralizarse en
  `comercial/` por el comando de test de CI incompleto de entonces (ya
  corregido). Pendiente relacionado: la orden 22 (`SEC-RL-002`, verificar
  `X-Forwarded-For` en el edge de Railway) cobra más peso ahora que el
  rate limiting de producción depende de que `_client_ip()` resuelva bien
  la IP real — sigue sin verificar.
- 2026-08-14 — Regularización de la diferencia arrastrada, con autorización de
  Dirección (cierra el hilo de los Issues #198/#200). El descuadre heredado ya
  se medía y se aislaba; ahora se puede cancelar. Dos acciones en
  `ConciliacionBancariaAdmin`: **proponer** (cualquiera que concilie) crea la
  póliza en `BORRADOR` y **autorizar** (`is_superuser`, que es como el ERP
  modela Dirección) la aplica. **La fecha de la póliza no es opcional: día
  anterior al primer movimiento del estado de cuenta.** El arrastre se mide como
  `saldo_libros(inicio − 1 día) − saldo_inicial_estado`, así que solo un asiento
  con esa fecha o anterior lo cancela; fecharlo dentro del periodo lo dejaría
  intacto **y además descuadraría el periodo por el mismo importe**, porque
  `saldo_segun_libros` bajaría mientras `diferencia_arrastrada` se sigue restando.
  El candado de autorización **no puede vivir en una sola pantalla**: aplicar el
  borrador desde `PolizaAdmin.aplicar_polizas` se lo saltaba, así que ahí también
  se comprueba `poliza.requiere_autorizacion_direccion and not is_superuser` (hay
  test para esa vía, no solo para la acción de la conciliación). `Poliza` gana
  `aplicada_por`/`fecha_aplicacion` — antes solo se asentaba quién cancelaba, no
  quién autorizaba. La propuesta es idempotente: reescribe el borrador existente
  en vez de acumular duplicados. De paso, `aplicar_saldo_apertura()` usaba
  `saldo_actual` (todo el histórico, incluido lo POSTERIOR al corte) para
  calcular la diferencia que luego asienta **en la fecha de corte** — mismo error
  de fondo que el de la conciliación; pasa a `saldo_a_fecha(fecha_corte)`.
- 2026-08-14 — Reasignar el asiento de un movimiento del estado de cuenta
  (seguimiento del PR #200). Quien concilia no encontraba cómo deseleccionar: lo
  que quita la asignación es la **× de select2 dentro de la caja**, que por
  defecto es casi invisible entre la etiqueta y la flecha (ahora en naranja y más
  grande). Peor: la **X roja de fuera de la caja no deselecciona, borra el
  `MovimientoContable` de la base de datos** — es el `delete-related` de
  `RelatedFieldWidgetWrapper`, que aparece porque `formfield_for_dbfield` lo
  cablea a `has_delete_permission()` del admin del modelo relacionado, y
  `MovimientoContableAdmin` ya bloqueaba add y change pero no delete. Borrar un
  renglón suelto descuadra la póliza, así que `has_delete_permission` pasa a
  `False` y con eso desaparece el icono en todos lados (el ojito de `view` se
  queda, sí es útil). Además el buscador acotado solo conoce el estado de la BD
  al abrir la pantalla: dentro de un mismo guardado nada impedía elegir el mismo
  asiento en dos renglones, y un asiento contado dos veces sale de las partidas
  de la conciliación sin dejar rastro del descuadre → `MovimientoEstadoCuentaFormSet.clean()`
  lo rechaza, tanto dentro del formset como contra otros estados de cuenta de la
  misma cuenta bancaria. Ojo con los tests de admin de esta pantalla:
  `EstadoCuentaBancario.archivo` es obligatorio, así que un POST sin archivo
  **no valida y no guarda**, y una prueba que solo comprueba "el valor no
  cambió" pasa sin haber probado nada (le pasaba a
  `test_no_se_pueden_editar_los_importes_del_banco`). Se arregló creando el
  objeto con `SimpleUploadedFile` bajo `override_settings(STORAGES=...)` con
  `InMemoryStorage` —el patrón del repo— y asegurando `status_code == 302`.
- 2026-08-14 — Orden 17 del backlog de seguridad (`SEC-AUTHZ-001d`), la que
  había quedado deliberadamente fuera del PR de la orden 14-16/18 (ver
  entrada de abajo). `importar_historico_view` ya rechazaba el POST a
  no-superusuarios; el GET (la vista previa del historial, con los datos
  del sistema anterior) seguía abierto a cualquier staff. Se movió el
  chequeo `if not request.user.is_superuser` al principio de la vista, antes
  de construir el contexto, y se cambió de `messages.error` + `redirect`
  a `raise PermissionDenied` — el criterio de aceptación del backlog pedía
  403 explícito, no un redirect silencioso, y de paso queda consistente con
  el patrón ya usado en `comercial/admin.py::carga_masiva_view` y
  `contabilidad/views.py::autocomplete_asiento_bancario` (`raise
  PermissionDenied` inline en vez de decorador, cuando el chequeo no es un
  simple `has_perm` de modelo sino una condición ad hoc). El branch POST se
  quedó sin su propio chequeo: ahora es redundante porque nada llega ahí sin
  pasar primero por el de arriba. Tests nuevos en
  `comercial/test_permisos_grupos.py::ImportarHistoricoSoloSuperusuarioTest`
  (GET y POST para staff sin `is_superuser`, GET para superusuario).
- 2026-08-14 — Órdenes 14-16/18 del backlog de seguridad (`SEC-AUTHZ-001a/b/c/e`,
  Issue #199, PR sobre `claude/solicitud-ai-nok66o`): 3 grupos Django
  (Ventas, Contabilidad, Nómina) con permisos por modelo, más
  `@permission_required` en las vistas custom que antes solo exigían
  `is_staff`. **Dirección no es un grupo**: sigue siendo `is_superuser`, el
  patrón que ya usaban `importar_historico_view` y
  `migrar_archivos_privados_view` — un superusuario pasa cualquier
  `has_perm`/`permission_required` sin permisos explícitos, así que crear un
  grupo "Dirección" habría sido redundante. La matriz completa (qué app va
  con qué grupo, y las excepciones) se decidió con el propietario antes de
  escribir el Issue, no unilateralmente; quedó documentada ahí y no se repite
  aquí. Dos decisiones puntuales sí vale la pena recordar: **(1)**
  `comercial.ConstanteSistema` es la única excepción dentro de un área —
  Ventas recibe solo `view`, no `add`/`change`/`delete`, porque son precios
  de referencia que consultan pero no deben poder alterar solos. El comando
  `crear_grupos_permisos` modela esto con un diccionario de excepciones por
  `(app_label, modelo)` en vez de listar los ~40 modelos uno por uno —
  itera `apps.get_app_config(app).get_models()` y aplica los 4 verbos
  estándar salvo que el modelo esté en `EXCEPCIONES`. **(2)** `reportes/`
  agrega en una sola pantalla reportes de las 4 áreas, así que no lleva un
  único permiso de módulo: cada una de sus 11 vistas exige el permiso de su
  área dueña, y `selector_reportes` pasa flags `puede_<area>` al contexto
  para que la plantilla oculte secciones enteras en vez de mostrar un botón
  que respondería 403 al pulsarlo. **Las 4 sub-vistas de
  `SolicitudFacturaAdmin.get_urls()` no usan `@permission_required`**: son
  métodos ligados de la clase, y decorar un método con un decorador pensado
  para funciones desplaza los argumentos (`self` termina donde el decorador
  espera `request`) — se replicó en su lugar el patrón ya existente de
  `comercial/admin.py::descuentos_view`, un `if not
  request.user.has_perm(...): messages.error(...); return redirect(...)`
  inline; por eso esas 4 rechazan con 302, no 403, a diferencia del resto.
  `core_erp/descargas.py::descargar_archivo_privado` **no se tocó**: ya
  comprobaba `has_perm(f'{app_label}.view_{model_name}')` de forma dinámica
  con un comentario propio anticipando esta orden exacta, así que los grupos
  nuevos lo alinean solos. Los tests viven en `comercial/test_permisos_grupos.py`
  aunque cruzan 7 apps, porque el comando de test de CI (y el documentado en
  este archivo) no incluía `reportes` ni corría `comunicacion` de forma
  consistente — se corrigieron ambos (`ci.yml` y la tabla de arriba) para
  incluir las 9 apps reales. **La orden 17 (`SEC-AUTHZ-001d`) se dejó fuera**:
  `importar_historico_view` ya bloqueaba el POST a no-superusuarios desde
  antes de este PR, pero el GET (la vista previa del historial) sigue abierto
  a cualquier staff y el rechazo es un redirect, no un 403 — no estaba en el
  plan del Issue #199 y tocarlo de pasada habría sido alcance no pedido.
- 2026-08-14 — Conciliación bancaria: la diferencia era **del histórico, no del
  periodo** (Issue #198). El caso reportado —banco $1,256.87 contra libros
  $43,725.82, diferencia $41,968.96— no venía de un error de julio: la fórmula
  de `calcular_diferencia()` reproducía el número exacto, lo malo eran las
  entradas. Tres causas. **(1)** `saldo_a_fecha()` acumula **desde el origen de
  la cuenta** (no tiene cota inferior) y eso se comparaba contra el saldo final
  de un único estado de cuenta: toda póliza anterior al primer estado de cuenta
  cargado entraba íntegra a la diferencia. **(2)** `saldo_inicial_estado` —el
  ancla que faltaba— lo extraía el parser, lo guardaba y **nadie lo usaba**: su
  único uso en el repo era un assert de `tests.py`. Ahora
  `diferencia_arrastrada = saldo_libros(día anterior al primer movimiento) −
  saldo_inicial_estado` aísla el descuadre heredado y `diferencia` queda del
  periodo. **(3)** `cargos_empresa_no_cobrados` y `abonos_empresa_no_abonados`
  participaban en la fórmula pero **ningún código las escribía jamás**, así que
  media conciliación estaba muerta: una póliza sobre bancos que el banco aún no
  refleja no se clasificaba como partida en tránsito, se iba entera a la
  diferencia. Al empezar a llenarlas apareció que **sus dos signos estaban
  invertidos** desde siempre (los depósitos en tránsito SUMAN al banco, no
  restan); nunca se notó porque valían cero. Se separó `analizar_conciliacion()`
  como fuente única del cálculo, que consumen la generación y el nuevo
  `manage.py diagnosticar_conciliacion` (solo lectura, desglosa las tres cosas
  por separado). En el admin: el buscador de asientos del inline ofrecía
  **cualquier** `MovimientoContable` —de cuentas de gastos incluidas, de
  cualquier fecha y estado de póliza—, con etiqueta `"Cargo $1369.18 →
  601.01.01"` sin folio, fecha ni concepto; se acotó con una vista propia
  (`contabilidad:autocomplete_asiento_bancario`, el id del padre viaja en la URL
  porque `limit_choices_to` no puede conocerlo) y se enriqueció `__str__`. La
  tabla de movimientos se compactó de ~92px a 36px por fila: lo que la inflaba
  era **una advertencia de zona horaria y un bloque de ayuda por cada renglón**,
  repetidos 60 veces. El scroll se arregló acotando la altura del contenedor
  (`max-height: 60vh; overflow: auto`) en vez de perseguir la barra: así queda
  siempre a la vista y de paso `position: sticky` funciona en el `thead`, que con
  scroll de página no funcionaría. Ojo al tocar ese CSS: Jazzmin pinta el fondo
  en el `<tr>` y gana por especificidad, así que las celdas fijas necesitan
  `background: #383632 !important` o dejan ver lo que pasa por debajo.
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
