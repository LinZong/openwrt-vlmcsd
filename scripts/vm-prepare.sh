#!/bin/sh
set -eu
cd /work
[ -f .cache/vm_id ] || ssh-keygen -q -t ed25519 -N '' -f .cache/vm_id
if [ ! -f .cache/vm-initialized ] || [ ! -f .cache/openwrt.img ]; then
if [ ! -f .cache/openwrt.img ]; then
 rc=0
 gzip -dc .cache/openwrt.img.gz > .cache/openwrt.img.part || rc=$?
 # Some authenticated OpenWrt images append metadata after the gzip stream.
 # GNU gzip returns 2 for that warning; a decompression error (1) remains fatal.
 if [ "$rc" != 0 ] && [ "$rc" != 2 ]; then rm -f .cache/openwrt.img.part; exit "$rc"; fi
 mv .cache/openwrt.img.part .cache/openwrt.img
fi
# The second partition starts at sector 33280 (verified with fdisk by vm setup).
python3 - <<'PY'
import struct
from pathlib import Path
p=Path('.cache/openwrt.img')
with p.open('rb') as f:
 f.seek(446+16); entry=f.read(16)
 start,count=struct.unpack_from('<II',entry,8)
 f.seek(start*512); data=f.read(count*512)
Path('.cache/rootfs.ext4').write_bytes(data)
Path('.cache/rootfs.offset').write_text(str(start*512))
PY
# Replay any existing journal before offline edits. Subsequent starts skip all disk edits.
rc=0
e2fsck -p .cache/rootfs.ext4 || rc=$?
[ "$rc" -le 1 ] || exit "$rc"
mkdir -p .cache/vm-files
cat > .cache/vm-files/network <<'CONFIG'
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
debugfs -w -R 'mkdir /etc/dropbear' .cache/rootfs.ext4 || true
debugfs -w -R 'rm /etc/dropbear/authorized_keys' .cache/rootfs.ext4 || true
debugfs -w -R 'write .cache/vm_id.pub /etc/dropbear/authorized_keys' .cache/rootfs.ext4
debugfs -w -R 'rm /etc/config/network' .cache/rootfs.ext4
debugfs -w -R 'write .cache/vm-files/network /etc/config/network' .cache/rootfs.ext4
# Disable dnsmasq DHCP in the VM; its upstream is QEMU's private NAT, not the LAN.
python3 - <<'PY'
from pathlib import Path
with Path('.cache/openwrt.img').open('r+b') as f:
 f.seek(int(Path('.cache/rootfs.offset').read_text())); f.write(Path('.cache/rootfs.ext4').read_bytes())
PY
touch .cache/vm-initialized
fi
printf '%s\n' "$VM_CACHE_KEY" > .cache/vm-cache-key
exec qemu-system-x86_64 -machine pc,accel=tcg -cpu qemu64 -smp 2 -m 512 -nographic \
 -drive file=.cache/openwrt.img,format=raw,if=virtio \
 -device virtio-net-pci,netdev=net0 -netdev user,id=net0,hostfwd=tcp:0.0.0.0:22222-:22,hostfwd=tcp:0.0.0.0:18080-:80,hostfwd=tcp:0.0.0.0:11688-:1688
