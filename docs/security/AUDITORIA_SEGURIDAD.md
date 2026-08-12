# Auditoría de seguridad — ERP-QKT

**Fecha**: 2026-08-12 · **Commit auditado**: `f813dcc` · **Issue**: #190
**Modalidad**: revisión de solo lectura del repositorio.

---

## 0. Estado de la remediación

La auditoría se entregó como revisión de solo lectura. Tras entregarla se
verificaron dos de los puntos externos y se corrigieron los hallazgos que
resultaron ser exposiciones activas. Esta sección registra qué cambió después
de la evaluación inicial; **el resto del documento describe el estado del
commit auditado (`f813dcc`) y se conserva como está**, para que la evidencia
original siga siendo legible.

| ID | Estado al auditar | Estado ahora | Nota |
|---|---|---|---|
| `SEC-XSS-001` | P0 confirmado en ejecución | **CORREGIDO** | `json_script` sustituye a `\|safe` en el calendario y en los dos dashboards |
| `SEC-DATA-001` | P1, condicionado a `NV-02` | **EXPOSICIÓN CONFIRMADA · CORREGIDO** | Ver abajo |
| `SEC-INJ-001` | P2 | **CORREGIDO** | Colateral: al dejar de interpolar texto libre en el `.ics` no queda nada que escapar |
| `NV-02` | No verificable | **VERIFICADO** | `ICAL_PUBLIC_TOKEN` **no estaba definida** en Railway |
| `NV-01` | No verificable | **VERIFICADO** | El bucket R2 **sí sirve lectura anónima**. `SEC-FILE-001` es una exposición activa; ver abajo |
| `SEC-FILE-001` | P1, condicionado a `NV-01` | **EXPOSICIÓN CONFIRMADA · parcialmente corregido** | El ERP ya no publica URLs del bucket (código). Falta activar el bucket privado en Cloudflare — ver abajo |
| `SEC-AUTHN-001a` | P1 | **CORREGIDO** | `portal_acceso` devuelve el mismo mensaje en los tres fallos |

### `SEC-DATA-001` fue una fuga real, no un riesgo teórico

La verificación de `NV-02` confirmó que `ICAL_PUBLIC_TOKEN` no estaba definida
en Railway, de modo que la condición fail-open descrita en el hallazgo se
cumplía. Se comprobó descargando `/airbnb/ical/eventos/` desde una sesión
anónima en producción: el `.ics` se entregó sin autenticación y contenía
nombre completo de cliente, nombre del evento, número de asistentes y fecha de
las cotizaciones confirmadas.

Contención aplicada, en este orden:

1. Se definió `ICAL_PUBLIC_TOKEN` en Railway y se actualizó la URL registrada
   en Airbnb para que la lleve como `?token=…`. Eso cerró la fuga sin esperar
   a ningún despliegue de código.
2. Se corrigió el diseño a fail-closed y se retiraron los datos personales del
   feed (ver los hallazgos correspondientes).

El token se generó en un gestor de contraseñas y nunca salió de él, así que no
requiere rotación por origen. Entra en el calendario ordinario de rotación de
credenciales (`NV-06`).

### `SEC-FILE-001`: el bucket es público, y el único control es la URL

`NV-01` se verificó indirectamente pero sin ambigüedad: las imágenes de la
landing se sirven desde `media.quintakooxtanil.com` y cargan en una sesión
anónima, luego el bucket permite lectura sin credenciales. Todo objeto
almacenado ahí es accesible para quien conozca su ruta.

Un primer intento de comprobación con la URL de un contrato devolvió 404, pero
**ese 404 no significaba que el bucket fuera privado**: la URL llevaba el
prefijo `/media/`, propio de rutas heredadas de Cloudinary, mientras que
`upload_to` es `contratos_pdf/` y el `S3Storage` no define `location`. El objeto
no estaba en R2, no es que estuviera protegido. El mensaje de Cloudflare
(*"does not exist **or** is not publicly accessible"*) mezcla ambos casos y no
sirve para distinguirlos.

Dos matices para dimensionarlo correctamente, sin exagerarlo ni minimizarlo:

- **No todo está en R2.** Los archivos anteriores a la migración siguen en
  Cloudinary; el 404 del contrato lo demuestra. Lo expuesto es lo subido desde
  que R2 es el storage activo.
- **`file_overwrite: False` añade un sufijo aleatorio corto** al nombre
  (`..._uxjral.pdf`), lo que hace la ruta no trivialmente adivinable. Eso es
  seguridad por oscuridad, no control de acceso: la URL se filtra por historial,
  por correo, por `Referer` (`SEC-CFG-003`) y —en el caso de los contratos—
  porque `portal_descargar_contrato` se la entrega directamente al navegador del
  cliente.

Para las identificaciones de solicitudes ARCO esto es lo más serio: son datos
personales recabados justamente para ejercer derechos ARCO, y el control que
`legal/views.py` implementa (permiso dedicado + bitácora de accesos) queda
sorteado por completo si alguien tiene la URL directa.

**No hay contención de una línea aquí.** Hacer el bucket privado sin más deja la
landing sin imágenes, porque comparten almacenamiento. La corrección se dividió
en dos mitades:

**Hecho (código).** El ERP dejó de publicar URLs del bucket para documentos
sensibles: `core_erp/descargas.py` los sirve con `FileResponse` exigiendo sesión
y el permiso `view` del modelo, y `portal_descargar_contrato` entrega el PDF en
vez de redirigir. Los `FileField` de identificaciones ARCO, nómina, contratos y
estados de cuenta ya apuntan a un storage `privado` configurable
(`core_erp/storages_qkt.py`), que **cae al bucket actual mientras no se
configure** — así el código se despliega sin depender del cambio de
infraestructura.

**Pendiente (infraestructura).** Hasta que exista el bucket privado, las URLs
que ya circulan siguen funcionando y el formulario de edición del admin sigue
mostrando el enlace del `FileField` por el widget por defecto de Django. Pasos,
en este orden:

1. Crear el bucket privado en Cloudflare R2, **sin** dominio público conectado.
2. Definir `CLOUDFLARE_R2_PRIVATE_BUCKET_NAME` en Railway (y las credenciales
   si son distintas).
3. `manage.py migrar_archivos_privados` para ver qué copiaría; luego
   `--aplicar`. Copia, no mueve: no borra nada del origen.
4. Verificar que las descargas funcionan contra el bucket nuevo.
5. Solo entonces, borrar esos objetos del bucket público.

---

## 1. Resumen ejecutivo

