# -*- coding: utf-8 -*-
"""Unity 提取（借 AssetStudio 的 CLI）。

实测得到的 AssetStudio.CLI 真实用法（tools/assetstudio 里那份）：

    AssetStudio.CLI.exe <input_path> <output_path> [options]

  * `--game` 是**必填**的，不传直接报错退出。
    实测传错值会打印 Argument 'xxx' not recognized，退出码仍是 0。
  * `--silent` 只压住日志，不影响导出。
  * `--types` 的 help 原文是
        --types <Texture2D|Shader:Parse|Sprite:Both|etc..>
    也就是「类型名[:导出选项]」，多个类型用空格分隔。
    tools/assetstudio/AssetStudio.CLI.dll.config 里列出了全部合法类型名：
        Animation AnimationClip Animator AnimatorController AnimatorOverrideController
        AssetBundle AudioClip Avatar Font GameObject IndexObject Material Mesh
        MeshFilter MeshRenderer MiHoYoBinData MonoBehaviour MonoScript MovieTexture
        PlayerSettings RectTransform Shader SkinnedMeshRenderer Sprite SpriteAtlas
        TextAsset Texture2D Transform VideoClip ResourceManager
  * **退出码不可信**：实测输入目录不存在时它打了一整页
    DirectoryNotFoundException 堆栈，退出码依然是 0。
    所以判断成败必须看输出目录里有没有文件，不能看 returncode。

为什么要分两次调用：一次把图、音、模型、Shader 全导出来会慢得离谱
（贴图转 PNG 是主要开销）。图片和音频是这个工具真正要交付的东西，
分两趟跑既快又能让两趟分别打日志，出问题好定位。
"""

import os
from pathlib import Path
from typing import List, Optional, Tuple

from me.collect import gather, kinds
from me.engines.base import Context, EngineError, Outcome
from me.tools import paths as tool_paths
from me.tools import stream

KEY = "unity"
NAME = "Unity"

# `*_Data` 目录里出现这些就说明这是 Unity 的数据目录。
# globalgamemanagers / level0 是几乎必然存在的两个：
#   globalgamemanagers —— 全局配置与场景清单
#   level0             —— 第一个场景
# 老版本 Unity 可能只有 mainData / resources.assets，所以也认这些。
DATA_MARKERS = (
    "globalgamemanagers",
    "globalgamemanagers.assets",
    "level0",
    "mainData",
    "resources.assets",
    "unity default resources",
    "data.unity3d",
)

# 输入里散落的 Unity 容器，找不到 _Data 时的兜底
CONTAINER_EXTS = (".assets", ".bundle", ".unity3d", ".apk", ".obb")

# `_Data` 目录里这些**没有扩展名**的文件是 Unity 自己的元数据/场景，
# 不是能直接用的资源（level0 里其实是序列化过的场景对象）。
# 兜底扫描时跳过它们，否则输出目录里会躺一堆看不出是什么的二进制，
# 还会虚高「导出了多少个文件」的计数。
# 注意别把 globalgamemanagers.assets 加进来 —— 那个是场景资源，要留。
METADATA_NAMES = frozenset((
    "globalgamemanagers", "level0", "level1", "level2", "maindata",
    "unity default resources", "data.unity3d", "boot.config",
))

# --game 的默认值。加密 / 特殊渠道的游戏要换别的渠道值。
DEFAULT_GAME = "Normal"

# 两趟导出的类型。空格分隔就是「多个类型」，不要写成 A:B 的冒号形式
# （冒号是「类型:导出选项」，例如 Sprite:Both、Shader:Parse，不是类型分隔符）。
DEFAULT_IMAGE_TYPES = "Texture2D Sprite"
DEFAULT_AUDIO_TYPES = "AudioClip"

# 冒号后面这些是**导出选项**，不是类型名。
# help 里的例子 `Sprite:Both` 就是「Sprite 用 Both 方式导出」。
# 用户要是照抄 `Texture2D:Sprite:Both` 这种写法，得把 Both 摘掉，
# 否则它会被当成一个不存在的类型名传进去，AssetStudio 会静默什么都不导。
EXPORT_OPTIONS = {"Both", "Parse", "Raw", "Convert", "Dump", "JSON"}


def _types_from_env(var: str, default: str) -> Optional[str]:
    """读环境变量里的 --types 值，返回 None 表示这一趟不跑。

    设成空串是有意义的操作：表示「不限定类型，全量导出」。
    有些魔改 Unity 的类名不在既定表里，用 --types 反而一个都不出，
    这时用户可以把变量设成空串跑一趟全量的。
    """
    raw = os.environ.get(var)
    if raw is None:
        return default
    raw = raw.strip()
    if not raw:
        return None
    return raw


