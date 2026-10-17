# 路线图

## V0.16（已定，尚未开工）

**全类型自研解析，但原核心不删。**

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

## V0.16 定稿方向（用户指示）

**每种引擎都提供「自研」与「第三方工具」两条通道，CTF 除外**（Clickteam 只走自研）。

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

