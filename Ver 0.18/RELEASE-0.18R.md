V0.18R（标签 `0.18R Beta 00098`）。自 0.17R 起的主要变化：

## 包体：Slim 微内核

* 发布包**不再带 `tools/` 那 178 MB 外部 CLI**：CLI 目录 27 个文件 / 20.2 MB，含壳的完整包 28 个文件 / 67.3 MB。
* 外部工具变成数据：`tools.ini` 清单三级来源（远端 → `%LOCALAPPDATA%\MultiExtractor\tools.ini` → 内置兜底），任何一级失败都不影响启动，`--tools` 会写明来源。
* 缺工具时程序自己下：`--tools-get` / 设置面板 `[9]` / 软键「下载工具」，直链 → 镜像（ghfast.top / ghproxy.net / gh-proxy.com）→ 全量包兜底，失败会记住上次成功的镜像。**默认从不使用本地缓存安装包**，只有设置面板 `[10]`「加载本地缓存安装包」（`--tools-get-cache`）才读缓存。

## 壳

* 与 CLI 同级分发（`--layout flat` 默认），操作区下面五颗软键：下载工具 / 扫描本机 / 指定目录 / 仅自研 / 缓存安装。
* 操作进度按百分比显示（`操作进度: N%`），进度条与下载进度用同一个 `pct` 驱动。
* 右侧日志每行带时间戳 `[YYYY-MM-DD HH:MM:SS]`。

## 在线更新

* `--update` 查仓库根目录的 `version.json`，只比主版本号（后缀不参与），同一条线换后缀用 `--update verid=0.18R` 点名。
* 下载完必验 SHA256，不一致就删包；替换交给 `daemon.bat`（等 2 秒 → `tar` 覆盖 → 删包 → 启动新版 → 自删除）。
* 新增 `make_dist.py --pack`：打**根层平铺**的 `UpdatePack.zip`。以前发的是带一层目录的手动包，`tar -xf` 解完只多一个子目录、一个文件都换不掉；这版把更新包和手动包分开。

## 下载哪个

| 资产 | 用途 |
|---|---|
| `MultiExtractor-0.18R-win64.zip` | 手动安装包。解压得到 `MultiExtractor-0.18R-win64\`，双击 `MultiExtractorGUI.exe`。 |
| `UpdatePack.zip` | 在线更新包，条目在根层。给 `--update` 用。 |

`MultiExtractor-0.18R-win64.zip` SHA256：`cc0d40a0f08ea9a1711bee6be44fda2288359b25acea5c350035e430193eb416`（53,805,473 B）

`UpdatePack.zip` SHA256：`1ba49d2f9711753b1e648ce89fc40cb31f613e51be42841dc1c386aca34ebd3d`（53,803,961 B）

## 从 0.18E 升上来

* 0.18E 用户升级到 0.18R：`MultiExtractorCLI.exe --update verid=0.18R`（同一条版本线只换后缀，不会自动催更新）。
* 包内没有外部工具：首次运行用软键「下载工具」或 `MultiExtractorCLI.exe --tools-get`。

## 已知缺口

* Unity 压缩 bundle 的载荷编码：头 / 块表 / 目录已对，载荷 LZ4 / LZMA / 明文 / 整体压缩四种假设都被真包证伪。
* UE5 pak v10 / v11 的编码条目表（位压缩）未解。
* 壳尚未消费 `update_pending` / `tool_get` 事件，更新流程仍以 CLI 为主。
