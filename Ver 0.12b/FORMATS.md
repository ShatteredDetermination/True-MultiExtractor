# 容器格式笔记

这份文档记录本项目各解析器**实际依赖的格式细节**，以及每条结论的验证方式。
自己写解析器时最大的风险是「照着一个记错的格式写，测试还自洽」，
所以这里对每条结论都标注了**证据等级**：

- **【实测】** —— 用真实游戏文件验证过（贴了具体数字）
- **【自洽】** —— 只用合成样本往返测试过，加密和解析可能共享同一个错误理解
- **【公开】** —— 来自公开的格式文档 / 开源实现，未在本机用真实样本复现

---

## 1. Clickteam Fusion 2.5（CTF）

解析器：`me/formats/ctf25/`　　证据等级：**【实测】**

样本：
- `Undertale Last Breath Scrapped.dat`（66.4 MB，build 295）→ 2138 张图 / 68 段音频
- `dtlg-v1.1.0.exe`（137.6 MB，build 292，单文件）→ 1515 张图 / 172 段音频 / 内嵌 TTF

> 格式参考自 CTFAK 2.0 的开源实现（`ChunkList.cs` / `Chunk.cs` / `ImageBank.cs` /
> `SoundBank.cs` / `FontBank.cs` / `PackData.cs` / `ImageTranslator.cs`），
> 但所有结论都在上面两个真实样本上复现过。**不使用 CTFAK 的二进制。**

### 1.1 定位 CCN 数据流

文件可能是裸 `.dat`，也可能是把数据拼在尾部的单文件 exe。统一规则：

```
在文件里找 "wwww"
  要求 u32@(pos+8)  == 32          (header_size)
  则   tag = pos + u32@(pos+12) - 32
  要求 buf[tag:tag+4] ∈ {PAMU, PAME, CRUF}
CCN 数据流从 tag 开始
```

实测对照：

| 样本 | wwww 偏移 | header_size | data_size | tag 偏移 | tag |
|---|---|---|---|---|---|
| Last Breath.dat | 0x0 | 32 | 64 | 0x20 | PAMU |
| dtlg.exe | 0x106600 | 32 | 4267768 | 0x5184d8 | PAMU |

> `.dat` 里 tag 恰好等于 32，所以「跳过 32 字节再读」这种写法看起来也能work，
> 但对单文件 exe 就不成立了——必须按 `dataSize` 算。

`PAMU` = Unicode 字符串（Fusion 2.5），`PAME` = ASCII（MMF2），`CRUF` = Fusion 3。
字符串编码方式由 magic 决定，后续所有字符串读取都依赖它。

### 1.2 头部

```
magic            4 字节  (PAMU / PAME / CRUF)
runtime_version  u16
runtime_subver   u16
product_version  i32
product_build    i32        <- 实测 Last Breath=295, dtlg=292
之后紧跟 chunk 流
```

### 1.3 chunk 结构

```
i16 id
i16 flag      0=未压缩  1=zlib  2=加密  3=压缩且加密
i32 size      紧跟其后的数据长度
```

`flag == 1` 时，那 `size` 字节的布局是：

```
u32 解压后大小
u32 压缩后大小      ( == size - 8 )
zlib 流
```

**【实测】** Last Breath 第一个 chunk：`id=0x2223, flag=1, size=67`，
头里写 `decl=112, comp=59`，`8 + 59 == 67`，
`zlib.decompress` 精确解出 112 字节。同批 chunk 解出：

| chunk | 名字 | 解出内容 |
|---|---|---|
| 0x2223 | Header | 112 字节二进制 |
| 0x2224 | Name | UTF-16 `"Undertale Last Breath - [Scrapped]"` |
| 0x2225 | Author | UTF-16 `"User"` |
| 0x223B | Copyright | UTF-16 `"Undertale/Toby Fox"` |

### 1.4 图像（chunk 0x6666）

Bank 开头是 `i32 条目数`，之后每条（Fusion 2.5+ 格式）：

```
i32 handle          (读到后减 1)
i32 checksum
i32 references
i32 保留
i32 dataSize        ( == 4 + LZ4 数据长度 )
i16 width
i16 height
u8  graphicMode
u8  flags
i16 保留
i16 hotspotX, hotspotY
i16 actionX,  actionY
4B  transparent (r,g,b,a)
i32 解压后大小
LZ4 数据 (dataSize - 4 字节)
```

