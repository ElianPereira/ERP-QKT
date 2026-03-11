from decouple import config, Csv
from pathlib import Path
import os
import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent

# --- SEGURIDAD ---
SECRET_KEY = config('SECRET_KEY')  # SIN default — fuerza a que exista en .env
DEBUG = config('DEBUG', default=False, cast=bool)
ALLOWED_HOSTS = config('ALLOWED_HOSTS', default='localhost,127.0.0.1', cast=Csv())

CSRF_TRUSTED_ORIGINS = [
    'https://erp-qkt.up.railway.app',
    'https://*.railway.app',
]

# --- Seguridad en producción (se activan cuando DEBUG=False) ---
if not DEBUG:
    SECURE_SSL_REDIRECT = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_BROWSER_XSS_FILTER = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    X_FRAME_OPTIONS = 'DENY'

INSTALLED_APPS = [
    'jazzmin',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.humanize', 
    'cloudinary_storage',
    'cloudinary',
    'comercial',
    'nomina',
    'facturacion',
    'weasyprint',
    'anymail',
    'airbnb',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
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
            ],
        },
    },
]

WSGI_APPLICATION = 'core_erp.wsgi.application'

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {'console': {'class': 'logging.StreamHandler'}},
    'loggers': {
        'django': {'handlers': ['console'], 'level': 'INFO'},
        'weasyprint': {'handlers': ['console'], 'level': 'WARNING'},
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

AUTH_PASSWORD_VALIDATORS = [
    { 'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator', },
    { 'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator', },
    { 'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator', },
    { 'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator', },
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

# --- STORAGES ---
STORAGES = {
    "default": {
        "BACKEND": "cloudinary_storage.storage.MediaCloudinaryStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedStaticFilesStorage",
    },
}

CLOUDINARY_STORAGE = {
    'CLOUD_NAME': config('CLOUDINARY_CLOUD_NAME', default=''),
    'API_KEY': config('CLOUDINARY_API_KEY', default=''),
    'API_SECRET': config('CLOUDINARY_API_SECRET', default=''),
}

MEDIA_URL = '/media/'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# --- CONFIGURACIÓN JAZZMIN ---
JAZZMIN_SETTINGS = {
    "site_title": "ERP Quinta Ko'ox Tanil",
    "site_header": "Sistema de Eventos",
    "site_brand": "QKT ERP",
    "welcome_sign": "Bienvenido al Panel de Control",
    "copyright": "Quinta Ko'ox Tanil",
    "site_logo": "img/logo.png",
    "login_logo": "img/logo.png",

    "show_sidebar": True,
    "navigation_expanded": True,

    "icons": {
        # App-level
        "comercial":                        "fas fa-calendar-alt",
        "airbnb":                           "fas fa-home",
        "nomina":                           "fas fa-users",
        "facturacion":                      "fas fa-file-invoice",
        "auth":                             "fas fa-cog",

        # EVENTOS & SERVICIOS
        "comercial.Cotizacion":             "fas fa-file-invoice-dollar",
        "comercial.Cliente":                "fas fa-address-book",
        "comercial.Pago":                   "fas fa-hand-holding-usd",
        "comercial.Gasto":                  "fas fa-money-bill-wave",
        # Catálogo
        "comercial.Producto":               "fas fa-box-open",
        "comercial.SubProducto":            "fas fa-blender",
        "comercial.Insumo":                 "fas fa-cubes",
        "comercial.PlantillaBarra":         "fas fa-cocktail",
        "comercial.Proveedor":              "fas fa-truck",
        "comercial.Compra":                 "fas fa-shopping-cart",
        "comercial.ConstanteSistema":       "fas fa-sliders-h",

        # AIRBNB & HOSPEDAJE
        "airbnb.ReservaAirbnb":             "fas fa-calendar-check",
        "airbnb.PagoAirbnb":               "fas fa-hand-holding-usd",
        "airbnb.ConflictoCalendario":       "fas fa-exclamation-triangle",
        "airbnb.AnuncioAirbnb":             "fas fa-home",

        # NÓMINA
        "nomina.Empleado":                  "fas fa-user-tie",
        "nomina.ReciboNomina":              "fas fa-file-contract",

        # FACTURACIÓN
        "facturacion.ClienteFiscal":        "fas fa-building",
        "facturacion.SolicitudFactura":     "fas fa-file-signature",

        # AUTH
        "auth.user":                        "fas fa-user",
        "auth.group":                       "fas fa-users-cog",
    },

    "topmenu_links": [
        {"name": "🏠 Inicio",          "url": "admin:index",           "permissions": ["auth.view_user"]},
        {"name": "📅 Calendario",       "url": "ver_calendario"},
        {"name": "🔗 Cal. Unificado",   "url": "calendario_unificado"},
        {"name": "🧮 Calculadora",      "url": "admin_calculadora"},
        {"name": "🛒 Lista de Compras", "url": "generar_lista_compras"},
        {"name": "💰 Cartera", "url": "cartera_cxc"},
        {"name": "🚪 Cerrar Sesión",    "url": "/admin/logout/",        "new_window": False},
        
    ],

    "order_with_respect_to": [
        # === EVENTOS & SERVICIOS ===
        "comercial",
        "comercial.Cotizacion",
        "comercial.Cliente",
        "comercial.Pago",
        "comercial.Gasto",
        # Catálogo (uso ocasional)
        "comercial.Producto",
        "comercial.SubProducto",
        "comercial.Insumo",
        "comercial.PlantillaBarra",
        "comercial.Proveedor",
        "comercial.Compra",
        "comercial.ConstanteSistema",   # ← al fondo

        # === AIRBNB & HOSPEDAJE ===
        "airbnb",
        "airbnb.ReservaAirbnb",
        "airbnb.PagoAirbnb",
        "airbnb.ConflictoCalendario",
        "airbnb.AnuncioAirbnb",         # ← configuración al fondo

        # === NÓMINA ===
        "nomina",
        "nomina.Empleado",
        "nomina.ReciboNomina",

        # === FACTURACIÓN ===
        "facturacion",
        "facturacion.ClienteFiscal",
        "facturacion.SolicitudFactura",

        # === ADMINISTRACIÓN ===
        "auth",
        "auth.user",
        "auth.group",
    ],

    "custom_css": "css/mobile_fix_v4.css",
    "custom_js": "js/tabs_fix.js",
}

JAZZMIN_UI_TWEAKS = {"theme": "flatly"}

# --- REDIRECCIONES DE LOGIN/LOGOUT ---
LOGIN_REDIRECT_URL = '/admin/'  
LOGOUT_REDIRECT_URL = '/admin/login/'