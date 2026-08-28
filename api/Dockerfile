# Build context is the repo root (see docker-compose.yml). The whole project is
# installed as a package, so `api.auth`, `scheduler.model` etc. resolve by their
# real dotted names — the image layout no longer has to be replicated by hand
# for imports to work, and the app runs identically here, from a clean checkout,
# and under pytest.
FROM python:3.11-slim

WORKDIR /app

# Dependencies first, from requirements.txt alone, so editing source does not
# invalidate the (slow) ortools install layer.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# --no-deps: everything is already installed above; this step only puts the
# packages on the path.
COPY . .
RUN pip install --no-cache-dir --no-deps -e .

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