**【实测】** Last Breath 第一条：`w=32 h=32 mode=8`，
解压后大小字段 = `4096` = `32×32×4`，完全吻合。
2138 条全部解析成功，`graphicMode` 全是 8。

**判定是否用 2.5+ 布局**：看 chunk `0x2253`（2.5+ Object Headers）
是否出现在图像库 `0x6666` **之前**。
【实测】Last Breath 里 0x2253 在第 22 个、0x6666 在第 45 个；dtlg 里是第 20 / 第 61 个。

**像素格式**（`imagecodec.py`）：

| graphicMode | 含义 |
|---|---|
| 4 | 24bpp BGR；flags 的 Alpha 位置位时尾部追加 8bit alpha 平面 |
| 6 | 15bpp（5-5-5，占 16 位） |
| 7 | 16bpp（5-6-5） |
| 8 | Fusion 2.5+ 32bpp **BGRA** |
| 0~5 | Android 专用，本工具不支持 |

行距填充规则（对应 CTFAK 的 `GetPadding`）：

```
pad_pixels = byte_align - (width * point_size) % byte_align
if pad_pixels == byte_align: 0
else: ceil(pad_pixels / point_size)
```

其中 `byte_align` 默认 2，alpha 平面用 4。
**mode 8 的行距永远无填充**（`width*4 % 2 == 0`），所以最省事。

**【实测】通道序验证**：把导出的精灵拼成一张对照图，
Sans 的蓝色外套显示为蓝色、`PRESS Z` 是绿色、青色菱形是青色。
如果 R/B 弄反，蓝色会变成橙色——所以 `BGRA -> RGBA` 的换序是正确的。

`flags` 位序（低位在前）：`RLE, RLEW, RLET, LZX, Alpha, ACE, Mac, RGBA`。
Alpha 位和 RGBA 位都没置位时，用 `transparent` 颜色做抠图；
**【实测】** 名单图 `big_2137_303x1042.png` 只 6% 像素不透明，抠图生效，
且文字清晰可读（`Ari, Supgreks, Norioga, Red07, Nocochip, Joshie, ...`）。

### 1.5 音频（chunk 0x6668）

```
i32 条目数
每条:
    u32 handle         (减 1)
    i32 checksum
    u32 references
    i32 解压后大小
    u8  flags
    3B  保留
    i32 保留
    i32 名字长度（字符数）
    若 flags != 33:
        i32 压缩后大小
        zlib 数据
    否则:
        直接跟「解压后大小」字节
    UTF-16 名字（名字长度 个字符，常含 \0 填充）
    剩余字节 = 完整音频文件内容
```

**【实测】** Last Breath 解出 68 段（35 wav / 22 ogg / 11 mp3，共 47.0 MB）；
dtlg 解出 172 段（84 mp3 / 83 ogg / 5 wav，共 124.9 MB）。
音频格式靠文件头判断，记录里不带格式信息。

> 坑：声明长度里带 `\0` 填充，直接 `.strip()` 去不掉，要 `.replace("\x00","")`。

### 1.6 字体

两个不同的东西：

- chunk `0x6667`：只有**元数据**（LogFont：字体名 / 字号 / 粗细），没有字体文件
- chunk `0x2259`：**真正内嵌的 TTF**，一条接一条，每条 `[u32 解压后大小][u32 压缩后大小][zlib]`

**【实测】** dtlg 的 0x2259 有 575030 字节，解出 10 个字体文件；
Last Breath 的 0x6667 声明了 `Tahoma` / `Comic Sans MS` / `@8bitoperatorCN` 等。

### 1.7 打包文件表

`wwww` 头后面、CCN 数据流之前可以有一张表（`i32 count` 在 `wwww+28`）：

```
u16 名字长度          (字符数)
名字                  (UTF-16 或 ASCII，取决于 magic)
i32 bingo
i32 数据长度
数据                  (bingo == -9608 / 0xDA98 时是 zlib 压缩的)
```

**【实测】** dtlg 的表有 39 项，第一项 `mmfs2.dll` 786944 字节；
Last Breath 的表 count = 0。
这些是运行时 dll / mfx，不是游戏资源，默认不导出。

---

## 2. Electron asar

解析器：`me/formats/asar.py`　　证据等级：**【实测】**

