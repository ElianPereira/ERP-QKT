# Plan de implementación de seguridad — ERP-QKT

**Fecha**: 2026-08-12 · **Commit base**: `f813dcc` · **Issue**: #190
**Fuente**: `AUDITORIA_SEGURIDAD.md` y `BACKLOG_SEGURIDAD.md`.

Este plan ordena el backlog respetando dependencias reales. **No se ha
implementado nada**: la ejecución empieza cuando el propietario apruebe cada
fase.

## Cómo leer este plan

Cada fase indica objetivo, tareas en orden, archivos que cambiarán, dependencias,
estrategia de pruebas, despliegue y rollback, criterios de salida y qué requiere
decisión humana. Las fases 0 y 1 son secuenciales entre sí; de la 2 en adelante
se pueden solapar si hay capacidad.

Restricción que aplica a todas las fases: `contabilidad` genera pólizas por
signals y `comercial` nunca las toca a mano. Ningún cambio de seguridad debe
alterar ese flujo. Igualmente, `core_erp/impuestos.py` sigue siendo la fuente
única del IVA.

---

## Fase 0 — Contención inmediata

**Objetivo**: eliminar el único P0 y determinar si hay dos exposiciones activas.
**Riesgos cubiertos**: `SEC-XSS-001`, y la incertidumbre sobre `SEC-DATA-001` y `SEC-FILE-001`.
**Duración estimada**: una sesión de trabajo.

### Tareas en orden

1. **Corregir `SEC-XSS-001`** (backlog 1). En `airbnb/views.py::calendario_unificado`, pasar `eventos_lista` al contexto sin `json.dumps`. En `calendario_unificado.html`, sustituir la interpolación por:
   ```html
   {{ eventos_json|json_script:"eventos-data" }}
   <script>
       var eventos = JSON.parse(document.getElementById('eventos-data').textContent);
   ```
   `json_script` escapa `<`, `>` y `&`, que es exactamente lo que `json.dumps` no hace.
2. **Migrar los otros dos `|safe` sobre `json.dumps`** (backlog 2) en los dos dashboards. Hoy no son explotables — solo llevan fechas y cifras agregadas — pero comparten el patrón, y corregirlos ahora evita que un cambio futuro los active.
3. **Verificar `NV-02`** (backlog 3): ¿está `ICAL_PUBLIC_TOKEN` definida y con valor no vacío en Railway?
4. **Verificar `NV-01`** (backlog 4): pedir desde una sesión anónima la URL de un archivo bajo `arco/identificaciones/` y anotar el código de respuesta.

Los pasos 3 y 4 no son de código y pueden correr en paralelo con los 1 y 2.

### Archivos que cambiarán

- `airbnb/views.py` (línea 125)
- `airbnb/templates/admin/airbnb/calendario_unificado.html` (línea 361)
- `comercial/templates/admin/dashboard.html` (líneas 162-167)
- `airbnb/templates/admin/airbnb/dashboard.html` (líneas 193-196)
- `comercial/views.py` (líneas 500-501) y `airbnb/views.py` (contexto del dashboard), para dejar de serializar antes de tiempo

### Dependencias y cambios incompatibles

Ninguna dependencia. El cambio es incompatible con cualquier JavaScript que
esperara la variable ya interpolada: hay que revisar que `eventos` se siga
consumiendo igual en el resto del script del calendario, y lo mismo con
`chart_labels` y las series en los dashboards. Es la única forma de romper algo
aquí, y se detecta abriendo las tres pantallas.

### Estrategia de pruebas

- Test de regresión: cliente con `nombre = '</script><script>window.x=1</script>'`, `GET /admin/calendario/` con sesión de staff, afirmar que `'</script><script>'` no aparece en la respuesta.
- Verificación manual: abrir `/admin/calendario/`, el dashboard de `comercial` y el de `airbnb` y confirmar que el calendario pinta eventos y que las gráficas renderizan.
- `python manage.py test airbnb comercial`.

### Despliegue y rollback

Despliegue normal a Railway. Rollback = revertir el commit; el cambio no toca
base de datos ni configuración, así que revertir es inmediato y sin efectos
secundarios.

