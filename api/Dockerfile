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

# Unbuffered, or the startup lines — which store was chosen, whether the demo
# week was seeded — sit in a pipe buffer and never reach the platform's log
# view. On a container that is the difference between diagnosing a bad
# DATABASE_URL in ten seconds and guessing.
#
# Set here rather than at the top of the file so that editing it does not
# invalidate the cached layer that installs OR-Tools — a several-minute
# rebuild for a variable that only matters at runtime.

# Shell form on purpose: a hosted platform assigns the port at runtime through
# $PORT, and the exec form would pass the literal string "$PORT" to uvicorn.
#
# HOST is overridable because private service-to-service networking on some
# platforms (Railway's `*.railway.internal`) resolves over IPv6, where a
# process bound to 0.0.0.0 is unreachable with nothing in the logs to say so.
#
# PANELIST_HOST=:: is IPv6-ONLY, not dual-stack — asyncio does not set
# IPV6_V6ONLY off, so binding :: refuses IPv4 outright (verified: 127.0.0.1
# connection-refused, [::1] fine). Use it only when the service is reached
# exclusively over a private IPv6 network; leave the default for anything
# behind an IPv4 ingress, which includes every public domain here.
ENV PYTHONUNBUFFERED=1
ENV PORT=8000
ENV PANELIST_HOST=0.0.0.0
# `exec` so uvicorn replaces the shell as PID 1 and actually receives SIGTERM;
# without it a platform's stop signal goes to /bin/sh and the container is
# eventually killed rather than shut down.
CMD exec uvicorn api.main:app --host ${PANELIST_HOST} --port ${PORT}
