#!/usr/bin/env python3
"""Environment checker for mcp-server-builder.

Verifies the toolchain needed to build MCP servers:
  Python route: python >= 3.10, uv (recommended), mcp SDK version
  TypeScript route: node >= 20, npm, npx, @modelcontextprotocol/server version

Stdlib only. Exit code 0 = all good, 1 = missing pieces (details printed).

Usage:
  python check_env.py --lang python
  python check_env.py --lang typescript
  python check_env.py                # checks both
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import urllib.request

PY_MIN = (3, 10)
NODE_MIN = (20, 0)


def run(cmd: list[str]) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return p.returncode, (p.stdout or p.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)


def parse_version(text: str) -> tuple[int, ...]:
    digits = []
    for part in text.split():
        digits = [d for d in part.split(".") if d.isdigit()]
        if digits:
            break
    return tuple(int(d) for d in digits) if digits else (0,)


def check(label: str, ok: bool, detail: str, problems: list[str]) -> None:
    mark = "OK  " if ok else "MISS"
    print(f"[{mark}] {label}: {detail}")
    if not ok:
        problems.append(f"{label}: {detail}")


def check_python(problems: list[str]) -> None:
    v = sys.version_info
    check("python", v >= PY_MIN, f"{v.major}.{v.minor}.{v.micro} (need >= 3.10)", problems)

    if shutil.which("uv"):
        rc, out = run(["uv", "--version"])
        check("uv", rc == 0, out.splitlines()[0] if out else "?", problems)
    else:
        check("uv", False, "not found — install: https://docs.astral.sh/uv/getting-started/installation/", problems)

    rc, out = run([sys.executable, "-m", "pip", "show", "mcp"])
    if rc == 0:
        ver = next((l.split(": ", 1)[1] for l in out.splitlines() if l.startswith("Version")), "?")
        major = parse_version(ver)[:1]
        check("mcp SDK", major >= (2,), f"{ver} installed (v2 line is current; v1 is legacy)", problems)
    else:
        check("mcp SDK", False, "not installed for this interpreter — per project: uv add 'mcp[cli]'", problems)


def check_typescript(problems: list[str]) -> None:
    if shutil.which("node"):
        rc, out = run(["node", "--version"])
        check("node", rc == 0 and parse_version(out) >= NODE_MIN,
              f"{out} (need >= 20)", problems)
    else:
        check("node", False, "not found — install Node.js >= 20: https://nodejs.org/", problems)

    for tool in ("npm", "npx"):
        if shutil.which(tool):
            check(tool, True, shutil.which(tool) or "", problems)
        else:
            check(tool, False, "not found (ships with Node.js)", problems)


def check_latest(problems: list[str]) -> dict[str, str]:
    """Best-effort latest-version lookup; offline is fine (informational)."""
    latest: dict[str, str] = {}
    try:
        with urllib.request.urlopen("https://pypi.org/pypi/mcp/json", timeout=10) as r:
            latest["mcp (PyPI latest)"] = json.load(r)["info"]["version"]
    except Exception as e:
        print(f"[info] PyPI lookup skipped: {e}")
    rc, out = run(["npm", "view", "@modelcontextprotocol/server", "version"])
    if rc == 0:
        latest["@modelcontextprotocol/server (npm latest)"] = out
    else:
        print("[info] npm lookup skipped (npm missing or offline)")
    for k, v in latest.items():
        print(f"[info] {k}: {v}")
    return latest


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--lang", choices=["python", "typescript", "both"], default="both")
    ap.add_argument("--latest", action="store_true", help="also query latest SDK versions (network)")
    args = ap.parse_args()

    problems: list[str] = []
    if args.lang in ("python", "both"):
        check_python(problems)
    if args.lang in ("typescript", "both"):
        check_typescript(problems)
    if args.latest:
        check_latest(problems)

    if problems:
        print("\nRESULT: INCOMPLETE — fix the items marked MISS above.")
        return 1
    print("\nRESULT: OK — toolchain ready.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
