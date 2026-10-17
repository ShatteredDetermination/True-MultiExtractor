# -*- coding: utf-8 -*-
"""外部工具**自动下载**的自测（`me/tools/fetch.py`）。

用户的要求是「他会自己下」——不是给个链接让人手动下。所以这一层必须真的
把文件取回来、解开、验过，而且失败不能崩。这里用 `file://` 冒充网络，
把每条路都走一遍，全程不联网：

  1. 解压：单层目录要剥掉、多层要保留、按 `tools/<子目录>` 只挑一个工具；
     （含一条回归：路径段匹配曾经差一个字符，`AssetStudio.CLI.exe`
       被解成 `ssetStudio.CLI.exe`）
  2. 装单个：直链成功 / 已就位跳过 / 没给 url 就老实说「要手工放」/
     地址坏了只报错不抛异常、不留 .part；
  3. 全量包兜底：只从包里挑目标工具的目录，别把 CLI 和壳一起倒进来；
  4. 缓存：第二次装不再重新下载；
  5. 没有同意时绝不下全量包（一百多 MB，不能偷偷下）。

运行：
    python selftest/test_tools_fetch.py
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
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


def section(t):
    print("\n== " + t + " ==")


# ---------------------------------------------------------------- 造样本
def make_zip(path: Path, entries: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return path


def tool_zip(tmp: Path) -> Path:
    """像一个真 release 包：多套一层目录，里面是 exe + 几个附属文件。"""
    return make_zip(tmp / "pkg" / "utmt_cli.zip", {
        "UTMT_CLI/UndertaleModCli.exe": b"MZ" + b"\x00" * 4096,
        "UTMT_CLI/UndertaleModCli.dll": b"dll" * 64,
        "UTMT_CLI/README.md": b"# utmt",
    })


def multi_top_zip(tmp: Path) -> Path:
    """两个顶层目录：不该剥任何前缀。"""
    return make_zip(tmp / "pkg" / "multi.zip", {
        "a/one.txt": b"1",
        "b/two.txt": b"2",
    })


def full_package_zip(tmp: Path) -> Path:
    """像一个全量包：<包名>/tools/<工具>/... + CLI + 壳。"""
    return make_zip(tmp / "pkg" / "MultiExtractor_full.zip", {
        "MultiExtractor-0.17R-win64/MultiExtractorGUI.exe": b"MZ" + b"g" * 128,
        "MultiExtractor-0.17R-win64/MultiExtractorCLI.exe": b"MZ" + b"c" * 128,
        "MultiExtractor-0.17R-win64/resources/python314.dll": b"dll",
        "MultiExtractor-0.17R-win64/tools/assetstudio/AssetStudio.CLI.exe":
            b"MZ" + b"a" * 2048,
        "MultiExtractor-0.17R-win64/tools/assetstudio/AssetStudio.dll": b"dll",
        "MultiExtractor-0.17R-win64/tools/utmt/UndertaleModCli.exe":
            b"MZ" + b"u" * 2048,
    })


def main() -> int:
    print("=" * 74)
    print("  外部工具自动下载（fetch）")
    print("=" * 74)

    tmp = Path(tempfile.mkdtemp(prefix="me_fetch_")).resolve()
    real_localappdata = os.environ.get("LOCALAPPDATA")
    real_dl = os.environ.get("ME_DOWNLOAD_DIR")
    os.environ["LOCALAPPDATA"] = str(tmp / "localappdata")
    os.environ.pop("ME_DOWNLOAD_DIR", None)
    os.environ["ME_NO_NET"] = "1"

    from me.tools import fetch
    from me.tools import manifest as mf
    from me.tools import paths

    real_snapshot = mf.snapshot
    real_tools_dir = paths.TOOLS_DIR
    real_root = paths.ROOT
    tools_root = tmp / "tools"
    paths.TOOLS_DIR = tools_root
    paths.ROOT = tmp / "app"

    def use_manifest(full_package: str = "", **tools):
        """把清单换成测试用的：每个工具一个 ToolSpec。"""
        mf.snapshot = lambda *a, **k: mf.Manifest(
            source="test", full_package=full_package, tools=tools)

    try:
        # ---------------------------------------------------------- 基础
        section("1. 小工具：human / mirrors / 下载目录")
        check(fetch.human(512) == "512.0B", fetch.human(512))
        check(fetch.human(1536) == "1.5KB", fetch.human(1536))
        check(fetch.human(3 * 1024 ** 2) == "3.0MB", fetch.human(3 * 1024 ** 2))
        check(fetch.mirrors()[0] == "", "默认第一个是直连", str(fetch.mirrors()))
        os.environ["ME_TOOLS_MIRROR"] = "https://a/, ,https://b/"
        check(fetch.mirrors() == ("https://a/", "https://b/"),
              "ME_TOOLS_MIRROR 覆盖并去掉空项", str(fetch.mirrors()))
        os.environ["ME_TOOLS_MIRROR"] = ""
        check(fetch.mirrors() == ("",), "空值 -> 只走直连", str(fetch.mirrors()))
        os.environ.pop("ME_TOOLS_MIRROR", None)
        check(str(fetch.download_dir()).startswith(str(tmp)),
              "下载缓存在临时 LOCALAPPDATA 下", str(fetch.download_dir()))

        # ---------------------------------------------------------- 解压
        section("2. 解压：剥单层目录 / 保留多层 / 按 tools/<子目录> 挑")
        zp = tool_zip(tmp)
        dest = tmp / "ex1"
        n, err = fetch.extract_zip(zp, dest)
        check(err == "" and n == 3, f"解出 3 个文件（{n}，{err}）")
        check((dest / "UndertaleModCli.exe").is_file(),
              "单层顶层目录被剥掉", str(list(dest.rglob('*'))[:5]))
        check(not (dest / "UTMT_CLI").exists(), "没有留下 UTMT_CLI/ 这一层")

        mz = multi_top_zip(tmp)
        dest2 = tmp / "ex2"
        n2, err2 = fetch.extract_zip(mz, dest2)
        check(err2 == "" and n2 == 2, f"多顶层照样解开（{n2}）")
        check((dest2 / "a" / "one.txt").is_file()
              and (dest2 / "b" / "two.txt").is_file(),
              "多顶层时不剥前缀")

        # 回归：按段匹配，别再差一个字符
        fp = full_package_zip(tmp)
        dest3 = tmp / "ex3"
        n3, err3 = fetch.extract_zip(fp, dest3, only="tools/assetstudio")
        check(err3 == "" and n3 == 2, f"只挑 assetstudio 的 2 个文件（{n3}）")
        check((dest3 / "AssetStudio.CLI.exe").is_file(),
              "文件名完整（不是 ssetStudio.CLI.exe）",
              str(sorted(p.name for p in dest3.rglob('*'))[:6]))
        check(not (dest3 / "AssetStudio.GUI.exe").exists(), "没多拿别的东西")

        section("3. 坏包 / 空包 / 挑不到")
        bad = tmp / "pkg" / "bad.zip"
        bad.write_bytes(b"this is not a zip at all")
        good, why = fetch._zip_ok(bad)
        check(not good and "zip" in why.lower(), f"坏 zip 被识别（{why}）")
        bad2 = make_zip(tmp / "pkg" / "empty.zip", {})
        good2, why2 = fetch._zip_ok(bad2)
        check(not good2, f"空 zip 被识别（{why2}）")
        n4, err4 = fetch.extract_zip(fp, tmp / "ex4", only="tools/nosuchtool")
        check(n4 == 0 and err4, f"挑不到时给错误（{err4}）")

        # ---------------------------------------------------------- 装单个
        section("4. 装单个工具：直链 / 跳过 / 手工 / 坏地址")
        spec = mf.ToolSpec(key="utmt", name="UTMT", kind="cli",
                           paths=("utmt/UndertaleModCli.exe",),
                           url=zp.as_uri())
        use_manifest("", utmt=spec)

        calls = []
        real_dl_once = fetch._download_once

        def counting(url, dest, log=None, tick=None):
            calls.append(url)
            return real_dl_once(url, dest, log, tick)

        fetch._download_once = counting
        try:
            res = fetch.install_one("utmt")
            check(res.ok and res.how == "zip",
                  f"直链装成功（{res.how}）", res.error)
            check(res.path is not None and res.path.is_file(),
                  "验过之后 exe 真的在", str(res.path))
            check((tools_root / "utmt" / "UndertaleModCli.exe").is_file(),
                  "落到 <工具根>/utmt/ 下")
            check(len(calls) == 1, f"下了一次（{len(calls)}）")

            calls.clear()
            again = fetch.install_one("utmt")
            check(again.ok and again.how == "skip",
                  f"已就位就跳过（{again.how}）", again.error)
            check(not calls, "跳过时没有再下载")

            calls.clear()
            forced = fetch.install_one("utmt", force=True)
            check(forced.ok and forced.how == "zip",
                  f"force 重装（{forced.how}）", forced.error)
            check(len(calls) == 1, "force 时重新下载一次")

            # 缓存策略（用户要求：默认不用缓存，除非选 [10] 加载本地缓存安装包）
            shutil.rmtree(tools_root / "utmt", ignore_errors=True)
            check(any(fetch.download_dir().glob("*.zip")), "缓存里已经有安装包")
            calls.clear()
            fresh = fetch.install_one("utmt")
            check(fresh.ok and len(calls) == 1,
                  f"缓存里有包也重新下载（{len(calls)} 次）", fresh.error)
            check(fresh.cached is False, "结果标着不是缓存")
            check("下载完成" in fresh.line(), fresh.line())

            shutil.rmtree(tools_root / "utmt", ignore_errors=True)
            calls.clear()
            from_cache = fetch.install_one("utmt", only_cache=True)
            check(from_cache.ok and from_cache.cached is True,
                  f"only_cache 用缓存装成功（{from_cache.how}）",
                  from_cache.error)
            check(not calls, "only_cache 一个字节都没下")
            check("缓存安装完成" in from_cache.line(), from_cache.line())

            shutil.rmtree(tools_root / "utmt", ignore_errors=True)
            for p in fetch.download_dir().glob("*.zip"):
                p.unlink()
            nc = fetch.install_one("utmt", only_cache=True)
            check(not nc.ok and nc.how == "nocache",
                  f"缓存空了 -> nocache（{nc.how}）")
            check("没有本地缓存安装包" in nc.line(), nc.line())
        finally:
            fetch._download_once = real_dl_once

        # 没给 url：老实说不能自动下
        spec2 = mf.ToolSpec(key="assetstudio", name="AssetStudio",
                            paths=("assetstudio/AssetStudio.CLI.exe",))
        use_manifest("", assetstudio=spec2)
        res2 = fetch.install_one("assetstudio")
        check(not res2.ok and res2.how == "manual",
              f"没直链 -> manual（{res2.how}）")
        check("全量包" in res2.error or "手工" in res2.error,
              f"说明白该怎么办（{res2.error}）")

        # 地址坏了：只报错，不抛异常、不留 .part
        spec3 = mf.ToolSpec(key="repak", name="repak",
                            paths=("repak/repak.exe",),
                            url=(tmp / "nope" / "nothing.zip").as_uri())
        use_manifest("", repak=spec3)
        res3 = fetch.install_one("repak")
        check(not res3.ok and res3.how == "fail", f"坏地址 -> fail（{res3.how}）")
        check(bool(res3.error), f"带错误原因（{res3.error[:60]}）")
        parts = list(tools_root.rglob("*.part"))
        check(not parts, "没留下 .part 半截文件", str(parts[:3]))

        # ---------------------------------------------------------- 全量包
        section("5. 全量包兜底：只挑需要的工具")
        use_manifest(fp.as_uri(), assetstudio=spec2)
        got = fetch.install_from_package(["assetstudio"])
        check(len(got) == 1 and got[0].ok, f"从全量包装上了（{got[0].how}）")
        check(got[0].path is not None and got[0].path.name == "AssetStudio.CLI.exe",
              "装的是 AssetStudio.CLI.exe", str(got[0].path))
        check((tools_root / "assetstudio" / "AssetStudio.dll").is_file(),
              "同目录的 DLL 也带上了")
        check(not (tools_root / "MultiExtractorGUI.exe").exists(),
              "没有把壳倒进工具根")
        check(not (tools_root / "resources").exists(),
              "没有把 resources/ 倒进工具根")

        # 缓存策略：默认重新下载；only_cache 才读缓存
        shutil.rmtree(tools_root / "assetstudio", ignore_errors=True)
        calls2 = []

        def counting2(url, dest, log=None, tick=None):
            calls2.append(url)
            return real_dl_once(url, dest, log, tick)

        fetch._download_once = counting2
        try:
            again2 = fetch.install_from_package(["assetstudio"])
            check(again2[0].ok and len(calls2) == 1,
                  f"默认不用缓存：第二次仍然重新下（{len(calls2)} 次）")
            check(again2[0].cached is False, "结果标着不是缓存")

            shutil.rmtree(tools_root / "assetstudio", ignore_errors=True)
            calls2.clear()
            from_cache = fetch.install_from_package(["assetstudio"],
                                                    only_cache=True)
            check(from_cache[0].ok and not calls2,
                  f"only_cache 用缓存里的全量包（下载 {len(calls2)} 次）")
            check(from_cache[0].cached is True, "结果标着来自本地缓存")
            check("缓存安装完成" in from_cache[0].line(), from_cache[0].line())
        finally:
            fetch._download_once = real_dl_once

        # ---------------------------------------------------------- 同意与否
        section("6. 没同意时绝不下全量包")
        use_manifest(fp.as_uri(), assetstudio=spec2)
        shutil.rmtree(tools_root / "assetstudio", ignore_errors=True)
        called_pkg = []
        real_pkg = fetch.install_from_package

        def record_pkg(*a, **k):
            called_pkg.append(a)
            return real_pkg(*a, **k)

        fetch.install_from_package = record_pkg
        try:
            results, used = fetch.install(["assetstudio"], allow_package=False)
            check(not called_pkg, "allow_package=False 时一次都没下全量包")
            check(used is False, "used_package = False")
            check(results and not results[0].ok,
                  "结果里如实写着没装上", str(results[0].how))

            results2, used2 = fetch.install(["assetstudio"], allow_package=True)
            check(len(called_pkg) == 1, "允许后才下了一次全量包")
            check(used2 is True, "used_package = True")
            check(results2 and results2[0].ok, f"这次装上了（{results2[0].how}）")
        finally:
            fetch.install_from_package = real_pkg

        # ---------------------------------------------------------- 清单缺项
        section("7. 清单里没有的工具 / 缺的键")
        check(fetch.missing_keys() == [], "装完就没缺的了", str(fetch.missing_keys()))
        res4 = fetch.install_one("nosuchtool")
        check(not res4.ok and "清单" in res4.error, f"未知键 -> 报错（{res4.error}）")
        res5 = fetch.install(["nosuchtool"])
        check(isinstance(res5, tuple) and len(res5) == 2,
              "install 永远返回 (results, used_package)")

        # ---------------------------------------------------------- 进度事件
        # 用户的现场：GUI 里下工具时进度条一直 0，标题变成「未回报进度」。
        # 下载器本来就是 Python 的，问题在于「一个字节都没到」时没有任何事件。
        # 这里用 file:// 冒充 GitHub，跑真的 main.py，验事件流。
        section("8. --tools-get --json：进度事件与 tool_get 都发得出来")

        cli_tools = tmp / "cli-tools"
        cli_tools.mkdir(parents=True, exist_ok=True)
        remote = make_zip(tmp / "remote" / "utmt.zip",
                          {"UTMT_CLI/UndertaleModCli.exe": b"MZ" + b"\x00" * 4096})
        ini = tmp / "remote" / "tools.ini"
        ini.write_text(
            "[meta]\nversion = 9\nupdated = 2026-10-17\nfull_package =\n\n"
            "[utmt]\nname = UTMT\nkind = cli\nnote = test\n"
            "paths = utmt/UndertaleModCli.exe\n"
            f"url = {remote.as_uri()}\n",
            encoding="utf-8")

        # 清单走「本地缓存」这一层：ME_NO_NET=1 不会去拉远端，
        # 而缓存是同步可读的，测试不依赖线程时序
        cache_dir = tmp / "localappdata" / "MultiExtractor"
        cache_dir.mkdir(parents=True, exist_ok=True)
        (cache_dir / "tools.ini").write_text(ini.read_text(encoding="utf-8"),
                                             encoding="utf-8")

        env = dict(os.environ)
        env["ME_TOOLS_URL"] = ini.as_uri()
        env["ME_DOWNLOAD_DIR"] = str(tmp / "cache2")
        env["LOCALAPPDATA"] = str(tmp / "localappdata")
        env["ME_NO_NET"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        proc = subprocess.run(
            [sys.executable, str(ROOT / "main.py"), "--tools-dir", str(cli_tools),
             "--tools-get", "--json"],
            cwd=str(ROOT), env=env, capture_output=True)
        out = proc.stdout.decode("utf-8", "replace")
        events = []
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("{"):
                try:
                    events.append(json.loads(line))
                except ValueError:
                    pass
        kinds = [e.get("t") for e in events]
        check(proc.returncode == 0, f"退出码 0（实际 {proc.returncode}）",
              (proc.stderr or b"")[-300:].decode("utf-8", "replace"))
        check("progress" in kinds, "发了 progress 事件（GUI 进度条能动）",
              str(kinds[:12]))
        check("tool_get" in kinds, "发了 tool_get 事件", str(kinds[:12]))
        got = [e for e in events if e.get("t") == "tool_get"]
        check(got and got[0].get("ok") is True, f"tool_get ok（{got[:1]}）")
        check((cli_tools / "utmt" / "UndertaleModCli.exe").is_file(),
              "工具真的落到 --tools-dir 指的地方",
              str(list(cli_tools.rglob('*'))[:5]))

        print("\n" + "=" * 74)
        if FAIL:
            print(f"  失败 {FAIL} 项，通过 {PASS} 项")
            return 1
        print(f"  全部通过（{PASS} 项）")
        return 0
    finally:
        mf.snapshot = real_snapshot
        paths.TOOLS_DIR, paths.ROOT = real_tools_dir, real_root
        os.environ.pop("ME_TOOLS_MIRROR", None)
        if real_dl is None:
            os.environ.pop("ME_DOWNLOAD_DIR", None)
        else:
            os.environ["ME_DOWNLOAD_DIR"] = real_dl
        if real_localappdata is None:
            os.environ.pop("LOCALAPPDATA", None)
        else:
            os.environ["LOCALAPPDATA"] = real_localappdata
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
