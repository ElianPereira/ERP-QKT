# Backlog de seguridad — ERP-QKT

**Fecha**: 2026-08-12 · **Commit base**: `f813dcc` · **Issue**: #190
**Origen**: hallazgos de `AUDITORIA_SEGURIDAD.md`.

**Estado**: las órdenes 1, 2, 3, 5, 6 y 26 ya están hechas (Fase 0 completa y
parte de la Fase 1) — ver §0 de la auditoría. Se marcan con ✅ y se conservan
en la tabla para no perder la trazabilidad. El resto sigue pendiente y requiere
aprobación del propietario antes de empezar.

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
| 4 | NV-01 | **Verificar** la política de acceso del bucket R2: pedir la URL de un archivo de `arco/identificaciones/` desde una sesión anónima | P1 | Determina si `SEC-FILE-001` es una exposición activa o latente | Ninguna | XS | Infra | Resultado documentado (HTTP 200 vs. 403) |
| 5 ✅ | SEC-DATA-001 | **HECHO.** Invertir a fail-closed el feed iCal: sin `ICAL_PUBLIC_TOKEN` configurado, responder 403 | P1 | Publicación anónima de nombre de cliente, evento, asistentes y fecha de todas las cotizaciones confirmadas | Orden 3 | XS | Dev | Con la variable vacía, `/airbnb/ical/eventos/` devuelve 403; con token válido, 200 |
| 6 ✅ | SEC-DATA-001b | **HECHO.** Reducir el contenido del feed al mínimo funcional: `SUMMARY` genérico, sin nombre de cliente ni número de personas | P1 | Aunque el token se filtre, no se expone la cartera de clientes | Orden 5 | XS | Dev | El `.ics` no contiene el nombre de ningún cliente; Airbnb sigue bloqueando las fechas correctamente |
| 7 | SEC-FILE-001a | Pasar el bucket R2 a privado y activar `querystring_auth: True` para los documentos sensibles | P1 | Acceso anónimo a identificaciones ARCO, contratos, nómina y estados de cuenta por URL directa | Orden 4 | M | Infra + Dev | `SolicitudARCO.identificacion.url` incluye parámetros de firma y caduca; las imágenes de la landing siguen cargando |
| 8 | SEC-FILE-001b | Servir los documentos verdaderamente sensibles (ARCO, nómina, contratos) por vista autenticada con `FileResponse`, replicando el patrón de `legal/views.py`; que `portal_descargar_contrato` sirva el contenido en vez de redirigir a `archivo.url` | P1 | Cierra el hueco de forma independiente de la configuración del bucket | Orden 7 | M | Dev | Ninguna vista expone `archivo.url` de un documento sensible; cada descarga queda registrada |
| 9 | SEC-AUTHN-001a | Unificar el mensaje de error de `portal_acceso` para código inexistente y teléfono incorrecto | P1 | Enumeración de identificadores de cotización válidos | Ninguna | XS | Dev | Ambos casos devuelven texto idéntico; test que lo verifica |
| 10 | SEC-AUTHN-001b | Añadir contador de intentos **por cotización** además del de IP, reusando el patrón de `ratelimit.py::_buckets_login` | P1 | Fuerza bruta distribuida sobre los 4 dígitos del teléfono | Orden 9 | S | Dev | Tras N intentos fallidos contra la misma cotización desde IPs distintas, la respuesta es 429 |
| 11 | SEC-AUTHN-001c | Dejar de crear el `PortalCliente` dentro de `portal_acceso`: resolver solo portales existentes y activos | P1 | Un atacante genera tokens permanentes para cotizaciones que nunca tuvieron portal | Orden 10 | S | Dev | `portal_acceso` no crea registros; el alta ocurre en el flujo comercial |
| 12 | NV-03 | **Verificar** backups de PostgreSQL en Railway: frecuencia, retención, cifrado y última restauración probada | P1 | Pérdida de datos sin posibilidad de recuperación | Ninguna | XS | Infra | Evidencia documentada; si no hay respaldo, se convierte en P0 operativo |
| 13 | NV-07 | **Definir** quién recibe alertas de error y por qué canal | P1 | Un incidente puede pasar inadvertido indefinidamente | Ninguna | S | Propietario | Canal y responsable documentados |
| 14 | SEC-AUTHZ-001a | Definir grupos por área (Ventas, Contabilidad, Nómina, Dirección) y documentar qué modelos y vistas toca cada uno | P1 | Cualquier cuenta staff accede a nómina, contabilidad, ARCO y datos fiscales | NV-08 | S | Propietario + Dev | Matriz de permisos aprobada por el propietario |
| 15 | SEC-AUTHZ-001b | Aplicar `@permission_required` a las vistas de **nómina** (`cargar_nomina`, `sync_jibble_view`, `jibble_diagnostico_view`) | P1 | Exposición de recibos de nómina entre empleados | Orden 14 | S | Dev | Un staff sin el permiso recibe 403 en `/admin/nomina/cargar/` |
| 16 | SEC-AUTHZ-001c | Aplicar `@permission_required` a las vistas de **contabilidad y reportes financieros** (`reporte_balanza`, `estado_resultados`, `balance_general`, `libro_mayor`, `auxiliar`, `cartera_cxc`, `conciliacion_depositos_airbnb`, `reporte_fiscal_airbnb`) | P1 | Exposición de contabilidad completa a cualquier staff | Orden 14 | M | Dev | Un staff sin el permiso recibe 403 en cada una |
| 17 | SEC-AUTHZ-001d | Restringir `importar_historico_view` a superusuario | P1 | Operación destructiva de importación masiva al alcance de cualquier staff | Orden 14 | XS | Dev | Un staff no superusuario recibe 403 |
| 18 | SEC-AUTHZ-001e | Ajustar permisos por modelo en el admin conforme a los grupos definidos | P1 | Acceso a modelos fuera del área de cada persona | Orden 14 | M | Dev | Cada grupo ve únicamente los modelos de su área |

