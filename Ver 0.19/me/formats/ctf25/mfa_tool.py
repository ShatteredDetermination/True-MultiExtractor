# -*- coding: utf-8 -*-
"""MFA 导出的第三方通道兜底 —— V0.19 起。

自研 MFA 写入器还在推进（见 ``mfa.py``）。在那之前，用户手里可能已经有能导
``.mfa`` 的现成工具；这条路单独放一个模块，边界写得清清楚楚：

* **不猜工具的命令行**。参数模板由用户给（``ME_MFA_TOOL_ARGS``），用 ``{in}`` 与
  ``{out}`` 占位，默认就是「输入文件 输出文件」两个参数。
* **不认账就不算成功**：只有当退出码是 0、输出文件存在、而且开头真的是 ``MFU2``
  时才算成功。工具悄悄写了个别的文件、或者半路失败，都要如实报错。
* 工具找不到时说清楚「没装 / 没给路径」，不假装能导。

工具怎么指定（优先级从高到低）：

1. 调用时传 ``tool=``；
2. 环境变量 ``ME_MFA_TOOL``；
3. ``PATH`` 里的 ``ctfak-mfa`` / ``mfa-export``。

参数模板用 ``shlex`` 拆，跨平台都能用引号。
"""

import os
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

MAGIC = b"MFU2"
ENV_TOOL = "ME_MFA_TOOL"
ENV_ARGS = "ME_MFA_TOOL_ARGS"
PATH_NAMES = ("ctfak-mfa", "mfa-export")
DEFAULT_ARGS = "{in} {out}"
TIMEOUT = 900
LOG_TAIL = 600


@dataclass
class ToolInfo:
    path: str = ""
    source: str = ""          # tool / env / path

    def __str__(self) -> str:
        return f"{self.path}（{self.source}）"


@dataclass
class MfaToolResult:
    ok: bool = False
    reason: str = ""
    tool: str = ""
    command: List[str] = field(default_factory=list)
    exit_code: Optional[int] = None
    stdout: str = ""
    stderr: str = ""
    out_path: str = ""
    out_size: int = 0

    def summary(self) -> str:
        if self.ok:
            return f"导出成功：{self.out_path}（{self.out_size} B）"
        return f"导出没成功：{self.reason}"


def _which(name: str) -> str:
    from shutil import which
    return which(name) or ""


def find_tool(tool: str = "", env: Optional[dict] = None) -> Optional[ToolInfo]:
    """找一个可用工具：显式路径 → ME_MFA_TOOL → PATH。找不到返回 None。"""
    env = os.environ if env is None else env
    if tool:
        p = Path(tool)
        if p.is_file():
            return ToolInfo(str(p.resolve()), "tool")
        got = _which(tool)
        if got:
            return ToolInfo(got, "tool")
        return None
    want = env.get(ENV_TOOL, "").strip()
    if want:
        p = Path(want)
        if p.is_file():
            return ToolInfo(str(p.resolve()), "env")
        got = _which(want)
        if got:
            return ToolInfo(got, "env")
        return None
    for name in PATH_NAMES:
        got = _which(name)
        if got:
            return ToolInfo(got, "path")
    return None


def build_command(tool: ToolInfo, src, out, args: str = "") -> List[str]:
    """把参数模板拆成命令行；``{in}`` / ``{out}`` 替换成绝对路径。"""
    tpl = args or os.environ.get(ENV_ARGS, "") or DEFAULT_ARGS
    parts = shlex.split(tpl)
    if not any("{in}" in p for p in parts):
        raise ValueError("参数模板里没有 {in}")
    if not any("{out}" in p for p in parts):
        raise ValueError("参数模板里没有 {out}")
    return [tool.path] + [p.replace("{in}", str(Path(src).resolve()))
                          .replace("{out}", str(Path(out).resolve()))
                          for p in parts]


def convert(src, out, tool: str = "", args: str = "",
            timeout: int = TIMEOUT, env: Optional[dict] = None,
            runner=None) -> MfaToolResult:
    """调第三方工具把 ``src`` 导成 ``out``；每一步失败都落在返回值里。

    ``runner`` 只为自测注入（默认 ``subprocess.run``），线上不传。
    """
    res = MfaToolResult()
    if not Path(src).is_file():
        res.reason = f"输入文件不存在：{src}"
        return res
    info = find_tool(tool, env)
    if info is None:
        res.reason = (f"没找到能导 mfa 的外部工具（{ENV_TOOL} 没设，"
                      f"PATH 里也没有 {' / '.join(PATH_NAMES)}）")
        return res
    res.tool = str(info)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        res.command = build_command(info, src, out, args)
    except ValueError as exc:
        res.reason = f"参数模板不对：{exc}"
        return res
    if out.exists():
        out.unlink()          # 先清掉旧文件，免得把上一次的产物当成本次成功
    run = runner or subprocess.run
    try:
        cp = run(res.command, capture_output=True, timeout=timeout,
                 cwd=str(out.parent))
    except subprocess.TimeoutExpired:
        res.reason = f"工具超过 {timeout} 秒没结束"
        return res
    except OSError as exc:
        res.reason = f"工具起不来：{exc}"
        return res
    res.exit_code = cp.returncode
    res.stdout = (cp.stdout or b"").decode("utf-8", "replace")[-LOG_TAIL:]
    res.stderr = (cp.stderr or b"").decode("utf-8", "replace")[-LOG_TAIL:]
    if cp.returncode != 0:
        res.reason = f"工具退出码 {cp.returncode}"
        return res
    if not out.is_file():
        res.reason = "工具退出码 0，但没生成输出文件"
        return res
    res.out_path = str(out)
    res.out_size = out.stat().st_size
    with open(out, "rb") as fh:
        head = fh.read(4)
    if head != MAGIC:
        res.reason = (f"输出不是 MFA（开头 {head!r}，应为 'MFU2'），"
                      f"可能是别的格式")
        return res
    res.ok = True
    res.reason = "ok"
    return res
