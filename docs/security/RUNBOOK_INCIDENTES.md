# Runbook de incidentes de seguridad — ERP-QKT

**Backlog de seguridad**: orden 52 (`SEC-DOC-001`), Issue #190.
**Responsable de mantener este documento**: Propietario + Dev.
**Estado**: borrador de Dev, pendiente de revisión y aprobación del
propietario — los puntos marcados `[CONFIRMAR:]` requieren una decisión o
un dato que solo el propietario tiene (a quién llamar, credenciales de
paneles que Dev no opera).

Este documento asume que quien lo ejecuta tiene acceso al dashboard de
Railway del proyecto y, según el incidente, a los paneles de Cloudflare,
Brevo, Meta for Developers (WhatsApp), Openpay y GitHub. No repite cómo
usar esos paneles — solo qué hacer y en qué orden.

---

## 1. Cómo reconocer que hay un incidente

Señales que deberían disparar este runbook, no una investigación informal:

- Un `django.security.*` con volumen inusual en los Deploy Logs de Railway
  (`AuthorizationAuditMiddleware` deja `usuario=... ruta=...` en cada 403;
  `django.security.DisallowedHost`/`django.security.csrf` son de fábrica de
  Django). Ver `core_erp/middleware.py`.
- Un pico de 429 (rate limit) en `/admin/login/`, `/cotizar/enviar/`, el
  portal (`/mi-evento/...`) o los webhooks — indicio de fuerza bruta o
  scraping.
- Un webhook de Openpay o WhatsApp con eventos que no corresponden a
  actividad real del negocio.
- Un correo o alerta de Railway/GitHub sobre un push, deploy o cambio de
  variable de entorno que nadie del equipo reconoce.
- `gitleaks` (job `security` en CI) detecta un secreto en un PR — ver
  §5.3, no es hipotético: ya bloqueó builds antes (orden 32).
- Cualquier reporte directo de un cliente sobre datos que no debería ver o
  un cargo que no reconoce.

Ante la duda, tratarlo como incidente: los pasos de contención de este
documento son de bajo costo y en su mayoría reversibles.

---

## 2. Roles durante un incidente

| Rol | Quién | Hace qué |
|---|---|---|
| Quien detecta | Cualquiera | Documenta hora, qué vio, y avisa a Dev/Propietario por el canal de la orden 13 (`[CONFIRMAR:]` — no está definido en este backlog) |
| Contención | Dev con acceso a Railway/Cloudflare | Ejecuta §3 |
| Decisión de negocio | Propietario | Autoriza rotar credenciales de pago (Openpay), notificar a clientes, o congelar el sitio |
| Comunicación | `[CONFIRMAR:]` — quién redacta y aprueba un aviso a clientes si aplica |

**Este backlog no define un canal ni un responsable de guardia** (esa es
la orden 13, `NV-07`, todavía pendiente) — hasta que exista, el primer
paso de cualquier incidente fuera de horario es contactar directamente al
propietario.

---

## 3. Contención inmediata (primeros 15 minutos)

Acciones reversibles, sin esperar diagnóstico completo. No todas aplican a
todo incidente — elegir según la señal del §1.

### 3.1 — Sesión de staff/admin comprometida (credencial filtrada, cuenta con acceso indebido)

1. **Desactivar la cuenta**, no borrarla: en `/admin/auth/user/`, quitar
   `is_active`. Borrarla rompe la trazabilidad (`created_by`,
   `aplicada_por`, `cancelada_por` en pólizas, auditoría de acciones
   destructivas de la orden 48) sin ganar nada que desactivar no dé ya.
2. **Revocar TODAS las sesiones activas del sistema** (no solo la de esa
   cuenta — Django no permite invalidar la sesión de un usuario específico
   sin tocar las demás salvo que se identifique su `session_key`):
   - Rápido y sin downtime: `python manage.py clearsessions` **no sirve**
     para esto — solo borra sesiones ya expiradas. Para invalidar sesiones
     **activas** hay que borrar filas de la tabla `django_session`
     directamente: `Session.objects.all().delete()` desde
     `manage.py shell` (o un `TRUNCATE` vía la consola de Postgres de
     Railway). Efecto: todo el mundo, staff y Dirección incluida, tiene
     que volver a iniciar sesión.
   - Más agresivo (si se sospecha que el propio `SECRET_KEY` está
     comprometido, no solo una sesión): rotar `SECRET_KEY` en Railway
     invalida todas las cookies de sesión firmadas con la clave vieja de
     inmediato, sin tocar la base de datos. Ver §5.1 antes de hacerlo —
     tiene el mismo efecto sobre `django_session` que un `DELETE`, así que
     no hace falta combinar ambos.
3. Revisar `AuthorizationAuditMiddleware` en los Deploy Logs (busca
   `403 de autorización: usuario=<esa cuenta>`) para saber qué intentó
   acceder sin permiso mientras estuvo activa.
