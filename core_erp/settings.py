import os
from decimal import Decimal
from pathlib import Path

import dj_database_url
from decouple import Csv

from core_erp.variables import env

BASE_DIR = Path(__file__).resolve().parent.parent

# --- SEGURIDAD ---
SECRET_KEY = env('DJANGO_SECRET_KEY', legado='SECRET_KEY')  # SIN default — fuerza a que exista en .env
DEBUG = env('DJANGO_DEBUG', legado='DEBUG', default=False, cast=bool)
ALLOWED_HOSTS = env('DJANGO_ALLOWED_HOSTS', legado='ALLOWED_HOSTS', default='localhost,127.0.0.1', cast=Csv())

# Nº de proxies de confianza delante de la app (el edge de Railway = 1). Se usa
# para leer la IP real del cliente en el rate limiting sin que sea spoofeable
# vía X-Forwarded-For. Ajustar solo si se añaden más proxies (CDN, etc.).
ERP_RATELIMIT_PROXIES = env('ERP_RATELIMIT_PROXIES', legado='RATELIMIT_TRUSTED_PROXY_COUNT', default=1, cast=int)

CSRF_TRUSTED_ORIGINS = [
    'https://erp-qkt.up.railway.app',
    'https://*.railway.app',
    'https://quintakooxtanil.com',
    'https://erp.quintakooxtanil.com',
]

# URL base del portal del cliente (usada en links, emails y notificaciones)
ERP_PORTAL_URL = env('ERP_PORTAL_URL', legado='PORTAL_URL', default='https://erp.quintakooxtanil.com')

# --- URL canónica del sitio (para links en emails, portales, etc.) ---
ERP_SITIO_URL = env('ERP_SITIO_URL', legado='SITE_URL', default='https://erp-qkt.up.railway.app')

# --- Referrer-Policy explícita (SEC-CFG-003) ---
# El token del portal del cliente va en la URL (/mi-evento/<token>/); esta
# política evita que el path (con el token) viaje en la cabecera Referer
# hacia un sitio de otro origen si alguien hace clic en un link externo
# desde ahí — solo se manda el origen (esquema+host), nunca el path, y nada
# en absoluto si se baja de HTTPS a HTTP. No depende de DEBUG: es una
# cabecera de respuesta sin efecto funcional, útil tenerla igual en local
# para poder probarla.
SECURE_REFERRER_POLICY = 'strict-origin-when-cross-origin'

# --- Seguridad en producción (se activan cuando DEBUG=False) ---
if not DEBUG:
    # Railway termina TLS en su borde y reenvía al contenedor por HTTP plano
    # marcando X-Forwarded-Proto: https. Sin esto, request.is_secure() es
    # siempre False detrás del proxy y SECURE_SSL_REDIRECT entra en un loop
    # infinito de redirecciones (confirmado en producción: ERR_TOO_MANY_REDIRECTS
    # en cuanto DEBUG pasó a False y esta rama empezó a ejecutarse de verdad).
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    SECURE_SSL_REDIRECT = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_BROWSER_XSS_FILTER = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    X_FRAME_OPTIONS = 'DENY'

# --- Expiración de sesión por INACTIVIDAD (admin/ERP) ---
# Estándar de industria (OWASP) para apps de negocio: 15–30 min de inactividad.
# Con SESSION_SAVE_EVERY_REQUEST=True el "reloj" se reinicia en cada petición,
# así la sesión caduca solo tras ERP_SESION_INACTIVIDAD_SEGUNDOS segundos SIN actividad
# (idle timeout), no de forma absoluta. Ajustable por variable de entorno; para
# datos financieros se puede endurecer a 900 (15 min).
ERP_SESION_INACTIVIDAD_SEGUNDOS = env('ERP_SESION_INACTIVIDAD_SEGUNDOS', legado='SESSION_IDLE_TIMEOUT', default=1800, cast=int)  # 30 min
SESSION_COOKIE_AGE = ERP_SESION_INACTIVIDAD_SEGUNDOS
SESSION_SAVE_EVERY_REQUEST = True
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
CSRF_COOKIE_SAMESITE = 'Lax'

INSTALLED_APPS = [
    'jazzmin',
    'django.contrib.admin',
    'core_erp.apps.QktAuthConfig',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.humanize',
    'comercial',
    'nomina',
    'facturacion',
    'weasyprint',
    'anymail',
    'contabilidad',
    'reportes',
    'indicadores',
    'comunicacion',
    'legal',
    'operaciones',
    'cumplimiento',
    'django_otp',
    'django_otp.plugins.otp_totp',
]

# --- TOTP obligatorio para superusuarios (SEC-AUTHN-002) ---
OTP_TOTP_ISSUER = 'ERP QKT'

# --- Cabeceras de seguridad por ruta. Ver core_erp/middleware.py ---
ERP_CSP_PUBLICO_ACTIVO = env('ERP_CSP_PUBLICO_ACTIVO', legado='PUBLIC_CSP_ENABLED', default=True, cast=bool)
ERP_CSP_PUBLICO_SOLO_REPORTE = env('ERP_CSP_PUBLICO_SOLO_REPORTE', legado='PUBLIC_CSP_REPORT_ONLY', default=False, cast=bool)
# CSP Report-Only del portal de pago (no bloquea): activar solo para probar qué
# recursos usa Openpay/3-D Secure antes de plantear una CSP bloqueante ahí.
ERP_CSP_PORTAL_SOLO_REPORTE = env('ERP_CSP_PORTAL_SOLO_REPORTE', legado='PORTAL_CSP_REPORT_ONLY', default=False, cast=bool)
# CSP Report-Only del admin (no bloquea): activar solo para probar qué
# recursos usa Jazzmin/FullCalendar/Chart.js antes de plantear una CSP
# bloqueante ahí (orden 37, SEC-CFG-002).
ERP_CSP_ADMIN_SOLO_REPORTE = env('ERP_CSP_ADMIN_SOLO_REPORTE', legado='ADMIN_CSP_REPORT_ONLY', default=False, cast=bool)

MIDDLEWARE = [
    # Primero de todos: cubre el log de cualquier middleware/vista/señal
    # posterior con el mismo correlation ID, incluido el de SecurityMiddleware.
    'core_erp.middleware.CorrelationIdMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'core_erp.middleware.PublicSecurityHeadersMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django_otp.middleware.OTPMiddleware',
    'core_erp.middleware.SuperuserTOTPGateMiddleware',
    'core_erp.middleware.AuthorizationAuditMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'core_erp.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [os.path.join(BASE_DIR, 'templates')],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'core_erp.context_processors.session_idle',
                'core_erp.context_processors.version_estaticos',
                'core_erp.context_processors.salud_programador',
            ],
        },
    },
]