def _looks_like_data_dir(path: Path) -> bool:
    """目录是不是 Unity 的 `*_Data` 数据目录。"""
    if not path.is_dir():
        return False
    for marker in DATA_MARKERS:
        if (path / marker).exists():
            return True
    # 没有清单文件但有 .assets / .bundle 也算（老版本、裁剪过的包）
    return any(path.glob("*.assets")) or any(path.glob("*.bundle"))


def find_data_dir(target) -> Optional[Path]:
    """定位 Unity 的数据目录 `*_Data`。"""
    target = Path(target)
    if target.is_file():
        return None
    if target.name.endswith("_Data") and _looks_like_data_dir(target):
        return target
    # 标准布局：<游戏名>/<游戏名>_Data/
    for p in sorted(target.glob("*_Data")):
        if _looks_like_data_dir(p):
            return p
    # 有的游戏把 _Data 埋深一层（比如 <游戏>/Win64/<游戏>_Data/）
    for p in sorted(target.rglob("*_Data")):
        if _looks_like_data_dir(p):
            return p
    return None


def find_loose_containers(target) -> List[Path]:
    """没有 _Data 时，找散落的 .assets / .bundle / .apk。"""
    target = Path(target)
    if target.is_file():
        return [target] if target.suffix.lower() in CONTAINER_EXTS else []
    out: List[Path] = []
    for p in sorted(target.rglob("*")):
        if p.is_file() and p.suffix.lower() in CONTAINER_EXTS:
            out.append(p)
    return out


def find_input(target) -> Tuple[Optional[Path], str]:
    """决定传给 AssetStudio 的输入路径，返回 (路径, 说明)。

    实测 AssetStudio 的 input 是文件或目录都收，而且会递归扫子目录。
    这里优先把 `*_Data` 目录整个交给它（而不是它的父目录）：
    父目录里往往还有几百 MB 的 exe / UnityPlayer.dll / 我们自己的输出，
    交给 CLR 白扫一遍只会更慢。
    """
    target = Path(target)
    if target.is_file():
        return target, "文件"

    data = find_data_dir(target)
    if data is not None:
        return data, f"数据目录 {data.name}"

    loose = find_loose_containers(target)
    if loose:
        return loose[0], f"容器文件 {loose[0].name}"

    # 还有一种是用户直接把 `_Data` 目录拖进来但目录结构非常规
    if any(target.glob("*.assets")) or any(target.glob("*.bundle")):
        return target, "含 .assets/.bundle 的目录"
    return None, ""


def build_argv(cli: Path, src: Path, out_dir: Path, game: str,
               types: Optional[str]) -> List[str]:
    """拼一次 AssetStudio 调用的命令行。

    单独抽出来是为了能自测：不真跑 CLI 也能断言参数拼得对不对。
    """
    argv = [str(cli), str(src), str(out_dir), "--game", game]
    if types:
        # 先按空格切，再按冒号切，这样 `Texture2D Sprite` 和
        # `Texture2D:Sprite:Both` 两种写法都能吃下；
        # 冒号后面那截如果是导出选项（Both/Parse/...）就丢掉，它不是类型名。
        names = []
        for chunk in types.replace(":", " ").split():
            if chunk in EXPORT_OPTIONS or chunk in names:
                continue
            names.append(chunk)
        if names:
            argv += ["--types"] + names
    argv.append("--silent")
    return argv


def _has_files(directory: Path) -> bool:
    """目录里有没有文件（用来绕开 AssetStudio 永远退出 0 的坑）。"""
    directory = Path(directory)
    if not directory.exists():
        return False
    for _root, _dirs, files in os.walk(directory):
        if files:
            return True
    return False


def _gather_loose(src: Path, ctx: Context) -> int:
    """收集 _Data 里散落的、扩展名能认出来的资源，返回写出数量。

    跳过两类东西：
      * 无扩展名的 Unity 元数据（globalgamemanagers / level0 ...）——
        它们是序列化过的引擎内部结构，抄出来用户也用不了；
      * .assets / .bundle 等容器 —— 那些本来就该交给 AssetStudio 去解，
        直接抄一份原始二进制没有意义。
    其余按扩展名正常归类，所以散落的 .png / .ogg / .mp4 都能捞到。
    """
    written = 0
    wanted = ctx.writer.categories
    for root, dirs, names in os.walk(src):
        dirs[:] = [d for d in dirs if d not in ("__MACOSX",)]
        for fn in names:
            ext = os.path.splitext(fn)[1].lower()
            if not ext or ext in CONTAINER_EXTS:
                continue
            if fn.lower() in METADATA_NAMES:
                continue
            category = kinds.classify(fn)
            if wanted and category not in wanted:
                continue
            path = Path(root) / fn
            rel = path.relative_to(src).as_posix()
            if ctx.writer.copy_from(category, rel, path, origin=str(src)):
                written += 1
    return written


