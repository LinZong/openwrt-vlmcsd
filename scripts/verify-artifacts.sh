#!/bin/bash
set -euo pipefail
cd /work
build_env=$(python3 scripts/build_config.py shell)
eval "$build_env"
release="$RELEASE_PATH"
build_env=$(python3 scripts/build_config.py shell --config "$release/build-config.json")
eval "$build_env"
apk="$SDK_PATH/staging_dir/host/bin/apk"
report="dist/verification/$OPENWRT_VERSION"
mkdir -p "$report"
(cd "$release" && sha256sum -c SHA256SUMS)
extract=$(mktemp -d /work/.cache/verify-elf.XXXXXX)
trap 'rm -rf "$extract"' EXIT
if [ "$PACKAGE_FORMAT" = apk ]; then
 for pkg in "$release"/*.apk; do "$apk" --keys-dir "$PWD/$release" verify "$pkg"; done
 "$apk" --keys-dir "$PWD/$release" extract --destination "$extract" "$release/vlmcsd-$PACKAGE_VERSION.apk"
 "$apk" --keys-dir "$PWD/$release" extract --destination "$extract" "$release/vlmcs-$PACKAGE_VERSION.apk"
else
 openssl dgst -sha256 -verify "$release/vlmcsd.pem" -signature "$release/SHA256SUMS.sig" "$release/SHA256SUMS"
 python3 - "$release" "$extract" <<'PY'
import json, sys
from pathlib import Path
sys.path.insert(0, 'scripts')
import ipk
release, dest = map(Path, sys.argv[1:])
packages = {p['name']: release / p['file'] for p in json.loads((release / 'release.json').read_text())['packages']}
for name, files in {'vlmcsd': ['usr/sbin/vlmcsd', 'usr/libexec/vlmcsd-timeout'], 'vlmcs': ['usr/bin/vlmcs']}.items():
    for file in files:
        path = dest / file
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(ipk.payload_file(packages[name], file))
PY
fi
for program in vlmcsd vlmcs vlmcsd-timeout; do
 case "$program" in
  vlmcsd) path="$extract/usr/sbin/vlmcsd";;
  vlmcs) path="$extract/usr/bin/vlmcs";;
  vlmcsd-timeout) path="$extract/usr/libexec/vlmcsd-timeout";;
 esac
 file "$path"
 readelf -h -l -d "$path" > "$report/$program.elf.txt"
 grep -q 'Type:.*DYN' "$report/$program.elf.txt"
 grep -q 'Advanced Micro Devices X86-64' "$report/$program.elf.txt"
 grep -Fq "/lib/ld-$SDK_LIBC-$OPENWRT_ARCH.so.1" "$report/$program.elf.txt"
 grep -q GNU_RELRO "$report/$program.elf.txt"
 grep -q BIND_NOW "$report/$program.elf.txt"
 ! grep GNU_STACK "$report/$program.elf.txt" | grep -q RWE
done
printf 'Architecture, musl, RELRO, immediate binding and non-executable stack verified.\n'