WSGI_APPLICATION = 'core_erp.wsgi.application'

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    # SEC-LOG-002: correlation ID por request, para poder agrupar todas las
    # líneas de log de una misma petición (con 2 workers de gunicorn
    # intercalando salida de procesos distintos, sin esto es imposible
    # separar qué línea es de qué request).
    'filters': {
        'correlation_id': {'()': 'core_erp.middleware.CorrelationIdFilter'},
    },
    'formatters': {
        'con_correlation_id': {
            'format': '[%(correlation_id)s] %(levelname)s %(name)s: %(message)s',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'filters': ['correlation_id'],
            'formatter': 'con_correlation_id',
        },
    },
    'loggers': {
        # propagate=False: sin esto, cualquier mensaje que llegue hasta acá
        # (todo lo que cuelga de 'django.*', incluido 'django.server') sigue
        # subiendo al logger raíz. Algunas librerías (dj_database_url) llaman
        # al logging.warning() de nivel de módulo, lo que dispara el
        # basicConfig() implícito de Python y deja un StreamHandler suelto en
        # el logger raíz, sin nuestro formatter — cada línea saldría
        # duplicada, una con correlation_id y otra sin él.
        'django': {'handlers': ['console'], 'level': 'INFO', 'propagate': False},
        'weasyprint': {'handlers': ['console'], 'level': 'WARNING'},
        # Los logger.info del webhook Openpay (ej. el código de verificación al
        # registrar el webhook) deben verse en los Deploy Logs de Railway.
        'comercial.views_openpay': {'handlers': ['console'], 'level': 'INFO'},
        # Motivos explícitos de rechazo de cargos (código de error, description
        # cruda de Openpay, request_id). Requisito de la certificación: el
        # cliente ve un error genérico, pero el log debe traer la causa real.
        'comercial.services_openpay': {'handlers': ['console'], 'level': 'INFO'},
        # Eventos de seguridad con identidad propia (403 de autorización vía
        # AuthorizationAuditMiddleware, y los sub-loggers que Django ya trae
        # de fábrica: django.security.DisallowedHost, django.security.csrf,
        # etc.). propagate=False evita que además se dupliquen en el logger
        # 'django' de arriba, que ya los recibiría por herencia.
        'django.security': {'handlers': ['console'], 'level': 'INFO', 'propagate': False},
    },
}

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}
db_from_env = dj_database_url.config(conn_max_age=500)
DATABASES['default'].update(db_from_env)

# --- CACHE COMPARTIDO ENTRE WORKERS ---
# El default de Django (LocMemCache) vive dentro de cada proceso y gunicorn
# arranca con --workers 2: los contadores del rate limiting y el candado
# anti-doble-cobro de Openpay quedarían duplicados e inservibles. Se usa la
# base de datos, que ya es compartida y no exige provisionar nada nuevo en
# Railway; la tabla la crea una migración, no hace falta tocar el Dockerfile.
# Si algún día se provisiona Redis (réplicas, más carga), hay que añadir
# `redis` a requirements.txt y reintroducir aquí un backend condicional —
# mientras no esté provisionado, no se declara esa rama: definir una
# REDIS_URL sin la dependencia instalada tumbaría el arranque.
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.db.DatabaseCache',
        'LOCATION': 'qkt_cache',
        'TIMEOUT': 3600,
        # MAX_ENTRIES por defecto son 300: con el HTML de los documentos
        # legales más los buckets del rate limiting, el cull podría borrar
        # contadores vivos y regalar intentos al atacante.
        'OPTIONS': {'MAX_ENTRIES': 10000, 'CULL_FREQUENCY': 4},
    }
}

# Número de registro ante PROFECO del contrato de adhesión de Hospedaje. El
# registro 9341-2023 solo ampara Evento y Pasadía: mientras esta variable
# esté vacía, el contrato de Hospedaje sale sin leyenda de registro.
ERP_PROFECO_REGISTRO_HOSPEDAJE = env('ERP_PROFECO_REGISTRO_HOSPEDAJE', legado='PROFECO_REGISTRO_HOSPEDAJE', default='')

# Contratos propios (marco + anexo por servicio, Issue #318). Encendido desde
# la validación legal del texto (2026-09-25); False vuelve a los contratos
# basados en el modelo PROFECO sin desplegar.
ERP_CONTRATO_PROPIO_ACTIVO = env('ERP_CONTRATO_PROPIO_ACTIVO', legado='CONTRATO_PROPIO_ACTIVO', default=True, cast=bool)
# Emite el contrato solo al confirmarse la cotización (comercial/signals.py).
# False vuelve al botón «Generar contrato» del admin sin desplegar.
ERP_CONTRATO_AUTOMATICO_ACTIVO = env('ERP_CONTRATO_AUTOMATICO_ACTIVO', legado='CONTRATO_AUTOMATICO', default=True, cast=bool)

# Registro PROFECO del contrato propio de Evento/Pasadía. La NOM-174-SCFI-2007
# (numeral 5.1) obliga a registrarlo; el propietario decidió emitirlo mientras
# el registro está en trámite. Vacío = el contrato sale sin leyenda.
ERP_PROFECO_REGISTRO_EVENTOS = env('ERP_PROFECO_REGISTRO_EVENTOS', legado='PROFECO_REGISTRO_EVENTOS', default='')

# --- ISH: Impuesto Sobre Hospedaje (estatal, Yucatán) ---
# Proporción, no porcentaje: 0.05 = 5%. El default de 0 mantiene el ERP
# exactamente como estaba (no calcula ni exhibe ISH); definir la variable en
# Railway es lo único que hace falta para activarlo, sin desplegar.
#
# La tasa la confirma el contador: es ley estatal, no federal, y cambia sin
# que este repo se entere. Aplica al hospedaje vendido directo (Ka'an y
# Otoch). Ver core_erp/impuestos.py.
ERP_FISCAL_TASA_ISH = Decimal(env('ERP_FISCAL_TASA_ISH', legado='TASA_ISH', default='0'))

