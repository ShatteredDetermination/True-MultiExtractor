# 第三方组件与许可证

`tools/` 下为随包外部命令行工具，经子进程调用，各自遵循其许可证。

| 工具 | 用途 | 许可证 | 上游 |
|---|---|---|---|
| AssetStudio (CLI) | 提取 Unity 资源 | MIT | https://github.com/Perfare/AssetStudio |
| repak | 解包 Unreal `.pak` | Apache-2.0 | https://github.com/trumank/repak |
| evbunpack | Enigma Virtual Box 脱壳 | Apache-2.0 | https://github.com/mos9527/evbunpack |

`main.py` / `me/` / `selftest/` 不含上述任何项目的代码，不链接其库，仅以独立进程调用。

---

## GameMaker

0.14 起不再使用 UndertaleModTool。`data.win` 由自研解析器直接读，
实现位于 `me/formats/gamemaker_win.py`、`gamemaker_image.py`、
`gamemaker_extract.py`，全部只依赖 Python 标准库。

因此发布包内**不含任何 GPL 组件**，`tools/utmt/` 已从源码树与发布包移除，
`python tools/setup_tools.py utmt` 亦不再提供。

`me/formats/ctf25/` 下的 Clickteam 解析器同样是自行实现，未使用 CTFAK 代码
（CTFAK：AGPL-3.0）。

---

## 宽松协议工具的处理

AssetStudio / repak / evbunpack 均为宽松协议：保留许可证与被许可方声明即可。
`tools/repak/` 已带两个 LICENSE 文件；`tools/assetstudio/` 和
`tools/evbunpack/` 上游未随包放许可证文件，自上游仓库的 `LICENSE` 复制一份。
