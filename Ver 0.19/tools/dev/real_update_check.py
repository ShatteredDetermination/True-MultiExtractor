# -*- coding: utf-8 -*-
"""实机闭环：拿真正打出来的 exe 跑一遍「查 → 下 → 校验 → 交接 → 覆盖 → 重启」。

这个脚本不是自测（不进 run_all），是人工验收用的：
它把 dist\\MultiExtractor 复制到临时目录，起一个本地 HTTP 假远端，
然后用打包出来的 MultiExtractorCLI.exe 自己更新自己。

用法：
    python tools/dev/real_update_check.py
"""

import hashlib
import http.server
import io
import json
import os
import shutil
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DIST = ROOT / "dist" / "MultiExtractor"


def main() -> int:
    if not (DIST / "MultiExtractorCLI.exe").exists():
        print(f"没有打包产物: {DIST}")
        return 2

    tmp = Path(tempfile.mkdtemp(prefix="me_real_upd_"))
    app = tmp / "app"
    shutil.copytree(DIST, app)
    print(f"临时程序目录: {app}")
    print(f"一开始 27 个文件里的 version.ini: "
          f"{(app / 'version.ini').read_text(encoding='utf-8-sig').strip()[:60]}")

    # ---- 假远端：UpdatePack.zip 里换掉 version.ini，再放一个标记文件 ----
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("version.ini",
                    '[CLIVersion]\nVersion = "0.99E Beta 00099"\n'
                    'By = "Resolved-Patient"\n')
        zf.writestr("update_marker.txt", "updated by self-updater")
    pack = buf.getvalue()
    sha = hashlib.sha256(pack).hexdigest()

    routes = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_a):
            pass

        def do_GET(self):
            body = routes.get(self.path)
            if body is None:
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
    srv.daemon_threads = True
    port = srv.server_address[1]
    routes["/UpdatePack.zip"] = pack
    routes["/version.json"] = json.dumps({
        "latest_stable": "0.17R",
        "latest_beta": "0.99E",
        "download_url": f"http://127.0.0.1:{port}/UpdatePack.zip",
        "sha256": sha,
        "changelog": "实机更新测试",
    }).encode()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(f"假远端: http://127.0.0.1:{port}/version.json  "
          f"(包 {len(pack)} 字节, sha {sha[:16]}…)")

    env = dict(os.environ)
    env["ME_UPDATE_URL"] = f"http://127.0.0.1:{port}/version.json"
    env.pop("ME_NO_NET", None)
    env["PYTHONIOENCODING"] = "utf-8"

    exe = str(app / "MultiExtractorCLI.exe")
    t0 = time.time()
    print("\n--- 执行 --update -y ---")
    with subprocess.Popen([exe, "--update", "-y"], stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, cwd=str(app), env=env) as p:
        out, _ = p.communicate(timeout=240)
        code = p.returncode
    print(out.decode("utf-8", "replace"))
    print(f"CLI 退出码 {code}，用时 {time.time() - t0:.1f}s")
    print("（CLI 已退出，下面等 daemon.bat 干活）")

    marker = app / "update_marker.txt"
    deadline = time.time() + 90
    while time.time() < deadline:
        if marker.exists() and not (app / "UpdatePack.zip").exists():
            break
        time.sleep(0.5)

    ok = True
    for label, cond in (
        ("标记文件被解压出来", marker.exists()),
        ("UpdatePack.zip 已被删除", not (app / "UpdatePack.zip").exists()),
        ("version.ini 已被覆盖",
         "0.99E" in (app / "version.ini").read_text(encoding="utf-8")),
        ("daemon.bat 已自删除", not (app / "daemon.bat").exists()),
    ):
        print(f"  [{'ok' if cond else 'err'}] {label}")
        ok = ok and cond
    print(f"  总用时 {time.time() - t0:.1f}s")

    # ---- 重启后的新进程：它进的是交互菜单，会一直等输入，这里清掉 ----
    time.sleep(1)
    killed = 0
    for proc in subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq MultiExtractorCLI.exe", "/FO", "CSV"],
            capture_output=True, text=True).stdout.splitlines()[1:]:
        pid = proc.split(",")[1].strip('"') if "," in proc else ""
        if pid.isdigit():
            subprocess.run(["taskkill", "/PID", pid, "/F"], capture_output=True)
            killed += 1
    print(f"  清理重启后等待输入的 CLI 进程: {killed} 个")

    srv.shutdown()
    shutil.rmtree(tmp, ignore_errors=True)
    return 0 if (ok and code == 0) else 1


if __name__ == "__main__":
    raise SystemExit(main())
