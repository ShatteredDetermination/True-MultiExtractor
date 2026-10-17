# 传参规范（Ver 0.13）

程序名：`MultiExtractor.exe`
事件流与退出码的完整约定见同目录的 `GUI-API.md`，本文件只讲参数。

## 1. 调用形式

```
MultiExtractor.exe [<target>] [选项...]
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
| `--tools` | 开关 | 关 | 检查外部 CLI 是否齐全 |
| `--engines` | 开关 | 关 | 列出引擎模块 |
| `--categories` | 开关 | 关 | 列出分类 |
| `--setup` | 开关 | 关 | 打印配置文件的真实位置与当前保存路径 |
| `--version` | 开关 | 关 | 只打印版本 |
| `-y, --yes` | 开关 | 关 | 不询问；`--json` 下自动生效 |
| `--json` | 开关 | 关 | 输出 NDJSON 事件流；**GUI 调用必须加** |
| `--cancel-file` | 文件路径 | 无 | 取消信号文件；**仅在 `--json` 下有效** |
| `-h, --help` | 开关 | — | 帮助 |

路径可以带引号，程序会自行去掉首尾空白、引号与 BOM。

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
--version → --categories → --tools → --engines → --setup → --detect → 提取
```

其余规则：

| 规则 | 说明 |
|---|---|
| `-o` 覆盖 `setup.ini` | 传了 `-o` 就完全忽略配置里的 `path` |
| `-e` 覆盖自动识别 | 传了 `-e` 就不看识别结果 |
| `--only` 只在提取时生效 | 与 `--detect` 同时给，`--detect` 优先，`--only` 被忽略 |
| `--json` 隐含 `-y` | 同时隐含不暂停 |
| `--cancel-file` 依赖 `--json` | 不加 `--json` 时该参数被忽略，不会报错 |

## 5. 退出码

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

## 6. 输出约定

| 流 | 编码 | 内容 |
|---|---|---|
| stdout | UTF-8 | 加 `--json` 后只走事件流，一行一个 JSON 对象 |
| stderr | UTF-8 | 人读文本、进度条、外部 CLI 原始输出 |

不加 `--json` 时人读文本仍走 stdout，与 0.12 一致。

## 7. 调用示例

```bat
:: 提取全部类型
MultiExtractor.exe "D:\Games\SomeGame" -o "D:\out" --json

:: 只要图片和音频
MultiExtractor.exe "D:\Games\SomeGame" -o "D:\out" --only image,audio --json

:: 直接丢 data.win
MultiExtractor.exe "D:\Games\SomeGame\data.win" -o "D:\out" --json

:: 强制引擎
MultiExtractor.exe "D:\Games\SomeGame" -o "D:\out" -e gamemaker --json

:: 只识别
MultiExtractor.exe "D:\Games\SomeGame" --detect --json

:: 取消（另开一个进程写这个文件）
MultiExtractor.exe "D:\Games\SomeGame" -o "D:\out" --json --cancel-file "D:\out\cancel.flag"

:: 建界面用的自描述命令
MultiExtractor.exe --categories --json
MultiExtractor.exe --engines --json
MultiExtractor.exe --tools --json
```

## 8. 边界行为（已实测）

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
