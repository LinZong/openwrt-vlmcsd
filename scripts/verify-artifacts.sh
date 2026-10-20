#!/bin/bash
set -euo pipefail
cd /work
sdk=.cache/sdk
apk="$sdk/staging_dir/host/bin/apk"
mkdir -p dist/verification
(cd dist/release && sha256sum -c SHA256SUMS)
build_env=$(python3 scripts/build_config.py shell --config dist/release/build-config.json)
eval "$build_env"
for pkg in dist/release/*.apk; do "$apk" --keys-dir "$PWD/dist/release" verify "$pkg"; done
extract=$(mktemp -d /work/.cache/verify-elf.XXXXXX)
trap 'rm -rf "$extract"' EXIT
"$apk" --keys-dir "$PWD/dist/release" extract --destination "$extract" "dist/release/vlmcsd-$PACKAGE_VERSION.apk"
"$apk" --keys-dir "$PWD/dist/release" extract --destination "$extract" "dist/release/vlmcs-$PACKAGE_VERSION.apk"
for program in vlmcsd vlmcs vlmcsd-timeout; do
 case "$program" in
  vlmcsd) path="$extract/usr/sbin/vlmcsd";;
  vlmcs) path="$extract/usr/bin/vlmcs";;
  vlmcsd-timeout) path="$extract/usr/libexec/vlmcsd-timeout";;
 esac
 file "$path"
 readelf -h -l -d "$path" > "dist/verification/$program.elf.txt"
 grep -q 'Type:.*DYN' "dist/verification/$program.elf.txt"
 grep -q 'Advanced Micro Devices X86-64' "dist/verification/$program.elf.txt"
 grep -Fq "/lib/ld-$SDK_LIBC-$OPENWRT_ARCH.so.1" "dist/verification/$program.elf.txt"
 grep -q GNU_RELRO "dist/verification/$program.elf.txt"
 grep -q BIND_NOW "dist/verification/$program.elf.txt"
 ! grep GNU_STACK "dist/verification/$program.elf.txt" | grep -q RWE
done
printf 'Architecture, musl, RELRO, immediate binding and non-executable stack verified.\n'
