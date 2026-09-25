from .base import *

# Fail closed unless deployment explicitly supplies its trusted hosts/origins.
DEBUG = False
# Never sign production JWTs with the repository's development key.
SECRET_KEY = env('SECRET_KEY')
if len(SECRET_KEY) < 50 or SECRET_KEY.startswith('django-insecure-'):
    from django.core.exceptions import ImproperlyConfigured
    raise ImproperlyConfigured('Set a strong, private production SECRET_KEY (at least 50 characters).')
ALLOWED_HOSTS = env.list('ALLOWED_HOSTS', default=['api.mainreport.ir', 'medicdashboard-django.liara.run'])
CORS_ALLOWED_ORIGINS = env.list('CORS_ALLOWED_ORIGINS', default=['https://mainreport.ir', 'https://medicdashboard-nextjs.liara.run'])
CSRF_TRUSTED_ORIGINS = env.list('CSRF_TRUSTED_ORIGINS', default=CORS_ALLOWED_ORIGINS)
CORS_ALLOW_ALL_ORIGINS = False

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
