# XG-040G-MD UBI CI

主流程 **AIR-UBI**（`.github/workflows/AIR-MAIN.yml`）构建 ImmortalWrt `master` 的
`airoha/an7581` → **`nokia_xg-040g-md-ubi`**，为 Linux 6.18 加入 SkyHigh SPI-NAND
Robust Read workaround，并在所有检查通过后发布 GitHub Release。

此 UBI 固件只针对 **Nokia XG-040G-MD**，不宣称与 XG-140G-MD / TF 通刷。
旧 **AIR-TCBOOT** 保留为手动兼容入口，不再自动构建；它不是本项目的 UBI 升级路径。
AIR-ONU 等其他入口保持各自用途，不等同于此 UBI 固件。

## SkyHigh 修复及范围

- 补丁随仓库保存于 `Patches/airoha-6.18/600-mtd-spinand-add-skyhigh-robust-read-workaround.patch`，
  CI 不临时下载第三方补丁。
- 根据 [SkyHigh AN223239](https://www.skyhighmemory.com/download/002-23239%20-%20SPI%20NAND%20Robust%20Read%20Method.pdf)
  的 double-check ready 流程：第一次 OIP=0 后再读一次状态，两次均为 ready 才继续。
- 基于 [XG-040G-MD 社区补丁](https://github.com/xiangtailiang/OpenWrt-for-XG-040G-MD/blob/main/patch/600-mtd-spinand-add-skyhigh-robust-read-workaround.patch)，
  适配当前 Linux 6.18 API；覆盖普通 Page Read 与 Continuous Read，厂商 ID `0x01` 才走该逻辑，
  保留 400 ms 超时、错误返回及第二次读取的 ECC 状态。
- 无需另选 Kconfig 开关。CI 在编译前、发布前检查实际展开的内核源码两条调用路径，
  并确认 `CONFIG_MTD_SPI_NAND=y`；内核版本变化或其他 Robust Read 补丁出现时停止构建，要求重新审核。
- 这项修复只覆盖 **Linux 驱动**，不修改原厂 TCBOOT、U-Boot 或 BL2 的 NAND 读取流程，
  不修复既有 NAND 数据损坏，也不能据此断定突然重启/成砖的唯一根因。
  上游基本 SkyHigh 驱动支持与此 workaround 是两回事。

## 构建与下载

1. 仓库启用 Actions，允许 `GITHUB_TOKEN` 写入 Releases。工作流已声明所需权限。
2. 在 **Actions → AIR-UBI → Run workflow** 选择分支。`TEST=false` 编译完整固件，
   `TEST=true` 只解析配置并上传配置 artifact，**不发布固件 Release**。
3. `PACKAGE` 仍支持以字面 `\n` 分隔的包配置。`Config/PRIVATE.txt`、`Scripts/PRIVATE.sh` 扩展入口保留。
   设备选择和关键包不可通过这些入口绕过校验。
4. 每天北京时间 **01:00** 的 Auto-Clean 完成且成功后触发自动编译，编译完成时间取决于上游和 runner。
   清理保留最近 10 个 Releases 和最近 14 天运行日志，不再先清空所有版本。
5. 完整构建通过后，下载 `UBI-…` Release，或对应运行的 `AIROHA-MAIN-…` artifact。

构建继续使用 `Config/AIROHA-MAIN.txt` + `Config/GENERAL.txt`，保留 Argon、中文 LuCI、
OpenClash、Lucky、NPU、USB 和通用包配置，增加显式 `fitblk` 与 UBI recovery initramfs。
`make defconfig` 后检查实际生效的唯一设备及关键包；其他包被上游删除/改选的情况写入
`package-selection-changes.json`，不会默默宣称所有包完全一致。
公开 ImmortalWrt `master` 与 bingoguo93 的 PON 分支功能不同，不保证后者专有的 PON/ONU 包可用。

网口沿用本仓库定制：**LAN1/LAN2/LAN3 为 LAN，LAN4 为 WAN**，默认地址 `192.168.1.1`，
默认无密码，首次登录请设置密码。UBI 使用上游 DTS 的中断定义，不应用旧 TCBOOT 的全局删中断操作。
升级保留旧配置时，以设备已有网络配置为准。

## Release 文件与校验

| 文件 | 用途 |
| --- | --- |
| `*-nokia_xg-040g-md-ubi-squashfs-sysupgrade.itb` | 已完成兼容 U-Boot / all-in-UBI 布局转换设备的持久升级镜像 |
| `*-nokia_xg-040g-md-ubi-initramfs-recovery.itb` | RAM 启动/恢复；不是持久升级文件 |
| `SHA256SUMS` | 本次发布所有附件的 SHA256，按原始固件名计算 |
| `profiles.json`、`sysupgrade-metadata.json` | 构建配置中的镜像校验和与固件实际设备元数据 |
| `build.config`、`*.buildinfo`、`*.manifest` | 生效配置、源码/feeds 信息与实际包清单 |
| `skyhigh-verification.json`、`build-provenance.json` | 内核源码/补丁 SHA256、完整上游与 CI 提交 ID |
| `package-selection-changes.json` | 请求包与最终配置的差异 |

发布前校验：唯一 UBI profile、普通/连续读 workaround、内建 SPI-NAND、FIT 头及长度、
sysupgrade 与 `profiles.json` 的 SHA256、`fwtool` 提取的实际 `supported_devices`、recovery 镜像存在。
UBI 打包保留上游原始固件名称及构建信息，不混入其他设备镜像；**不发布 preloader / BL31 / U-Boot
作为刷机附件**。这些检查不代替设备上的 `sysupgrade -T` 或实机验证。

## 刷机注意事项

**原厂/TCBOOT/非 UBI 设备不能直接刷本 Release 的 sysupgrade 镜像。**
UBI profile 使用不同分区布局、UBI 内核卷和兼容 U-Boot；仅换固件文件名不会完成迁移。
首次迁移应使用适配 XG-040G-MD 的工具/流程，例如核验版本与设备兼容性的
[MedveFlasher](https://github.com/Medvedolog/nokia-router-medveflasher)，单独核对 bootloader 的 SkyHigh 支持。

迁移前必须备份**这台设备自身的全部原厂分区**，尤其 `ri`、`bosa`、MAC/校准信息和 bootloader，
将备份保存到电脑并校验可读性与 SHA256；另一台同型号的备份不能替代。
迁移/恢复使用有线 **LAN2/LAN3/LAN4**，避免 LAN1/2.5G，确认操作的设备身份、NAND 几何及目标布局，
供电稳定后才进入格式化步骤。看到 `CONFIRM FORMAT AND FLASH` 必须先完成这些检查并明确确认。
迁移后本固件默认 LAN4=WAN，访问管理界面使用 LAN2/LAN3。

已经在兼容 UBI 布局的设备，先校验下载附件的 `SHA256SUMS`，在路由器上运行：

```sh
sysupgrade -T /tmp/<实际的-nokia_xg-040g-md-ubi-squashfs-sysupgrade.itb文件名>
```

检查失败就停止，**禁止 `sysupgrade -F` 强刷**。跨布局迁移不可保留旧分区方案；是否保留网络配置
应根据迁移工具和当前固件决定。本文不执行刷机，也不要求重刷现有稳定 bootloader。
重启后核对 board 为 `nokia,xg-040g-md-ubi`、UBI 卷状态、网口及关键服务。
至少持续观察 48 小时以上的 bit-flip / ECC / UBI scrub / uncorrectable 日志与多次重启，
短时间日志干净不能证明长期稳定。

## 验证及维护

`Validate workflows and UBI guards` 在 PR、main 和修复分支运行 actionlint（含 ShellCheck）、
Bash/Python 语法检查及 UBI 错误拒绝/打包测试。本地可运行：

```sh
actionlint
for script in Scripts/*.sh; do bash -n "$script"; done
python3 -m unittest discover -s tests -v
```

配置测试不编译内核；完整构建还必须通过 patch application、内核构建、镜像与发布检查。
上游源码和第三方包仍跟随各自分支，会变化；发布附件记录精确提交，Linux 大版本改变时必须重新适配补丁。
首次成功 CI 只验证构建与产物，不代表已完成实机长期测试。

目录：`.github/workflows` 为工作流，`Config` 为包/设备配置，`Scripts` 为定制与校验脚本，
`Patches` 为随仓库保存的内核补丁。
