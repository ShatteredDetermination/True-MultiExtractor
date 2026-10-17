# True MultiExtractTool

多引擎游戏资源提取工具。输入游戏目录或单个 exe；识别引擎后按 image / audio / text / font 分类导出。

仅用 Python 标准库；numpy 为可选加速。外部 CLI 随包提供于 `tools/`，用户端无需额外下载。

---

## 快速开始

### 双击运行

```
MultiExtractor.exe
```

无需 Python 环境。双击运行，按提示输入游戏路径；游戏目录或 exe 可直接拖入窗口。

游戏目录或 exe 拖到 `MultiExtractor.exe` 上 = 省去输入步骤。

打包版与源码版功能一致。`MultiExtractor.exe`、`resources/`、`tools/` 三者必须位于同一层目录；单独拷贝 exe 无法运行。保存位置由同层 `setup.ini` 决定，见「保存位置：setup.ini」。

### 从源码运行

```
run.bat
```

要求 Python 3.8 以上。

### 命令行

```bat
:: 全部类型
MultiExtractor.exe "D:\Games\SomeGame"

:: 只要图片和音频
MultiExtractor.exe "D:\Games\SomeGame" --only image,audio

:: 指定输出目录
MultiExtractor.exe "D:\Games\SomeGame" -o D:\out

:: 只识别引擎，不提取
MultiExtractor.exe "D:\Games\SomeGame" --detect

:: 检查外部工具与运行环境（反馈问题时先运行此命令）
MultiExtractor.exe --tools

:: 查看 / 定位 setup.ini（改保存位置）
MultiExtractor.exe --setup

:: 保留中间产物（脱壳 / 解包结果），排查问题时使用
MultiExtractor.exe "D:\Games\SomeGame" --keep-work
```

源码版：上文的 `MultiExtractor.exe` 替换为 `python main.py`。

### GUI 调用（事件流）

供 WinForms 壳调用，不作为人读选项。

- 加 `--json` 后：stdout 为 NDJSON 事件流，一行一个 JSON 对象，可逐行解析
- 同一 `--json` 下：人读文本（横幅、`[ok]/[warn]/[err]`、进度条、外部 CLI 原始输出）转往 stderr
- 取消：`--cancel-file PATH`，该文件出现即取消，进程退出码 5；仅 `--json` 下有效
- 双击 `MultiExtractor.exe` 仍进入原交互 CLI；`--json` 只用于壳调用
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
python build_exe.py                          :: 打包到 dist/MultiExtractor/
python build_exe.py --dest "D:\交付\Ver 0.13" :: 组装到指定目录
python build_exe.py --zip                    :: 生成 zip
```

产物结构即发布包结构：

```
MultiExtractor.exe      双击运行
resources/              Python 运行时，不可删除
tools/                  四个外部 CLI
setup.ini               保存位置配置
README.md  FORMATS.md  THIRD_PARTY.md  GUI-API.md  CLI-ARGS.md
```

打包后校验：`python selftest/test_frozen.py`（引擎是否齐全、工具是否可定位、提取结果是否与源码版逐字节一致）。

运行时目录名：`resources`（非 PyInstaller 默认的 `_internal`）。

---

## 支持的引擎

| 引擎 | 实现方式 | 状态 |
|---|---|---|
| Electron / NW.js | 自写 asar 解析（纯 Python） | 真实样本验证通过 |
| GameMaker | UndertaleModTool CLI；读取失败时修补 chunk 重试 | 真实样本验证通过 |
| Clickteam Fusion 2.5 | 自写解析器（纯 Python，不使用 CTFAK） | 真实样本验证通过 |
| Enigma Virtual Box（保护壳） | evbunpack 脱壳 + 递归识别内层 | 真实样本验证通过 |
| Godot | 自写 .pck 解析（纯 Python） | 合成样本验证 |
| RPG Maker (XP/VX/VX Ace) | 自写 RGSSAD 解密（纯 Python） | 真实归档验证通过 |
| Unity | AssetStudio CLI | 参数与调用已验证；无真实游戏样本 |
| Unreal Engine | repak | 真 repak 往返验证通过；无真实游戏样本 |
| 其他 / 未识别 | 通用扩展名收集 | 始终可用（兜底） |

引擎未识别：退回通用收集，目录内全部可识别资源按分类导出。

输入可为 `data.win` / `game.droid`，无需整个游戏目录。识别依据：`FORM....GEN8` 容器头（与文件名无关）。

### GameMaker 文件 UTMT 无法读取的处理

UTMT 仅支持已知的 GameMaker 版本；遇到未知 chunk 结构抛异常，不输出任何内容。退回顺序：

1. 直接运行 UTMT
2. 失败后清空出问题的 chunk（原长占位）重试。长度不可变，文件内存有绝对偏移

不提供绕开 UTMT 的按字节切资源路径。内嵌 PNG / WAV 整块 = 整张纹理页（图集），非切好的单个精灵；精灵坐标在 TPAG 中。资源一律经 UTMT 解析，解析失败即报失败。

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

调用 UndertaleModTool CLI；不使用 UTMT 自带的导出脚本。非交互脚本：`tools/utmt/Scripts/MultiExtractor/`；输出目录经环境变量 `ME_OUT` 传递。

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
│   └── tools/              外部 CLI 的定位与调用
├── tools/                  随包发布的外部 CLI
│   ├── setup_tools.py      维护者用：下载 / 更新工具
│   ├── utmt/               UndertaleModTool CLI（含自己的非交互导出脚本）
│   ├── assetstudio/        AssetStudio CLI
│   ├── evbunpack/          Enigma VB 脱壳
│   └── repak/              Unreal .pak
└── selftest/               自测脚本
    ├── run_all.py          执行全部
    ├── test_samples.py     真实样本端到端回归
    ├── test_setup_ini.py   配置读取（含 BOM / GBK / UTF-16）
    ├── test_frozen.py      打包版 exe 自测（未打包时跳过）
    └── test_*.py           各模块的合成样本测试
```

版本归档的目录约定（本机）：

```
C:\MultiExtractor\
├── Source Code\Ver 0.13\   本文件所在位置
└── Compiled\Ver 0.13\      build_exe.py 的产物
```

---

## 常见问题

### 未识别到引擎

`--detect` 输出识别结果与证据。无特征时采用通用收集。`-e <引擎键>` 手动指定引擎（`python main.py --engines` 列出全部可用引擎）。

### 缺少外部工具

发布包包含工具。缺失时运行：

```bat
python tools\setup_tools.py --mirror
```

### 输出目录过大

`--only` 限定分类：`--only image`。

### 中间产物占空间

运行结束后自动删除 `work/`；`--keep-work` 保留。

### setup.ini 修改后保存位置未变

`MultiExtractor.exe --setup` 输出实际读取的文件、当前生效路径、配置校验。两种原因：`-o` 覆盖 ini；`setup.ini` 未与 exe 放在同一层目录。

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