### Criterios de salida

- [ ] El test de regresión de XSS pasa.
- [ ] Las tres pantallas afectadas funcionan igual que antes.
- [ ] Respuestas de `NV-01` y `NV-02` documentadas en el Issue #190.
- [ ] Si `NV-02` resulta vacía: `SEC-DATA-001` se trata como incidente activo y se adelanta el orden 5 a esta fase.
- [ ] Si `NV-01` devuelve 200 anónimo: `SEC-FILE-001` se trata como incidente activo y la fase 2 se adelanta a la 1.

### Requiere decisión humana

Nada en los pasos 1 y 2. Los pasos 3 y 4 requieren acceso a Railway y a
Cloudflare, que el propietario o quien administre la infraestructura debe
ejecutar.

---

## Fase 1 — Identidad, autorización y accesos

**Objetivo**: cerrar los P1 de control de acceso: feed abierto, portal adivinable
y autorización plana.
**Riesgos cubiertos**: `SEC-DATA-001`, `SEC-AUTHN-001`, `SEC-AUTHZ-001`.
**Duración estimada**: 1-2 semanas.

### Tareas en orden

1. **Feed iCal fail-closed** (backlog 5). Invertir la condición: sin token configurado, 403.
2. **Reducir el contenido del feed** (backlog 6) a `SUMMARY` genérico sin nombre de cliente ni número de asistentes. Airbnb solo necesita saber que la fecha está ocupada.
3. **Mensaje de error unificado en `portal_acceso`** (backlog 9).
4. **Contador de intentos por cotización** (backlog 10), reusando `_buckets_login` de `ratelimit.py` — el patrón ya existe y está probado.
5. **`portal_acceso` deja de crear `PortalCliente`** (backlog 11).
6. **Matriz de permisos por área** (backlog 14). Es decisión de negocio, no técnica: hay que saber quién debe ver qué.
7. **Permisos en nómina** (backlog 15).
8. **Permisos en contabilidad y reportes financieros** (backlog 16).
9. **`importar_historico_view` solo superusuario** (backlog 17).
10. **Permisos por modelo en el admin** (backlog 18).

### Archivos que cambiarán

- `airbnb/views.py::generar_ical_eventos`
- `comercial/views_portal.py::portal_acceso`
- `core_erp/ratelimit.py` (función auxiliar de buckets por cotización)
- `nomina/views.py`, `contabilidad/views.py`, `reportes/views.py`, `airbnb/views.py`, `comercial/views.py` (decoradores)
- `*/admin.py` (permisos por modelo)
- Migración para los permisos personalizados (`Meta.permissions`)

### Dependencias y cambios incompatibles

**Este es el punto de mayor riesgo operativo de todo el plan.** Aplicar permisos
sin haber creado los grupos y asignado usuarios deja al equipo fuera de
pantallas que usa a diario. El orden importa: primero crear grupos y asignar,
después restringir.

La tarea 5 cambia comportamiento visible: un cliente cuya cotización no tenga
`PortalCliente` dejará de poder entrar por `/mi-evento/`. Hay que confirmar con
el propietario que el alta del portal ocurre siempre en el flujo comercial, o
dejar una vía alterna.

La tarea 2 requiere confirmar que Airbnb sigue interpretando el feed
correctamente tras reducir el contenido.

### Estrategia de pruebas

- Tests de autorización cruzada: por cada área, un usuario con permiso (200) y otro sin él (403).
- Test de que ambos errores de `portal_acceso` devuelven el mismo texto.
- Test de bloqueo por cotización desde IPs distintas.
- Test de que el `.ics` no contiene nombres de clientes.
- **Verificación manual imprescindible**: cada persona del equipo entra con su cuenta y confirma que llega a lo que necesita.

### Despliegue y rollback

Despliegue en dos pasos: (a) crear grupos y asignar usuarios en producción, sin
restringir nada; (b) desplegar los decoradores. Entre ambos, verificar
asignaciones.

Rollback: revertir los decoradores es inmediato. Los grupos pueden quedarse — no
restringen por sí solos.

### Criterios de salida

