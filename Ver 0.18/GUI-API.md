# GUI 接口契约（Ver 0.18）

给 WinForms 壳用。壳只做两件事：拼命令行 和 逐行读事件流。

---

## 1. 两种运行方式

| 方式 | 命令 | 行为 |
|---|---|---|
| 双击 | `MultiExtractorCLI.exe` | 原来的交互式 CLI 菜单，等人按键 |
| 壳调用 | `MultiExtractorCLI.exe <target> -o <dir> --json` | 非交互，事件流走 stdout |

`--json` 隐含：不询问、不暂停、stdout 只走事件流。壳必须加 `--json`，
否则得到的是人读文本，无法稳定解析。

## 2. 流的分工

| 流 | 编码 | 内容 |
|---|---|---|
| stdout | UTF-8 | 只走事件流，一行一个 JSON 对象，每行都 flush |
| stderr | UTF-8 | 人读文本：横幅、`[ok]/[warn]/[err]`、进度条、外部 CLI 的原始输出 |

约定：stdout 永远可以逐行 `json.loads`。壳想要一个"原始日志"面板，
就把 stderr 原样贴进去。

每行以 `\n` 结束，无 BOM，无 ANSI 颜色。

## 3. 命令行参数

| 参数 | 说明 |
|---|---|
| `<target>` | 游戏目录、单个 exe、或 `data.win` / `game.droid` 之类数据文件 |
| `-o, --out DIR` | 输出根目录。壳应当总是显式传，否则会落到 exe 旁边的 `Extract Resources` |
| `-e, --engine KEY` | 强制引擎，跳过自动识别（键见 `--engines`） |
| `--only LIST` | 只要这些分类，逗号分隔：`image,audio,text,font,video,other` |
| `--no-deep` | 不解容器，只收集散落文件 |
| `--keep-work` | 保留 `work/` 中间产物 |
| `--detect` | 只识别引擎，不提取 |
| `--experimental` | 启用实验性功能（自研通道的前置开关，V0.17） |
| `--self` / `--no-self` | 使用自研拆包核心；必须同时给 `--experimental` |
| `--channels` | 列出每个引擎的两条通道状态 |
| `--tools` | 检查外部 CLI 是否齐全；**缺工具时退出码 4**，事件流里发 `tools_manifest` + 每个工具一条 `tool` |
| `--tools-dir DIR` | 指定外部工具根目录，写进 `toolsdir.ini`（下次启动生效） |
| `--tools-refresh` | 强制重新拉远端 `tools.ini` |
| `--tools-get [KEY...]` | 自动下载缺失的外部工具；事件流里发 `tool_get` + `end` |
| `--tools-get-full` | 允许为补工具下载全量包（约 140 MB） |
| `--update [VERID]` | 在线更新（查清单 → 下载 → 校验 → 交接守护进程），见第 10 节 |
| `--engines` | 列出引擎模块 |
| `--categories` | 列出分类 |
| `--setup` | 查看配置文件位置与保存路径 |
| `--version` | 只打印版本 |
| `-y, --yes` | 不询问（`--json` 下自动生效） |
| `--json` | 打开事件流 |
| `--cancel-file PATH` | 该文件一出现就取消，见第 6 节 |

## 4. 退出码

定死，不要再改：

| 码 | 含义 |
|---|---|
| `0` | 成功 |
| `1` | 目标不存在或不可读 |
| `2` | 参数错误 |
| `3` | 执行结束，未提取到任何文件 |
| `4` | 缺少外部 CLI |
| `5` | 被 `--cancel-file` 取消 |
| `70` | 未预期的内部异常 |
| `130` | Ctrl+C |

判成败请看退出码，别去 grep stderr 的文字。

## 5. 事件

公共字段，每条事件都有：

| 字段 | 类型 | 说明 |
|---|---|---|
| `t` | string | 事件类型 |
| `seq` | int | 从 1 开始的序号，递增；可用于丢包检测 |
| `ms` | int | 相对进程启动的毫秒数 |

事件类型：

### `start`
一定是最先出现的一条。

