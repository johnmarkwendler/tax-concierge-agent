FROM node:24-slim AS frontend

WORKDIR /frontend

COPY frontend/package.json ./
RUN npm install

COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir .

COPY main.py ./
COPY --from=frontend /frontend/dist ./frontend/dist

CMD exec uvicorn main:app --host 0.0.0.0 --port "${PORT:-8080}"
