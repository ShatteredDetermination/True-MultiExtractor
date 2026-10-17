# 路线图

## V0.17R（已发布）：自研 / 第三方两条通道

**用户指示（原话）**：「所有引擎都有自研工具……实验性选项是使用自研工具，
若启用实验性功能被选中且使用自研工具选中时换用自研工具拆包，默认用第三方工具，
CTF 除外。」

**版本号规则（2026-10-17 定，以后都这样）**：状态后缀紧贴版本线 ——
`R` 发布 / `Rc` 候选发布 / `E` 实验性 / `WIP` 开发中 / `S` 已废弃。
同一版本线只换后缀：开发中 `0.17WIP`，做好了 `0.17R`；开新线才改数字（`0.18WIP`），
五位编号单调累加。本目录已发布，标签 `0.17R Beta 00094`。
已发布的 0.16 在 `../Ver 0.16`（内容等于原 0.15E，只换标签）。
### 已落地

| 东西 | 位置 |
|---|---|
| 唯一判定处 | `me/channel.py`（`decide()` / `CAPS` / `matrix()`） |
| 上下文接线 | `me/engines/base.py` 的 `Context.channel` + `use_self` |
| 编排接线 | `me/runner.py`（发 `channel` 事件、`result.channel`、notes 首行） |
| CLI | `--experimental` / `--self` / `--no-self` / `--channels` |
| GUI | CheckBox1（实验性总闸）+ CheckBox7（自研核心）→ `--experimental --self` |
| 自测 | `selftest/test_channel.py`（51 项） |

真值表（`decide()` 的全部规则）：

| 实验性 | 自研开关 | 该引擎自研已实现 | 结果 |
|---|---|---|---|
| 关 | 任意 | 任意 | 第三方 |
| 开 | 关 | 任意 | 第三方 |
| 开 | 开 | 是 | 自研 |
| 开 | 开 | 否 | 第三方 + 说明「待补」 |
| — | — | 没有第三方（CTF 等） | 自研 |
| — | — | 第三方工具缺失 | 自研（自研也没覆盖则报错） |

### 还差（V0.17 未完成的部分）

1. **Unity 自研**：AssetStudio 现在仍是唯一通道。要自己写
   `me/formats/unity_assets.py`（SerializedFile + UnityFS bundle + Texture2D 解码）。
2. ~~**Unreal 自研**~~：已落地 `me/formats/ue_pak.py`，覆盖 pak v0–v9
   （UE4 全系，含 zlib/gzip 块）。剩 UE5 的 v10/v11（多层索引 + 位压缩条目），
   现在逐 pak 退回 repak。
3. ~~**Enigma 自研**~~：已落地 `me/formats/evb.py`（只读虚拟文件系统，LZMA 支持）；
   不做 PE 还原 —— 那是「把原始 exe 拼回来」，对抽资源没有用。
4. Unity 做完后，`selftest/test_channel.py` 里「待补只允许出现在 unity」那条
   断言改成「一个都不许有」。

### 验收（这一版算不算做完）

* `python selftest/run_all.py` 全绿（当前 26 个脚本）；
* `--channels` 里自研状态全部是「已实现」；
* 每个自研解析器都有对照测试：同一样本，自研与第三方解出的条目集合可比，
  差异要能逐条解释（不是「大概差不多」）。
  * Unreal：`selftest/test_ue_pak.py`（17 组 repak 对照 + 手写合成 pak）；
  * Enigma：`selftest/test_evb.py`（真样本 4/4 逐字节相同 + 手写 PE/EVB）；
  * Unity：待补。

---

## V0.15E（已发布为 0.16）：全量重建

V0.15 那套「在原 span 里缝补」的做法已经停用。data1.win/data2.win 证明了它
没有自由度：长度和布局被一串绝对偏移绑死，补一处顶出一处。

现在的路线是**把文件当代码**：读成对象 → 从零算布局 → 重新写出。

### 分层

| 文件 | 职责 |
|---|---|
| `me/formats/gamemaker_ast.py` | 纯语义模型。字符串、字形、字体，以及它们之间的引用关系。**不含任何文件偏移**。 |
| `me/formats/gamemaker_serializer.py` | 布局与写出。两遍走同一段 emit 代码：第一遍只推进游标、登记地址；第二遍真写字节并核对每个对象是否落在登记地址上。 |

引用用对象持有关系表达（`FontItem.name` 就是那个 `GmString`），写出时翻译成
绝对偏移。这样解析代码只管语义，布局怎么变都碰不到它。

两种字符串地址必须分开，这是最容易错的地方：

* STRG 指针表里存**记录起点**（长度字段所在处）—— UTMT 走
  `ReadUndertaleObjectPointer`，直接用这个值；
* 别处的 name 字段存**正文起点**（记录起点 + 4）—— UTMT 走
  `ReadUndertaleString`，会自己做 `addr - 4`。

Serializer 因此提供 `ref()`（记录起点）和 `string_ref()`（正文起点）两个入口。

### STRG 的对象模型

```
u32 count
u32 记录起点[count]
记录[count]          # 按表顺序紧挨着排
0x00 补位            # 补到**文件绝对** 128 边界
```

补位是关键：UTMT 读完最后一条后

```csharp
while (reader.AbsPosition % 0x80 != 0)
    if (reader.ReadByte() != 0) throw new IOException("Padding error in STRG");
```

起点是「按表顺序最后读的那条」的末尾，终点是下一个 128 的倍数。所以只需让
chunk 一直长到那个边界，这件事就自动成立 —— **长度可变之后，data2.win 那个
「差 40 字节」的问题直接消失了**，不需要搬字符串。

### FONT 的对象模型

```
u32 count
u32 条目起点[count]
条目:
  +0  u32  Name        -> 正文起点
  +4  u32  DisplayName -> 正文起点
  +8  ...  定长头剩余（原样保存，长度 = header_len - 12）
  +40 u32  字形数 G
  +44 u32  字形记录起点[G]     # 紧密数组：rec_base + k*16
      16B 字形记录[G]
```

