# True MultiExtractTool

多引擎游戏资源提取工具。丢一个游戏目录或者单个 exe 进去，自动识别引擎，
按 **图片 / 音频 / 文本 / 字体** 分类把资源导出来。

只用 Python 标准库（numpy 只做可选加速），外部 CLI 已经打包在 `tools/` 下，
**用户端不需要再下载任何东西**。

---

## 快速开始

### 双击运行（推荐）

```
MultiExtractor.exe
```

**不需要装 Python**，双击就行，然后按提示输入游戏路径
（可以直接把文件夹或 exe 拖进窗口）。

也可以把游戏目录或 exe **直接拖到 `MultiExtractor.exe` 上**，省一步输入。

> 打包版和源码版功能完全一样。`MultiExtractor.exe`、`resources/`、`tools/`
> 三样必须待在同一层目录 —— **不要只把 exe 单独拷走**，它跑不起来。
> 提取结果存哪由旁边的 `setup.ini` 决定，见下面一节。

### 从源码运行

```
run.bat
```

需要 Python 3.8 以上。

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

:: 检查外部工具、运行环境（反馈问题时先跑这个）
MultiExtractor.exe --tools

:: 查看/定位 setup.ini（改保存位置）
MultiExtractor.exe --setup

:: 保留中间产物（脱壳/解包结果），排查问题时有用
MultiExtractor.exe "D:\Games\SomeGame" --keep-work
```

源码版把上面的 `MultiExtractor.exe` 换成 `python main.py` 即可。

---

## 保存到哪：setup.ini

程序旁边有一个 `setup.ini`，**用记事本改**就能换提取结果的保存位置：

```ini
[Output]
; 相对路径 —— 从程序所在目录算起
path = Extract Resources

