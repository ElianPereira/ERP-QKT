import os
from pathlib import Path

import dj_database_url
from decouple import Csv, config

BASE_DIR = Path(__file__).resolve().parent.parent

# --- SEGURIDAD ---
SECRET_KEY = config('SECRET_KEY')  # SIN default — fuerza a que exista en .env
DEBUG = config('DEBUG', default=False, cast=bool)
ALLOWED_HOSTS = config('ALLOWED_HOSTS', default='localhost,127.0.0.1', cast=Csv())

# Nº de proxies de confianza delante de la app (el edge de Railway = 1). Se usa
# para leer la IP real del cliente en el rate limiting sin que sea spoofeable
# vía X-Forwarded-For. Ajustar solo si se añaden más proxies (CDN, etc.).
RATELIMIT_TRUSTED_PROXY_COUNT = config('RATELIMIT_TRUSTED_PROXY_COUNT', default=1, cast=int)

CSRF_TRUSTED_ORIGINS = [
    'https://erp-qkt.up.railway.app',
    'https://*.railway.app',
    'https://quintakooxtanil.com',
    'https://erp.quintakooxtanil.com',
]

# URL base del portal del cliente (usada en links, emails y notificaciones)
PORTAL_URL = config('PORTAL_URL', default='https://erp.quintakooxtanil.com')

# --- URL canónica del sitio (para links en emails, portales, etc.) ---
SITE_URL = config('SITE_URL', default='https://erp-qkt.up.railway.app')

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
# así la sesión caduca solo tras SESSION_IDLE_TIMEOUT segundos SIN actividad
# (idle timeout), no de forma absoluta. Ajustable por variable de entorno; para
# datos financieros se puede endurecer a 900 (15 min).
SESSION_IDLE_TIMEOUT = config('SESSION_IDLE_TIMEOUT', default=1800, cast=int)  # 30 min
SESSION_COOKIE_AGE = SESSION_IDLE_TIMEOUT
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
    'airbnb',
    'contabilidad',
    'reportes',
    'comunicacion',
    'legal',
    'operaciones',
    'django_otp',
    'django_otp.plugins.otp_totp',
]

# --- TOTP obligatorio para superusuarios (SEC-AUTHN-002) ---
OTP_TOTP_ISSUER = 'ERP QKT'

# --- Cabeceras de seguridad por ruta. Ver core_erp/middleware.py ---
PUBLIC_CSP_ENABLED = config('PUBLIC_CSP_ENABLED', default=True, cast=bool)
PUBLIC_CSP_REPORT_ONLY = config('PUBLIC_CSP_REPORT_ONLY', default=False, cast=bool)
# CSP Report-Only del portal de pago (no bloquea): activar solo para probar qué
# recursos usa Openpay/3-D Secure antes de plantear una CSP bloqueante ahí.
PORTAL_CSP_REPORT_ONLY = config('PORTAL_CSP_REPORT_ONLY', default=False, cast=bool)
# CSP Report-Only del admin (no bloquea): activar solo para probar qué
# recursos usa Jazzmin/FullCalendar/Chart.js antes de plantear una CSP
# bloqueante ahí (orden 37, SEC-CFG-002).
ADMIN_CSP_REPORT_ONLY = config('ADMIN_CSP_REPORT_ONLY', default=False, cast=bool)

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

# --- FEED iCAL PÚBLICO (lo consume Airbnb para bloquear fechas) ---
# Se lee aquí y no con config() dentro de la vista para que exista una sola
# fuente y los tests puedan sobreescribirlo con override_settings. Sin valor,
# la vista responde 403: ver ICAL en airbnb/views.py::generar_ical_eventos.
ICAL_PUBLIC_TOKEN = config('ICAL_PUBLIC_TOKEN', default='')

# --- BLOQUEO DE FUERZA BRUTA EN /admin/login/ ---
ADMIN_LOGIN_VENTANA = config('ADMIN_LOGIN_VENTANA', default=900, cast=int)
ADMIN_LOGIN_MAX_INTENTOS_IP = config('ADMIN_LOGIN_MAX_INTENTOS_IP', default=10, cast=int)
ADMIN_LOGIN_MAX_INTENTOS_USUARIO = config('ADMIN_LOGIN_MAX_INTENTOS_USUARIO', default=20, cast=int)

