# vlmcsd for OpenWrt

包含服务、诊断客户端、中文 LuCI 和独立部署恢复工具。当前固定的 OpenWrt/上游版本、提交、校验值和容器基础镜像统一定义在 [`config/build.json`](config/build.json)。目标平台为 x86_64 / musl / APK 3。

## 构建

本机需 Python 3、Docker（macOS 可使用 OrbStack）、curl 和 shasum。启动 OrbStack 后：

```sh
./scripts/build.sh
```

SDK 压缩包先校验本地 SHA256；官方校验清单、签名和发布公钥先在本地验证签名、指纹及 SDK 条目。上游源码按完整提交 SHA 获取 Git 对象并校验，自动生成源码归档和校验值。完整缓存直接复用，归档损坏时从本地 Git 对象重新生成，缺少 Git 对象时才联网获取。下载失败或验证失败不会覆盖已有有效材料。Docker 基础依赖继续复用镜像构建缓存，SDK 内部依赖复用其下载缓存。

编译默认使用构建容器可用的 CPU 核数，并考虑 cgroup v2 CPU 配额。可以手动限制并行任务数，例如 `BUILD_JOBS=4 ./scripts/build.sh`；设为 `1` 则串行编译。实际并行度记录在 `.cache/build.log` 开头。`BUILD_JOBS` 是本次运行参数，不写入固定版本配置，也不改变发布版本。

脚本验证官方 SDK 的签名、签名密钥指纹和固定 SHA256，使用 SDK 自带的固定 feeds，构建并签名 APK。第一次准备 SDK 较慢；路由器不参与编译。私钥保存于 `.secrets/`，请备份且勿提交。构建日志位于 `.cache/build.log`。

产物在 `dist/release/`：服务包、客户端、LuCI、中文翻译、离线依赖、公钥、恢复用 timeout、许可证、`release.json`、`upstream.json` 和 `SHA256SUMS`。`release.json` 记录上游提交、SDK 哈希、feeds、包版本和依赖。构建时间 `SOURCE_DATE_EPOCH` 自动取自固定 Git 提交的提交者时间，签名后的文件单独校验。

## 升级构建依赖

只编辑 `config/build.json`，然后重新运行 `./scripts/build.sh`：

| 配置项 | 用途 |
| --- | --- |
| `OPENWRT_VERSION`、`SDK_GCC_VERSION`、`SDK_SHA256` | 固定 SDK 发行版、工具链和压缩包校验值；下载地址和文件名自动生成。 |
| `OPENWRT_KEY_FINGERPRINT` | 校验 OpenWrt 发布公钥，公钥下载地址由完整指纹派生。 |
| `UPSTREAM_REPOSITORY`、`UPSTREAM_COMMIT` | 上游只需填写 GitHub 的 `owner/repository` 和完整 40 位提交 SHA。 |
| `PACKAGE_RELEASE` | 本项目的 OpenWrt 包修订号；与上游版本无关，默认保持现有值。 |
| `CONTAINER_BASE_IMAGE`、`BUILDER_PLATFORM` | 固定容器基础镜像摘要和构建平台。 |
| `VM_IMAGE_SHA256` | 校验与 OpenWrt 版本对应的测试 VM 镜像。 |

`scripts/build_config.py` 使用 Python 标准库读取和校验配置，向 Shell、Python 和 Make 提供相同的派生值；可用 `python3 scripts/build_config.py json` 查看。构建时自动生成 SDK 内的 `vlmcsd-build.mk`，供两个包的 Makefile 使用，不要手动修改生成文件。Dockerfile 的 `BASE_IMAGE` 参数也由脚本从配置传入。

`UPSTREAM_VERSION`、`UPSTREAM_SHA256`、`SOURCE_DATE_EPOCH` 不再手填。构建时，`scripts/upstream.py` 获取并核对固定 Git 提交，执行对象完整性检查，再用 `git archive` 和固定 gzip 头生成源码包。版本格式为 `YYYYMMDD~<提交前12位SHA>`，日期使用该提交的 UTC 时间；APK 版本再附加 `-r<PACKAGE_RELEASE>`，不依赖标签、分支最新状态或当前系统时间。

自动生成结果保存在 `.cache/upstream.json`，包括仓库、提交、源码树、版本、构建时间、归档 SHA256 和子模块引用；Git 对象与归档分别缓存在 `.cache/upstream-git/` 和 `.cache/upstream-sources/`。改变仓库或提交后会自动重新解析。归档遵循 `git archive` 的范围，不递归拉取子模块；当前上游仅有不参与 C 源码编译的 wiki 子模块，与原 GitHub 源码包的范围一致。

