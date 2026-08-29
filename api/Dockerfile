# Build context is the repo root (see docker-compose.yml). The project is
# installed as a package, so `api.auth`, `scheduler.model` and the rest resolve
# by their real dotted names in the image, a checkout, and under pytest alike.
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

# Unbuffered, or the startup lines (which store was chosen, whether the demo
# week was seeded) sit in a pipe buffer and never reach the platform's logs.
# Set here rather than at the top, so editing it does not invalidate the cached
# OR-Tools layer.
#
# PANELIST_HOST is overridable because private service-to-service networking on
# some platforms (Railway's `*.railway.internal`) resolves over IPv6, where a
# process bound to 0.0.0.0 is unreachable. Note that `::` is IPv6-only, not
# dual-stack — asyncio leaves IPV6_V6ONLY set, so it refuses IPv4 outright.
# Use it only behind a private IPv6 network.
ENV PYTHONUNBUFFERED=1
ENV PORT=8000
ENV PANELIST_HOST=0.0.0.0
# Shell form so $PORT is expanded at runtime, and `exec` so uvicorn replaces
# the shell as PID 1 and receives SIGTERM rather than being killed.
CMD exec uvicorn api.main:app --host ${PANELIST_HOST} --port ${PORT}