# Llave Fernet con la que se cifra la e.firma (.key y contraseña) para la descarga
# masiva del SAT. Sin ella el módulo no guarda ni usa ninguna e.firma.
ERP_SAT_FIEL_CIFRADO_SECRET = env('ERP_SAT_FIEL_CIFRADO_SECRET', legado='SAT_FIEL_ENCRYPTION_KEY', default='')

# --- BLOQUEO DE FUERZA BRUTA EN /admin/login/ ---
ERP_ADMIN_LOGIN_VENTANA_SEGUNDOS = env('ERP_ADMIN_LOGIN_VENTANA_SEGUNDOS', legado='ADMIN_LOGIN_VENTANA', default=900, cast=int)
ERP_ADMIN_LOGIN_MAX_IP = env('ERP_ADMIN_LOGIN_MAX_IP', legado='ADMIN_LOGIN_MAX_INTENTOS_IP', default=10, cast=int)
ERP_ADMIN_LOGIN_MAX_USUARIO = env('ERP_ADMIN_LOGIN_MAX_USUARIO', legado='ADMIN_LOGIN_MAX_INTENTOS_USUARIO', default=20, cast=int)

# Bloqueo por cotización en el acceso al portal del cliente (SEC-AUTHN-001b):
# complementa el rate_limit por IP de portal_acceso, que un atacante distribuido
# elude cambiando de IP en cada intento contra los mismos 4 dígitos.
ERP_PORTAL_ACCESO_VENTANA_SEGUNDOS = env('ERP_PORTAL_ACCESO_VENTANA_SEGUNDOS', legado='PORTAL_ACCESO_VENTANA', default=900, cast=int)
ERP_PORTAL_ACCESO_MAX = env('ERP_PORTAL_ACCESO_MAX', legado='PORTAL_ACCESO_MAX_INTENTOS', default=10, cast=int)

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'es-mx'
TIME_ZONE = 'America/Merida'
USE_I18N = True
USE_TZ = True
USE_L10N = False
USE_THOUSAND_SEPARATOR = True
DECIMAL_SEPARATOR = '.'
THOUSAND_SEPARATOR = ','

# --- RUTA ESTÁTICA ---
STATIC_URL = '/static/'
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')
# Versión de los estáticos propios del admin (qkt_ui.css/js) para la URL:
# CompressedStaticFilesStorage no pone hash en el nombre, así que sin esto el
# navegador o Cloudflare siguen sirviendo la hoja anterior tras un deploy.
# Railway expone el commit desplegado; en local queda vacío.
STATIC_VERSION = env('RAILWAY_GIT_COMMIT_SHA', default='')[:12]
STATICFILES_DIRS = [os.path.join(BASE_DIR, 'static')]

# --- CORREO ---
EMAIL_BACKEND = "anymail.backends.brevo.EmailBackend"
ANYMAIL = {"BREVO_API_KEY": env('BREVO_EMAIL_API_KEY', legado='BREVO_API_KEY', default='')}
DEFAULT_FROM_EMAIL = env('ERP_EMAIL_REMITENTE', legado='DEFAULT_FROM_EMAIL', default='quintakooxtanil@gmail.com')
SERVER_EMAIL = env('ERP_EMAIL_REMITENTE', legado='DEFAULT_FROM_EMAIL', default='quintakooxtanil@gmail.com')
# Remitentes por tipo de correo (Issue #221). Cada uno cae a DEFAULT_FROM_EMAIL
# si no está configurado, para que el deploy no rompa nada si llega antes de
# que el dominio quede verificado en Brevo.
ERP_EMAIL_REMITENTE_RESERVAS = env('ERP_EMAIL_REMITENTE_RESERVAS', legado='EMAIL_FROM_RESERVAS', default=DEFAULT_FROM_EMAIL)
ERP_EMAIL_REMITENTE_PAGOS = env('ERP_EMAIL_REMITENTE_PAGOS', legado='EMAIL_FROM_PAGOS', default=DEFAULT_FROM_EMAIL)
ERP_EMAIL_REMITENTE_NOTIFICACIONES = env('ERP_EMAIL_REMITENTE_NOTIFICACIONES', legado='EMAIL_FROM_NOTIFICACIONES', default=DEFAULT_FROM_EMAIL)

# --- WHATSAPP CLOUD API (Meta) ---
# Credenciales del número emisor. Se leen aquí (y no con env() disperso por
# el código) para que exista una sola fuente y los tests puedan sobreescribirlas.
META_WA_KOOXI_ID = env('META_WA_KOOXI_ID', legado='WA_PHONE_NUMBER_ID', default='')
META_WA_KOOXI_TOKEN = env('META_WA_KOOXI_TOKEN', legado='WA_CLOUD_API_TOKEN', default='')

# Dos números con roles distintos, y ninguno con fallback en código:
#   ERP_WA_NEGOCIO_NUMERO     → respaldo de las alertas internas mientras el directorio de
#                               Responsables (Admin → Comunicación) no tenga ENCARGADO/DIRECCION.
#                               Debe ser un teléfono que lea una persona y NO puede
#                               coincidir con el número emisor (Meta rechaza con 131021).
#   META_WA_KOOXI_NUMERO      → el que se le muestra al cliente en los enlaces wa.me
#                               del portal. Si va vacío, la plantilla oculta el enlace.
ERP_WA_NEGOCIO_NUMERO = env('ERP_WA_NEGOCIO_NUMERO', legado='WA_NUMERO_NEGOCIO', default='')
META_WA_KOOXI_NUMERO = env('META_WA_KOOXI_NUMERO', legado='WA_NUMERO_CONTACTO_PUBLICO', default='')
META_WA_API_VERSION = env('META_WA_API_VERSION', legado='WA_GRAPH_VERSION', default='v20.0')

