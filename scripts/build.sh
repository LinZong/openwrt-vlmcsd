#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
while [ "$#" -gt 0 ]; do
 case "$1" in
  --profile|--target)
   [ "$#" -ge 2 ] || { echo "Missing value for $1" >&2; exit 2; }
   case "$1" in --profile) export OPENWRT_PROFILE=$2;; --target) export OPENWRT_TARGET=$2;; esac
   shift 2;;
  *) echo 'Usage: build.sh [--profile 23.05|24.10|25.12] [--target target/subtarget]' >&2; exit 2;;
 esac
done
build_env=$(python3 scripts/build_config.py shell)
eval "$build_env"
python3 scripts/build_helpers.py jobs >/dev/null
trap 'printf "Build failed; inspect %s/{container-build,sdk-prepare,build}.log\n" "$SDK_METADATA_PATH" >&2' ERR
mkdir -p .cache dist .secrets "$SDK_METADATA_PATH"
chmod 700 .secrets
command -v docker >/dev/null || { echo 'Install Docker CLI and a Docker Engine-compatible daemon.' >&2; exit 1; }
docker info >/dev/null 2>&1 || { echo 'Docker daemon is unavailable; check the active Docker context/DOCKER_HOST.' >&2; exit 1; }
download_checked() {
 local url=$1 destination=$2 expected=$3
 if [ -f "$destination" ] && python3 scripts/build_helpers.py check-sha256 "$destination" "$expected" >/dev/null 2>&1; then
  printf 'Reusing verified download: %s\n' "$destination"
  return
 fi
 curl -fL --retry 3 "$url" -o "$destination.part"
 python3 scripts/build_helpers.py check-sha256 "$destination.part" "$expected"
 mv "$destination.part" "$destination"
}
download_checked "$SDK_URL/$SDK_FILE" "$SDK_ARCHIVE_PATH" "$SDK_SHA256"
# Use the invoking user's IDs for native Linux bind mounts as well as macOS Docker.
# OpenWrt refuses root by default; allow it only inside this disposable builder.
builder_user="$(id -u):$(id -g)"
mkdir -p .cache/builder-home
docker build --platform "$BUILDER_PLATFORM" --build-arg "BASE_IMAGE=$CONTAINER_BASE_IMAGE" -t "$BUILDER_IMAGE" -f scripts/Dockerfile . > "$SDK_METADATA_PATH/container-build.log" 2>&1
container=(docker run --rm --platform "$BUILDER_PLATFORM" --user "$builder_user" -e HOME=/work/.cache/builder-home -e "OPENWRT_PROFILE=${OPENWRT_PROFILE:-}" -e "OPENWRT_TARGET=$OPENWRT_TARGET" -e "BUILD_JOBS=${BUILD_JOBS:-}" -v "$PWD:/work" "$BUILDER_IMAGE")
printf 'Preparing OpenWrt %s SDK (%s)\n' "$OPENWRT_VERSION" "$PACKAGE_FORMAT"
"${container[@]}" bash -ec '
 if [ "$(id -u)" = 0 ]; then export FORCE=1; fi
 build_env=$(python3 scripts/build_config.py shell)
 eval "$build_env"
 python3 scripts/upstream.py
 python3 scripts/build_helpers.py sdk-metadata
 python3 scripts/build_config.py json --sdk-checksums "$SDK_METADATA_PATH/sha256sums" > "$SDK_METADATA_PATH/build-config.json"
 if [ "$(cat "$SDK_PATH/.sdk-sha256" 2>/dev/null || true)" != "$SDK_SHA256" ]; then
  rm -rf "$SDK_PATH"
  mkdir -p "$SDK_PATH"
  tar -xf "$SDK_ARCHIVE_PATH" --strip-components=1 -C "$SDK_PATH"
  printf "%s\n" "$SDK_SHA256" > "$SDK_PATH/.sdk-sha256"
 fi
 if [ ! -f "$SDK_PATH/.prepared" ]; then
  cd "$SDK_PATH"
  if [ "$PACKAGE_FORMAT" = apk ]; then
   ./scripts/feeds update base packages luci
  else
   ./scripts/feeds update packages luci
  fi
  ./scripts/feeds install uci jsonfilter libubox luci-base rpcd ucode
  touch .prepared
 fi
' > "$SDK_METADATA_PATH/sdk-prepare.log" 2>&1
printf 'Compiling OpenWrt %s packages\n' "$OPENWRT_VERSION"
"${container[@]}" bash -ec 'if [ "$(id -u)" = 0 ]; then export FORCE=1; fi; exec bash scripts/inside-build.sh' > "$SDK_METADATA_PATH/build.log" 2>&1
docker image inspect "$BUILDER_IMAGE" --format '{{.Id}}' > "$SDK_METADATA_PATH/builder-image.txt"
"${container[@]}" python3 scripts/assemble.py
printf 'Release: %s/%s\nBuild log: %s/%s/build.log\n' "$PWD" "$RELEASE_PATH" "$PWD" "$SDK_METADATA_PATH"