样本：`PSOutertale/resources/app.asar`（252 MB）→ 3990 个文件 / 251.1 MB / 9 个目录

布局（chromium Pickle 序列化）：

```
偏移 0   u32 = 4                    pickle#1 载荷长度（固定 4）
偏移 4   u32 = headerBuf 长度
偏移 8   u32 = 4 + 对齐后的 json 长度
偏移 12  u32 = json 原始长度
偏移 16  json（4 字节对齐，不足补 0）
之后     文件数据区
```

**【实测】** 这个样本 `data_offset = 1061824`，
按上述布局解析出的 JSON 顶层是 `assets / code / icon.png / index.js / package.json / preload.js / spacetime.js`，
扩展名分布 `png×2512, json×1048, mp3×347, ts×59, csv×17`。

文件节点的 `offset` 是相对数据区的字符串；
`unpacked: true` 的节点数据在同级 `<名字>.unpacked/` 目录里；
`link` 节点是符号链接，跳过。
代码里还留了一个兜底路径：如果头部长度字段对不上，就直接扫 `{"files":` 找 JSON 起点。

---

## 3. GameMaker（data.win）

证据等级：**【实测】**（但不解析格式，走 UndertaleModTool）

样本：`Undertale True Genocide/data.win`（7.8 MB）→ 552 图 / 98 内嵌音频 + 16 外置 / 3595 字符串

不自己解析 `.win`（格式随 GameMaker 版本变化很大），而是调用 UTMT CLI：

```bat
UndertaleModCli.exe load <data.win> -s <脚本1.csx> -s <脚本2.csx> -s <脚本3.csx>
```

两个坑：

1. **`-s` 只能带一个脚本路径**，多脚本要写多次 `-s`（帮助文字写的是
   `Ex. a.csx b.csx`，但实测那样会被当成一个文件名）。
2. **不能用 `conhost --headless` 包一层**。实测包了之后 UTMT 一个文件都不导出
   （退出码还是 0，很有欺骗性），直接调用才正常。

UTMT 自带的导出脚本用 `PromptChooseDirectory()` 弹目录框，无人值守跑不了，
所以本项目在 `tools/utmt/Scripts/MultiExtractor/` 下放了三个自己的脚本，
输出目录从环境变量 `ME_OUT` 读。

### 3.1 容器结构（【实测】，自己走通了）

`data.win` 是一串顺序 chunk：

```
0   FORM
4   u32 LE 总长（= 文件长度 - 8）
8   GEN8              <- FORM 的 type 码，同时就是第一个 chunk 的名字
12  u32 LE GEN8 chunk 长度 L
16  GEN8 数据（L 字节）
16+L 起：<名字4字节><u32长度><数据> 依次排列
```

拿两个真实文件走过，链都能**一路走到文件末尾、长度字段全部自洽**，
chunk 名和顺序也完全一致（23 个）：

```
OPTN LANG EXTN SOND AGRP SPRT BGND PATH SCPT GLOB SHDR FONT TMLN OBJT
ROOM DAFL TPAG CODE VARI FUNC STRG TXTR AUDO
```

注意：`data.win` 里存着**绝对文件偏移**（chunk 内部的指针数组、字符串表
偏移都是文件绝对位置）。这意味着**任何改变文件长度的修改都会连带损坏
后面所有 chunk**。这一点在 3.2 里是要命的。

### 3.2 UTMT 读不了的时候（自写兜底，【实测】）

样本：`Downloads/data.win`（61.1 MB）—— 容器完好，但 UTMT 硬失败：

```
Reading misaligned at 001EAD30, realigning back to 00F4933F
Reading out of chunk bounds at 00F4933F while reading UndertaleFont
while reading item 2 of 20 in a list of UndertaleFont in chunk FONT
```

**根因（实测确认，不是猜的）**：FONT chunk 的指针数组是
`count(20) + 20×绝对偏移`，其中 **6 个指针指向 16.0~16.25 MB**，
落在 STRG 的声明区间内部。而那 6 个位置**确实存在合法的 font 条目** ——
它们开头的两个字段是指向字符串表的合法指针，紧邻的字节里还有
`"fnt_papyrus"` / `"Papyrus"` 这样的字体名字符串。也就是说这 6 个字体
（连同名字）被物理放进了字符串表区间，于是 FONT 坏（指针越界）、
STRG 也坏（字符串表里插进了字体数据）。