---

## P2 — Media

| Orden | ID | Tarea | Prioridad | Riesgo reducido | Dependencias | Esfuerzo | Responsable | Criterios de aceptación |
|---|---|---|---|---|---|---|---|---|
| 19 | SEC-RL-001a | Aplicar `@rate_limit` a las descargas del portal (`portal_evento`, `portal_descargar_cotizacion/plan/contrato`), ~10/min | P2 | DoS por generación repetida de PDFs con WeasyPrint | Ninguna | XS | Dev | Superar el límite devuelve 429 |
| 20 | SEC-RL-001b | Aplicar `@rate_limit` a las 5 APIs públicas del cotizador, ~60/min | P2 | Scraping de catálogo y precios | Ninguna | XS | Dev | Superar el límite devuelve 429 |
| 21 | SEC-RL-001c | Aplicar `@rate_limit` a ambos webhooks y al feed iCal, ~120/min | P2 | Martilleo de credenciales del webhook y fuerza bruta del Bearer de Jibble | Ninguna | XS | Dev | Superar el límite devuelve 429 sin afectar el tráfico legítimo |
| 22 | SEC-RL-002 | **Verificar** el comportamiento del edge de Railway con `X-Forwarded-For` y ajustar `RATELIMIT_TRUSTED_PROXY_COUNT` si procede | P2 | Evasión total del rate limiting y del bloqueo de login si el edge no añade la IP real | NV-04 | S | Infra + Dev | Una petición con XFF fabricado no altera la IP registrada; resultado anotado en la Memoria de `CLAUDE.md` |
| 23 | SEC-CSRF-001 | Quitar `@csrf_exempt` de `cotizador_enviar` y enviar el token desde el formulario público | P2 | CSRF que crea registros y consume cuota de WhatsApp con coste real | Ninguna | S | Dev | `POST /cotizar/enviar/` sin token devuelve 403; el formulario legítimo sigue funcionando |
| 24 | SEC-VAL-001 | Sustituir la validación manual de `cotizador_enviar` por un `forms.Form` con tipos, longitudes y `choices` | P2 | Entrada sin restricción en `notas`, `tipo_evento` y `como_nos_encontro`, que alimentan `nombre_evento` | Orden 23 | M | Dev | Campos fuera de rango devuelven 400; los tests del cotizador siguen pasando |
| 25 | SEC-INFO-001 | Reemplazar `str(e)` por mensaje genérico + `logger.exception()` en `views_cotizador.py:371,394` y `nomina/views.py:377` | P2 | Filtración de rutas, nombres de tablas y detalles internos | Ninguna | XS | Dev | El cuerpo de un 500 no contiene el texto de la excepción; el detalle aparece en el log |
| 26 ✅ | SEC-INJ-001 | **HECHO** (colateral de la orden 6): el `.ics` ya no interpola texto libre, solo el folio numérico | P2 | Inyección de propiedades iCal en los calendarios que consuman el feed | Orden 6 | S | Dev | Cubierto por `test_un_nombre_con_saltos_de_linea_no_inyecta_propiedades` |
| 27 | SEC-SESS-001 | Añadir `expira_en` a `PortalCliente`, verificarlo en las 5 vistas del portal y permitir regenerar el token desde el admin | P2 | Token permanente en historiales, correos y WhatsApp | Ninguna | M | Dev | Un portal expirado devuelve 404; se puede regenerar sin tocar la BD a mano |
| 28 | SEC-CFG-001 | Definir `SECURE_PROXY_SSL_HEADER` tras confirmar la cabecera que envía el edge | P2 | `request.is_secure()` incorrecto: bucles de redirección y URLs de retorno 3-D Secure en `http://` | NV-05 | XS | Infra + Dev | `request.is_secure()` devuelve `True` en producción |
| 29 | SEC-CI-001a | `ruff check --fix` sobre los 555 hallazgos auto-corregibles y revisión manual de los 7 `E722` | P2 | Deuda que mantiene el gate de lint desactivado | Ninguna | S | Dev | `ruff check .` sin errores |
| 30 | SEC-CI-001b | Quitar `continue-on-error: true` del paso de lint en `ci.yml` | P2 | Un gate que no bloquea nada | Orden 29 | XS | Dev | Un PR con error de lint falla el CI |
| 31 | SEC-CI-001c | Añadir el ruleset `S` (flake8-bandit) al `select` de ruff, con las excepciones justificadas | P2 | Ausencia total de análisis estático de seguridad | Orden 30 | M | Dev | Un PR con `subprocess.call(shell=True)` falla el CI |
| 32 | SEC-CI-001d | Añadir `gitleaks` al pipeline de CI | P2 | Un secreto commiteado pasa inadvertido | Ninguna | S | Dev | Un PR con una clave con formato de secreto falla el CI |
| 33 | SEC-SECRET-002 | Ejecutar `gitleaks detect --log-opts="--all"` sobre el historial completo | P2 | Secreto commiteado y borrado, todavía recuperable | Ninguna | XS | Dev | Informe adjunto al Issue; si aparece algo, rotar la credencial afectada |
| 34 | SEC-DEP-001 | Generar `requirements.lock` con `pip-compile` e instalar desde ahí en `Dockerfile` y CI | P2 | Builds no reproducibles; imposible reconstruir el entorno de un incidente | Ninguna | M | Dev | Dos builds del mismo commit producen el mismo `pip freeze` |
| 35 | SEC-FILE-002 | Añadir `FileExtensionValidator` a los 16 `FileField`/`ImageField` y verificación de firma para PDF y XML | P2 | Contenido activo subido al storage | Orden 7 | M | Dev | Un `.html` renombrado a `.pdf` es rechazado por el formulario |
| 36 | SEC-LOG-001 | Declarar el logger `django.security` y añadir registro explícito de los 403 de autorización | P2 | Eventos de seguridad sin nivel ni formato propios | Ninguna | S | Dev | Una petición con `Host` inválido produce una línea identificable |
| 37 | SEC-CFG-002 | CSP para `/admin/` en modo Report-Only, recoger violaciones de Jazzmin y endurecer por etapas | P2 | Sin defensa en profundidad en la superficie de mayor privilegio | Orden 1 | L | Dev | `/admin/` devuelve cabecera CSP; ninguna funcionalidad de Jazzmin se rompe |
| 38 | NV-06 | **Documentar** el calendario de rotación de credenciales (Openpay, WhatsApp, Brevo, R2, Jibble) | P2 | Credenciales de larga vida sin control | Ninguna | S | Propietario + Infra | Documento con fecha de última rotación y periodicidad acordada |
| 39 | NV-08 | **Revisar** el listado de cuentas con `is_staff`/`is_superuser` y retirar las que no correspondan | P2 | Cuentas con más privilegio del necesario o ya innecesarias | Ninguna | S | Propietario | Listado revisado y depurado |
| 40 | NV-09 | **Verificar** quién accede al dashboard de Openpay y si usan MFA | P2 | Acceso al panel de cobros sin segundo factor | Ninguna | XS | Propietario | Listado documentado |

