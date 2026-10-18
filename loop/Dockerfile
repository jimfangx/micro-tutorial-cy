ARG BASE=ubuntu:24.04
FROM ${BASE}
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    build-essential ca-certificates git curl libglib2.0-0 libnuma1 libtinfo6 \
    libelf1 libzstd1 zlib1g libgomp1 && rm -rf /var/lib/apt/lists/*
RUN mkdir -p /workspace /output /opt/loop /home/ray && chmod 1777 /home/ray
ENV HOME=/home/ray
WORKDIR /workspace
