FROM alpine:3.22.1@sha256:4bcff63911fcb4448bd4fdacec207030997caf25e9bea4045fa6c8c44de311d1

RUN apk add --no-cache logrotate=3.21.0-r1

COPY --chmod=0444 agent-backend.conf /etc/logrotate.d/agent-backend
COPY --chmod=0600 crontab /etc/crontabs/root

VOLUME ["/var/lib/logrotate"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD ["pidof", "crond"]

CMD ["crond", "-f", "-l", "2"]