字形指针数组写成**紧密布局**是刻意的：UTMT 按指针逐个 seek，数组本身错乱
（data1.win/data2.win 都是这样）只能靠这份规范把它拉直。字形记录整体按
16 字节保存（14 字节字段 + 2 字节字距计数），没动过的字形逐字节一致。

### 实测补上的两条判据

**1. 原文件 FONT 尾部的 512 字节是残留数据，不是结构**

data.win 的 FONT span 149,296，模型算出来 148,784 —— 差 512。那 512 字节
不是零，是 `00 00 01 00 02 00 … 3f 00` 循环的 256 个 u16（一张残留的索引表），
位置在最后一条字形之后，没有任何指针指向它。模型显式丢弃它。

**2. chunk 数据末尾必须留 1 个字节的守卫**

`Reading out of chunk bounds at 00024B0C while reading chunk FONT` 的真因：
UTMT 读完「列表型」chunk 的最后一个结构后，还会再读 **1 个字节**。
GM 自己写出的文件里每个 chunk 末尾都带着补位（data.win 的 FONT 后面就有
那 512 字节），所以这一步从来不会踩到边界；我们的排布是紧的，就会踩。

实测（FONT 后面挂一个不认识的 TEST chunk 当探针）：

| FONT 末尾余量 | 结果 |
|---|---|
| 0 字节 | 卡在 FONT 末尾 |
| 1 字节 | 读完 FONT，走到 TEST |
| 2 / 3 / 4 / 8 字节 | 同上 |

所以 `Chunk.guard` 默认 1（STRG/FONT 都带），原样搬运的 chunk 保持 0。

### 验收（`selftest/test_strg_font_rebuild.py`，49 项全过）

只取 GEN8 + 重建后的 FONT + STRG 生成新文件，三个样本都做到：

| 样本 | STRG 条数 | FONT 项数 | UTMT |
|---|---|---|---|
| data.win | 63,977 | 20 | 两个块都读完，报错停在「缺后续 chunk」 |
| data1.win | 31,148 | 11 | 同上 |
| data2.win | 13,156 | 12 | 同上（原文件 6 个坏 FONT 指针、29 条字符串在 chunk 外，全部归位）|

报错是 `Found pointer targets that were never read` —— 指向 TPAG/TXTR 等
还没建模的 chunk，属于**预期的残缺**，不是 FONT/STRG 的问题。

### 下一步

按同样的套路补其余 chunk 的模型。顺序按「谁含绝对偏移、谁挡路」排：
TPAG（纹理指针）→ TXTR（128 对齐的 blob）→ AUDO → SPRT/BGND/OBJ/ROOM →
CODE/VARI/FUNC。全部建模后，写出阶段就能自由排布，data2.win 的
「TXTR 差 427 字节」也会像 STRG 那样自然消失。

### 第二步：TXTR / AUDO / TPAG（已通过）

这三个是「被指针指着的数据块」，规则都在 UTMT 的源码里查到了：

| chunk | 形态 | 判据 |
|---|---|---|
| TPAG | 22 字节定长记录 + 指针表 | 记录里只有 u16 索引指向 TXTR 第几页，**没有绝对偏移** |
| TXTR | 条目 + 纹理数据 | 每条数据从 128 的倍数开始；读完最后一条后按 4 对齐 |
| AUDO | 条目（u32 长度 + 数据） | 条目间补到 4；最后一条不补；收尾按 4 对齐 |

TPAG 的条目本身没有偏移，但**它的指针表是绝对偏移** —— 这一条把「原字节照搬」
这条路彻底堵死了：搬一个 chunk 就会让它的指针表失效。所以引入通用形态
`OpaqueListChunk`（指针表重算 + 条目原样），TPAG 直接用它。

TXTR 的条目同样只用「原字节 + 一个符号化槽位」处理：模型不猜 Scaled /
GeneratedMips 那些字段的含义，只把纹理数据指针提出来，所以不会猜错。

长度可变带来的直接结果：

| 样本 | TXTR 原 span | 重建后 | 差 |
|---|---|---|---|
| data.win | 12,763,872 | 12,764,660 | +788 |
| data1.win | 11,407,072 | 12,620,140 | +1,213,068 |
| data2.win | 5,264,277 | 5,264,440 | +163 |

data2.win 就是那个「原位差 427 字节、怎么摆都放不下」的文件 —— 现在只是让 chunk
长 163 字节，7 条纹理全部落在 128 边界上，UTMT 读过去了。data1.win 涨得多是因为
它的纹理数据本来就互相重叠（16 条加起来 12,618,816 字节，比原 span 还大），
重建时每条各存一份，宁可多占也不共享重叠区间。

### 验收（`selftest/test_blob_chunks_rebuild.py`，33 项全过）

GEN8 + FONT + TPAG + STRG + TXTR + AUDO 拼一份残缺文件，三个样本都做到：

* TXTR 每条数据从 128 边界开始、按条目顺序排、不越 chunk；
* AUDO 每条 4 对齐、长度字段自洽；
* UTMT 读完 TXTR/AUDO 才报错，报错是 `Found pointer targets that were never read`
  （指向还没建模的 SPRT/OBJ/ROOM 等）。

### 下一步

`SPRT / BGND / OBJ / ROOM / SCPT / TMLN / SOND / CODE / VARI / FUNC / GLOB / DAFL`
—— 这些 chunk 的条目里都带字符串指针（name 字段），可以全部用
`OpaqueListChunk` + `Record.refs` 处理：只把「指向 STRG 正文的槽位」标出来，
其余字节不动。这样就不需要把每个 chunk 的全部字段语义都吃透，
也能安全重排。

### 第三步：条目型 chunk 的通用模型（进行中）

