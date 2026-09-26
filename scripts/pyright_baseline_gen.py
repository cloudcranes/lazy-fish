"""Regenerate pyright-baseline.json from current strict-mode diagnostics.

跑完应当 commit：新基线只覆盖"现存的"诊断，不新增任何问题。

用法：`python scripts/pyright_baseline_gen.py`。
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYRIGHT = shutil.which("pyright") or "pyright"


def _decode(stdout: bytes) -> bytes:
    if stdout.startswith(b"\xff\xfe"):
        return stdout.decode("utf-16-le").encode("utf-8")
    return stdout


def _file_key(raw_path: str) -> str:
    p = Path(raw_path).resolve()
    try:
        rel = p.relative_to(ROOT)
    except ValueError:
        return raw_path.replace("\\", "/")
    return str(rel).replace("\\", "/")


def main() -> int:
    p = subprocess.run(
        [PYRIGHT, "--pythonversion", "3.10", "--outputjson", "xyzw_auto_clicker"],
        cwd=str(ROOT),
        capture_output=True,
    )
    raw = _decode(p.stdout)
    payload = json.loads(raw)
    diags: list[dict[str, object]] = []
    for d in payload.get("generalDiagnostics", []):
        diags.append(
            {
                "file": _file_key(str(d["file"])),
                "line": d["range"]["start"]["line"] + 1,
                "rule": d.get("rule", ""),
                "message": (d.get("message") or "")[:160],
            }
        )
    out = {
        "pythonVersion": "3.10",
        "strict": ["xyzw_auto_clicker"],
        "diagnostics": diags,
    }
    target = ROOT / "pyright-baseline.json"
    target.write_text(
        json.dumps(out, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"baseline written: {len(diags)} diagnostics")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
