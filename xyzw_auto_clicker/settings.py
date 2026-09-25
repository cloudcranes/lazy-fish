from __future__ import annotations

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
TEMPLATE_DIR = DATA_DIR / "templates"
SHOT_DIR = DATA_DIR / "screenshots"
# 删除方案的落地处：见 plans.delete_plan 的说明（不移出项目目录的软删除）
TRASH_DIR = DATA_DIR / "plans-trash"
STOP_FILE = BASE_DIR / "STOP"

for path in (DATA_DIR, TEMPLATE_DIR, SHOT_DIR, TRASH_DIR):
    path.mkdir(parents=True, exist_ok=True)


def fix_mojibake_name(name: str) -> str:
    try:
        fixed = name.encode("latin1").decode("utf-8")
    except UnicodeError:
        return name
    return fixed if fixed != name else name


def repair_template_names() -> list[tuple[str, str]]:
    repaired: list[tuple[str, str]] = []
    for path in TEMPLATE_DIR.glob("*.png"):
        fixed_name = fix_mojibake_name(path.name)
        if fixed_name == path.name:
            continue
        target = path.with_name(fixed_name)
        if target.exists():
            continue
        path.rename(target)
        repaired.append((path.name, fixed_name))
    return repaired
