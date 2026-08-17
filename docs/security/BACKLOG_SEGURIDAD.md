# Backlog de seguridad — ERP-QKT

**Fecha**: 2026-08-12 · **Commit base**: `f813dcc` · **Issue**: #190
**Origen**: hallazgos de `AUDITORIA_SEGURIDAD.md`.

**Estado**: las órdenes 1, 2, 3, 4, 5, 6, 8, 9, 10, 11, 14, 15, 16, 17, 18,
19, 20, 21, 23, 24, 25, 26, 27, 29, 30, 31, 32, 33, 34, 35, 36, 37, 41, 43,
44, 45, 46, 47, 48, 49, 50 y 51 ya están hechas (Fase 0 y Fase 1 completas; de la Fase 2, rate
limiting, CSRF/validación del cotizador, el ocultamiento de detalles de
excepción, el gate de lint en CI, el análisis estático de seguridad de
ruff, la detección de secretos en CI, la auditoría de secretos en el
historial completo, el registro explícito de 403 de autorización, los
builds reproducibles con `requirements.lock` y la validación de extensión
y firma de los 16 `FileField`/`ImageField` ya están — queda la
verificación del proxy de Railway, orden 22, que depende de Infra). De la
Fase 3 también están hechas las órdenes 37 (CSP Report-Only del admin, la
parte de Dev — falta que Infra/Propietario active la variable y observe
violaciones reales antes de endurecer), 41 (Referrer-Policy), 43 (usuario
sin privilegios en el `Dockerfile`), 44 (calendario acotado por rango), 45
(correlation ID por request), 46 (acciones de CI fijadas por SHA), 47
(idempotencia del webhook de Openpay, ya cubierta por código preexistente),
48 (confirmación en acciones destructivas del admin), 49 (`MEDIA_ROOT`), 50
(sanitizado del HTML de markdown con `nh3`) y 51 (suite de regresión de
seguridad). Las dos
verificaciones externas resultaron
**positivas ambas**: el feed iCal estaba abierto (corregido) y el bucket R2
sirve lectura anónima —la orden 8 ya lo mitiga sirviendo por vista
autenticada; la orden 7 (bucket privado aparte) sigue pendiente del lado de
Cloudflare. Se marcan con ✅ y se conservan en la tabla para no perder la
trazabilidad. El resto sigue pendiente y requiere aprobación del propietario
antes de empezar.

**Nota fuera de tabla**: los 228 archivos que solo vivían en Cloudinary (no
migrados nunca a R2) se dieron por perdidos — la cuenta quedó deshabilitada
por exceder su cuota y el propietario descartó reactivarla. No es una orden
de este backlog porque no es un hallazgo de seguridad, pero explica por qué
algunos documentos de nómina, contratos o ARCO seguirán sin abrir incluso
después de la orden 7: no es que la migración falle, es que el origen ya no
existe. Ver Memoria en `CLAUDE.md`.

## Criterios de prioridad

| Prioridad | Definición | Plazo |
|---|---|---|
| **P0 — Crítica** | Explotación probable o impacto crítico: bypass de autenticación/autorización, secretos expuestos, datos sensibles públicamente accesibles | Inmediato |
| **P1 — Alta** | Riesgo serio que debe resolverse antes del siguiente lanzamiento o exposición pública | Esta semana |
| **P2 — Media** | Defensa en profundidad, cobertura incompleta o riesgo condicionado | Siguientes iteraciones |
| **P3 — Baja** | Hardening, mantenimiento, documentación o madurez | Cuando haya holgura |

**Esfuerzo**: `XS` (< 1 h) · `S` (medio día) · `M` (1-2 días) · `L` (3-5 días) · `XL` (más de una semana; se subdivide).

**Responsables**: *Dev* (cambios de código) · *Infra* (Railway, Cloudflare, DNS) · *Propietario* (decisiones de negocio y política).

---

## P0 — Crítica

