FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV POETRY_VERSION=2.1.4
ENV POETRY_NO_INTERACTION=1
ENV POETRY_VIRTUALENVS_CREATE=false

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        gcc \
        libpq-dev \
        curl \
        tesseract-ocr \
        tesseract-ocr-eng \
        tesseract-ocr-deu \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir "poetry==$POETRY_VERSION"

COPY pyproject.toml README.md ./

RUN poetry install --no-root

COPY . .

EXPOSE 8000

# Keep the image safe to run without docker-compose as well. The startup script
# applies all pending Alembic revisions before launching Uvicorn.
CMD ["sh", "scripts/start-api.sh"]
