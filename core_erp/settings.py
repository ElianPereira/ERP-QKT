from decouple import config
from pathlib import Path
import os
import dj_database_url

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = config('SECRET_KEY', default='django-insecure-key-dev')

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = config('DEBUG', default=False, cast=bool)

# Permitir cualquier host para evitar errores de conexión en Railway
ALLOWED_HOSTS = ['*']

# --- SEGURIDAD CSRF (CRITICO PARA EL LOGIN) ---
# Esto permite que Django confíe en los formularios enviados desde Railway
CSRF_TRUSTED_ORIGINS = [
    'https://erp-qkt.up.railway.app',
    'https://*.railway.app',
]

# --- APLICACIONES INSTALADAS ---
INSTALLED_APPS = [
    'jazzmin',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.humanize', 

    # Mis Apps Existentes
    'comercial',

    # Mis Apps Nuevas
    'nomina',
    'facturacion',
    
    # Librerías extra
    'weasyprint',
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

# --- CONFIGURACIÓN DE LOGS ---
# Optimizado para que Railway no se sature escribiendo texto
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
        },
    },
    'loggers': {
        'django': {
            'handlers': ['console'],
            'level': 'INFO',
        },
        'weasyprint': {
            'handlers': ['console'],
            'level': 'WARNING',  # Solo errores graves
        },
        'fontTools': {
            'handlers': ['console'],
            'level': 'WARNING', 
        },
    },
}

# --- BASE DE DATOS ---
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

# Configuración automática para PostgreSQL en Railway
db_from_env = dj_database_url.config(conn_max_age=500)
DATABASES['default'].update(db_from_env)

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    { 'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator', },
    { 'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator', },
    { 'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator', },
    { 'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator', },
]

# --- IDIOMA Y ZONA HORARIA ---
LANGUAGE_CODE = 'es-mx'
TIME_ZONE = 'America/Merida'
USE_I18N = True
USE_TZ = True

# --- FORMATO DE NÚMEROS ---
USE_L10N = False 
USE_THOUSAND_SEPARATOR = True
DECIMAL_SEPARATOR = '.'
THOUSAND_SEPARATOR = ','
NUMBER_GROUPING = 3

# --- ARCHIVOS ESTÁTICOS ---
STATIC_URL = 'static/'
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')
STATICFILES_DIRS = [os.path.join(BASE_DIR, 'static')]

# Usamos CompressedStaticFilesStorage (sin Manifest) para evitar errores si falta algún archivo
STATICFILES_STORAGE = 'whitenoise.storage.CompressedStaticFilesStorage'

# --- ARCHIVOS MEDIA (PDFs, Excel, XML) ---
MEDIA_URL = '/media/'
MEDIA_ROOT = os.path.join(BASE_DIR, 'media')

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# --- CONFIGURACIÓN DE CORREO (BREVO / SENDINBLUE - MODO SSL SEGURO) ---
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = 'smtp-relay.brevo.com'
# CAMBIO CRÍTICO: Usar puerto 465 con SSL evita el timeout del "handshake"
EMAIL_PORT = 465
EMAIL_USE_TLS = False  # Apagado para usar SSL puro
EMAIL_USE_SSL = True   # Encendido para conexión segura inmediata
EMAIL_TIMEOUT = 15     # IMPORTANTE: Si falla, libera el proceso en 15s (evita Worker Timeout)

# Las credenciales se leen desde las variables de entorno (.env o Railway)
EMAIL_HOST_USER = config('EMAIL_HOST_USER', default='') 
EMAIL_HOST_PASSWORD = config('EMAIL_HOST_PASSWORD', default='')

# Asegúrate de que este correo esté verificado como remitente en Brevo
DEFAULT_FROM_EMAIL = 'quintakooxtanil@gmail.com'

# --- CONFIGURACIÓN DE JAZZMIN ---
JAZZMIN_SETTINGS = {
    "site_title": "ERP Quinta Ko'ox Tanil",
    "site_header": "Sistema de Eventos",
    "site_brand": "QKT ERP",
    "welcome_sign": "Bienvenido al Panel de Control",
    "copyright": "Quinta Ko'ox Tanil",
    "site_logo": "img/logo.png",
    "login_logo": "img/logo.png",
    "icons": {
        "auth": "fas fa-users-cog",
        "auth.user": "fas fa-user",
        "auth.Group": "fas fa-users",
        
        # Comercial
        "comercial.Cliente": "fas fa-address-book",
        "comercial.Cotizacion": "fas fa-file-invoice-dollar",
        "comercial.Insumo": "fas fa-cubes",
        "comercial.Pago": "fas fa-hand-holding-usd",
        "comercial.Producto": "fas fa-box-open",
        
        # Nómina
        "nomina.Empleado": "fas fa-user-tie",
        "nomina.ReciboNomina": "fas fa-file-contract",

        # Facturación
        "facturacion.ClienteFiscal": "fas fa-building",
        "facturacion.SolicitudFactura": "fas fa-file-signature",
    },
    "topmenu_links": [
        {"name": "Inicio",  "url": "admin:index", "permissions": ["auth.view_user"]},
        {"name": "📅 Ver Calendario", "url": "ver_calendario"}, 
        {"name": "Ver Sitio", "url": "/"},
    ],
    "show_sidebar": True,
    "navigation_expanded": False,
    "order_with_respect_to": ["comercial", "nomina", "facturacion", "auth"], 
}

JAZZMIN_UI_TWEAKS = {
    "theme": "flatly",
    "dark_mode_theme": None,
}