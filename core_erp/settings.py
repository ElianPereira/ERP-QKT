from decouple import config
from pathlib import Path
import os
import dj_database_url # <--- Agregado para el futuro deploy

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = config('SECRET_KEY')

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = config('DEBUG', default=False, cast=bool)

ALLOWED_HOSTS = ['*']
# CSRF_TRUSTED_ORIGINS = ['http://192.168.83.133:8000'] # Puedes descomentar esto si lo necesitas local

# --- APLICACIONES INSTALADAS ---
INSTALLED_APPS = [
    'jazzmin',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    
    # --- NUEVA APP PARA FORMATO DE NÚMEROS (COMAS Y PUNTOS) ---
    'django.contrib.humanize', 

    # Mis Apps
    'comercial',
    
    # Librerías extra
    'weasyprint',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware', # <--- RECOMENDADO: Agregado para ver estilos en deploy
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
        'DIRS': [],
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

# Database
# Configuración híbrida: Si hay URL (nube) usa esa, si no, usa SQLite local
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}
# Esto ayuda al deploy futuro sin romper lo local
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

# --- FORMATO DE NÚMEROS (IMPORTANTE: ESTO PONE LAS COMAS) ---
USE_L10N = False 
USE_THOUSAND_SEPARATOR = True
DECIMAL_SEPARATOR = '.'
THOUSAND_SEPARATOR = ','
NUMBER_GROUPING = 3

# --- ARCHIVOS ESTÁTICOS (CSS, JS, IMÁGENES) ---
STATIC_URL = 'static/'
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles') # Necesario para deploy
STATICFILES_DIRS = [
    os.path.join(BASE_DIR, 'static'),
]
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage' # Necesario para deploy

# Default primary key field type
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# --- CONFIGURACIÓN DE CORREO (GMAIL) ---
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = 'smtp.gmail.com'
EMAIL_PORT = 587
EMAIL_USE_TLS = True
EMAIL_HOST_USER = config('EMAIL_HOST_USER')
EMAIL_HOST_PASSWORD = config('EMAIL_HOST_PASSWORD')
DEFAULT_FROM_EMAIL = 'quintakooxtanil@gmail.com'

# --- CONFIGURACIÓN DE JAZZMIN (DISEÑO DEL PANEL) ---
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
        "comercial.Cliente": "fas fa-address-book",
        "comercial.Cotizacion": "fas fa-file-invoice-dollar",
        "comercial.Insumo": "fas fa-cubes",
        "comercial.Pago": "fas fa-hand-holding-usd",
        "comercial.Producto": "fas fa-box-open",
    },
    "topmenu_links": [
        {"name": "Inicio",  "url": "admin:index", "permissions": ["auth.view_user"]},
        {"name": "📅 Ver Calendario", "url": "ver_calendario"}, # Corregí la URL name a 'ver_calendario'
        {"name": "Ver Sitio", "url": "/"},
    ],
    "show_sidebar": True,
    "navigation_expanded": False,
    "order_with_respect_to": ["comercial", "auth"], 
}

JAZZMIN_UI_TWEAKS = {
    "theme": "flatly",
    "dark_mode_theme": None,
}