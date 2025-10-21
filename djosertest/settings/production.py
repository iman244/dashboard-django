from .base import *

CSRF_TRUSTED_ORIGINS = [
    "https://medicdashboard-django.liara.run",
    "https://medicdashboard-nextjs.liara.run",
]

ALLOWED_HOSTS = ['*']

CORS_ALLOWED_ORIGINS = [
    "https://medicdashboard-django.liara.run",
    "https://medicdashboard-nextjs.liara.run",
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