4. Si la cuenta es superusuario y el ERP ya tiene TOTP obligatorio
   (backlog orden 42, `SEC-AUTHN-002` — confirmar si ya está mergeada):
   el dispositivo TOTP de esa cuenta (`TOTPDevice`, en
   `/admin/otp_totp/totpdevice/`) debe borrarse también, no solo
   desactivar la cuenta — si alguien recupera el acceso más adelante con
   la contraseña vieja, el segundo factor viejo seguiría siendo válido.

### 3.2 — Secreto expuesto (commiteado, filtrado en un log, o en un canal no seguro)

**No hay "casi expuesto"**: si un secreto salió del repo/entorno seguro
por cualquier medio, se rota — no se evalúa si alguien ya lo vio. Ver la
tabla de credenciales del §5 para el procedimiento de cada una.

Si el secreto está **commiteado** (no solo en un `.env` local): rotar la
credencial **cierra el riesgo real**; reescribir el historial de git
(`git filter-repo`/BFG) es opcional y de menor prioridad — el secreto
sigue siendo público en el historial hasta que se rota, sin importar si
se reescribe o no. Ver Memoria de `CLAUDE.md`, entrada de la orden 33: ya
existe un proceso (`gitleaks detect --log-opts="--all"`) para auditar el
historial completo si hace falta confirmar el alcance.

### 3.3 — Actividad anómala en pagos (Openpay)

1. **No cancelar transacciones en curso a ciegas** — un cliente con un
   cobro legítimo en proceso no debe perder su pago por una contención
   mal dirigida.
2. Revisar el dashboard de Openpay (producción, no sandbox — el ERP está
   en producción desde el 2026-08-05, ver Memoria) filtrando por el rango
   de tiempo del incidente.
3. Si hay evidencia de fraude activo (cargos no autorizados, patrón de
   tarjetas robadas): `[CONFIRMAR:]` — decisión del propietario, incluye
   posible congelamiento temporal de nuevos cobros. El soporte de Openpay
   es **(55) 97 55 35 59 / soporte@openpay.mx** (ver Memoria).
4. `borrar_transacciones_de_prueba` (orden 48, ya con confirmación
   server-side) **nunca** es la herramienta para esto — es solo para datos
   de prueba explícitamente marcados, se niega a correr en
   `OPENPAY_MODE=production`.

### 3.4 — Vulnerabilidad activa en una vista pública (XSS, bypass de autorización, etc.)

1. Si el código para corregirla existe pero no está desplegado: fusionar y
   desplegar es la contención — no hay "modo mantenimiento" configurado en
   este repo, así que la vía más rápida es el fix real, no un apagado
   parcial.
2. Si la vulnerabilidad depende de una variable de entorno (como el feed
   iCal sin `ICAL_PUBLIC_TOKEN`, orden 5 — ya cerrada, pero es el patrón de
   referencia): a veces la contención más rápida es **definir la variable
   en Railway**, sin esperar un deploy de código. Revisar primero si el
   hallazgo tiene esa forma antes de escribir código bajo presión.
3. Documentar el hallazgo en `docs/security/BACKLOG_SEGURIDAD.md` con una
   orden nueva aunque ya esté contenido — el backlog es el registro de qué
   se corrigió y por qué, no solo de lo pendiente.

---

## 4. Preservación de evidencia

Antes de "limpiar" cualquier cosa, capturar:

- **Deploy Logs de Railway** del rango de tiempo del incidente — Railway
  los retiene por tiempo limitado, exportarlos (copiar/pegar o descarga)
  es más urgente que cualquier otro paso de diagnóstico.
- El registro de `django.security` (403 de autorización con usuario y
  ruta — orden 36) y el `X-Correlation-ID` de las peticiones sospechosas
  (orden 45) — con el correlation ID se puede aislar **todas** las líneas
  de log de una petición específica entre el tráfico intercalado de los
  workers de gunicorn.
- Si aplica: el payload crudo del webhook sospechoso (Openpay, WhatsApp,
  Jibble) — quedan en los logs si el handler los loguea antes de fallar;
  si no, capturarlo desde el panel del proveedor.
- Un `pg_dump` o snapshot de las tablas afectadas **antes** de revertir
  cualquier dato — necesario si más adelante hay que reconstruir qué pasó
  o hay una obligación legal de reportar el incidente.
- Una copia de este documento con las horas reales de cada paso ejecutado
  (no las de la plantilla) — es el registro post-mortem.

---

## 5. Rotación de credenciales

Tabla de qué rotar y dónde, por variable de entorno de Railway. Todas se
rotan desde el dashboard del proveedor correspondiente y luego se
actualiza el valor en Railway — **el orden importa**: generar la
credencial nueva primero, actualizar Railway después, revocar la vieja al
final, para no dejar una ventana sin credencial válida.

