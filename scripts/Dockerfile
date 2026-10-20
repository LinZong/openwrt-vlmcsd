ARG BASE_IMAGE
FROM ${BASE_IMAGE}
ENV DEBIAN_FRONTEND=noninteractive TZ=UTC
RUN apt-get update && apt-get install -y --no-install-recommends build-essential ca-certificates curl wget git gawk gettext libncurses-dev libssl-dev zlib1g-dev unzip file rsync python3 python3-venv perl patch diffutils zstd bzip2 xz-utils flex bison pkg-config swig libelf-dev autoconf automake libtool openssl gnupg shellcheck && rm -rf /var/lib/apt/lists/*
RUN userdel ubuntu && useradd -m -u 1000 builder
USER builder
WORKDIR /work
