ARG BASE=ghcr.io/rust-cross/rust-musl-cross:armv7-musleabihf
FROM ${BASE}
RUN apt-get update && apt-get install -y --no-install-recommends pkg-config bzip2 curl ca-certificates git \
    && rm -rf /var/lib/apt/lists/*
# Pinned Rust release (versions.toml). static.rust-lang.org sits on Fastly, which some ISPs reach
# only intermittently, hence the retries.
ARG RUST_TOOLCHAIN=stable
RUN for i in 1 2 3 4 5 6 7 8 9 10; do \
      rustup toolchain install "$RUST_TOOLCHAIN" --profile minimal \
        --target armv7-unknown-linux-musleabihf --target x86_64-unknown-linux-gnu && break; \
      echo "rustup attempt $i failed, retrying"; sleep 5; \
    done \
    && rustup default "$RUST_TOOLCHAIN" && rustc --version
ARG ALSA_VER=1.2.16.1
# Where the speaker keeps alsa-lib's configuration. Only a fallback: lithify-agent points
# ALSA_CONFIG_DIR at <install dir>/alsa when it starts librespot.
ARG ALSA_CONFIG_DIR=/lsync/lithify/alsa
# Static alsa-lib for the speaker (Cortex-A7, hard-float). Without -march=armv7-a the atomics
# become __sync_* calls that static musl cannot resolve. The configuration files are collected
# in /opt/alsa-conf/alsa for the bundle.
RUN curl -fsSL https://www.alsa-project.org/files/pub/lib/alsa-lib-${ALSA_VER}.tar.bz2 | tar xj -C /tmp \
    && cd /tmp/alsa-lib-${ALSA_VER} \
    && ./configure --host=armv7-unknown-linux-musleabihf CC=armv7-unknown-linux-musleabihf-gcc \
         CFLAGS="-O2 -march=armv7-a -mtune=cortex-a7 -mfpu=neon-vfpv4 -mfloat-abi=hard" \
         --prefix=/opt/alsa --with-configdir=${ALSA_CONFIG_DIR} \
         --enable-static --disable-shared --disable-python --disable-alisp --disable-topology --disable-ucm \
    && make -j"$(nproc)" && make install \
    && mkdir -p /opt/alsa-conf && cp -a ${ALSA_CONFIG_DIR} /opt/alsa-conf/alsa \
    && rm -rf /tmp/alsa-lib-${ALSA_VER}
ENV TARGET_PKG_CONFIG_LIBDIR=/opt/alsa/lib/pkgconfig \
    TARGET_PKG_CONFIG_PATH=/opt/alsa/lib/pkgconfig \
    TARGET_PKG_CONFIG_SYSROOT_DIR= \
    PKG_CONFIG_ALL_STATIC=1
RUN git config --system --add safe.directory '*'
