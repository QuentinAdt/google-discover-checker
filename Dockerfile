FROM python:3.11-slim

# Installation des dépendances système
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    libglib2.0-0 \
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libdbus-1-3 \
    libxkbcommon0 \
    libx11-6 \
    libxcomposite1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    libasound2 \
    libatspi2.0-0 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Créer un utilisateur non-root
RUN useradd -m appuser

# Définir le répertoire de travail
WORKDIR /app

# Copier les fichiers du projet
COPY requirements.txt .

# Installer les dépendances Python
RUN pip install --no-cache-dir -r requirements.txt

# Installer Playwright en tant que root (pour avoir les permissions nécessaires)
RUN playwright install --with-deps chromium

# Copier le reste des fichiers
COPY app.py .

# Changer le propriétaire des fichiers pour l'utilisateur non-root
RUN chown -R appuser:appuser /app

# Changer d'utilisateur
USER appuser

# S'assurer que l'utilisateur appuser a accès aux binaires de Playwright
# Cela garantit que les fichiers de cache sont correctement configurés
RUN python -c "from playwright.sync_api import sync_playwright; print('Playwright test: OK')"

# Exposer le port utilisé par l'application
EXPOSE 5001

# Démarrer l'application
CMD ["python", "app.py"] 