处理分三层，逐级往下退：

| 层 | 做法 | 实测结果 |
|---|---|---|
| 1 | 直接跑 UTMT | 失败（FONT） |
| 2 | 把出问题的 chunk **原长占位**清成空列表再跑 | 清掉 FONT → 死在 STRG；再清 STRG → 死在 TXTR；放弃 |
| 3 | 放弃解析容器，按结构校验抓内嵌资源 | **成功：32 张纹理页 + 221 个 WAV** |

第 2 层的**铁律是长度必须严格不变**：实测把 FONT 从 149,296 压成 4 字节后，
UTMT 立刻从「读不了 FONT」变成「读不了 STRG」—— 因为绝对偏移全部错位了。
所以清空 = 写个 `count=0`、剩余字节原地填 0。

第 3 层只收**结构完整**的资源，宁可不收也不收垃圾：

| 格式 | 校验 |
|---|---|
| PNG | chunk 链自洽、IHDR 开头、IEND 结尾、尺寸 1~32768 |
| WAV | RIFF+WAVE+fmt 子块参数合理，且长度字段与实际完全一致 |
| OGG | 连续的 Ogg 页，页头结构合法 |

**不做 JPEG**：只按 SOI/EOI 硬切的话，在 61 MB 上能切出 2000 多个片段、
合计体积超过文件本身（实测占 108.9%，物理上不可能），而 GameMaker 的
`data.win` 里本来也不存 JPEG。

两个实测踩到的坑，都写进自测了：

1. **PNG 的 chunk 类型不能要求全大写。** PNG 规范里类型是 4 个字母，
   首字母大小写表示 ancillary 位，所以 `sRGB` / `gAMA` / `tEXt` / `pHYs`
   都是**合法**的。加了 `isupper()` 之后，真实的纹理页因为带 `sRGB`
   块被**全部拒掉**，一张图都抓不出来。
2. **清空的 chunk 必须能安全清。** 非列表型 chunk（`GEN8`/`OPTN`/`LANG`/
   `EXTN`…）塞个 `count=0` 只会更糟，所以有白名单。

第 3 层的输出说明里会明确写：抓到的是**整张纹理页**（图集），不是切好的
单个精灵 —— 精灵坐标在 TPAG chunk 里，而正是解析不了容器才走到这一步。

### 3.3 ⚠ 实验性：拿 UTMT 当预言机反推指针（【实测】，但**未完成**）

> 这一节记录的是 `me/formats/gamemaker_rebuild.py`，属于 **0.12b 废弃版**的实验性
> 功能，主线（0.12）里没有。写在这里是因为"报错文本就是答案"这个发现本身有价值，
> 不该随着废弃丢掉。

UTMT 对指针列表的要求是**条目严格连续**，第 i+1 个条目必须正好接在第 i 个结束的
位置，否则抛：

```
Reading misaligned at <上一个条目结束的位置>, realigning back to <指针里写的值>
```

注意这行报错把**正确位置**直接说出来了 —— `Reading misaligned at` 后面那个地址
就是下一个条目的真实起点。所以可以拿 UTMT 当**预言机**：一轮修一个指针，
迭代到指针表连续为止。

实测（`Downloads/data.win`，61 MB，FONT chunk 20 个指针里 6 个指向 16 MB 处）：

```
第 1 轮：FONT 槽位 [1] 由 0xf4933f 修正为 0x1ead30
第 2 轮：FONT 槽位 [2] 由 0xf56acf 修正为 0x1ec260
第 3 轮：FONT 槽位 [4] 由 0xf6425f 修正为 0x1edf3c
第 4 轮：FONT 槽位 [5] 由 0xf719ef 修正为 0x1ef46c
第 5 轮：FONT 槽位 [8] 由 0xf721d7 修正为 0x1f0b70
第 6 轮：FONT 槽位 [9] 由 0xf7f967 修正为 0x1f131c
```

6 轮、4 秒，20/20 落在 chunk 内、严格递增。**关键是这些值可信**：反推出的
`0x1ead30`、`0x1ec260` 恰好夹在原本存活的 `0x1ea584` 和 `0x1ed790` 之间 ——
不是"UTMT 恰好接受"，是真算对了。

**为什么没做成**：修完指针后文件仍然死在

