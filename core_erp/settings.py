from pathlib import Path
import os

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = 'django-insecure-cambiar-esto-por-algo-seguro'

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = True

ALLOWED_HOSTS = []

# --- APLICACIONES INSTALADAS ---
INSTALLED_APPS = [
    'jazzmin',              # <--- 1. JAZZMIN SIEMPRE VA PRIMERO
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    
    # Mis Apps
    'comercial',
    
    # Librerías extra
    'weasyprint',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
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
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

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

# --- ARCHIVOS ESTÁTICOS (CSS, JS, IMÁGENES) ---
STATIC_URL = 'static/'
STATICFILES_DIRS = [
    os.path.join(BASE_DIR, 'static'),
]

# Default primary key field type
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# --- CONFIGURACIÓN DE CORREO (GMAIL) ---
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = 'smtp.gmail.com'
EMAIL_PORT = 587
EMAIL_USE_TLS = True
# Asegúrate de poner aquí tus datos reales si los cambiaste
EMAIL_HOST_USER = 'quintakooxtanil@gmail.com' 
EMAIL_HOST_PASSWORD = 'dvdkbizbtrvndxka' # <--- OJO: Revisa que esta sea tu clave de aplicación
DEFAULT_FROM_EMAIL = 'quintakooxtanil@gmail.com'

# --- CONFIGURACIÓN DE JAZZMIN (DISEÑO DEL PANEL) ---
JAZZMIN_SETTINGS = {
    "site_title": "ERP Quinta Ko'ox Tanil",
    "site_header": "Sistema de Eventos",
    "site_brand": "Ko'ox Tanil ERP",
    "welcome_sign": "Bienvenido al Panel de Control",
    "copyright": "Quinta Ko'ox Tanil",
    
    "site_logo": "img/logo.png",
    "login_logo": "img/logo.png",
    
    # Iconos del menú lateral
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
    
    # Menú superior (Top Menu)
    "topmenu_links": [
        {"name": "Inicio",  "url": "admin:index", "permissions": ["auth.view_user"]},
        
        # --- AQUÍ ESTÁ EL BOTÓN DEL CALENDARIO ---
        {"name": "📅 Ver Calendario", "url": "admin_calendario"},
        
        {"name": "Ver Sitio", "url": "/"},
    ],
    
    "show_sidebar": True,
    "navigation_expanded": True,
    "order_with_respect_to": ["comercial", "auth"], 
}

JAZZMIN_UI_TWEAKS = {
    "theme": "flatly", # Tema limpio y moderno
    "dark_mode_theme": None,
}