; 绝对路径 —— 直接写完整路径
; path = D:\拆包输出
```

| 写法 | 结果 |
|---|---|
| `Extract Resources` | 存到程序旁边的 `Extract Resources\` |
| `D:\拆包输出` | 存到该绝对路径 |
| 用 `-o` 参数 | 覆盖 ini 里的设置 |

几个细节：

- 文件不存在时**首次运行会自动生成**一份带注释的默认配置
- 每个游戏会在下面单独建一层以游戏名命名的子目录，多次提取不会互相覆盖
- 相对路径一律相对**程序所在目录**算，整包拷到别的盘也不会失效
- 用记事本保存成 UTF-8（带不带 BOM 都行）、ANSI 都能正常读
- ini 被改坏了不会崩，会退回默认值并**明确提示**你哪里不对

### 自己打包 exe

```bat
python build_exe.py                          :: 打包到 dist/MultiExtractor/
python build_exe.py --dest "D:\交付\Ver 0.11" :: 组装到指定目录
python build_exe.py --zip                    :: 顺便生成 zip
```

产物结构就是发布包的样子：

```
MultiExtractor.exe      双击这个
resources/              Python 运行时（别删）
tools/                  四个外部 CLI
setup.ini               保存位置配置
README.md  FORMATS.md  THIRD_PARTY.md
```

打包后可以跑 `python selftest/test_frozen.py` 验证（引擎是否齐全、
工具是否定位得到、提取结果是否与源码版逐字节一致）。

> 运行时目录叫 `resources` 而不是 PyInstaller 默认的 `_internal` ——
> 后者对用户没有意义，还容易被误以为是可以删的内部文件。

---

## 支持的引擎

| 引擎 | 实现方式 | 状态 |
|---|---|---|
| **Electron / NW.js** | 自写 asar 解析（纯 Python） | 真实样本验证通过 |
| **GameMaker** | UndertaleModTool CLI，读不了时自动修补重试、再退到自写抓取 | 真实样本验证通过 |
| **Clickteam Fusion 2.5** | **自写解析器**（纯 Python，不用 CTFAK） | 真实样本验证通过 |
| **Enigma Virtual Box**（保护壳） | evbunpack 脱壳 + 递归识别内层 | 真实样本验证通过 |
| Godot | 自写 .pck 解析（纯 Python） | 合成样本验证 |
| RPG Maker (XP/VX/VX Ace) | 自写 RGSSAD 解密（纯 Python） | **真实归档验证通过** |
| Unity | AssetStudio CLI | 参数与调用已验证；**无真实游戏样本** |
| Unreal Engine | repak | 真 repak 往返验证通过；**无真实游戏样本** |
| 其他 / 未识别 | 通用扩展名收集 | 始终可用（兜底） |

识别不出来也不会失败：会退回「通用收集」，把目录里所有能认出来的资源按分类导出。

**直接把 `data.win` / `game.droid` 粘进来就行**，不用非得给整个游戏目录 ——
靠 `FORM....GEN8` 容器头认，和文件名无关。

### GameMaker 文件 UTMT 读不了怎么办

UTMT 只认它支持的那些 GameMaker 版本，碰到不认识的 chunk 结构就直接抛异常、
一个字节都不给你。本工具会自动往下退：

1. 直接跑 UTMT（绝大多数游戏走这条）
2. 失败 → 试着把出问题的 chunk **原长占位**清空再跑
   （长度绝对不能变，文件里存着绝对偏移，一变就全错位）
3. 还是不行 → 放弃解析容器，按结构校验直接抓内嵌的 PNG / WAV / OGG

第 3 层拿到的是**整张纹理页**（图集），不是切好的单个精灵 —— 精灵坐标在
TPAG 里，而正是解析不了容器才走到这一步。输出说明里会写清楚这点。

`--detect` 和提取结果里都会说明用了哪一层。

### ⚠ 实验性：data.win 指针重建

设 `ME_EXPERIMENTAL_REBUILD=1` 会多开一层（默认**关闭**，因为它又慢又不保证
能修好）。它针对的是「列表型 chunk 的指针数组被写坏」：`data.win` 里每个列表
chunk 都是 `count + 绝对偏移指针 + 条目`，UTMT 要求条目**严格连续**，否则报

```
Reading misaligned at <条目应该开始的位置>, realigning back to <指针里写的值>
```

而这行报错恰好把**正确位置**告诉了我们。所以这一层拿 UTMT 当**预言机**，
一轮修一个指针，迭代到指针表连续为止。全程**长度严格不变**（文件里存的是绝对
偏移，长度一变后面全部错位）。

实测：某个 61MB 的文件 FONT chunk 20 个指针里 6 个指向 16MB 处（那些条目被
物理挪进了字符串表区间），**6 轮全部重建成功**，而且反推出来的值和原本存活的
合法指针完全自洽 —— 不是"UTMT 恰好接受"，是真算对了。

**但它不保证能把文件修到可读。** 上面那个文件修完指针后仍然死在
`Padding error in STRG`：STRG 的声明长度比实际内容长 278,465 字节，要修就得
缩短它、把后面所有数据前移、再全文件重算绝对偏移 —— 那是个全局重写器，本工具
**没有实现**。修不完时这一层老实返回失败，流程继续退到抓取兜底，**不会产出
半坏的中间文件**。

也可以单独当工具用：

```bat
:: 只诊断，不动文件
python me\formats\gamemaker_rebuild.py "D:\Games\X\data.win" --diagnose-only