# Plantillas aprobadas en Meta. Los mensajes iniciados por el negocio (pago y
# recordatorio) solo llegan por plantilla: fuera de la ventana de 24 h Meta
# rechaza el texto libre con el error 131047. Si una plantilla no está
# configurada, el envío queda FALLIDO y auditado, sin romper la operación.
META_WA_PLANTILLA_IDIOMA = env('META_WA_PLANTILLA_IDIOMA', legado='WA_TEMPLATE_LANGUAGE', default='es_MX')
META_WA_PLANTILLA_COTIZACION = env('META_WA_PLANTILLA_COTIZACION', legado='WA_TEMPLATE_COTIZACION', default='')
META_WA_PLANTILLA_PAGO = env('META_WA_PLANTILLA_PAGO', legado='WA_TEMPLATE_PAGO', default='')
META_WA_PLANTILLA_RECORDATORIO = env('META_WA_PLANTILLA_RECORDATORIO', legado='WA_TEMPLATE_RECORDATORIO', default='')
# Guía pre-evento (Issue #234): enlace de descarga, nunca el PDF adjunto —
# adjuntarlo requeriría una plantilla tipo "documento", más lenta de aprobar.
META_WA_PLANTILLA_GUIA = env('META_WA_PLANTILLA_GUIA', legado='WA_TEMPLATE_GUIA', default='')
# Seguimiento de cotizaciones sin pago (Issue #366): una sola vez, a los
# ERP_KOOXI_SEGUIMIENTO_DIAS de creada y solo a quien aceptó la finalidad MARKETING.
# Sin plantilla, el cron no manda nada.
META_WA_PLANTILLA_SEGUIMIENTO = env('META_WA_PLANTILLA_SEGUIMIENTO', legado='WA_TEMPLATE_SEGUIMIENTO', default='')
# Fase 2a: el seguimiento pasa a tres pasos. La de arriba es el paso del día 3;
# estas son el del día 1 y «tu fecha está por ocuparse». Sin plantilla, ese paso no se manda.
META_WA_PLANTILLA_SEGUIMIENTO_DIA1 = env('META_WA_PLANTILLA_SEGUIMIENTO_DIA1', default='')
META_WA_PLANTILLA_SEGUIMIENTO_FECHA = env('META_WA_PLANTILLA_SEGUIMIENTO_FECHA', default='')
# Visitas a la Quinta (Fase 2a): confirmación y recordatorio al cliente fuera de la
# ventana de 24 h. Sin plantilla se intenta texto libre, que solo llega con la ventana abierta.
META_WA_PLANTILLA_VISITA = env('META_WA_PLANTILLA_VISITA', default='')
ERP_KOOXI_SEGUIMIENTO_DIAS = env('ERP_KOOXI_SEGUIMIENTO_DIAS', legado='WA_SEGUIMIENTO_DIAS', default=3, cast=int)
# Post-venta (Fase 3, Issue #434). Sin plantilla, el comando correspondiente no manda nada.
# Encuesta: 1 día después del servicio. Reactivación: aniversario del evento (MARKETING).
META_WA_PLANTILLA_ENCUESTA = env('META_WA_PLANTILLA_ENCUESTA', default='')
META_WA_PLANTILLA_REACTIVACION = env('META_WA_PLANTILLA_REACTIVACION', default='')
# Enlace de reseña en Google que se manda a quien califica 4–5; vacío = solo se agradece.
ERP_RESENA_GOOGLE_URL = env('ERP_RESENA_GOOGLE_URL', default='')
# Depósito en garantía: horas sin reporte de daños tras pedir la inspección antes de
# devolverlo solo, y retención máxima (MXN) que el encargado puede aplicar sin el botón
# de Dirección (0 = toda retención requiere a Dirección).
ERP_DEPOSITO_INSPECCION_HORAS = env('ERP_DEPOSITO_INSPECCION_HORAS', default=48, cast=int)
ERP_DEPOSITO_TOPE_RETENCION = env('ERP_DEPOSITO_TOPE_RETENCION', default='0')
# Opcional: la alerta interna va como texto libre si esta queda vacía, lo que
# solo funciona mientras la ventana de 24 h con ese número esté abierta.
META_WA_PLANTILLA_ALERTA_INTERNA = env('META_WA_PLANTILLA_ALERTA_INTERNA', legado='WA_TEMPLATE_ALERTA_INTERNA', default='')
# Alerta interna de pago acreditado (distinta de la de arriba: otro contenido,
# otros parámetros — reusar la misma plantilla mandaría el texto equivocado a
# un número real). Igual de opcional, mismo fallback a texto libre.
META_WA_PLANTILLA_ALERTA_PAGO = env('META_WA_PLANTILLA_ALERTA_PAGO', legado='WA_TEMPLATE_ALERTA_PAGO', default='')
# Alerta interna de contracargo (Issue #303) — urgente, lleva el plazo límite
# de 3 días hábiles para mandar evidencia a Openpay. Plantilla propia, mismo
# fallback a texto libre que las anteriores.
META_WA_PLANTILLA_ALERTA_CONTRACARGO = env('META_WA_PLANTILLA_ALERTA_CONTRACARGO', legado='WA_TEMPLATE_ALERTA_CONTRACARGO', default='')
# Solicitud de factura al contador (facturacion/services.py): a diferencia de
# la guía, aquí SÍ se somete una plantilla tipo "documento" — el pedido
# original es mandar el PDF adjunto, no un enlace. Ver
# docs/whatsapp_plantilla_solicitud_factura.md para el texto exacto a
# someter en Meta Business Manager. Vacía = sigue mandando el PDF como
# mensaje 'document' directo (funciona dentro de la ventana de 24h, falla
# fuera de ella — comportamiento sin cambios hasta que se apruebe).
META_WA_PLANTILLA_SOLICITUD_FACTURA = env('META_WA_PLANTILLA_SOLICITUD_FACTURA', legado='WA_TEMPLATE_SOLICITUD_FACTURA', default='')
# Módulo de operaciones (Issue #257): abre la conversación con el colaborador
# antes del checklist/aviso de horario en texto libre (que sí puede llevar
# saltos de línea, a diferencia de un parámetro de plantilla). Vacía = se
# manda solo el texto libre, funciona mientras la ventana de 24h esté
# abierta — mismo criterio que el resto de mensajería interna sin plantilla.
META_WA_PLANTILLA_OPERACIONES = env('META_WA_PLANTILLA_OPERACIONES', legado='WA_TEMPLATE_OPERACIONES', default='')