def extract(ctx: Context) -> Outcome:
    target = Path(ctx.target)
    out = Outcome()

    cli = tool_paths.find("assetstudio")
    if cli is None:
        raise EngineError(
            "缺少 AssetStudio CLI，无法提取 Unity 资源。\n"
            "      修复：python tools/setup_tools.py assetstudio")

    src, how = find_input(target)
    if src is None:
        raise EngineError(
            "没有找到 Unity 数据：既没有 <游戏名>_Data 目录，"
            "也没有 globalgamemanagers / level0 / *.assets / *.bundle。\n"
            "      如果游戏是安装版，请指向含 <游戏名>_Data 的那个目录。")

    game = os.environ.get("ME_UNITY_GAME", "").strip() or DEFAULT_GAME
    out.notes.append(f"输入: {how} -> {src}")

    if not ctx.deep:
        out.notes.append("当前为「不解容器」模式，未调用 AssetStudio")
        out.fallback = True
        return out

    work = ctx.sub_work("unity")
    # 两趟写到不同子目录，收进 writer 后相对路径里带 images/ audio/，好对账
    jobs = []
    img_types = _types_from_env("ME_UNITY_IMAGE_TYPES", DEFAULT_IMAGE_TYPES)
    aud_types = _types_from_env("ME_UNITY_AUDIO_TYPES", DEFAULT_AUDIO_TYPES)
    # 用户只要某一类时，另一趟直接别跑
    if img_types and ctx.writer.wants("image"):
        jobs.append(("images", img_types))
    if aud_types and ctx.writer.wants("audio"):
        jobs.append(("audio", aud_types))
    if not jobs:
        out.notes.append("按当前分类筛选，没有需要跑的类型；如需导出请放开图片/音频分类")

    total = 0
    for label, types in jobs:
        out_dir = work / label
        argv = build_argv(cli, src, out_dir, game, types)
        # AssetStudio 导出贴图很慢（要逐张转 PNG），所以输出必须实时透传，
        # 否则界面长时间静止，用户会以为卡死。
        res = stream.run_live(
            argv,
            timeout=_timeout(),
            echo=ctx.verbose,
            banner=f"AssetStudio 正在导出 {label}（--types {types}）：",
        )

        # 注意：returncode 为 0 不代表成功，失败也可能返回 0，所以下面看文件数
        n = gather.gather_tree(out_dir, ctx.writer,
                               rel_prefix=label, origin=str(src))
        total += n
        out.notes.append(f"{label}: --types {types} -> {n} 个文件"
                         f"（退出码 {res.code}）")
        if n == 0:
            # 只在真出错时贴日志尾巴；成功但没资源是正常情况（比如纯 3D 游戏）
            tail = res.tail(1)
            if tail:
                out.notes.append(f"{label} 的 CLI 输出: " + tail.strip()[:200])

    # 兜底：把数据目录里本来就散着、AssetStudio 没管的文件也收进来
    # （有些游戏把音频/视频直接裸放在 StreamingAssets 里）
    if src.is_dir():
        extra = _gather_loose(src, ctx)
        if extra:
            total += extra
            out.notes.append(f"数据目录里的散落资源另收 {extra} 个文件")

    out.written = total
    out.detail = f"从 {src.name} 导出 {total} 个文件"

    if total == 0:
        out.notes.append(
            "AssetStudio 一个文件都没导出。常见原因：\n"
            "        1) 游戏加密或走特殊渠道 —— 试试换 --game 渠道值。\n"
            "           实测 --help 列出的全部候选：Normal / UnityCN / GI / GI_Pack /\n"
            "           GI_CB1 / GI_CB2 / GI_CB3 / GI_CB3Pre / BH3 / BH3Pre / BH3PrePre /\n"
            "           SR / SR_CB2 / ZZZ_CB1 / TOT / Naraka / EnsembleStars / OPFP /\n"
            "           FakeHeader / FantasyOfWind / ShiningNikki / HelixWaltz2 / NetEase /\n"
            "           AnchorPanic / DreamscapeAlbireo / ImaginaryFest / AliceGearAegis /\n"
            "           ProjectSekai / CodenameJump / GirlsFrontline / Reverse1999 /\n"
            "           ArknightsEndfield / JJKPhantomParade / MuvLuvDimensions /\n"
            "           PartyAnimals / LoveAndDeepspace / SchoolGirlStrikers / ExAstris /\n"
            "           PerpetualNovelty\n"
            "           用环境变量 ME_UNITY_GAME=<候选值> 指定。\n"
            "        2) 类型名对不上 —— 魔改引擎的类名可能不同。\n"
            "           用 ME_UNITY_IMAGE_TYPES / ME_UNITY_AUDIO_TYPES 换类型，\n"
            "           设成空串表示不限定类型、全量导出（慢，但能确认到底有没有资源）。\n"
            "        3) 输入路径不对 —— 上面 notes 里的「输入」一行是实际传进去的路径。")
        out.fallback = True
    return out


def _timeout() -> int:
    from me.config import TOOL_TIMEOUT
    return TOOL_TIMEOUT