:: 诊断 + 尝试重建
python me\formats\gamemaker_rebuild.py "D:\Games\X\data.win" -o rebuilt.win
```

已知限制：新版 GameMaker 里有些 chunk（`VARI` / `FUNC` / `LANG` …）**不是**
「count + 绝对指针」这个布局，硬按这个读会得到一堆荒谬数值。所以诊断有个保守
判据：指针不到一半落在 chunk 内就认为是我们读错了布局、直接不报，免得跑去修
一个根本不是指针数组的 chunk。

---

## 环境变量

大部分情况不用管，遇到特殊情况时这些可以救急：

| 变量 | 作用 |
|---|---|
| `ME_AES_KEY` | Unreal 加密 pak 的 AES 密钥（hex 或 base64） |
| `ME_UNITY_GAME` | Unity 的 `--game` 渠道值，默认 `Normal`。加密/特殊渠道游戏要换 |
| `ME_UNITY_IMAGE_TYPES` | 覆盖 Unity 图片趟的 `--types`；**设成空串 = 不限定类型全量导出** |
| `ME_UNITY_AUDIO_TYPES` | 覆盖 Unity 音频趟的 `--types`；同上 |
| `ME_TEXTURE_PAGES` | 设为 `1` 时，GameMaker 额外导出整张纹理页（体积大，和精灵重复） |
| `ME_NO_PAUSE` | 设为 `1` 时，双击运行结束后**不**停下来等按键（脚本/自动化调用用） |
| `ME_EXPERIMENTAL_REBUILD` | 设为 `1` 开启**实验性** data.win 指针重建（默认关闭，见上） |

例：
```bat
set ME_UNITY_GAME=UnityCN
python main.py "D:\Games\SomeGacha"
```


---

## 输出结构

```
<setup.ini 里配置的目录>/<游戏名>/
├── image/          图片（png / jpg / gif ...）
├── audio/          音频（ogg / wav / mp3 ...）
├── text/           文本、字符串表、应用信息
├── font/           字体（ttf / otf ...）
├── video/          视频
├── other/          其他
├── manifest.json   完整清单（每个文件的来源、大小、分类）
└── REPORT.txt      人类可读的统计报表
```

默认那一层是 `Extract Resources`（程序旁边），改 `setup.ini` 可换地方。

`manifest.json` 里每条记录都带 `origin` 字段，能追溯文件是从哪个容器里出来的。

---

## 引擎实现说明

### Clickteam Fusion 2.5（自写解析器）

**为什么不用 CTFAK**：CTFAK 不稳定，遇到稍特殊的文件就崩。

本工具自己实现了 CTF 2.5 的解析，拆成 `me/formats/ctf25/` 下的一组小文件：

| 文件 | 职责 |
|---|---|
| `binary.py` | 字节读取器（小端整数 / ASCII / UTF-16LE） |
| `ids.py` | chunk 编号 → 名字对照表 |
| `lz4.py` | LZ4 block 解压（图像数据用） |
| `container.py` | 在文件里定位 CCN 数据流 |
| `chunks.py` | chunk 流遍历与解压 |
| `imagebank.py` | 图像条目解析 |
| `imagecodec.py` | 像素格式 → RGBA |
| `soundbank.py` | 音频条目解析 |
| `fontbank.py` | 字体元数据 + 内嵌 TTF |
| `extractor.py` | 对外入口 |

两种发布形态都支持：`.dat` 数据文件形态、单文件 exe 形态。

### Enigma Virtual Box

先 `evbunpack` 脱壳，然后**重新识别内层引擎**再递归提取。
比如「GameMaker + Enigma 壳」的 exe，会自动走完「脱壳 → 找到 data.win → 交给 GameMaker 模块」。

### GameMaker

调用 UndertaleModTool 的 CLI，但**不用它自带的导出脚本**——
那些脚本会弹目录选择框，无人值守跑不了。
本项目在 `tools/utmt/Scripts/MultiExtractor/` 下放了自己的非交互脚本，
输出目录通过环境变量 `ME_OUT` 传递。

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
├── THIRD_PARTY.md          第三方工具许可证（分发前必看）
├── me/                     主程序包
│   ├── config.py           全局常量、路径
│   ├── setup_ini.py        setup.ini 的读写（容忍 BOM / GBK / UTF-16）
│   ├── console.py          控制台代码页、双击启动时别让窗口闪掉
│   ├── ui.py               控制台输出（含进度条）
│   ├── runner.py           流程编排：识别 → 选引擎 → 提取 → 出报表
│   ├── scanner/            引擎识别
│   │   ├── signatures.py   特征表（加引擎只改这里）
│   │   ├── dirscan.py      目录索引
│   │   └── detect.py       综合判定
│   ├── collect/            资源收集
│   │   ├── kinds.py        扩展名 → 分类表
│   │   ├── gather.py       目录遍历
│   │   ├── writer.py       输出写出（路径清洗、去重、记账）
│   │   ├── manifest.py     清单 / 报表
│   │   ├── pngwrite.py     最小 PNG 编码器（避免依赖 Pillow）
│   │   └── sniff.py        按文件头猜格式
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
    ├── run_all.py          跑全部
    ├── test_samples.py     真实样本端到端回归
    ├── test_setup_ini.py   配置读取（含 BOM / GBK / UTF-16）
    ├── test_frozen.py      打包版 exe 自测（没打包过就跳过）
    └── test_*.py           各模块的合成样本测试
```