SDK 哈希变化时重新下载、解压并准备 SDK。首次从旧缓存迁移会重新准备 SDK。测试 VM 镜像或初始化脚本变化时，`vm.sh start` 会提示先执行 `vm.sh reset`，避免继续使用旧测试系统。配置包含目标架构字段，但切换到其他架构仍需适配 QEMU、内核目标和 ELF 校验，不能仅修改配置就假定支持。

每个发布目录附带本次 `build-config.json` 快照和 `upstream.json` 自动生成记录。快照包含已解析的版本、时间和哈希，后续修改配置或清理缓存不会改变旧发布的校验要求。部署时按照该发布的 `release.json` 校验目标系统。`docs/review.md`、`docs/validation.md` 中的版本和产物哈希是历史验收记录，不随配置改写。

## 审查后部署

`plan` 通过 SSH 进行只读调查，结果只写入本机；不会上传文件、更新仓库或修改路由器。

```sh
./scripts/deploy.sh plan --target gateway
```

先审查 `dist/plans/deploy-gateway.json` 和同名 Markdown。确认包、公钥、哈希、设备、变更范围和测试结果后，再显式执行：

```sh
./scripts/deploy.sh apply --target gateway --plan dist/plans/deploy-gateway.json
```

脚本核对计划所绑定的设备状态和文件哈希，拒绝过期计划。安装材料先上传，随后启动独立于 SSH 的路由器端事务。所有 APK 必须通过签名验证，安装不访问网络，不允许无关包升级或删除。

**超时：部署 120 秒、单项健康检查 10 秒、回滚独立 120 秒。成功即退出，不设确认倒计时或固定观察期。** 正常流程只花实际需要的时间。超时工具在安装前已准备，删除应用包后仍可用于恢复。

升级保留原有接口、端口和其他参数，计划中列出将写入的 UCI 值。

状态、报告和手动回滚：

```sh
./scripts/deploy.sh status --target gateway --transaction TRANSACTION_ID
./scripts/deploy.sh rollback --target gateway --transaction TRANSACTION_ID
```

事务记录持久保存在路由器 `/root/vlmcsd-deploy/`，本机报告在 `dist/transactions/`。SSH 断线时不要重复执行 apply，先查询原事务。报告包括 JSON 事件、各阶段日志、协议诊断和前后快照；包含路由器配置和账户元数据，应按敏感备份管理。

升级必须有精确匹配旧版本的 APK；将旧包放入 `dist/rollback-package-cache/`。缺少旧包会拒绝生成计划。不得通过覆盖 APK 数据库来“回滚”。恢复失败或检测到相关配置被并发修改时，保留材料并报告人工恢复，不掩盖失败。

## 服务与 LuCI

安装包默认关闭。部署脚本启用 `lan` IPv4 和 `127.0.0.1` 的 TCP 1688，使用独立非登录用户。UCI 配置为 `/etc/config/vlmcsd`，INI 在运行时生成。接口必须属于 LAN 防火墙区域且有私网 IPv4；没有地址时不会改为通配监听。

在 LuCI「服务 → 批量许可服务」管理配置、启停、状态、近期日志及 v4/v5/v6 自检。状态每 5 秒刷新，不发送 KMS 请求。服务日志由 logd 管理，不额外持续写入闪存；部署事务日志持久保存。

默认工作数为 4、连接超时 30 秒。端口范围 1024–65535，工作数 1–64，连接超时 1–300 秒。所有配置在后端再次校验。

DNS 自动发现默认关闭。首次安装默认关闭；升级保留现有开关。需要启用时重新生成计划并传入 `--dns`，关闭可使用 `--no-dns`，也可在 LuCI 中设置。应用仅管理 `dhcp.vlmcsd_srv` 记录；冲突时拒绝覆盖。记录变化才重载 dnsmasq，关闭或卸载时撤销本应用记录。

## 卸载

```sh
./scripts/uninstall.sh plan --target gateway
./scripts/uninstall.sh apply --target gateway --plan dist/plans/uninstall-gateway.json
```

默认保留应用配置和事务备份；生成计划时加 `--purge` 才删除应用配置。只移除本应用以及有归属记录、未被其他软件使用的新增依赖和账户。卸载也有预检、备份和失败恢复。事务备份不会因卸载而删除。

## 隔离测试

```sh
python3 -m unittest discover -s tests -v
./scripts/vm.sh start
python3 tests/integration.py
python3 tests/faults.py
./scripts/vm.sh stop
```

测试 VM 使用配置指定版本的官方 x86_64 镜像、QEMU 软件模拟和专用密钥，SSH 绑定本机 22222，LuCI 绑定本机 18080。测试只操作 VM，不使用 gateway。详细结果见 `docs/validation.md`。

服务源码遵循随上游分发的 TheFlightSims OSS License；恢复用超时工具源码随本项目提供，采用 Apache-2.0。新写的 OpenWrt 集成、脚本和 LuCI 文件采用 Apache-2.0。