```
UndertaleSerializationException: Padding error in STRG
```

UTMT 读 STRG 是「读 count、读指针、顺序读字符串（`ReadGMString` 逐字节读到 NUL）」，
要求读完正好停在 chunk 末尾。而这个 STRG 的声明长度比实际内容长 **278,465 字节**
（多出来的就是那 6 个被挪走的字体条目）。唯一的修法是**缩短 STRG**，但那样
TXTR / AUDO 全体前移，文件里所有大于该位置的绝对偏移都要减掉同一个差值 ——
需要全局偏移重写器，没实现。

试过一条捷径（把一个字符串撑到 chunk 末尾把它吞掉）：UTMT 一路读到了
`00F8D17F`（正好是 chunk 末尾）**还要再读**，证明字符串是顺序消费的，
撑长一个只会让后面的字符串没地方读，行不通。

两个实测踩到的坑：

1. **每轮只能改一个槽位。** 我最初一次把「值等于报错值」的槽位全改掉。真实文件里
   6 个坏值互不相同，侥幸没暴露；但多个槽位写着同一个坏值（比如全 0）时，
   一次全改会把它们一起填成同一个值。
2. **目标 chunk 必须以 UTMT 报的为准。** 新版 GameMaker 里 `VARI` / `FUNC` / `LANG`
   **不是**「count + 绝对指针」这个布局，硬按这个读会得到一堆荒谬数值
   （实测 LANG 报成「1 个指针里 1 个越界」，VARI 3907/3907 全越界）。
   靠诊断顺序去挑目标，会跑去修一个根本不是指针数组的 chunk。
   诊断因此加了保守判据：指针不到一半落在 chunk 内就认为布局读错了，不报。

---

## 4. Enigma Virtual Box

证据等级：**【实测】**

样本：`cage-of-abyss-full-game-v0.2.2.exe`（69.3 MB）

识别依据：文件里同时含 `.enigma1` 和 `YYRunnerInterface`。
**同一个 exe 里能搜到内层引擎特征**，所以识别时要把「壳」和「内层引擎」分开报。

流程：`evbunpack.exe <壳exe> <输出目录>` 脱出 5 个文件（1 个是还原出来的原始 exe），
然后**重新识别**输出目录 → 识别为 GameMaker → 找到 `data.win` → 交给 GameMaker 模块。

**【实测】** 最终导出 2026 图 / 142 音频 / 15867 条字符串，耗时 36.6 秒。

---

## 5. Godot PCK

解析器：`me/formats/godot_pck.py`　　证据等级：**【公开】+【自洽】**

本机**没有真实 `.pck` 样本**，所以这一节没有【实测】结论。头部布局取自 Godot 源码
（3.5 的 `core/io/pck_packer.cpp` + `core/io/file_access_pack.cpp`、4.3 的
`core/io/pck_packer.cpp`、master 的 `core/io/file_access_pack.h`）；
布局判定逻辑用合成样本往返测试（`selftest/test_godot.py`，66 项断言全过）。

### 5.1 头部

```
u32 magic = 0x43504447 ("GDPC")
u32 format_version          1 = Godot 3.x，2 = Godot 4.x，3/4 = 4.4+（sha256 目录）
u32 ver_major, u32 ver_minor, u32 ver_patch
u32 flags                   PACK_DIR_ENCRYPTED=1 / PACK_REL_FILEBASE=2 / PACK_SPARSE_BUNDLE=4
-- 版本区 --
 v1（Godot 3.x）：没有 file_base 字段，后面直接是 64 字节预留区
 v2（Godot 4.x）：u64 file_base，之后 64 字节预留区（4.3 起里面是签名偏移/长度）
-- 目录区 --
u32 file_count
每条记录：
    u32 path_len            真 Godot 存的是「路径长度 + 补齐字节数」，已按 4 字节对齐
    path[path_len]          UTF-8，补齐处填 0
    u64 offset, u64 size
    u8  md5[16]             v3/v4 起换成 sha256[32]
    u32 flags               v2 起才有：PACK_FILE_ENCRYPTED=1 / PACK_FILE_REMOVAL=2
```

目录起点（`file_count` 所在字节）——数字都来自上面那些源码：

