FROM --platform=linux/amd64 debian:bookworm-slim@sha256:7c7b2c966bc9ee8cedfeef67e0e279108992c77681fa595db4a9d65c06ccc587
# Signed, timestamped Debian snapshots and exact installed package versions.
COPY packages.lock /opt/p2812-builder/packages.lock
RUN rm -f /etc/apt/sources.list /etc/apt/sources.list.d/debian.sources && \
    printf '%s\n' \
      'deb [check-valid-until=no] http://snapshot.debian.org/archive/debian/20261010T000000Z/ bookworm main' \
      'deb [check-valid-until=no] http://snapshot.debian.org/archive/debian/20261010T000000Z/ bookworm-updates main' \
      'deb [check-valid-until=no] http://snapshot.debian.org/archive/debian-security/20261010T000000Z/ bookworm-security main' > /etc/apt/sources.list && \
    apt-get -o Acquire::Retries=2 update && \
    awk '{print $1 "=" $2}' /opt/p2812-builder/packages.lock | \
      xargs apt-get install -y --no-install-recommends --allow-downgrades && \
    dpkg-query -W > /opt/p2812-builder/installed-packages.txt && \
    diff -u /opt/p2812-builder/packages.lock /opt/p2812-builder/installed-packages.txt && \
    rm -rf /var/lib/apt/lists/*
WORKDIR /work