`VariableListChunk` + `Record`：条目长度不等，所以不能按步长切 —— 指针表给了
每条的起点，相邻指针之差就是它的物理长度。条目字节整体保留，只把**发现出来的
指针槽位**换成符号引用。

槽位不是猜的：对每个候选偏移，统计所有条目在这一位上的 u32 —— 非零的必须
**全部**落在同一类目标上（STRG 的字符串正文，或某个 chunk 的条目地址），
零表示没有该字段。真实布局里 name 这类字段在所有条目上都成立；偶然让两三百条
同时撞上同一类地址的概率可以忽略。

单块隔离验收（`selftest/test_chunk_matrix.py`，拼 GEN8 + STRG + 目标块，看
UTMT 能不能**读完**这一块）：

| chunk | data.win | data1.win | data2.win |
|---|---|---|---|
| SOND / BGND / PATH / AGRP / FONT / OBJT / ROOM / CODE | ok | ok | ok |
| SHDR / TMLN / DAFL | （空块） | （空块） | ok |
| GLOB | 空 | 空 | ok（换成原字节，因为没有指针） |
| SPRT | **炸** | **炸** | ok |
| SCPT | **炸** | **炸** | **炸** |
| FUNC / VARI | **未建模** | **未建模** | **未建模** |

剩下的三块各自有明确原因，不是「不知道哪错」：

1. **SPRT**：条目结构是 `name + 尺寸 + margin + 掩码列表 + 帧列表`，帧列表的
   偏移随掩码数量变化，固定偏移扫不到 —— 需要真模型（GMS1.4 的
   `UndertaleSprite`），不能靠槽位发现。
2. **SCPT**：单块时能过，一旦把 CODE 一起带进来就报 `Reading out of bounds.`
   —— 说明 **bytecode 里含绝对地址**（跳转目标），UTMT 读代码时会去解析它们。
   这块要做的是 UTMT 那样的 code-address 重写（记录每条 code 的地址，改写
   指令里的地址操作数），不是简单搬运。
3. **FUNC / VARI**：不是「指针表 + 条目」，是**内联列表**（`u32 count` 之后直接
   排记录）。实测：FUNC 记录 12 字节（`name, 次数, code 指针`，字符串槽位每 3 个
   u32 出现一次），VARI 记录 20 字节（每 5 个 u32 一个字符串槽位）。
   FUNC 后面还跟着第二个列表（CodeLocals）。VARI 头三个 u32 是
   `count, count, 40` 之类的头部，需要按内联列表建模。

### 第四步：FUNC / VARI / SCPT / SPRT / CODE（已通过）

**FUNC / VARI 是内联列表**（`u32 数量` 之后直接排记录，不是指针表）：

* FUNC：12 字节/条 = `Name, Occurrences, FirstAddress`，后面再跟一个
  CodeLocals 列表（`u32 局部变量数, Name, 每个 (Index, Name)`）；
* VARI：头 12 字节是 `VarCount1, VarCount2, MaxLocalVarCount`，之后
  20 字节/条 = `Name, InstanceType, VarID, Occurrences, FirstAddress`。
  条数按长度反推（`(size-12)/20`），不用 VarCount1。

**CODE 里有 5 MB 没有被条目录用到的字节**。data.win 的 6,272 条代码条目
都挤在块末尾 125 KB，前面 5 MB 是活的 bytecode，而 FUNC/VARI 的
`FirstAddress` 正指在那片区域里 —— 条目级重排会改变这些内部偏移。
所以 CODE 换成 `RebasedListChunk`：整块逐字节搬运，只把指针表按新起点
重算。`FirstAddress` 记成「CODE 块 + 块内偏移」，搬家后跟着走。
这条也顺带修好了 SCPT（它原来一和 CODE 一起出现就 `Reading out of bounds`）。

**SPRT 需要真模型**（帧数组偏移随掩码数量变化，槽位扫不到）：

```
+0  Name      +4 Width,Height   +12 Margin×4   +28 Transparent/Smooth/Preload
+40 BBoxMode,SepMasks   +48 OriginX,Y   +56 帧数N   +60 N×TPAG指针
    掩码数M + M段位图（长度由宽高算出） + 补到 4
```
掩码位图的长度是算出来的（`((宽+7)//8)*高`），文件里没有长度前缀。

**另外发现一条全局约束**：每个 chunk 的数据长度必须是 4 的倍数，否则下一个
chunk 起点就不在 4 字节边界上，SPRT 这类带 `Align(4)` 的块会直接报
`Invalid alignment padding`。所以收尾规则统一成「先补到 4 的倍数，再加
4 个零字节」（既保证对齐，又给列表块那个「多读 1 字节」留余量）。
原样搬运的块不加守卫，长度保持原值。

单块矩阵：**38/38 全过**（16 种块 × 3 个样本）。

### 第五步：OBJT（已通过）

OBJT 的条目里套着**三层指针表**，这是第一次遇到「指针表里还是指针表」：

```
Events        = u32 13 + 13 个指针          # 13 种事件类型
每种事件      = u32 n  + n 个指针 -> Event
Event         = u32 Subtype + Actions 指针表
Actions       = u32 m  + m 个指针 -> EventAction
EventAction   = 56 字节，+28 ActionName（字符串正文），+32 代码索引
```

用 `PtrList` 表示任意层数的指针表，`Event`/`EventAction` 作为叶子。
Sprite/Parent/Mask/CodeId 都是**下标**不是地址，不用重定位；真正要重算的
只有名字和每层指针表本身。

实测（data.win）：1,709 个物件、22,217 个事件类型表、5,840 个动作，
顶层事件表每条都是 13 项 —— 和 GMS 1.4 的事件类型数一致。

### 第六步：ROOM（已通过，且做到逐字节可证）

