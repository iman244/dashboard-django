#!/bin/bash

# Medical Dashboard Django App Deployment Script
# This script will be run on the server to deploy the application

set -e  # Exit on any error

echo "🚀 Starting Medical Dashboard Django deployment..."

# Create app directory if it doesn't exist
APP_DIR="/opt/medical-dashboard"
mkdir -p $APP_DIR
cd $APP_DIR

# Set environment variables for Docker Compose
echo "🔧 Setting up environment variables..."
export DB_NAME=${DB_NAME:-medicdashboard}
export DB_USER=${DB_USER:-postgres}
export DB_PASSWORD=${DB_PASSWORD:-qwer123456}
export SECRET_KEY=${SECRET_KEY:-your-super-secret-key-change-this-in-production}
export DEBUG=${DEBUG:-False}
export ALLOWED_HOSTS=${ALLOWED_HOSTS:-87.107.111.36,localhost,127.0.0.1}

echo "✅ Environment variables configured"

# Stop existing containers
echo "🛑 Stopping existing containers..."
docker-compose down || true

# Pull latest changes (if using git)
echo "📥 Pulling latest changes..."
if [ -d ".git" ]; then
    git pull origin main
else
    echo "⚠️  No git repository found. Make sure code is updated manually."
fi

# Build and start containers
echo "🔨 Building and starting containers..."
docker-compose up -d --build

# Wait for database to be ready
echo "⏳ Waiting for database to be ready..."
sleep 10

# Run Django migrations
echo "🗄️  Running Django migrations..."
docker-compose exec web python manage.py migrate

# Collect static files
echo "📦 Collecting static files..."
docker-compose exec web python manage.py collectstatic --noinput

# Create superuser (optional - uncomment if needed)
# echo "👤 Creating superuser..."
# docker-compose exec web python manage.py createsuperuser

echo "✅ Deployment completed successfully!"
echo "🌐 Your app should be available at: http://$(hostname -I | awk '{print $1}'):8000"
echo "📊 Check logs with: docker-compose logs -f"