| 版本 | 目录起点 | 依据 |
|---|---|---|
| v1 | **0x54 = 84** | `pck_start` 写 5×u32（20）+ 16×u32 预留（64）；读侧同样是 5×u32 + 16×u32 |
| v2 | **0x60 = 96** | 20 + u32 flags + u64 file_base + 64 预留（4.3 读侧 = signature_offset/size 各 8 + 12×u32） |
| v1 简化写法 | 0x18 = 24 | 只有 4 字节 flags、没有预留区（本项目格式说明的写法，合成样本已覆盖） |
| v2 简化写法 | 0x30 = 48 | 4 + 8 + 16 字节预留区（同上） |

`path_len` 的两种口径（算不算补齐字节）都吃得下：读的时候按 `(-path_len) % 4` 补跳，
再把路径末尾的 `\0` 去掉——真 Godot 存的是含补齐的长度（此时只需去掉 `\0`），
简化写法存的是不含补齐的长度（此时正好跳 1~3 字节）。

### 5.2 offset 的基准（最容易写错的一处）

- **Godot 3.x**：`flush()` 里 `ofs` 从「对齐后的数据区绝对位置」开始累加，记录里存的是
  **文件绝对偏移**，读侧直接 `ofs + p_offset`。
- **Godot 4.x**：`add_file()` 里 `ofs` 从 0 开始累加（相对数据区），`flush()` 收尾时回到
  头部把**数据区的绝对位置**写进 `file_base`，读侧是 `file_base + ofs`。
- 本解析器两种都算一遍：谁能让「所有条目都落在固定头部之后、文件末尾之内、互不重叠、
  且不和目录区交叉」就信谁，版本只决定先试哪个；都不自洽就取越界最少的那个，
  并在报告里写「数据可能错位」——宁可不写，也不错位写出去。
- **「相对数据区起点」的第三方变种不做猜测**（`file_base` 留 0 的那种），会当失败报出来。

### 5.3 往返验证（【自洽】）

`selftest/test_godot.py` 自己造 6 种布局的 `.pck`，每个包 6 个文件
（png / ogg / json / 中文名 txt / 1.5MB 大文件 / `res://../../evil.png`），逐字节比对：

| 布局 | 头部 | 目录起点 | 实际判出的基准 |
|---|---|---|---|
| spec_v2 | flags + file_base + 16 预留 | 0x30 | 相对 file_base=0x1C0 |
| godot4_real | flags + file_base + 64 预留 | 0x60 | 相对 file_base=0x200 |
| spec_v1 | flags、无 file_base | 0x18 | 文件绝对偏移 |
| godot3_real | 无 flags、64 预留 | 0x54 | 文件绝对偏移 |
| v2_absolute | 同 godot4_real，但记录存绝对偏移 | 0x60 | 文件绝对偏移（自动判出） |
| v2_nopad_len | `path_len` 不含补齐字节 | 0x30 | 相对 file_base=0x1C0 |

同时验证：目录穿越（`res://../../evil.png` 落到 `image/evil.png`，并把企图报给用户）、
非 PCK 与加密目录抛 `PckError`、逐文件加密/标记删除的条目跳过并计数、分类过滤、
**尾部被砍掉一半的包只写完整的 4 个文件**并标注「基准不自洽」、
1.5MB 的文件走分块搬运（>1MB 不整块进内存）、引擎三条分支（目录里找 pck / 工程形式 /
`deep=False` 只提示 + fallback）。

### 5.4 未验证 / 不确定的部分

1. **v3/v4 只是尽力而为**：按「记录哈希换成 sha256[32]、目录起点仍在 96/48」猜的，
   没有源码级确认也没有样本；解不出来会明确报错，不会静默出错数据。
2. **编辑器导出的包**（数据区可能在目录之前）只做了布局无关的校验，
   本机没有真实样本；校验允许这种摆放，但没有【实测】。
3. **加密的 `.pck` 不支持**：目录加密直接抛错；逐文件加密的条目跳过并计数。
4. **内嵌在 exe 里的 pck**（`p_offset != 0` / `PACK_REL_FILEBASE`）没有处理，
   本工具只按独立 `.pck` 文件从偏移 0 读。

---

## 6. RPG Maker RGSSAD / RGSS2A / RGSS3A

解析器：`me/formats/rgssad.py`　　证据等级：**【实测】**

