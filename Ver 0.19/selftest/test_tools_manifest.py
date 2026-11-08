# -*- coding: utf-8 -*-
"""外部工具清单（tools.ini）的三级来源自测。

Slim 包不带 tools/，所以「有哪些工具、去哪下」全靠这份清单。清单有三个来源，
优先级从高到低是 远端 → 本地缓存 → 内置兜底，本测试把三级都真的走一遍：

  1. 远端是用 ``file://`` 假装的（真实 HTTP 只在 CI 才有意义，而且会外连）；
  2. 缓存写进临时 LOCALAPPDATA，不碰用户真实的 %LOCALAPPDATA%；
  3. 内置兜底在「远端挂、缓存空」时不能是空的，界面才有东西可显示。

另外钉住两条容错：清单是**远端可控**的输入，解析器不许因为一行怪东西整段罢工；
拉取失败绝不许抛异常（启动路径等着它，抛了就起不来）。

运行：
    python selftest/test_tools_manifest.py
"""

import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PASS = 0
FAIL = 0


def check(ok, what, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [ok] {what}")
    else:
        FAIL += 1
        print(f"  [err] {what}  {detail}")


# 一份「远端」清单：故意混进注释、空行、缺 paths 的工具、坏 size_mb
SAMPLE_INI = """\
; 远端清单示例
# 也允许井号注释

[meta]
version = 7
updated = 2026-10-18
full_package = https://example.com/MultiExtractor-full.zip

[utmt]
name = UndertaleModTool CLI
kind = cli
note = GameMaker data.win
paths = utmt/UndertaleModCli.exe, utmt\\UndertaleModTool.exe
url = https://example.com/utmt.zip
size_mb = 12.5

[assetstudio]
name = AssetStudio CLI
paths = assetstudio/AssetStudio.CLI.exe
size_mb = 不是数字

[broken]
name = 没有 paths 的工具

[repak]
name = repak
paths = repak/repak.exe
"""


def main() -> int:
    print("=" * 74)
    print("  tools.ini：远端 / 缓存 / 内置兜底")
    print("=" * 74)

    tmp = Path(tempfile.mkdtemp(prefix="me_manifest_"))
    real_localappdata = os.environ.get("LOCALAPPDATA")
    os.environ["LOCALAPPDATA"] = str(tmp / "localappdata")
    os.environ["ME_NO_NET"] = "1"          # 本测试自己控制，不让别的层抢先

    from me.tools import manifest as mf

    try:
        cache = mf.cache_path()
        check(str(cache).startswith(str(tmp)), "缓存落在临时目录", str(cache))
        mf.reset_for_tests()
        mf.cache_dir().mkdir(parents=True, exist_ok=True)
        if cache.exists():
            cache.unlink()

        # ---------------------------------------------------------- 解析
        print("\n[1] 解析")
        m = mf.parse(SAMPLE_INI, "remote")
        check(m is not None, "能解析出清单")
        if m is None:
            print("\n  失败：解析返回 None，后面全跳过")
            return 1
        check(m.version == "7", "meta.version", m.version)
        check(m.updated == "2026-10-18", "meta.updated", m.updated)
        check(m.full_package.endswith(".zip"), "meta.full_package", m.full_package)
        check(set(m.keys()) == {"utmt", "assetstudio", "repak"},
              "缺 paths 的 [broken] 被丢掉", str(m.keys()))
        check(m.keys()[0] == "utmt", "顺序按文件出现顺序", str(m.keys()))

        u = m.tools["utmt"]
        check(u.name == "UndertaleModTool CLI", "name", u.name)
        check(u.note == "GameMaker data.win", "note", u.note)
        check(len(u.paths) == 2, "paths 拆成两条", str(u.paths))
        check(u.paths[1] == "utmt/UndertaleModTool.exe",
              "反斜杠被归一成正斜杠", u.paths[1])
        check(u.primary == "utmt/UndertaleModCli.exe", "primary 取第一条", u.primary)
        check(abs(u.size_mb - 12.5) < 1e-6, "size_mb 解析", str(u.size_mb))
        check(m.tools["assetstudio"].size_mb == 0.0,
              "坏 size_mb 归 0 不抛异常", str(m.tools["assetstudio"].size_mb))
        check(m.tools["repak"].url == "", "没有 url 就是空串")

        check(mf.parse("", "remote") is None, "空文本 -> None")
        check(mf.parse("   \n\n ", "remote") is None, "全空白 -> None")
        check(mf.parse("[meta]\nversion = 1\n", "remote") is None,
              "只有 meta 没有工具 -> None")
        check(mf.parse("这不是 ini\n随便一行\n", "remote") is None,
              "完全不成形的文本 -> None")

        # ---------------------------------------------------------- 远端（file://）
        print("\n[2] 远端（file:// 冒充 HTTP）")
        src = tmp / "remote_tools.ini"
        # 按字节写：文本模式在 Windows 上会把 \n 变成 \r\n，
        # 后面「缓存存的是原文」那条就会因为换行符不同而误报。
        src.write_bytes(SAMPLE_INI.encode("utf-8"))
        url = src.as_uri()
        check(url.startswith("file:"), "URL 是 file://", url)

        r = mf.load_remote(url, 3.0)
        check(r is not None, "从远端拉到并解析")
        check(r is not None and r.source == "remote", "source == remote",
              r.source if r else "")
        check(r is not None and len(r.tools) == 3, "远端 3 个工具",
              str(len(r.tools) if r else -1))
        check(cache.exists(), "成功的远端正**写缓存**", str(cache))
        check(cache.read_text(encoding="utf-8") == SAMPLE_INI,
              "缓存存的是原文")

        # 拉到了但不是清单：不许覆盖缓存
        junk = tmp / "junk.ini"
        junk.write_text("随便写点啥\n", encoding="utf-8")
        before = cache.read_text(encoding="utf-8")
        check(mf.load_remote(junk.as_uri(), 3.0) is None, "内容不是清单 -> None")
        check(cache.read_text(encoding="utf-8") == before,
              "坏响应不覆盖已有缓存")

        # ---------------------------------------------------------- 缓存
        print("\n[3] 本地缓存")
        c = mf.load_cache()
        check(c is not None, "能读回缓存")
        check(c is not None and c.source == "cache", "source == cache",
              c.source if c else "")
        check(c is not None and set(c.keys()) == set(r.keys()),
              "缓存与远端内容一致")
        check(mf.load_cache() is not c, "每次都是新对象（不会被就地改）")

        # ---------------------------------------------------------- 内置兜底
        print("\n[4] 内置兜底")
        check(len(mf.BUILTIN.tools) == 4, "内置 4 个工具",
              str(len(mf.BUILTIN.tools)))
        check(mf.BUILTIN.source == "builtin", "source == builtin")
        check(all(s.paths for s in mf.BUILTIN.tools.values()),
              "内置每个工具都有候选路径")
        check(mf.BUILTIN.full_package.startswith("https://"),
              "内置有全量包地址", mf.BUILTIN.full_package)

        cache.unlink()
        b = mf.load_any(use_remote=False)
        check(b.source == "builtin", "没网没缓存 -> 落到内置", b.source)
        check(len(b.tools) == 4, "内置兜底不是空的", str(len(b.tools)))
        check(mf.status_line(b).find("内置兜底") >= 0, "状态行点明来源",
              mf.status_line(b))

        # 有缓存时，即便远端不通也该用缓存
        cache.write_text(SAMPLE_INI, encoding="utf-8")
        n = mf.load_any(url=(tmp / "does_not_exist.ini").as_uri(), timeout=0.5)
        check(n.source == "cache", "远端不通 -> 用缓存", n.source)

        # ---------------------------------------------------------- 网络容错
        print("\n[5] 网络容错（不抛异常、有上限）")
        t0 = time.time()
        got = mf.fetch("http://127.0.0.1:9/没有这个服务.ini", 0.5)
        dt = time.time() - t0
        check(got is None, "连不上 -> None", repr(got))
        check(dt < 3.0, f"0.5s 超时没有拖住（用了 {dt:.2f}s）")
        check(mf.fetch("", 1.0) is None, "空 URL -> None")

        os.environ["ME_TOOLS_TIMEOUT"] = "0.7"
        check(abs(mf.timeout_s() - 0.7) < 1e-6, "ME_TOOLS_TIMEOUT 生效",
              str(mf.timeout_s()))
        os.environ["ME_TOOLS_TIMEOUT"] = "999"
        check(mf.timeout_s() <= 30.0, "超时被夹在上限内", str(mf.timeout_s()))
        os.environ["ME_TOOLS_TIMEOUT"] = "不是数字"
        check(mf.timeout_s() == mf.TIMEOUT, "坏值回到默认",
              str(mf.timeout_s()))
        os.environ.pop("ME_TOOLS_TIMEOUT", None)
        check(mf.TIMEOUT == 3.0, "默认 3 秒（用户明确要求）", str(mf.TIMEOUT))

        # ---------------------------------------------------------- 后台预热
        print("\n[6] 启动时的后台预热")
        cache.unlink()
        mf.reset_for_tests()
        os.environ["ME_TOOLS_URL"] = url
        check(mf.remote_url() == url, "ME_TOOLS_URL 覆盖默认地址", mf.remote_url())

        mf.start_warmup()
        check(mf.warmup_done(5.0), "预热线程结束了")
        snap = mf.snapshot()
        check(snap.source == "remote", "预热结果被 snapshot 取到", snap.source)
        check(len(snap.tools) == 3, "预热结果里有工具", str(len(snap.tools)))
        check(cache.exists(), "预热也写缓存")

        # 重复调用不重复拉（除非 force）
        mf.start_warmup()
        check(mf.warmup_done(0.1), "已在预热过：不重开线程")

        # 预热失败时 snapshot 落到缓存/内置，且不阻塞
        mf.reset_for_tests()
        os.environ["ME_TOOLS_URL"] = (tmp / "nope.ini").as_uri()
        mf.start_warmup()
        mf.warmup_done(5.0)
        fallback = mf.snapshot()
        check(fallback.source in ("cache", "builtin"),
              "预热失败 -> 缓存/内置", fallback.source)
        check(len(fallback.tools) > 0, "兜底不是空的")

        print("\n" + "=" * 74)
        if FAIL:
            print(f"  失败 {FAIL} 项")
            return 1
        print(f"  全部通过（{PASS} 项）")
        return 0
    finally:
        mf.reset_for_tests()
        os.environ.pop("ME_TOOLS_URL", None)
        os.environ.pop("ME_TOOLS_TIMEOUT", None)
        if real_localappdata is None:
            os.environ.pop("LOCALAPPDATA", None)
        else:
            os.environ["LOCALAPPDATA"] = real_localappdata
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
