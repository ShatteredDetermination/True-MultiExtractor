# Fusion 2.5 打开测试（V0.19 验收用）

这份文档只为一件事：请你在 **Fusion 2.5 编辑器**里打开下面这个文件，看它能不能打开、
打开后能看到什么。本机没有 Fusion，这一步只能由你验证。

## 被测文件

```
C:\Users\Administrator\Desktop\MultiExtractorDeveloping\_out\fusion-test\DustTale-demo.mfa
（17,618,543 B；同目录还有 DustTale-demo.mfa.json 清单）
```

来源：`dtlg-v1.1.0.exe`（144 MB）→ 自研通道导出，图像限 32 张、音频 32 段，
其余（字体 17 个、帧名 31 个、图标 1 张、工程信息）都是全量。

## 预期（按当前实现，逐条对应）

| 项 | 预期 |
|---|---|
| 能否打开 | 应该能打开：文件结构自洽（我们自己的 `--mfa-check` 零问题，两份真样本整份逐字节重建都成立） |
| 工程名 | `DustTale: Last Genocide` |
| 窗口尺寸 / 帧率 | 640×480 / 60fps |
| 帧（Frames） | **31 个，帧名来自原游戏**：`start / flash warning / dtlg team presents / … / game over phase 4` |
| 图像（Images） | 32 张（演示限量），尺寸/像素来自原游戏图像库 |
| 音频（Sounds） | 32 段（演示限量），数据与原游戏一致 |
| 字体（Fonts） | 17 条（其中 10 条带内嵌 TTF） |
| 图标（Icons） | 1 张 16×16（来自原游戏） |
| 帧内内容 | **空**：没有对象、没有实例、没有事件。所以每个帧是空白画面，尺寸 640×480 |
| 滚动区 | **已按成品写入**（帧尾虚拟矩形）：多数帧就是 640×480，`flash warning` 是 1280×960、`dtlg team presents` 是 840×680 |
| 图层 | **已按成品写入**：31 帧共 39 层，多数是 `Layer 1`，`dtlg team presents` 是 `Main` / `Effects` / `Top` 三层（缩放都是 1.0） |

## 二号测试件（只测「对象」这一层）

```
C:\Users\Administrator\Desktop\MultiExtractorDeveloping\_out\fusion-test\min-object.mfa
（194,487 B；模板 + 3 个占位对象 + 3 条实例，帧名 Object Test）
```

如果一号件能打开、二号件打不开，问题就在「对象 / 实例」这层（占位对象只有类型 / 名字 /
图标，加载器是空壳）；反过来都打不开，问题在更外层的容器或资源库。
这个文件就是 `mfa_objects.add_objects_to_frame()` 把 3 个对象塞进 `Template.mfa` 的产物
（Active / String / Counter 各一个，实例分别摆在 100,120 / 320,240 / 500,300；String 那个带正文 `Hello from MultiExtractor`）。

## 请回报的三件事

1. **能不能打开**（打开时如果弹错误框，请把框里的文字发我）；
2. 打开后**帧列表里的帧名**是不是上面那 31 个；
3. 资源里**能不能看到那 32 张图、32 段音频、17 个字体**。

有了这三条，V0.19 的「可被 Fusion 编辑器打开」就算有实证了；没有第 3 条也没关系，
先确认第 1、2 条 —— 那是这个版本能不能发的前提。

## 如果打不开

把错误信息发我即可。已知的可疑点（我会优先查）：

- 图像条目我按 32 位（`graphicMode=8`、BGRA）写，Fusion 对某些模式可能有额外要求；
- 图标库只有 1 张 16×16，模板原本是 12 张（16/32/48 多尺寸），Fusion 可能期望多尺寸；
- 帧体里的「非对象段」是从模板 `Template.mfa` 原样搬的（无对象、事件为空）；
- 图层 flags 统一写了 1（=可见）：成品那一格读出来是 0，含义还没定，这是可能与成品不一致的地方。

发版前要把这份文档删掉 —— 它只是验收用的临时说明。
