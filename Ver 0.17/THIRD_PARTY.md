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

## 两条通道（V0.17）

V0.17 起每种引擎都提供两条通道：**自研解析**与**第三方工具**。
默认第三方；`--experimental` + `--self`（GUI 上两个勾）同时成立才换自研。

| 引擎 | 自研 | 第三方 | 自研状态 |
|---|---|---|---|
| GameMaker | `me/formats/gamemaker_win.py` 等 | UndertaleModTool CLI | 已实现 |
| Godot | `me/formats/godot_pck.py` | —（不接） | 已实现 |
| Electron | `me/formats/asar.py` | —（不接） | 已实现 |
| RPG Maker | `me/formats/rgssad.py` | —（不接） | 已实现 |
| Clickteam Fusion | `me/formats/ctf25/` | —（**CTF 除外，不接第三方**） | 已实现 |
| Unreal | `me/formats/ue_pak.py`（pak v0–v9） | repak | 已实现（UE5 的 v10/v11 待补） |
| Enigma Virtual Box | `me/formats/evb.py`（只读虚拟文件系统） | evbunpack | 已实现（不做 PE 还原） |
| Unity | 待补 | AssetStudio | V0.17 未完成 |

没有第三方通道的引擎（Godot / Electron / RPG Maker / CTF）不受开关影响，
永远走自研 —— 不是「勾了才换」，是本来只有这一条路。CTF 是用户指示里点名的
例外：`me/formats/ctf25/` 自行实现，不引入 CTFAK（AGPL-3.0）。

判定与状态查询：`me/channel.py`（唯一实现）、`python main.py --channels`。
自研没覆盖的引擎勾了自研也会明确退回第三方，并在 `notes` 里写明原因。

---

## GameMaker 的两条通道（V0.17 实测）

* 第三方：`tools/utmt/UndertaleModCli.exe dump <file> -o <dir> -s -t --sprites -c UMT_DUMP_ALL`
  → `strings.txt` / `CodeEntries/*.gml` / `EmbeddedTextures/*.png` / `Sprites/*.png`。
  UTMT 不导音频与字体。
* 自研：`me/formats/gamemaker_win.py` + `gamemaker_image.py` + `gamemaker_extract.py`，
  只依赖标准库，另外还能出音频、字体、路径、选项表。
* 第三方解不出东西（UTMT 版本表外）时自动退回自研，两个都失败才算失败。

实测对照（`Deltarune\DELTARUNE\data.win`，2.9MB，bytecode 17）：

| 通道 | 文件数 | 图片 | 音频 | 文本 | 耗时 |
|---|---|---|---|---|---|
| 第三方 UTMT | 123 | 21 | 0 | 102 | 1.8s |
| 自研 | 39 | 28 | 9 | 2 | 1.2s |

（自研把整张图集按页切，UTMT 出的是按帧切好的精灵，文件数不能直接比大小 ——
写清覆盖差异比对齐数字有用。）

`tools/utmt/` 是 GPL-3.0。以独立进程调用不传染本项目的许可证，但**再分发时必须
一并保留 UTMT 的许可证与来源说明**（该目录随上游发布物自带）。

---

## 宽松协议工具的处理

AssetStudio / repak / evbunpack 均为宽松协议：保留许可证与被许可方声明即可。
`tools/repak/` 已带两个 LICENSE 文件；`tools/assetstudio/` 和
`tools/evbunpack/` 上游未随包放许可证文件，自上游仓库的 `LICENSE` 复制一份。
