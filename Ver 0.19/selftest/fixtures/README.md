# 测试夹具：真实 RPG Maker 加密归档

这三个文件是**真实游戏导出的加密归档**，不是我造的样本。
它们的作用是让 RGSSAD 解析器能被真正证伪 ——
自己造样本再自己解，只能证明「加解密自洽」，
无法发现「生成器和解析器共享同一个错误理解」这类问题。

| 文件 | 大小 | 头 8 字节 | 格式 |
|---|---|---|---|
| `Game.rgssad` | 504228 | `52 47 53 53 41 44 00 01` | RGSSAD（v1，RPG Maker XP） |
| `Game.rgss2a` | 373977 | `52 47 53 53 41 44 00 01` | RGSS2A（v1，RPG Maker VX） |
| `Game.rgss3a` | 636580 | `52 47 53 53 41 44 00 03` | RGSS3A（v3，RPG Maker VX Ace） |

## 来源与许可

取自 [uuksu/RPGMakerDecrypter](https://github.com/uuksu/RPGMakerDecrypter)
的测试目录 `RPGMakerDecrypter.Tests/EncryptedArchives/`。

该项目使用 **MIT 许可证**：

```
The MIT License (MIT)

Copyright (c) 2016 Mikko Uuksulainen

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## 这两个坑就是靠它们发现的

1. **魔数不是字面的 `RGSS3A`**。真实归档的魔数一律是 `"RGSSAD\0"`，
   第 8 字节才是版本号（`1` = v1，`3` = v3）。
   合成样本当时用的是错误的字面魔数，所以测试全绿但真实 `.rgss3a` 一个都认不出来。
2. **数据正文不参与表 key 的递推**。v1 表读完 `size` 后是直接 seek 跳过正文的，
   key 不动。合成样本的生成器和解析器都多推进了一次，同样测不出来。

（如果删掉这个目录，`test_rpgmaker.py` 会自动退回联网下载，见 `test_reference_vectors`。）
