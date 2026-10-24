# Variables de entorno

Catálogo de todas las variables que lee el ERP (Issue #424). **Solo nombres: ningún valor va en este archivo.**

## Regla de nombres

```
ORIGEN_SERVICIO_DATO[_DISTINTIVO]
```

- **ORIGEN**: quién la emite. Proveedor (`META`, `ANTHROPIC`, `OPENPAY`, `CLOUDFLARE`, `BREVO`, `JIBBLE`), configuración propia (`ERP`) o framework (`DJANGO`).
- **SERVICIO**: producto o módulo (`WA`, `R2`, `KOOXI`, `EMAIL`, `PORTAL`, `FISCAL`…).
- **DATO**: `API_KEY`, `SECRET`, `TOKEN`, `ID`, `URL`, `EMAIL`, `PLANTILLA`, `MODELO`, `ACTIVO`, `MAX`, `TASA`, `HORAS`, `DIAS`, `SEGUNDOS`, `BUCKET`, `NUMERO`, `MODO`, `VERSION`.
- **DISTINTIVO**: solo si hay más de una del mismo tipo (`PAGOS`, `PRIVADO`, `JUEZ`…).
- **Exentas** (las exige Railway o una librería): `DATABASE_URL`, `PORT`, `RAILWAY_*`.

## Alta de una variable nueva

1. Nombre con la regla de arriba; lectura **solo** con `core_erp.variables.env()` en `core_erp/settings.py`.
2. Una fila en este catálogo (lo exige `core_erp/test_variables.py`) y una línea en `.env.example`.
3. Agregarla a las *Shared Variables* de Railway (ver `docs/railway_programador.md`).

Los nombres anteriores (última columna) **ya no se leen**: el respaldo `legado=` se retiró el 2026-10-24. Se conservan aquí solo como referencia.

## DJANGO — framework

| Variable | Para qué sirve | Emite | Dónde se rota | Servicios de Railway | Nombre anterior |
|---|---|---|---|---|---|
| `DJANGO_SECRET_KEY` | Llave de firma de sesiones y tokens; sin valor por defecto, el servidor no arranca sin ella. | Django (lo define el ERP) | Railway → Variables (se inventa o genera; sin panel externo) | web, Programador | `SECRET_KEY` |
| `DJANGO_DEBUG` | Modo depuración. Siempre False en producción. | Django (lo define el ERP) | Railway → Variables (se inventa o genera; sin panel externo) | web, Programador | `DEBUG` |
| `DJANGO_ALLOWED_HOSTS` | Hosts permitidos, separados por coma. | Django (lo define el ERP) | Railway → Variables (se inventa o genera; sin panel externo) | web | `ALLOWED_HOSTS` |

## META — WhatsApp Cloud API

| Variable | Para qué sirve | Emite | Dónde se rota | Servicios de Railway | Nombre anterior |
|---|---|---|---|---|---|
| `META_WA_KOOXI_TOKEN` | Token permanente de un usuario del sistema para enviar mensajes. | Meta | Meta for Developers / Business Manager (WhatsApp → Configuración de la API; token: Usuarios del sistema) | web, Programador | `WA_CLOUD_API_TOKEN` |
| `META_WA_KOOXI_ID` | ID del número emisor (el de Kooxi). | Meta | Meta for Developers / Business Manager (WhatsApp → Configuración de la API; token: Usuarios del sistema) | web, Programador | `WA_PHONE_NUMBER_ID` |
| `META_WA_KOOXI_NUMERO` | Número público que ve el cliente en los enlaces wa.me (dígitos con código de país). | Meta | Meta for Developers / Business Manager (WhatsApp → Configuración de la API; token: Usuarios del sistema) | web, Programador | `WA_NUMERO_CONTACTO_PUBLICO` |
| `META_WA_APP_SECRET` | App Secret de la app de Meta; firma del webhook (sin él se rechaza todo). | Meta | Meta for Developers / Business Manager (WhatsApp → Configuración de la API; token: Usuarios del sistema) | web | `WA_APP_SECRET` |
| `META_WA_WEBHOOK_TOKEN` | Token de verificación del webhook de WhatsApp. | Meta | Meta for Developers / Business Manager (WhatsApp → Configuración de la API; token: Usuarios del sistema) | web | `WA_WEBHOOK_VERIFY_TOKEN` |
| `META_WA_API_VERSION` | Versión de la Graph API. | Meta | Meta for Developers / Business Manager (WhatsApp → Configuración de la API; token: Usuarios del sistema) | web, Programador | `WA_GRAPH_VERSION` |
| `META_WA_PLANTILLA_IDIOMA` | Idioma de las plantillas aprobadas. | Meta | Meta for Developers / Business Manager (WhatsApp → Configuración de la API; token: Usuarios del sistema) | web, Programador | `WA_TEMPLATE_LANGUAGE` |
| `META_WA_PLANTILLA_COTIZACION` | Plantilla de cotización al cliente. | Meta | Meta for Developers / Business Manager (WhatsApp → Configuración de la API; token: Usuarios del sistema) | web, Programador | `WA_TEMPLATE_COTIZACION` |
| `META_WA_PLANTILLA_PAGO` | Plantilla de confirmación de pago. | Meta | Meta for Developers / Business Manager (WhatsApp → Configuración de la API; token: Usuarios del sistema) | web, Programador | `WA_TEMPLATE_PAGO` |
| `META_WA_PLANTILLA_RECORDATORIO` | Plantilla de recordatorio de pago. | Meta | Meta for Developers / Business Manager (WhatsApp → Configuración de la API; token: Usuarios del sistema) | web, Programador | `WA_TEMPLATE_RECORDATORIO` |
| `META_WA_PLANTILLA_GUIA` | Plantilla de la guía pre-evento. | Meta | Meta for Developers / Business Manager (WhatsApp → Configuración de la API; token: Usuarios del sistema) | web, Programador | `WA_TEMPLATE_GUIA` |
| `META_WA_PLANTILLA_SEGUIMIENTO` | Plantilla de seguimiento a cotizaciones sin pago (vacía = el cron no manda nada). | Meta | Meta for Developers / Business Manager (WhatsApp → Configuración de la API; token: Usuarios del sistema) | web, Programador | `WA_TEMPLATE_SEGUIMIENTO` |
| `META_WA_PLANTILLA_ENCUESTA` | Plantilla de la encuesta de satisfacción (vacía = el comando no manda nada). | Meta | Meta for Developers / Business Manager (WhatsApp → Configuración de la API; token: Usuarios del sistema) | web, Programador | — |
| `META_WA_PLANTILLA_REACTIVACION` | Plantilla de la reactivación anual por aniversario (vacía = no se manda). | Meta | Meta for Developers / Business Manager (WhatsApp → Configuración de la API; token: Usuarios del sistema) | web, Programador | — |
| `META_WA_PLANTILLA_SEGUIMIENTO_DIA1` | Plantilla del seguimiento del día 1 a cotizaciones sin pago (vacía = ese paso no se manda). | Meta | Meta for Developers / Business Manager (WhatsApp → Configuración de la API; token: Usuarios del sistema) | web, Programador | — |
| `META_WA_PLANTILLA_SEGUIMIENTO_FECHA` | Plantilla «tu fecha está por ocuparse» del seguimiento (vacía = ese paso no se manda). | Meta | Meta for Developers / Business Manager (WhatsApp → Configuración de la API; token: Usuarios del sistema) | web, Programador | — |
| `META_WA_PLANTILLA_VISITA` | Plantilla de confirmación y recordatorio de visitas a la Quinta (vacía = texto libre, solo con ventana de 24 h abierta). | Meta | Meta for Developers / Business Manager (WhatsApp → Configuración de la API; token: Usuarios del sistema) | web, Programador | — |
| `META_WA_PLANTILLA_ALERTA_INTERNA` | Plantilla de alerta interna (nueva cotización). | Meta | Meta for Developers / Business Manager (WhatsApp → Configuración de la API; token: Usuarios del sistema) | web, Programador | `WA_TEMPLATE_ALERTA_INTERNA` |
| `META_WA_PLANTILLA_ALERTA_PAGO` | Plantilla de alerta interna de pago acreditado. | Meta | Meta for Developers / Business Manager (WhatsApp → Configuración de la API; token: Usuarios del sistema) | web, Programador | `WA_TEMPLATE_ALERTA_PAGO` |
| `META_WA_PLANTILLA_ALERTA_CONTRACARGO` | Plantilla de alerta interna de contracargo. | Meta | Meta for Developers / Business Manager (WhatsApp → Configuración de la API; token: Usuarios del sistema) | web, Programador | `WA_TEMPLATE_ALERTA_CONTRACARGO` |
| `META_WA_PLANTILLA_SOLICITUD_FACTURA` | Plantilla (documento) de solicitud de factura al contador. | Meta | Meta for Developers / Business Manager (WhatsApp → Configuración de la API; token: Usuarios del sistema) | web, Programador | `WA_TEMPLATE_SOLICITUD_FACTURA` |
| `META_WA_PLANTILLA_OPERACIONES` | Plantilla de avisos internos y de operaciones («Tienes un aviso nuevo: {{1}}»). | Meta | Meta for Developers / Business Manager (WhatsApp → Configuración de la API; token: Usuarios del sistema) | web, Programador | `WA_TEMPLATE_OPERACIONES` |

## ANTHROPIC — modelo de Kooxi

| Variable | Para qué sirve | Emite | Dónde se rota | Servicios de Railway | Nombre anterior |
|---|---|---|---|---|---|
| `ANTHROPIC_API_KEY_KOOXI` | Llave de la API de Anthropic para el agente, el juez y el resumen de preguntas. | Anthropic | console.anthropic.com → API keys | web, Programador | `ANTHROPIC_API_KEY` |

## OPENPAY — cobros

| Variable | Para qué sirve | Emite | Dónde se rota | Servicios de Railway | Nombre anterior |
|---|---|---|---|---|---|
| `OPENPAY_PAGOS_MODO` | Ambiente: sandbox o production (cambia URL de la API y Openpay.js). | Openpay | Panel de Openpay → Configuración → Credenciales / Webhooks | web, Programador | `OPENPAY_MODE` |
| `OPENPAY_PAGOS_ID` | Merchant ID del comercio. | Openpay | Panel de Openpay → Configuración → Credenciales / Webhooks | web, Programador | `OPENPAY_MERCHANT_ID` |
| `OPENPAY_PAGOS_SECRET` | Llave privada de la API (cargos y reembolsos). | Openpay | Panel de Openpay → Configuración → Credenciales / Webhooks | web, Programador | `OPENPAY_PRIVATE_KEY` |
| `OPENPAY_PAGOS_API_KEY_PUBLICA` | Llave pública que usa Openpay.js en el portal. | Openpay | Panel de Openpay → Configuración → Credenciales / Webhooks | web | `OPENPAY_PUBLIC_KEY` |
| `OPENPAY_WEBHOOK_USUARIO` | Usuario de la autenticación básica del webhook. | Openpay | Panel de Openpay → Configuración → Credenciales / Webhooks | web | `OPENPAY_WEBHOOK_USER` |
| `OPENPAY_WEBHOOK_SECRET` | Contraseña de la autenticación básica del webhook. | Openpay | Panel de Openpay → Configuración → Credenciales / Webhooks | web | `OPENPAY_WEBHOOK_PASSWORD` |

## CLOUDFLARE — R2 (archivos)

| Variable | Para qué sirve | Emite | Dónde se rota | Servicios de Railway | Nombre anterior |
|---|---|---|---|---|---|
| `CLOUDFLARE_R2_CUENTA_ID` | ID de la cuenta de Cloudflare (parte del endpoint de R2). | Cloudflare | Cloudflare → R2 → Manage API tokens | web, Programador | `CLOUDFLARE_R2_ACCOUNT_ID` |
| `CLOUDFLARE_R2_PUBLICO_ID` | Access key del bucket público. | Cloudflare | Cloudflare → R2 → Manage API tokens | web, Programador | `CLOUDFLARE_R2_ACCESS_KEY_ID` |
| `CLOUDFLARE_R2_PUBLICO_SECRET` | Secret key del bucket público. | Cloudflare | Cloudflare → R2 → Manage API tokens | web, Programador | `CLOUDFLARE_R2_SECRET_ACCESS_KEY` |
| `CLOUDFLARE_R2_PUBLICO_BUCKET` | Nombre del bucket público. | Cloudflare | Cloudflare → R2 → Manage API tokens | web, Programador | `CLOUDFLARE_R2_BUCKET_NAME` |
| `CLOUDFLARE_R2_PUBLICO_URL` | Dominio público del bucket (media). | Cloudflare | Cloudflare → R2 → Manage API tokens | web, Programador | `CLOUDFLARE_R2_CUSTOM_DOMAIN` |
| `CLOUDFLARE_R2_PRIVADO_BUCKET` | Bucket privado (vacío = esos archivos siguen en el público). Se define al final del corte, ver docs/security. | Cloudflare | Cloudflare → R2 → Manage API tokens | web, Programador | `CLOUDFLARE_R2_PRIVATE_BUCKET_NAME` |
| `CLOUDFLARE_R2_PRIVADO_ID` | Access key del bucket privado (si va vacía usa la del público). | Cloudflare | Cloudflare → R2 → Manage API tokens | web, Programador | `CLOUDFLARE_R2_PRIVATE_ACCESS_KEY_ID` |
| `CLOUDFLARE_R2_PRIVADO_SECRET` | Secret key del bucket privado (si va vacía usa la del público). | Cloudflare | Cloudflare → R2 → Manage API tokens | web, Programador | `CLOUDFLARE_R2_PRIVATE_SECRET_ACCESS_KEY` |
| `CLOUDFLARE_R2_PRIVADO_URL_SEGUNDOS` | Segundos de validez de las URLs firmadas. | Cloudflare | Cloudflare → R2 → Manage API tokens | web, Programador | `CLOUDFLARE_R2_PRIVATE_URL_EXPIRE` |

## BREVO — correo

| Variable | Para qué sirve | Emite | Dónde se rota | Servicios de Railway | Nombre anterior |
|---|---|---|---|---|---|
| `BREVO_EMAIL_API_KEY` | API key de Brevo para enviar correo. | Brevo | Brevo → SMTP y API → API keys | web, Programador | `BREVO_API_KEY` |

## JIBBLE — asistencia y nómina

| Variable | Para qué sirve | Emite | Dónde se rota | Servicios de Railway | Nombre anterior |
|---|---|---|---|---|---|
| `JIBBLE_NOMINA_ID` | Client ID de la API de Jibble. | Jibble | Jibble → Configuración → credenciales de API | web, Programador | `JIBBLE_CLIENT_ID` |
| `JIBBLE_NOMINA_SECRET` | Client secret de la API de Jibble. | Jibble | Jibble → Configuración → credenciales de API | web, Programador | `JIBBLE_CLIENT_SECRET` |

## ERP — correo

| Variable | Para qué sirve | Emite | Dónde se rota | Servicios de Railway | Nombre anterior |
|---|---|---|---|---|---|
| `ERP_EMAIL_REMITENTE` | Remitente por defecto (alimenta DEFAULT_FROM_EMAIL de Django). | ERP (configuración propia) | Railway → Shared Variables (valor propio; sin panel externo) | web, Programador | `DEFAULT_FROM_EMAIL` |
| `ERP_EMAIL_REMITENTE_RESERVAS` | Remitente de cotizaciones, contratos y guías. | ERP (configuración propia) | Railway → Shared Variables (valor propio; sin panel externo) | web, Programador | `EMAIL_FROM_RESERVAS` |
| `ERP_EMAIL_REMITENTE_PAGOS` | Remitente de pagos, recordatorios y facturas. | ERP (configuración propia) | Railway → Shared Variables (valor propio; sin panel externo) | web, Programador | `EMAIL_FROM_PAGOS` |
| `ERP_EMAIL_REMITENTE_NOTIFICACIONES` | Remitente de alertas internas. | ERP (configuración propia) | Railway → Shared Variables (valor propio; sin panel externo) | web, Programador | `EMAIL_FROM_NOTIFICACIONES` |

## ERP — sitio y portal

| Variable | Para qué sirve | Emite | Dónde se rota | Servicios de Railway | Nombre anterior |
|---|---|---|---|---|---|
| `ERP_SITIO_URL` | URL canónica del sitio (enlaces en correos). | ERP (configuración propia) | Railway → Shared Variables (valor propio; sin panel externo) | web, Programador | `SITE_URL` |
| `ERP_PORTAL_URL` | URL base del portal del cliente (enlaces, correos y avisos). | ERP (configuración propia) | Railway → Shared Variables (valor propio; sin panel externo) | web, Programador | `PORTAL_URL` |
| `ERP_PORTAL_ACCESO_MAX` | Intentos fallidos por cotización antes de bloquear el acceso al portal. | ERP (configuración propia) | Railway → Shared Variables (valor propio; sin panel externo) | web | `PORTAL_ACCESO_MAX_INTENTOS` |
| `ERP_PORTAL_ACCESO_VENTANA_SEGUNDOS` | Ventana (segundos) del bloqueo del portal. | ERP (configuración propia) | Railway → Shared Variables (valor propio; sin panel externo) | web | `PORTAL_ACCESO_VENTANA` |

## ERP — seguridad

| Variable | Para qué sirve | Emite | Dónde se rota | Servicios de Railway | Nombre anterior |
|---|---|---|---|---|---|
| `ERP_ADMIN_LOGIN_MAX_IP` | Intentos fallidos por IP en /admin/login/. | ERP (configuración propia) | Railway → Shared Variables (valor propio; sin panel externo) | web | `ADMIN_LOGIN_MAX_INTENTOS_IP` |
| `ERP_ADMIN_LOGIN_MAX_USUARIO` | Intentos fallidos por usuario en /admin/login/. | ERP (configuración propia) | Railway → Shared Variables (valor propio; sin panel externo) | web | `ADMIN_LOGIN_MAX_INTENTOS_USUARIO` |
| `ERP_ADMIN_LOGIN_VENTANA_SEGUNDOS` | Ventana (segundos) del bloqueo del login. | ERP (configuración propia) | Railway → Shared Variables (valor propio; sin panel externo) | web | `ADMIN_LOGIN_VENTANA` |
| `ERP_SESION_INACTIVIDAD_SEGUNDOS` | Segundos de inactividad antes de cerrar la sesión. | ERP (configuración propia) | Railway → Shared Variables (valor propio; sin panel externo) | web | `SESSION_IDLE_TIMEOUT` |
| `ERP_RATELIMIT_PROXIES` | Proxies de confianza delante de la app (el edge de Railway = 1). | ERP (configuración propia) | Railway → Shared Variables (valor propio; sin panel externo) | web | `RATELIMIT_TRUSTED_PROXY_COUNT` |
| `ERP_CSP_PUBLICO_ACTIVO` | Enciende la CSP de las páginas públicas. | ERP (configuración propia) | Railway → Shared Variables (valor propio; sin panel externo) | web | `PUBLIC_CSP_ENABLED` |
| `ERP_CSP_PUBLICO_SOLO_REPORTE` | CSP pública en modo prueba (solo reporta). | ERP (configuración propia) | Railway → Shared Variables (valor propio; sin panel externo) | web | `PUBLIC_CSP_REPORT_ONLY` |
| `ERP_CSP_PORTAL_SOLO_REPORTE` | CSP del portal en modo prueba. | ERP (configuración propia) | Railway → Shared Variables (valor propio; sin panel externo) | web | `PORTAL_CSP_REPORT_ONLY` |
| `ERP_CSP_ADMIN_SOLO_REPORTE` | CSP del admin en modo prueba. | ERP (configuración propia) | Railway → Shared Variables (valor propio; sin panel externo) | web | `ADMIN_CSP_REPORT_ONLY` |
| `ERP_NOMINA_WEBHOOK_TOKEN` | Token que autoriza el webhook de sincronización de Jibble. | ERP (configuración propia) | Railway → Shared Variables (valor propio; sin panel externo) | web | `NOMINA_CRON_TOKEN` |
| `ERP_SAT_FIEL_CIFRADO_SECRET` | Llave Fernet que cifra la e.firma (la genera el ERP, no el SAT). Generar una y no perderla. | ERP (configuración propia) | Railway → Shared Variables (valor propio; sin panel externo) | web, Programador | `SAT_FIEL_ENCRYPTION_KEY` |

## ERP — negocio

| Variable | Para qué sirve | Emite | Dónde se rota | Servicios de Railway | Nombre anterior |
|---|---|---|---|---|---|
| `ERP_FISCAL_TASA_ISH` | Tasa del Impuesto Sobre Hospedaje como proporción (0.05 = 5%). Debe coincidir en web y Programador. | ERP (configuración propia) | Railway → Shared Variables (valor propio; sin panel externo) | web, Programador | `TASA_ISH` |
| `ERP_PROFECO_REGISTRO_EVENTOS` | Número de registro PROFECO del contrato de Evento/Pasadía (vacío = sin leyenda). | ERP (configuración propia) | Railway → Shared Variables (valor propio; sin panel externo) | web, Programador | `PROFECO_REGISTRO_EVENTOS` |
| `ERP_PROFECO_REGISTRO_HOSPEDAJE` | Registro PROFECO del contrato de Hospedaje (vacío = sin leyenda). | ERP (configuración propia) | Railway → Shared Variables (valor propio; sin panel externo) | web, Programador | `PROFECO_REGISTRO_HOSPEDAJE` |
| `ERP_CONTRATO_AUTOMATICO_ACTIVO` | Emite el contrato al confirmarse la cotización. | ERP (configuración propia) | Railway → Shared Variables (valor propio; sin panel externo) | web, Programador | `CONTRATO_AUTOMATICO` |
| `ERP_CONTRATO_PROPIO_ACTIVO` | Usa los contratos propios (False = modelo PROFECO). | ERP (configuración propia) | Railway → Shared Variables (valor propio; sin panel externo) | web, Programador | `CONTRATO_PROPIO_ACTIVO` |
| `ERP_WA_NEGOCIO_NUMERO` | Respaldo transitorio del directorio de responsables (#421); se borra cuando el directorio sea la única fuente. | ERP (configuración propia) | Railway → Shared Variables (valor propio; sin panel externo) | web, Programador | `WA_NUMERO_NEGOCIO` |

## ERP — Kooxi (agente de WhatsApp)

| Variable | Para qué sirve | Emite | Dónde se rota | Servicios de Railway | Nombre anterior |
|---|---|---|---|---|---|
| `ERP_KOOXI_ACTIVO` | Enciende al agente (apagado = no contesta). | ERP (configuración propia) | Railway → Shared Variables (valor propio; sin panel externo) | web | `WA_AGENTE_ACTIVO` |
| `ERP_KOOXI_NUMEROS_PRUEBA` | Si no está vacía, solo contesta a estos números (coma). | ERP (configuración propia) | Railway → Shared Variables (valor propio; sin panel externo) | web | `WA_AGENTE_NUMEROS_PRUEBA` |
| `ERP_KOOXI_MODELO` | Modelo del agente. | ERP (configuración propia) | Railway → Shared Variables (valor propio; sin panel externo) | web, Programador | `WA_AGENTE_MODELO` |
| `ERP_KOOXI_ESFUERZO` | Esfuerzo de razonamiento del modelo. | ERP (configuración propia) | Railway → Shared Variables (valor propio; sin panel externo) | web | `WA_AGENTE_ESFUERZO` |
| `ERP_KOOXI_JUEZ_ACTIVO` | Enciende al juez que revisa cada respuesta. | ERP (configuración propia) | Railway → Shared Variables (valor propio; sin panel externo) | web | `WA_AGENTE_JUEZ_ACTIVO` |
| `ERP_KOOXI_MODELO_JUEZ` | Modelo del juez y del resumen semanal de preguntas. | ERP (configuración propia) | Railway → Shared Variables (valor propio; sin panel externo) | web, Programador | `WA_AGENTE_MODELO_JUEZ` |
| `ERP_KOOXI_PAUSA_HUMANO_HORAS` | Horas de pausa si una persona contesta desde la app. | ERP (configuración propia) | Railway → Shared Variables (valor propio; sin panel externo) | web | `WA_AGENTE_PAUSA_HUMANO_HORAS` |
| `ERP_KOOXI_DINERO_MAX` | Tope en pesos de las acciones de dinero que Dirección ordena por WhatsApp; arriba, se hace en el admin. | ERP (configuración propia) | Railway → Shared Variables (valor propio; sin panel externo) | web | — |
| `ERP_KOOXI_MAX_RESPUESTAS_NUMERO_DIA` | Tope de respuestas del modelo por número al día. | ERP (configuración propia) | Railway → Shared Variables (valor propio; sin panel externo) | web | `WA_AGENTE_MAX_RESPUESTAS_NUMERO_DIA` |
| `ERP_KOOXI_MAX_RESPUESTAS_DIA` | Tope global de respuestas del modelo al día. | ERP (configuración propia) | Railway → Shared Variables (valor propio; sin panel externo) | web | `WA_AGENTE_MAX_RESPUESTAS_DIA` |
| `ERP_KOOXI_MAX_HISTORIAL_CARACTERES` | Historial máximo que se reenvía al modelo (caracteres). | ERP (configuración propia) | Railway → Shared Variables (valor propio; sin panel externo) | web | `WA_AGENTE_MAX_HISTORIAL_CARACTERES` |
| `ERP_KOOXI_SEGUIMIENTO_DIAS` | Días tras crearse una cotización sin pago para el seguimiento. | ERP (configuración propia) | Railway → Shared Variables (valor propio; sin panel externo) | web, Programador | `WA_SEGUIMIENTO_DIAS` |
| `ERP_RESENA_GOOGLE_URL` | Enlace de reseña en Google para quien califica 4–5 en la encuesta. | ERP (configuración propia) | Railway → Shared Variables (valor propio; sin panel externo) | web, Programador | — |
| `ERP_DEPOSITO_INSPECCION_HORAS` | Horas sin reporte de daños antes de devolver solo el depósito en garantía. | ERP (configuración propia) | Railway → Shared Variables (valor propio; sin panel externo) | web, Programador | — |
| `ERP_DEPOSITO_TOPE_RETENCION` | Retención máxima (MXN) que el encargado puede aplicar sin el botón de Dirección; 0 = toda retención la aprueba Dirección. | ERP (configuración propia) | Railway → Shared Variables (valor propio; sin panel externo) | web, Programador | — |

## Exentas

| Variable | Nota |
|---|---|
| `DATABASE_URL` | La define Railway (Postgres); sin ella el ERP usa SQLite local. |
| `PORT` | La define Railway; gunicorn escucha en él. |
| `RAILWAY_GIT_COMMIT_SHA` | La define Railway; versiona los estáticos del admin. |

## Se eliminan

- `CLOUDINARY_CLOUD_NAME`, `CLOUDINARY_API_KEY`, `CLOUDINARY_API_SECRET`: `settings.py` ya arranca sin ellas (el dict `CLOUDINARY_STORAGE` quedó con valores vacíos). Se pueden borrar de Railway.
- `WA_NUMERO_NEGOCIO` (ahora `ERP_WA_NEGOCIO_NUMERO`): sigue siendo el respaldo del directorio de responsables (#421). Se borra cuando el directorio sea la única fuente.
- Cualquier variable de Railway que no aparezca en este catálogo ya no la lee el código.