- [ ] `/airbnb/ical/eventos/` sin token configurado devuelve 403.
- [ ] El `.ics` no contiene el nombre de ningún cliente y Airbnb sigue bloqueando fechas.
- [ ] Código inexistente y teléfono incorrecto devuelven el mismo mensaje.
- [ ] N intentos fallidos contra la misma cotización desde IPs distintas producen 429.
- [ ] Un staff sin permisos de nómina recibe 403 en `/admin/nomina/cargar/`.
- [ ] Un staff sin permisos de contabilidad recibe 403 en los reportes financieros.
- [ ] Todo el equipo confirma que puede trabajar con normalidad.

### Requiere decisión humana

- **La matriz de permisos** (backlog 14): qué grupos existen y quién pertenece a cada uno. Sin esta decisión la fase no arranca.
- Revisar y depurar las cuentas `is_staff`/`is_superuser` existentes (`NV-08`).
- Confirmar que reducir el contenido del feed no rompe ningún proceso del negocio.

---

## Fase 2 — Archivos, validación y lógica de negocio

**Objetivo**: cerrar el P1 de archivos y los P2 de validación e inyección.
**Riesgos cubiertos**: `SEC-FILE-001`, `SEC-FILE-002`, `SEC-VAL-001`, `SEC-CSRF-001`, `SEC-INFO-001`, `SEC-INJ-001`, `SEC-SESS-001`.
**Duración estimada**: 1-2 semanas.

> Si `NV-01` reveló acceso anónimo al bucket, las tareas 1 y 2 se adelantan a la Fase 0.

### Tareas en orden

1. **Bucket privado y URLs firmadas** (backlog 7).
2. **Documentos sensibles por vista autenticada** (backlog 8), replicando el patrón de `legal/views.py`, incluido `portal_descargar_contrato`.
3. **Validadores en los `FileField`** (backlog 35).
4. **`csrf_exempt` fuera del cotizador** (backlog 23).
5. **`forms.Form` en el cotizador** (backlog 24).
6. **Mensajes de error genéricos** (backlog 25).
7. **Escapado RFC 5545 en el feed iCal** (backlog 26).
8. **Caducidad del token de portal** (backlog 27).

### Archivos que cambiarán

- `core_erp/settings.py` (bloque `STORAGES`)
- `comercial/views_portal.py`, `nomina/views.py`, `contabilidad/` (vistas de descarga)
- `comercial/models.py`, `nomina/models.py`, `legal/models.py`, `contabilidad/models.py`, `facturacion/models.py` (validadores) + migración
- `comercial/views_cotizador.py` y un `forms.py` nuevo
- `airbnb/views.py` (escapado iCal)
- `comercial/models.py::PortalCliente` (`expira_en`) + migración
- Plantilla del cotizador (token CSRF)

### Dependencias y cambios incompatibles

**El cambio de bucket a privado es el más delicado de todo el plan.** Las
imágenes de la landing se sirven del mismo storage: si se hace privado sin
separar, la página pública se queda sin imágenes. Hay dos caminos:

- **Separar buckets** — uno público solo para `landing/` y `productos/`, otro privado para documentos. Es más trabajo pero deja el modelo correcto.
- **Un solo bucket privado con URLs firmadas para todo** — más simple, pero mete firma y caducidad en las imágenes públicas, lo que complica el cacheo de la landing.

**Recomendación**: separar buckets. La landing no tiene por qué compartir
almacenamiento con identificaciones oficiales.

La tarea 8 (`expira_en`) requiere decidir el plazo y qué hacer con los tokens ya
emitidos: la migración debe poblar el campo para los existentes, y conviene no
caducarlos todos de golpe.

La tarea 4 puede romper el cotizador si el JS no envía el token; probar en
staging antes de producción.

### Estrategia de pruebas

- Test de que `SolicitudARCO.identificacion.url` no es accesible sin credenciales.
- Test de que `portal_descargar_contrato` sirve contenido en vez de redirigir.
- Test de que un `.html` renombrado a `.pdf` es rechazado.
- Test de que `POST /cotizar/enviar/` sin CSRF devuelve 403.
- Test de que un 500 no filtra el texto de la excepción.
- Test de CRLF en el feed iCal.
- Test de portal expirado → 404.
- **Verificación manual**: enviar una cotización desde el cotizador público, descargar contrato desde el portal, y comprobar que la landing carga todas sus imágenes.