ROOM 是整份文件里最大的一块（data.win 4,506,968 字节，占 7%），也是唯一
「全靠指针寻址」的块：条目、四张表、每条表项各有一个绝对地址，UTMT 顺着指针
跳过去读定长字段。

```
指针表   = u32 n + n 个指针 -> Room
Room     = 88 字节头（名字、caption、宽高、Flags …
           +40/+44/+48/+52 = Backgrounds/Views/GameObjects/Tiles 四个指针）
表       = u32 c + c 个指针 -> 表项
表项     = 定长：背景 40 / 视图 56 / 物件 36 或 40 / 图块 48
```

**表项长度不猜。** 两路证据互证，任一路说不通就抛：

1. 指针数组里相邻表项的地址差（实测间距的众数）；
2. UTMT 的版本判据：`UndertaleRoom.GameObject` 只在 `BytecodeVersion >= 16`
   时多读一个 PreCreateCode，所以物件表 36 / 40 两种，其余三张表各版本一致。

三个样本全部吻合：data.win(bytecode 16) → 物件 40；data1.win(**bytecode 15**)
→ 物件 **36**；data2.win(16) → 40。

| 样本 | 背景 | 视图 | 物件 | 图块 | 模型算出 | 原 chunk | 差 |
|---|---|---|---|---|---|---|---|
| data.win | 40 | 56 | **40** | 48 | 4,506,968 | 4,506,968 | **0** |
| data1.win | 40 | 56 | **36** | 48 | 4,447,900 | 4,447,900 | **0** |

也就是说 ROOM 里**没有补位字节**：指针表、条目头、四张表、每条表项首尾相接，
正好铺满整个 chunk。`_check_tiling` 就是拿这个当判据 —— 少算或多算一个字节都
会在某一段断开，并指名是哪一间房的哪张表。

**「UTMT 能加载」不等于写对了。** 这一步踩到的坑值得记下来：改之前表项长度
是按「相邻地址差」猜的，单条目的表猜不出间距，退化成一个 4 字节记录。data.win
有 31 个房间的物件表只有一条、5 个房间的图块表只有一条 —— 36 个实例的属性被
截断，UTMT 依然 **exit 0**。原因就是上面说的：它顺指针跳到每个表项起点读定长
字段，中间缺多少字节它不看。

所以 ROOM 的验收改成三层（`selftest/test_room_roundtrip.py`，16 条断言）：

1. **长度**：重建出的 ROOM 数据长度 == 原 chunk；
2. **指针**：每个指针槽位在新文件里必须指向**同一个模型对象**（按对象身份比，
   不按地址比），且数值 == 该对象在新布局里的地址（字符串 +4）；
3. **字节**：两边指针槽位都涂成同一标记后，其余字节必须**完全相同**。

实测：

| 样本 | 指针槽位 | 同对象 | 数值正确 | 涂掉指针后差异 |
|---|---|---|---|---|
| data.win | 90,469 | 90,469 | 90,469 | **0 字节** |
| data1.win | 90,425 | 90,425 | 90,425 | **0 字节** |

第 3 层之所以成立，是因为 ROOM 表项里**没有指针**（UTMT 的
Background/View/GameObject/Tile 序列化里没有 `ReadUndertaleObjectPointer`），
其余字节原样搬运就是对的。顺带做的审计：涂掉指针槽位后，仍有 156,033 个字的
值落在文件地址区间里，按字段归类是 `Tiles[+32]` 实例 ID 68,815、`Tiles[+28]`
深度 68,056、`GameObjects[+12]` 实例 ID 13,926、`Tiles[+0]` X 坐标 3,212、
`GameObjects[+8]` 物件下标 1,087（下标 ≥1500 才落进区间）—— 全是数字，没有
漏掉的指针。

**小步快跑**：同一份测试还会只带 `GEN8 + STRG + TPAG + OBJT + ROOM` 组一个
最小文件（8.3 MB）单独喂 UTMT，exit 0，四个块全部读到。STRG 必须带上，它是
OBJT/ROOM 名字槽位的唯一来源。

**交叉引用是下标，不是地址。** 房间里的物件/背景/代码引用（`GameObjects[+8]`
ObjectDefinition、`Tiles[+8]` 与 `Backgrounds[+8]` BackgroundIndex、
`GameObjects[+16]` CreationCode、`+36` PreCreateCode）全是目标块里的**序号**，
所以不用重定位；但序号必须落在范围里，否则 UTMT 取不到资源。实测：

| 字段 | 目标 | data.win | data1.win |
|---|---|---|---|
| ObjectDefinition | OBJT 0..1708 / 0..1702 | 全在范围 | 全在范围 |
| CreationCode | CODE 0..6271 / 0..6107 | 全在范围 | 全在范围 |
| PreCreateCode | CODE（bytecode≥16 才有） | 全 -1 | 表项 36 字节，无此字段 |
| BackgroundIndex（图块/背景） | BGND 0..248 / 0..196 | 全在范围 | 全在范围 |

房间**不引用 TPAG** —— TPAG 是精灵的纹理页序号，只有 SPRT 引用它。
`-1`（0xFFFFFFFF）表示「没有」。

### 第七步：GMS2 图层 + 零告警 + 接进 CLI（已通过）

**1. ROOM 的 GMS2 布局。** data2.win 是 GMS2，房间头多一个 Layers 指针（+88），
四张老表全空，内容都在图层里：

```
GMS1 头 88 字节  → 四张表
GMS2 头 92 字节  → 四张表（空） + Layers
Layer = 36 字节头（名字指针 / LayerId / 类型 / 深度 / 偏移速度 / 可见）
        + 按类型：0 Path 无数据 / 1 Background 40 字节 / 2 Instances 数量+实例ID
                  3 Assets 两张指针表（Tile 48B、SpriteInstance 44B 带名字）
                  4 Tiles 背景索引+网格 / 6 Effect（未建模，直接拒绝）
```