El ERP está mejor protegido de lo que sugiere su tamaño: no hay SQL crudo en
ninguna parte, no hay `eval`/`exec`/`pickle`, todas las llamadas HTTP salientes
llevan timeout, la integración de pagos valida importes en el servidor y compara
credenciales en tiempo constante, `manage.py check --deploy` pasa sin una sola
advertencia y `pip-audit` no reporta vulnerabilidades conocidas. El trabajo
reciente de rate limiting (Issue #179) dejó contadores compartidos entre workers
y bloqueo de fuerza bruta en el login del admin, ambos con pruebas.

Dicho eso, **hay un hallazgo crítico que debe atenderse antes que cualquier otra
cosa**: un atacante sin cuenta, sin credenciales y desde internet puede ejecutar
JavaScript arbitrario en el navegador de un usuario administrador. Se confirmó
con una prueba real contra el código de este commit, no por inspección visual.
El formulario público del cotizador (`/cotizar/enviar/`) acepta un nombre con
etiquetas HTML, ese texto se guarda en la base de datos y después se inserta sin
escapar dentro de un bloque `<script>` del calendario del admin
(`/admin/calendario/`). Cuando cualquier persona del equipo abre el calendario,
el código del atacante corre con su sesión: puede crear usuarios, leer clientes,
pagos, RFC y nómina, o exfiltrar todo en silencio. No hace falta engañar a nadie
para que haga clic en un enlace — basta con que alguien del negocio abra una
pantalla que usa a diario.

Detrás de ese hallazgo hay cuatro riesgos altos, todos con el mismo patrón de
fondo: **controles que existen pero se pueden rodear por otra puerta**. El feed
de calendario para Airbnb queda completamente abierto si una variable de entorno
no está configurada, y publica el nombre de cada cliente con su evento y fecha.
El acceso al portal del cliente se protege con los últimos 4 dígitos del teléfono
sobre un identificador secuencial, lo que se puede adivinar. Los archivos —
contratos, identificaciones oficiales de solicitudes ARCO, recibos de nómina,
estados de cuenta — se guardan en un bucket configurado para servir URLs sin
firma, lo que anula la vista protegida que el módulo legal construyó justamente
para no revelar rutas directas. Y la autorización de todo el ERP se reduce a "es
staff o no": quien pueda entrar al admin ve nómina, contabilidad y datos fiscales
por igual.

Ninguno de estos exige reescribir el sistema. El crítico se corrige con un
cambio de una línea en una plantilla; los altos, con ajustes acotados de
configuración y unas cuantas vistas.

---

## 2. Alcance y limitaciones

### Alcance revisado

| Elemento | Cobertura |
|---|---|
| Apps Django | Las 9 (`comercial`, `contabilidad`, `airbnb`, `nomina`, `facturacion`, `comunicacion`, `reportes`, `legal`, `core_erp`) |
| Vistas | 100% del inventario (`*/views*.py`): 63 funciones de vista, revisadas sus decoradores de autorización |
| Rutas | `core_erp/urls.py` completo + `airbnb/urls.py`, `reportes/urls.py`, `contabilidad/urls.py`, `legal/urls.py` |
| Configuración | `core_erp/settings.py`, `core_erp/middleware.py`, `Dockerfile`, `railway.json`, `.env.example`, `pyproject.toml` |
| Rate limiting | `core_erp/ratelimit.py` completo + `core_erp/test_ratelimit.py` |
| Pagos | `comercial/views_openpay.py`, `comercial/services_openpay.py` |
| CI/CD | `.github/workflows/{ci,ai-implement,ai-review-merge}.yml` |
| Plantillas | Búsqueda exhaustiva de `|safe` y `autoescape off` en `templates/` y `*/templates/` |
| Dependencias | `requirements.txt` vía `pip-audit` |

### Fuera de alcance / no auditado

- **Migraciones** (`*/migrations/*.py`) — excluidas por convención del repo; no aportan superficie de ataque propia.
- **`graphify-out/`** — artefacto generado.
- **Historial de git anterior al commit auditado** — se verificó que `.env` y `db.sqlite3` estén en `.gitignore` y que no haya secretos versionados en el árbol actual, pero **no** se hizo un barrido del historial completo (ver `SEC-SECRET-002`).
- **Repositorio `QKT-Pages`** — es un proyecto distinto; esta auditoría cubre solo ERP-QKT.

### Limitaciones estructurales

Todo lo que vive fuera del repositorio no se puede verificar leyendo código: la
configuración real de las variables de entorno en Railway, la política de acceso
del bucket de Cloudflare R2, el comportamiento exacto del edge de Railway al
reescribir cabeceras de proxy, los respaldos de PostgreSQL, la rotación de
credenciales y el monitoreo. Esos puntos se clasifican como `NO VERIFICABLE` y se
listan en la sección 7 con la evidencia concreta que hay que pedir. **Un control
`NO VERIFICABLE` no es un control seguro** — es un control desconocido.

### Método

Los hallazgos se derivan de leer el código, no de suponer. El hallazgo crítico
`SEC-XSS-001` se confirmó ejecutando una prueba real contra este commit (ver su
sección de evidencia). Para el resto se cita ruta y línea. Cuando un riesgo
depende de una condición externa, se dice explícitamente cuál.

---

## 3. Arquitectura y superficies de ataque

**Stack**: Django 6.1 · PostgreSQL (Railway) / SQLite (dev) · admin Django con tema
Jazzmin como única interfaz interna · WeasyPrint (PDFs) · Cloudflare R2 vía
`django-storages` · Brevo/Anymail (email) · WhatsApp Cloud API (Meta) · Openpay
por REST directo · gunicorn con 2 workers · despliegue en Railway.

No hay SPA ni API pública versionada: el ERP es el admin de Django, y lo público
son tres superficies concretas.

### Superficies expuestas a internet sin autenticación

| Ruta | Vista | Qué hace | Rate limit |
|---|---|---|---|
| `/` | `landing_publico` | Landing con contenido de BD | ✗ |
| `/cotizar/` | `cotizador_publico` | Formulario | ✗ |
| `POST /cotizar/enviar/` | `cotizador_enviar` | **Crea Cliente + Cotización, dispara WhatsApp/email** | ✓ 10/min |
| `/api/disponibilidad/` | `api_disponibilidad_fecha` | Consulta fechas | ✗ |
| `/api/fechas-ocupadas/` | `api_fechas_ocupadas` | Lista fechas bloqueadas | ✗ |
| `/api/cotizador/productos/` | `api_productos_cotizador` | Catálogo y precios | ✗ |
| `/api/cotizador/paquetes/` | `api_paquetes_cotizador` | Catálogo y precios | ✗ |
| `/api/cotizador/total/` | `api_total_cotizador` | Cálculo de importes | ✗ |
| `/airbnb/ical/eventos/` | `generar_ical_eventos` | **Nombre de cliente + evento + fecha de todas las cotizaciones confirmadas** | ✗ |
| `/aviso-de-privacidad/`, `/terminos-y-condiciones/` | `documento_publico` | Documentos legales (cacheados) | ✗ |
| `POST /pagos/openpay/webhook/` | `openpay_webhook_view` | Confirma cargos · Basic Auth | ✗ |
| `POST /api/nomina/sync-jibble/` | `webhook_sync_jibble` | Genera recibos de nómina · Bearer token | ✗ |

### Superficie semi-pública (autenticación por token en la URL)

| Ruta | Vista | Rate limit |
|---|---|---|
| `/mi-evento/` | `portal_acceso` (código + 4 dígitos del teléfono) | ✓ 20/min |
| `/mi-evento/<token>/` | `portal_evento` — cotización, pagos, contrato, comunicaciones | ✗ |
| `/mi-evento/<token>/cotizacion.pdf` · `plan-pagos.pdf` · `contrato.pdf` | Descargas | ✗ |
| `POST /mi-evento/<token>/pagar-openpay/` | `portal_procesar_pago_openpay` | ✓ 10/min |
| `/mi-evento/<token>/pago-3ds/` | `portal_retorno_3ds` | ✓ 20/min |
| `/mi-evento/<token>/ficha-paynet/<id>.pdf` | `portal_ficha_paynet` | ✓ 20/min |

El token es `secrets.token_urlsafe(32)` (256 bits) — no es adivinable. Su
debilidad no es la entropía sino que **no caduca ni rota nunca**
(`SEC-SESS-001`).

### Superficie autenticada

Todo bajo `/admin/`, protegido con `@staff_member_required` (verificado en las 48
vistas administrativas). El login está interceptado por `admin_login_limitado`
antes de `admin.site.urls`, con bloqueo por IP y por usuario.

### Activos sensibles

1. **Datos personales de clientes** — nombre, teléfono, email, RFC, razón social, código postal fiscal.
2. **Identificaciones oficiales** — `legal.SolicitudARCO.identificacion` (INE/pasaporte escaneados).
3. **Datos financieros** — cotizaciones, pagos, saldos, transacciones Openpay, pólizas contables, estados de cuenta bancarios.
4. **Nómina** — empleados y recibos.
5. **Credenciales de terceros** — llaves de producción de Openpay, token de WhatsApp Cloud API, API key de Brevo, credenciales de R2 y Jibble.
6. **Sesiones de staff** — el acceso al admin equivale al acceso a todo lo anterior.

---

## 4. Modelo de amenazas

### Actores y niveles de acceso

| Actor | Acceso | Puede alcanzar |
|---|---|---|
| Anónimo en internet | Rutas públicas | Landing, cotizador, APIs, feed iCal, documentos legales |
| Cliente con token | `/mi-evento/<token>/` | Su cotización, sus pagos, su contrato |
| Cliente sin token | `/mi-evento/` | Intento de acceso con código + 4 dígitos |
| Openpay (servidor) | Webhook con Basic Auth | Confirmación de cargos |
| Cron externo | Webhook con Bearer token | Generación de recibos de nómina |
| Staff | `/admin/` completo | **Todo el ERP, sin distinción de área** |
| Superusuario | `/admin/` + gestión de usuarios | Todo, incluida la creación de cuentas |

### Límites de confianza

1. **Internet → Django** — el más expuesto; incluye el POST anónimo que escribe en BD.
2. **Cliente con token → datos de su cotización** — el token es el único control.
3. **Staff → todo el ERP** — no hay frontera interna: es un límite plano.
4. **Django → servicios externos** (Openpay, Meta, Brevo, R2, Jibble) — credenciales en variables de entorno.
5. **Bucket R2 → internet** — configurado con `querystring_auth: False`; el archivo, una vez subido, se sirve por URL sin firma.

### Los cinco escenarios de abuso más importantes

1. **Toma de control del ERP vía el cotizador público.** Un atacante envía un formulario con HTML en el nombre; cuando cualquier persona del equipo abre el calendario del admin, el JavaScript del atacante corre con esa sesión. → `SEC-XSS-001` (confirmado en ejecución).
2. **Cosecha de la cartera de clientes desde una URL sin autenticación.** Si `ICAL_PUBLIC_TOKEN` no está configurado en Railway, `/airbnb/ical/eventos/` devuelve nombre, evento, número de personas y fecha de todas las cotizaciones confirmadas, a cualquiera que pida la URL. → `SEC-DATA-001`.
3. **Acceso al portal de un cliente ajeno por adivinación.** El identificador de cotización es secuencial y el segundo factor son 4 dígitos (10 000 combinaciones); los mensajes de error distinguen "no existe" de "no coincide", lo que permite enumerar qué códigos son válidos antes de gastar intentos. → `SEC-AUTHN-001`.
4. **Fuga de documentos sensibles por URL directa.** Contratos, identificaciones ARCO, recibos de nómina y estados de cuenta viven en un bucket que sirve URLs sin firma; el control de acceso del ERP no aplica a esas URLs. → `SEC-FILE-001`.
5. **Abuso de un empleado con acceso legítimo al admin.** Cualquier cuenta staff — incluida la de alguien contratado para una sola área — puede leer y modificar nómina, contabilidad, datos fiscales e identificaciones oficiales. → `SEC-AUTHZ-001`.

---

## 5. Tabla maestra de controles

| ID | Área | Control | Estado | Prioridad | Evidencia | Riesgo o hueco | Recomendación | Verificación |
|---|---|---|---|---|---|---|---|---|
| A-01 | Autorización | Autorización server-side en cada endpoint | IMPLEMENTADO | — | 48 vistas admin con `@staff_member_required`; `legal/views.py:16-17` usa `permission_required` | — | Conservar | Test que recorra las rutas admin sin sesión y espere 302 |
| A-02 | Autorización | Separación de roles / mínimo privilegio | NO IMPLEMENTADO | P1 | Solo `is_staff` en todo el ERP; único `permission_required` en `legal/views.py:17` | Cualquier staff accede a nómina, contabilidad, ARCO y datos fiscales | Grupos por área + `permission_required` en vistas sensibles | `SEC-AUTHZ-001` |
| A-03 | Autorización | Protección IDOR/BOLA en el portal | IMPLEMENTADO | — | `views_openpay.py:79-82` filtra `OpenpayTransaccion` por `cotizacion=portal.cotizacion` | — | Conservar; es el patrón correcto | Test cruzado entre dos portales |
| A-04 | Autorización | Mass assignment | IMPLEMENTADO | — | Sin `ModelForm` con `fields='__all__'` en vistas públicas; el cotizador asigna campo por campo (`views_cotizador.py:206-260`) | — | Conservar | Revisión en cada PR que toque el cotizador |
| B-01 | Autenticación | Hashing de contraseñas | IMPLEMENTADO | — | Default de Django 6 (PBKDF2-SHA256); `AUTH_PASSWORD_VALIDATORS` con 4 validadores (`settings.py:172-177`) | — | Conservar | `manage.py check` |
| B-02 | Autenticación | MFA para administradores | NO IMPLEMENTADO | P3 | Sin `django-otp` ni equivalente en `requirements.txt` | Una contraseña filtrada da acceso total al ERP | `django-otp` + `TOTPDevice` para superusuarios | `SEC-AUTHN-002` |
| B-03 | Autenticación | Bloqueo de fuerza bruta en login | IMPLEMENTADO | — | `ratelimit.py:124-163`, `urls.py:43-57`; buckets independientes IP/usuario | — | Conservar | `core_erp/test_ratelimit.py` (9 tests) |
| B-04 | Autenticación | Enumeración de cuentas en el portal | PARCIAL | P1 | `views_portal.py:107-109` distingue "no encontramos" de "no coinciden" | Permite enumerar códigos válidos | Mensaje único e indistinguible | `SEC-AUTHN-001` |
| B-05 | Sesiones | Cookies `HttpOnly`/`Secure`/`SameSite` | IMPLEMENTADO | — | `settings.py:38-39, 54-56` | — | Conservar | `check --deploy` (limpio) |
| B-06 | Sesiones | Expiración por inactividad | IMPLEMENTADO | — | `settings.py:50-53`: 30 min idle, `SESSION_SAVE_EVERY_REQUEST=True` | — | Considerar 15 min por datos financieros | Test de sesión expirada |
| B-07 | Sesiones | Caducidad/rotación del token de portal | NO IMPLEMENTADO | P2 | `models.py:1362-1381`: `token_urlsafe(32)`, sin expiración | Token permanente en historiales, correos y WhatsApp | Campo `expira_en` + rotación al cerrar el evento | `SEC-SESS-001` |
| B-08 | Autenticación | Recuperación de contraseña | NO APLICA | — | Sin vistas de `password_reset` registradas; el reset se hace por consola o desde el admin | No hay flujo expuesto que atacar | — | — |
| B-09 | CSRF | Protección CSRF | PARCIAL | P2 | `CsrfViewMiddleware` activo; `@csrf_exempt` en `views_cotizador.py:86` y `views_openpay.py:298`, `nomina/views.py:329` | El del cotizador es un POST público que escribe en BD y gasta cuota de WhatsApp | Quitar el exempt del cotizador; los webhooks sí lo justifican | `SEC-CSRF-001` |
| C-01 | Rate limiting | Almacén compartido entre workers | IMPLEMENTADO | — | `settings.py:155-165` `DatabaseCache`; `gunicorn --workers 2` | — | Conservar | `test_ratelimit.py::CacheCompartidoTest` |
| C-02 | Rate limiting | Resistencia a `X-Forwarded-For` | PARCIAL | P2 | `ratelimit.py:19-42` cuenta desde la derecha con `RATELIMIT_TRUSTED_PROXY_COUNT` | La corrección depende de que el edge de Railway **añada** la IP real; no verificable desde el repo | Confirmar con evidencia del edge | `SEC-RL-002` |
| C-03 | Rate limiting | Cobertura de rutas | PARCIAL | P2 | Solo 6 vistas decoradas; sin límite en `portal_evento`, descargas, las 5 APIs del cotizador, ambos webhooks | Enumeración, scraping y DoS de bajo costo | Extender a todo endpoint público | `SEC-RL-001` |
| C-04 | Rate limiting | Límites diferenciados y TTL correcto | IMPLEMENTADO | — | `ratelimit.py:50-73`: no usa `cache.incr()`, fija timeout en cada escritura | — | Conservar | `test_contar_no_extiende_el_ttl_al_incrementar` |
| C-05 | Rate limiting | Límite de tamaño de body y de archivos | IMPLEMENTADO | — | Defaults de Django 6 sin sobreescribir: `DATA_UPLOAD_MAX_MEMORY_SIZE` 2.5 MB, `DATA_UPLOAD_MAX_NUMBER_FILES` 100 | — | No subirlos | Revisión de settings |
| C-06 | Rate limiting | Pruebas de evasión | IMPLEMENTADO | — | `test_ratelimit.py::test_x_forwarded_for_no_permite_rotar_la_ip` | — | Ampliar al portal | — |
| D-01 | Inyecciones | SQL injection | IMPLEMENTADO | — | Cero `raw()`, `RawSQL` o `cursor.execute` con interpolación fuera de tests | — | Conservar | `grep` en cada PR |
| D-02 | Inyecciones | Deserialización y evaluación dinámica | IMPLEMENTADO | — | Cero `eval`, `exec`, `pickle`, `yaml.load` | — | Conservar | `grep` en cada PR |
| D-03 | XSS | XSS almacenado en el admin | **NO IMPLEMENTADO** → *corregido, ver §0* | **P0** | `calendario_unificado.html:361` + `airbnb/views.py:125` — **confirmado en ejecución** | Ejecución de JS con sesión de staff, desde un POST anónimo | `json_script` en vez de `|safe` | `SEC-XSS-001` |
| D-04 | XSS | Escapado en el resto de plantillas | IMPLEMENTADO | — | Autoescape activo; los otros `|safe` son `json.dumps` de datos no controlados por el usuario (`comercial/views.py:500-501`) o constantes (`paynet`) | — | Conservar | Revisión de cada `|safe` nuevo |
| D-05 | Inyecciones | Inyección CRLF en el feed iCal | NO IMPLEMENTADO → *corregido, ver §0* | P2 | `airbnb/views.py:346-349`: `nombre_evento` y `cliente.nombre` sin escapar en el `.ics` | Inyección de propiedades iCal en los calendarios que consuman el feed | Escapar `\`, `;`, `,`, CR y LF (RFC 5545) | `SEC-INJ-001` |
| D-06 | Inyecciones | SSRF | NO APLICA | — | Ninguna petición saliente toma la URL de entrada del usuario; todas son constantes o derivadas de settings | — | — | — |
| D-07 | Validación | Validación en backend | PARCIAL | P2 | El cotizador valida manualmente (`views_cotizador.py:137-160`); no hay esquema formal | Campos como `notas` o `tipo_evento` entran sin restricción de contenido | Formularios de Django en endpoints públicos | `SEC-VAL-001` |
| D-08 | Redirects | Redirects abiertos | IMPLEMENTADO | — | Todos los `redirect()` van a rutas internas o a `reverse()`; el único externo es `contrato.archivo.url` (storage propio) | — | Conservar | — |
| E-01 | Archivos | URLs de descarga sin firma | NO IMPLEMENTADO | P1 | `settings.py:239` `querystring_auth: False` + `custom_domain` | Contratos, identificaciones ARCO, nómina y estados de cuenta accesibles por URL directa; anula `legal/views.py:18` | Activar URLs firmadas o bucket privado + proxy autenticado | `SEC-FILE-001` |
| E-02 | Archivos | Validación de tipo real | NO IMPLEMENTADO | P2 | Ningún `FileField` de los 16 inventariados tiene `validators` | Un ejecutable o HTML activo subido al bucket queda servible | `FileExtensionValidator` + verificación de firma | `SEC-FILE-002` |
| E-03 | Archivos | Nombres generados por el servidor | PARCIAL | — | `file_overwrite: False` añade sufijo aleatorio; el nombre base es el del cliente | Nombres semi-predecibles, sin sobrescritura | Aceptable si E-01 se corrige | — |
| E-04 | Archivos | Autorización en la descarga | PARCIAL | P1 | `legal/views.py:16-40` correcto; el resto expone `archivo.url` directo | Depende íntegramente de E-01 | Ver `SEC-FILE-001` | — |
| F-01 | Secretos | Secretos fuera del repositorio | IMPLEMENTADO | — | `.gitignore:14` `.env`; `git ls-files` solo devuelve `.env.example`; `db.sqlite3` ignorado | — | Conservar | `git ls-files` en CI |
| F-02 | Secretos | Barrido del historial de git | NO VERIFICABLE | P2 | No se auditó el historial completo | Un secreto commiteado y luego borrado sigue en el historial | `gitleaks detect --log-opts="--all"` | `SEC-SECRET-002` |
| F-03 | Secretos | Separación por ambiente y rotación | NO VERIFICABLE | P2 | `OPENPAY_MODE` conmuta ambiente; la rotación vive fuera del repo | — | Documentar calendario de rotación | Sección 7 |
| F-04 | Cripto | Comparación en tiempo constante | IMPLEMENTADO | — | `views_openpay.py:293-294`, `nomina/views.py:339`, `airbnb/views.py:311` usan `hmac.compare_digest` | — | Conservar | — |
| F-05 | Cripto | Aleatoriedad segura | IMPLEMENTADO | — | `models.py:1380` `secrets.token_urlsafe(32)` | — | Conservar | — |
| F-06 | Datos | Redacción en logs | IMPLEMENTADO | — | `test_ratelimit.py` verifica que el log no incluya contraseñas; buckets de usuario hasheados (`ratelimit.py:106-109`) | — | Conservar | `test_log_no_incluye_password_ni_credenciales_completas` |
| F-07 | Datos | Detalle de excepciones al cliente | NO IMPLEMENTADO | P2 | `views_cotizador.py:371, 394`; `nomina/views.py:377` devuelven `str(e)` | Filtra rutas, nombres de tablas y detalles internos | Mensaje genérico + log del detalle | `SEC-INFO-001` |
| G-01 | Configuración | HTTPS, HSTS, cookies seguras | IMPLEMENTADO | — | `settings.py:33-42`; `check --deploy` sin advertencias | — | Conservar | `manage.py check --deploy` |
| G-02 | Configuración | `SECURE_PROXY_SSL_HEADER` | PARCIAL | P2 | No está definido en `settings.py` pese a correr detrás del edge de Railway | `request.is_secure()` puede devolver `False` siempre; afecta a `SECURE_SSL_REDIRECT` y a la construcción de URLs absolutas | Definirlo tras confirmar la cabecera del edge | `SEC-CFG-001` |
| G-03 | Configuración | CSP | PARCIAL | P2 | `middleware.py:36-49` cubre landing, cotizador y APIs; `/admin/` excluido a propósito; portal solo Report-Only opcional | El admin — la superficie con más privilegio — no tiene CSP, que es justo la mitigación de defensa en profundidad para `SEC-XSS-001` | CSP al admin sin `unsafe-inline` en `script-src` | `SEC-CFG-002` |
| G-04 | Configuración | Cabeceras `nosniff`, `frame-ancestors`, `Referrer-Policy` | PARCIAL | P3 | `nosniff` y `X-Frame-Options` vía settings; `frame-ancestors` solo en la CSP pública; **sin `Referrer-Policy`** | El token del portal puede filtrarse por `Referer` al navegar a un dominio externo | `Referrer-Policy: strict-origin-when-cross-origin` global | `SEC-CFG-003` |
| G-05 | Configuración | `DEBUG` y trazas en producción | IMPLEMENTADO | — | `settings.py:11` default `False`, sin default de `SECRET_KEY` | — | Conservar | `check --deploy` |
| G-06 | Configuración | CORS | NO APLICA | — | Sin `django-cors-headers`; no hay consumo cross-origin | — | — | — |
| G-07 | Contenedor | Ejecución sin privilegios | NO IMPLEMENTADO | P3 | `Dockerfile` sin `USER`: corre como root | Una RCE tendría root dentro del contenedor | `USER` no privilegiado | `SEC-CFG-004` |
| H-01 | Dependencias | Vulnerabilidades conocidas | IMPLEMENTADO | — | `pip-audit -r requirements.txt` → sin vulnerabilidades (2026-08-11) | — | Conservar | `pip-audit` en CI (ya existe) |
| H-02 | Dependencias | Lockfile reproducible | NO IMPLEMENTADO | P2 | `requirements.txt` sin versiones fijas salvo `Django>=6.0` | Un build hoy y otro mañana instalan versiones distintas; imposibilita reproducir un incidente | `pip-compile` → `requirements.lock` | `SEC-DEP-001` |
| H-03 | CI | Acciones fijadas por SHA | PARCIAL | P3 | `ai-*.yml` fijadas por SHA; `ci.yml:13,16,50,55` usa `@v4`/`@v5` | Menor: son acciones oficiales de GitHub | Fijar por SHA también en `ci.yml` | `SEC-CI-002` |
| H-04 | CI | Permisos mínimos del token | IMPLEMENTADO | — | `ai-implement.yml:7` y `ai-review-merge.yml:8` con `permissions: {}` y elevación por job | — | Conservar | — |
| H-05 | CI | SAST y secret scanning | NO IMPLEMENTADO | P2 | `pyproject.toml:12` selecciona solo `E,F,W,I` — sin `S` (flake8-bandit); sin escaneo de secretos | Ningún análisis estático de seguridad corre nunca | Añadir `S` a ruff + `gitleaks` | `SEC-CI-001` |
| H-06 | CI | Gates que fallen de verdad | NO IMPLEMENTADO | P2 | `ci.yml:29` `continue-on-error: true` en el lint (691 hallazgos actuales, ninguno de seguridad) | El gate existe pero no bloquea nada | Corregir la deuda y quitar el flag | `SEC-CI-001` |
| I-01 | Logging | Registro de eventos de seguridad | PARCIAL | P2 | `ratelimit.py:145-149` y `urls.py:47-51` registran fallos y bloqueos; **`django.security` no está en `LOGGING`** | Los eventos que Django emite por su cuenta (host inválido, CSRF) no tienen handler explícito | Añadir el logger `django.security` | `SEC-LOG-001` |
| I-02 | Auditoría | Auditoría de acciones administrativas | IMPLEMENTADO | — | `django.contrib.admin.LogEntry` nativo; `legal.AccesoIdentificacionARCO` registra cada descarga con usuario e IP | — | Conservar; el patrón de `legal` es el modelo a seguir | — |
| I-03 | Logging | Correlation/request ID | NO IMPLEMENTADO | P3 | `settings.py:119-134` sin `filters` ni formato con ID | Difícil correlacionar eventos de un mismo request | Middleware de request ID | `SEC-LOG-002` |
| I-04 | Respuesta | Alertas accionables y runbook | NO VERIFICABLE | P1 | Nada en el repositorio | No se sabe si alguien se entera de un incidente | Runbook + alertas | Sección 7 |
| J-01 | Disponibilidad | Timeouts en llamadas salientes | IMPLEMENTADO | — | Openpay 20s (`services_openpay.py:370,457,531,572,619`), Jibble 15s, Meta 30s/`WA_TIMEOUT` | — | Conservar | `grep "requests\." sin timeout` |
| J-02 | Disponibilidad | Consultas de coste acotado | PARCIAL | P3 | `api_fechas_ocupadas` acota a 730 días; `calendario_unificado` carga **todas** las cotizaciones sin paginar | Degradación progresiva con el volumen | Acotar por rango de fechas | `SEC-DOS-001` |
| J-03 | Respaldos | Backups, restauración, RPO/RTO | NO VERIFICABLE | P1 | Nada en el repositorio | Sin evidencia de que exista respaldo ni de que se haya probado restaurarlo | Solicitar evidencia | Sección 7 |
| K-01 | Negocio | Manipulación de importes desde el cliente | IMPLEMENTADO | — | `views_openpay.py:129-138` valida contra `saldo_pendiente()` y `monto_minimo_pago()` en el servidor | — | Conservar | `comercial/test_openpay.py`, `test_monto_minimo_pago.py` |
| K-02 | Negocio | Idempotencia y doble cobro | IMPLEMENTADO | — | `OpenpayTransaccion.openpay_id` único + `update_or_create`; candado en cache (`views_openpay.py:165-172`) liberado en `finally` | — | Conservar | `comercial/test_openpay.py` |
| K-03 | Negocio | Webhooks autenticados | IMPLEMENTADO | — | `views_openpay.py:275-295` Basic Auth con `compare_digest` y rechazo explícito si falta configuración | — | Conservar | — |
| K-04 | Negocio | Protección contra replay en webhooks | PARCIAL | P3 | La idempotencia por `openpay_id` limita el efecto; no hay validación de frescura | Un payload capturado puede reenviarse | Aceptable dado K-02 | `SEC-BIZ-001` |
| K-05 | Negocio | Reautenticación en acciones sensibles | NO IMPLEMENTADO | P3 | Ninguna acción del admin la pide | Una sesión secuestrada opera sin fricción | Confirmación para borrados y cambios de permisos | — |
| L-01 | Pruebas | Suite existente | PARCIAL | P2 | 20 archivos de test; fuertes en negocio (pagos, IVA, notificaciones) y rate limiting | Sin pruebas de autorización cruzada, XSS, CSRF ni cabeceras | Añadir suite de seguridad | `SEC-TEST-001` |
| L-02 | Pruebas | Pruebas de rate limiting | IMPLEMENTADO | — | `core_erp/test_ratelimit.py`, 9 tests incluyendo evasión por XFF | — | Conservar | — |

**Conteo por estado**: IMPLEMENTADO 28 · PARCIAL 16 · NO IMPLEMENTADO 15 · NO APLICA 3 · NO VERIFICABLE 5 · **Total 67**

---

## 6. Hallazgos detallados

### SEC-XSS-001 — XSS almacenado no autenticado con toma de sesión de staff

> **Corregido.** El calendario y los dos dashboards usan `json_script`; hay
> tests de regresión en `airbnb/test_seguridad.py`. Ver §0.

- **Severidad**: Crítica · **Prioridad**: P0
- **Componente**: `airbnb/views.py::calendario_unificado` + `airbnb/templates/admin/airbnb/calendario_unificado.html`
- **OWASP**: A03:2021 Injection · **CWE**: CWE-79, CWE-116

**Evidencia**

`airbnb/views.py:125` serializa los eventos y los pasa al contexto:

```python
'eventos_json': json.dumps(eventos_lista, cls=DjangoJSONEncoder),
```

donde cada entrada se arma con datos controlados por el usuario (`airbnb/views.py:53-54`):

```python
eventos_lista.append({
    'title': f"{icon} {c.cliente.nombre} - {c.nombre_evento}",
```

y la plantilla lo inserta sin escapar dentro de un bloque `<script>`
(`calendario_unificado.html:358-361`):

```html
<script>
document.addEventListener('DOMContentLoaded', function() {
    var calendarEl = document.getElementById('calendar');
    var eventos = {{ eventos_json|safe }};
```

`json.dumps` escapa comillas y barras invertidas, pero **no** escapa `<`, `>` ni
`&`. Una cadena que contenga `</script>` cierra el bloque desde dentro del literal
JavaScript, y lo que siga se interpreta como HTML nuevo.

`Cotizacion.objects.exclude(estado='CANCELADA')` (`airbnb/views.py:43`) incluye
las cotizaciones creadas por el formulario público, así que el atacante no
necesita que nadie apruebe nada.

**Confirmación en ejecución**

Se ejecutó una prueba contra este commit (`f813dcc`), en base de datos de test,
que reproduce la cadena completa:

1. `POST /cotizar/enviar/` anónimo, sin cookies ni CSRF, con
   `nombre = "</script><script>window.PWNED=1</script>"` → **HTTP 200**.
2. El valor queda persistido: `nombre_evento` guardado como
   `'Evento General — </script><script>window.PWNED=1</script>'`.
3. `GET /admin/calendario/` con sesión de staff → el cuerpo devuelve:

```
var eventos = [{"title": " </SCRIPT><SCRIPT>WINDOW.PWNED=1</SCRIPT> - Evento General — </script><script>window.PWNED=1</script>", "start": "2026-09-10", ...
```

Ambas copias del payload son ejecutables: el nombre del cliente se normaliza a
mayúsculas, y `</SCRIPT>` cierra el bloque igual que `</script>` porque el
parseo de etiquetas HTML no distingue mayúsculas. El payload en `nombre_evento`
llega intacto.

**Escenario de abuso**

Un atacante envía el formulario público de cotización con un payload en el nombre
o en el tipo de evento. No necesita cuenta, ni credenciales, ni que la cotización
sea aprobada. Cuando cualquier persona del equipo abre el calendario del admin —
una pantalla de uso diario, enlazada desde el menú superior — el script corre con
su sesión: puede leer y exfiltrar la cartera de clientes con teléfonos y RFC,
consultar pagos y contabilidad, o emitir peticiones autenticadas al admin
(incluida la creación de un superusuario, leyendo el token CSRF del DOM). Nada de
esto deja rastro distinguible de actividad legítima del usuario.

`SESSION_COOKIE_HTTPONLY = True` impide robar la cookie por JavaScript, pero **no**
impide actuar con ella desde la propia página.

**Impacto**

Técnico: ejecución de código en el contexto de la aplicación con privilegios de
staff; escalada a superusuario si la víctima lo es. Negocio: exposición de datos
personales y fiscales de clientes (obligaciones LFPDPPP), de nómina y de
contabilidad; posibilidad de manipular registros financieros.

**Probabilidad**: alta. Sin requisitos previos, con un vector de entrada público y
documentado, y una víctima que abre la pantalla afectada de forma rutinaria.

**Corrección recomendada**

Sustituir la interpolación por `json_script`, que es el mecanismo de Django para
exactamente este caso y escapa `<`, `>` y `&`:

```html
{{ eventos_json|json_script:"eventos-data" }}
<script>
    var eventos = JSON.parse(document.getElementById('eventos-data').textContent);
```

`json_script` espera el objeto Python, no una cadena ya serializada, así que en la
vista debe pasarse `eventos_lista` directamente en vez de `json.dumps(...)`.

Revisar con el mismo criterio `comercial/templates/admin/dashboard.html:162-167` y
`airbnb/templates/admin/airbnb/dashboard.html:193-196`: hoy solo contienen fechas
y cifras agregadas, no datos de usuario, pero comparten el patrón y basta con que
alguien añada una etiqueta con nombre de cliente para que se vuelvan explotables.

**Prueba de aceptación**

Test que cree un cliente con `nombre = '</script><script>window.x=1</script>'`,
haga `GET /admin/calendario/` con sesión de staff y afirme que
`'</script><script>' not in response.content.decode()`.

---

### SEC-DATA-001 — Feed iCal público abierto por defecto (fail-open)

> **Verificado como exposición activa y corregido.** `ICAL_PUBLIC_TOKEN` no
> estaba definida en Railway, así que la condición descrita abajo se cumplía en
> producción: el `.ics` se descargó desde una sesión anónima con nombres reales
> de clientes. Ver §0.

- **Severidad**: Alta · **Prioridad**: P1
- **Componente**: `airbnb/views.py::generar_ical_eventos`, ruta `/airbnb/ical/eventos/`
- **OWASP**: A01:2021 Broken Access Control · **CWE**: CWE-306, CWE-1188

**Evidencia** (`airbnb/views.py:307-313`):

```python
token_esperado = config('ICAL_PUBLIC_TOKEN', default='')
if token_esperado:
    import hmac
    token_recibido = request.GET.get('token', '')
    if not hmac.compare_digest(token_recibido, token_esperado):
        return HttpResponseForbidden('Token inválido')
```

Si la variable no está configurada, `token_esperado` es `''` y **el bloque
completo se salta**: la URL queda abierta a cualquiera. El docstring lo declara
como decisión deliberada ("Si no, la URL queda abierta (para
retrocompatibilidad)"), lo que la convierte en un fallo de diseño, no en un
descuido.

El contenido servido (`airbnb/views.py:344-349`) incluye por cada cotización
confirmada:

```python
titulo = f"EVENTO QKT: {cot.nombre_evento}"
descripcion = f"Cliente: {cot.cliente.nombre}\\nPersonas: {cot.num_personas}\\nEstado: Confirmado"
```

`ICAL_PUBLIC_TOKEN` sí figura en `.env.example:68`, pero eso no dice nada sobre
si está definido en Railway — y el código no falla si no lo está.

**Escenario de abuso**

Alguien pide `GET /airbnb/ical/eventos/` y recibe, en texto plano, el nombre de
cada cliente con evento confirmado, el nombre del evento, el número de asistentes
y la fecha. Es la agenda comercial del negocio, útil para un competidor y
suficiente para construir un pretexto creíble en un intento de fraude dirigido
contra un cliente ("le llamamos de Quinta Ko'ox Tanil por su evento del 12 de
septiembre").

**Impacto**: exposición de datos personales sin consentimiento (LFPDPPP) e
inteligencia comercial. **Probabilidad**: alta si la variable no está configurada
en producción — condición que **no se puede verificar desde el repositorio** y que
debe confirmarse de inmediato.

**Corrección**: invertir la lógica a fail-closed — sin token configurado, la vista
responde 403. Dado que la consume Airbnb, conviene además reducir el contenido al
mínimo que cumple su función (bloquear fechas): un `SUMMARY` genérico tipo "No
disponible", sin nombre de cliente ni número de personas.

**Prueba de aceptación**: con `ICAL_PUBLIC_TOKEN` vacío, `GET /airbnb/ical/eventos/`
devuelve 403; con token válido, 200 y el cuerpo no contiene el nombre del cliente.

---

### SEC-AUTHN-001 — Acceso al portal adivinable y con enumeración de códigos

- **Severidad**: Alta · **Prioridad**: P1
- **Componente**: `comercial/views_portal.py::portal_acceso`
- **OWASP**: A07:2021 Identification and Authentication Failures · **CWE**: CWE-307, CWE-204

**Evidencia** (`views_portal.py:91-111`):

```python
cotizacion_id = int(codigo_limpio)
cotizacion = Cotizacion.objects.select_related('cliente').get(id=cotizacion_id)
tel_cliente = ''.join(filter(str.isdigit, cotizacion.cliente.telefono or ''))
if tel_cliente[-4:] == telefono:
    ...
    else:
        error = "Los datos no coinciden. Verifica tu código y teléfono."
except Cotizacion.DoesNotExist:
    error = "No encontramos una cotización con ese código."
```

Tres debilidades que se combinan:

1. **Identificador secuencial**: el "código" es el `id` autoincremental de la
   cotización. No hay que adivinarlo, se enumera desde 1.
2. **Segundo factor de 4 dígitos**: 10 000 combinaciones, y muchas descartables
   (los teléfonos de Yucatán comparten prefijos).
3. **Mensajes distinguibles**: "No encontramos una cotización con ese código" vs.
   "Los datos no coinciden" revela qué identificadores existen antes de gastar un
   solo intento en adivinar dígitos.

El rate limit es de 20 intentos por minuto **por IP** (`views_portal.py:69`), y no
hay contador por cotización: quien distribuya los intentos entre IPs no encuentra
techo agregado. A 20/min desde una sola IP, agotar las 10 000 combinaciones de una
cotización toma unas 8 horas; con diez IPs, menos de una.

Además, un acceso exitoso **crea el `PortalCliente` si no existía**
(`views_portal.py:98-101`), de modo que el atacante genera un token permanente
para una cotización que quizá nunca tuvo portal.

**Escenario de abuso**: enumerar identificadores válidos, elegir los recientes y
forzar los 4 dígitos. Con el token resultante se accede a la cotización completa,
el desglose de pagos, el plan de pagos, el contrato en PDF y el historial de
comunicaciones — y el token no caduca (`SEC-SESS-001`).

**Impacto**: acceso no autorizado a datos personales y financieros de un cliente
concreto. **Probabilidad**: media-alta; requiere esfuerzo sostenido pero ningún
conocimiento previo.

**Corrección**:
1. Unificar el mensaje de error para los tres casos de fallo.
2. Añadir un contador de intentos **por cotización**, además del de IP, reusando el
   patrón de `ratelimit.py::_buckets_login` (bucket por identificador hasheado).
3. No crear el `PortalCliente` en el intento de acceso: que lo cree el flujo
   comercial y que `portal_acceso` solo resuelva uno existente y activo.
4. Considerar un tercer dato de baja entropía pero no enumerable (fecha del evento).

**Prueba de aceptación**: test que verifique el mismo mensaje para código
inexistente y para teléfono incorrecto, y que tras N intentos fallidos contra la
misma cotización desde IPs distintas la respuesta sea 429.

---

### SEC-FILE-001 — Archivos sensibles servidos por URL sin firma

> **Verificado como exposición activa** (2026-08-12): el bucket sirve lectura
> anónima. Pendiente de corregir — requiere separar buckets, ver §0 y Fase 2.

- **Severidad**: Alta · **Prioridad**: P1
- **Componente**: `core_erp/settings.py::STORAGES`, `legal/views.py`, `comercial/views_portal.py::portal_descargar_contrato`
- **OWASP**: A01:2021 Broken Access Control · **CWE**: CWE-284, CWE-552

**Evidencia** (`settings.py:236-239`):

```python
"custom_domain": config('CLOUDFLARE_R2_CUSTOM_DOMAIN', default='media.quintakooxtanil.com'),
"region_name": "auto",
"signature_version": "s3v4",
"querystring_auth": False,
```

`querystring_auth: False` indica a `django-storages` que **no** firme las URLs:
`archivo.url` devuelve una URL permanente sobre el dominio público, sin
expiración ni credencial. Los `FileField` afectados incluyen:

| Modelo | Campo | Contenido |
|---|---|---|
| `legal.SolicitudARCO` | `identificacion` | **Identificación oficial escaneada** |
| `comercial.ContratoServicio` | `archivo` | Contrato firmado con datos personales |
| `nomina.ReciboNomina` | `archivo_pdf` | Recibo de nómina |
| `contabilidad.EstadoCuentaBancario` | `archivo` | Estado de cuenta bancario |
| `facturacion.SolicitudFactura` | `archivo_pdf`/`archivo_xml`/`archivo_zip` | CFDI |

La contradicción es explícita en `legal/views.py:19`, cuyo docstring dice
*"Sirve la identificación sin revelar una URL directa del storage"*: la vista
exige login, permiso `legal.ver_identificacion_arco` y registra cada acceso en
`AccesoIdentificacionARCO` — pero el archivo sigue siendo accesible por su URL
directa, que no pasa por ninguno de esos controles. El registro de auditoría de
accesos ARCO, que existe precisamente para cumplir la LFPDPPP, solo ve los
accesos que pasan por la vista.

En `views_portal.py:252`, `portal_descargar_contrato` hace
`redirect(contrato.archivo.url)`: entrega al navegador del cliente esa URL
permanente, que queda en su historial y puede compartirse sin querer.

**Requisito previo**: que el bucket permita lectura anónima. **Confirmado el
2026-08-12** (ver §0): las imágenes de la landing cargan desde
`media.quintakooxtanil.com` en sesión anónima.

**Escenario de abuso**: quien obtenga una URL — por historial, reenvío de correo,
cabecera `Referer` (`SEC-CFG-003`), o adivinando rutas como
`arco/identificaciones/<nombre>` — accede al documento sin autenticación y sin
dejar registro.

**Impacto**: exposición de identificaciones oficiales y documentos financieros.
Para las identificaciones ARCO el impacto regulatorio es directo: son datos
personales recabados para ejercer derechos ARCO, con deber reforzado de custodia.

**Corrección**:
1. **Inmediato**: verificar la política de acceso del bucket R2.
2. Pasar el bucket a privado y `querystring_auth: True`, sirviendo los archivos
   por URLs firmadas de vida corta.
3. Para lo verdaderamente sensible (ARCO, nómina, contratos), servir siempre por
   vista autenticada con `FileResponse` — el patrón que `legal/views.py` ya
   implementa — y nunca exponer `archivo.url`.
4. Separar buckets: uno público solo para imágenes de la landing, otro privado
   para documentos.

**Prueba de aceptación**: `SolicitudARCO.identificacion.url` no debe ser accesible
sin credenciales; test que afirme que la URL generada contiene parámetros de firma
o que `portal_descargar_contrato` sirve el contenido en vez de redirigir.

---

### SEC-AUTHZ-001 — Autorización plana: `is_staff` da acceso a todo el ERP

- **Severidad**: Alta · **Prioridad**: P1
- **Componente**: todas las vistas administrativas y `*/admin.py`
- **OWASP**: A01:2021 Broken Access Control · **CWE**: CWE-269, CWE-266

**Evidencia**: de las 48 vistas administrativas inventariadas, 47 usan
`@staff_member_required` y ninguna comprueba permisos más finos. La única
excepción en todo el repositorio es `legal/views.py:17`:

```python
@permission_required('legal.ver_identificacion_arco', raise_exception=True)
```

El admin de Django sí respeta los permisos por modelo si se asignan, pero las
vistas a medida — nómina (`cargar_nomina`), reportes contables
(`reporte_balanza`, `reporte_estado_resultados`, `reporte_balance_general`),
cartera (`ver_cartera_cxc`), conciliación bancaria, reporte fiscal de Airbnb,
importación histórica — solo verifican `is_staff`.

**Escenario de abuso**: una persona contratada para capturar cotizaciones recibe
una cuenta staff. Con ella puede consultar los recibos de nómina de sus
compañeros, la contabilidad completa, los RFC y razones sociales de todos los
clientes, y disparar `importar_historico_view`. No hay que vulnerar nada: el
sistema lo permite por diseño.

Este hallazgo además **amplifica `SEC-XSS-001`**: como no hay compartimentación,
comprometer la sesión de cualquier staff equivale a comprometer todo.

**Impacto**: exposición interna de datos de nómina, fiscales y contables; y
ausencia de trazabilidad por área. **Probabilidad**: alta — no requiere ataque,
solo una cuenta legítima.

**Corrección**:
1. Definir grupos por área: Ventas, Contabilidad, Nómina, Dirección.
2. Sustituir `@staff_member_required` por `@permission_required` con el permiso
   correspondiente en las vistas de cada área.
3. Asignar permisos por modelo en el admin según el grupo.
4. Reservar el acceso a `legal` (ARCO) a un grupo mínimo, como ya está.

Es el hallazgo de mayor esfuerzo de la lista (`L`) y conviene entregarlo por
áreas, empezando por nómina y contabilidad.

**Prueba de aceptación**: test con un usuario staff sin permisos de nómina que
recibe 403 al pedir `/admin/nomina/cargar/`.

---

### SEC-RL-001 — Cobertura incompleta del rate limiting

- **Severidad**: Media · **Prioridad**: P2 · **CWE**: CWE-770

**Evidencia**: solo 6 vistas están decoradas (`cotizador_enviar`,
`portal_acceso`, `portal_pago_openpay`, `portal_ficha_paynet`,
`portal_retorno_3ds`, más el bloqueo específico del login). Quedan sin límite:

| Endpoint | Riesgo |
|---|---|
| `portal_evento`, `portal_descargar_cotizacion/plan/contrato` | Generan PDFs con WeasyPrint — costosos en CPU |
| `api_disponibilidad_fecha`, `api_fechas_ocupadas`, `api_productos_cotizador`, `api_paquetes_cotizador`, `api_total_cotizador` | Scraping del catálogo y precios |
| `/airbnb/ical/eventos/` | Ver `SEC-DATA-001` |
| `POST /pagos/openpay/webhook/` | Martilleo con credenciales inválidas |
| `POST /api/nomina/sync-jibble/` | Fuerza bruta del Bearer token |
| `documento_publico` | Mitigado por cache |

Las descargas del portal son las de mayor coste: cada petición renderiza un PDF
completo con WeasyPrint. Un atacante con un token válido — el suyo propio — puede
saturar CPU con un bucle trivial.

**Corrección**: aplicar `@rate_limit` a todos los endpoints públicos, con límites
por naturaleza: generación de PDF ~10/min, APIs de lectura ~60/min, webhooks
~120/min (holgado para el tráfico legítimo pero techo ante martilleo).

**Prueba de aceptación**: test parametrizado que recorra la lista de endpoints
públicos y verifique 429 al superar el límite.

---

### SEC-RL-002 — La resistencia a `X-Forwarded-For` depende del edge

- **Severidad**: Media · **Prioridad**: P2 · **CWE**: CWE-348 · **Estado**: PARCIAL / requiere evidencia externa

**Evidencia** (`ratelimit.py:32-42`): la implementación cuenta desde la derecha
tantos saltos como `RATELIMIT_TRUSTED_PROXY_COUNT` (default 1), que es la
estrategia correcta **siempre que** el edge de Railway *añada* la IP real del
cliente al final de la cabecera. Si el edge la reenviara sin modificar, el último
elemento sería controlable por el atacante y el rate limiting entero se podría
rotar a voluntad — incluido el bloqueo de fuerza bruta del login.

El código no puede saber cuál de los dos comportamientos aplica: es una propiedad
de la infraestructura.

**Corrección**: confirmar empíricamente en producción (petición con un XFF
fabricado y observar qué IP registra el log) y ajustar
`RATELIMIT_TRUSTED_PROXY_COUNT` si hiciera falta. Documentar el resultado en la
Memoria de `CLAUDE.md`.

**Prueba de aceptación**: enviar `X-Forwarded-For: 1.2.3.4` a `/admin/login/` en
producción y verificar en los Deploy Logs que la IP registrada no es `1.2.3.4`.

---

### SEC-CSRF-001 — `@csrf_exempt` en un POST público que escribe y gasta cuota

- **Severidad**: Media · **Prioridad**: P2 · **CWE**: CWE-352

**Evidencia** (`views_cotizador.py:85-88`):

```python
@rate_limit(key='cotizador_enviar', limit=10, window=60)
@csrf_exempt
@require_http_methods(["POST"])
def cotizador_enviar(request):
```

Cada envío crea un `Cliente` y una `Cotizacion`, registra una aceptación legal y
dispara una notificación de WhatsApp a `WA_NUMERO_NEGOCIO` — es decir, tiene coste
monetario y produce ruido operativo. El `csrf_exempt` permite que cualquier sitio
web haga que los navegadores de sus visitantes envíen cotizaciones falsas en
nombre de terceros.

Los otros dos `csrf_exempt` (webhook de Openpay y de Jibble) **sí están
justificados**: son servidor-a-servidor y tienen su propia autenticación.

**Corrección**: quitar `@csrf_exempt` y enviar el token CSRF desde el formulario
público (Django lo entrega vía cookie y el JS puede leerlo). Mientras tanto,
bajar el límite a 3-5/min y validar `Origin`/`Referer` contra
`CSRF_TRUSTED_ORIGINS`.

**Prueba de aceptación**: `POST /cotizar/enviar/` sin token CSRF devuelve 403.

---

### SEC-INFO-001 — Detalle de excepciones devuelto al cliente

- **Severidad**: Media · **Prioridad**: P2 · **CWE**: CWE-209

**Evidencia**: `views_cotizador.py:371` y `:394`, y `nomina/views.py:377`:

```python
return JsonResponse({'ok': False, 'error': str(e)}, status=500)
```

Con `DEBUG=False` Django oculta la traza, pero estos `str(e)` la sortean: un
`IntegrityError` o un `OperationalError` revelan nombres de tablas y columnas, y
un error de sistema de archivos revela rutas absolutas del contenedor. Es
información de reconocimiento gratuita.

Contrasta con el buen patrón ya usado en `views_openpay.py:196-197`, donde el
detalle va al log y el cliente recibe un mensaje genérico.

**Corrección**: mensaje genérico al cliente + `logger.exception()` con el detalle.

**Prueba de aceptación**: forzar una excepción en `api_disponibilidad_fecha` y
verificar que el cuerpo no contiene el texto de la excepción.

---

### SEC-INJ-001 — Inyección CRLF en el feed iCal

> **Corregido** de forma colateral: el `.ics` ya no interpola texto libre, solo
> el folio numérico de la cotización, así que no queda nada que escapar. Ver §0.

- **Severidad**: Media · **Prioridad**: P2 · **CWE**: CWE-93

**Evidencia** (`airbnb/views.py:346-360`): `nombre_evento` y `cliente.nombre` se
concatenan directamente en las propiedades `SUMMARY` y `DESCRIPTION` del `.ics`,
que es un formato delimitado por CRLF. RFC 5545 exige escapar `\`, `;`, `,` y los
saltos de línea dentro de un valor de texto; aquí no se escapa nada.

Los valores provienen del cotizador público, igual que en `SEC-XSS-001`.

**Escenario**: un `nombre_evento` con CRLF inyecta propiedades iCal arbitrarias
(por ejemplo un `ATTENDEE` o un `URL`) en el calendario de quien consuma el feed.

**Corrección**: función de escapado conforme a RFC 5545 aplicada a todo valor
interpolado. Se resuelve junto con la reducción de contenido de `SEC-DATA-001`.

**Prueba de aceptación**: cotización con `\r\n` en el nombre; el `.ics` resultante
tiene el mismo número de líneas `BEGIN:VEVENT` que cotizaciones.

---

### SEC-SESS-001 — Token de portal permanente, sin caducidad ni rotación

- **Severidad**: Media · **Prioridad**: P2 · **CWE**: CWE-613

**Evidencia** (`comercial/models.py:1362-1381`): `token_urlsafe(32)` — entropía
correcta — pero sin campo de expiración ni mecanismo de rotación. El modelo tiene
`activo`, que permite desactivar a mano, pero nada caduca solo.

El token viaja en la URL, así que queda en el historial del navegador, en los
correos y mensajes de WhatsApp donde se envió, en los servidores de esos
proveedores y —si no se corrige `SEC-CFG-003`— en la cabecera `Referer` hacia
sitios externos. Un token de hace dos años sigue dando acceso a la cotización, el
plan de pagos y el contrato.

**Corrección**: añadir `expira_en` (por ejemplo, 90 días después del evento) y
verificarlo en las cinco vistas del portal; permitir regenerar el token desde el
admin.

**Prueba de aceptación**: portal con `expira_en` en el pasado devuelve 404.

---

### SEC-CFG-001 — `SECURE_PROXY_SSL_HEADER` sin definir detrás del proxy

- **Severidad**: Media · **Prioridad**: P2 · **CWE**: CWE-16 · **Requiere evidencia externa**

**Evidencia**: `settings.py` activa `SECURE_SSL_REDIRECT = True` (línea 34) pero
no define `SECURE_PROXY_SSL_HEADER`. El TLS termina en el edge de Railway y la
aplicación recibe la petición por HTTP, de modo que `request.is_secure()` puede
devolver `False` de forma permanente. Eso afecta a `SECURE_SSL_REDIRECT` (riesgo
de bucle de redirección) y a `request.build_absolute_uri()`, que se usa en
`views_openpay.py:182` para construir la URL de retorno de 3-D Secure — generar
ahí una URL `http://` podría degradar el flujo de pago.

Que el sitio funcione hoy en producción sugiere que el edge ya envía la petición
de forma que Django la considera segura, pero **eso no está verificado** y una
configuración explícita no debería depender de un comportamiento implícito.

**Corrección**: tras confirmar que el edge envía `X-Forwarded-Proto`, añadir:

```python
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
```

**Prueba de aceptación**: en producción, una vista de diagnóstico o un log que
confirme `request.is_secure() is True`.

---

### SEC-CFG-002 — Sin CSP en el admin

- **Severidad**: Media · **Prioridad**: P2 · **CWE**: CWE-1021

**Evidencia** (`core_erp/middleware.py:19-20`): el admin queda deliberadamente
fuera de toda CSP porque Jazzmin/AdminLTE romperían con una política estricta.

La decisión es razonable en sí misma, pero deja sin defensa en profundidad
justamente a la superficie de mayor privilegio: una CSP con `script-src` sin
`unsafe-inline` habría contenido `SEC-XSS-001` aunque el escapado fallara.

**Corrección**: tras corregir `SEC-XSS-001`, introducir una CSP para `/admin/` en
modo `Report-Only`, recoger violaciones reales de Jazzmin y endurecerla por
etapas. Como mínimo `object-src 'none'`, `base-uri 'self'` y `frame-ancestors
'self'`, que no rompen AdminLTE.

**Prueba de aceptación**: `GET /admin/` devuelve cabecera CSP; la consola no
reporta violaciones que rompan funcionalidad.

---

### SEC-CFG-003 — Sin `Referrer-Policy`

- **Severidad**: Baja · **Prioridad**: P3 · **CWE**: CWE-200

**Evidencia**: `middleware.py:31` fija `Permissions-Policy` en todas las
respuestas, pero no hay `Referrer-Policy` ni en el middleware ni en `settings.py`
(`SECURE_REFERRER_POLICY` no está definido; el default de Django es
`same-origin`, que cubre lo esencial, pero no está declarado explícitamente).

Importa porque el token del portal viaja en la URL: al navegar desde
`/mi-evento/<token>/` a un dominio externo, el token puede filtrarse en la
cabecera `Referer`.

**Corrección**: `SECURE_REFERRER_POLICY = 'strict-origin-when-cross-origin'`.

**Prueba de aceptación**: la respuesta de `/mi-evento/<token>/` incluye la
cabecera.

---

### SEC-CFG-004 — Contenedor ejecutándose como root

- **Severidad**: Baja · **Prioridad**: P3 · **CWE**: CWE-250

**Evidencia**: el `Dockerfile` no declara `USER`; gunicorn corre como root.

**Corrección**: crear un usuario sin privilegios y cambiar a él antes del `CMD`,
tras verificar permisos de escritura en `staticfiles/`.

**Prueba de aceptación**: `whoami` en el contenedor no devuelve `root`.

---

### SEC-VAL-001 — Validación manual, sin esquema, en el endpoint público

- **Severidad**: Media · **Prioridad**: P2 · **CWE**: CWE-20

**Evidencia** (`views_cotizador.py:93-160`): el cuerpo se parsea con
`json.loads` y cae a `request.POST.dict()` si falla; cada campo se extrae con
`str(data.get(...)).strip()` y se valida a mano. Se comprueban nombre, teléfono y
fecha, pero `notas`, `tipo_evento` y `como_nos_encontro` entran sin restricción de
longitud ni de contenido — y los tres acaban en `nombre_evento`
(`views_cotizador.py:245-254`), que es el vehículo de `SEC-XSS-001` y
`SEC-INJ-001`.

**Corrección**: un `forms.Form` de Django para el cotizador, con tipos, longitudes
máximas y `choices` para los campos enumerados. Resuelve de raíz una familia de
problemas en vez de parchear cada consumidor.

**Prueba de aceptación**: campos fuera de rango o con tipo incorrecto devuelven
400 con mensaje de validación.

---

### SEC-FILE-002 — Sin validación de tipo real en las subidas

- **Severidad**: Media · **Prioridad**: P2 · **CWE**: CWE-434

**Evidencia**: ninguno de los 16 `FileField`/`ImageField` inventariados declara
`validators`. Los `ImageField` sí obtienen validación de Pillow si se guardan por
`ModelForm` (patrón ya adoptado en la carga masiva de la landing, ver Memoria de
`CLAUDE.md`), pero los `FileField` — contratos, XML de compras, estados de cuenta,
identificaciones ARCO — aceptan cualquier contenido.

Combinado con `SEC-FILE-001`, un HTML o SVG subido al bucket público quedaría
servible desde `media.quintakooxtanil.com`, un origen distinto al de la
aplicación pero de confianza aparente para los clientes.

**Corrección**: `FileExtensionValidator` en cada campo y verificación de firma
(magic bytes) para PDF y XML. Servir siempre con `Content-Disposition: attachment`
y `X-Content-Type-Options: nosniff`.

**Prueba de aceptación**: subir un `.html` renombrado a `.pdf` es rechazado por el
formulario.

---

### SEC-CI-001 — Gates de seguridad inexistentes o desactivados

- **Severidad**: Media · **Prioridad**: P2 · **CWE**: CWE-1127

**Evidencia**:
- `pyproject.toml:12` — `select = ["E", "F", "W", "I"]`: sin el ruleset `S`
  (flake8-bandit). No corre **ningún** análisis estático de seguridad.
- `ci.yml:27-29` — el paso de lint tiene `continue-on-error: true`. En el estado
  actual del repo `ruff check .` reporta 691 hallazgos (400 `W293`, 98 `I001`,
  79 `E701`, 7 `E722`…): todos de estilo, ninguno de seguridad, pero el volumen
  es lo que mantiene el gate desactivado.
- No hay escaneo de secretos en el pipeline.

`pip-audit` sí corre (`ci.yml:77-79`), aunque también con `continue-on-error`,
decisión defendible para avisos transitivos.

**Corrección**: (1) `ruff check --fix` para los 555 auto-corregibles y revisar a
mano los 7 `E722`; (2) quitar `continue-on-error` del lint; (3) añadir `S` al
`select` con las excepciones que haga falta; (4) añadir `gitleaks` al CI.

**Prueba de aceptación**: un PR que introduzca `subprocess.call(shell=True)` o una
clave con formato de secreto falla el CI.

---

### SEC-DEP-001 — Sin lockfile: builds no reproducibles

- **Severidad**: Media · **Prioridad**: P2 · **CWE**: CWE-1104

**Evidencia**: `requirements.txt` tiene 17 dependencias directas y **ninguna
versión fijada** salvo `Django>=6.0`. `pip install -r requirements.txt` hoy y
mañana pueden producir árboles distintos. El `Dockerfile:17` instala así en cada
build, de modo que un despliegue puede cambiar de versión de dependencia sin que
ningún commit lo refleje.

Esto no es solo higiene: impide reproducir el entorno exacto de un incidente y
abre la puerta a que una versión comprometida entre en producción sin revisión.

**Corrección**: `pip-compile` para generar `requirements.lock` con hashes,
instalar desde ahí en Docker y CI, y mantener `requirements.txt` como archivo de
dependencias directas.

**Prueba de aceptación**: dos builds del mismo commit producen el mismo
`pip freeze`.

---

### SEC-LOG-001 — Logger `django.security` sin configurar

- **Severidad**: Media · **Prioridad**: P2 · **CWE**: CWE-778

**Evidencia** (`settings.py:119-134`): `LOGGING` declara `django`, `weasyprint`,
`comercial.views_openpay` y `comercial.services_openpay`, pero no
`django.security`, que es donde Django emite `SuspiciousOperation`,
`DisallowedHost` y los fallos de CSRF. Como `disable_existing_loggers` es `False`
y `django` está configurado, esos eventos propagan y acaban visibles — pero por
herencia, no por decisión, y sin nivel ni formato propios.

Tampoco se registran los `403` del admin ni hay métrica agregada de `429`.

**Corrección**: declarar `django.security` con nivel `WARNING` y handler propio;
añadir un logger para los rechazos de autorización.

**Prueba de aceptación**: una petición con `Host` inválido produce una línea de
log identificable.

---

### SEC-LOG-002 — Sin correlation/request ID

- **Severidad**: Baja · **Prioridad**: P3 · **CWE**: CWE-778

**Evidencia**: `LOGGING` no define `filters` ni un formato con identificador de
petición. Al investigar un incidente no se pueden agrupar las líneas de un mismo
request, lo que con dos workers concurrentes complica bastante la lectura.

**Corrección**: middleware que genere un UUID por petición, expuesto en el
formato de log y devuelto en una cabecera de respuesta.

---

### SEC-DOS-001 — Consultas sin acotar en el calendario

- **Severidad**: Baja · **Prioridad**: P3 · **CWE**: CWE-770

**Evidencia** (`airbnb/views.py:43`):

```python
cotizaciones = Cotizacion.objects.exclude(estado='CANCELADA').select_related('cliente')
```

Sin filtro temporal ni paginación: la vista carga el histórico completo y lo
serializa entero al HTML. Hoy es manejable; crece de forma monótona con el
negocio. `api_fechas_ocupadas` sí acota correctamente a 730 días
(`views_cotizador.py:388`), que es el patrón a replicar.

**Corrección**: filtrar por el rango visible del calendario.

---

### SEC-AUTHN-002 — Sin MFA para cuentas administrativas

- **Severidad**: Media · **Prioridad**: P3 · **CWE**: CWE-308

**Evidencia**: no hay `django-otp` ni equivalente en `requirements.txt`.

Se prioriza como P3 y no más alto porque el bloqueo de fuerza bruta del login
(Issue #179) ya mitiga el ataque en línea, y porque `SEC-XSS-001` y
`SEC-AUTHZ-001` reducen el riesgo por vías que MFA no cubre. Pero mientras no
exista, una contraseña filtrada o reutilizada da acceso completo al ERP.

**Corrección**: `django-otp` + `TOTPDevice`, obligatorio para superusuarios.

---

### SEC-BIZ-001 — Sin protección explícita contra replay en el webhook

- **Severidad**: Baja · **Prioridad**: P3 · **CWE**: CWE-294

**Evidencia** (`views_openpay.py:298-332`): el webhook valida Basic Auth pero no
comprueba frescura ni unicidad del evento. En la práctica la idempotencia por
`openpay_id` (`update_or_create`) hace que un reenvío converja al mismo estado,
que es la mitigación efectiva. Se documenta como riesgo residual, no como
vulnerabilidad explotable con el diseño actual.

**Corrección**: registrar los identificadores de evento procesados y descartar
repetidos. Baja prioridad dado K-02.

---

### SEC-SECRET-002 — Historial de git no auditado

- **Severidad**: Media · **Prioridad**: P2 · **NO VERIFICABLE con lo revisado**

**Evidencia**: el árbol actual está limpio (`.env` y `db.sqlite3` en
`.gitignore`, `git ls-files` solo devuelve `.env.example`), pero no se auditó el
historial completo. Un secreto commiteado y luego eliminado sigue recuperable, y
el repo ha manejado credenciales de producción de Openpay desde el 2026-08-05.

**Corrección**: `gitleaks detect --log-opts="--all"`. Si aparece algo, rotar la
credencial — reescribir el historial no basta si el repositorio ya se clonó.

---

## 7. Controles no verificables y evidencia a solicitar

| ID | Control | Por qué no se puede verificar | Evidencia a solicitar | Prioridad |
|---|---|---|---|---|
| ~~NV-01~~ | ~~Política de acceso del bucket R2~~ | **VERIFICADO 2026-08-12**: sirve lectura anónima. `SEC-FILE-001` es exposición activa (§0) | — | Cerrado |
| ~~NV-02~~ | ~~`ICAL_PUBLIC_TOKEN` en producción~~ | **VERIFICADO 2026-08-12**: no estaba definida. `SEC-DATA-001` era una fuga activa; contenida y corregida (§0) | — | Cerrado |
| NV-03 | Backups de PostgreSQL | Servicio administrado de Railway | Frecuencia, retención, cifrado, y fecha de la última restauración probada | **P1** |
| NV-04 | Comportamiento del edge con `X-Forwarded-For` | Depende de la infraestructura de Railway | Log de una petición con XFF fabricado, mostrando qué IP registra la app | P2 — condiciona `SEC-RL-002` |
| NV-05 | HSTS efectivo en el edge | Django lo emite, pero el edge puede alterarlo | `curl -I https://erp.quintakooxtanil.com` mostrando `Strict-Transport-Security` | P2 |
| NV-06 | Rotación de credenciales | Proceso operativo fuera del repo | Fecha de última rotación de: llaves Openpay, token WhatsApp, API key Brevo, credenciales R2, Jibble | P2 |
| NV-07 | Alertas y responsables | No hay configuración en el repo | Quién recibe alertas de error y por qué canal | **P1** |
| NV-08 | Quién tiene cuentas staff | Depende de la BD de producción | Listado de usuarios con `is_staff`/`is_superuser` y su justificación | P2 — condiciona `SEC-AUTHZ-001` |
| NV-09 | Acceso al dashboard de Openpay | Fuera del repo | Quiénes tienen acceso y si usan MFA | P2 |
| NV-10 | Historial de git | No auditado en esta revisión | Resultado de `gitleaks detect --log-opts="--all"` | P2 — ver `SEC-SECRET-002` |

---

## 8. Fortalezas que deben conservarse

Vale la pena enumerarlas porque son decisiones deliberadas que un refactor
descuidado podría deshacer:

1. **Cero SQL crudo.** Todo el acceso a datos pasa por el ORM. No hay `raw()`, ni
   `RawSQL`, ni `cursor.execute` con interpolación fuera de los tests.
2. **Cero evaluación dinámica.** No hay `eval`, `exec`, `pickle` ni `yaml.load`.
3. **Validación de importes en el servidor.** `views_openpay.py:129-138` valida el
   monto contra `saldo_pendiente()` y `monto_minimo_pago()` — el cliente propone,
   el servidor dispone.
4. **Idempotencia de pagos bien construida.** `openpay_id` único +
   `update_or_create` + candado en cache liberado en `finally`.
5. **Comparaciones en tiempo constante** en los tres puntos donde se comparan
   secretos (`views_openpay.py:293`, `nomina/views.py:339`, `airbnb/views.py:311`).
6. **Timeouts en todas las llamadas salientes**, sin excepción.
7. **Rate limiting compartido entre workers** con TTL correcto y el razonamiento
   documentado en el propio código (`ratelimit.py:50-73`).
8. **Bloqueo de fuerza bruta con buckets independientes IP/usuario**, y la
   decisión — no obvia — de no limpiar el bucket de IP en un login exitoso, que
   cierra el password spraying.
9. **Nombres de usuario hasheados en la tabla de cache** (`ratelimit.py:106-109`).
10. **El módulo `legal` como referencia de diseño**: versionado con SHA-256,
    permiso dedicado, bitácora de accesos con usuario e IP, `Cache-Control:
    private, no-store`. Es el patrón que el resto del ERP debería imitar.
11. **`manage.py check --deploy` sin advertencias.**
12. **Secretos fuera del repositorio**, con `SECRET_KEY` sin valor por defecto.
13. **`pip-audit` sin vulnerabilidades conocidas** al 2026-08-11.
14. **Workflows de IA con `permissions: {}` por defecto** y acciones fijadas por
    SHA.
15. **Cultura de documentar el porqué**: la Memoria de `CLAUDE.md` y los
    comentarios de decisión en el código hicieron esta auditoría mucho más rápida
    y evitaron varios falsos positivos.

---

## 9. Validaciones ejecutadas

| Comando | Resultado |
|---|---|
| `python manage.py check` | Sin incidencias |
| `python manage.py check --deploy --fail-level WARNING` | **Sin advertencias** |
| `pip-audit -r requirements.txt` | **Sin vulnerabilidades conocidas** |
| `ruff check .` | 691 hallazgos, todos de estilo (400 `W293`, 98 `I001`, 79 `E701`, 7 `E722`); ninguno de seguridad |
| Prueba PoC de `SEC-XSS-001` | **Confirmado**: payload ejecutable en `/admin/calendario/` desde un POST anónimo |
| `grep` de `raw(`/`RawSQL`/`cursor.execute` | Sin coincidencias reales |
| `grep` de `eval(`/`exec(`/`pickle`/`yaml.load` | Sin coincidencias |
| `grep` de `|safe` y `autoescape off` | 10 coincidencias; 1 explotable (`SEC-XSS-001`), 1 de riesgo bajo (`legal/documento.html`), 8 seguras |
| `git ls-files` de patrones de secretos | Solo `.env.example` |
| Inventario de decoradores en `*/views*.py` | 63 vistas; 47 con `@staff_member_required`, 1 con `permission_required`, 15 públicas |

La prueba PoC se ejecutó en base de datos de test y se eliminó al terminar; no
quedó código de prueba en el repositorio ni se modificó ningún archivo del
producto.

---

## 10. Siguientes pasos

Ver `BACKLOG_SEGURIDAD.md` para el listado priorizado completo y
`PLAN_IMPLEMENTACION_SEGURIDAD.md` para la secuencia por fases.

**Nada de esto se implementa sin tu aprobación.** El orden recomendado:

1. **Hoy**: corregir `SEC-XSS-001` (cambio de una línea en la plantilla + una en la vista).
2. **Hoy**: confirmar `NV-02` (¿está `ICAL_PUBLIC_TOKEN` en Railway?) y `NV-01` (¿es público el bucket R2?). Son dos consultas que deciden si `SEC-DATA-001` y `SEC-FILE-001` son exposiciones activas o riesgos latentes.
3. **Esta semana**: cerrar los P1 restantes.
4. **Siguiente iteración**: los P2, empezando por la cobertura de rate limiting y los gates de CI.
