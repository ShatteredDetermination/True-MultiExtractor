# 第三方组件与许可证

`tools/` 下为随包外部命令行工具，经子进程调用，各自遵循其许可证。

| 工具 | 用途 | 许可证 | 上游 |
|---|---|---|---|
| UndertaleModTool (CLI) | 提取 / 分析 GameMaker `.win` | **GPL-3.0** | https://github.com/UnderminersTeam/UndertaleModTool |
| AssetStudio (CLI) | 提取 Unity 资源 | MIT | https://github.com/Perfare/AssetStudio |
| repak | 解包 Unreal `.pak` | Apache-2.0 | https://github.com/trumank/repak |
| evbunpack | Enigma Virtual Box 脱壳 | Apache-2.0 | https://github.com/mos9527/evbunpack |

`main.py` / `me/` / `selftest/` 不含上述任何项目的代码，不链接其库，仅以独立进程调用。

---

## 两条通道（V0.16 起）

V0.16 起每种引擎都提供两条通道：**自研解析**与**第三方工具**。

| 引擎 | 自研 | 第三方 |
|---|---|---|
| GameMaker | `me/formats/gamemaker_win.py` 等 | UndertaleModTool CLI |
| Unity | 待补 | AssetStudio |
| Unreal | 待补 | repak |
| Enigma Virtual Box | 待补 | evbunpack |
| Clickteam Fusion | `me/formats/ctf25/`（**只有自研，不接第三方**） | — |

切换方式：GUI 的 **CheckBox7**（默认走第三方，勾选后走自研），
参数侧语义一致。细节见 `ROADMAP.md`。

---

## GameMaker 的现状（V0.15）

0.14 曾把 UTMT 移出发布包、GameMaker 改成纯自研；**V0.15 把 UTMT 放回来**，
因为 V0.16 的第三方通道要用它。现在 GameMaker 有两条互不干扰的路：

* 自研（0.15 的默认）：`me/formats/gamemaker_win.py` + `gamemaker_image.py` +
  `gamemaker_extract.py`，只依赖 Python 标准库，带全局进度条。
* 第三方：`tools/utmt/UndertaleModCli.exe`，V0.16 用 CheckBox7 切换。

`tools/utmt/` 是 GPL-3.0。以独立进程调用不传染本项目的许可证，但**再分发时必须
一并保留 UTMT 的许可证与来源说明**（该目录随上游发布物自带）。

`me/formats/ctf25/` 下的 Clickteam 解析器是自行实现，未使用 CTFAK 代码
（CTFAK：AGPL-3.0），V0.16 也不接第三方。

---

## 宽松协议工具的处理

AssetStudio / repak / evbunpack 均为宽松协议：保留许可证与被许可方声明即可。
`tools/repak/` 已带两个 LICENSE 文件；`tools/assetstudio/` 和
`tools/evbunpack/` 上游未随包放许可证文件，自上游仓库的 `LICENSE` 复制一份。