走哪条不看文件名，看房间 Flags 的 `IsGMS2`（0x20000）。实测 data2：145 间房、
1,008 个图层，模型长度 1,819,052 = chunk 1,819,064 − 12 字节零尾，**差 0**。

**2. chunk 长度必须等于 UTMT 读到的字节数。** UTMT 的判据是

```
读到的长度 != 头部写的长度  →  警告 "…(N padding?)"  →  跳过差值
```

早先给所有块补 4 字节防越界，补位写进了长度却没人读，于是**每块一条告警**。
按块实测后：只有 FONT 真的多读 4 字节（`FontTable.guard = 4`），其余块一律不补；
AUDO 末尾也不补（原文件 AUDO 长度 35,060,359 本身是奇数）；不给 chunk 强加 4 对齐
（data2 的 TPAG 是 76,470 字节，下一块本来就落在 2 mod 4 上）。
结果：data.win / data1.win 都是 **0 告警**。

**3. 定长记录不再吞块尾补位。** `VariableListChunk` 原来把「最后一条」切到 chunk
末尾，块尾那几个垫字节就被算进了最后一条（data2 的 SCPT 多 12、BGND 多 4）。
现在先看指针间距：全相等就按这个长度切每一条，块尾多出来的字节记为 `tail`
并丢掉；间距不等才退回变长切法。SCPT 的告警因此消失。

**4. 接进 CLI。** 新增 `--rebuild`（全量重建，长度允许变化）；`--repair` 改成
**先走全量重建**，只有遇到还没建模的布局才退回「等长原位补丁」并说明原因。
GUI 的修复按钮走的是 `--repair`，所以它现在就默认用新架构。
顺手修掉一个老问题：`end` 事件会被写两条（子命令一条 + 入口 `finish` 一条），
现在 `emit` 负责去重。

### 整份重建的进度（`selftest/test_full_rebuild.py`）

| 样本 | 原字节 | 重建后 | UTMT | 告警 |
|---|---|---|---|---|
| data.win | 64,130,806 | 63,042,302 | **24/24 块，exit 0** | **0** |
| data1.win | 68,851,830 | 58,477,298 | **22/22 块，exit 0** | **0** |
| data2.win | 17,717,336 | 17,459,404 | **25/25 块，exit 0** | 5（继承源文件） |

data2 剩下的 5 条告警都在**原字节搬运**的小块里（LANG / GLOB / AGRP / SHDR /
EXTN / TMLN / DAFL / EMBI），我们的输出长度与源文件一致，所以那是源文件自带的
补位字节在报 —— 要把它们清掉，得把这几块也建成真模型（知道结构长度），
而不是搬字节。这是下一步。

### 第八步：零告警收尾 + 幂等性（进行中）

**空表就是 4 字节。** EXTN / SHDR / TMLN / DAFL 这类 `UndertaleListChunk` 的
结构是「count + 指针表 + 条目」，count 为 0 时结构长度就是 4 字节；源文件里
多出来的那几字节是补位，搬过去只会换来一条告警。现在 count == 0 时按结构写
4 字节，并在报告里注明去掉了多少补位。data2 的告警 5 → 3。

**幂等性成立。** 把重建结果再重建一遍，三个样本的产物**字节完全相同**：

| 样本 | 第一次 | 第二次 | 相同 |
|---|---|---|---|
| data.win | 63,042,302 | 63,042,302 | 是 |
| data1.win | 58,477,298 | 58,477,298 | 是 |
| data2.win | 17,459,404 | 17,459,404 | 是 |

这条等于证明「新布局是不动点」：模型没丢信息、布局没漂移，整份文件确实可以
自由重排，而不是碰巧排对了一次。已加进 `test_full_rebuild.py`。

**GMS2 的 ROOM 也做了逐字节证。** `test_room_roundtrip.py` 现在跑三个样本：
data2 的 37,215 处指针槽位全部指向同一个对象、数值正确，涂掉指针后**0 字节差异**。

### 还没做（下一轮）

1. **data2 剩 3 条告警**：BGND（+4）、LANG/AGRP/GLOB（+8）、TMLN（+4）。
   TMLN 那条要注意：UTMT 在 GMS2 下**一个字节都不读** TMLN，只有 0 长度的块
   才不告警 —— 写 0 长度是否真能被 GM 接受，得先确认再动。
2. ~~PATH 与 OPTN 里还有没建模的指针~~ **查过了，是我看错了，两块都没有指针**：

   * PATH 条目 = 名字(4) + smooth(4) + closed(4) + precision(4) + 点数(4)
     + 点数 × (X, Y, Speed)。实测 data.win 一条 56 字节 = 20 + 3×12，
     名字指向 STRG 正文（自动发现的槽位，已重定位），其余全是数字。
     之前数出来的 90 个「地址型字」= 45 个指针表条目 + 45 个名字指针，
     两样本来就重定位了。
   * OPTN 的 +8 不是指针，是 `UndertaleOptions.Info`（**u64** 标志位）的低 32 位。
     OPTN 的 NewFormat 头 60 字节里只有 BackImage/FrontImage/LoadImage 三个
     指针（实测都是 0），后面是常量表（名字/值两个字符串指针，已当字符串槽位处理）。

   实测比对（原 vs 重建）：data.win / data1.win / data2.win 的 PATH 每条
   （名字、smooth、closed、precision、点数、坐标）与 OPTN（u64 标志位、
   三个图片指针、常量表）**逐项相同**。



## V0.16（已并入 V0.17，本节留作来源）

**全类型自研解析，但原核心不删。**

> 这一节原本是 0.16 的计划；实际执行时版本线改成「0.15E 发布为 0.16，
> 两条通道在 0.17 做」，所以下面这些条目现在归 **V0.17**（见文件开头）。
> 保留原文是因为它记录了当时的取舍：自研是可选路径，不是替代品。

