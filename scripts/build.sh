#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
build_env=$(python3 scripts/build_config.py shell)
eval "$build_env"
mkdir -p .cache dist .secrets
chmod 700 .secrets
command -v docker >/dev/null || { echo 'Install/start OrbStack with Docker support' >&2; exit 1; }
docker info >/dev/null 2>&1 || { echo 'Start OrbStack, then rerun this command' >&2; exit 1; }
download_checked() {
 local url=$1 destination=$2 expected=$3
 if [ -f "$destination" ] && printf '%s  %s\n' "$expected" "$destination" | shasum -a 256 -c - >/dev/null 2>&1; then
  return
 fi
 curl -fL --retry 3 "$url" -o "$destination.part"
 printf '%s  %s\n' "$expected" "$destination.part" | shasum -a 256 -c -
 mv "$destination.part" "$destination"
}
download_checked "$SDK_URL/$SDK_FILE" .cache/sdk.tar.zst "$SDK_SHA256"
curl -fsSL "$SDK_URL/sha256sums" -o .cache/sha256sums
curl -fsSL "$SDK_URL/sha256sums.asc" -o .cache/sha256sums.asc
curl -fsSL "$OPENWRT_KEY_URL" -o .cache/openwrt-release.asc
download_checked "$UPSTREAM_ARCHIVE_URL" .cache/upstream.tar.gz "$UPSTREAM_SHA256"
docker build --platform "$BUILDER_PLATFORM" --build-arg "BASE_IMAGE=$CONTAINER_BASE_IMAGE" -t "$BUILDER_IMAGE" -f scripts/Dockerfile . > .cache/container-build.log 2>&1
docker run --rm --platform "$BUILDER_PLATFORM" -v "$PWD:/work" "$BUILDER_IMAGE" bash -ec '
 build_env=$(python3 scripts/build_config.py shell)
 eval "$build_env"
 mkdir -p .cache/gnupg; chmod 700 .cache/gnupg
 gpg --homedir .cache/gnupg --import .cache/openwrt-release.asc
 gpg --homedir .cache/gnupg --with-colons --fingerprint | grep -Fq ":$OPENWRT_KEY_FINGERPRINT:"
 gpg --homedir .cache/gnupg --verify .cache/sha256sums.asc .cache/sha256sums
 python3 scripts/build_config.py json --sdk-checksums .cache/sha256sums > .cache/build-config.json
 if [ "$(cat .cache/sdk/.sdk-sha256 2>/dev/null || true)" != "$SDK_SHA256" ]; then
  rm -rf .cache/sdk
  mkdir -p .cache/sdk
  tar --zstd -xf .cache/sdk.tar.zst --strip-components=1 -C .cache/sdk
  printf "%s\n" "$SDK_SHA256" > .cache/sdk/.sdk-sha256
 fi
 if [ ! -f .cache/sdk/.prepared ]; then
  cd .cache/sdk
  ./scripts/feeds update base packages luci
  ./scripts/feeds install uci jsonfilter libubox luci-base rpcd ucode
  touch .prepared
 fi
' > .cache/sdk-prepare.log 2>&1
docker run --rm --platform "$BUILDER_PLATFORM" -v "$PWD:/work" "$BUILDER_IMAGE" bash scripts/inside-build.sh > .cache/build.log 2>&1
docker image inspect "$BUILDER_IMAGE" --format '{{.Id}}' > .cache/builder-image.txt
docker run --rm --platform "$BUILDER_PLATFORM" -v "$PWD:/work" "$BUILDER_IMAGE" python3 scripts/assemble.py
printf 'Release: %s/dist/release\nBuild log: %s/.cache/build.log\n' "$PWD" "$PWD"