### Despliegue y rollback

El cambio de storage se despliega solo, sin mezclarlo con nada más. Antes:
inventariar los archivos existentes y confirmar que la migración de bucket (si
la hay) los conserva accesibles. Rollback: volver a la configuración anterior de
`STORAGES` — los archivos no se mueven, solo cambia cómo se sirven.

El resto son cambios de código con rollback por revert.

### Criterios de salida

- [ ] Ninguna URL de documento sensible es accesible sin autenticación.
- [ ] La landing carga todas sus imágenes.
- [ ] El cotizador público funciona con CSRF activo.
- [ ] Los tests de la fase pasan y la suite completa sigue en verde.

### Requiere decisión humana

- Separar buckets o no.
- Plazo de caducidad del token de portal y tratamiento de los ya emitidos.
- Acceso a Cloudflare para reconfigurar R2.

---

## Fase 3 — Configuración de producción, dependencias e infraestructura

**Objetivo**: cerrar los P2 de configuración y cadena de suministro.
**Riesgos cubiertos**: `SEC-CFG-001` a `SEC-CFG-004`, `SEC-DEP-001`, `SEC-RL-001`, `SEC-RL-002`.
**Duración estimada**: 1 semana.

### Tareas en orden

1. **Rate limiting en los endpoints que faltan** (backlog 19, 20, 21). Es lo más barato de toda la fase y cubre el hueco más ancho.
2. **Verificar el edge y ajustar `RATELIMIT_TRUSTED_PROXY_COUNT`** (backlog 22).
3. **`SECURE_PROXY_SSL_HEADER`** (backlog 28).
4. **`SECURE_REFERRER_POLICY`** (backlog 41).
5. **`requirements.lock`** (backlog 34).
6. **`USER` en el `Dockerfile`** (backlog 43).
7. **Fijar acciones de `ci.yml` por SHA** (backlog 46).
8. **`MEDIA_ROOT`** (backlog 49).

### Archivos que cambiarán

- `comercial/views_portal.py`, `comercial/views_cotizador.py`, `comercial/views_openpay.py`, `nomina/views.py`, `airbnb/views.py` (decoradores de rate limit)
- `core_erp/settings.py`
- `requirements.txt` → `requirements.lock`
- `Dockerfile`
- `.github/workflows/ci.yml`

### Dependencias y cambios incompatibles

La tarea 3 depende de la evidencia de `NV-05`: definir
`SECURE_PROXY_SSL_HEADER` con una cabecera que el edge no envíe, o que un
atacante pueda falsificar, es peor que no definirla. Verificar primero.

La tarea 6 puede romper el arranque si el usuario sin privilegios no puede
escribir donde `collectstatic` deposita los archivos. Probar la imagen en local
antes de desplegar.

La tarea 5 puede cambiar versiones instaladas respecto a las que hay hoy en
producción: generar el lock a partir de un `pip freeze` del entorno actual, no
desde cero.

### Estrategia de pruebas

- Test parametrizado que recorra los endpoints públicos y verifique 429 al superar el límite.
- Verificación en producción de que una petición con XFF fabricado no altera la IP registrada.
- `curl -I` contra producción para confirmar HSTS y `Referrer-Policy`.
- Build local de la imagen Docker con el nuevo `USER` y arranque completo.
- Dos builds del mismo commit → mismo `pip freeze`.

### Despliegue y rollback

Los cambios de settings van juntos y se verifican con `curl -I` inmediatamente
después. El `Dockerfile` va solo, porque un fallo ahí impide el arranque:
tener a mano el commit anterior para revertir rápido.

### Criterios de salida

- [ ] Todo endpoint público tiene rate limit.
- [ ] `request.is_secure()` devuelve `True` en producción.
- [ ] `curl -I` muestra HSTS y `Referrer-Policy`.
- [ ] El contenedor no corre como root y la app arranca.
- [ ] El build es reproducible.