* 每种引擎/格式都补一份自研解析器（现在是 `gamemaker_*` 这一套已经自研，
  Unity / Unreal / Electron / Godot / RPG Maker / Clickteam 仍然靠
  `tools/` 下的第三方 CLI）。
* **第三方核心保留**，不要删掉 —— 自研是可选路径，不是替代品。
* 切换方式：GUI 的 **CheckBox7**。
  * 不勾选（默认）：走第三方 CLI，即现在的行为。
  * 勾选：走自研解析器。
* 自研路径同样要满足全局进度条契约（见 `GUI-API.md` §5）：
  `total` 永不回退、`done` 只增不减、收工 `done == total`。
* 参数侧同样需要一个开关（建议 `--self` / `--no-self`），
  语义与 CheckBox7 一致，默认第三方。

## V0.15（进行中）

**重建模块**：读原 data.win → 重新搭一份自洽结构 → 填回原位置，
目标是原版 UTMT 也能解析。核心实现见 `me/formats/gamemaker_rebuild.py`。

### 两个样本的实测状态

样本：`C:\Users\Administrator\Downloads\data.win`（64,130,806 字节，bytecode 16）
和 `data1.win`（68,851,830 字节，bytecode 15）。UTMT 对两者都报同一个错：

```
Reading out of chunk bounds at ... while reading object UndertaleFont
while reading item 2 of 20 in a list of UndertaleFont in chunk FONT
```

* **FONT 指针表损坏【已修好】**：20 项里有 6 项（下标 1/2/4/5/8/9）、
  11 项里有 7 项指向 chunk 之外 —— 指到了 STRG/AUDO 的字节范围里去。
* 修完之后 UTMT **过了 FONT**，改在下一处报错 —— 说明重建确实生效：
  * `data.win`：`Padding error in STRG at 00F491BE`
  * `data1.win`：`Padding error! at 040137EF`（卡在 TXTR）

### FONT 的模型（已由 UTMT 反证成立）

```
条目 = 定长头 H 字节 + G 个 u32 字形指针 + G 个 16 字节字形记录
G   = u32(条目 + 40)
size = H + G * (4 + 16)
```

* H **不硬编码**，从样本量出来（相邻可信指针之差减 20G，取众数）：
  Undertale、data.win、data1.win 都量到 **44**。
* 条目数据紧跟指针表起排，所以从表尾按 size 走一遍就能得到每一项的起点。
  **走出来的起点必须与所有未损坏的指针逐一吻合**，否则模型不成立、拒绝重建。
* 反证：原始文件里 UTMT 自己报 `Reading misaligned at 001EAD30`，
  而 0x1EAD30 正是走出来第 2 项的起点 —— UTMT 算的条目长度与我们的公式一致。

### 已完成

* `me/formats/gamemaker_rebuild.py`：诊断 + FONT 重建 + 写指针前自检。
  `--pointers-only`（只改坏指针槽，用来分辨问题在指针表还是条目内容）、
  `--deep`（全文件反查有没有别处指进 FONT）。
* `selftest/test_gamemaker_rebuild.py`：合成样本 21 项断言全过，
  含「改坏指针 → 重建 → 指针回到真实起点、长度不变、条目字节未动」。
* CLI `--fix`：先重建再提取（`Runner.run(fix=True)` → `Context.fix` →
  GameMaker 引擎重建 `data.win` 到 `work/` 再解析）。GUI 的
  「修复/重建 Data.win」勾选框接这个参数。
* 全量自测 13/13 通过（含 68.4s 的真实样本回归）。
* 版本线：`0.15 Beta 00002`。
* **STRG 已通过**：data.win 的尾部补位（278,466 字节）清零后，UTMT 只警告一句
  `File specified length 3135783, but read only 2857384 (278399 padding?)`，
  不再报错。判据是「读完最后一条字符串后，chunk 剩下的整段都当补位，
  必须全是零」。
* **TXTR 已重排**：按 chunk 分组，组内按地址顺序对齐重排、补位清零，
  装不下时把末尾几条搬到「同 chunk 内整段全零」的空白区，只改 TXTR 条目里的
  blob 指针（blob 不被别的东西引用）。data.win 32 条原地排下（补位 41 字节）。

### 下一步（未完成）

1. **TXTR 还卡着**（STRG 已解决）。已查清的事实：
   * UTMT 报错位置 = 「第一个非零补位字节 + 1」。
   * data.win 现在卡在 `0x00F8D30C`（= 第一条纹理数据的起点）；把那个补位字节
     清零后只往后挪了 **1 字节**（`0x00F8D30D`）—— 它在**继续往后扫**，
     而后面就是 PNG 签名。强烈暗示读完条目后是**顺序**往下读纹理数据。
   * data1.win 尾部紧贴无缝，重排需要的 19 字节补位没地方放；
     而且它的条目顺序与地址顺序不一致（e11 在 57,387,430，e2 在 64,419,100）。
   * `data.win` 卡在 STRG `0x00F491BE`；`data1.win` 卡在 TXTR `0x040137EF`。
   * 两个位置都等于「某条记录的边界 + 1」，且都紧跟在一个 4 字节**未对齐**的
     记录尾之后：`data1.win` 是 TXTR 条目 2 的 blob（64,419,100 + 2,769,618
     = 67,188,718，模 4 = 2），紧接着就是 TXTR 条目 15 的 blob。
   * 文件尾部（落在 AUDO 的字节范围里）是「PNG blob 区 + 字符串区」，
     由某个外部工具重新排布过：blob 之间还有 103 字节这类空隙，
     `STRG` 有 14,002 条字符串的正文也在这个尾区。
   * **TXTR 条目里的 `+0` 不是长度**（data.win 全是 1，data1.win 与 Undertale 全是 0），
     而是「scaled」之类的标志；blob 是**裸 PNG，没有长度前缀**，
     长度只能用 PNG 结构自己算（本仓的 `png_len` 对三个文件的每一条都算得出来）。
   * **不能搬字符串**：字符串被全文件各 chunk 以绝对偏移引用，重排等于要把
     OBJT/ROOM/SPRT/CODE 等全部建成完整模型。所以 STRG 只能就地修。