# --- AGENTE DE WHATSAPP CON IA (Issue #346) ---
# Webhook entrante: el token lo inventa quien da de alta el webhook en Meta;
# el App Secret firma cada entrega (X-Hub-Signature-256). Sin él, toda
# entrega se rechaza (fail-closed), igual que el feed iCal.
META_WA_WEBHOOK_TOKEN = env('META_WA_WEBHOOK_TOKEN', legado='WA_WEBHOOK_VERIFY_TOKEN', default='')
META_WA_APP_SECRET = env('META_WA_APP_SECRET', legado='WA_APP_SECRET', default='')
# Apagado por default: el deploy solo registra los mensajes entrantes. Para
# probar, definir ERP_KOOXI_NUMEROS_PRUEBA (lista separada por comas) y
# encenderlo; con la lista vacía contesta a todos.
ERP_KOOXI_ACTIVO = env('ERP_KOOXI_ACTIVO', legado='WA_AGENTE_ACTIVO', default=False, cast=bool)
ERP_KOOXI_NUMEROS_PRUEBA = env('ERP_KOOXI_NUMEROS_PRUEBA', legado='WA_AGENTE_NUMEROS_PRUEBA', default='', cast=Csv())
ERP_KOOXI_MODELO = env('ERP_KOOXI_MODELO', legado='WA_AGENTE_MODELO', default='claude-opus-5-5')
# Profundidad de razonamiento del agente (low/medium/high). Más alto contesta
# mejor las preguntas con varias condiciones y cuesta más por respuesta.
ERP_KOOXI_ESFUERZO = env('ERP_KOOXI_ESFUERZO', legado='WA_AGENTE_ESFUERZO', default='medium')
# Juez (Issue #366): un modelo pequeño revisa cada respuesta antes de enviarla.
# El filtro determinista de `services_guardia` corre siempre, con o sin juez.
ERP_KOOXI_JUEZ_ACTIVO = env('ERP_KOOXI_JUEZ_ACTIVO', legado='WA_AGENTE_JUEZ_ACTIVO', default=True, cast=bool)
ERP_KOOXI_MODELO_JUEZ = env('ERP_KOOXI_MODELO_JUEZ', legado='WA_AGENTE_MODELO_JUEZ', default='claude-haiku-4-5')
# Cuánto se calla el agente en una conversación después de que el propietario
# contesta desde la app de WhatsApp Business (coexistencia).
ERP_KOOXI_PAUSA_HUMANO_HORAS = env('ERP_KOOXI_PAUSA_HUMANO_HORAS', legado='WA_AGENTE_PAUSA_HUMANO_HORAS', default=12, cast=int)
# Topes contra abuso y gasto (respuestas del modelo por día, hora de Yucatán).
# Al pasar el de un número, esa conversación pasa a una persona; al pasar el
# global, el agente deja de usar IA hasta el día siguiente y avisa al equipo.
ERP_KOOXI_MAX_RESPUESTAS_NUMERO_DIA = env('ERP_KOOXI_MAX_RESPUESTAS_NUMERO_DIA', legado='WA_AGENTE_MAX_RESPUESTAS_NUMERO_DIA', default=30, cast=int)
ERP_KOOXI_MAX_RESPUESTAS_DIA = env('ERP_KOOXI_MAX_RESPUESTAS_DIA', legado='WA_AGENTE_MAX_RESPUESTAS_DIA', default=300, cast=int)
# Tope en pesos de las acciones de dinero que Dirección ordena por WhatsApp
# (devolver depósito, descuento manual, reembolso hecho fuera del sistema):
# arriba de este monto, Kooxi responde que se haga en el admin (con 2FA).
ERP_KOOXI_DINERO_MAX = env('ERP_KOOXI_DINERO_MAX', default='5000.00', cast=Decimal)
# Historial que se reenvía al modelo; al pasarlo la conversación empieza de cero.
ERP_KOOXI_MAX_HISTORIAL_CARACTERES = env('ERP_KOOXI_MAX_HISTORIAL_CARACTERES', legado='WA_AGENTE_MAX_HISTORIAL_CARACTERES', default=40000, cast=int)
# La llave de Anthropic se pasa explícita a cada cliente (api_key=settings.ANTHROPIC_API_KEY_KOOXI).
ANTHROPIC_API_KEY_KOOXI = env('ANTHROPIC_API_KEY_KOOXI', legado='ANTHROPIC_API_KEY', default='')

# --- STORAGES (Cloudflare R2, S3-compatible) ---
STORAGES = {
    "default": {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            "access_key": env('CLOUDFLARE_R2_PUBLICO_ID', legado='CLOUDFLARE_R2_ACCESS_KEY_ID', default=''),
            "secret_key": env('CLOUDFLARE_R2_PUBLICO_SECRET', legado='CLOUDFLARE_R2_SECRET_ACCESS_KEY', default=''),
            "bucket_name": env('CLOUDFLARE_R2_PUBLICO_BUCKET', legado='CLOUDFLARE_R2_BUCKET_NAME', default='qkt-media'),
            "endpoint_url": f"https://{env('CLOUDFLARE_R2_CUENTA_ID', legado='CLOUDFLARE_R2_ACCOUNT_ID', default='')}.r2.cloudflarestorage.com",
            "custom_domain": env('CLOUDFLARE_R2_PUBLICO_URL', legado='CLOUDFLARE_R2_CUSTOM_DOMAIN', default='media.quintakooxtanil.com'),
            "region_name": "auto",
            "signature_version": "s3v4",
            "querystring_auth": False,
            # False es intencional: el default histórico de django-storages
            # (True) pisa un archivo existente con el mismo nombre en vez de
            # generar uno nuevo con sufijo, a diferencia de FileSystemStorage.
            "file_overwrite": False,
        },
    },
    # Bucket privado para documentos sensibles (identificaciones ARCO, nómina,
    # contratos, estados de cuenta). A diferencia del default: sin
    # custom_domain y con querystring_auth, así las URLs van firmadas y
    # caducan. Mientras CLOUDFLARE_R2_PRIVADO_BUCKET esté vacío,
    # core_erp/storages_qkt.py cae al default y nada cambia — la activación es
    # solo definir estas variables en Railway. Ver docs/security/.
    "privado": {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            "access_key": env('CLOUDFLARE_R2_PRIVADO_ID', legado='CLOUDFLARE_R2_PRIVATE_ACCESS_KEY_ID',
                                 default=env('CLOUDFLARE_R2_PUBLICO_ID', legado='CLOUDFLARE_R2_ACCESS_KEY_ID', default='')),
            "secret_key": env('CLOUDFLARE_R2_PRIVADO_SECRET', legado='CLOUDFLARE_R2_PRIVATE_SECRET_ACCESS_KEY',
                                 default=env('CLOUDFLARE_R2_PUBLICO_SECRET', legado='CLOUDFLARE_R2_SECRET_ACCESS_KEY', default='')),
            "bucket_name": env('CLOUDFLARE_R2_PRIVADO_BUCKET', legado='CLOUDFLARE_R2_PRIVATE_BUCKET_NAME', default=''),
            "endpoint_url": f"https://{env('CLOUDFLARE_R2_CUENTA_ID', legado='CLOUDFLARE_R2_ACCOUNT_ID', default='')}.r2.cloudflarestorage.com",
            "region_name": "auto",
            "signature_version": "s3v4",
            # Firma cada URL y la caduca: es la diferencia con el bucket público.
            "querystring_auth": True,
            "querystring_expire": env('CLOUDFLARE_R2_PRIVADO_URL_SEGUNDOS', legado='CLOUDFLARE_R2_PRIVATE_URL_EXPIRE', default=300, cast=int),
            "file_overwrite": False,
        },
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedStaticFilesStorage",
    },
}