```json
{"t":"start","seq":1,"ms":157,"v":1,"app":"True MultiExtractTool",
 "version":"0.13 Beta","pid":21212,"target":"D:\\Games\\X",
 "engine":null,"only":null,"out":"D:\\out","cancel_file":null}
```

`v` 是 schema 版本，当前 `1`。

### `note`
人读文本的镜像。壳可以直接把它当日志行显示。

```json
{"t":"note","seq":3,"ms":161,"level":"step","text":"识别中: D:\\Games\\X"}
```

`level` ∈ `step` | `info` | `ok` | `warn` | `err` | `detail`。

### `detect`
识别结果。`--detect` 和正式提取都会发。

```json
{"t":"detect","seq":4,"ms":162,"engine":"gamemaker","name":"GameMaker",
 "shell":"","inner":"","entry":"data.win","evidence":["FORM/GEN8 容器头: GameMaker 数据文件"],
 "truncated":false,"known":true}
```

`known=false` 表示没认出来，会退到通用收集（不是错误）。

### `tools_manifest` / `tool`（V0.18E）

只有 `--tools` 会发。`tools_manifest` 说明这份清单从哪来；`tool` 一个工具一条。

```json
{"t":"tools_manifest","seq":6,"ms":41,"source":"builtin","version":"1",
 "updated":"2026-10-18","count":4,
 "full_package":"https://github.com/.../releases","url":"https://raw.githubusercontent.com/..."}
{"t":"tool","seq":7,"ms":42,"key":"utmt","ok":false,"path":null,
 "expect":"utmt/UndertaleModCli.exe","desc":"GameMaker data.win（第三方通道）"}
```

| 字段 | 说明 |
|---|---|
| `source` | `remote` / `cache` / `builtin` —— 壳可以据此提示「清单来自内置兜底（没网）」 |
| `count` | 清单里一共几个工具 |
| `full_package` | 全量包地址（含 `tools/`），缺工具时给用户看 |
| `ok` | 本机找不找得到这个工具 |
| `expect` | 期望的相对路径（相对工具根） |

Slim 包（V0.18E 起）不带 `tools/`，所以 `ok` 全是 `false`、退出码 `4` 是**正常**的；
壳不应当把它当成错误弹窗，而要展示「清单 + 全量包地址 + 软键提示」。

### 更新相关事件（V0.18E）

| 事件 | 何时发 | 关键字段 |
|---|---|---|
| `update_check` | `--update` 查完清单 | `ok`（false 表示超时/断网）、`local`、`remote`、`available`、`kind`（stable/beta）、`beta_available`、`changelog`、`url`、`sha256` |
| `update_ready` | 下载并校验通过 | `pack`、`size`、`version`、`sha256` |
| `update_launch` | 已写 `daemon.bat` 且拉起 | `daemon`、`pack`、`reason`（`pending-pack` = 上轮遗留的包） |
| `update_failed` | 下载/校验失败 | `stage`、`error` |
| `update_cancelled` | 用户拒绝更新 | `reason` |
| `update_pending` | **每次启动**发现的遗留更新包 | `pack`、`size`、`version`、`changelog` |

`update_pending` 是给壳用的：CLI 在 `--json` 下**不弹窗**，壳收到后自己决定要不要问
「检测到已下载的更新包，是否立即安装？」（阻塞式），用户选 Y 就再调一次
`--update -y`（或直接 `MultiExtractorCLI.exe --update -y` 让它自己交接）。

### `tool_get`（V0.18E，`--tools-get` 专用）

一个工具一条，报告「程序自己下」的结果。

```json
{"t":"tool_get","seq":9,"ms":4200,"key":"repak","ok":true,"how":"zip",
 "path":"C:\\...\\tools\\repak\\repak.exe","size":2022374,"error":null}
```

| 字段 | 说明 |
|---|---|
| `ok` | 是否装好（按清单 `paths` 第一条验证） |
| `how` | `skip` 已就位 / `zip` 解压安装 / `file` 单文件 / `package` 来自全量包 / `manual` 需手工 / `fail` 失败 |
| `size` | 取回的字节数（用缓存时是缓存大小） |
| `error` | 失败原因；`manual` 时说明为什么不能自动下 |

