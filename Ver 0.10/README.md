# True MultiExtractTool

多引擎游戏资源提取工具。丢一个游戏目录或者单个 exe 进去，自动识别引擎，
按 **图片 / 音频 / 文本 / 字体** 分类把资源导出来。

只用 Python 标准库（numpy 只做可选加速），外部 CLI 已经打包在 `tools/` 下，
**用户端不需要再下载任何东西**。

---

## 快速开始

### 双击运行

```
run.bat
```

然后按提示输入游戏路径（可以直接把文件夹或 exe 拖进窗口）。

### 命令行

```bat
:: 全部类型
python main.py "D:\Games\SomeGame"

:: 只要图片和音频
python main.py "D:\Games\SomeGame" --only image,audio

:: 指定输出目录
python main.py "D:\Games\SomeGame" -o D:\out

:: 只识别引擎，不提取
python main.py "D:\Games\SomeGame" --detect

:: 检查外部工具是否齐全
python main.py --tools

:: 保留中间产物（脱壳/解包结果），排查问题时有用
python main.py "D:\Games\SomeGame" --keep-work
```

需要 Python 3.8 以上。

---

## 支持的引擎

| 引擎 | 实现方式 | 状态 |
|---|---|---|
| **Electron / NW.js** | 自写 asar 解析（纯 Python） | 真实样本验证通过 |
| **GameMaker** | UndertaleModTool CLI | 真实样本验证通过 |
| **Clickteam Fusion 2.5** | **自写解析器**（纯 Python，不用 CTFAK） | 真实样本验证通过 |
| **Enigma Virtual Box**（保护壳） | evbunpack 脱壳 + 递归识别内层 | 真实样本验证通过 |
| Godot | 自写 .pck 解析（纯 Python） | 合成样本验证 |
| RPG Maker (XP/VX/VX Ace) | 自写 RGSSAD 解密（纯 Python） | **真实归档验证通过** |
| Unity | AssetStudio CLI | 参数与调用已验证；**无真实游戏样本** |
| Unreal Engine | repak | 真 repak 往返验证通过；**无真实游戏样本** |
| 其他 / 未识别 | 通用扩展名收集 | 始终可用（兜底） |

识别不出来也不会失败：会退回「通用收集」，把目录里所有能认出来的资源按分类导出。

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

例：
```bat
set ME_UNITY_GAME=UnityCN
python main.py "D:\Games\SomeGacha"
```


---

## 输出结构

```
output/<游戏名>/
├── image/          图片（png / jpg / gif ...）
├── audio/          音频（ogg / wav / mp3 ...）
├── text/           文本、字符串表、应用信息
├── font/           字体（ttf / otf ...）
├── video/          视频
├── other/          其他
├── manifest.json   完整清单（每个文件的来源、大小、分类）
└── REPORT.txt      人类可读的统计报表
```

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
├── run.bat                 双击启动
├── make_release.py         发布前清理 / 打包
├── README.md               本文件
├── FORMATS.md              各容器格式笔记（含证据等级）
├── THIRD_PARTY.md          第三方工具许可证（分发前必看）
├── me/                     主程序包
│   ├── config.py           全局常量、路径
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
│   ├── tools/              外部 CLI 的定位与调用
│   └── work/               中间产物（默认跑完自动清理）
├── tools/                  随包发布的外部 CLI
│   ├── setup_tools.py      维护者用：下载 / 更新工具
│   ├── utmt/               UndertaleModTool CLI（含自己的非交互导出脚本）
│   ├── assetstudio/        AssetStudio CLI
│   ├── evbunpack/          Enigma VB 脱壳
│   └── repak/              Unreal .pak
└── selftest/               自测脚本
    ├── run_all.py          跑全部
    ├── test_samples.py     真实样本端到端回归
    └── test_*.py           各模块的合成样本测试
```

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

:: 发布前清理（删 __pycache__ / work / output），可选打 zip
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