2. 修好之后重跑 UTMT，直到它把文件读进去。
3. 重新打包（`build_exe.py --dest "Compiled\Ver 0.15" --zip`）。



---

## V0.15 里程碑（0.15 Beta 00004）

**`Downloads\data.win` 已经修好：原版 UTMT 载入成功。**

| | 原始文件 | 重建后 |
|---|---|---|
| UTMT 退出码 | **1** | **0** |
| 结果 | `Reading out of chunk bounds ... while reading UndertaleFont` | 只留 4 条 WARNING，全部 chunk 读完 |

四块修复，每一块都是「修好之后 UTMT 的失败点往后推一截」反证出来的：

1. **FONT 指针表**：20 项里 6 项指向 chunk 之外。从表尾按
   `size = 44 + 20×字形数` 走出真实起点，重建指针表。
2. **STRG 尾部补位**：读完最后一条字符串后，chunk 剩下的**整段**都算补位且必须是零
   （实测 278,466 字节）。清零后 UTMT 只警告
   `File specified length 3135783, but read only 2857384 (278399 padding?)`。
3. **TXTR 纹理数据**：按 **128 字节**对齐（从 Undertale 那份量出来：92 / 8 / 52
   字节的三处空隙正好把上一条补到 128 的倍数）。原地排不下就把末尾几条搬到
   chunk 内部的空白区（STRG 尾部清零后是现成的 278 KB），只改条目的 blob 指针。
   * 反例：把纹理数据搬进 AUDO 覆盖的空白区会让 UTMT 在
     `UndertaleEmbeddedTexture+TexData` 报 `count` 越界 —— 所以要优先用
     chunk 内部 / STRG 尾部的窗口，成段空白区只作后备。
4. **AUDO 音频数据**：条目 = `u32 长度 + 数据`，正常条目间留 2 字节（= 补到 4 字节）。
   两个样本被工具整体挪偏 1 字节（首指针是奇数），出现 1 / 3 字节间隔。
   重排到 4 字节对齐后通过。

### 还没解决

`Downloads\data1.win`：TXTR 的 16 条纹理分散在两个互不相邻的区域
（TXTR 段内 12–23M、AUDO 段内 57–68M），尾部紧贴无缝，128 字节对齐需要多出
约 600 字节；搬到后备的成段空白区之后 UTMT 报
`Read underflow at 04033C74 while reading chunk TXTR`，并刷了一串
`Reading misaligned` —— 说明它对纹理数据的读取顺序还有约束
（条目顺序 vs 地址顺序不一致：e11 在 57,387,430，e2 在 64,419,100）。
下一步：把 data1 的纹理按**条目顺序**连续重排，而不是按地址顺序。

---

## V0.16 定稿方向（用户指示，已落到 V0.17）

**每种引擎都提供「自研」与「第三方工具」两条通道，CTF 除外**（Clickteam 只走自研）。

> 已在 V0.17 实现，判定见 `me/channel.py`，状态表见文件开头。
> 下面这段是当时的原始指示，留作对照。

* `tools/utmt/` 已在 V0.15 恢复（128.7 MB / 362 文件），配套接线一并恢复：
  `me/tools/paths.py` 的 REGISTRY、`tools/setup_tools.py` 的下载项、
  `build_exe.py` / `selftest/test_frozen.py` 的 EXPECT_TOOLS、`THIRD_PARTY.md` 的 GPL 章节。
* 切换：GUI **CheckBox7**，默认走第三方，勾选后走自研；参数侧保持一致语义。
* 自研通道同样要满足全局进度条契约（`GUI-API.md` §5）。
* CTF 不接第三方 —— `me/formats/ctf25/` 是自研实现，不引入 CTFAK（AGPL-3.0）。

---

## data1.win 的 TXTR：当前诊断（0.15 Beta 00005）

把「原始缺陷」和「我重排引入的问题」分开了 —— 用 `--only FONT` 只修 FONT：

* 原始纹理布局下，UTMT 的第一处 TXTR 报错就是
  `Padding error! at 040137EF`（= 67,188,719），正是 **e2 的末尾 + 1**：
  e2 在 64,419,100、长 2,769,618，尾 67,188,718，而 67,188,718 处紧接着就是
  **e15 的 PNG 签名** —— 128 字节对齐缺口完全被下一条数据占着。
* 16 条纹理里 e0 / e1 在 TXTR 段内（12,374,144 / 12,378,240，都 128 对齐），
  其余 14 条散在 AUDO 覆盖的尾部，而且**是一条紧贴的链**：
  e11→e8→e10→e3→e4→e6→e2→e15→e14→e12 每条都正好结束在下一条的起点，
  链尾 67,993,697 就是尾部字符串区的开头 —— **一点富余都没有**。
* 按 128 对齐重排，这条链需要多出 **1,088 字节**补位。可用的落脚点：
  STRG 尾部只有 82 字节；后备的成段空白区（33,394,241 等）搬进去之后
  UTMT 会先刷一串 `Reading misaligned`，最后
  `Read underflow at 04033C74 while reading chunk TXTR`。
* 结论：**对齐补位总得落在某个地方**（对齐起点或对齐末尾，总量一样）。
  下一轮的两个方向：
  1. 把尾部那条链整体搬进 TXTR 段内的空隙（19,962,938–20,643,584 约 680 KB、
     21,318,795–22,970,752 约 1.65 MB），先确认这些空隙是否全零；
  2. 查清 `Read underflow` 到底是「读取越过 chunk 边界」还是
     「跳转次数太多把 UTMT 的剩余长度记账搞坏」—— 原始文件只跳 1 次就报
     padding error，我搬 10 条就变成 underflow。