| Orden | ID | Tarea | Prioridad | Riesgo reducido | Dependencias | Esfuerzo | Responsable | Criterios de aceptación |
|---|---|---|---|---|---|---|---|---|
| 1 ✅ | SEC-XSS-001 | **HECHO.** Sustituir `{{ eventos_json\|safe }}` por `json_script` en el calendario del admin y pasar `eventos_lista` sin serializar desde la vista | P0 | Ejecución de JS arbitrario con sesión de staff desde un POST anónimo; escalada a control total del ERP | Ninguna | XS | Dev | (1) Un `Cliente` con `nombre = '</script><script>window.x=1</script>'` no produce `</script>` sin escapar en `/admin/calendario/`; (2) el calendario sigue renderizando eventos correctamente; (3) test de regresión en la suite |
| 2 ✅ | SEC-XSS-001b | **HECHO.** Auditar los otros dos usos de `\|safe` sobre `json.dumps` (`comercial/templates/admin/dashboard.html:162-167`, `airbnb/.../dashboard.html:193-196`) y migrarlos a `json_script` | P0 | Mismo patrón; hoy no explotable porque solo llevan cifras agregadas, pero un cambio de contexto lo activaría | Orden 1 | XS | Dev | Ningún `\|safe` sobre `json.dumps` queda en plantillas del admin |

---

## P1 — Alta

