# vlmcsd for OpenWrt

English | [简体中文](README.zh.md)

OpenWrt packages for [tfslabs/vlmcsd](https://github.com/tfslabs/vlmcsd): a procd service, protocol diagnostic client, English/Chinese LuCI application, and deployment/recovery tools.

The daemon runs as a dedicated unprivileged user, listens on the selected private LAN IPv4 address and loopback, and validates configuration before applying it. LuCI provides configuration, service controls, live status, recent logs, and v4/v5/v6 protocol diagnostics.

## Compatibility

| OpenWrt series | Pinned build target | Native format | Service and full LuCI | Transactional deployment/rollback |
| --- | --- | --- | --- | --- |
| 23.05 | 23.05.6, x86_64 + ARM64/musl | IPK / opkg | Supported | Use native opkg installation |
| 24.10 | 24.10.6, x86_64 + ARM64/musl | IPK / opkg | Supported | Use native opkg installation |
| 25.12 | 25.12.1, x86_64 + ARM64/musl | APK 3 / apk | Supported | Supported |

Each target uses its matching official SDK. Packages are not interchangeable between these releases. Other patch versions require updating the SDK/image pins and validating a matching guest; supported targets are listed below; ARMv7 is outside this phase. Legacy release compatibility does not imply ongoing security support from OpenWrt. See [compatibility and validation](docs/compatibility.md) for the test scope.

### ARM64 targets

| `--target` | Package architecture | Validation environment |
| --- | --- | --- |
| `armsr/armv8` | `aarch64_generic` | Matching SDK and full-system QEMU |
| `mediatek/filogic` | `aarch64_cortex-a53` | Matching SDK and QEMU user mode |
| `rockchip/armv8` | `aarch64_generic` | Matching SDK and ABI-compatible ARMsr guest |

All three targets are configured for each pinned release. Consult the [validation record](docs/compatibility.md#arm64-validation) for completed checks; emulator results do not establish physical-board validation. Packages target the selected OpenWrt ABI and release; a single generic “arm64” package does not cover every ARM64 router. Check `/etc/openwrt_release` (`DISTRIB_TARGET`, `DISTRIB_ARCH`, `DISTRIB_RELEASE`) before choosing a build.

## Build

Requirements: Python 3.10+, Bash, curl, and a working Docker CLI/daemon. [Docker Engine on Linux](https://docs.docker.com/engine/install/) is supported; Docker Desktop and OrbStack on macOS can also be used. The scripts respect your current Docker context and `DOCKER_HOST`, with no OrbStack-specific commands. The official SDK runs on Linux amd64: ARM hosts need Docker's amd64 emulation. No privileged container or host OpenWrt toolchain is required. Installation/VM tests also need the OpenSSH client; host-side IPK signature verification uses OpenSSL. Builds, artifact verification and reproducibility have been [verified on Ubuntu 22.04.5 with Docker Engine 29.1.3](docs/compatibility.md#linux-docker-engine-verification-2026-10-21).

```sh
./scripts/build.sh                    # Default: OpenWrt 25.12
./scripts/build.sh --profile 23.05
./scripts/build.sh --profile 24.10
./scripts/build.sh --profile 25.12 --target mediatek/filogic
./scripts/build.sh --profile 24.10 --target rockchip/armv8
./scripts/build.sh --profile 23.05 --target armsr/armv8
```

`OPENWRT_PROFILE=23.05 OPENWRT_TARGET=armsr/armv8` selects a release and target through the environment. Command-line selectors take precedence; defaults remain 25.12 and x86/64. These settings are used by artifact verification, reproducibility checks, and the test VM. Compilation uses available container CPUs, including cgroup v2 quota limits. Set `BUILD_JOBS=4` to limit parallelism, or `BUILD_JOBS=1` for serial builds. Containers use your UID/GID so native Linux bind mounts remain writable.

Verified SDK archives, authenticated official checksums/signatures/keys, Git objects, generated source archives, Docker layers, and SDK downloads are reused locally. Corrupted downloads are replaced only after verification. SDK caches are isolated by target; upstream Git/source caches are shared. Initial downloads and SDK preparation can take several minutes.

| Target | Release directory | Build log |
| --- | --- | --- |
| 25.12.1 | `dist/release/` | `.cache/build.log` |
| 23.05.6 | `dist/release-23.05.6-x86_64/` | `.cache/sdk-cache/23.05.6-x86_64/build.log` |
| 24.10.6 | `dist/release-24.10.6-x86_64/` | `.cache/sdk-cache/24.10.6-x86_64/build.log` |
| ARM64 | `dist/release-<version>-<target>-<subtarget>/` | `.cache/sdk-cache/<version>-<target>-<subtarget>/build.log` |

Releases contain four application packages, the public key, recovery timeout binary, licenses, `release.json`, frozen `build-config.json`, generated `upstream.json`, and `SHA256SUMS`. APKs are individually signed. Native IPKs are unchanged SDK output; their bundle has a detached RSA signature, `SHA256SUMS.sig`. The private key is stored in `.secrets/`: keep it private and back it up.

## Update pinned inputs

Edit only [`config/build.json`](config/build.json), then rebuild the selected profile. `OPENWRT_TARGETS` records package ABIs; `OPENWRT_PROFILES.<series>.TARGETS` holds SDK hashes and ARMsr kernel/rootfs pins by target; shared settings remain at the top level.

| Setting | Purpose |
| --- | --- |
| `OPENWRT_VERSION`, `SDK_GCC_VERSION`, `SDK_SHA256` | Pin the official SDK; URLs, archive names, compression, and package format are derived. |
| `OPENWRT_KEY_FINGERPRINT` | Pin the actual release signing key's primary fingerprint. |
| `UPSTREAM_REPOSITORY`, `UPSTREAM_COMMIT` | GitHub `owner/repository` and a complete 40-character commit SHA. These are the only upstream inputs. |
| `PACKAGE_RELEASE` | OpenWrt packaging revision. |
| `CONTAINER_BASE_IMAGE`, `BUILDER_PLATFORM` | Container image digest and SDK execution platform. |
| `VM_IMAGE_SHA256`, `VM_KERNEL_SHA256` | Matching official test disk/rootfs and ARM64 kernel checksums. |

`scripts/upstream.py` fetches the exact Git commit, checks object integrity, and generates a deterministic `git archive`/gzip source package. The upstream version is `YYYYMMDD~<first 12 commit characters>`, using the commit's UTC timestamp; package revisions use `-rN` for APK or `-N` for IPK. `SOURCE_DATE_EPOCH` and source SHA256 are generated, never hand-maintained. Git archive scope does not recursively include submodules; the pinned upstream's wiki submodule is not needed to compile the C sources.

Inspect resolved settings with `python3 scripts/build_config.py json --profile 23.05`. Shell, Python, and package Makefiles read the same values. Do not edit the generated SDK `vlmcsd-build.mk`. Release snapshots freeze the selected settings and generated metadata, independently of later configuration/environment changes. Historical records in `docs/review.md` and `docs/validation.md` retain their original versions and hashes.

## Native installation: OpenWrt 23/24

Build the profile matching the router's exact release and architecture. Verify the release on your build host, then install with opkg. Obtain the public key from a trusted build or verify its fingerprint separately; a key shipped alongside an unknown download does not establish its authenticity. On macOS, use `shasum -a 256 -c SHA256SUMS` if `sha256sum` is unavailable.

Example for 23.05.6 (`gateway` is your SSH host alias):

```sh
cd dist/release-23.05.6-x86_64
openssl dgst -sha256 -verify vlmcsd.pem -signature SHA256SUMS.sig SHA256SUMS
sha256sum -c SHA256SUMS
ssh gateway 'mkdir -p /tmp/vlmcsd-ipk'
scp -O ./*.ipk gateway:/tmp/vlmcsd-ipk/
ssh gateway 'opkg update && opkg install /tmp/vlmcsd-ipk/*.ipk'
ssh gateway 'uci set vlmcsd.main.enabled=1; uci commit vlmcsd; /etc/init.d/vlmcsd enable; /etc/init.d/vlmcsd start; /etc/init.d/rpcd restart'
```

Use the 24.10 release directory for 24.10.6. This workflow needs router Internet access for missing runtime dependencies from its matching official feeds. Offline installation requires supplying those dependencies separately; the four application IPKs are not a complete offline repository. Back up UCI configuration before upgrades. Native opkg installation does not provide this project's transaction rollback.

Open **Services → Volume License Service** in LuCI, then refresh the browser after first installation. LuCI's system language setting selects English or Chinese; The supplied translation package enables Chinese application strings; install base LuCI Chinese translations too if you want the surrounding system UI translated. Configuration, start/stop/restart, status, logs, DNS discovery, and all protocol diagnostics are available on all three series.

To remove native packages while retaining configuration:

```sh
ssh gateway '/etc/init.d/vlmcsd stop; /etc/init.d/vlmcsd disable; opkg remove luci-i18n-vlmcsd-zh-cn luci-app-vlmcsd vlmcs vlmcsd; /etc/init.d/rpcd restart'
```

## Reviewed deployment: OpenWrt 25

The transaction scripts require APK releases and reject IPK input before connecting over SSH. Start with a read-only plan:

```sh
./scripts/deploy.sh plan --target gateway
# ARM64 example: select the exact target release explicitly
./scripts/deploy.sh plan --target arm-router --release dist/release-25.12.1-mediatek-filogic
```

Review `dist/plans/deploy-gateway.json` and its Markdown report, including device identity, package/key hashes, configuration changes, and test results. Explicitly apply that reviewed plan:

```sh
./scripts/deploy.sh apply --target gateway --plan dist/plans/deploy-gateway.json
```

The driver checks device state and artifact hashes again, stages the materials, then starts a router-side transaction independent of the SSH connection. It verifies APK signatures, installs without network access, and rejects unrelated package upgrades/removals. Missing runtime dependencies must be supplied as matching signed offline packages. Upgrades preserve the existing interface, port, and other settings.

**Limits: deployment 120 seconds, each health check 10 seconds, recovery separately 120 seconds.** Successful operations exit immediately, with no confirmation countdown. The timeout tool is staged before installation and remains available after application removal.

```sh
./scripts/deploy.sh status --target gateway --transaction TRANSACTION_ID
./scripts/deploy.sh rollback --target gateway --transaction TRANSACTION_ID
./scripts/uninstall.sh plan --target gateway
./scripts/uninstall.sh apply --target gateway --plan dist/plans/uninstall-gateway.json
```

Router journals persist in `/root/vlmcsd-deploy/`; local reports are under `dist/transactions/`. After an SSH interruption, query the existing transaction before trying another apply. Reports include configuration/account metadata and should be treated as sensitive backups.

New manifests bind deployment to the exact release, OpenWrt target and package architecture. Legacy manifests without a target remain x86-only. Rollback packages are checked against their actual APK name/version/architecture.

An upgrade needs the exact previous APK bytes in `dist/rollback-package-cache/`; plans fail if they are unavailable. Rollback uses the package manager. Recovery failure or concurrent configuration changes are reported for manual intervention, with recovery materials retained. Uninstall retains application configuration and transaction backups; `--purge` on the uninstall plan explicitly removes application configuration. Only owned, unused dependencies/accounts are eligible for removal.

## Runtime behavior

Packages default to disabled. The 25 transaction installer enables TCP 1688 on `lan` IPv4 and `127.0.0.1`; native IPK installation requires the explicit enable/start commands above. The init service starts on subsequent boots when both UCI enablement and the rc.d service link are enabled.

UCI lives in `/etc/config/vlmcsd`; the runtime INI is generated. The selected interface must belong to the LAN firewall zone and have a private IPv4 address. Loss of that address never falls back to wildcard listening. Defaults are four workers and a 30-second connection timeout; allowed ranges are ports 1024–65535, workers 1–64, and timeout 1–300 seconds. The backend validates configuration independently of LuCI.

Status refreshes every five seconds without sending activation requests. Service logs use logd rather than additional continuous flash writes. Optional DNS discovery defaults to off, manages only `dhcp.vlmcsd_srv`, rejects conflicting ownership, and reloads dnsmasq only when the record changes. Enable it in LuCI, or use `--dns` / `--no-dns` when preparing a 25 deployment plan.

## Testing and contributing

```sh
python3 -m unittest discover -s tests -v
node --check package/luci-app-vlmcsd/htdocs/luci-static/resources/view/vlmcsd/overview.js
shellcheck -S error scripts/*.sh package/vlmcsd/files/vlmcsd-helper package/vlmcsd/files/vlmcsd.init
```

Run artifact verification or reproducibility inside the matching builder container, with the repository mounted at `/work`; see [AGENTS.md](AGENTS.md) for commands. The manually dispatched GitHub workflow builds/verifies all three profiles on Linux Docker Engine.

The dedicated QEMU guest binds SSH to localhost port 22222 and LuCI to port 18080. It uses a test-only key and private NAT, and never uses the `gateway` alias. Switching profiles requires resetting this disposable guest:

```sh
OPENWRT_PROFILE=23.05 ./scripts/vm.sh reset
OPENWRT_PROFILE=23.05 python3 tests/ipk_integration.py
./scripts/vm.sh stop
```

Use `24.10` for the second IPK target. For 25, use `./scripts/vm.sh reset`, `python3 tests/integration.py`, and `python3 tests/faults.py`; these transaction tests require a guest with the matching runtime dependencies and offline packages prepared. See [compatibility](docs/compatibility.md) and the historical [validation record](docs/validation.md).

Contributors and coding agents should read [AGENTS.md](AGENTS.md) before making changes.

## Licenses

The daemon/client retain the upstream TheFlightSims OSS License, included in each release. New OpenWrt integration, LuCI code, scripts, and the recovery timeout tool are licensed under [Apache-2.0](LICENSE). Keep both licenses when redistributing packages.

## ARM64 verification commands

Run inside the selected builder container, with the repository mounted at `/work`:

```sh
export OPENWRT_PROFILE=25.12 OPENWRT_TARGET=armsr/armv8
bash scripts/verify-artifacts.sh
python3 scripts/reproducibility.py
python3 scripts/user-mode-test.py
```

For a full guest, run `./scripts/vm.sh --profile 23.05 --target armsr/armv8 reset`, then `OPENWRT_PROFILE=23.05 OPENWRT_TARGET=armsr/armv8 python3 tests/ipk_integration.py`. The native integration suite also accepts APK packages; transactional APK tests additionally use `tests/integration.py` after guest dependency preparation. Guest disks and SSH credentials are isolated by release/VM target. Guests share loopback ports and must run sequentially. `vm.sh stop` removes the test container; disks remain cached. Physical-board targets do not boot their board firmware under the generic emulator.