---

## P3 — Baja

| Orden | ID | Tarea | Prioridad | Riesgo reducido | Dependencias | Esfuerzo | Responsable | Criterios de aceptación |
|---|---|---|---|---|---|---|---|---|
| 41 | SEC-CFG-003 | Definir `SECURE_REFERRER_POLICY = 'strict-origin-when-cross-origin'` | P3 | Filtración del token del portal por la cabecera `Referer` | Ninguna | XS | Dev | La respuesta de `/mi-evento/<token>/` incluye la cabecera |
| 42 | SEC-AUTHN-002 | Instalar `django-otp` y exigir TOTP a los superusuarios | P3 | Una contraseña filtrada da acceso completo al ERP | Orden 14 | L | Dev | Un superusuario sin dispositivo TOTP no completa el login |
| 43 | SEC-CFG-004 | Añadir `USER` sin privilegios al `Dockerfile` | P3 | Una RCE tendría root dentro del contenedor | Ninguna | S | Dev | `whoami` en el contenedor no devuelve `root`; el despliegue funciona |
| 44 | SEC-DOS-001 | Acotar por rango de fechas la consulta de `calendario_unificado` | P3 | Degradación progresiva conforme crece el histórico | Orden 1 | S | Dev | La vista consulta solo el rango visible |
| 45 | SEC-LOG-002 | Middleware de correlation/request ID, expuesto en logs y en cabecera de respuesta | P3 | Dificultad para correlacionar eventos con 2 workers concurrentes | Orden 36 | S | Dev | Todas las líneas de log de un request comparten identificador |
| 46 | SEC-CI-002 | Fijar por SHA las acciones de `ci.yml` (`actions/checkout`, `actions/setup-python`) | P3 | Riesgo bajo de cadena de suministro | Ninguna | XS | Dev | Ninguna acción usa tag flotante |
| 47 | SEC-BIZ-001 | Registrar identificadores de evento del webhook de Openpay y descartar repetidos | P3 | Replay de payloads capturados (hoy mitigado por idempotencia) | Ninguna | S | Dev | Un payload repetido no genera efectos adicionales |
| 48 | SEC-BIZ-002 | Confirmación explícita en acciones destructivas del admin (borrados, cambios de permisos) | P3 | Una sesión secuestrada opera sin fricción | Orden 14 | M | Dev | Las acciones destructivas piden confirmación |
| 49 | SEC-CFG-005 | Definir `MEDIA_ROOT` en `settings.py` | P3 | `urls.py:189` referencia un setting inexistente en modo `DEBUG` | Ninguna | XS | Dev | `manage.py runserver` con `DEBUG=True` sirve `/media/` sin error |
| 50 | SEC-XSS-003 | Evaluar sanitizado del HTML generado por markdown en `legal/documento.html:155` | P3 | Un admin comprometido podría publicar HTML activo en una página pública | Ninguna | S | Dev | El markdown se renderiza con lista blanca de etiquetas |
| 51 | SEC-TEST-001 | Suite de tests de seguridad: autorización cruzada, XSS, CSRF, cabeceras, expiración de sesión | P3 | Regresiones silenciosas en los controles corregidos | Órdenes 1-18 | L | Dev | Un PR que reintroduzca cualquiera de los hallazgos corregidos falla el CI |
| 52 | SEC-DOC-001 | Runbook de incidentes: contención, revocación de sesiones, rotación de secretos, preservación de evidencia, comunicación | P3 | Respuesta improvisada ante un incidente | Orden 13 | M | Propietario + Dev | Documento en `docs/security/` revisado por el propietario |