### Requiere decisión humana

- Acceso a Railway para las verificaciones de `NV-04` y `NV-05`.
- Aceptar el riesgo de cambiar el `Dockerfile` en un despliegue.

---

## Fase 4 — Logging, alertas, respaldos y respuesta a incidentes

**Objetivo**: poder detectar y responder. Hoy no hay evidencia de que un
incidente sería percibido.
**Riesgos cubiertos**: `SEC-LOG-001`, `SEC-LOG-002`, `NV-03`, `NV-07`, `SEC-DOC-001`.
**Duración estimada**: 1 semana.

> `NV-03` (respaldos) y `NV-07` (alertas) están marcados P1 en el backlog. Si la
> verificación de la Fase 0 revela que no hay respaldos, esta fase se adelanta
> completa: sin respaldo probado, cualquier otro control es secundario.

### Tareas en orden

1. **Verificar respaldos de PostgreSQL** (backlog 12): frecuencia, retención, cifrado, última restauración probada.
2. **Definir alertas y responsables** (backlog 13).
3. **Logger `django.security` y registro de 403** (backlog 36).
4. **Correlation/request ID** (backlog 45).
5. **Runbook de incidentes** (backlog 52).
6. **Calendario de rotación de credenciales** (backlog 38).

### Archivos que cambiarán

- `core_erp/settings.py` (bloque `LOGGING`)
- `core_erp/middleware.py` (request ID)
- `docs/security/RUNBOOK_INCIDENTES.md` (nuevo)

### Dependencias y cambios incompatibles

Ninguna incompatibilidad técnica. La dependencia real es de disponibilidad
humana: el runbook y las alertas exigen decidir quién responde y por qué canal,
y eso no lo resuelve el código.

### Estrategia de pruebas

- Una petición con `Host` inválido produce una línea de log identificable.
- Todas las líneas de un mismo request comparten identificador.
- **Simulacro**: provocar un error controlado y verificar que la alerta llega a quien debe.
- **Prueba de restauración**: restaurar un respaldo en un entorno desechable y medir cuánto tarda. Sin esto, "hay backups" es una suposición.

### Despliegue y rollback

Cambios de bajo riesgo. El middleware de request ID toca todas las peticiones:
verificar que no rompe las respuestas de PDF ni las de JSON.

### Criterios de salida

- [ ] Evidencia documentada de respaldos, con fecha de la última restauración probada.
- [ ] Canal de alertas definido, con responsable nombrado.
- [ ] Simulacro de alerta ejecutado con éxito.
- [ ] Runbook revisado por el propietario.
- [ ] RPO/RTO documentados.

### Requiere decisión humana

Casi toda la fase: quién responde, por qué canal, con qué tiempo de reacción, y
qué se comunica a los clientes si hay una brecha de datos personales — esto
último con implicaciones legales bajo la LFPDPPP.

---

## Fase 5 — Pruebas automatizadas, CI y mantenimiento continuo

**Objetivo**: que lo corregido no se pierda con el tiempo.
**Riesgos cubiertos**: `SEC-CI-001`, `SEC-SECRET-002`, `SEC-TEST-001`, `SEC-CFG-002`, `SEC-AUTHN-002`, `SEC-BIZ-001`, `SEC-DOS-001`, `SEC-XSS-003`.
**Duración estimada**: 2 semanas.

### Tareas en orden

1. **`gitleaks` sobre el historial completo** (backlog 33). Va primero: si aparece un secreto, hay que rotarlo antes que cualquier otra cosa de esta fase.
2. **`ruff check --fix` y revisión de los 7 `E722`** (backlog 29).
3. **Quitar `continue-on-error` del lint** (backlog 30).
4. **Ruleset `S` de ruff** (backlog 31).
5. **`gitleaks` en CI** (backlog 32).
6. **Suite de tests de seguridad** (backlog 51).
7. **CSP del admin en Report-Only** (backlog 37).
8. **MFA para superusuarios** (backlog 42).
9. **Resto de P3**: replay del webhook, consulta acotada del calendario, sanitizado de markdown, confirmación en acciones destructivas.

### Archivos que cambiarán

