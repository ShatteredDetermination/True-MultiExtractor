# 传参规范（Ver 0.18）

程序名：`MultiExtractorCLI.exe`
事件流与退出码的完整约定见同目录的 `GUI-API.md`，本文件只讲参数。

## 1. 调用形式

```
MultiExtractorCLI.exe [<target>] [选项...]
```

不带任何参数 = 交互式 CLI 菜单（双击 exe 的效果）。
带 `<target>` 或任一选项 = 非交互执行，跑完即退出。

## 2. 参数总表

| 参数 | 取值 | 默认 | 说明 |
|---|---|---|---|
| `<target>` | 路径 | 无 | 游戏目录、单个 exe、或 `data.win` 等数据文件 |
| `-o, --out` | 目录路径 | `setup.ini` 的 `path` + 游戏名 | 输出根目录 |
| `-e, --engine` | 引擎键 | 自动识别 | 强制引擎，跳过识别 |
| `--only` | 分类列表 | 全部 | 逗号分隔；半角全角都认；大小写不敏感 |
| `--no-deep` | 开关 | 关 | 不解容器，仅收集散落文件 |
| `--keep-work` | 开关 | 关 | 保留 `work/` 中间产物 |
| `--detect` | 开关 | 关 | 只识别引擎，不提取；需要 `<target>` |
| `--rebuild` | 开关 | 关 | 全量重建 GameMaker 容器（不等长），写 `<名字>_rebuilt.win` |
| `--repair` | 开关 | 关 | 只重建 GameMaker 容器，写 `<名字>_rebuilt.win`，不提取 |
| `--fix` | 开关 | 关 | 先重建再提取，重建产物落在 `work/` |
| `--experimental` | 开关 | 关 | 启用实验性功能（换自研通道的前置开关） |
| `--self` / `--no-self` | 开关 | `--no-self` | 使用自研拆包核心；**必须同时给 `--experimental`** |
| `--channels` | 开关 | 关 | 列出每个引擎的自研 / 第三方通道状态 |
| `--tools` | 开关 | 关 | 检查外部 CLI 是否齐全（缺工具退出码为 4） |
| `--tools-dir` | 目录路径 | 无 | 指定外部工具根目录，写进 `toolsdir.ini`，下次启动生效 |
| `--tools-refresh` | 开关 | 关 | 强制重新拉远端 `tools.ini`（默认启动时后台拉一次） |
| `--tools-get` | 工具键，可多个 | 全部缺失的 | **程序自己下载**缺失的外部工具；只给某几个键就只下它们 |
| `--tools-get-full` | 开关 | 关 | 允许为了补工具下载全量包（约 140 MB）；不加就只走直链 |
| `--update` | `verid=xxxx` / `beta` / `stable`，可省略 | 省略=自动选最新 | 在线更新：查 `version.json`、比版本、下更新包、校验 SHA256、拉起守护进程后退出 |
| `--engines` | 开关 | 关 | 列出引擎模块 |
| `--categories` | 开关 | 关 | 列出分类 |
| `--setup` | 开关 | 关 | 打印配置文件的真实位置与当前保存路径 |
| `--version` | 开关 | 关 | 只打印版本 |
| `-y, --yes` | 开关 | 关 | 不询问；`--json` 下自动生效 |
| `--json` | 开关 | 关 | 输出 NDJSON 事件流；**GUI 调用必须加** |
| `--cancel-file` | 文件路径 | 无 | 取消信号文件；**仅在 `--json` 下有效** |
| `-h, --help` | 开关 | — | 帮助 |

路径可以带引号，程序会自行去掉首尾空白、引号与 BOM。

## 2.1 外部工具清单（tools.ini）与软键菜单

V0.18E 起发布包是 **Slim**：不带 `tools/` 那 178 MB 外部 CLI。缺的工具靠
三级来源的清单 + 首次运行的软键菜单现补：

| 级别 | 来源 | 何时生效 |
|---|---|---|
| 1 | 远端 `tools.ini` | 启动时后台线程拉一次，3 秒超时，**永不阻塞主流程** |
| 2 | 本地缓存 | `%LOCALAPPDATA%\MultiExtractor\tools.ini`，上次成功拉到的原文 |
| 3 | 内置兜底 | 代码里的 `me/tools/manifest.py:BUILTIN`，断网也有清单可显示 |

