FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        chromium \
        curl \
        fonts-dejavu-core \
        fonts-liberation \
        tini \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 1000 kiosk \
    && useradd --uid 1000 --gid 1000 --create-home kiosk

COPY scripts/kiosk/chromium-managed-policy.json /etc/chromium/policies/managed/quad-race.json
COPY docker/kiosk/entrypoint.sh /usr/local/bin/quad-kiosk
RUN chmod 0755 /usr/local/bin/quad-kiosk

USER kiosk
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/quad-kiosk"]