| Credencial | Variable(s) en Railway | Dónde se rota | Efecto colateral al rotar |
|---|---|---|---|
| Django `SECRET_KEY` | `SECRET_KEY` | Se genera una nueva (`django.core.management.utils.get_random_secret_key()`) | Invalida **todas** las sesiones activas y cualquier token firmado con la clave vieja (ver §3.1) |
| Base de datos | — | Railway (rotar la contraseña de Postgres desde su panel) | Todos los workers necesitan reiniciar con la `DATABASE_URL` nueva — coordinar el deploy |
| Openpay | `OPENPAY_PRIVATE_KEY`, `OPENPAY_PUBLIC_KEY`, `OPENPAY_WEBHOOK_USER`, `OPENPAY_WEBHOOK_PASSWORD` | Dashboard de Openpay producción | El webhook debe volver a autenticarse (Basic Auth) — probar con una transacción de prueba antes de confiar en que quedó bien |
| Brevo (email) | `BREVO_API_KEY` | Panel de Brevo | Ninguno inmediato — los envíos en cola pueden fallar durante la ventana de rotación |
| WhatsApp / Meta Cloud API | `WA_CLOUD_API_TOKEN` | Meta for Developers | Un token de usuario del sistema **no expira solo**, pero si se revoca desde Meta hay que generar uno nuevo y confirmar que sigue siendo "permanente" (no el de 24h de prueba) — ver el error 190 que ya documenta `comunicacion/services.py` |
| Cloudflare R2 (bucket público) | `CLOUDFLARE_R2_ACCESS_KEY_ID`, `CLOUDFLARE_R2_SECRET_ACCESS_KEY` | Panel de Cloudflare R2 | Afecta imágenes de landing servidas públicamente — sin downtime si se rota con la cuenta de acceso, no el bucket |
| Cloudflare R2 (bucket privado) | `CLOUDFLARE_R2_PRIVATE_ACCESS_KEY_ID`, `CLOUDFLARE_R2_PRIVATE_SECRET_ACCESS_KEY` | Panel de Cloudflare R2 | Afecta descargas de documentos sensibles (ARCO, nómina, contratos) — confirmar que `core_erp/descargas.py` sigue funcionando tras rotar |
| Jibble | `JIBBLE_CLIENT_ID`, `JIBBLE_CLIENT_SECRET` | Panel de Jibble | La sincronización de nómina falla en silencio con fallback documentado (Memoria) hasta que se actualice |
| Cron de nómina | `NOMINA_CRON_TOKEN` | Se genera un valor nuevo (no viene de un proveedor externo) | Hay que actualizar el Cron de Railway que llama al webhook con el token nuevo |
| Feed iCal | `ICAL_PUBLIC_TOKEN` | Se genera un valor nuevo | Hay que actualizar la URL registrada en Airbnb con el `?token=` nuevo, o Airbnb deja de sincronizar en silencio (ver Memoria, incidente original de esta variable) |

**Todas las variables de entorno viven únicamente en Railway** — no hay
`.env` de producción en el repo ni en ningún otro sistema; `.env.example`
documenta los nombres, nunca valores reales.

---

## 6. Comunicación

`[CONFIRMAR:]` — este backlog no define una política de notificación a
clientes ni un umbral de "esto amerita avisar". Puntos que el propietario
debe decidir y que este documento debe reflejar cuando se resuelvan:

- ¿A partir de qué severidad se notifica a los clientes afectados?
- ¿Existe una obligación legal de notificar (dato personal expuesto, bajo
  la legislación aplicable a `legal/` — ARCO)? El módulo `legal` ya
  gestiona consentimiento y bitácora ARCO; un incidente que toque datos de
  esa bitácora podría activar esa obligación.
- ¿Quién redacta y aprueba el mensaje antes de enviarlo?
- Canal interno de aviso inmediato (bloqueado por la orden 13, `NV-07`,
  todavía sin resolver — este runbook depende de que esa orden se cierre
  para tener un "a quién le marco a las 2 a.m." real).

---

## 7. Cierre del incidente

1. Confirmar que la causa raíz está corregida en código (no solo
   contenida) — si el fix queda pendiente, abrir una orden nueva en
   `docs/security/BACKLOG_SEGURIDAD.md`, no dejarlo solo en este runbook.
2. Confirmar que todas las credenciales tocadas en §5 quedaron
   funcionando en producción (no solo rotadas — probadas).
3. Revisar si el incidente reveló un hueco de monitoreo (algo que debió
   generar una alerta y no la generó) y documentarlo como una orden nueva.
4. Escribir el post-mortem: qué pasó, cuándo se detectó, cuánto tardó la
   contención, qué se hizo, qué falló en el proceso mismo (no solo en el
   sistema). Guardarlo en `docs/security/` con fecha, no sobrescribir este
   runbook con detalles de un incidente específico.
5. Actualizar este runbook si el incidente reveló un paso faltante —es un
   documento vivo, no una plantilla que se llena una vez.

---

## 8. Qué falta para que este runbook esté completo

Este borrador cubre lo que Dev puede definir desde el código y la
infraestructura documentada en el repo. Los `[CONFIRMAR:]` de arriba
—canal de alertas (orden 13), política de notificación a clientes, y
quién tiene acceso a cada panel de proveedor— son decisiones del
propietario, no technical debt. Hasta que se resuelvan, este documento es
utilizable pero incompleto en la parte de comunicación.