`--tools` 会打印清单来源、上游地址、每个工具的期望路径，缺工具时给出全量包
地址与修复命令。环境变量 `ME_TOOLS_URL` / `ME_TOOLS_TIMEOUT` 可覆盖地址与超时。

首次运行、没网、也没缓存时，交互式菜单会先问一次软键（数字键直接选）：

| 软键 | 作用 |
|---|---|
| `1` | **自动下载**缺失的外部工具（程序自己下、自己解压、自己验；不是只给个链接） |
| `2` | 扫描常见目录（程序目录 / 桌面 / 下载 / `C:\Tools` 等），自动认出工具根 |
| `3` | 手动指定工具根目录，写 `toolsdir.ini` |
| `4` | 仅用自研核心继续（第三方通道关闭，本次会话不再提示） |

### 自动下载（`--tools-get`，等同软键 `1`）

下载逻辑在 `me/tools/fetch.py`，原则是「程序自己把文件取回来」：

* 清单里带 `url` 的工具走直链，**按镜像顺序重试**（`ME_TOOLS_MIRROR` 可覆盖，
  空串=直连；默认 `""` → `ghfast.top` → `ghproxy.net` → `gh-proxy.com`）；
* `url` 是 zip 就解压到 `<工具根>/<子目录>/`（自动剥掉多余的顶层目录），
  是单个 exe 就直接放进去；
* 装完按清单里 `paths` 的第一条**验一遍**，验不过算失败；
* 没有 `url` 的工具（AssetStudio 上游只发 `.7z`）才需要全量包：下载一次
  `[meta] full_package` 的 zip，只挑 `tools/<子目录>/` 出来；
* 安装包缓存在 `%LOCALAPPDATA%\MultiExtractor\download`，重装不再重下；
* 下载失败**不抛异常**，只返回带原因的结果，`.part` 半截文件会删掉。

全量包一百多 MB，所以 `--tools-get` 默认**不**下它（要显式 `--tools-get-full`），
交互式软键里会先问一句 `[Y/n]`。

`ME_NO_SOFTKEY=1` 可跳过软键菜单（脚本与自测用）。

### 在线更新（`--update`）

```bat
MultiExtractorCLI.exe --update                 :: 自动选（有更高的公测版就提示公测版）
MultiExtractorCLI.exe --update verid=0.18E     :: 点名要这个版本
MultiExtractorCLI.exe --update beta            :: 只要公测通道
MultiExtractorCLI.exe --update stable          :: 只要稳定通道
MultiExtractorCLI.exe --update -y              :: 不询问，直接装
```

远端清单（仓库根目录 `version.json`，可用 `ME_UPDATE_URL` 覆盖）::

```json
{
  "latest_stable": "0.17R",
  "latest_beta": "0.18E",
  "download_url": "https://.../UpdatePack.zip",
  "sha256": "abcd...",
  "changelog": "修复了..."
}
```

**版本比较规则**：剥离 `E / Rc / S / WIP` 后缀，**只比主版本号**
（`0.18E` 与 `0.18R` 视为同一版，不会互相催更新）。
同一条版本线要换后缀，得用 `verid=0.18R` 明确点名。

流程与硬约束：

| 步骤 | 说明 |
|---|---|
| 拉清单 | 3 秒超时；断网 / 超时 / JSON 坏掉都**静默失败**，只打一行 `[warn] 检查更新超时` |
| 比版本 | 见上；本机是稳定版而远端有更高公测版时，问「有公测版可用，是否更新？」 |
| 下载 | `UpdatePack.zip` 落到程序根，单行原地刷新进度条 |
| 校验 | SHA256 与清单不一致 → 报错并**删掉文件**（绝不留半截包） |
| 守护 | 写 `daemon.bat` → 静默拉起 → **主程序退出**（释放 exe / dll 文件锁） |
| 覆盖 | `daemon.bat` 等 2 秒 → `tar -xf UpdatePack.zip -C "%~dp0."` → 删包 → `start` 新程序 → 自删除 |

退出码：`0` 已是最新 / 已取消 / 已交接给守护进程；`70` 下载或校验失败。
`--json` 下事件流会发 `update_check` / `update_ready` / `update_launch` / `update_failed` / `update_cancelled`。

启动自检（每次启动都做）：

