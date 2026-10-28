FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

# Polices des visuels PNG générés sur le serveur (publication Instagram, Facebook)
RUN apt-get update && apt-get install -y --no-install-recommends fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# Versions figées et vérifiées (empreintes) : une reconstruction installe exactement ce
# qui a été testé. Mise à jour : voir « Mettre à jour les dépendances » dans le README.
COPY requirements.lock ./
RUN pip install --no-cache-dir --upgrade pip setuptools \
    && pip install --no-cache-dir --require-hashes -r requirements.lock
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir --no-deps .

COPY config ./config
COPY knowledge ./knowledge

RUN useradd --create-home agent && mkdir -p /app/exports && chown agent /app/exports
USER agent

EXPOSE 8000
CMD ["ibig-agent", "serve", "--host", "0.0.0.0", "--port", "8000"]