用 [uuksu/RPGMakerDecrypter](https://github.com/uuksu/RPGMakerDecrypter) 仓库里的
**真实游戏导出归档**做了交叉验证（`RPGMakerDecrypter.Tests/EncryptedArchives/`）。
这些不是我们造的样本，所以能证伪「生成器和解析器共享同一个错误理解」。

### 6.1 实测结果

| 文件 | 大小 | 头 8 字节 | 条目 | 解出的第一个文件 |
|---|---|---|---|---|
| `Game.rgssad` | 504228 | `52 47 53 53 41 44 00 01` | 16 | `Data/Actors.rxdata` 10981 字节 |
| `Game.rgss2a` | 373977 | `52 47 53 53 41 44 00 01` | 16 | `Data/Actors.rvdata` 10887 字节 |
| `Game.rgss3a` | 636580 | `52 47 53 53 41 44 00 03` | 16 | `Data/Actors.rvdata2` 3032 字节 |

三者解出的明文都以 **`04 08 5b .. 30 6f 3a 0f 52 50 47 3a 3a 41 63 74 6f 72`**
开头 —— 也就是 Ruby Marshal 头（`\x04\x08`）+ `RPG::Actor` 类名。
v3 那份还解出了日文（`@name I"\x11\xe3\x82\xa8` = "エ"），说明字节级解密正确。

### 6.2 两个把合成测试骗过去的坑

**坑一：魔数不是字面的 `RGSS3A`。**

真实归档的魔数**一律是 `"RGSSAD\0"`，第 8 字节才是版本号**（`1` = v1 格式，
`3` = v3 格式）。`.rgss2a` 和 `.rgss3a` 这两个文件名里虽然写着 2A/3A，
文件头里却是 `RGSSAD\0`。

早期版本的 `guess_archive_type` 要求字面 `RGSS3A` 才认，结果真实 `.rgss3a`
一个都识别不出来 —— 而合成样本因为用了错误的字面魔数，测试照样全绿。
**这是这份文档里"往返测试不能证伪格式理解"最有力的一个实例。**

**坑二：数据正文不参与表 key 的递推。**

v1/v2 的表是这样推进 key 的：

```
length = read_u32() ^ key          → key = key*7+3
name[i] = read_byte() ^ (key & 0xFF)   （每个字节之后 key = key*7+3）
size   = read_u32() ^ key          → key = key*7+3
fileKey = key
直接 seek(size) 跳过正文           ← key 不动！
```

正文自己也用「每 4 字节让 key 前进一次」的节奏，但那是**条目自己的 key 副本**
（`_decrypt_chunk` 里的局部变量），**不回灌到表的 key**。

依据：`RPGMakerDecrypter.RGSSAD/RGSSADv1.cs` 的 `ReadRGSSAD()` 里，读完 `Size`
之后只做 `BaseStream.Seek(Size, SeekOrigin.Current)`，没有改 key。

同样的错误如果犯在表解析里，第一条之后的 `nameLen` 就会读成垃圾，
但往返测试查不出来（生成器会犯同样的错）。

### 6.3 v3 的表结构

```
头 8 字节 + u32 initial_key
key = initial_key * 9 + 3          ← 只在这里前进一次
循环:
    offset   = read_u32() ^ key
    size     = read_u32() ^ key
    fileKey  = read_u32() ^ key     ← 该文件正文自己的起始 key
    nameLen  = read_u32() ^ key
    if offset == 0: 表结束
    name[i]  = read_byte() ^ keyBytes[i % 4]    （key 固定，按小端 4 字节循环）
```

注意 v3 的**整张表都用同一个 key**，`DecryptInteger` 不推进 key
（和 v1 的 `DecryptInteger(ref key)` 语义不同）。表的结束标记是
「offset 字段 == 0」，不是读到文件尾。

正文解密 v1/v3 相同：用条目的 key，按小端 4 字节循环异或，
每处理满 4 字节前进一次；尾部不足 4 字节的余数用当前 key 的低位字节，且不前进。

### 6.4 未验证的部分

- `RGSS2A` / `RGSS3A` 字面魔数的容错路径**没有真实样本**，只是少数第三方打包器
  可能这么写，代码里保留了这条分支
- 分块读取（1 MB）在跨块时的 key 对齐只用合成样本验证过
  （块大小是 4 的倍数，理论上不会错位）
- 没有测过超大归档（>2 GB）和名字特别长的条目


---

## 7. Unity / Unreal

证据等级：**【实测（针对 CLI 行为）】** + **【未验证（针对真实游戏资源）】**

本机**没有** Unity / Unreal 的真实游戏样本，所以这一节记录的是
「外部 CLI 的实测行为」和「参数拼装」，不是「格式解析」。
真实游戏上的导出效果**未经证实**。

### 7.1 AssetStudio CLI（Unity）

```
AssetStudio.CLI.exe <input_path> <output_path> --game <渠道> [--types 类型...] [--silent]
```

**退出码永远不可信**【实测】：输入目录不存在时它会打一整页
`DirectoryNotFoundException` 堆栈，退出码仍然是 **0**；
传错 `--game` 值也只打印 `Argument 'x' not recognized` 然后退出 0。

> 所以判断成败**必须**看输出目录里有没有文件，不能看 returncode。
> `unity.py` 里就是靠「输出目录里有没有文件」判成败的。

`--types` 的语义【实测】：
- help 原文是 `--types <Texture2D|Shader:Parse|Sprite:Both|etc..>`
- **冒号后面是「导出选项」而不是类型分隔符**：`Sprite:Both` = Sprite 用 Both 方式导出
- 多个类型用**空格**分隔
- 合法类型名（29 个）可以从 `tools/assetstudio/AssetStudio.CLI.dll.config` 里读到

`--game` 是必填项，候选值 40+ 个（`Normal` / `UnityCN` / `GI` / `BH3` / `SR` / `TOT` /
`Naraka` / `FakeHeader` / `NetEase` / ...）。加密或特殊渠道的游戏要换渠道值。

本项目分**两趟**调用（图片一趟、音频一趟），因为一次导出所有类型会慢很多。

### 7.2 repak（Unreal）

```
repak.exe [OPTIONS] <COMMAND>
repak.exe --aes-key <K> list  <pak>
repak.exe --aes-key <K> unpack <pak> --output <dir> --force
```

**【实测】`--aes-key` 必须写在子命令前面**（它是顶层选项）：

| 写法 | 结果 |
|---|---|
| `repak list --aes-key K x.pak` | `error: unexpected argument '--aes-key' found`，退出 2 |
| `repak --aes-key K list x.pak` | 正常，退出 0 |

hex 和 base64 都接受。`unpack` 默认带 `--strip-prefix ../../../`，
正好把 UE 的 `../../../Game/Content/...` 剥掉，所以不用自己传。

**【实测】repak 不支持 IoStore**：把 `.utoc` 喂给它只会得到
`version unsupported or is encrypted` 并退出 1，顶层 help 里也没有任何
utoc / iostore 字样。UE5 游戏经常只有 `.utoc`/`.ucas` 没有 `.pak`，
这种情况需要改用 FModel 或 UE Viewer。

**【实测】pak 魔数 `E1126F5A` 不在文件开头**。
一个 5031 字节的 pak 里，它出现在偏移 4827（尾部索引/footer 区域），
文件开头全是 0。这正好解释了为什么 `signatures.py` 里是用
`bytes.fromhex("E1126F5A")` 而不是 `5A6F12E1` 来判定的：
`0x5A6F12E1` 是小端读出后的值，存盘的字节序是 `E1 12 6F 5A`。

**往返验证**【实测】：用 `repak pack` 把含中文名文件和 4KB `.ubulk` 的目录
打成合法 pak，再用本项目的 `unreal.py` 解回来 →
5 个条目、5 个文件、**逐字节一致**，分类为 `{'text': 2, 'other': 3}`。

### 7.3 未验证的部分（重要）

1. **没有真实 Unity 样本**：`--game` / `--types` 从未对真 `.assets` 生效过。
   只验证了「参数拼装正确」以及「在假目录上退出码 0 且 0 文件」。
2. **`.utoc` 的结论用的是假文件**（256 字节随机数据）。
   可信度来自「help 无该子命令 + 报 version unsupported」。
3. **AES 加密 pak 未测**：只验证了 `--aes-key` 的位置和透传。
4. **AssetStudio 传了不存在的类型名是否静默过滤**未知。
   为此留了 `ME_UNITY_IMAGE_TYPES` / `ME_UNITY_AUDIO_TYPES` /
   `ME_UNITY_GAME` 三个环境变量作兜底（`--types` 设成空串 = 不限类型全量导出）。

