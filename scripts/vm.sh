#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
action=''
while [ "$#" -gt 0 ]; do
 case "$1" in
  --profile|--target)
   [ "$#" -ge 2 ] || { echo "Missing value for $1" >&2; exit 2; }
   case "$1" in --profile) export OPENWRT_PROFILE=$2;; --target) export OPENWRT_TARGET=$2;; esac
   shift 2;;
  start|stop|reset|ssh) action=$1; shift; [ "$action" != ssh ] || break;;
  *) break;;
 esac
done
build_env=$(python3 scripts/build_config.py shell)
eval "$build_env"
if [ "$action" != stop ] && [ "$OPENWRT_TARGET" != x86/64 ] && [ "$OPENWRT_TARGET" != armsr/armv8 ]; then
 echo 'Full-system VM supports x86/64 or armsr/armv8; physical-board targets use user-mode tests.' >&2
 exit 2
fi
case "$action" in
 start)
  mkdir -p "$VM_PATH" .cache/builder-home
  if [ -f "$VM_PATH/openwrt.img" ] && [ "$(cat "$VM_PATH/vm-cache-key" 2>/dev/null || true)" != "$VM_CACHE_KEY" ]; then
   echo 'VM image/config changed; run ./scripts/vm.sh reset to recreate the test guest.' >&2
   exit 1
  fi
  download() {
   local url=$1 path=$2 checksum=$3
   if ! python3 scripts/build_helpers.py check-sha256 "$path" "$checksum" >/dev/null 2>&1; then
    curl -fL --retry 3 "$url" -o "$path.part"
    python3 scripts/build_helpers.py check-sha256 "$path.part" "$checksum"
    mv "$path.part" "$path"
   fi
  }
  download "$VM_URL" "$VM_PATH/openwrt.img.gz" "$VM_IMAGE_SHA256"
  if [ "$OPENWRT_TARGET" = armsr/armv8 ]; then
   download "$SDK_URL/$VM_KERNEL_FILE" "$VM_PATH/kernel.bin" "$VM_KERNEL_SHA256"
  fi
  [ -f "$VM_PATH/vm_id" ] || ssh-keygen -q -t ed25519 -N '' -f "$VM_PATH/vm_id"
  docker build --platform "$BUILDER_PLATFORM" --build-arg "BASE_IMAGE=$CONTAINER_BASE_IMAGE" -t "$VM_CONTAINER_IMAGE" -f scripts/Dockerfile.vm . > "$VM_PATH/vm-container-build.log" 2>&1
  docker run -d --platform "$BUILDER_PLATFORM" --name vlmcsd-test-vm --user "$(id -u):$(id -g)" -e HOME=/work/.cache/builder-home -e "VM_CACHE_KEY=$VM_CACHE_KEY" -e "VM_PATH=$VM_PATH" -e "OPENWRT_TARGET=$OPENWRT_TARGET" -p 127.0.0.1:22222:22222 -p 127.0.0.1:18080:18080 -p 127.0.0.1:11688:11688 -v "$PWD:/work" "$VM_CONTAINER_IMAGE" sh scripts/vm-prepare.sh
  echo 'VM booting. SSH: port 22222; LuCI: http://127.0.0.1:18080'
  ;;
 stop) docker rm -f vlmcsd-test-vm;;
 reset)
  docker rm -f vlmcsd-test-vm 2>/dev/null || true
  rm -f "$VM_PATH/openwrt.img" "$VM_PATH/rootfs.ext4" "$VM_PATH/rootfs.offset" "$VM_PATH/vm_known_hosts" "$VM_PATH/vm-initialized" "$VM_PATH/vm-cache-key"
  exec "$0" start;;
 ssh) exec ssh -o "UserKnownHostsFile=$VM_PATH/vm_known_hosts" -o StrictHostKeyChecking=accept-new -i "$VM_PATH/vm_id" -p 22222 root@127.0.0.1 "$@";;
 *) echo 'Usage: vm.sh [--profile series] [--target target/subtarget] start|stop|reset|ssh [command]' >&2; exit 2;;
esac
