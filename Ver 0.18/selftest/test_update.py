# -*- coding: utf-8 -*-
"""在线更新（`me/update.py` + `main.py --update`）的自测。

用户要的是「全自动在线更新」，所以这里不只测函数，还要把**闭环**真跑一遍：

    本地 HTTP 服务当远端  →  CLI 查 version.json、比版本
                          →  下载 UpdatePack.zip、校验 SHA256
                          →  写 daemon.bat、拉起、主程序退出
                          →  daemon.bat 等 2 秒、tar 解压覆盖、删包、重启、删自己

覆盖的分支：
  * 版本比较（剥离 E/Rc/S 后缀，只比主版本号）、公测版提示、verid= 点名；
  * JSON 坏掉 / 缺字段 / 断网 / 超时（3 秒口径）→ 静默失败；
  * SHA256 不一致 → 报错并**删掉**包，不留半截文件；
  * 启动自检：清理残留 daemon.bat、发现 UpdatePack.zip 时不阻塞非交互调用；
  * daemon.bat 的关键行（tar / del / start / 自删除 / 等待）。

运行：
    python selftest/test_update.py
"""

import hashlib
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

MAIN = ROOT / "main.py"

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


# ---------------------------------------------------------------- 假远端
class Server:
    """一个能改内容的极简 HTTP 服务：/version.json 与 /UpdatePack.zip。"""

    def __init__(self):
        self.routes = {}
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_a):        # 别刷屏
                pass

            def do_GET(self):
                body = outer.routes.get(self.path)
                if body is None:
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def stop(self):
        try:
            self.httpd.shutdown()
            self.httpd.server_close()
        except OSError:
            pass


class SilentServer:
    """只接受连接、永不回话 —— 用来验 3 秒超时。"""

    def __init__(self):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(4)
        self.port = self.sock.getsockname()[1]
        self.keep = []
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            self.keep.append(conn)      # 拿着不回，让客户端自己超时

    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/version.json"

    def stop(self):
        for c in self.keep:
            try:
                c.close()
            except OSError:
                pass
        try:
            self.sock.close()
        except OSError:
            pass


