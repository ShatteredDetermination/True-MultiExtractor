# 第三方组件与许可证

`tools/` 下打包了若干外部命令行工具，本工具通过子进程调用它们。
它们**不是**本项目的代码，各自遵循自己的许可证。

| 工具 | 用途 | 许可证 | 上游 |
|---|---|---|---|
| UndertaleModTool (CLI) | 提取 GameMaker `data.win` | **GPL-3.0** | https://github.com/UnderminersTeam/UndertaleModTool |
| AssetStudio (CLI) | 提取 Unity 资源 | MIT | https://github.com/Perfare/AssetStudio |
| repak | 解包 Unreal `.pak` | Apache-2.0 | https://github.com/trumank/repak |
| evbunpack | Enigma Virtual Box 脱壳 | Apache-2.0 | https://github.com/mos9527/evbunpack |

本工具**自己的代码**（`main.py` / `me/` / `selftest/`）不包含上述任何项目的代码，
也没有链接它们的库，只是以独立进程的方式调用。

---

## 分发前必须注意的一件事

**`tools/utmt/` 里打包的是 GPL-3.0 的程序。**

如果你要把整个 `MultiExtractor` 目录打包发给别人（也就是「整包发布」），
那么按 GPL-3.0 的要求，你需要做到：

1. **保留许可证原文** —— `tools/utmt/LICENSE.txt` 不要删（现在已经在了）
2. **提供对应源代码** —— 可以是随包附带源码，也可以是书面的源码获取途径
   （GPL-3.0 第 6 节）。最省事的做法是在发布说明里写明上游仓库地址和版本号：
   - 版本：`0.9.2.0`
   - 源码：https://github.com/UnderminersTeam/UndertaleModTool （tag `0.9.2.0`）
3. 不要声称这个打包整体的许可证是 MIT 之类的宽松协议

好消息是：GPL 的「传染性」**不会**波及你自己的 Python 代码。
把独立程序放在同一个压缩包里一起分发，属于 GPL 所说的
"mere aggregation"（单纯聚合），不会让你的代码也被 GPL 覆盖。

而 `me/formats/ctf25/` 下的 Clickteam 解析器是**本项目自己写的**，
没有使用 CTFAK 的代码（CTFAK 是 AGPL-3.0，用它的代码会带来更强的传染性），
所以这部分你完全可以自己决定怎么授权。

---

## 如果不想处理 GPL 的麻烦

有两个办法：

**办法一：不打包 UTMT，改成首次运行时下载。**
把 `tools/utmt/` 从发布包里去掉，用户在第一次用到 GameMaker 提取时
运行 `python tools/setup_tools.py utmt` 自行下载。
代价是用户端需要联网。

**办法二：接受 GPL，按上面三条做。**
只要保留 LICENSE 并说明源码出处，实际负担很小。

其余三个工具（MIT / Apache-2.0）都是宽松协议，
只需保留许可证和被许可方声明即可，`tools/repak/` 下已经带了两个 LICENSE 文件。
`tools/assetstudio/` 和 `tools/evbunpack/` 上游没有随包放许可证文件，
建议从上游仓库的 `LICENSE` 复制一份进去。
