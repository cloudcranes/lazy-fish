"""Gate pyright strict output against pyright-baseline.json.

pyright 自 1.1 起未原生支持 baseline.json；本脚本：
1. 调 pyright --outputjson 收当前诊断
2. 与 pyright-baseline.json 里 (file, line, rule) 三元组对比
3. 仅打印"基线之外的"新诊断；存在则退出码 1，否则 0

Make/CI 入口：`python scripts/pyright_check.py`。
基线再生成：`python scripts/pyright_baseline_gen.py`。
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYRIGHT = shutil.which("pyright") or "pyright"
BASELINE = ROOT / "pyright-baseline.json"


def _decode(stdout: bytes) -> bytes:
    # pyright on Windows pipes UTF-16 LE with BOM; strip + re-encode UTF-8.
    if stdout.startswith(b"\xff\xfe"):
        return stdout.decode("utf-16-le").encode("utf-8")
    return stdout


def _file_key(raw_path: str) -> str:
    """Normalize pyright's absolute path into repo-relative POSIX style."""
    p = Path(raw_path).resolve()
    try:
        rel = p.relative_to(ROOT)
    except ValueError:
        return raw_path.replace("\\", "/")
    return str(rel).replace("\\", "/")


def main() -> int:
    if not BASELINE.exists():
        print(f"missing baseline: {BASELINE}", file=sys.stderr)
        return 2

    p = subprocess.run(
        [PYRIGHT, "--pythonversion", "3.10", "--outputjson", "xyzw_auto_clicker"],
        cwd=str(ROOT),
        capture_output=True,
    )
    raw = _decode(p.stdout)
    payload = json.loads(raw)
    cur = payload.get("generalDiagnostics", [])

    base = json.loads(BASELINE.read_text(encoding="utf-8"))
    seen = {
        (d["file"], d["line"], d["rule"])
        for d in base.get("diagnostics", [])
    }

    new: list[dict[str, object]] = []
    for d in cur:
        key = (_file_key(str(d["file"])), d["range"]["start"]["line"] + 1, d.get("rule", ""))
        if key in seen:
            continue
        new.append(
            {
                "file": key[0],
                "line": key[1],
                "rule": key[2],
                "message": (d.get("message") or "")[:140],
            }
        )

    print(f"pyright: {len(cur)} total, {len(new)} new beyond baseline")
    for d in new:
        print(f"  {d['file']}:{d['line']} {d['rule']} {d['message']}")
    return 1 if new else 0


if __name__ == "__main__":
    sys.exit(main())