全下成功 → 进程退出码 `0`；仍有缺的 → `4`（与 `--tools` 一致）。
壳可以据此显示「自动补工具」进度：`progress` 事件同样是 `phase=extract`。

### `channel`
走哪条拆包通道（V0.17）。在 `detect` 之后、`stage` 之前发，一次运行一条。

```json
{"t":"channel","seq":5,"ms":163,"engine":"gamemaker","channel":"third",
 "label":"第三方","note":null}
```

`channel` ∈ `self` | `third`；`note` 是给用户看的原因（默认情况为 `null`）。
判定见 `me/channel.py`：默认第三方，`--experimental` 与 `--self` 同时给出才换自研，
CTF 永远自研。壳可以用 `label` 直接显示「自研 / 第三方」，不要自己推。

### `stage`
进入提取阶段。

```json
{"t":"stage","seq":5,"ms":162,"name":"extract","engine":"gamemaker","engine_name":"GameMaker"}
```

### `progress`
进度。带节流（最快约 6 条/秒），只有 `done >= total` 那条一定会发。

```json
{"t":"progress","seq":6,"ms":163,"phase":"extract","done":120,"total":552,"label":"spr_hero.png"}
```

壳应该用 `done/total` 更新进度条，不要用事件条数。

0.14 起 `total` **永不回退**，`done` 只增不减，收工时一定有一条 `done == total`。
所以可以直接显示百分比，进度条不会往回跳。

`total` 的单位是**工作单元**，不是文件数：解不开的纹理页、名字撞车的散落文件也
照样记一个单位并推进，所以 `total` 是 `result.written` 的**上界**，可能略多几个。
实测（0.14 Beta 00034）：

| 样本 | 引擎 | total 段 | 收工读数 | written | 差 |
|---|---|---|---|---|---|
| Undertale True Genocide | gamemaker | 603 → 620 | 620/620 | 620 | 0 |
| PSOutertale | electron | 3990 → 3993 | 3993/3993 | 3993 | 0 |
| cage-of-abyss | enigma | 2114 → 2117 | 2117/2117 | 2116 | +1 |
| dtlg（单文件 CTF） | clickteam | 1704 | 1704/1704 | 1698 | +6 |
| Undertale Last Breath | clickteam | 2211 | 2211/2211 | 2211 | 0 |

**壳不要假设 `total == result.written`。** 只保证 `total >= written`，
进度条不会在活干完之前满格。

「段」是内部记账单位：每一层（子解析器、收尾的散落文件收集）报自己那一段的
`done/total`，由 `me/progress.py` 的账本折进全局总数。段与段之间全局总数会
**变大一点**（新段的活是新发现的），但不会掉回去 —— 0.13 那种 `3990 -> 3`
的回跳就是这么来的：谁最后报谁说了算。

0.13 及以前 `total` 会随子任务变（16 -> 254 来回跳），因为那时资源是交给
UndertaleModTool 导的 —— 黑盒里有多少活，CLI 数不出来。

### `result`
提取结束时的结构化结果。这是壳真正要的数据。

```json
{"t":"result","seq":14,"ms":171,"ok":true,"written":3,
 "out_dir":"D:\\out","engine":"gamemaker","engine_name":"GameMaker",
 "detail":"从 data.win 导出 3 个文件",
 "categories":{"image":1,"text":1,"audio":1},
 "notes":["数据文件: data.win"],
 "fallback":false,"error":null,"elapsed":0.01}
```

| 字段 | 说明 |
|---|---|
| `ok` | `written > 0` |
| `written` | 写出文件总数 |
| `out_dir` | 实际输出根目录（没传 `-o` 时是按游戏名推出来的那个） |
| `detail` | 一行事实，名词短语，不带句子。`--repair` 下是 `原文件 -> 产物` |
| `categories` | 分类 → 数量 |
| `notes` | 引擎给的补充说明，全部是字符串 |
| `fallback` | 是否走了通用收集兜底 |
| `channel` | `self` / `third`，与 `channel` 事件一致（V0.17） |
| `error` | 失败原因，没失败是 `null` |
| `elapsed` | 秒 |

