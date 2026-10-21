#!/bin/bash
set -euo pipefail
cd /work
build_env=$(python3 scripts/build_config.py shell --require-upstream)
eval "$build_env"
BUILD_JOBS=$(python3 scripts/build_helpers.py jobs)
export BUILD_JOBS
printf 'Build parallelism: %s jobs\n' "$BUILD_JOBS"
export TZ=UTC LC_ALL=C
python3 scripts/build_config.py make > "$SDK_PATH/vlmcsd-build.mk"
cd "/work/$SDK_PATH"
# Direct LuCI make bypasses SDK toplevel.mk; fakeroot still needs this path.
export STAGING_DIR_HOST="$PWD/staging_dir/host"
# The SDK fakeroot library must pair with SDK tar/stat, not newer host statx APIs.
export PATH="$STAGING_DIR_HOST/bin:$PATH"
mkdir -p dl
cp "/work/$UPSTREAM_ARCHIVE_PATH" "dl/$UPSTREAM_SOURCE_FILE"
# Package sources must always reflect the reviewed working tree.
rm -rf package/vlmcsd package/luci-app-vlmcsd
cp -R /work/package/vlmcsd /work/package/luci-app-vlmcsd package/
cat > .config <<CONFIG
CONFIG_TARGET_${TARGET_BOARD}=y
CONFIG_TARGET_${TARGET_SUBTARGET}=y
CONFIG_SIGNED_PACKAGES=y
CONFIG_AUTOREMOVE=n
CONFIG_PACKAGE_vlmcsd=m
CONFIG_PACKAGE_vlmcs=m
CONFIG_PACKAGE_luci-app-vlmcsd=m
CONFIG_LUCI_LANG_zh_Hans=y
CONFIG_PACKAGE_luci-i18n-vlmcsd-zh-cn=m
CONFIG_ALL_NONSHARED=n
CONFIG_ALL_KMODS=n
CONFIG_ALL=n
CONFIG
make SOURCE_DATE_EPOCH="$SOURCE_DATE_EPOCH" defconfig
make SOURCE_DATE_EPOCH="$SOURCE_DATE_EPOCH" -j"$BUILD_JOBS" package/vlmcsd/compile V=s
make SOURCE_DATE_EPOCH="$SOURCE_DATE_EPOCH" -j"$BUILD_JOBS" package/feeds/luci/luci-base/host/compile V=s
# This LuCI package contains only files; target runtime dependencies are checked on the target.
make -j"$BUILD_JOBS" -r -C package/luci-app-vlmcsd TOPDIR="$PWD" SOURCE_DATE_EPOCH="$SOURCE_DATE_EPOCH" compile V=s
