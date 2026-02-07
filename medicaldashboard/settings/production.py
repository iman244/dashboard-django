from .base import *

CSRF_TRUSTED_ORIGINS = [
    "https://medicdashboard-django.liara.run",
    "https://medicdashboard-nextjs.liara.run",
    "https://mainreport.ir",
    "http://87.107.111.36:8000",  # Django API
    "http://87.107.111.36:3000",  # Next.js app
    "http://localhost:3000",      # Local development
]

ALLOWED_HOSTS = ['*']

CORS_ALLOWED_ORIGINS = [
    "https://medicdashboard-django.liara.run",
    "https://medicdashboard-nextjs.liara.run",
    "https://mainreport.ir",
    "http://87.107.111.36:3000",  # Next.js app on same server
    "http://localhost:3000",      # Local development
]

# Allow all origins for development (you can restrict this later)
CORS_ALLOW_ALL_ORIGINS = True

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
