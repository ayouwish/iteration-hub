FROM python:3.12-alpine

RUN apk add --no-cache git openssh-client ca-certificates
RUN git config --system --add safe.directory /data

WORKDIR /app

COPY pm.py /app/pm.py

ENV PM_TOKEN=change-me
EXPOSE 8787
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s \
  CMD wget -qO- http://127.0.0.1:8787/healthz || exit 1

CMD ["python", "pm.py", "--roadmap", "/data", "serve", "--host", "0.0.0.0", "--port", "8787"]