`notes` 与 stderr 已经打过的行逐字相同，壳不要再打一遍（否则日志里每条两份）。

`--repair` 也发 `result`：`written` 恒为 `1`，`categories` 为空，
`notes` 是重建的四类改动逐行记录。壳靠 `written` 判断有没有产出，
所以这条事件不能省。

### `end`
最后一条。一定有。

```json
{"t":"end","seq":15,"ms":171,"exit":0}
```

`exit` 与进程退出码一致。收到它就可以收工了；也可以直接等进程退出。

### 自描述事件

`--version` → `version`；`--categories` → 每个分类一条 `category`；
`--engines` → 每个引擎一条 `engine`；`--tools` → 每个工具一条 `tool`；
`--setup` → 一条 `config`。

```json
{"t":"category","seq":4,"ms":131,"key":"image","label":"图片"}
{"t":"engine","seq":3,"ms":120,"key":"gamemaker","name":"GameMaker"}
{"t":"tool","seq":4,"ms":148,"key":"assetstudio","ok":true,
 "path":"...\\AssetStudio.CLI.exe","expect":"assetstudio/AssetStudio.CLI.exe",
 "desc":"AssetStudio CLI: Unity"}
{"t":"config","seq":5,"ms":140,"file":"...\\setup.ini","exists":true,
 "out_path":"Extract Resources","out_root":"...\\Extract Resources","error":null}
```

壳可用 `--categories` / `--engines` 动态建下拉框，无需写死列表。

## 6. 取消协议

1. 壳给每次运行指定一个独占的取消文件路径，传 `--cancel-file PATH`；
2. 用户点"取消"→ 壳创建那个空文件；
3. 程序在两处检查它：
   - 阶段边界（识别后、提取前）
   - 外部 CLI 运行期间（每 0.5 秒一次），命中就先把子进程收掉再退出
4. 事件流给出 `note(warn, "已取消")` + `end(exit=5)`，进程退出码 `5`；
5. 壳收到后删掉取消文件。

取消是优雅退出：不会留下 evbunpack / AssetStudio / repak 孤儿进程，已写出的文件保留。
`--cancel-file` 只在 `--json` 下有效。

## 7. WinForms 接入示例

```csharp
var psi = new ProcessStartInfo
{
    FileName               = exePath,
    UseShellExecute        = false,
    CreateNoWindow         = true,
    RedirectStandardOutput = true,   // 事件流
    RedirectStandardError  = true,   // 人读日志
    StandardOutputEncoding = Encoding.UTF8,
    StandardErrorEncoding  = Encoding.UTF8,
};
foreach (var a in new[] { target, "-o", outDir, "--json",
                          "--cancel-file", cancelPath })
    psi.ArgumentList.Add(a);         // 不要手工拼引号，ArgumentList 会处理

var p = new Process { StartInfo = psi, EnableRaisingEvents = true };

p.OutputDataReceived += (_, e) =>
{
    if (e.Data == null) return;                 // 流结束
    var ev = JsonSerializer.Deserialize<JsonElement>(e.Data);
    switch (ev.GetProperty("t").GetString())
    {
        case "detect": /* 刷新引擎标签 */ break;
        case "progress":
            var done  = ev.GetProperty("done").GetInt32();
            var total = ev.GetProperty("total").GetInt32();
            BeginInvoke(() => progressBar.Value =
                total > 0 ? done * 100 / total : 0);
            break;
        case "result": /* 填结果面板 */ break;
        case "end":    /* 收尾 */       break;
    }
};

p.ErrorDataReceived += (_, e) => { if (e.Data != null) logBox.AppendText(e.Data + "\r\n"); };

p.Start();
p.BeginOutputReadLine();
p.BeginErrorReadLine();
await Task.Run(() => p.WaitForExit());
int code = p.ExitCode;      // 退出码表见第 4 节
```

取消：