1. 盘上有 `daemon.bat` → 静默删掉（说明上轮更新已跑完）；
2. 盘上有 `UpdatePack.zip` → 交互式问「是否立即安装？(Y/N)」；
   非交互（管道 / 计划任务 / `--json`）**不询问**，只发 `update_pending` 事件并保留文件。

### 交互菜单里的斜杠指令

不带参数启动就是交互菜单。**路径输入那一行**和**所有菜单的「请选择」**都认
行首带斜杠的指令（没进菜单也能用 `/settings`）：

| 指令 | 作用 |
|---|---|
| `/help`（`/h` `/` `/?` `/帮助`） | 列出所有指令 |
| `/settings`（`/s` `/配置` `/设置`） | 设置面板：保存位置、外部工具目录、会话开关 |
| `/tools` | 外部工具状态（缺了会说怎么补） |
| `/channels` | 每个引擎的拆包通道 |
| `/setup` | `setup.ini` 的位置与当前值 |
| `/update` | 检查在线更新 |
| `/quit`（`/q` `/exit` `/退出`） | 退出菜单 |

`/settings` 面板：

```
[1] 保存位置      Extract Resources  (-> <程序目录>\Extract Resources)   ← 写 setup.ini
[2] 外部工具目录  <程序目录>\tools（默认）                              ← 写 toolsdir.ini
[3] 外部工具      4/4 就位（缺哪个会直接写出来）
[4] 实验性功能    [关]      [5] 自研打包核心 [关]
[6] 不解容器      [关]      [7] 保留中间产物 [关]
[8] 检查外部工具（缺哪个、怎么补）
[9] 自动下载缺失的工具
[10] 清空工具目录（回到默认）    [0] 返回
```

| 项 | 落盘 | 说明 |
|---|---|---|
| `1` | `setup.ini` | 永久生效 |
| `2` `10` | `toolsdir.ini` | 永久生效；`10` 是清回默认 |
| `4`–`7` | 仅本次会话 | 等价 `--experimental` / `--self` / `--no-deep` / `--keep-work`，改完回菜单立刻按新开关走 |
| `3` `8` | — | 完整自检：清单来源、每个工具的实际路径、缺了怎么补（与 `--tools` 同一套代码） |
| `9` | 下载到工具根 | 与软键 `1` 同一条路（直链 → 镜像 → 全量包兜底），下完回面板看 `[3]` 就知道成没成 |

**外部工具完整性检查**复用 `--tools` 的实现，菜单与命令行永远一致：装齐了显示
`[ok] 外部工具齐全`；缺了给三条补救路（`--tools-get` 自己下 / 全量包地址 /
`--tools-dir` 指过去）。

## 3. 取值域

**分类**（`--only`）：`image` `audio` `text` `font` `video` `other`

**引擎键**（`-e`）：

| 键 | 引擎 |
|---|---|
| `electron` | Electron / NW.js |
| `gamemaker` | GameMaker (data.win) |
| `clickteam` | Clickteam Fusion 2.5 |
| `enigma` | Enigma Virtual Box |
| `godot` | Godot |
| `rpgmaker` | RPG Maker |
| `unity` | Unity |
| `unreal` | Unreal Engine |
| `generic` | 通用收集（按扩展名） |

不传 `-e` = 自动识别；识别不出来自动退回 `generic`。

## 4. 参数交互与优先级

**子命令互斥**，只执行第一个命中的，判定顺序固定：

```
--version → --categories → --tools → --engines → --channels → --setup
          → --detect → --rebuild → --repair → 提取
```

`--repair` 与 `--fix` 只对 GameMaker 生效：前者不提取，后者先重建再提取。
两者都给时 `--repair` 优先（不提取）。`--rebuild` 与 `--repair` 都给时
`--rebuild` 优先（全量重建，长度会变）。

其余规则：

| 规则 | 说明 |
|---|---|
| `-o` 覆盖 `setup.ini` | 传了 `-o` 就完全忽略配置里的 `path` |
| `-e` 覆盖自动识别 | 传了 `-e` 就不看识别结果 |
| `--only` 只在提取时生效 | 与 `--detect` 同时给，`--detect` 优先，`--only` 被忽略 |
| `--json` 隐含 `-y` | 同时隐含不暂停 |
| `--cancel-file` 依赖 `--json` | 不加 `--json` 时该参数被忽略，不会报错 |

## 5. 拆包通道（V0.17）