def make_pack(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def run_cli(argv, env_extra=None, timeout=180, stdin_text=""):
    # 默认离线（自测不该真联网）；要联本地 HTTP 的用例在 env_extra 里显式 ME_NO_NET=0
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["ME_NO_NET"] = "1"
    for k, v in (env_extra or {}).items():
        env[k] = str(v)
    proc = subprocess.run([sys.executable, str(MAIN)] + argv,
                          input=stdin_text.encode("utf-8"),
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          timeout=timeout, cwd=str(ROOT), env=env)
    return proc.returncode, proc.stdout.decode("utf-8", "replace")


# ---------------------------------------------------------------- 用例
def test_version_logic():
    from me import update as upd

    section("1. 版本解析与比较（剥离后缀，只比主版本号）")
    check(upd.parse_version("0.18E Beta 00096") == (0, 18, "E", "00096"),
          f"完整版本串（{upd.parse_version('0.18E Beta 00096')}）")
    check(upd.parse_version("0.18E") == (0, 18, "E", ""), "没有编号也认")
    check(upd.parse_version("v0.17R") == (0, 17, "R", ""), "允许 v 前缀")
    check(upd.parse_version("0.18Rc") == (0, 18, "Rc", ""), "Rc 是两字母后缀")
    check(upd.parse_version("乱写") is None, "认不出的返回 None")

    check(upd.main_version("0.18E Beta 00096") == (0, 18), "主版本号剥离后缀")
    check(upd.compare("0.18E", "0.17R") == 1, "0.18E > 0.17R")
    check(upd.compare("0.18Rc", "0.18E") == 0, "0.18Rc == 0.18E（后缀不参与）")
    check(upd.compare("0.17R", "0.18WIP") == -1, "0.17R < 0.18WIP")
    check(upd.compare("0.18S", "0.18") == 0, "S 后缀也不参与")

    check(upd.is_beta("0.18E") is True, "E 算公测")
    check(upd.is_beta("0.18Rc") is True, "Rc 算公测")
    check(upd.is_beta("0.18WIP") is True, "WIP 算公测")
    check(upd.is_beta("0.17R") is False, "R 不算公测")
    check(upd.is_stable("0.17R") and upd.is_stable("0.18"), "R 与无后缀算稳定")
    check(upd.same_line_better("0.18E", "0.18R") is True, "同一条线的 E -> R 算升级")
    check(upd.same_line_better("0.18R", "0.18E") is False, "同一条线的 R -> E 不算")


def test_json():
    from me import update as upd

    section("2. version.json 解析容错")
    good = json.dumps({
        "latest_stable": "0.17R",
        "latest_beta": "0.18E",
        "download_url": "https://x/UpdatePack.zip",
        "sha256": "ABCD",
        "changelog": "修复了若干问题",
        "extra": 1,
    })
    info = upd.parse(good)
    check(info is not None, "正常 JSON 能解析")
    check(info.latest_stable == "0.17R" and info.latest_beta == "0.18E",
          "两个通道都读到了", f"{info.latest_stable}/{info.latest_beta}")
    check(info.sha256 == "abcd", "sha256 归一成小写", info.sha256)
    check("修复了" in info.changelog, "changelog 读到了")
    check(info.raw.get("extra") == "1", "额外字段也留着")

    check(upd.parse("") is None, "空文本 -> None")
    check(upd.parse("{不是 json") is None, "坏 JSON -> None")
    check(upd.parse("[]") is None, "数组不是清单 -> None")
    check(upd.parse('{"download_url": "x"}') is None, "一个版本号都没有 -> None")


def test_choose():
    from me import update as upd

    section("3. 选哪个版本（公测版提示）")
    info = upd.parse(json.dumps({
        "latest_stable": "0.19R", "latest_beta": "0.20E",
        "download_url": "https://x/p.zip", "sha256": "aa"}))

    ver, why = upd.choose(info)
    check(ver == "0.20E", f"本机 0.18E 时选更高的公测版（{ver} {why}）")

    p = upd.plan(info)
    check(p["available"] is True, "plan.available")
    check(p["beta_available"] is False, "本机是公测版时不再提示「有公测版」")

    # 本机稳定版 + 远端有公测版 -> 提示公测版
    old_local = upd.local_version
    upd.local_version = lambda: "0.17R Beta 00094"
    try:
        p2 = upd.plan(info)
        check(p2["beta_available"] is True, "稳定版机器上提示有公测版", str(p2))
        ver2, why2 = upd.pick(info, "")
        check(ver2 == "0.20E" and "公测" in why2, f"自动选公测版（{why2}）")
        check(upd.pick(info, "stable")[0] == "0.19R", "verid=stable 选稳定版")
        check(upd.pick(info, "beta")[0] == "0.20E", "verid=beta 选公测版")
        check(upd.pick(info, "verid=0.19R")[0] == "0.19R", "verid= 点名具体版本")
        check(upd.pick(info, "verid=0.15R")[0] == "", "点一个不存在的版本 -> 空")
        check("没有" in upd.pick(info, "verid=0.15R")[1], "并说明原因",
              upd.pick(info, "verid=0.15R")[1])
    finally:
        upd.local_version = old_local

    same = upd.parse(json.dumps({"latest_stable": "0.18R", "latest_beta": "0.18E"}))
    p_same = upd.plan(same)
    check(p_same["available"] is False, "同一条版本线不自动催更新", str(p_same))
    p_point = upd.plan(same, "verid=0.18R")
    check(p_point["available"] is True, "verid=0.18R 明确点名才允许同线换后缀",
          str(p_point))


def test_daemon_text():
    from me import update as upd

    section("4. daemon.bat 的关键行")
    tmp = Path(tempfile.mkdtemp(prefix="me_bat_"))
    try:
        bat = upd.write_daemon(tmp, exe="MultiExtractorCLI.exe")
        text = bat.read_text(encoding="utf-8")
        check(bat.name == "daemon.bat", "文件名是 daemon.bat")
        check("chcp 65001" in text, "先切 UTF-8 代码页")
        # detached 拉起时没有控制台，timeout 会直接失败、等于没等 —— 用 ping
        check("ping -n 3" in text, "等 2 秒再动手（等主进程放开文件锁）")
        check("timeout /t" not in text, "不用 timeout（没有控制台时它直接失败）")
        check('cd /d "%~dp0"' in text, "切到脚本所在目录")
        check('tar -xf "UpdatePack.zip" -C "%~dp0."' in text,
              "用系统 tar 解压覆盖（注意 %~dp0 后的点）")
        check('if not exist "UpdatePack.zip"' in text, "包不在就先退出")
        check("if errorlevel 1" in text, "解压失败要判断")
        check('del /f /q "UpdatePack.zip"' in text, "成功后才删包")
        check('start "" "MultiExtractorCLI.exe"' in text, "解压完启动新版本")
        check("(goto) 2>nul" in text, "自删除用 (goto) 写法（不用嵌套引号）")
        check("ping -n 3" in text and text.index("ping -n 3") < text.index("tar -xf"),
              "等待在解压之前")
        check(text.index("tar -xf") < text.index("start \"\""),
              "先解压再启动")
        check(text.index("del /f /q \"UpdatePack.zip\"")
              < text.index("start \"\""), "删包在启动之前")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_sanitize_pending():
    from me import update as upd

    section("5. 启动自检用的两个函数")
    tmp = Path(tempfile.mkdtemp(prefix="me_san_"))
    try:
        check(upd.sanitize(tmp) is False, "没有 daemon.bat 时什么都不做")
        (tmp / "daemon.bat").write_text("x", encoding="utf-8")
        check(upd.sanitize(tmp) is True, "有就删掉")
        check(not (tmp / "daemon.bat").exists(), "确实删掉了")

        check(upd.pending(tmp) is None, "没有包 -> None")
        (tmp / "UpdatePack.zip").write_bytes(b"")
        check(upd.pending(tmp) is None, "0 字节的包不算（半截下载）")
        (tmp / "UpdatePack.zip").write_bytes(b"PK\x03\x04fake")
        got = upd.pending(tmp)
        check(got is not None and got.name == "UpdatePack.zip", "有包 -> 返回路径")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_network():
    from me import update as upd

    section("6. 网络：正常 / 404 / 超时")
    # 这一段要真的连本地 HTTP，先把「整个自测默认离线」的开关摘掉
    saved = os.environ.pop("ME_NO_NET", None)
    srv = Server()
    silent = SilentServer()
    try:
        srv.routes["/version.json"] = json.dumps({
            "latest_stable": "0.17R", "latest_beta": "0.99E",
            "download_url": "https://x/p.zip", "sha256": "aa"}).encode()
        info = upd.fetch(srv.url("/version.json"), 3.0)
        check(info is not None and info.latest_beta == "0.99E",
              "能从本地 HTTP 拉到清单")

        check(upd.fetch(srv.url("/nope.json"), 1.0) is None, "404 -> None")
        check(upd.fetch(silent.url(), 0.5) is None, "对方不说话 -> None")
        t0 = time.time()
        upd.fetch(silent.url(), 0.5)
        check(time.time() - t0 < 3.0, "0.5 秒超时没有被拖住")

        os.environ["ME_NO_NET"] = "1"
        check(upd.fetch(srv.url("/version.json"), 1.0) is None,
              "ME_NO_NET=1 时不联网")
        os.environ.pop("ME_NO_NET", None)
        check(upd.TIMEOUT == 3.0, "默认超时 3 秒（用户要求）")
    finally:
        srv.stop()
        silent.stop()
        if saved is not None:
            os.environ["ME_NO_NET"] = saved


def test_download_sha():
    from me import update as upd

    section("7. 下载与 SHA256 校验")
    srv = Server()
    tmp = Path(tempfile.mkdtemp(prefix="me_dl_"))
    try:
        payload = make_pack({"a.txt": b"hello"})
        srv.routes["/p.zip"] = payload
        sha = hashlib.sha256(payload).hexdigest()

        dest = tmp / "UpdatePack.zip"
        ok, err, size = upd.download(srv.url("/p.zip"), dest, sha)
        check(ok and size == len(payload), f"下载成功（{size} 字节）{err}")
        check(dest.exists(), "文件落盘")
        check(upd.sha256_file(dest) == sha, "落盘内容 sha256 对得上")

        dest2 = tmp / "bad.zip"
        bad_sha = "0" * 64
        ok2, err2, _ = upd.download(srv.url("/p.zip"), dest2, bad_sha)
        check(not ok2, "sha 不一致 -> 失败")
        check("SHA256" in err2, f"错误里点明是校验失败（{err2[:40]}）")
        check(not dest2.exists(), "不一致的包被删掉（不留坑）")
        check(not (tmp / "bad.zip.part").exists(), "也没有 .part 残留")

        ok3, err3, _ = upd.download(srv.url("/nope.zip"), tmp / "x.zip", "")
        check(not ok3 and err3, "下载失败不抛异常")
    finally:
        srv.stop()
        shutil.rmtree(tmp, ignore_errors=True)


def test_cli_flow():
    from me import update as upd

    section("8. CLI 端到端：--update（含守护进程闭环）")
    srv = Server()
    tmp = Path(tempfile.mkdtemp(prefix="me_upd_")).resolve()
    app = tmp / "app"
    app.mkdir()
    try:
        # 假的「程序目录」：一个会写探针的 main.py 充当「新版本启动后要做的事」
        (app / "main.py").write_text(
            "import pathlib\n"
            f"pathlib.Path(r'{app / 'restarted.txt'}').write_text('v2', encoding='utf-8')\n",
            encoding="utf-8")
        (app / "version.ini").write_text('[CLIVersion]\nVersion = "0.17R Beta 00094"\n',
                                         encoding="utf-8")
        (app / "old.txt").write_text("old", encoding="utf-8")

        pack = make_pack({
            "version.ini": b'[CLIVersion]\nVersion = "0.99E Beta 00099"\n',
            "new.txt": b"new file from pack",
            "main.py": ("import pathlib\n"
                        f"pathlib.Path(r'{app / 'restarted.txt'}').write_text('v2', encoding='utf-8')\n").encode(),
        })
        srv.routes["/UpdatePack.zip"] = pack
        srv.routes["/version.json"] = json.dumps({
            "latest_stable": "0.17R",
            "latest_beta": "0.99E",
            "download_url": srv.url("/UpdatePack.zip"),
            "sha256": hashlib.sha256(pack).hexdigest(),
            "changelog": "测试用更新包",
        }).encode()

        env = {"ME_UPDATE_URL": srv.url("/version.json"),
               "ME_UPDATE_DIR": str(app), "ME_NO_NET": "0"}

        # 8.1 断网/超时：静默失败
        code, out = run_cli(["--update"], {**env, "ME_NO_NET": "1"})
        check(code == 0, f"断网时退出码 0（{code}）")
        check("检查更新超时" in out, "断网时留一行 [warn]", out[-200:])

        # 8.2 拒绝更新：什么都不做
        code, out = run_cli(["--update"], env, stdin_text="N\n")
        check(code == 0, f"拒绝时退出码 0（{code}）")
        check("已取消更新" in out, "明确说已取消", out[-200:])
        check(not (app / "UpdatePack.zip").exists(), "拒绝时没下载")

        # 8.3 完整闭环：下载 -> 校验 -> 写 bat -> 拉起 -> 退出 -> 解压覆盖 -> 重启
        code, out = run_cli(["--update", "-y"], env)
        check(code == 0, f"--update -y 退出码 0（{code}）\n{out[-500:]}")
        check("有公测版可用" in out or "发现新版本" in out, "提示有公测版",
              out[:400])
        check("SHA256 校验通过" in out, "报告校验通过", out[-300:])
        check("daemon.bat" in out, "报告生成了守护脚本", out[-300:])
        check((app / "daemon.bat").exists(), "daemon.bat 真的在盘上")

        # 等守护进程跑完（bat 里 timeout /t 2 + 解压）
        deadline = time.time() + 60
        while time.time() < deadline:
            if (app / "new.txt").exists() and not (app / "UpdatePack.zip").exists():
                break
            time.sleep(0.5)

        check((app / "new.txt").exists(), "更新包里的新文件被解压出来了")
        check("0.99E" in (app / "version.ini").read_text(encoding="utf-8"),
              "version.ini 被覆盖成新版本",
              (app / "version.ini").read_text(encoding="utf-8"))
        check(not (app / "UpdatePack.zip").exists(), "更新包被删掉")
        check((app / "old.txt").exists(), "没被更新包覆盖的旧文件还在")

        # 重启探针：daemon.bat 用 start 拉起了新 main.py
        deadline = time.time() + 30
        while time.time() < deadline and not (app / "restarted.txt").exists():
            time.sleep(0.5)
        check((app / "restarted.txt").exists(),
              "更新后新程序被拉起（探针文件）")

        # 自删除
        deadline = time.time() + 15
        while time.time() < deadline and (app / "daemon.bat").exists():
            time.sleep(0.5)
        check(not (app / "daemon.bat").exists(), "daemon.bat 跑完把自己删了")

        # 8.4 SHA 不一致：报错、删包、不生成 bat
        app2 = tmp / "app2"
        app2.mkdir()
        env2 = {**env, "ME_UPDATE_DIR": str(app2)}
        srv.routes["/version.json"] = json.dumps({
            "latest_stable": "0.17R", "latest_beta": "0.99E",
            "download_url": srv.url("/UpdatePack.zip"),
            "sha256": "0" * 64,
        }).encode()
        code, out = run_cli(["--update", "-y"], env2)
        check(code != 0, f"SHA 不一致时退出码非 0（{code}）")
        check("SHA256" in out, "说明是校验失败", out[-200:])
        check(not (app2 / "UpdatePack.zip").exists(), "坏包被删掉")
        check(not (app2 / "daemon.bat").exists(), "校验失败时绝不生成 bat")

        # 8.5 已是最新
        srv.routes["/version.json"] = json.dumps({
            "latest_stable": "0.0R", "latest_beta": "0.0E",
            "download_url": srv.url("/UpdatePack.zip"),
            "sha256": "aa"}).encode()
        code, out = run_cli(["--update", "-y"], env2)
        check(code == 0 and "已是最新" in out, f"没有新版时如实说（{code}）")
        check(not (app2 / "UpdatePack.zip").exists(), "没下载任何东西")
    finally:
        srv.stop()
        shutil.rmtree(tmp, ignore_errors=True)


def test_startup_checks():
    section("9. 启动自检：清理 bat / 发现包不阻塞")
    tmp = Path(tempfile.mkdtemp(prefix="me_start_")).resolve()
    app = tmp / "app"
    app.mkdir()
    try:
        (app / "daemon.bat").write_text("@echo off\n", encoding="utf-8")
        code, out = run_cli(["--version"], {"ME_UPDATE_DIR": str(app), "ME_NO_NET": "1"})
        check(code == 0, f"--version 退出码 0（{code}）")
        check(not (app / "daemon.bat").exists(), "残留的 daemon.bat 被清掉")
        check("已清理" in out, "并且说了一句", out[:300])

        (app / "UpdatePack.zip").write_bytes(b"PK\x03\x04fake")
        code, out = run_cli(["--version"], {"ME_UPDATE_DIR": str(app), "ME_NO_NET": "1"})
        check(code == 0, f"有包时 --version 仍正常退出（{code}）")
        check("检测到已下载的更新包" in out, "声明发现了更新包", out[:400])
        check("非交互" in out, "非交互模式不询问（不挂死脚本）", out[:400])
        check((app / "UpdatePack.zip").exists(), "不询问时保留包")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    print("=" * 74)
    print("  在线更新（Self-Updater）")
    print("=" * 74)

    tmp_local = os.environ.get("LOCALAPPDATA")
    os.environ["ME_NO_NET"] = "1"

    test_version_logic()
    test_json()
    test_choose()
    test_daemon_text()
    test_sanitize_pending()
    test_network()
    test_download_sha()
    test_cli_flow()
    test_startup_checks()

    if tmp_local is None:
        os.environ.pop("LOCALAPPDATA", None)
    else:
        os.environ["LOCALAPPDATA"] = tmp_local

    print("\n" + "=" * 74)
    if FAIL:
        print(f"  失败 {FAIL} 项，通过 {PASS} 项")
        return 1
    print(f"  全部通过（{PASS} 项）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