# django-cloudinary-storage/cloudinary siguen instalados (ver requirements.txt)
# únicamente porque varias migraciones históricas de comercial/facturacion
# importan cloudinary_storage.storage a nivel de módulo — Django necesita
# poder cargarlas para construir el grafo de migraciones (makemigrations,
# migrate, test), aunque ya no se use como storage activo. Ese módulo exige
# que este dict exista con estas 3 claves para poder importarse sin error.
CLOUDINARY_STORAGE = {'CLOUD_NAME': '', 'API_KEY': '', 'API_SECRET': ''}

# El storage activo real es remoto (R2/Cloudinary, ver STORAGES arriba);
# MEDIA_ROOT solo importa para el `static(MEDIA_URL, document_root=...)` de
# core_erp/urls.py, que sirve /media/ localmente cuando DEBUG=True. Sin este
# setting, Django cae al default global de global_settings.py (`''`), que
# sirve (o falla en servir) desde el directorio de trabajo actual en vez de
# una ruta real del proyecto.
MEDIA_ROOT = BASE_DIR / 'media'
MEDIA_URL = '/media/'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# --- JIBBLE API ---
JIBBLE_NOMINA_ID = env('JIBBLE_NOMINA_ID', legado='JIBBLE_CLIENT_ID', default='')
JIBBLE_NOMINA_SECRET = env('JIBBLE_NOMINA_SECRET', legado='JIBBLE_CLIENT_SECRET', default='')
ERP_NOMINA_WEBHOOK_TOKEN = env('ERP_NOMINA_WEBHOOK_TOKEN', legado='NOMINA_CRON_TOKEN', default='')

# --- OPENPAY (checkout propio: tarjeta/efectivo/SPEI + webhook) ---
# OPENPAY_PAGOS_MODO controla el ambiente (sandbox/production) y con eso la URL
# base de la API (ver comercial/services_openpay.py) y el modo de Openpay.js
# (ver templates/portal/evento.html). Para pasar a producción tras la
# certificación: cambiar OPENPAY_PAGOS_MODO=production y las 5 variables de abajo
# por las credenciales reales en las variables de entorno de Railway — no
# requiere cambios de código.
OPENPAY_PAGOS_MODO = env('OPENPAY_PAGOS_MODO', legado='OPENPAY_MODE', default='sandbox')
OPENPAY_PAGOS_ID = env('OPENPAY_PAGOS_ID', legado='OPENPAY_MERCHANT_ID', default='')
OPENPAY_PAGOS_SECRET = env('OPENPAY_PAGOS_SECRET', legado='OPENPAY_PRIVATE_KEY', default='')
OPENPAY_PAGOS_API_KEY_PUBLICA = env('OPENPAY_PAGOS_API_KEY_PUBLICA', legado='OPENPAY_PUBLIC_KEY', default='')
OPENPAY_WEBHOOK_USUARIO = env('OPENPAY_WEBHOOK_USUARIO', legado='OPENPAY_WEBHOOK_USER', default='')
OPENPAY_WEBHOOK_SECRET = env('OPENPAY_WEBHOOK_SECRET', legado='OPENPAY_WEBHOOK_PASSWORD', default='')

# ==============================================================
# SECCIÓN JAZZMIN
# ==============================================================