每种引擎都有**自研**与**第三方**两条拆包通道。默认第三方；
`--experimental` 与 `--self` **同时**给出才换自研。

| 情况 | 走哪条 |
|---|---|
| 只给 `--self` | 第三方（`note` 里说明缺实验性功能） |
| 只给 `--experimental` | 第三方 |
| 两个都给 | 自研（该引擎自研已实现时） |
| 两个都给，但该引擎自研没覆盖 | 第三方（`note` 里写明待补） |
| 第三方工具缺失 | 自研（自研也没有则报错） |
| CTF（clickteam） | 永远自研，不接第三方，与开关无关 |

判定只有一处实现：`me/channel.py` 的 `decide()`。当前状态用 `--channels` 查：

```
python main.py --channels
```

提取时事件流会多一条 `channel`：`{t:"channel", engine, channel:"self"|"third",
label:"自研"|"第三方", note}`，`result.channel` 同值。GUI 上有两个勾：
「我已知晓并同意启用实验性功能」（CheckBox1，总闸）与「使用自研拆包核心」
（CheckBox7，总闸不勾时是灰的），两个都勾上才会传 `--experimental --self`。

## 6. 退出码

| 码 | 含义 |
|---|---|
| `0` | 成功 |
| `1` | `<target>` 不存在或不可读 |
| `2` | 参数错误（未知参数、非法分类、`--json`/`--detect` 缺目标） |
| `3` | 执行结束，未提取到任何文件 |
| `4` | 缺少外部 CLI（仅 `--tools`） |
| `5` | 被 `--cancel-file` 取消 |
| `70` | 未预期的内部异常 |
| `130` | Ctrl+C |

## 7. 输出约定

| 流 | 编码 | 内容 |
|---|---|---|
| stdout | UTF-8 | 加 `--json` 后只走事件流，一行一个 JSON 对象 |
| stderr | UTF-8 | 人读文本、进度条、外部 CLI 原始输出 |

不加 `--json` 时人读文本仍走 stdout，与 0.12 一致。

`result.notes` 里的字符串与 stderr 上已经打过的那几行**逐字相同**，壳不必
再打一遍，否则日志里每条都会出现两次。

## 8. 调用示例

```bat
:: 提取全部类型
MultiExtractorCLI.exe "D:\Games\SomeGame" -o "D:\out" --json

:: 只要图片和音频
MultiExtractorCLI.exe "D:\Games\SomeGame" -o "D:\out" --only image,audio --json

:: 直接丢 data.win
MultiExtractorCLI.exe "D:\Games\SomeGame\data.win" -o "D:\out" --json

:: 强制引擎
MultiExtractorCLI.exe "D:\Games\SomeGame" -o "D:\out" -e gamemaker --json

:: 只识别
MultiExtractorCLI.exe "D:\Games\SomeGame" --detect --json

:: 只重建容器结构（原版 UTMT 读不了时用）
MultiExtractorCLI.exe "C:\Users\Administrator\Downloads\data1.win" --repair --json

:: 重建后再提取
MultiExtractorCLI.exe "D:\Games\SomeGame\data.win" -o "D:\out" --fix --json

:: 取消（另开一个进程写这个文件）
MultiExtractorCLI.exe "D:\Games\SomeGame" -o "D:\out" --json --cancel-file "D:\out\cancel.flag"

:: 建界面用的自描述命令
MultiExtractorCLI.exe --categories --json
MultiExtractorCLI.exe --engines --json
MultiExtractorCLI.exe --tools --json
MultiExtractorCLI.exe --channels --json

:: 换自研通道（两个开关都要给）
MultiExtractorCLI.exe "D:\Games\SomeGame\data.win" -o "D:\out" --experimental --self --json

:: 查每种引擎现在能不能走自研
MultiExtractorCLI.exe --channels
```

## 9. 边界行为（已实测）

| 输入 | 结果 |
|---|---|
| `--only image，audio`（全角逗号） | 正常，等价半角 |
| `--only IMAGE`（大写） | 正常，大小写不敏感 |
| `--only image,`（末尾多余逗号） | 正常，空项被跳过 |
| `--only bogus` | 退出码 `2`，stderr 打印 `未知分类: bogus（可选: ...）` |
| `-e nosuch`（不存在的引擎键） | **退出码仍是 `0`**：发 `warn` 事件后退回通用收集。见下方「已知坑」 |
| `--detect` 不带 `<target>` | 退出码 `2` |
| `--json` 不带任何目标与子命令 | 退出码 `2`（直接报错，**不会**掉进交互菜单） |
| `--cancel-file` 不带 `--json` | 参数被忽略，正常执行 |
| `-h, --help` | 退出码 `0`，帮助文本走 stderr |

