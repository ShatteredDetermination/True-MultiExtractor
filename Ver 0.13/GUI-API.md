# GUI 接口契约（Ver 0.13）

给 WinForms 壳用。壳只做两件事：拼命令行 和 逐行读事件流。

---

## 1. 两种运行方式

| 方式 | 命令 | 行为 |
|---|---|---|
| 双击 | `MultiExtractor.exe` | 原来的交互式 CLI 菜单，等人按键 |
| 壳调用 | `MultiExtractor.exe <target> -o <dir> --json` | 非交互，事件流走 stdout |

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
| `--tools` | 检查外部 CLI 是否齐全 |
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
| `categories` | 分类 → 数量 |
| `notes` | 引擎给的补充说明，全部是字符串 |
| `fallback` | 是否走了通用收集兜底 |
| `error` | 失败原因，没失败是 `null` |
| `elapsed` | 秒 |

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
{"t":"tool","seq":4,"ms":148,"key":"utmt","ok":true,
 "path":"...\\UndertaleModTool.exe","expect":"utmt/UndertaleModCli.exe",
 "desc":"UndertaleModTool CLI: GameMaker (data.win)"}
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

取消是优雅退出：不会留下 UTMT / evbunpack 孤儿进程，已写出的文件保留。
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
