# Build context is the repo root (see docker-compose.yml), so that the
# generator/scheduler/replanner packages land in the image rather than
# existing only via compose volume mounts.
FROM python:3.11-slim

WORKDIR /app

COPY api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY api/ .
COPY scheduler/ ./scheduler/
COPY replanner/ ./replanner/
COPY generator/ ./generator/

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
