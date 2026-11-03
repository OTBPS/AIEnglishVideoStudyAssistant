"""Every quality gate, in the order that fails fastest. Same script locally and in CI.

    python tools/check.py            all gates
    python tools/check.py --fix      apply ruff fixes + formatting first

Exit code is non-zero if any gate fails; all gates run so you see everything.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
VENV_SCRIPTS = BACKEND / ".venv" / ("Scripts" if sys.platform == "win32" else "bin")
PY_TARGETS = ["videotutor", "tests", "../tools"]


def tool(name: str) -> str:
    local = VENV_SCRIPTS / (name + (".exe" if sys.platform == "win32" else ""))
    return str(local) if local.exists() else name


def gates(fix: bool) -> list[tuple[str, list[str], Path]]:
    py = tool("python")
    out: list[tuple[str, list[str], Path]] = []
    if fix:
        out += [
            ("ruff fix", [tool("ruff"), "check", "--fix", *PY_TARGETS], BACKEND),
            ("ruff format", [tool("ruff"), "format", *PY_TARGETS], BACKEND),
        ]
    out += [
        ("product sync", [py, str(ROOT / "tools" / "sync_product.py"), "--check"], ROOT),
        ("ruff lint", [tool("ruff"), "check", *PY_TARGETS], BACKEND),
        ("ruff format", [tool("ruff"), "format", "--check", *PY_TARGETS], BACKEND),
        ("mypy", [tool("mypy"), "videotutor", "../tools"], BACKEND),
        ("import layers", [tool("lint-imports")], BACKEND),
        ("pytest + coverage", [tool("pytest"), "--cov", "--cov-report=term"], BACKEND),
    ]
    node = shutil.which("node")
    if node:
        for js in sorted((ROOT / "extension" / "src").glob("*.js")):
            out.append((f"node --check {js.name}", [node, "--check", str(js)], ROOT))
    else:
        print("note: node not found, extension JavaScript not syntax-checked")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fix", action="store_true")
    args = ap.parse_args(argv)

    failed: list[str] = []
    for name, cmd, cwd in gates(args.fix):
        print(f"\n=== {name}", flush=True)
        if subprocess.run(cmd, cwd=cwd, check=False).returncode != 0:
            failed.append(name)
    print("\n" + ("FAILED: " + ", ".join(failed) if failed else "all checks passed"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