| Orden | ID | Tarea | Prioridad | Riesgo reducido | Dependencias | Esfuerzo | Responsable | Criterios de aceptación |
|---|---|---|---|---|---|---|---|---|
| 3 ✅ | NV-02 | **HECHO.** Verificar si `ICAL_PUBLIC_TOKEN` está definida en Railway | P1 | — | Ninguna | XS | Infra | **No estaba definida**: `SEC-DATA-001` era una fuga activa. Contenida definiendo la variable y actualizando la URL en Airbnb |
| 4 ✅ | NV-01 | **HECHO.** Verificar la política de acceso del bucket R2 | P1 | — | Ninguna | XS | Infra | **Sirve lectura anónima** (las imágenes de la landing cargan sin sesión): `SEC-FILE-001` es exposición activa. Sube a la cabeza de la cola |
| 5 ✅ | SEC-DATA-001 | **HECHO.** Invertir a fail-closed el feed iCal: sin `ICAL_PUBLIC_TOKEN` configurado, responder 403 | P1 | Publicación anónima de nombre de cliente, evento, asistentes y fecha de todas las cotizaciones confirmadas | Orden 3 | XS | Dev | Con la variable vacía, `/airbnb/ical/eventos/` devuelve 403; con token válido, 200 |
| 6 ✅ | SEC-DATA-001b | **HECHO.** Reducir el contenido del feed al mínimo funcional: `SUMMARY` genérico, sin nombre de cliente ni número de personas | P1 | Aunque el token se filtre, no se expone la cartera de clientes | Orden 5 | XS | Dev | El `.ics` no contiene el nombre de ningún cliente; Airbnb sigue bloqueando las fechas correctamente |
| 7 | SEC-FILE-001a | **Código listo, falta el paso de Cloudflare.** Crear el bucket privado (sin dominio público), definir `CLOUDFLARE_R2_PRIVATE_BUCKET_NAME` en Railway y correr `manage.py migrar_archivos_privados --aplicar` | P1 | Acceso anónimo a identificaciones ARCO, contratos, nómina y estados de cuenta por URL directa | Orden 4 | M | **Infra** | `SolicitudARCO.identificacion.url` incluye parámetros de firma y caduca; las imágenes de la landing siguen cargando |
| 8 ✅ | SEC-FILE-001b | **HECHO.** Servir los documentos verdaderamente sensibles (ARCO, nómina, contratos) por vista autenticada con `FileResponse`, replicando el patrón de `legal/views.py`; que `portal_descargar_contrato` sirva el contenido en vez de redirigir a `archivo.url` | P1 | Cierra el hueco de forma independiente de la configuración del bucket | Orden 7 | M | Dev | Ninguna vista expone `archivo.url` de un documento sensible; cada descarga queda registrada |
| 9 ✅ | SEC-AUTHN-001a | **HECHO.** Unificar el mensaje de error de `portal_acceso` para código inexistente y teléfono incorrecto | P1 | Enumeración de identificadores de cotización válidos | Ninguna | XS | Dev | Ambos casos devuelven texto idéntico; test que lo verifica |
| 10 ✅ | SEC-AUTHN-001b | **HECHO.** Contador de intentos **por cotización** además del de IP, en `ratelimit.py::portal_acceso_bloqueado` | P1 | Fuerza bruta distribuida sobre los 4 dígitos del teléfono | Orden 9 | S | Dev | Tras N intentos fallidos contra la misma cotización desde IPs distintas, la respuesta es 429; test que reparte los intentos entre 10 IPs |
| 11 ✅ | SEC-AUTHN-001c | **HECHO.** `portal_acceso` ya no crea el `PortalCliente`: solo resuelve portales existentes, activos y vigentes | P1 | Un atacante genera tokens permanentes para cotizaciones que nunca tuvieron portal | Orden 10 | S | Dev | `portal_acceso` no crea registros; el alta sigue ocurriendo en `Cotizacion.save()` |
| 12 | NV-03 | **Verificar** backups de PostgreSQL en Railway: frecuencia, retención, cifrado y última restauración probada | P1 | Pérdida de datos sin posibilidad de recuperación | Ninguna | XS | Infra | Evidencia documentada; si no hay respaldo, se convierte en P0 operativo |
| 13 | NV-07 | **Definir** quién recibe alertas de error y por qué canal | P1 | Un incidente puede pasar inadvertido indefinidamente | Ninguna | S | Propietario | Canal y responsable documentados |
| 14 ✅ | SEC-AUTHZ-001a | **HECHO.** Definir grupos por área (Ventas, Contabilidad, Nómina) y documentar qué modelos y vistas toca cada uno — Dirección no es un grupo Django, sigue siendo `is_superuser` | P1 | Cualquier cuenta staff accede a nómina, contabilidad, ARCO y datos fiscales | NV-08 | S | Propietario + Dev | Matriz de permisos aprobada por el propietario (Issue #199) |
| 15 ✅ | SEC-AUTHZ-001b | **HECHO.** `@permission_required` en las vistas de **nómina** (`cargar_nomina`, `sync_jibble_view`, `jibble_diagnostico_view`) | P1 | Exposición de recibos de nómina entre empleados | Orden 14 | S | Dev | Un staff sin el permiso recibe 403 en `/admin/nomina/cargar/` |
| 16 ✅ | SEC-AUTHZ-001c | **HECHO.** `@permission_required` en las vistas de **contabilidad y reportes financieros** (`balanza_comprobacion`, `estado_resultados`, `cartera_cxc` y las 11 vistas de `reportes/views.py`, cada una con el permiso de su área dueña) | P1 | Exposición de contabilidad completa a cualquier staff | Orden 14 | M | Dev | Un staff sin el permiso recibe 403 en cada una |
| 17 ✅ | SEC-AUTHZ-001d | **HECHO.** Restringir `importar_historico_view` a superusuario, también en el GET (el POST ya lo hacía) | P1 | Operación destructiva de importación masiva al alcance de cualquier staff | Orden 14 | XS | Dev | Un staff no superusuario recibe 403 |
| 18 ✅ | SEC-AUTHZ-001e | **HECHO.** `manage.py crear_grupos_permisos` asigna los permisos estándar de cada modelo (view/add/change/delete) a su grupo; verificado que ningún `ModelAdmin` existente amplía el acceso por encima del grupo (solo hay overrides que restringen más, nunca que abren) | P1 | Acceso a modelos fuera del área de cada persona | Orden 14 | M | Dev | Cada grupo ve únicamente los modelos de su área |

---

## P2 — Media

| Orden | ID | Tarea | Prioridad | Riesgo reducido | Dependencias | Esfuerzo | Responsable | Criterios de aceptación |
|---|---|---|---|---|---|---|---|---|
| 19 ✅ | SEC-RL-001a | **HECHO.** `@rate_limit` en las descargas del portal (`portal_evento`, `portal_descargar_cotizacion/plan/contrato`), ~10/min | P2 | DoS por generación repetida de PDFs con WeasyPrint | Ninguna | XS | Dev | Superar el límite devuelve 429 |
| 20 ✅ | SEC-RL-001b | **HECHO.** `@rate_limit` en las 5 APIs públicas del cotizador, ~60/min | P2 | Scraping de catálogo y precios | Ninguna | XS | Dev | Superar el límite devuelve 429 |
| 21 ✅ | SEC-RL-001c | **HECHO.** `@rate_limit` en ambos webhooks (Openpay, Jibble) y en el feed iCal, ~120/min | P2 | Martilleo de credenciales del webhook y fuerza bruta del Bearer de Jibble | Ninguna | XS | Dev | Superar el límite devuelve 429 sin afectar el tráfico legítimo |
| 22 | SEC-RL-002 | **Verificar** el comportamiento del edge de Railway con `X-Forwarded-For` y ajustar `RATELIMIT_TRUSTED_PROXY_COUNT` si procede | P2 | Evasión total del rate limiting y del bloqueo de login si el edge no añade la IP real | NV-04 | S | Infra + Dev | Una petición con XFF fabricado no altera la IP registrada; resultado anotado en la Memoria de `CLAUDE.md` |
| 23 ✅ | SEC-CSRF-001 | **HECHO.** Quitado `@csrf_exempt` de `cotizador_enviar`; el formulario público renderiza `{% csrf_token %}` y lo manda como `X-CSRFToken` | P2 | CSRF que crea registros y consume cuota de WhatsApp con coste real | Ninguna | S | Dev | `POST /cotizar/enviar/` sin token devuelve 403; el formulario legítimo sigue funcionando |
| 24 ✅ | SEC-VAL-001 | **HECHO.** Validación manual de `cotizador_enviar` sustituida por `CotizadorEnviarForm` (tipos, longitudes y `choices` cerradas en `tipo_evento`/`como_nos_encontro`) | P2 | Entrada sin restricción en `notas`, `tipo_evento` y `como_nos_encontro`, que alimentan `nombre_evento` | Orden 23 | M | Dev | Campos fuera de rango devuelven 400; los tests del cotizador siguen pasando |
| 25 ✅ | SEC-INFO-001 | **HECHO.** `str(e)` reemplazado por mensaje genérico + `logger.exception()` en `api_disponibilidad_fecha`/`api_fechas_ocupadas` (`views_cotizador.py`) y `webhook_sync_jibble` (`nomina/views.py`) | P2 | Filtración de rutas, nombres de tablas y detalles internos | Ninguna | XS | Dev | El cuerpo de un 500 no contiene el texto de la excepción; el detalle aparece en el log |
| 26 ✅ | SEC-INJ-001 | **HECHO** (colateral de la orden 6): el `.ics` ya no interpola texto libre, solo el folio numérico | P2 | Inyección de propiedades iCal en los calendarios que consuman el feed | Orden 6 | S | Dev | Cubierto por `test_un_nombre_con_saltos_de_linea_no_inyecta_propiedades` |
| 27 ✅ | SEC-SESS-001 | **HECHO.** `expira_en` en `PortalCliente` (90 días desde el evento), verificado en las 7 vistas que usan el token, acción de admin para regenerar | P2 | Token permanente en historiales, correos y WhatsApp | Ninguna | M | Dev | Un portal expirado devuelve 404; se regenera desde el admin sin tocar la BD a mano |
| 28 | SEC-CFG-001 | Definir `SECURE_PROXY_SSL_HEADER` tras confirmar la cabecera que envía el edge | P2 | `request.is_secure()` incorrecto: bucles de redirección y URLs de retorno 3-D Secure en `http://` | NV-05 | XS | Infra + Dev | `request.is_secure()` devuelve `True` en producción |
| 29 ✅ | SEC-CI-001a | **HECHO.** `ruff check --fix`/`--unsafe-fixes` sobre los hallazgos auto-corregibles; las 94 líneas `E701`/`E702` (una sola sentencia por línea, sin cambiar nada más) y los 7 `E722` (`except:` → `except Exception:`) se corrigieron a mano porque ruff no los auto-corrige | P2 | Deuda que mantenía el gate de lint desactivado | Ninguna | S | Dev | `ruff check .` sin errores |
| 30 ✅ | SEC-CI-001b | **HECHO.** Quitado `continue-on-error: true` del paso de lint en `ci.yml` | P2 | Un gate que no bloqueaba nada | Orden 29 | XS | Dev | Un PR con error de lint falla el CI |
| 31 ✅ | SEC-CI-001c | **HECHO.** Ruleset `S` (flake8-bandit) añadido al `select` de ruff. Un hallazgo real (`comercial/admin.py::badge_cotizador`, XSS potencial vía `obj.icono` sin escapar) corregido con `format_html`; XXE en el parseo de CFDI (`comercial/models.py`, `comercial/services.py`) cerrado migrando a `defusedxml`; el resto son excepciones documentadas en `pyproject.toml`/`# noqa` (ver Memoria) | P2 | Ausencia total de análisis estático de seguridad | Orden 30 | M | Dev | Un PR con `subprocess.call(shell=True)` falla el CI |
| 32 ✅ | SEC-CI-001d | **HECHO.** `gitleaks` añadido al job `security` de `ci.yml` — binario oficial descargado con verificación de checksum (no la GitHub Action, que exige licencia en repos privados), escaneo del árbol de trabajo (`--no-git`) | P2 | Un secreto commiteado pasa inadvertido | Ninguna | S | Dev | Un PR con una clave con formato de secreto falla el CI |
| 33 ✅ | SEC-SECRET-002 | **HECHO.** `gitleaks detect --log-opts="--all"` corrido sobre el historial completo (793 commits) — **sin hallazgos**, no hay ninguna credencial que rotar | P2 | Secreto commiteado y borrado, todavía recuperable | Ninguna | XS | Dev | Informe adjunto al Issue; si aparece algo, rotar la credencial afectada |
| 34 ✅ | SEC-DEP-001 | **HECHO.** `requirements.lock` generado con `pip-compile`; `Dockerfile`, `ci.yml` y los workflows de IA (`ai-review-merge.yml`/`ai-implement.yml`) instalan desde ahí, no desde `requirements.txt` | P2 | Builds no reproducibles; imposible reconstruir el entorno de un incidente | Ninguna | M | Dev | Dos builds del mismo commit producen el mismo `pip freeze` |
| 35 ✅ | SEC-FILE-002 | **HECHO.** `FileExtensionValidator` (`core_erp/validadores_archivos.py`) en los 16 `FileField`/`ImageField`, más verificación de firma binaria real para los que aceptan PDF/XML/ZIP (los que solo aceptan imagen ya la tienen gratis vía `ImageField`+Pillow) | P2 | Contenido activo subido al storage | Ninguna | M | Dev | Un `.html` renombrado a `.pdf` es rechazado por el formulario — `core_erp/test_validadores_archivos.py` |
| 36 ✅ | SEC-LOG-001 | **HECHO.** Logger `django.security` declarado en `settings.py`; nuevo `AuthorizationAuditMiddleware` registra cada 403 con usuario y ruta, cubriendo por igual `raise PermissionDenied`, `@permission_required` y cualquier 403 manual | P2 | Eventos de seguridad sin nivel ni formato propios | Ninguna | S | Dev | Una petición con `Host` inválido produce una línea identificable |
| 37 ✅ | SEC-CFG-002 | **HECHO (la parte de Dev).** CSP Report-Only para `/admin/`, opt-in vía `ADMIN_CSP_REPORT_ONLY` (`core_erp/middleware.py`) — mismo patrón que la orden 27 ya usaba para el portal de pago. **Pendiente de Infra/Propietario**: activar la variable en Railway, usar el admin con normalidad y revisar la consola del navegador por violaciones antes de plantear una CSP bloqueante — "endurecer por etapas" no es alcanzable sin esa observación en producción real | P2 | Sin defensa en profundidad en la superficie de mayor privilegio | Orden 1 | L | Dev | `/admin/` devuelve cabecera CSP (con el flag activo); ninguna funcionalidad de Jazzmin se rompe (Report-Only nunca bloquea, por diseño) — `core_erp/test_regresion_seguridad.py::PublicSecurityHeadersMiddlewareTest` |
| 38 | NV-06 | **Documentar** el calendario de rotación de credenciales (Openpay, WhatsApp, Brevo, R2, Jibble) | P2 | Credenciales de larga vida sin control | Ninguna | S | Propietario + Infra | Documento con fecha de última rotación y periodicidad acordada |
| 39 | NV-08 | **Revisar** el listado de cuentas con `is_staff`/`is_superuser` y retirar las que no correspondan | P2 | Cuentas con más privilegio del necesario o ya innecesarias | Ninguna | S | Propietario | Listado revisado y depurado |
| 40 | NV-09 | **Verificar** quién accede al dashboard de Openpay y si usan MFA | P2 | Acceso al panel de cobros sin segundo factor | Ninguna | XS | Propietario | Listado documentado |

---

## P3 — Baja

| Orden | ID | Tarea | Prioridad | Riesgo reducido | Dependencias | Esfuerzo | Responsable | Criterios de aceptación |
|---|---|---|---|---|---|---|---|---|
| 41 ✅ | SEC-CFG-003 | **HECHO.** `SECURE_REFERRER_POLICY = 'strict-origin-when-cross-origin'` definida (fuera del bloque `if not DEBUG`, es solo una cabecera de respuesta) | P3 | Filtración del token del portal por la cabecera `Referer` | Ninguna | XS | Dev | La respuesta de `/mi-evento/<token>/` incluye la cabecera |
| 42 | SEC-AUTHN-002 | Instalar `django-otp` y exigir TOTP a los superusuarios | P3 | Una contraseña filtrada da acceso completo al ERP | Orden 14 | L | Dev | Un superusuario sin dispositivo TOTP no completa el login |
| 43 ✅ | SEC-CFG-004 | **HECHO.** Usuario de sistema `appuser` sin privilegios añadido al `Dockerfile`; `chown -R` de `/app` antes del `USER appuser`, después de `collectstatic` | P3 | Una RCE tendría root dentro del contenedor | Ninguna | S | Dev | `whoami` en el contenedor no devuelve `root`; el despliegue funciona |
| 44 ✅ | SEC-DOS-001 | **HECHO.** Los eventos ya no viajan embebidos en la página: `calendario_unificado_eventos` (nuevo endpoint JSON) los sirve acotados a `start`/`end`, y FullCalendar lo consulta por AJAX según el rango visible en cada momento | P3 | Degradación progresiva conforme crece el histórico | Orden 1 | S | Dev | La vista consulta solo el rango visible |
| 45 ✅ | SEC-LOG-002 | **HECHO.** `CorrelationIdMiddleware` genera un ID por request (nunca aceptado del cliente), expuesto en `X-Correlation-ID` y en todo log emitido durante el procesamiento vía `CorrelationIdFilter` | P3 | Dificultad para correlacionar eventos con 2 workers concurrentes | Orden 36 | S | Dev | Todas las líneas de log de un request comparten identificador |
| 46 ✅ | SEC-CI-002 | **HECHO.** `actions/checkout`/`actions/setup-python` en `ci.yml` fijadas por SHA (mismas versiones y SHA ya usados en `ai-review-merge.yml`/`ai-implement.yml`, que ya estaban fijados) | P3 | Riesgo bajo de cadena de suministro | Ninguna | XS | Dev | Ninguna acción usa tag flotante |
| 47 ✅ | SEC-BIZ-001 | **YA CUBIERTO por código preexistente**, verificado y documentado en esta orden — ver Memoria 2026-08-17. `OpenpayTransaccion.openpay_id` es único (`unique=True, db_index=True`) y `procesar_webhook_openpay()` corta con `if registro.procesado: return registro` | P3 | Replay de payloads capturados (hoy mitigado por idempotencia) | Ninguna | S | Dev | Un payload repetido no genera efectos adicionales — cubierto por `ProcesarWebhookIdempotenciaTest.test_no_duplica_pago_con_mismo_openpay_id` |
| 48 ✅ | SEC-BIZ-002 | **HECHO.** `confirmar_accion_destructiva` (`core_erp/admin_utils.py`) — mismo patrón que `delete_selected` de Django — envuelve las 10 acciones de admin de mayor impacto: aplicar/cancelar pólizas, autorizar regularización, aplicar saldo de apertura, regenerar token del portal, registrar reembolso, reembolsar en Openpay, borrar transacciones de prueba, cancelar solicitudes de factura y publicar versión de documento legal | P3 | Una sesión secuestrada opera sin fricción | Orden 14 | M | Dev | Las acciones destructivas piden confirmación |
| 49 ✅ | SEC-CFG-005 | **HECHO.** `MEDIA_ROOT = BASE_DIR / 'media'` en `settings.py` — antes caía al default global de Django (`''`) | P3 | `urls.py:189` referencia un setting inexistente en modo `DEBUG` | Ninguna | XS | Dev | `manage.py runserver` con `DEBUG=True` sirve `/media/` sin error |
| 50 ✅ | SEC-XSS-003 | **HECHO.** `DocumentoLegal.render_html()` sanitiza con `nh3` (lista blanca de etiquetas/atributos) antes de que `legal/documento.html:155` lo sirva con `\|safe` | P3 | Un admin comprometido podría publicar HTML activo en una página pública | Ninguna | S | Dev | El markdown se renderiza con lista blanca de etiquetas |
| 51 ✅ | SEC-TEST-001 | **HECHO.** `core_erp/test_regresion_seguridad.py` — índice de dónde vive cada test de las órdenes 1-18 (ya cubiertas al corregirse, no reinventadas) + cobertura nueva a los dos huecos reales encontrados: `PublicSecurityHeadersMiddleware` (CSP/Permissions-Policy) y la expiración de sesión por inactividad, sin ningún test hasta ahora | P3 | Regresiones silenciosas en los controles corregidos | Órdenes 1-18 | L | Dev | Un PR que reintroduzca cualquiera de los hallazgos corregidos falla el CI |
| 52 | SEC-DOC-001 | Runbook de incidentes: contención, revocación de sesiones, rotación de secretos, preservación de evidencia, comunicación | P3 | Respuesta improvisada ante un incidente | Orden 13 | M | Propietario + Dev | Documento en `docs/security/` revisado por el propietario |

---

## Resumen

| Prioridad | Tareas | Hechas | Esfuerzo restante aproximado |
|---|---|---|---|
| P0 | 2 | **2** | — |
| P1 | 16 | 13 | ~1-2 días |
| P2 | 22 | 17 | ~2-3 días |
| P3 | 12 | 10 | ~1 semana |
| **Total** | **52** | **42** | — |

**Lo siguiente, por relación impacto/esfuerzo**:

1. Orden 7 — `SEC-FILE-001a`: **solo falta el paso de Cloudflare**; el código ya está y es inerte hasta que se configure el bucket.
2. Orden 12 — `NV-03` (`XS`): confirmar que existen respaldos y que se han probado.
3. Orden 13 — `NV-07` (`S`): definir quién recibe las alertas.
4. Orden 22 — `SEC-RL-002` (`S`): verificar `X-Forwarded-For` en el edge de Railway, ahora que el rate limiting (órdenes 19-21) ya depende de que `_client_ip()` resuelva la IP real.
5. Orden 42 — `SEC-AUTHN-002` (`L`): `django-otp` + TOTP obligatorio para superusuarios — código listo en el PR #226, pendiente de que el propietario pruebe el escaneo de QR y apruebe.
6. Orden 52 — `SEC-DOC-001` (`M`, Propietario + Dev): runbook de incidentes — borrador listo en el PR #226, pendientes 3 `[CONFIRMAR:]` de negocio.
7. Orden 37 — `SEC-CFG-002`: la parte de Dev ya está (CSP Report-Only del admin, opt-in vía `ADMIN_CSP_REPORT_ONLY`); falta que Infra/Propietario active la variable en Railway, use el admin con normalidad y reporte las violaciones que aparezcan en la consola del navegador — sin esa observación no hay forma responsable de endurecer a una CSP bloqueante.

No queda ninguna tarea de P0-P3 accionable por Dev en solitario sin depender de Infra o del propietario.

El `ICAL_PUBLIC_TOKEN` no necesita rotación inmediata: se generó en un gestor de
contraseñas. Queda cubierto por el calendario ordinario de rotación (orden 38).

**Nota sobre las órdenes 7 y 8**: no admiten una contención rápida como la del
feed iCal. Hacer el bucket privado sin separar antes las imágenes de la landing
deja el sitio público sin fotos, así que el orden correcto es crear el bucket
privado, migrar los documentos, y solo entonces cerrar el acceso público del
actual.

Ninguna tarea `XL`: la de mayor esfuerzo es `SEC-AUTHZ-001`, ya subdividida en
cinco entregables (órdenes 14-18) que se pueden desplegar por separado.
