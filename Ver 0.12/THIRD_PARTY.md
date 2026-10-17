# 第三方组件与许可证

`tools/` 下为随包外部命令行工具，经子进程调用，各自遵循其许可证。

| 工具 | 用途 | 许可证 | 上游 |
|---|---|---|---|
| UndertaleModTool (CLI) | 提取 GameMaker `data.win` | GPL-3.0 | https://github.com/UnderminersTeam/UndertaleModTool |
| AssetStudio (CLI) | 提取 Unity 资源 | MIT | https://github.com/Perfare/AssetStudio |
| repak | 解包 Unreal `.pak` | Apache-2.0 | https://github.com/trumank/repak |
| evbunpack | Enigma Virtual Box 脱壳 | Apache-2.0 | https://github.com/mos9527/evbunpack |

`main.py` / `me/` / `selftest/` 不含上述任何项目的代码，不链接其库，仅以独立进程调用。

---

## 分发条款

`tools/utmt/` 内为 GPL-3.0 程序。整包发布 `MultiExtractor` 目录时，GPL-3.0 要求：

1. 保留许可证原文：`tools/utmt/LICENSE.txt`（已随包）
2. 提供对应源代码：随包源码，或书面的源码获取途径（GPL-3.0 第 6 节）。
   发布说明注明上游仓库与版本号：
   - 版本：`0.9.2.0`
   - 源码：https://github.com/UnderminersTeam/UndertaleModTool （tag `0.9.2.0`）
3. 整包许可证不得标为 MIT 等宽松协议

GPL 覆盖范围不含 Python 代码：独立程序置于同一压缩包分发，属 GPL 所称
"mere aggregation"（单纯聚合）。

`me/formats/ctf25/` 下的 Clickteam 解析器为自行实现，未使用 CTFAK 代码
（CTFAK：AGPL-3.0）；该部分授权方式自定。

---

## GPL 的两种处理方式

方式一：不打包 UTMT，首次运行时下载。
`tools/utmt/` 不放入发布包；首次 GameMaker 提取前运行
`python tools/setup_tools.py utmt` 下载。代价：用户端需联网。

方式二：接受 GPL，按上述三条执行（保留 LICENSE、注明源码出处）。

其余三个工具（MIT / Apache-2.0）为宽松协议：保留许可证与被许可方声明。
`tools/repak/` 已带两个 LICENSE 文件；`tools/assetstudio/` 和 `tools/evbunpack/`
上游未随包放许可证文件，自上游仓库的 `LICENSE` 复制一份。
