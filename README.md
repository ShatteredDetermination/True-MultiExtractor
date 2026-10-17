# True MultiExtractor

多引擎游戏资源提取工具。命令行（Python）+ WinForms 壳（.NET 10 单文件）。

本仓库只放**源码**；编译版在 [Releases](../../releases) 里。第三方 CLI（UTMT /
AssetStudio / evbunpack / repak，约 178 MB）不进 git，用 `tools/setup_tools.py` 自行下载。

## 版本线

目录 `Ver 0.10` … `Ver 0.17` 是逐版本留档，每个目录是一份完整可运行的源码树。
git 历史里每个版本一个 commit，对应 tag `v0.10` … `v0.16`、`v0.17R`。

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
可用 `python tools/setup_tools.py` 拉取。

## 打包

```bat
python build_exe.py                 :: CLI → dist/MultiExtractor/MultiExtractorCLI.exe
python make_release.py --zip        :: 源码 zip（先跑，它会清理 dist/）
python make_dist.py                 :: GUI + CLI 同级发布包（需先 dotnet publish 壳）
```

壳在独立仓库的 `MultiExtractorGUI` 里，`.NET 10` 自包含单文件，`dotnet publish -c Release`。

## 自测

```bat
python selftest/run_all.py          :: 全部（29 个脚本）
python selftest/run_all.py samples  :: 只跑真实样本回归
```

## 免责声明

仅供学习与研究。请只对自己拥有或获授权的文件使用；提取结果的使用后果自负。
