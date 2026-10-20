# 部署审查入口

实现和隔离测试已完成。**真实路由器 gateway 尚未部署，未上传安装材料。**

- 目标：OpenWrt 25.12.1 / x86_64，LAN `192.168.10.1`。
- 服务默认绑定 LAN IPv4 和回环，TCP 1688，DNS 自动发现关闭。
- 执行截止 120 秒，单项健康检查 10 秒，回滚独立 120 秒；检查通过立即结束。
- 升级保留接口、端口及其他配置；部署计划明确列出写入值。失败自动恢复，另有独立卸载入口。
- 测试结果见 `docs/validation.md`；源码入口见 `README.md`。

## 安装包

| 文件 | 架构 | SHA256 |
|---|---|---|
| `luci-app-vlmcsd-2604-r1.apk` | noarch | 7c54bc7efbf6551499479117c382f770ca29325a70e4247b90d4ff6f2cdfb0ab |
| `luci-i18n-vlmcsd-zh-cn-2604-r1.apk` | noarch | c6f4b2a568fa3a01558c92a3e0bb7cac593b23d0f6d64e43675f32aaaec0586c |
| `vlmcs-2604-r1.apk` | x86_64 | a0e087c961daf304ace964436e1d9b3949336e6b203342ea31edefb1e7db566c |
| `vlmcsd-2604-r1.apk` | x86_64 | 93009bb05b77e971f06c9644204e98984b1d10e64f6f40d8b76ea69ed7aa82a1 |

公钥、许可证、恢复执行器和构建来源清单均在 `dist/release/`。

## 已准备的真实设备计划

- JSON：`dist/plans/deploy-gateway.json`
- 可读摘要：`dist/plans/deploy-gateway.md`
- 计划 SHA256：`baca5451640a5e1d5d87a5b001e8cfb3c3c3bb71b078b466c4ecf571e27a51d2`
- 新装：luci-app-vlmcsd, luci-i18n-vlmcsd-zh-cn, vlmcs, vlmcsd。
- 写入的 UCI 值：`{"dns": "0", "enabled": "1", "interface": "lan", "port": "1688"}`。
- 没有旧版本应用，无需先替换其他实现；服务账户和临时恢复任务的创建包含在事务内。

批准后使用已审查计划执行：

```sh
./scripts/deploy.sh apply --target gateway --plan dist/plans/deploy-gateway.json
```

计划绑定安装包、部署脚本和目标相关配置。任何变更都需要重新运行 `plan` 并审查；不要绕过过期计划检查。备份与报告包含设备元数据，保存在本机和路由器的事务目录，不包含在可分享的源码交付包。
