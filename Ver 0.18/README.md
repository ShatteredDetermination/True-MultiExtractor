# True MultiExtractTool

多引擎游戏资源提取工具。输入游戏目录或单个 exe；识别引擎后按 image / audio / text / font 分类导出。

仅用 Python 标准库；numpy 为可选加速。

V0.18E 起发布包是 **Slim**：**不带** `tools/` 那 178 MB 外部 CLI（CLI 目录 20 MB）。
缺的工具由 `tools.ini` 清单（远端 / 本地缓存 / 内置兜底三级）+ 首次运行的
**软键菜单**现补 —— 软键 `1` 是**真的把工具下下来**（`me/tools/fetch.py`：
直链 → 镜像重试 → 解压 → 验路径），不是给个链接让用户自己去点。
命令行等价：`MultiExtractorCLI.exe --tools-get`（只差 `.7z` 那个再加
`--tools-get-full`）。要连工具一起发给别人，用 `python make_dist.py --full`。

---

## 快速开始

### 双击运行

```
MultiExtractorCLI.exe
```

无需 Python 环境。双击运行，按提示输入游戏路径；游戏目录或 exe 可直接拖入窗口。

游戏目录或 exe 拖到 `MultiExtractorCLI.exe` 上 = 省去输入步骤。

打包版与源码版功能一致。`MultiExtractorCLI.exe` 与 `resources/` 必须位于同一层目录
（Slim 包没有 `tools/`，用了才需要）；单独拷贝 exe 无法运行。保存位置由同层 `setup.ini` 决定，见「保存位置：setup.ini」。

### 从源码运行

```
run.bat
```

要求 Python 3.8 以上。

### 命令行

```bat
:: 全部类型
MultiExtractorCLI.exe "D:\Games\SomeGame"

:: 只要图片和音频
MultiExtractorCLI.exe "D:\Games\SomeGame" --only image,audio

:: 指定输出目录
MultiExtractorCLI.exe "D:\Games\SomeGame" -o D:\out

:: 只识别引擎，不提取
MultiExtractorCLI.exe "D:\Games\SomeGame" --detect

:: 检查外部工具与运行环境（反馈问题时先运行此命令）
MultiExtractorCLI.exe --tools

:: 查看 / 定位 setup.ini（改保存位置）
MultiExtractorCLI.exe --setup

:: 保留中间产物（脱壳 / 解包结果），排查问题时使用
MultiExtractorCLI.exe "D:\Games\SomeGame" --keep-work
```

源码版：上文的 `MultiExtractorCLI.exe` 替换为 `python main.py`。

### GUI 调用（事件流）

供 WinForms 壳调用，不作为人读选项。

- 加 `--json` 后：stdout 为 NDJSON 事件流，一行一个 JSON 对象，可逐行解析
- 同一 `--json` 下：人读文本（横幅、`[ok]/[warn]/[err]`、进度条、外部 CLI 原始输出）转往 stderr
- 取消：`--cancel-file PATH`，该文件出现即取消，进程退出码 5；仅 `--json` 下有效
- 双击 `MultiExtractorCLI.exe` 仍进入原交互 CLI；`--json` 只用于壳调用
- 事件类型、字段、流的分工、接入示例：见随包的 `GUI-API.md`
- 参数逐个说明与取值域：见随包的 `CLI-ARGS.md`

| 退出码 | 含义 |
|---|---|
| `0` | 成功 |
| `1` | 目标不存在或不可读 |
| `2` | 参数错误 |
| `3` | 执行结束，未提取到任何文件 |
| `4` | 缺少外部 CLI |
| `5` | 被 `--cancel-file` 取消 |
| `70` | 未预期的内部异常 |
| `130` | Ctrl+C |

---

## 交互菜单指令（V0.18E）

不带参数启动后，**目标路径**那一行和**任何菜单的「请选择」**都能打斜杠指令：

```
  输入 /help 列出指令
目标路径: /settings
```

