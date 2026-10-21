# Contributor and coding-agent guide

## Scope and layout

This repository packages upstream vlmcsd for OpenWrt; it is not the upstream C repository.

- `config/build.json`: the only hand-maintained source of build pins and release profiles.
- `scripts/build_config.py`: validates pins and derives shell, Python, and Make settings.
- `scripts/upstream.py`: exact-commit Git verification and deterministic source generation.
- `scripts/build.sh`, `inside-build.sh`, `assemble.py`, `ipk.py`: Docker/SDK builds and native release assembly.
- `package/vlmcsd/`: daemon/client packaging, patches, procd init, UCI helper, timeout source.
- `package/luci-app-vlmcsd/`: JavaScript view, rpcd ucode backend, ACL/menu, translations.
- `scripts/manage.py`, `engine.sh`: APK-only reviewed deployment and recovery.
- `scripts/vm.sh`, `vm-prepare.sh`, `tests/`: disposable QEMU integration and local regressions.
- `README.md`: English entry point; `README.zh.md`: Chinese documentation.
- `docs/compatibility.md`: current support and validation scope. Other review/validation documents contain historical evidence.

Inspect `git status` before editing. Preserve unrelated and pre-existing changes. Do not commit, push, publish, rotate keys, or modify a real router unless the user's task authorizes that action. Normal development and compatibility checks use the dedicated QEMU guest, never a discovered SSH alias.

## Build invariants

- Supported targets are x86/64, x86_64/musl: OpenWrt 23.05 and 24.10 use native IPK/opkg; 25.12 uses APK 3/apk. Do not mix SDKs, ABIs, or package managers across releases.
- Select a series with `--profile` on `build.sh`, or `OPENWRT_PROFILE` for other tools. The default stays 25.12. Read effective values through `load_config()` or its CLI, not duplicated literals.
- Upstream configuration consists only of `UPSTREAM_REPOSITORY` and a full `UPSTREAM_COMMIT`. Derive version, source hash, tree, and `SOURCE_DATE_EPOCH` from verified Git objects. Do not add hand-maintained upstream version/hash fields.
- Never weaken SDK checksum, GPG signature/fingerprint, Git object, APK signature, or IPK bundle verification to make a build pass.
- Keep SDK caches/release paths isolated. A frozen `build-config.json` must ignore the current profile environment. Reuse downloads only after validation; failed downloads must not replace valid cached inputs.
- Use the SDK host-tool PATH when packaging IPKs: its fakeroot must pair with SDK tar/stat, since newer host statx APIs can bypass older fakeroot interception. Release assembly rejects host UID/GID leakage.
- Preserve deterministic archives/build timestamps. APK revisions use `-rN`, IPK revisions use `-N`. Keep generated Make settings and the build-environment script in `PKG_FILE_DEPENDS` so changes invalidate package stamps.
- Support ordinary Linux Docker Engine and macOS Docker implementations. Respect Docker context/`DOCKER_HOST`; avoid OrbStack commands, privileged containers, host-only tools, or assumptions that the host UID is 1000. Host utilities should work on both macOS and Linux.
- Use `build_helpers.build_jobs()` for compilation parallelism and honor `BUILD_JOBS`. Do not parallelize ordered mutations against the same SDK or upstream cache.
- `.cache/`, `dist/`, `.secrets/`, keys, router backups, and transaction journals are local artifacts. Never commit them or print private material. Keep upstream and integration license notices separate.

## Runtime and deployment invariants

- Service defaults remain disabled. Runtime configuration comes from UCI; never hand-edit the generated INI.
- Preserve non-root execution, bounded procd respawn, private LAN plus loopback listeners, firewall-zone checks, backend validation, and bounded diagnostics. Missing LAN addresses must not produce wildcard listeners.
- LuCI status polling must not send activation requests. Logs use logd. DNS discovery is opt-in, manages only the owned section, and must not overwrite unrelated DHCP configuration.
- Keep English source strings and Chinese translations synchronized. Check compatibility against the oldest supported LuCI/rpcd/ucode runtime when changing APIs or syntax.
- Transactional deployment/rollback remains APK-only. Reject IPK releases before SSH. Native opkg installation is a separate documented workflow, without transaction rollback claims.
- Preserve review-plan binding to target state/scripts/artifact hashes, offline APK installation, exact old-package rollback, owned-dependency cleanup, and independent router-side recovery. Do not replace package databases to simulate rollback.
- Deployment/health/recovery limits remain 120/10/120 seconds with no confirmation countdown. Successful operations should finish as soon as their checks pass.

## Verification

Run checks appropriate to the change. Pure documentation edits need link/command review, not full SDK rebuilds. Runtime/build changes need the relevant tests and matching package build.

```sh
python3 -m unittest discover -s tests -v
node --check package/luci-app-vlmcsd/htdocs/luci-static/resources/view/vlmcsd/overview.js
shellcheck -S error scripts/*.sh package/vlmcsd/files/vlmcsd-helper package/vlmcsd/files/vlmcsd.init
BUILD_JOBS=4 ./scripts/build.sh --profile 23.05
```

For verification and reproducibility, use the selected builder image and profile (change both together):

```sh
docker run --rm --platform linux/amd64 --user "$(id -u):$(id -g)" \
  -e HOME=/work/.cache/builder-home -e OPENWRT_PROFILE=23.05 \
  -v "$PWD:/work" vlmcsd-builder:23.05.6 bash scripts/verify-artifacts.sh
docker run --rm --platform linux/amd64 --user "$(id -u):$(id -g)" \
  -e HOME=/work/.cache/builder-home -e OPENWRT_PROFILE=23.05 -e BUILD_JOBS=4 \
  -v "$PWD:/work" vlmcsd-builder:23.05.6 python3 scripts/reproducibility.py
```

A root caller must also pass `-e FORCE=1` for OpenWrt make inside the disposable container. Never run SDK compilation directly on a production router.

Native IPK end-to-end tests install dependencies from official feeds and mutate/remove application packages **only in the dedicated guest**. They have no configurable remote target and verify the QEMU model/release/package manager first:

```sh
OPENWRT_PROFILE=23.05 ./scripts/vm.sh reset
OPENWRT_PROFILE=23.05 python3 tests/ipk_integration.py
./scripts/vm.sh stop
```

For 25 use the existing `tests/integration.py`, `tests/faults.py`, and `tests/upgrade.py` against a matching prepared guest; consult their setup and historical validation notes. Resetting the VM destroys its test data. Do not run guest integration suites concurrently: they share ports and one guest disk.

Report which profiles were actually built/tested and which remain unverified. Do not promote source/API inspection into an end-to-end verification claim. Update both READMEs when commands, support, package formats, or user-visible behavior change. Preserve historical validation records; add fresh evidence separately.