---

## V0.15 验收通过（0.15 Beta 00050）

| 样本 | 原始文件 | 重建后 |
|---|---|---|
| `Downloads\data.win` | UTMT 退出码 **1**（Reading out of chunk bounds @FONT） | 退出码 **0** |
| `Downloads\data1.win` | UTMT 退出码 **1**（同样卡 FONT） | 退出码 **0** |

两个文件长度都一字节未变（64,130,806 / 68,851,830）。

### 最后一块拼图：末条纹理必须在 chunk 末尾之前

`data1.win` 一直报 `Read underflow at 04033C74`，位置恒定为 e15 末尾 + 2，
换搬家策略也不变。真因是 UTMT **读完最后一条纹理之后要算「本 chunk 还剩多少字节」**：

    remain = chunkEnd - Position

data1 的纹理都在 57–68M，而 TXTR 段末尾只有 23,780,968 → remain 是个大负数
（-43,539,980，正好等于它前一条 WARNING 里的读数），于是抛 underflow。

data.win 能过就是因为它搬家的落脚点（STRG 尾部 16,028,160）在 chunk 末尾**之前**。

修法：**按条目顺序排在最后的那条纹理，如果落在 chunk 末尾之后，就把它搬回段内**
（搬进 TXTR 段内的空白区）。data1 的 e15 搬回 23,033,xxx 之后，两个样本都通过。
 
### 完整修复清单

1. **FONT 指针表**：按 `44 + 20×字形数` 从表尾走出真实起点，重建指针表。
2. **STRG 尾部补位**：读完最后一条字符串后 chunk 剩余整段必须全零（实测 278,466 字节）。
3. **TXTR 纹理数据**：128 字节对齐；原地排不下就搬进 chunk 段内的空白区；
   **末条必须在 chunk 末尾之前**。
4. **AUDO 音频数据**：4 字节对齐（条目间正常留 2 字节）。

---

## 第三个样本 data2.win：两条判据要改

`data2.win`（17,717,336 字节，bytecode 16，24 个 chunk）把上面第 2、3 条都推翻了。
这次是读 UTMT 的源码定下来的，不再靠反推。

### STRG 的补位不是「走到 chunk 末尾」，是「走到 128 的倍数」

`UndertaleChunkSTRG.UnserializeChunk`：

```csharp
while (reader.AbsPosition % 0x80 != 0)
    if (reader.ReadByte() != 0)
        throw new IOException("Padding error in STRG");
```

- 起点 = **按指针表顺序最后读的那条**的末尾（不是段内最大地址）；
- 终点 = 从那往后**下一个 128 的倍数**，不是 chunk 末尾。

data.win 的尾部之所以「整段必须全零」，只是因为那段里本来没有要读的东西，
清零恰好让它走到 128 边界。真正要保证的是：

    align128(最后一条字符串的末尾) <= chunk 末尾

data2.win：最后一条停在 5,961,135 → 128 边界 5,961,216，而 chunk 末尾
5,961,176，**差 40 字节**。清零解决不了，只能挪字符串数据（挪了要连带改
所有指向它的绝对地址，那是另一个量级的改动）。

### TXTR 的判据：不是「必须对齐」，是「下一条不能早于 128 边界」

`UndertaleEmbeddedTexture.UnserializeBlob`：

```csharp
while (reader.AbsPosition % 0x80 != 0)
    if (reader.ReadByte() != 0) throw new IOException("Padding error!");
reader.ReadUndertaleObject(_textureData);
```

补位只会扫到 128 边界，之后 `ReadUndertaleObject` 再按登记地址对齐（发一条
`Reading misaligned`）。所以真实约束是

    align128(上一条的末尾) <= 下一条的起点

数据可以留空隙，空隙里的字节甚至不必是零；但**下一条不能压在补位区里**。

### data2.win 的 TXTR 放不下（可证）

| 项 | 数值 |
|---|---|
| TXTR chunk 数据长度 | 5,264,277 |
| 头部 + 指针表 + 条目 | 116 |
| 每条纹理对齐后的总量 | 5,264,544 |
| 第一块数据最小起点（相对 chunk 起点） | 160 |

需要 `160 + 5,264,544 = 5,264,704`，实际只有 `5,264,277` → **差 427 字节**。
文件里也没有 ≥64 KB 的成段空白区（最小的一块纹理 476,817 字节，放不下），
所以**长度不变的方案不存在**。要过 UTMT 只有两条路：

1. 允许改长度：重新分配所有 chunk 的绝对指针（全量重建）；
2. 允许动纹理：无损重编码或丢掉一部分元数据块，腾出 ≥427 字节。

这两条都没做，`--repair` 现在会如实报
`STRG: 补位需到 5,961,216, 超出 chunk 末尾 5,961,176 40 字节`。

### 顺带查清：data.win 少的那 1 条字符串

之前验收记的是「字符串 63,975 → 63,974，只少 1 条」，原因一直没查。现在知道了：

AUDO 每条都要补到 4 字节，连**最后一条**也补。而 data.win 的音频段
（29,071,375 起）正好紧贴指针表右边界，前面没有后退余地，补位只能往后长
1–3 字节 —— 那 3 字节正好落在 63,270,121 处的一条字符串的长度字段上，
把它清成 0，那条名字就空了。data2.win 同理（末尾紧跟 29 条字符串）。

试过让整段前移、让末条不补位，都不成立：前者被指针表挡住，后者只把 3 字节
减到 1 字节，还是压在那条字符串上。要彻底解决得把那条字符串搬到别处并改掉
所有指向它的绝对地址 —— 与 STRG 那条是同一件事。

现在这一步会明确报出来：
`AUDO: 末尾补位 63,270,121 -> 63,270,124 覆盖字符串记录 63,270,121, 该名字变空`

