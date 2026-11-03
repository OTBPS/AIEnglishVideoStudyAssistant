"""Build the release: checks -> exe -> smoke test -> installers -> manifests.

    python tools/build.py                          full pipeline
    python tools/build.py --skip-checks            when check.py just ran
    python tools/build.py --previous-files F.json  also build a delta installer
                                                   from the release F describes
    python tools/build.py --no-installer           stop after the smoke-tested exe

Every step fails loudly; a release directory only exists if all of it worked.
Output: dist/<Name>/ (the program) and dist/release/ (what gets published).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path
from typing import Any

import release_lib as rl

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
INSTALLER = ROOT / "installer"
DIST = ROOT / "dist"
BUILD = ROOT / "build"
RELEASE = DIST / "release"
VENV_PY = BACKEND / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
SMOKE_TIMEOUT_S = 90


class BuildError(RuntimeError):
    pass


def step(title: str) -> None:
    print(f"\n=== {title}", flush=True)


def run(cmd: list[str], *, cwd: Path = ROOT, env: dict[str, str] | None = None) -> None:
    if subprocess.run(cmd, cwd=cwd, env=env, check=False).returncode != 0:
        raise BuildError(f"command failed: {' '.join(cmd)}")


def load_product() -> dict[str, Any]:
    with (ROOT / "product.toml").open("rb") as f:
        return tomllib.load(f)


def find_iscc() -> Path:
    candidates = [os.environ.get("ISCC", "")]
    for base in ("LOCALAPPDATA", "ProgramFiles(x86)", "ProgramFiles"):
        root = os.environ.get(base)
        if root:
            sub = "Programs/Inno Setup 6" if base == "LOCALAPPDATA" else "Inno Setup 6"
            candidates.append(str(Path(root) / sub / "ISCC.exe"))
    candidates.append(shutil.which("ISCC") or "")
    for c in candidates:
        if c and Path(c).is_file():
            return Path(c)
    raise BuildError("Inno Setup 6 (ISCC.exe) not found; install it or set ISCC=path")


def git_commit() -> str:
    out = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False
    )
    return out.stdout.strip() or "unknown"


# ------------------------------------------------------------------ steps
def build_exe(name: str) -> Path:
    step("PyInstaller")
    shutil.rmtree(DIST / name, ignore_errors=True)
    shutil.rmtree(BUILD / "pyinstaller", ignore_errors=True)
    run(
        [
            str(VENV_PY),
            "-m",
            "PyInstaller",
            str(INSTALLER / "videotutor.spec"),
            "--noconfirm",
            "--clean",
            "--log-level",
            "WARN",
            "--distpath",
            str(DIST),
            "--workpath",
            str(BUILD / "pyinstaller"),
        ]
    )
    app_dir = DIST / name
    step("copy browser extension")
    shutil.copytree(
        ROOT / "extension",
        app_dir / "extension",
        ignore=shutil.ignore_patterns("node_modules", "dist", "*.md"),
    )
    return app_dir


def smoke_test(app_dir: Path, name: str) -> None:
    """Run the packaged exe headless against a throwaway home directory."""
    step("smoke test packaged exe")
    with tempfile.TemporaryDirectory(prefix="vt-smoke-") as home:
        env = {**os.environ, "VIDEOTUTOR_HOME": home}
        try:
            code = subprocess.run(
                [str(app_dir / f"{name}.exe"), "--smoke-test"],
                env=env,
                timeout=SMOKE_TIMEOUT_S,
                check=False,
            ).returncode
        except subprocess.TimeoutExpired as exc:
            raise BuildError(f"smoke test hung for {SMOKE_TIMEOUT_S}s") from exc
        log = Path(home) / "logs" / "app.log"
        if code != 0:
            for f in (log, Path(home) / "logs" / "startup_error.txt"):
                if f.exists():
                    print(f.read_text(encoding="utf-8", errors="replace"))
            raise BuildError(f"smoke test failed with exit code {code}")
    print("smoke test passed")


def iscc(product: dict[str, Any], app_dir: Path, extra: list[str]) -> None:
    p, r = product["product"], product["runtime"]
    defines = {
        "AppName": p["name"],
        "AppDisplayName": p["display_name"],
        "AppVersion": p["version"],
        "AppPublisher": p["publisher"],
        "AppRepository": p["repository"],
        "AppGuid": p["app_id"].strip("{}"),
        "AppMutex": r["single_instance_mutex"],
        "DistDir": str(app_dir),
        "OutputDir": str(RELEASE),
        "IconFile": str(BACKEND / "videotutor" / "assets" / "app.ico"),
    }
    args = [f"/D{k}={v}" for k, v in defines.items()]
    run([str(find_iscc()), "/Q", *args, *extra, str(INSTALLER / "videotutor.iss")])


def build_delta(
    product: dict[str, Any], app_dir: Path, current: dict[str, rl.FileEntry], previous_files: Path
) -> dict[str, Any] | None:
    p = product["product"]
    step(f"delta installer from {previous_files.name}")
    doc = json.loads(previous_files.read_text(encoding="utf-8"))
    old_version, old_entries = rl.parse_files_manifest(doc, p["name"])
    if rl.version_tuple(old_version) >= rl.version_tuple(p["version"]):
        raise BuildError(f"previous version {old_version} is not older than {p['version']}")
    delta = rl.diff(old_entries, current)
    if delta.empty:
        print("no file changed; no delta package")
        return None
    include = BUILD / "delta.iss"
    include.parent.mkdir(parents=True, exist_ok=True)
    include.write_text(rl.render_delta_iss(delta), encoding="utf-8")
    iscc(product, app_dir, ["/DDelta", f"/DFromVersion={old_version}", f"/DDeltaInclude={include}"])
    out = RELEASE / f"{p['name']}-Update-{old_version}-to-{p['version']}.exe"
    print(f"{len(delta.changed)} changed, {len(delta.removed)} removed")
    return rl.package_entry(
        "delta",
        out,
        from_version=old_version,
        changed_files=len(delta.changed),
        removed_files=len(delta.removed),
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-checks", action="store_true")
    ap.add_argument("--no-installer", action="store_true")
    ap.add_argument("--previous-files", type=Path)
    args = ap.parse_args(argv)

    product = load_product()
    p = product["product"]
    name, version = p["name"], p["version"]
    try:
        if not args.skip_checks:
            step("quality gates")
            run([str(VENV_PY), str(ROOT / "tools" / "check.py")])
        else:
            run([str(VENV_PY), str(ROOT / "tools" / "sync_product.py"), "--check"])

        app_dir = build_exe(name)
        smoke_test(app_dir, name)
        if args.no_installer:
            return 0

        shutil.rmtree(RELEASE, ignore_errors=True)
        RELEASE.mkdir(parents=True)

        step("files manifest")
        entries = rl.scan_tree(app_dir)
        files_json = RELEASE / f"{name}-{version}-files.json"
        rl.write_json(files_json, rl.files_manifest(name, version, entries))
        print(f"{len(entries)} files, {sum(e.size for e in entries.values()) / 1e6:.1f} MB")

        step("full installer")
        iscc(product, app_dir, [])
        packages = [rl.package_entry("full", RELEASE / f"{name}-Setup-{version}.exe")]

        if args.previous_files:
            delta = build_delta(product, app_dir, entries, args.previous_files)
            if delta:
                packages.append(delta)
        packages.append(rl.package_entry("files-manifest", files_json))

        step("release manifest")
        rl.write_json(
            RELEASE / "release-manifest.json",
            rl.release_manifest(name, version, git_commit(), packages),
        )
        (RELEASE / "SHA256SUMS.txt").write_text(rl.sha256sums(packages), encoding="utf-8")
        for pkg in packages:
            print(
                f"  {pkg['kind']:<15} {pkg['file']:<45} {pkg['size'] / 1e6:7.1f} MB  "
                f"{pkg['sha256'][:16]}..."
            )
    except BuildError as exc:
        print(f"\nBUILD FAILED: {exc}", file=sys.stderr)
        return 1
    print(f"\nbuild OK: {RELEASE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