# Bloqueo por cotización en el acceso al portal del cliente (SEC-AUTHN-001b):
# complementa el rate_limit por IP de portal_acceso, que un atacante distribuido
# elude cambiando de IP en cada intento contra los mismos 4 dígitos.
PORTAL_ACCESO_VENTANA = config('PORTAL_ACCESO_VENTANA', default=900, cast=int)
PORTAL_ACCESO_MAX_INTENTOS = config('PORTAL_ACCESO_MAX_INTENTOS', default=10, cast=int)

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
STATICFILES_DIRS = [os.path.join(BASE_DIR, 'static')]

# --- CORREO ---
EMAIL_BACKEND = "anymail.backends.brevo.EmailBackend"
ANYMAIL = {"BREVO_API_KEY": config('BREVO_API_KEY', default='')}
DEFAULT_FROM_EMAIL = config('DEFAULT_FROM_EMAIL', default='quintakooxtanil@gmail.com')
SERVER_EMAIL = config('DEFAULT_FROM_EMAIL', default='quintakooxtanil@gmail.com')
# Remitentes por tipo de correo (Issue #221). Cada uno cae a DEFAULT_FROM_EMAIL
# si no está configurado, para que el deploy no rompa nada si llega antes de
# que el dominio quede verificado en Brevo.
EMAIL_FROM_RESERVAS = config('EMAIL_FROM_RESERVAS', default=DEFAULT_FROM_EMAIL)
EMAIL_FROM_PAGOS = config('EMAIL_FROM_PAGOS', default=DEFAULT_FROM_EMAIL)
EMAIL_FROM_NOTIFICACIONES = config('EMAIL_FROM_NOTIFICACIONES', default=DEFAULT_FROM_EMAIL)

# --- WHATSAPP CLOUD API (Meta) ---
# Credenciales del número emisor. Se leen aquí (y no con config() disperso por
# el código) para que exista una sola fuente y los tests puedan sobreescribirlas.
WA_PHONE_NUMBER_ID = config('WA_PHONE_NUMBER_ID', default='')
WA_CLOUD_API_TOKEN = config('WA_CLOUD_API_TOKEN', default='')

# Dos números con roles distintos, y ninguno con fallback en código:
#   WA_NUMERO_NEGOCIO         → destino de las alertas internas de nueva cotización.
#                               Debe ser un teléfono que lea una persona y NO puede
#                               coincidir con el número emisor (Meta rechaza con 131021).
#   WA_NUMERO_CONTACTO_PUBLICO → el que se le muestra al cliente en los enlaces wa.me
#                               del portal. Si va vacío, la plantilla oculta el enlace.
WA_NUMERO_NEGOCIO = config('WA_NUMERO_NEGOCIO', default='')
WA_NUMERO_CONTACTO_PUBLICO = config('WA_NUMERO_CONTACTO_PUBLICO', default='')
WA_GRAPH_VERSION = config('WA_GRAPH_VERSION', default='v20.0')

# Plantillas aprobadas en Meta. Los mensajes iniciados por el negocio (pago y
# recordatorio) solo llegan por plantilla: fuera de la ventana de 24 h Meta
# rechaza el texto libre con el error 131047. Si una plantilla no está
# configurada, el envío queda FALLIDO y auditado, sin romper la operación.
WA_TEMPLATE_LANGUAGE = config('WA_TEMPLATE_LANGUAGE', default='es_MX')
WA_TEMPLATE_COTIZACION = config('WA_TEMPLATE_COTIZACION', default='')
WA_TEMPLATE_PAGO = config('WA_TEMPLATE_PAGO', default='')
WA_TEMPLATE_RECORDATORIO = config('WA_TEMPLATE_RECORDATORIO', default='')
# Guía pre-evento (Issue #234): enlace de descarga, nunca el PDF adjunto —
# adjuntarlo requeriría una plantilla tipo "documento", más lenta de aprobar.
WA_TEMPLATE_GUIA = config('WA_TEMPLATE_GUIA', default='')
# Opcional: la alerta interna va como texto libre si esta queda vacía, lo que
# solo funciona mientras la ventana de 24 h con ese número esté abierta.
WA_TEMPLATE_ALERTA_INTERNA = config('WA_TEMPLATE_ALERTA_INTERNA', default='')
# Solicitud de factura al contador (facturacion/services.py): a diferencia de
# la guía, aquí SÍ se somete una plantilla tipo "documento" — el pedido
# original es mandar el PDF adjunto, no un enlace. Ver
# docs/whatsapp_plantilla_solicitud_factura.md para el texto exacto a
# someter en Meta Business Manager. Vacía = sigue mandando el PDF como
# mensaje 'document' directo (funciona dentro de la ventana de 24h, falla
# fuera de ella — comportamiento sin cambios hasta que se apruebe).
WA_TEMPLATE_SOLICITUD_FACTURA = config('WA_TEMPLATE_SOLICITUD_FACTURA', default='')
# Módulo de operaciones (Issue #257): abre la conversación con el colaborador
# antes del checklist/aviso de horario en texto libre (que sí puede llevar
# saltos de línea, a diferencia de un parámetro de plantilla). Vacía = se
# manda solo el texto libre, funciona mientras la ventana de 24h esté
# abierta — mismo criterio que el resto de mensajería interna sin plantilla.
WA_TEMPLATE_OPERACIONES = config('WA_TEMPLATE_OPERACIONES', default='')

