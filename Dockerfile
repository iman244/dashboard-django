# 1. Use official Python image
FROM python:3.11-slim

# 2. Set work directory
WORKDIR /app

# 3. Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# 4. Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 5. Copy project files
COPY . .

# 6. Set environment variables (optional, can also be set at runtime)
# ENV DJANGO_SETTINGS_MODULE=goldenhorde.settings
# ENV PYTHONUNBUFFERED=1
ENV DJANGO_ENV=production
ENV ENVIRONMENT=production

# 7. Collect static files with a disposable key scoped to this process only.
# Runtime must supply its own private SECRET_KEY; no build key is persisted.
RUN python -c 'import os, secrets, subprocess, sys; os.environ["SECRET_KEY"] = secrets.token_urlsafe(64); subprocess.run([sys.executable, "manage.py", "collectstatic", "--noinput"], check=True)'

# 8. Expose port (change if you use a different port)
EXPOSE 8000

# 9. Start server (using Daphne for ASGI, or use gunicorn for WSGI)
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "medicaldashboard.wsgi:application"]
