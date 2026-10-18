ARG BASE=ubuntu:24.04
FROM ${BASE}
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    build-essential ca-certificates git curl rsync libglib2.0-0 libnuma1 libtinfo6 \
    libelf1 libzstd1 zlib1g libgomp1 && rm -rf /var/lib/apt/lists/*
ARG CONDA_ROOT
ARG WORKER_UID=1000
RUN mkdir -p /workspace /home/ray && chown -R ${WORKER_UID}:${WORKER_UID} /workspace /home/ray \
    && printf 'export PATH=%s/bin:%s/riscv-tools/bin:/usr/local/bin:/usr/bin:/bin\n' "$CONDA_ROOT" "$CONDA_ROOT" > /etc/profile.d/chia.sh
ENV PATH=${CONDA_ROOT}/bin:${CONDA_ROOT}/riscv-tools/bin:${PATH}
USER ${WORKER_UID}:${WORKER_UID}
ENV HOME=/home/ray
WORKDIR /workspace