### 已知坑

1. **`-e` 传错引擎键不报错。** 程序只发一条 `warn`，然后按通用收集跑，
   退出码是 `0`。壳要自己校验引擎键（用 `--engines --json` 取合法列表），
   否则用户看到的"成功"其实是降级结果。
2. **`--json` 下必须传 `-o`。** 不传时输出会落到 exe 旁边的
   `Extract Resources\<游戏名>\`。真实路径从 `result.out_dir` 取，不要自己拼。
3. **`--only other` 是合法值**，用来只收引擎专有格式（如 Unreal 的 `.uasset`）。

## 10. 交互菜单（不带参数运行）

双击 exe 或 `python main.py` 进的就是这里。菜单是**渐进式**的：没分析目标之前
不给提取/重建，避免还不知道识别结果就乱按。所有动作最终都拼成命令行参数、
再用同一张 argparse 表解析一遍（`me/menu_flow.py` + `main._run_argv`），
菜单与命令行不可能走岔。

### 10.1 第一步：问路径 → 只给三件事

```
输入游戏路径（文件或目录）:
  [0] 分析目标（--detect）
  [1] 更换路径
  [2] 退出
```

分析走的就是 `--detect` 那条路径（`me/scanner/detect.py`），打印引擎键、
主数据文件、证据链；识别失败也继续，退回通用收集。

### 10.2 第二步：分析完，菜单按引擎能力动态生成

```
[检测结果]: GameMaker（主数据 data.win）
保存到: Extract Resources  (-> <发布目录>\Extract Resources)
拆包通道: 第三方 —— 第三方工具（依赖 UndertaleModTool CLI）

请选择接下来的操作:
  [0] 提取资源（--extract）
  [1] 修复/重建 data.win（--rebuild / --repair）   ← 只有 GameMaker 才有
  [2] 切换拆包通道（当前: 第三方）                  ← 只有双通道引擎才有
  [3] 识别详情
  [4] 更换路径
  [5] 更多选项（不解容器 / 中间产物 / 自检 / 配置 / 版本）
  [6] 退出
```

- 修复/重建只对 `gamemaker` 出现：别的引擎没有 `data.win` 结构可修。
- 通道切换只对「有第三方且自研已就绪」的引擎出现（electron / godot /
  rpgmaker / clickteam 这类单一通道的不显示；CTF 是**只有自研**的例外）。

### 10.3 子菜单

| 子菜单 | 选中项 | 等价参数 |
|---|---|---|
| 提取 | 图片 / 音频 / 文本 / 字体 / 全部 | `--only image` … 全部不带 `--only` |
| 修复/重建 | 全量重建（推荐） | `--rebuild`，文件长度会变 |
| | 原位补丁（保守） | `--repair`，长度不变 |
| | 先重建再提取 | `--fix` |
| | 取消返回 | — |
| 切换通道 | 切换到自研核心 | `--experimental --self` |
| | 切换到第三方 | 去掉 `--self`（仍保留 `--experimental`） |
| 更多选项 | 不解容器 [`开`/`关`] | `--no-deep` |
| | 保留中间产物 [`开`/`关`] | `--keep-work` |
| | 指定引擎（当前: 自动 / 键名） | `-e <键>` |
| | 工具自检 / 引擎列表 / 配置 / 版本 | `--tools` / `--engines` / `--setup` / `--version` |

方括号里是当前状态，选一次翻转一次。切通道只影响**之后**的提取/重建，
已经跑完的不追溯。

### 10.4 自测

`selftest/test_cli_menu.py`（119 断言）盯四件事：

1. 渐进式：分析前不出现提取/重建项；
2. 能力生成：GameMaker 有修复+通道，CTF/Godot 没有；
3. **索引 → 参数不错位**：修复子菜单 `0→--rebuild`、`1→--repair`、`2→--fix`，
   提取子菜单 `0→image` … `4→全部（不带 --only）`；
4. 真起子进程喂 stdin，检查渲染顺序、开关状态保持、退出码 0。