版本归档的目录约定（本机）::

    C:\MultiExtractor\
    ├── Source Code\Ver 0.11\   本文件所在位置
    └── Compiled\Ver 0.11\      build_exe.py 的产物


**加一个新引擎**只需要两步：

1. 在 `me/scanner/signatures.py` 的特征表里加一行
2. 在 `me/engines/` 下新建一个模块，提供 `KEY` / `NAME` / `extract(ctx)`

`me/engines/__init__.py` 会自动发现新模块，不用改注册代码。

---

## 常见问题

**识别不出来怎么办？**
先用 `--detect` 看识别结果和证据。如果确实没有特征，会走通用收集。
也可以用 `-e <引擎键>` 手动指定引擎（`python main.py --engines` 可以列出所有可用的）。

**提示缺少外部工具？**
发布包已经带好工具了。如果确实缺，运行：
```bat
python tools\setup_tools.py --mirror
```

**输出目录太大？**
用 `--only` 限定分类，例如只要图片：`--only image`。

**中间产物占空间？**
默认跑完会自动删除 `work/`。加 `--keep-work` 才会保留。

**改了 setup.ini，保存位置却没变？**
先跑 `MultiExtractor.exe --setup`，它会告诉你实际读了哪个文件、
当前生效的路径是什么、配置有没有问题。两个常见原因：
`-o` 参数优先级最高会覆盖 ini；或者 setup.ini 没和 exe 放在同一层目录。

**导出的 .uasset 打不开？**
Unreal 的 pak 里是引擎专有格式，不是直接的图片音频。
需要用 FModel 或 UE Viewer 再转换一次。

**Unity 提取出 0 个文件？**
AssetStudio 的退出码永远返回 0，所以「命令没报错」不代表导出成功——
本工具是靠「输出目录里有没有文件」判断的。
如果确实一个文件都没有，多半是加密渠道游戏，试试换 `ME_UNITY_GAME`
（候选值 40 多个，用 `tools\assetstudio\AssetStudio.CLI.exe --help` 可以看到全部），
或者把 `ME_UNITY_IMAGE_TYPES` 设成空串做一次不限类型的全量导出。

**Unreal 只找到 .utoc / .ucas？**
那是 UE5 的 IoStore 格式，repak 不支持。用 FModel 或 UE Viewer。

---

## 已知限制

- Clickteam 加密发布的文件（chunk flag = 2 或 3）暂不支持
- Unreal 的 `.uasset` 需要二次转换才能看到图片音频；`.utoc` / `.ucas`（UE5 IoStore）repak 不支持，要用 FModel / UE Viewer
- Unity 的特殊加密渠道游戏需要试不同的 `--game` 参数
- 部分老引擎的 Android 专用像素格式（graphicMode 0~5）不支持

---

## 开发与自测

```bat
:: 跑全部自测
python selftest\run_all.py

:: 只跑真实样本回归
python selftest\run_all.py samples

:: 发布前清理（删 __pycache__ / work / Extract Resources / dist 等）
python make_release.py
python make_release.py --zip
```

自测里有两类测试，看结果时要注意区别：

- **真实样本回归**（`test_samples.py`）—— 拿真实游戏文件端到端跑，最可信
- **合成样本往返测试** —— 自己造容器再解出来比对。
  这类测试只能证明「加密和解密自洽」，**不能**证明格式理解正确。
  `FORMATS.md` 里每条格式结论都标了证据等级（【实测】/【自洽】/【公开】），
  看文档时注意区分。

RPG Maker 那一项额外带了**真实归档夹具**（`selftest/fixtures/`，来自 MIT 许可的
RPGMakerDecrypter 仓库）。它存在的意义就是证伪上面那类「自洽但错误」的问题 ——
实际上它已经抓出了两个合成测试查不出来的真 bug，详见
`selftest/fixtures/README.md` 和 `FORMATS.md` 第 6.2 节。

### 加一个新引擎

1. 在 `me/scanner/signatures.py` 的特征表里加一行
2. 在 `me/engines/` 下新建模块，提供 `KEY` / `NAME` / `extract(ctx)`

`me/engines/__init__.py` 会自动发现新模块。
