import os
from pathlib import Path

import dj_database_url
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / '.env')
IS_VERCEL = os.getenv('VERCEL') == '1'

SECRET_KEY = os.getenv('SECRET_KEY', 'changeme-in-production')
DEBUG = os.getenv('DEBUG', 'True').lower() == 'true'
ALLOWED_HOSTS = [host.strip() for host in os.getenv('ALLOWED_HOSTS', '127.0.0.1,localhost,.onrender.com,.vercel.app').split(',') if host.strip()]
CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        'CSRF_TRUSTED_ORIGINS',
        'http://127.0.0.1,http://localhost,https://*.onrender.com,https://*.vercel.app',
    ).split(',')
    if origin.strip()
]

# Auto-include hosting domains when available (Render/Vercel)
render_host = (os.getenv('RENDER_EXTERNAL_HOSTNAME') or '').strip()
vercel_url = (os.getenv('VERCEL_URL') or '').strip()
for dyn_host in (render_host, vercel_url):
    if dyn_host and dyn_host not in ALLOWED_HOSTS:
        ALLOWED_HOSTS.append(dyn_host)
    if dyn_host:
        dyn_origin = f'https://{dyn_host}'
        if dyn_origin not in CSRF_TRUSTED_ORIGINS:
            CSRF_TRUSTED_ORIGINS.append(dyn_origin)

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'accounts',
    'core',
    'cadastros',
    'compras',
    'financeiro',
    'licencas',
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
    'licencas.middleware.LicencaAtivaMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
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

WSGI_APPLICATION = 'config.wsgi.application'

DATABASES = {
    'default': dj_database_url.parse(
        os.getenv('DATABASE_URL', f"sqlite:///{BASE_DIR / 'db.sqlite3'}"),
        conn_max_age=600,
        ssl_require=os.getenv('DB_SSL_REQUIRE', 'True' if IS_VERCEL else 'False').lower() == 'true',
    )
}

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'pt-br'
TIME_ZONE = os.getenv('TIME_ZONE', 'America/Cuiaba')
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_STORAGE = (
    'django.contrib.staticfiles.storage.StaticFilesStorage'
    if IS_VERCEL
    else 'whitenoise.storage.CompressedManifestStaticFilesStorage'
)
WHITENOISE_USE_FINDERS = IS_VERCEL

MEDIA_URL = 'media/'
MEDIA_ROOT = BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
AUTH_USER_MODEL = 'accounts.User'
LOGIN_REDIRECT_URL = '/dashboard/'
LOGOUT_REDIRECT_URL = '/'

# Allow embedding internal CRUD pages in same-origin iframes (used by "+" quick-create modal).
X_FRAME_OPTIONS = 'SAMEORIGIN'

STRIPE_PUBLIC_KEY = os.getenv('STRIPE_PUBLIC_KEY', '')
STRIPE_SECRET_KEY = os.getenv('STRIPE_SECRET_KEY', '')
STRIPE_WEBHOOK_SECRET = os.getenv('STRIPE_WEBHOOK_SECRET', '')
STRIPE_PRICE_ID = os.getenv('STRIPE_PRICE_ID', '')
LICENCA_BILLING_MODE = (os.getenv('LICENCA_BILLING_MODE', 'manual') or 'manual').strip().lower()
LICENCA_MANUAL_MODE = LICENCA_BILLING_MODE == 'manual'
LICENCA_STRIPE_MODE = LICENCA_BILLING_MODE == 'stripe'
LICENCA_ASAAS_MODE = LICENCA_BILLING_MODE == 'asaas'
ADMIN_DEFAULT_USERNAME = os.getenv('ADMIN_DEFAULT_USERNAME', 'grdados.oficial@gmail.com')
ADMIN_DEFAULT_EMAIL = os.getenv('ADMIN_DEFAULT_EMAIL', 'grdados.oficial@gmail.com')
ADMIN_DEFAULT_PASSWORD = os.getenv('ADMIN_DEFAULT_PASSWORD', 'Ecs*120208')

SALARIO_MINIMO_VIGENTE = os.getenv('SALARIO_MINIMO_VIGENTE', '1621.00')
LICENCA_PERCENTUAL_SALARIO_MINIMO = os.getenv('LICENCA_PERCENTUAL_SALARIO_MINIMO', '0.29')
LICENCA_DESCONTO_ANUAL = os.getenv('LICENCA_DESCONTO_ANUAL', '0.08')
STRIPE_PRICE_ID_SEMESTRAL = os.getenv('STRIPE_PRICE_ID_SEMESTRAL', '')
STRIPE_PRICE_ID_ANUAL = os.getenv('STRIPE_PRICE_ID_ANUAL', '')
ASAAS_API_KEY = os.getenv('ASAAS_API_KEY', '').strip()
ASAAS_BASE_URL = os.getenv('ASAAS_BASE_URL', 'https://sandbox.asaas.com/api/v3').strip()
ASAAS_WEBHOOK_TOKEN = os.getenv('ASAAS_WEBHOOK_TOKEN', '').strip()
DEFAULT_FROM_EMAIL = os.getenv('DEFAULT_FROM_EMAIL', 'grdados.oficial@gmail.com')
EMAIL_HOST = os.getenv('EMAIL_HOST', 'smtp.gmail.com')
EMAIL_PORT = int(os.getenv('EMAIL_PORT', '587'))
EMAIL_HOST_USER = os.getenv('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.getenv('EMAIL_HOST_PASSWORD', '')
EMAIL_USE_TLS = (os.getenv('EMAIL_USE_TLS', 'True').lower() == 'true')

# Email backend:
# - Respect explicit EMAIL_BACKEND when provided.
# - If SMTP credentials are configured, default to SMTP (even in DEBUG) to allow real delivery tests.
# - Fallback to console backend only when DEBUG and no SMTP credentials are present.
EMAIL_BACKEND = os.getenv('EMAIL_BACKEND', '').strip()
if not EMAIL_BACKEND:
    if EMAIL_HOST_USER and EMAIL_HOST_PASSWORD:
        EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
    else:
        EMAIL_BACKEND = (
            'django.core.mail.backends.console.EmailBackend'
            if DEBUG
            else 'django.core.mail.backends.smtp.EmailBackend'
        )

# Production hardening (Render / Vercel)
if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