记不住也没关系：启动时会提醒一次；菜单里打错会跟上同一句；路径打错不会把你
踢出去，只报 `[err] 路径不存在: <路径>` 然后重问。

| 指令 | 作用 |
|---|---|
| `/settings` | 设置：输出目录 / 工具根目录 / 外部工具 / 会话开关 |
| `/help` | 列出指令 |
| `/tools` `/channels` `/setup` `/update` | 状态自检与在线更新 |
| `/quit` | 退出 |

`/settings` 面板：

```
  [设置]
        [1] 输出目录　　　 <程序目录>\Extract Resources
        [2] 工具根目录　　 <程序目录>\tools
        [3] 外部工具　　　 4/4 已安装
        [4] 实验性功能　　 [关]
        [5] 自研核心　　　 [关]
        [6] 不解容器　　　 [关]
        [7] 保留中间产物　 [关]
        [8] 检查外部工具
        [9] 下载缺失工具
        [10] 重置工具根目录
        [0] 返回
```

1、2、10 会落盘（`setup.ini` / `toolsdir.ini`），4–7 是本次会话的开关；
`[3]` 直接显示 `N/4 已安装（缺 xxx）`，`[8]` 是完整工具自检（与 `--tools` 同一套代码），
`[9]` 与软键 `1` 同一条下载路径（直链 → 镜像 → 全量包兜底）。

## 在线更新（V0.18E）

```bat
MultiExtractorCLI.exe --update          :: 查更新（有更高的公测版会先问一句）
MultiExtractorCLI.exe --update -y       :: 不问，直接装
```

远端清单是仓库根目录的 `version.json`：

```json
{
  "latest_stable": "0.17R",
  "latest_beta": "0.18E",
  "download_url": "https://.../UpdatePack.zip",
  "sha256": "abcd...",
  "changelog": "修复了..."
}
```

* **只比主版本号**：`E / Rc / S / WIP` 后缀在比较时剥掉，`0.18E` 与 `0.18R` 视为同一版；
  同一条线要换后缀得写 `--update verid=0.18R`。
* 拉清单 3 秒超时；断网只打一行 `[warn] 检查更新超时`，不挡任何事。
* 下载完必验 SHA256，不一致就删包报错。
* 替换靠 `daemon.bat`：主程序退出 → 等 2 秒 → `tar` 解压覆盖 → 删包 → 启动新版本 → 自删除。
  之所以要绕这一圈，是因为 Windows 上正在运行的 exe / dll 是锁着的，自己覆盖自己必然失败。
* 每次启动自检：清掉残留的 `daemon.bat`；发现 `UpdatePack.zip` 就问要不要立刻装
  （非交互场景只发事件，不阻塞）。

## 保存位置：setup.ini

提取结果保存位置：程序旁的 `setup.ini`。

```ini
[Output]
; 相对路径，自程序所在目录起算
path = Extract Resources

; 绝对路径，直接写完整路径
; path = D:\拆包输出
```

