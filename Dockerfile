# Étape 1 : Construction et installation des dépendances
FROM python:3.10-slim as builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# Étape 2 : Image finale légère pour l'exécution
FROM python:3.10-slim

WORKDIR /app

# Créer un utilisateur non-root pour l'exécution sécurisée
RUN useradd -m -r botuser && mkdir -p /app/logs && chown -R botuser:botuser /app

# Récupérer les dépendances installées de l'étape de construction
COPY --from=builder /root/.local /home/botuser/.local
COPY --chown=botuser:botuser . .

# Passer à l'utilisateur non-root
USER botuser

# Mettre à jour le PATH pour inclure le dossier bin des dépendances utilisateur
ENV PATH=/home/botuser/.local/bin:$PATH
ENV PYTHONUNBUFFERED=1

# Port par défaut exposé pour le health check
EXPOSE 8080

CMD ["python", "runner.py"]
