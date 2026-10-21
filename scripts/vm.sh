#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
build_env=$(python3 scripts/build_config.py shell)
eval "$build_env"
case "${1:-}" in
 start)
  mkdir -p .cache .cache/builder-home
  if [ -f .cache/openwrt.img ] && [ "$(cat .cache/vm-cache-key 2>/dev/null || true)" != "$VM_CACHE_KEY" ]; then
   echo 'VM image/config changed; run ./scripts/vm.sh reset to recreate the test guest.' >&2
   exit 1
  fi
  if ! python3 scripts/build_helpers.py check-sha256 .cache/openwrt.img.gz "$VM_IMAGE_SHA256" >/dev/null 2>&1; then
   curl -fL --retry 3 "$VM_URL" -o .cache/openwrt.img.gz.part
   python3 scripts/build_helpers.py check-sha256 .cache/openwrt.img.gz.part "$VM_IMAGE_SHA256"
   mv .cache/openwrt.img.gz.part .cache/openwrt.img.gz
  fi
  # Generate test credentials as the host user; Linux bind mounts preserve ownership.
  [ -f .cache/vm_id ] || ssh-keygen -q -t ed25519 -N '' -f .cache/vm_id
  docker build --build-arg "BASE_IMAGE=$CONTAINER_BASE_IMAGE" -t "$VM_CONTAINER_IMAGE" -f scripts/Dockerfile.vm . > .cache/vm-container-build.log 2>&1
  docker run -d --name vlmcsd-test-vm --user "$(id -u):$(id -g)" -e HOME=/work/.cache/builder-home -e "VM_CACHE_KEY=$VM_CACHE_KEY" -p 127.0.0.1:22222:22222 -p 127.0.0.1:18080:18080 -p 127.0.0.1:11688:11688 -v "$PWD:/work" "$VM_CONTAINER_IMAGE" sh scripts/vm-prepare.sh
  echo 'VM booting. SSH: port 22222; LuCI: http://127.0.0.1:18080'
  ;;
 stop) docker rm -f vlmcsd-test-vm;;
 reset)
  docker rm -f vlmcsd-test-vm 2>/dev/null || true
  rm -f .cache/openwrt.img .cache/rootfs.ext4 .cache/rootfs.offset .cache/vm_known_hosts .cache/vm-initialized .cache/vm-cache-key
  exec "$0" start;;
 ssh) shift; exec ssh -o UserKnownHostsFile=.cache/vm_known_hosts -o StrictHostKeyChecking=accept-new -i .cache/vm_id -p 22222 root@127.0.0.1 "$@";;
 *) echo 'Usage: vm.sh start|stop|reset|ssh [command]' >&2; exit 2;;
esac
