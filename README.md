# True MultiExtractor

多引擎游戏资源提取工具。命令行（Python）+ WinForms 壳（.NET 10 单文件）。

本仓库只放**源码**；编译版在 [Releases](../../releases) 里。第三方 CLI（UTMT /
AssetStudio / evbunpack / repak，约 178 MB）不进 git，用 `tools/setup_tools.py` 自行下载。

## 下载编译版

| 版本 | 编译版 | 说明 |
|---|---|---|
| **0.17R** | `MultiExtractor-0.17R-win64.zip`（137 MB） | 最新发布：GUI + CLI 同级、.NET 10 单文件壳 |
| 0.15 | `MultiExtractor_0.15.zip`（91 MB） | 单文件 CLI |
| 0.14 | `MultiExtractor_0.14.zip`（29 MB） | 单文件 CLI |
| 0.13 | `MultiExtractor_0.13.zip`（91 MB） | 单文件 CLI |
| 0.12 | `MultiExtractor_0.12.zip`（91 MB） | 单文件 CLI |

0.10 / 0.11 / 0.12b / 0.16 只有源码存档（当年未打 exe 包），用对应 tag 检出后自行打包。

## 版本线

目录 `Ver 0.10` … `Ver 0.18` 是逐版本留档，每个目录是一份完整可运行的源码树。
git 历史里每个版本一个 commit，对应 tag `v0.10` … `v0.16`、`v0.17R`。

`Ver 0.18` 是当前开发线（`0.18E`）：Slim 微内核（CLI 20 MB、不带 tools/）、
外部工具自动下载（`--tools-get`）、在线自更新（`--update` + `daemon.bat`）。

## 在线更新清单（version.json）

仓库根目录的 `version.json` 是**在线更新用的数据源**（不是某个版本的存档），
程序用 `https://raw.githubusercontent.com/ShatteredDetermination/True-MultiExtractor/main/version.json`
读取它：

```json
{
  "latest_stable": "0.17R",
  "latest_beta": "0.18E",
  "download_url": "https://.../UpdatePack.zip",
  "sha256": "...",
  "changelog": "..."
}
```

* 发版时改这里（`latest_stable` / `latest_beta` / `download_url` / `sha256`），
  用户端 `MultiExtractorCLI.exe --update` 就会看到；
* 比较版本时**只比主版本号**（`E / Rc / S / WIP` 后缀剥离），同一条线换后缀要
  `--update verid=0.18R` 明确点名；
* `sha256` 必须是被 `download_url` 指向的那个文件的哈希，对不上会被删包报错。

版本号形态：`X.Y<状态后缀> Beta NNNNN`

| 后缀 | 含义 |
|---|---|
| `R` | Release，发布 |
| `Rc` | Release candidate，候选发布 |
| `E` | Experimental，实验性 |
| `WIP` | Work in process，开发中 |
| `S` | Scrapped，已废弃 |

开发中是 `0.17WIP`，做好了就是 `0.17R` —— 同一版本线只换后缀，五位编号单调累加
（`python bump.py`）。

## 从源码运行

```bat
cd "Ver 0.17"
python main.py                     :: 交互菜单
python main.py <游戏目录或文件>     :: 直接提取
python main.py --channels          :: 自研 / 第三方两条通道的状态
python main.py --tools             :: 检查外部 CLI 是否齐全
```

需要 Python 3.9+（开发机用 3.14）。外部工具缺失时对应引擎会退回自研通道或提示安装，
也可以让程序自己下：`python main.py --tools-get`（要连 .7z 那个再加 `--tools-get-full`）。

## 打包

```bat
python build_exe.py                 :: CLI → dist/MultiExtractor/MultiExtractorCLI.exe
python make_release.py --zip        :: 源码 zip（先跑，它会清理 dist/）
python make_dist.py                 :: GUI + CLI 同级发布包（需先 dotnet publish 壳）
```

壳在独立仓库的 `MultiExtractorGUI` 里，`.NET 10` 自包含单文件，`dotnet publish -c Release`。

## 自测

```bat
python selftest/run_all.py          :: 全部（34 个脚本）
python selftest/run_all.py samples  :: 只跑真实样本回归
```

## 免责声明

仅供学习与研究。请只对自己拥有或获授权的文件使用；提取结果的使用后果自负。
