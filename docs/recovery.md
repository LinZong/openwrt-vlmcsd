# 事务恢复说明

部署成功没有额外等待。120 秒是路由器执行阶段的故障截止时间；超时后进入单独限时的恢复流程。源码构建、下载和上传材料在执行阶段之前完成。

每个事务有固定编号，保存在路由器 `/root/vlmcsd-deploy/编号/`。`state` 使用临时文件和原子重命名更新，`events.jsonl` 记录阶段与错误。`worker.log` 记录包管理、配置和健康检查输出。备份、公钥、旧 APK 和独立超时执行器均保存在同一持久目录。

状态包括 `staged`、`preparing`、`applying`、`succeeded`、`rejected`、`rolling_back`、`rolled_back` 和 `recovery_failed`。`rejected` 表示设备预检或备份失败，应用未开始修改；`recovery_failed` 必须人工检查，不代表恢复成功。

临时 procd 服务 `vlmcsd-recover` 在安装前启动并配置开机运行。它独立于 SSH 和应用包，监视执行器、单调时钟截止时间及启动编号。成功或恢复完成后撤销。事务 flock 和 APK 自身的锁共同防止并发包管理；恢复不会直接复制 APK 数据库。

SSH 断线时，先查询原事务，不重复安装：

```sh
./scripts/deploy.sh status --target gateway --transaction 编号
```

部署成功后仍可显式恢复：

```sh
./scripts/deploy.sh rollback --target gateway --transaction 编号
```

恢复前检查事务之后的相关配置和 APK world 是否被其他操作修改。有冲突时保留备份并报告失败，避免覆盖后续改动。升级前没有精确旧版本安装包时不接受部署。

人工恢复时先查看 `events.jsonl`、`worker.log`、备份和 APK 事务清单；确认包管理没有其他写入后，通过原事务的 `engine.sh rollback` 执行同一恢复逻辑。不要手工覆盖 `/lib/apk/db`，不要无条件恢复整个 `/etc/passwd` 或 DHCP 配置。

回滚是应用级恢复；存储介质损坏、无法启动用户空间等问题需要设备自身的恢复手段。部署不会刷写固件或修改 SSH、WAN、路由配置。