- `pyproject.toml`
- `.github/workflows/ci.yml`
- Prácticamente todos los `.py` (autofix de ruff — es un diff grande pero mecánico)
- `core_erp/middleware.py` (CSP del admin)
- `requirements.txt` (django-otp)
- Nuevos `test_seguridad_*.py` por app

### Dependencias y cambios incompatibles

**El autofix de ruff produce un diff enorme.** Debe ir en su propio PR, sin
mezclar con cambios funcionales, o cualquier revisión posterior se vuelve
ilegible. Los 400 `W293` y 98 `I001` son ruido puro; los 7 `E722` (`except:`
desnudo) sí merecen revisión caso por caso, porque un `except:` que traga
`KeyboardInterrupt` o `SystemExit` puede ocultar fallos reales.

El ruleset `S` va a marcar hallazgos existentes: activarlo con `--exit-zero` una
primera vez, revisar la lista, añadir `noqa` justificados donde corresponda, y
recién entonces hacerlo bloqueante.

La CSP del admin es la tarea con más probabilidad de romper algo visible:
Jazzmin y AdminLTE usan scripts y estilos inline. Por eso empieza en
Report-Only, y por eso está al final del plan y no al principio.

MFA cambia el flujo de login de todos los superusuarios: coordinar el momento y
tener una vía de recuperación (acceso por consola) antes de activarlo.

### Estrategia de pruebas

- Un PR de prueba que reintroduzca cada hallazgo corregido debe fallar el CI.
- La CSP en Report-Only se observa una semana antes de hacerla bloqueante.
- MFA se prueba primero con una cuenta de prueba, no con la del propietario.

### Despliegue y rollback

Un PR por tarea. El de ruff, aislado. MFA se despliega con una ventana acordada
y con acceso a consola verificado antes de empezar.

### Criterios de salida

- [ ] `ruff check .` sin errores y sin `continue-on-error`.
- [ ] El ruleset `S` corre y bloquea.
- [ ] `gitleaks` corre en CI y sobre el historial, sin hallazgos pendientes.
- [ ] Un PR que reintroduzca cualquier hallazgo corregido falla el CI.
- [ ] `/admin/` devuelve cabecera CSP sin romper Jazzmin.
- [ ] Los superusuarios tienen MFA.

### Requiere decisión humana

- Momento para activar MFA y quién conserva acceso de emergencia.
- Aceptar el PR de autofix masivo de ruff.
- Rotar credenciales si `gitleaks` encuentra algo en el historial.

---

## Resumen del plan

| Fase | Objetivo | Esfuerzo | Bloqueante para |
|---|---|---|---|
| 0 | Contención del P0 y verificaciones | 1 sesión | Todo lo demás |
| 1 | Identidad, autorización y accesos | 1-2 semanas | Fase 5 (tests de autorización) |
| 2 | Archivos, validación y negocio | 1-2 semanas | — |
| 3 | Configuración, dependencias, infra | 1 semana | — |
| 4 | Logging, alertas, respaldos | 1 semana | — |
| 5 | Pruebas, CI y mantenimiento | 2 semanas | — |

**Ruta crítica**: Fase 0 → Fase 1 → Fase 5. Las fases 2, 3 y 4 pueden solaparse
con la 1 si hay capacidad, salvo que la Fase 0 revele que `SEC-FILE-001` es una
exposición activa, en cuyo caso la Fase 2 se adelanta.

## Lo que este plan no resuelve

Conviene decirlo explícitamente:

- **La configuración de infraestructura no se puede arreglar desde el repositorio.** Los diez puntos `NO VERIFICABLE` de la auditoría requieren acceso a Railway y Cloudflare. Un plan que los ignorara daría una falsa sensación de completitud.
- **Los respaldos son el punto ciego más grande.** Si no existen o nunca se han probado, ningún control de esta lista importa tanto como eso.
- **La autorización por áreas exige una decisión de negocio.** No es un problema técnico: alguien tiene que decidir quién ve la nómina.
- **Este plan no cubre auditoría externa ni pentesting.** Es una revisión de código; un pentest sobre el sistema desplegado encontraría cosas que leer el repositorio no revela.
