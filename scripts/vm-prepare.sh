#!/bin/sh
set -eu
cd /work
VM_PATH=${VM_PATH:-.cache}
OPENWRT_TARGET=${OPENWRT_TARGET:-x86/64}
export VM_PATH
[ -f "$VM_PATH"/vm_id ] || ssh-keygen -q -t ed25519 -N '' -f "$VM_PATH"/vm_id
if [ ! -f "$VM_PATH"/vm-initialized ] || [ ! -f "$VM_PATH"/openwrt.img ]; then
if [ ! -f "$VM_PATH"/openwrt.img ]; then
 rc=0
 gzip -dc "$VM_PATH"/openwrt.img.gz > "$VM_PATH"/openwrt.img.part || rc=$?
 # Some authenticated OpenWrt images append metadata after the gzip stream.
 # GNU gzip returns 2 for that warning; a decompression error (1) remains fatal.
 if [ "$rc" != 0 ] && [ "$rc" != 2 ]; then rm -f "$VM_PATH"/openwrt.img.part; exit "$rc"; fi
 mv "$VM_PATH"/openwrt.img.part "$VM_PATH"/openwrt.img
fi
if [ "$OPENWRT_TARGET" = x86/64 ]; then
# The second partition starts at sector 33280 (verified with fdisk by vm setup).
python3 - <<'PY'
import struct, os
from pathlib import Path
base = Path(os.environ['VM_PATH'])
p=base / 'openwrt.img'
with p.open('rb') as f:
 f.seek(446+16); entry=f.read(16)
 start,count=struct.unpack_from('<II',entry,8)
 f.seek(start*512); data=f.read(count*512)
(base / 'rootfs.ext4').write_bytes(data)
(base / 'rootfs.offset').write_text(str(start*512))
PY
else
 cp "$VM_PATH/openwrt.img" "$VM_PATH/rootfs.ext4"
 truncate -s 512M "$VM_PATH/rootfs.ext4"
 resize2fs "$VM_PATH/rootfs.ext4"
fi
# Replay any existing journal before offline edits. Subsequent starts skip all disk edits.
rc=0
e2fsck -p "$VM_PATH"/rootfs.ext4 || rc=$?
[ "$rc" -le 1 ] || exit "$rc"
mkdir -p "$VM_PATH"/vm-files
cat > "$VM_PATH"/vm-files/network <<'CONFIG'
config interface 'loopback'
 option device 'lo'
 option proto 'static'
 option ipaddr '127.0.0.1'
 option netmask '255.0.0.0'
config device
 option name 'br-lan'
 option type 'bridge'
 list ports 'eth0'
config interface 'lan'
 option device 'br-lan'
 option proto 'static'
 option ipaddr '10.0.2.15'
 option netmask '255.255.255.0'
 option gateway '10.0.2.2'
 list dns '10.0.2.3'
CONFIG
# Enable public-key SSH on the test image, without changing the user's SSH configuration.
debugfs -w -R 'mkdir /etc/dropbear' "$VM_PATH"/rootfs.ext4 || true
debugfs -w -R 'rm /etc/dropbear/authorized_keys' "$VM_PATH"/rootfs.ext4 || true
debugfs -w -R "write $VM_PATH/vm_id.pub /etc/dropbear/authorized_keys" "$VM_PATH"/rootfs.ext4
debugfs -w -R 'rm /etc/config/network' "$VM_PATH"/rootfs.ext4
debugfs -w -R "write $VM_PATH/vm-files/network /etc/config/network" "$VM_PATH"/rootfs.ext4
# Disable dnsmasq DHCP in the VM; its upstream is QEMU's private NAT, not the LAN.
if [ "$OPENWRT_TARGET" = x86/64 ]; then
python3 - <<'PYDISK'
import os
from pathlib import Path
base = Path(os.environ['VM_PATH'])
with (base / 'openwrt.img').open('r+b') as f:
 f.seek(int((base / 'rootfs.offset').read_text())); f.write((base / 'rootfs.ext4').read_bytes())
PYDISK
else
 mv "$VM_PATH/rootfs.ext4" "$VM_PATH/openwrt.img"
fi
touch "$VM_PATH"/vm-initialized
fi
printf '%s\n' "$VM_CACHE_KEY" > "$VM_PATH"/vm-cache-key
if [ "$OPENWRT_TARGET" = armsr/armv8 ]; then
 exec qemu-system-aarch64 -machine virt,accel=tcg -cpu cortex-a57 -smp 2 -m 512 -nographic \
  -kernel "$VM_PATH/kernel.bin" -append "console=ttyAMA0 root=/dev/vda rootwait rw" \
  -drive file="$VM_PATH/openwrt.img",format=raw,if=none,id=rootfs -device virtio-blk-device,drive=rootfs \
  -device virtio-net-device,netdev=net0 -netdev user,id=net0,hostfwd=tcp:0.0.0.0:22222-:22,hostfwd=tcp:0.0.0.0:18080-:80,hostfwd=tcp:0.0.0.0:11688-:1688
fi
exec qemu-system-x86_64 -machine pc,accel=tcg -cpu qemu64 -smp 2 -m 512 -nographic \
 -drive file="$VM_PATH"/openwrt.img,format=raw,if=virtio \
 -device virtio-net-pci,netdev=net0 -netdev user,id=net0,hostfwd=tcp:0.0.0.0:22222-:22,hostfwd=tcp:0.0.0.0:18080-:80,hostfwd=tcp:0.0.0.0:11688-:1688