JAZZMIN_SETTINGS = {
    "site_title": "QKT ERP",
    "site_header": "QKT ERP",
    "site_brand": "QKT ERP",
    "welcome_sign": "Iniciar sesión",
    "copyright": "Quinta Ko'ox Tanil",
    "site_logo": "img/logo.png",
    "login_logo": "img/logo.png",

    "icons": {
        # COMERCIAL
        "comercial":                        "fas fa-store",
        "comercial.Cotizacion":             "fas fa-file-invoice-dollar",
        "comercial.Cliente":                "fas fa-user-friends",
        "comercial.Pago":                   "fas fa-credit-card",
        "comercial.Gasto":                  "fas fa-receipt",
        "comercial.Producto":               "fas fa-box-open",
        "comercial.ProductoEventos":        "fas fa-glass-cheers",
        "comercial.ProductoPasadia":        "fas fa-umbrella-beach",
        "comercial.ProductoHospedaje":      "fas fa-bed",
        "comercial.SubProducto":            "fas fa-cubes",
        "comercial.Insumo":                 "fas fa-tools",
        "comercial.PlantillaBarra":         "fas fa-cocktail",
        "comercial.Proveedor":              "fas fa-truck",
        "comercial.Compra":                 "fas fa-shopping-cart",
        "comercial.MovimientoInventario":   "fas fa-boxes",
        "comercial.PortalCliente":          "fas fa-door-open",
        "comercial.ConstanteSistema":       "fas fa-cog",
        "comercial.ContratoServicio":       "fas fa-handshake",
        "comercial.Espacio":                "fas fa-map-marker-alt",
        "comercial.AsignacionEspacio":      "fas fa-map-pin",
        "comercial.AsignacionPersonal":     "fas fa-user-check",
        "comercial.PlanPago":               "fas fa-calendar-alt",
        "comercial.RecordatorioPago":       "fas fa-bell",
        "comercial.OpenpayTransaccion":     "fas fa-exchange-alt",
        "comercial.AccionWhatsApp":         "fas fa-mobile-alt",
        "comercial.ImagenLanding":          "fas fa-images",
        "comercial.TestimonioLanding":      "fas fa-star",
        "comercial.EspacioLanding":         "fas fa-vector-square",
        "comercial.PreguntaFrecuente":      "fas fa-question-circle",
        "comercial.TipoEvento":             "fas fa-list",
        "comercial.Descuento":              "fas fa-percentage",
        "comercial.DescuentoAplicado":      "fas fa-history",
        "comercial.Temporada":              "fas fa-calendar-week",
        "comercial.GuiaTipoServicio":       "fas fa-book-open",
        "comercial.FirmaContrato":          "fas fa-pen-nib",
        "comercial.DepositoGarantia":       "fas fa-lock",
        "comercial.Contracargo":            "fas fa-undo-alt",
        "comercial.SolicitudCancelacion":   "fas fa-calendar-times",
        "comercial.BloqueoFecha":           "fas fa-calendar-times",

        # CONTABILIDAD
        "contabilidad":                     "fas fa-calculator",
        "contabilidad.poliza":              "fas fa-file-invoice",
        "contabilidad.cuentacontable":      "fas fa-sitemap",
        "contabilidad.movimientocontable":  "fas fa-arrows-alt-h",
        "contabilidad.conciliacionbancaria":    "fas fa-balance-scale",
        "contabilidad.cuentabancaria":      "fas fa-university",
        "contabilidad.unidadnegocio":       "fas fa-building",
        "contabilidad.configuracioncontable":   "fas fa-cogs",
        "contabilidad.saldoapertura":       "fas fa-hourglass-start",
        "contabilidad.estadocuentabancario":    "fas fa-file-alt",
        "contabilidad.reglaconciliacion":   "fas fa-magic",
        "contabilidad.credencialsat":       "fas fa-key",
        "contabilidad.solicituddescargasat": "fas fa-cloud-download-alt",
        "contabilidad.cfdisat":             "fas fa-file-invoice",
        "contabilidad.polizaeliminada":     "fas fa-trash-alt",

        # LEGAL
        "legal":                            "fas fa-gavel",
        "legal.DocumentoLegal":             "fas fa-scroll",
        "legal.AceptacionLegal":            "fas fa-signature",
        "legal.Finalidad":                  "fas fa-bullseye",
        "legal.SolicitudARCO":              "fas fa-user-shield",

        # NÓMINA
        "nomina":                           "fas fa-money-check-alt",
        "nomina.Empleado":                  "fas fa-user-tie",
        "nomina.ReciboNomina":              "fas fa-file-contract",

        # FACTURACIÓN
        "facturacion":                      "fas fa-stamp",
        "facturacion.ClienteFiscal":        "fas fa-id-card",
        "facturacion.SolicitudFactura":     "fas fa-file-signature",
        "facturacion.ConfiguracionContador": "fas fa-user-cog",

        # COMUNICACIÓN
        "comunicacion":                     "fas fa-comments",
        "comunicacion.ComunicacionCliente": "fas fa-paper-plane",
        "comunicacion.HorarioVisita":       "fas fa-clock",
        "comunicacion.Visita":              "fas fa-calendar-check",
        "comunicacion.ConversacionWhatsApp": "fab fa-whatsapp",
        "comunicacion.BajaWhatsApp":        "fas fa-bell-slash",
        "comunicacion.Responsable":         "fas fa-address-book",
        "comunicacion.Pendiente":           "fas fa-clipboard-list",
        "comunicacion.EncuestaPostEvento":  "fas fa-star-half-alt",
        "comunicacion.InspeccionDeposito":  "fas fa-clipboard-check",
        "comunicacion.Hallazgo":            "fas fa-binoculars",
        "nomina.AprobacionNomina":          "fas fa-clipboard-check",

        # AUTH
        "auth":                             "fas fa-shield-alt",
        "auth.user":                        "fas fa-user",
        "auth.group":                       "fas fa-users-cog",

        # REPORTERÍA
        "reportes":                          "fas fa-chart-bar",
        "reportes.reportegenerado":          "fas fa-clipboard-list",

        # INDICADORES (solo Dirección)
        "indicadores":                       "fas fa-chart-line",
        "indicadores.ParametrosNegocio":     "fas fa-sliders-h",
        "indicadores.MetaMensual":           "fas fa-bullseye",

        # OPERACIONES
        "operaciones":                       "fas fa-broom",
        "operaciones.PlantillaChecklist":    "fas fa-tasks",
        "operaciones.TareaProgramada":       "fas fa-clock",
        "operaciones.EjecucionCron":         "fas fa-history",

        # CUMPLIMIENTO
        "cumplimiento":                      "fas fa-clipboard-check",
        "cumplimiento.Tramite":              "fas fa-stamp",
        "cumplimiento.Vigencia":             "fas fa-calendar-check",
        "cumplimiento.AvisoVencimiento":     "fas fa-bell",
    },

    # Modelos que siguen registrados en el admin (autocompletado, popups de
    # alta "+" desde un inline) pero no aparecen como módulo propio en el
    # menú: se administran por completo desde la pantalla de otro modelo.
    "hide_models": [
        # Costeo por receta (Insumo → Subproducto → Producto): en la práctica
        # casi todo se captura como "Precio de venta fijo" directo en
        # Producto, así que este flujo casi no se usa y solo agrega ruido al
        # menú. Sigue disponible desde la pestaña "SubProductos" de Producto
        # (el "+" del campo abre el alta de un Subproducto/Insumo nuevo) para
        # quien sí quiera costear por receta.
        "comercial.Insumo",
        "comercial.SubProducto",
        # El catálogo plano de Productos se reorganizó en 3 submódulos por
        # línea de negocio (Eventos/Pasadía/Hospedaje, ver
        # admin_lineas_negocio.py) — son proxies del mismo modelo/tabla, así
        # que se oculta el "Producto" sin filtrar para no duplicar el menú.
        "comercial.Producto",
        # Bitácora de avisos de vencimiento (solo lectura, se consulta desde Cumplimiento)
        "cumplimiento.AvisoVencimiento",
        # Se capturan desde «Plan del año» («Ajustes avanzados» abre Parámetros); siguen accesibles por URL
        "indicadores.ParametrosNegocio",
        "indicadores.MetaMensual",
    ],

    # ── TOP MENU ──────────────────────────────────────────────
    "topmenu_links": [
        {"name": "Inicio",             "url": "admin:index",            "permissions": ["auth.view_user"]},
        {"name": "Calendario",         "url": "calendario_unificado"},
        {"name": "Cartera",            "url": "cartera_cxc"},
        {"name": "Reportes",           "url": "reportes:selector"},
        {"name": "Indicadores",        "url": "indicadores:tablero",     "permissions": ["indicadores.view_metamensual"]},
        {"name": "Cerrar sesión",      "url": "/admin/logout/",          "new_window": False},
    ],

    "order_with_respect_to": [
        # === EVENTOS & SERVICIOS ===
        "comercial",
        "comercial.Cotizacion",
        "comercial.Cliente",
        "comercial.Pago",
        "comercial.Gasto",
        "comercial.ProductoEventos",
        "comercial.ProductoPasadia",
        "comercial.ProductoHospedaje",
        "comercial.Producto",
        "comercial.SubProducto",
        "comercial.Insumo",
        "comercial.PlantillaBarra",
        "comercial.Proveedor",
        "comercial.Compra",
        "comercial.MovimientoInventario",
        "comercial.PortalCliente",
        "comercial.ConstanteSistema",
        "comercial.ContratoServicio",
        "comercial.FirmaContrato",
        "comercial.BloqueoFecha",
        "comercial.Espacio",
        "comercial.AsignacionEspacio",
        "comercial.AsignacionPersonal",
        "comercial.PlanPago",
        "comercial.RecordatorioPago",
        "comercial.OpenpayTransaccion",
        "comercial.AccionWhatsApp",
        "comercial.DepositoGarantia",
        "comercial.Contracargo",
        "comercial.SolicitudCancelacion",
        "comercial.ImagenLanding",
        "comercial.TestimonioLanding",
        "comercial.EspacioLanding",
        "comercial.PreguntaFrecuente",

        # === CONTABILIDAD ===
        "contabilidad",
        "contabilidad.poliza",
        "contabilidad.polizaeliminada",
        "contabilidad.cuentacontable",
        "contabilidad.movimientocontable",
        "contabilidad.conciliacionbancaria",
        "contabilidad.cuentabancaria",
        "contabilidad.unidadnegocio",
        "contabilidad.configuracioncontable",
        "contabilidad.credencialsat",
        "contabilidad.solicituddescargasat",
        "contabilidad.cfdisat",

        # === NÓMINA ===
        "nomina",
        "nomina.Empleado",
        "nomina.ReciboNomina",
        "nomina.AprobacionNomina",

        # === FACTURACIÓN ===
        "facturacion",
        "facturacion.ClienteFiscal",
        "facturacion.SolicitudFactura",
        "facturacion.ConfiguracionContador",

        # === COMUNICACIÓN ===
        "comunicacion",
        "comunicacion.ComunicacionCliente",
        "comunicacion.ConversacionWhatsApp",
        "comunicacion.BajaWhatsApp",
        "comunicacion.Responsable",
        "comunicacion.Pendiente",
        "comunicacion.EncuestaPostEvento",
        "comunicacion.InspeccionDeposito",
        "comunicacion.Hallazgo",
        "comunicacion.Visita",
        "comunicacion.HorarioVisita",

        # === OPERACIONES ===
        "operaciones",
        "operaciones.PlantillaChecklist",
        "operaciones.TareaProgramada",
        "operaciones.EjecucionCron",

        # === REPORTES ===
        "reportes",
        "reportes.reportegenerado",

        # === INDICADORES ===
        "indicadores",
        "indicadores.ParametrosNegocio",
        "indicadores.MetaMensual",
        # === CUMPLIMIENTO ===
        "cumplimiento",
        "cumplimiento.Tramite",
        "cumplimiento.Vigencia",

        # === ADMINISTRACIÓN ===
        "auth",
        "auth.user",
        "auth.group",
    ],

    "custom_css": "css/admin_fix.css",
    "custom_js": "js/tabs_fix.js",

    # Los grupos del menú (Eventos & Servicios, etc.) inician colapsados;
    # un clic en el título muestra/oculta sus submódulos.
    "navigation_expanded": False,

    # django_otp (2FA, orden 42/SEC-AUTHN-002) trae su propia sección
    # autogenerada "Otp_Totp"; se oculta y en su lugar se agrega un enlace
    # dentro de "auth" (Usuarios) al mismo changelist de
    # TOTPDevice — queda fusionado de verdad en un solo grupo del menú, sin
    # crear un proxy con app_label='auth' (eso exigiría una migración dentro
    # del propio paquete django.contrib.auth, fuera del repo — inviable).
    "hide_apps": ["otp_totp"],
    "custom_links": {
        "auth": [{
            "name": "2FA",
            "url": "admin:otp_totp_totpdevice_changelist",
            "icon": "fas fa-mobile-alt",
            "permissions": ["auth.view_user"],
        }],
        "indicadores": [
            {"name": "Este mes", "url": "indicadores:tablero", "icon": "fas fa-chart-line",
             "permissions": ["indicadores.view_metamensual"]},
            {"name": "Plan del año", "url": "indicadores:plan", "icon": "fas fa-th-large",
             "permissions": ["indicadores.view_metamensual"]},
        ],
        "cumplimiento": [{
            "name": "Tablero",
            "url": "cumplimiento:tablero",
            "icon": "fas fa-th-list",
            "permissions": ["cumplimiento.view_tramite"],
        }],
        "comunicacion": [{
            "name": "Kooxi",
            "url": "admin:comunicacion_tablero_agente",
            "icon": "fas fa-robot",
            "permissions": ["comunicacion.view_conversacionwhatsapp"],
        }],
        "contabilidad": [{
            "name": "Cobertura",
            "url": "contabilidad:panel_cobertura",
            "icon": "fas fa-clipboard-check",
            "permissions": ["contabilidad.view_movimientocontable"],
        }],
    },
}

JAZZMIN_UI_TWEAKS = {
    "theme": "darkly",
    "default_theme_mode": "dark",
    "navbar": "navbar-dark",
    "sidebar": "sidebar-dark-success",
    "accent": "accent-success",
    "brand_colour": "navbar-success",
    "body_small_text": False,
    "navbar_small_text": False,
    "sidebar_nav_small_text": False,
    "no_navbar_border": True,
    "sidebar_disable_expand": False,
    "sidebar_nav_child_indent": True,
    "sidebar_nav_compact_style": False,
    "sidebar_nav_legacy_style": False,
    "sidebar_nav_flat_style": False,
}

# --- REDIRECCIONES DE LOGIN/LOGOUT ---
LOGIN_REDIRECT_URL = '/admin/'
LOGOUT_REDIRECT_URL = '/admin/login/'

CONTABILIDAD_SIGNALS_ENABLED = True
