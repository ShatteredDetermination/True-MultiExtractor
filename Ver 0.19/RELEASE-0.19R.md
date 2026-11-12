# V0.19R 发版清单（草稿，**尚未发版**）

这份文件是发版前的清单与草稿，不是发布说明。**现在还不能发**：按 ROADMAP 的验收口径，
必须由用户在 Fusion 2.5 里打开导出的 `.mfa` 并回报结果，那才算「可被 Fusion 编辑器打开」。

## 发版前提（缺一不可）

- [ ] 用户在 Fusion 2.5 里打开 `_out/fusion-test/min-object.mfa`（194 KB，3 个对象）——能打开 / 报错内容
- [ ] 用户在 Fusion 2.5 里打开 `_out/fusion-test/DustTale-demo.mfa`（17.6 MB，31 帧）——能打开 / 报错内容
- [ ] 帧列表里能看到 31 个来自原游戏的帧名
- [ ] 资源里能看到 32 张图 / 32 段音频 / 17 个字体

前两条任意一条失败，就要先修结构，不发版。

## 本版变化（拟）

| 项 | 内容 |
|---|---|
| MFA 写入器 | `mfa.py`：容器七段全解，两份参考样本**逐字节重建**；帧尾 chunk 列表与虚拟矩形读写 |
| flag=3 解密 | `crypto.py`：密钥 = 应用名（0x2224）+ 编辑器工程路径（0x222E），dtlg 逐帧 7 块全解 |
| 帧信息 | 帧头（宽高 / flags）、虚拟矩形（滚动区）、图层（名字 / 缩放）写进 mfa |
| 对象 | `--mfa-objects`：占位对象 + 实例位置（1,249 个对象 / 1,249 条实例）；对象属性头（标志 / 限定符 / 背景色 / 四字母标识，867 个全解）；String 正文（96 个里取到 61 段） |
| 清单 | 产物旁 `<名字>.mfa.json`：帧名 / 尺寸 / 矩形 / 图层 / 实例位置 / 对象表 / 各库条数 / 未重建项 |
| 第三方兜底 | `--mfa-tool`（env `ME_MFA_TOOL`）：退出码 0 + 产物 `MFU2` 才算成功 |

## 自测（当前数字）

| 文件 | 项数 |
|---|---|
| `selftest/test_mfa.py` | 216 项 / 13 段 |
| `selftest/test_mfa_build.py` | 113 项 / 6 段 |
| `selftest/test_mfa_objects.py` | 55 项 / 5 段 |
| `selftest/test_crypto.py` | 47 项 / 4 段 |
| `selftest/test_mfa_tool.py` | 24 项 / 6 段 |

（用户要求不跑全量，以上都是单跑的结果。）

## 发版步骤（用户确认后执行）

1. 删掉 `FUSION-TEST.md`（验收用的临时说明，不进发布包）；
2. `python bump.py --tag R` → `0.19R Beta 001NN`，同步 `version.ini`；
3. `python selftest/test_mfa.py`、`test_mfa_build.py`、`test_mfa_objects.py`、
   `test_crypto.py`、`test_mfa_tool.py` 单跑一遍，全绿；
4. `python make_dist.py --pack` 打 `UpdatePack.zip`，`python make_release.py --zip` 打源码包；
5. 写 `RELEASE-0.19R.md`（把本文件改成正式发布说明），更新 `version.json` 的
   `latest_stable` / `download_url` / `sha256`；
6. tag `v0.19R`，推送 main 与 tag，发 Release 资产。

## 已知缺口（发布说明里要写）

- **对象私有段**：动画表（Active 的帧/方向）还没对上（本轮试过按属性头里的段偏移去读，
  读出来的方向偏移是垃圾值，已回滚，不装懂）；Counter 外观、扩展对象的 name/magic/subType 同样待补。
  现在对象是**占位**：类型 / 名字 / 图标 / 标志 / 限定符 / 背景色是真的，动画与外观不是。
- **事件树**：一条事件都没重建，产物的帧里没有事件。
- **第二份样本**（`Undertale Last Breath Scrapped.dat`）：flag=3 密钥对不上，帧头/实例/正文都读不出，
  只有帧名、资源与对象表；转换时按应用窗口兜底并在报告里写明。
- **画面上看不到原游戏**：资源 + 摆位骨架，不是可玩的还原工程。