---

## Resumen

| Prioridad | Tareas | Hechas | Esfuerzo restante aproximado |
|---|---|---|---|
| P0 | 2 | **2** | — |
| P1 | 16 | 3 | ~2 semanas |
| P2 | 22 | 1 | ~3 semanas |
| P3 | 12 | 0 | ~2 semanas |
| **Total** | **52** | **6** | — |

**Lo siguiente, por relación impacto/esfuerzo**:

1. Orden 4 — `NV-01` (`XS`): la consulta que decide si los documentos del bucket están expuestos. Es lo único que queda de las verificaciones inmediatas.
2. Orden 9 — `SEC-AUTHN-001a` (`XS`): cierra la enumeración de códigos del portal.
3. Orden 12 — `NV-03` (`XS`): confirmar que existen respaldos y que se han probado.
4. Orden 13 — `NV-07` (`S`): definir quién recibe las alertas.
5. Orden 14 — `SEC-AUTHZ-001a` (`S`): la matriz de permisos, sin la cual la Fase 1 no puede continuar.

El `ICAL_PUBLIC_TOKEN` no necesita rotación inmediata: se generó en un gestor de
contraseñas. Queda cubierto por el calendario ordinario de rotación (orden 38).

Ninguna tarea `XL`: la de mayor esfuerzo es `SEC-AUTHZ-001`, ya subdividida en
cinco entregables (órdenes 14-18) que se pueden desplegar por separado.