| 写法 | 结果 |
|---|---|
| `Extract Resources` | 存到程序旁的 `Extract Resources\` |
| `D:\拆包输出` | 存到该绝对路径 |
| `-o` 参数 | 覆盖 ini 中的设置 |

- 文件不存在时：首次运行生成默认配置
- 每个游戏在根目录下单独建立以游戏名命名的子目录；多次提取不互相覆盖
- 相对路径基准：程序所在目录；整包拷贝到其他盘符仍有效
- 编码：UTF-8（带或不带 BOM）、ANSI
- ini 损坏：退回默认值并打印解析错误

### 自行打包 exe

```bat
python build_exe.py                          :: Slim 包（默认，不带 tools/）
python build_exe.py --zip                    :: 顺便压 zip
python build_exe.py --full                   :: 全量包（带 tools/，需要先在 tools/ 里放好工具）
python build_exe.py --dest "D:\交付\Ver 0.18" :: 组装到指定目录
```

产物结构即发布包结构（Slim）：

```
MultiExtractorCLI.exe      双击运行
resources/                 Python 运行时，不可删除
setup.ini                  保存位置配置
version.ini                版本与署名
README.md  FORMATS.md  THIRD_PARTY.md  GUI-API.md  CLI-ARGS.md
```

实测体积：Slim **27 个文件 / 20.1 MB**（zip 9.4 MB）；全量包 137 MB 量级。
打包脚本会断言「Slim 里不许出现 `tools/`」「体积不超过 120 MB」，越界直接失败。

打包后校验：`python selftest/test_frozen.py`（引擎是否齐全、工具是否可定位、提取结果是否与源码版逐字节一致）。

运行时目录名：`resources`（非 PyInstaller 默认的 `_internal`）。

### GUI 壳（.NET 10 单文件）

壳在 `MultiExtractorGUI\`，产物是**一个自包含 exe**（约 47 MB，用户机器不用装任何运行时）：

```bat
dotnet publish -c Release          :: 输出到 MultiExtractorGUI\dist\
```

分发形态（**CLI 与壳同级**，壳优先在同一目录找 CLI，不需要任何配置）：

```
MultiExtractor-<版本>-win64\
├── MultiExtractorGUI.exe     壳，双击运行（.NET 10 单文件，47 MB）
├── MultiExtractorCLI.exe        CLI，壳优先在同一目录找到它（20 MB）
├── resources\                CLI 的 Python 运行时，别删
├── setup.ini                 保存位置（可手改）
└── version.ini               版本与署名（GUI 右上角读它）
```

Slim 包里**没有 `tools/`**；外部工具缺失时壳会照常启动，靠 CLI 的
`--tools`（`tools_manifest` 事件）展示清单与全量包地址。

组装发布包（壳 + CLI 一起）：

```bat
python make_release.py --zip   :: 先出源码包（会清 dist\ 与 build_exe\）
python make_dist.py            :: 再打 CLI、组 GUI+CLI 发布包并校验（默认 Slim）
python make_dist.py --full     :: 要连 tools\ 一起发时用这个
```

壳定位 CLI 的顺序（`Form1.RequireCli`）：

1. 环境变量 `ME_CLI_DIR` —— 快捷方式/部署脚本显式指定
2. `<壳所在目录>\MultiExtractorCLI.exe` —— **同级目录，打包默认，优先**
3. `<壳所在目录>\cli\MultiExtractorCLI.exe` —— cli 子目录（兼容旧包）
4. `%LOCALAPPDATA%\MultiExtractor\CLIPath.ini` 的 `[CLIDirPath] Dir` —— 安装式

前三个都找不到 exe 时会退而求其次找 `main.py` + Python（源码形态，标题会标
`[DEVELOPER MODE]`）。

**没部署过的机器**（三种都不存在时）：壳右上角版本位置会变成红字
`未找到 CLI · 点此定位`，点它选 CLI 目录即可 —— 壳会校验 PE 头、试跑一次
`--version`，通过后自己写 `CLIPath.ini`，并立刻加载版本/分类/引擎列表。
不需要用户手写任何 ini。

`CLIPath.ini` 与 `setup.ini` 的生成时机：

| 文件 | 位置 | 谁写、什么时候 |
|---|---|---|
| `CLIPath.ini` | `%LOCALAPPDATA%\MultiExtractor\` | 壳首次成功定位后写一次；CLI 每次运行都重写（整包挪盘后壳能立刻找到） |
| `version.ini` | CLI 目录下 | `build_exe.py` 打包时写一次；CLI 每次运行重写 |
| `setup.ini` | CLI 目录下 | CLI 首次运行自动生成（带注释）；也可手改 |

所以「CLI 先跑过」或「壳点一次定位」二者任一发生，配置就齐了。
exe；代价是**放弃 ClickOnce**（单文件/自包含不支持），分发从「安装器」变成
「拷目录」。旧工程留档在 `..\legacy-framework472\`。

---

## 支持的引擎

| 引擎 | 默认通道 | 自研通道 |
|---|---|---|
| Electron / NW.js | 自写 asar 解析（纯 Python） | 同左（不接第三方） |
| GameMaker | **UTMT CLI**（字符串/代码/纹理/精灵帧） | `me/formats/gamemaker_*.py`，另有音频/字体；UTMT 读不了的文件靠它 |
| Clickteam Fusion 2.5 | 自写解析器（纯 Python，不使用 CTFAK） | 同左（**CTF 除外，不接第三方**） |
| Enigma Virtual Box（保护壳） | evbunpack 脱壳 + 递归识别内层 | 已实现 `me/formats/evb.py`（只读文件系统） |
| Godot | 自写 .pck 解析（纯 Python） | 同左（不接第三方） |
| RPG Maker (XP/VX/VX Ace) | 自写 RGSSAD 解密（纯 Python） | 同左（不接第三方） |
| Unity | AssetStudio CLI | 待补（V0.17） |
| Unreal Engine | repak | 已实现 `me/formats/ue_pak.py`（pak v0–v9） |
| 其他 / 未识别 | 通用扩展名收集 | 同左（兜底） |

通道切换（V0.17）：默认**第三方**；`--experimental` 与 `--self` 同时给出才换自研
（GUI 上是「同意启用实验性功能」+「使用自研拆包核心」两个勾）。
`python main.py --channels` 查每个引擎的两条通道状态。

引擎未识别：退回通用收集，目录内全部可识别资源按分类导出。

输入可为 `data.win` / `game.droid`，无需整个游戏目录。识别依据：`FORM....GEN8` 容器头（与文件名无关）。

### GameMaker 读不动的文件怎么处理

解析器不查版本表，chunk 内布局按文件实测。真读不动时的顺序：

1. 直接解析容器
2. 失败后清空出问题的 chunk（原长占位）重试。长度不可变，文件内存有绝对偏移

不提供纯按字节切的资源路径。内嵌 PNG / WAV 整块 = 整张纹理页（图集），非切好的单个精灵；精灵坐标在 TPAG 里，必须按结构取。

`--detect` 与提取结果均标注所用层。

---

## 环境变量

未设置时用默认值。

| 变量 | 作用 |
|---|---|
| `ME_AES_KEY` | Unreal 加密 pak 的 AES 密钥（hex 或 base64） |
| `ME_UNITY_GAME` | Unity 的 `--game` 渠道值，默认 `Normal`；加密或特殊渠道游戏需更换 |
| `ME_UNITY_IMAGE_TYPES` | 覆盖 Unity 图片一轮的 `--types`；空串 = 不限类型、全量导出 |
| `ME_UNITY_AUDIO_TYPES` | 覆盖 Unity 音频一轮的 `--types`；同上 |
| `ME_TEXTURE_PAGES` | 设为 `1` 时，GameMaker 额外导出整张纹理页（体积大，与精灵重复） |
| `ME_NO_PAUSE` | 设为 `1` 时，双击运行结束后不等待按键（脚本 / 自动化调用） |

例：

```bat
set ME_UNITY_GAME=UnityCN
python main.py "D:\Games\SomeGacha"
```

---

## 输出结构

```
<setup.ini 内配置的目录>/<游戏名>/
├── image/          图片（png / jpg / gif ...）
├── audio/          音频（ogg / wav / mp3 ...）
├── text/           文本、字符串表、应用信息
├── font/           字体（ttf / otf ...）
├── video/          视频
├── other/          其他
├── manifest.json   完整清单（每个文件的来源、大小、分类）
└── REPORT.txt      人类可读的统计报表
```

默认根目录：程序旁的 `Extract Resources`；`setup.ini` 可换位置。

`manifest.json` 每条记录带 `origin` 字段（来源容器）。

---

## 引擎实现

### Clickteam Fusion 2.5（自写解析器）

不使用 CTFAK。实现：`me/formats/ctf25/`。

| 文件 | 职责 |
|---|---|
| `binary.py` | 字节读取器（小端整数 / ASCII / UTF-16LE） |
| `ids.py` | chunk 编号 → 名字对照表 |
| `lz4.py` | LZ4 block 解压（图像数据用） |
| `container.py` | 在文件内定位 CCN 数据流 |
| `chunks.py` | chunk 流遍历与解压 |
| `imagebank.py` | 图像条目解析 |
| `imagecodec.py` | 像素格式 → RGBA |
| `soundbank.py` | 音频条目解析 |
| `fontbank.py` | 字体元数据 + 内嵌 TTF |
| `extractor.py` | 对外入口 |

两种发布形态均支持：`.dat` 数据文件形态、单文件 exe 形态。

### Enigma Virtual Box

先 `evbunpack` 脱壳，再重新识别内层引擎并递归提取。例如 GameMaker + Enigma 壳的 exe：脱壳 → 定位 `data.win` → 移交 GameMaker 模块。

### GameMaker

两条通道（V0.17）：

* **默认第三方**：`tools/utmt/UndertaleModCli.exe dump`，出字符串、反编译 GML、
  纹理页、精灵帧；UTMT 不导音频与字体。
* **自研**（`--experimental --self`）：`me/formats/gamemaker_win.py` 读结构、
  `gamemaker_image.py` 解 PNG / QOI / 2zoq、`gamemaker_extract.py` 按 TPAG 切片落盘。
  另外还能出音频、字体、路径、选项表；UTMT 版本表外的文件只有它能拆。

第三方解不出来时自动退回自研，notes 里写明走了哪条。

---

## 目录结构

```
MultiExtractor/
├── main.py                 入口（交互菜单 + 命令行）
├── run.bat                 双击启动（源码运行）
├── build_exe.py            打包成 exe
├── make_release.py         发布前清理 / 打源码包
├── setup.ini               提取结果的保存位置（首次运行自动生成）
├── README.md               本文件
├── FORMATS.md              各容器格式笔记（含证据等级）
├── GUI-API.md              GUI 接口契约（事件流、退出码）
├── CLI-ARGS.md             命令行参数规范
├── THIRD_PARTY.md          第三方工具许可证
├── me/                     主程序包
│   ├── config.py           全局常量、路径
│   ├── setup_ini.py        setup.ini 的读写（容忍 BOM / GBK / UTF-16）
│   ├── console.py          控制台代码页、双击启动时防止窗口自动关闭
│   ├── ui.py               控制台输出（含进度条）
│   ├── events.py           机器可读事件流（NDJSON）
│   ├── runner.py           流程编排：识别 → 选引擎 → 提取 → 生成报表
│   ├── scanner/            引擎识别
│   │   ├── signatures.py   特征表（新增引擎仅修改此处）
│   │   ├── dirscan.py      目录索引
│   │   └── detect.py       综合判定
│   ├── collect/            资源收集
│   │   ├── kinds.py        扩展名 → 分类表
│   │   ├── gather.py       目录遍历
│   │   ├── writer.py       输出写出（路径清洗、去重、登记）
│   │   ├── manifest.py     清单 / 报表
│   │   ├── pngwrite.py     最小 PNG 编码器（避免依赖 Pillow）
│   │   └── sniff.py        按文件头判定格式
│   ├── formats/            各容器格式的纯 Python 解析器
│   │   ├── asar.py         Electron
│   │   ├── ctf25/          Clickteam Fusion 2.5（10 个小文件）
│   │   ├── godot_pck.py    Godot
│   │   └── rgssad.py       RPG Maker
│   ├── engines/            各引擎的提取流程
│   │   ├── base.py         接口定义
│   │   ├── menu.py         交互菜单用的引擎列表
│   │   └── *.py            每个引擎一个文件
│   └── tools/              外部工具的清单与定位（V0.18E 起不再随包发二进制）
│       ├── manifest.py     tools.ini 三级来源：远端 / 缓存 / 内置兜底
│       ├── paths.py        本机落点：toolsdir.ini → tools/ → 程序根
│       ├── softkey.py      首次无网的 4 颗软键菜单
│       ├── fetch.py        自动下载外部工具（直链 / 镜像 / 全量包 / 缓存）
│   ├── update.py           在线更新：清单比对、下载校验、daemon.bat、启动自检
│   ├── commands.py         交互菜单的斜杠指令（/settings 等）
│       └── proc.py         stream.py  外部 CLI 的调用与实时输出
├── tools/                  **Slim 包里没有这一层**；源码树里只剩维护者脚本
│   └── setup_tools.py      维护者用：下载 / 更新外部工具（utmt / assetstudio / evbunpack / repak）
└── selftest/               自测脚本
    ├── run_all.py          执行全部（32 个脚本，全部离线可跑）
    ├── test_samples.py     真实样本端到端回归
    ├── test_tools_manifest.py  tools.ini 三级来源（file:// 冒充远端）
    ├── test_softkey.py     软键菜单 4 颗键的每条分支
    ├── test_slim_selfcore.py  没有 tools/ 时自研核心独立跑通提取 + 全量重建
    ├── test_tools_fetch.py  外部工具自动下载（file:// 冒充网络）
    ├── test_update.py       在线更新：本地 HTTP 假远端 + daemon 闭环
    ├── test_commands.py     斜杠指令：解析、/settings 每项、真跑菜单
    ├── test_setup_ini.py   配置读取（含 BOM / GBK / UTF-16）
    ├── test_frozen.py      打包版 exe 自测（未打包时跳过）
    └── test_*.py           各模块的合成样本测试