# --- STORAGES (Cloudflare R2, S3-compatible) ---
STORAGES = {
    "default": {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            "access_key": config('CLOUDFLARE_R2_ACCESS_KEY_ID', default=''),
            "secret_key": config('CLOUDFLARE_R2_SECRET_ACCESS_KEY', default=''),
            "bucket_name": config('CLOUDFLARE_R2_BUCKET_NAME', default='qkt-media'),
            "endpoint_url": f"https://{config('CLOUDFLARE_R2_ACCOUNT_ID', default='')}.r2.cloudflarestorage.com",
            "custom_domain": config('CLOUDFLARE_R2_CUSTOM_DOMAIN', default='media.quintakooxtanil.com'),
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
    # caducan. Mientras CLOUDFLARE_R2_PRIVATE_BUCKET_NAME esté vacío,
    # core_erp/storages_qkt.py cae al default y nada cambia — la activación es
    # solo definir estas variables en Railway. Ver docs/security/.
    "privado": {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            "access_key": config('CLOUDFLARE_R2_PRIVATE_ACCESS_KEY_ID',
                                 default=config('CLOUDFLARE_R2_ACCESS_KEY_ID', default='')),
            "secret_key": config('CLOUDFLARE_R2_PRIVATE_SECRET_ACCESS_KEY',
                                 default=config('CLOUDFLARE_R2_SECRET_ACCESS_KEY', default='')),
            "bucket_name": config('CLOUDFLARE_R2_PRIVATE_BUCKET_NAME', default=''),
            "endpoint_url": f"https://{config('CLOUDFLARE_R2_ACCOUNT_ID', default='')}.r2.cloudflarestorage.com",
            "region_name": "auto",
            "signature_version": "s3v4",
            # Firma cada URL y la caduca: es la diferencia con el bucket público.
            "querystring_auth": True,
            "querystring_expire": config('CLOUDFLARE_R2_PRIVATE_URL_EXPIRE', default=300, cast=int),
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
CLOUDINARY_STORAGE = {
    'CLOUD_NAME': config('CLOUDINARY_CLOUD_NAME', default=''),
    'API_KEY': config('CLOUDINARY_API_KEY', default=''),
    'API_SECRET': config('CLOUDINARY_API_SECRET', default=''),
}

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
JIBBLE_CLIENT_ID = config('JIBBLE_CLIENT_ID', default='')
JIBBLE_CLIENT_SECRET = config('JIBBLE_CLIENT_SECRET', default='')
NOMINA_CRON_TOKEN = config('NOMINA_CRON_TOKEN', default='')

# --- OPENPAY (checkout propio: tarjeta/efectivo/SPEI + webhook) ---
# OPENPAY_MODE controla el ambiente (sandbox/production) y con eso la URL
# base de la API (ver comercial/services_openpay.py) y el modo de Openpay.js
# (ver templates/portal/evento.html). Para pasar a producción tras la
# certificación: cambiar OPENPAY_MODE=production y las 5 variables de abajo
# por las credenciales reales en las variables de entorno de Railway — no
# requiere cambios de código.
OPENPAY_MODE = config('OPENPAY_MODE', default='sandbox')
OPENPAY_MERCHANT_ID = config('OPENPAY_MERCHANT_ID', default='')
OPENPAY_PRIVATE_KEY = config('OPENPAY_PRIVATE_KEY', default='')
OPENPAY_PUBLIC_KEY = config('OPENPAY_PUBLIC_KEY', default='')
OPENPAY_WEBHOOK_USER = config('OPENPAY_WEBHOOK_USER', default='')
OPENPAY_WEBHOOK_PASSWORD = config('OPENPAY_WEBHOOK_PASSWORD', default='')

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
        "comercial.ImagenLanding":          "fas fa-images",
        "comercial.TestimonioLanding":      "fas fa-star",
        "comercial.EspacioLanding":         "fas fa-vector-square",
        "comercial.PreguntaFrecuente":      "fas fa-question-circle",
        "comercial.TipoEvento":             "fas fa-list",
        "comercial.Descuento":              "fas fa-percentage",
        "comercial.DescuentoAplicado":      "fas fa-history",
        "comercial.Temporada":              "fas fa-calendar-week",

        # AIRBNB
        "airbnb":                           "fas fa-bed",
        "airbnb.ReservaAirbnb":             "fas fa-calendar-check",
        "airbnb.PagoAirbnb":               "fas fa-money-bill-wave",
        "airbnb.ConflictoCalendario":       "fas fa-exclamation-triangle",
        "airbnb.AnuncioAirbnb":             "fas fa-home",

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

        # AUTH
        "auth":                             "fas fa-shield-alt",
        "auth.user":                        "fas fa-user",
        "auth.group":                       "fas fa-users-cog",

        # REPORTERÍA
        "reportes":                          "fas fa-chart-bar",
        "reportes.reportegenerado":          "fas fa-clipboard-list",
    },

    # ── TOP MENU ──────────────────────────────────────────────
    "topmenu_links": [
        {"name": "Inicio",             "url": "admin:index",            "permissions": ["auth.view_user"]},
        {"name": "Calendario",         "url": "ver_calendario"},
        {"name": "Compras",            "url": "generar_lista_compras"},
        {"name": "Cartera",            "url": "cartera_cxc"},
        {"name": "Reportes",           "url": "reportes:selector"},
        {"name": "Cerrar sesión",      "url": "/admin/logout/",          "new_window": False},
    ],

    "order_with_respect_to": [
        # === EVENTOS & SERVICIOS ===
        "comercial",
        "comercial.Cotizacion",
        "comercial.Cliente",
        "comercial.Pago",
        "comercial.Gasto",
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
        "comercial.Espacio",
        "comercial.AsignacionEspacio",
        "comercial.AsignacionPersonal",
        "comercial.PlanPago",
        "comercial.RecordatorioPago",
        "comercial.OpenpayTransaccion",
        "comercial.ImagenLanding",
        "comercial.TestimonioLanding",
        "comercial.EspacioLanding",
        "comercial.PreguntaFrecuente",

        # === AIRBNB & HOSPEDAJE ===
        "airbnb",
        "airbnb.ReservaAirbnb",
        "airbnb.PagoAirbnb",
        "airbnb.ConflictoCalendario",
        "airbnb.AnuncioAirbnb",

        # === CONTABILIDAD ===
        "contabilidad",
        "contabilidad.poliza",
        "contabilidad.cuentacontable",
        "contabilidad.movimientocontable",
        "contabilidad.conciliacionbancaria",
        "contabilidad.cuentabancaria",
        "contabilidad.unidadnegocio",
        "contabilidad.configuracioncontable",

        # === NÓMINA ===
        "nomina",
        "nomina.Empleado",
        "nomina.ReciboNomina",

        # === FACTURACIÓN ===
        "facturacion",
        "facturacion.ClienteFiscal",
        "facturacion.SolicitudFactura",
        "facturacion.ConfiguracionContador",

        # === COMUNICACIÓN ===
        "comunicacion",
        "comunicacion.ComunicacionCliente",

        # === REPORTES ===
        "reportes",
        "reportes.reportegenerado",

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
    # dentro de "auth" (Autenticación y Usuarios) al mismo changelist de
    # TOTPDevice — queda fusionado de verdad en un solo grupo del menú, sin
    # crear un proxy con app_label='auth' (eso exigiría una migración dentro
    # del propio paquete django.contrib.auth, fuera del repo — inviable).
    "hide_apps": ["otp_totp"],
    "custom_links": {
        "auth": [{
            "name": "Dispositivos TOTP (2FA)",
            "url": "admin:otp_totp_totpdevice_changelist",
            "icon": "fas fa-mobile-alt",
            "permissions": ["auth.view_user"],
        }],
        "contabilidad": [{
            "name": "Panel de cobertura",
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