```csharp
File.WriteAllBytes(cancelPath, Array.Empty<byte>());
```

## 8. 已知坑

1. 必须 `RedirectStandardOutput = true`。 否则子进程会开自己的控制台窗口。
2. 别用 `StandardOutputEncoding` 之外的编码读。事件是 UTF-8。
3. 别把 stderr 当数据源。 它混着外部工具的原始输出，格式不保证。
4. `progress` 有节流，条数不等于文件数，用 `done/total`。
5. `--detect` 不发 `result`，它只发 `detect` + `end`。
6. 参数错误（exit 2）时不会有 `detect`/`result`，只有 `start` + `note(err)` + `end`。
7. 长任务不要设 `WaitForExit(int)` 之外的超时逻辑；要中断就用取消文件，别 `Kill()`——`Kill()` 会留下外部 CLI 孤儿进程。
8. 没传 `-o` 时输出落在 exe 旁边的 `Extract Resources\<游戏名>\`，用 `result.out_dir` 拿真实路径，不要自己拼。

## 10. 在线更新（V0.18E）

壳不需要理解解压逻辑，只要知道「CLI 会把自己换掉再退出」：

```
壳 → CLI --update -y（--json）
      update_check → update_ready → update_launch → end(exit=0)
CLI 退出（释放文件锁）
daemon.bat → 等 2 秒 → tar 解压覆盖 → 删包 → start 新 CLI → 自删除
```

三条给壳的注意事项：

1. 看到 `update_launch` 后**不要**再对同一个子进程做任何操作，它马上会退出；
2. 退出码 `0` 不代表「更新完成」，只代表「已交接」；真正完成以文件被覆盖为准；
3. 重启后的新进程需要壳重新 locate CLI（版本可能变了，`version.ini` 会更新）。

## 9. 壳的分发与 CLI 定位（V0.17）

壳（MultiExtractorGUI）从 V0.17 起是 **.NET 10 自包含单文件 exe**（约 47 MB），
用户机器不用装 .NET。代价：**ClickOnce 作废**，分发变成「拷目录」。

推荐布局：

```
MultiExtractor-<版本>-win64\
├── MultiExtractorGUI.exe     壳（47 MB，.NET 10 自包含单文件）
├── MultiExtractorCLI.exe     CLI（20 MB 目录，Slim 不带 tools/）
├── resources\                CLI 的 Python 运行时
└── setup.ini  version.ini
```

V0.18E 起默认是 **Slim** 包：**没有 `tools/`**（省掉 178 MB）。外部工具由
`tools.ini` 清单（远端 / 缓存 / 内置三级）+ 首次运行的软键菜单现补；
要把工具随包发，用 `python make_dist.py --full`。

定位顺序（`Form1.RequireCli`，找到即停）：

1. 环境变量 `ME_CLI_DIR` —— 显式指定；
2. `<壳所在目录>\` —— **同级目录，打包默认，优先**；
3. `<壳所在目录>\cli\` —— cli 子目录（兼容旧包）；
4. `%LOCALAPPDATA%\MultiExtractor\CLIPath.ini` 的 `[CLIDirPath] Dir` —— 安装式。

都没有时，壳把右上角版本位置换成红字 `未找到 CLI · 点此定位`，点开选目录：
壳会校验 PE 头（挡掉同名文本文件/半截下载 —— 否则 Windows 会弹「不支持的
16 位应用程序」），再试跑一次 `--version`（退出码 0 且输出含 MultiExtract 才
算通过），通过后写 `CLIPath.ini` 并重新加载版本/分类/引擎。首次成功定位时若
ini 不存在，壳也会自动写一份 —— 全新机器不需要任何人手写 ini。

候选目录里先找 `MultiExtractorCLI.exe`，没有再找 `main.py` + Python（源码形态，
窗口标题标 `[DEVELOPER MODE]`，版本从 CLI 目录的 `version.ini` 读）。

版本与署名只有一个来源：CLI 目录下的 `version.ini`（`me/config.py` 写出）。
壳不写死版本号，换 CLI 就换版本显示。