```

版本归档的目录约定（本机）：

```
C:\Users\Administrator\Desktop\
└── MultiExtractorDeveloping\   正在开发的版本就放这里（改版本号时同步一次）

C:\MultiExtractor\
├── Source Code\
│   ├── Ver 0.16\           已发布版（= 原 0.15E，全量重建）
│   ├── Ver 0.17\           已发布（0.17R）
│   └── Ver 0.18\           已归档（0.18E 阶段的快照）
└── Compiled\
    ├── Ver 0.15\           最后一个用 python312 打出来的 exe 包
    └── Ver 0.17\           0.17R 的 exe 包（PyInstaller 6.22.3 / Python 3.14）
```

**开发目录不再叫 `Ver X.Y`**：`Desktop\MultiExtractorDeveloping` 只有一个，
里面永远是「当前正在做的那一版」；等用户说改版本号，才同步/归档一次。
所以 `selftest/test_version_rule.py` 对目录名的检查加了这一条例外
（`DEV_DIR`），归档目录仍然必须是 `Ver X.Y` 且版本线对得上。

版本号规则（2026-10-17 定，以后都用这套）：状态后缀紧贴版本线 ——

| 后缀 | 含义 | 例子 |
|---|---|---|
| `R` | Release，发布 | `0.17R Beta 00093` |
| `Rc` | Release candidate，候选发布 | `0.18Rc Beta 00102` |
| `E` | Experimental，实验性 | `0.18E Beta 00105` |
| `WIP` | Work in process，开发中 | `0.18WIP Beta 00094` |
| `S` | Scrapped，已废弃 | `0.16S Beta 00070` |

同一版本线只换后缀：开发中是 `0.17WIP`，做好了就是 `0.17R`。开下一条线才改数字
（`0.18WIP`），五位编号始终单调累加、不归零。

```bat
python bump.py --show                 :: 当前版本
python bump.py                        :: 编号 +1（每轮一次）
python bump.py --tag R                :: 这版做好了 → 发布
python bump.py --tag WIP              :: 退回开发中
python bump.py --line 0.18 --tag WIP  :: 开下一条线（等价旧写法 --release 0.18 加 R）
```

`selftest/test_version_rule.py` 把形态、目录一致性、上面四种 bump 动作都钉成断言。
`selftest/test_version_rule.py` 会把目录名与版本号的一致性钉死。

---

## 常见问题

### 未识别到引擎

`--detect` 输出识别结果与证据。无特征时采用通用收集。`-e <引擎键>` 手动指定引擎（`python main.py --engines` 列出全部可用引擎）。

### 缺少外部工具（Slim 包正常现象）

V0.18E 起发布包不带 `tools/`。`--tools` 会打印清单来源：来自**内置兜底**说明
既没网也没缓存；来自**远端 / 本地缓存**说明清单已拿到，缺的只是二进制。

补工具的四条路（交互式菜单首次运行会以「软键」形式问你）：

1. 按 `1` 让程序自己下载（直链优先，装完自动验证；AssetStudio 才走全量包）；
2. 按 `2` 扫描本机常见目录，自动认出工具根；
3. 按 `3` 手动指定工具根目录（写 `%LOCALAPPDATA%\MultiExtractor\toolsdir.ini`）；
4. 按 `4` 只用自研核心继续 —— GameMaker 的提取与 `--rebuild` 都不需要外部工具。

命令行等价写法：

```bat
MultiExtractorCLI.exe --tools-get                 :: 自动下全部缺失的
MultiExtractorCLI.exe --tools-get utmt repak      :: 只下这两个
MultiExtractorCLI.exe --tools-get-full            :: 允许下 140 MB 全量包（补 .7z 那个）
MultiExtractorCLI.exe --tools-dir "D:\某处\tools" :: 已经有工具，指过去就行
```

### 缺少外部工具（旧版 / 源码树）

发布包包含工具。缺失时运行：

```bat
python tools\setup_tools.py --mirror
```

### 输出目录过大

`--only` 限定分类：`--only image`。

### 中间产物占空间

运行结束后自动删除 `work/`；`--keep-work` 保留。

### setup.ini 修改后保存位置未变

`MultiExtractorCLI.exe --setup` 输出实际读取的文件、当前生效路径、配置校验。两种原因：`-o` 覆盖 ini；`setup.ini` 未与 exe 放在同一层目录。

### 导出的 .uasset 无法打开

Unreal pak 内为引擎专有格式，非图片音频；转换：FModel 或 UE Viewer。

### Unity 提取 0 个文件

AssetStudio 退出码恒为 0；成败依据：输出目录内是否有文件。0 文件时为加密渠道游戏：`ME_UNITY_GAME=<渠道值>`（候选值 40 多个，`tools\assetstudio\AssetStudio.CLI.exe --help` 列出全部），或 `ME_UNITY_IMAGE_TYPES=` 空串执行一次不限类型的全量导出。

### 仅有 .utoc / .ucas

UE5 IoStore；repak 不支持；转换：FModel 或 UE Viewer。

---

## 已知限制

- Clickteam 加密发布的文件（chunk flag = 2 或 3）不支持
- Unreal 的 `.uasset` 需二次转换方可获取图片音频；`.utoc` / `.ucas`（UE5 IoStore）repak 不支持，需用 FModel / UE Viewer
- Unity 的特殊加密渠道游戏需尝试不同的 `--game` 参数
- 部分老引擎的 Android 专用像素格式（graphicMode 0~5）不支持

---

## 开发与自测

```bat
:: 执行全部自测
python selftest\run_all.py

:: 仅执行真实样本回归
python selftest\run_all.py samples

:: 发布前清理（删 __pycache__ / work / Extract Resources / dist 等）
python make_release.py
python make_release.py --zip
```

自测两类：

- 真实样本回归（`test_samples.py`）：真实游戏文件端到端运行
- 合成样本往返测试：自行构造容器再解出比对。仅证明加密与解密自洽，不证明格式理解正确

`FORMATS.md` 中每条格式结论均标注证据等级：【实测】/【自洽】/【公开】。

RPG Maker 另附真实归档夹具（`selftest/fixtures/`，来自 MIT 许可的 RPGMakerDecrypter 仓库）；已发现 2 个合成测试无法发现的 bug（`selftest/fixtures/README.md`、`FORMATS.md` 第 6.2 节）。

### 新增引擎

1. `me/scanner/signatures.py` 特征表加一行
2. `me/engines/` 下新建模块，提供 `KEY` / `NAME` / `extract(ctx)`

`me/engines/__init__.py` 自动发现新模块，无需改注册代码。
