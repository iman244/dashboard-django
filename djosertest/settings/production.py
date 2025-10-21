from .base import *

CSRF_TRUSTED_ORIGINS = ["http://194.60.231.201:8001", "http://194.60.231.201:3001"]

ALLOWED_HOSTS = ['*']

CORS_ALLOWED_ORIGINS = [
    "http://194.60.231.201:3001",
]

STATIC_URL = 'static/'
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': env('DB_NAME', default='medicdashboard'),
        'USER': env('DB_USER', default='postgres'),
        'PASSWORD': env('DB_PASSWORD', default='qwer123456'),
        'HOST': env('DB_HOST', default='localhost'),
        'PORT': env('DB_PORT', default='5432'),
    }
}
