# syntax=docker/dockerfile:1
#
# StrmQt's streaming host, with nobody at its screen: a headless KWin with
# StrmQt on it, and a Sunshine that streams that screen to StrmLight.
#
# Everything comes from the projects' own release packages, checked against
# the SHA-256 GitHub records for each. A StrmQt .deb placed in packages/
# is installed instead of the release's, for trying a build before it ships.

FROM docker.io/library/ubuntu:26.04

ARG STRMQT_VERSION=0.9.2
ARG STRMQT_SHA256=
ARG SUNSHINE_VERSION=2026.914.233613
ARG SUNSHINE_SHA256=b8a8ceeeb2b3f30a18a994ad268c94e0c91170f33a864c7aad31eed927ad7d47

ENV DEBIAN_FRONTEND=noninteractive

COPY packages/ /tmp/packages/

# --no-install-recommends keeps KWallet out: a keyring would ask for its
# password on the TV, where nobody can type it, so StrmQt keeps its Emby
# sign-in in its 0600 vault file instead (StrmQt's SecretsStore).
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl; \
    cd /tmp/packages; \
    if ! ls strmqt_*.deb >/dev/null 2>&1; then \
        test -n "$STRMQT_SHA256" || { echo "STRMQT_SHA256 is needed to download StrmQt $STRMQT_VERSION" >&2; exit 1; }; \
        curl -fsSLo "strmqt_${STRMQT_VERSION}.deb" \
            "https://github.com/mpengellyCA/strmQt-linux/releases/download/v${STRMQT_VERSION}/strmqt_${STRMQT_VERSION}-1.ubuntu26.04_amd64.deb"; \
        echo "${STRMQT_SHA256}  strmqt_${STRMQT_VERSION}.deb" | sha256sum -c -; \
    fi; \
    curl -fsSLo sunshine.deb \
        "https://github.com/LizardByte/Sunshine/releases/download/v${SUNSHINE_VERSION}/sunshine_${SUNSHINE_VERSION}-1%2Bubuntu26.04_amd64.deb"; \
    echo "${SUNSHINE_SHA256}  sunshine.deb" | sha256sum -c -; \
    apt-get install -y --no-install-recommends \
        ./strmqt_*.deb ./sunshine.deb \
        kwin-wayland kwin-common libkscreen-bin \
        dbus pipewire pipewire-bin wireplumber \
        mesa-va-drivers mesa-vulkan-drivers libgl1-mesa-dri \
        fonts-noto-core fonts-noto-color-emoji \
        procps libcap2-bin; \
    # KWin is installed with CAP_SYS_NICE, for a realtime thread, and a
    # program with a capability outside Docker's default set cannot even be
    # started. The container's KWin goes without.
    setcap -r "$(command -v kwin_wayland)"; \
    # The capture StrmQt's Sunshine uses (StreamingHost::sunshineCapturesKWin).
    grep -q zkde_screencast_unstable_v1 "$(command -v sunshine)"; \
    rm -f /usr/share/dbus-1/services/org.kde.kwalletd*.service; \
    apt-get purge -y curl; apt-get autoremove -y; \
    command -v kwin_wayland; \
    rm -rf /var/lib/apt/lists/* /tmp/packages

# Ubuntu's image has a user 1000 of its own; the host's is strmqt.
RUN set -eux; \
    if id ubuntu >/dev/null 2>&1; then userdel -r ubuntu; fi; \
    groupadd --force --system render; \
    useradd --create-home --uid 1000 --shell /bin/bash --groups video,render strmqt; \
    install -d -m 0700 -o strmqt -g strmqt /run/user/1000

# entrypoint.sh is executable in git, and COPY keeps its mode.
COPY entrypoint.sh /usr/local/bin/strmqt-headless

# What a desktop session would give the host, set for `docker compose exec`
# as well: `strmqt admin` finds the running host's sockets there.
ENV LANG=C.UTF-8 \
    XDG_RUNTIME_DIR=/run/user/1000 \
    DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus \
    STRMQT_HOST_FOREGROUND=1 \
    # Chromium's sandbox needs user namespaces, which Docker's default
    # seccomp profile refuses; the container is the sandbox here.
    QTWEBENGINE_DISABLE_SANDBOX=1

USER strmqt
WORKDIR /home/strmqt
VOLUME /home/strmqt

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s \
    CMD strmqt admin status --json | grep -q '"running": true'

ENTRYPOINT ["/usr/local/bin/strmqt-headless"]
