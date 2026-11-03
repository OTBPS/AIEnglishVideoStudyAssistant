"""End-to-end installer verification on a real Windows machine (CI or local).

    python tools/verify_install.py                       uses dist/release/
    python tools/verify_install.py --previous-setup OLD.exe
                                                         also: OLD -> delta -> files == current

Scenarios, in order:
  1. fresh silent install (no autostart task), registry + files + installed smoke test
  2. reinstall the same version (repair/upgrade path)
  3. delta package built for another base version is refused
  4. downgrade is refused; /ALLOWDOWNGRADE overrides it
  5. silent uninstall removes the program and its registry entry, keeps user data
  6. (optional) previous full -> delta -> installed files match this release

It refuses to run if the product is already installed for this user, so it
can never clobber a real installation. Everything it creates is removed.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import tomllib
from pathlib import Path
from typing import Any

import release_lib as rl

ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / "dist" / "release"
SILENT = ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/TASKS="]
TIMEOUT_S = 180

if sys.platform != "win32":
    sys.exit("verify_install.py needs Windows")

import winreg  # noqa: E402 -- Windows-only, after the platform guard


class VerifyError(AssertionError):
    pass


def product() -> dict[str, Any]:
    with (ROOT / "product.toml").open("rb") as f:
        return tomllib.load(f)


def uninstall_key(p: dict[str, Any]) -> str:
    return rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{p['product']['app_id']}_is1"


def reg_get(key: str, value: str) -> str | None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            data, _ = winreg.QueryValueEx(k, value)
            return str(data)
    except OSError:
        return None


def reg_set(key: str, value: str, data: str) -> None:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, value, 0, winreg.REG_SZ, data)


def run_setup(exe: Path, app_dir: Path, log: Path, *extra: str) -> int:
    cmd = [str(exe), *SILENT, f"/DIR={app_dir}", f"/LOG={log}", *extra]
    return subprocess.run(cmd, timeout=TIMEOUT_S, check=False).returncode


def expect(cond: bool, message: str) -> None:
    if not cond:
        raise VerifyError(message)
    print(f"  ok  {message}")


def wait_until(cond: Any, what: str, timeout: float = 60) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            print(f"  ok  {what}")
            return
        time.sleep(0.5)
    raise VerifyError(f"timed out waiting for: {what}")


def smoke(exe: Path) -> int:
    with tempfile.TemporaryDirectory(prefix="vt-verify-home-") as home:
        env = {**os.environ, "VIDEOTUTOR_HOME": home}
        return subprocess.run(
            [str(exe), "--smoke-test"], env=env, timeout=90, check=False
        ).returncode


def installed_files_match(app_dir: Path, files_json: Path, name: str) -> bool:
    _, expected = rl.parse_files_manifest(json.loads(files_json.read_text(encoding="utf-8")), name)
    actual = rl.scan_tree(app_dir)
    actual = {k: v for k, v in actual.items() if not k.startswith("unins")}
    return actual == expected


def uninstall(app_dir: Path, key: str) -> None:
    uninstaller = app_dir / "unins000.exe"
    if uninstaller.exists():
        subprocess.run(
            [str(uninstaller), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"],
            timeout=TIMEOUT_S,
            check=False,
        )
    wait_until(lambda: reg_get(key, "DisplayVersion") is None, "uninstall entry removed")
    wait_until(lambda: not app_dir.exists(), "program folder removed")


def main(argv: list[str] | None = None) -> int:  # noqa: PLR0915 -- a linear test script
    ap = argparse.ArgumentParser()
    ap.add_argument("--release-dir", type=Path, default=RELEASE)
    ap.add_argument("--previous-setup", type=Path)
    args = ap.parse_args(argv)

    p = product()
    name, version = p["product"]["name"], p["product"]["version"]
    key = uninstall_key(p)
    full = args.release_dir / f"{name}-Setup-{version}.exe"
    files_json = args.release_dir / f"{name}-{version}-files.json"
    manifest = json.loads((args.release_dir / "release-manifest.json").read_text("utf-8"))
    deltas = [args.release_dir / pk["file"] for pk in manifest["packages"] if pk["kind"] == "delta"]

    if reg_get(key, "DisplayVersion") is not None:
        print(f"{name} is installed for this user; refusing to touch a real installation.")
        return 2

    # Real data dir: only touched if it does not exist yet, and cleaned up after.
    data_home = Path(os.environ["LOCALAPPDATA"]) / name
    created_home = not data_home.exists()

    work = Path(tempfile.mkdtemp(prefix="vt-verify-"))
    app_dir = work / "app"
    try:
        print("1. fresh install")
        for pk in manifest["packages"]:
            f = args.release_dir / pk["file"]
            expect(rl.sha256_file(f) == pk["sha256"], f"sha256 of {f.name} matches manifest")
        expect(run_setup(full, app_dir, work / "1.log") == 0, "full installer exits 0")
        expect(reg_get(key, "DisplayVersion") == version, f"registry reports {version}")
        expect(installed_files_match(app_dir, files_json, name), "installed files == manifest")
        expect(smoke(app_dir / f"{name}.exe") == 0, "installed exe passes smoke test")
        startup = Path(os.environ["APPDATA"]) / r"Microsoft\Windows\Start Menu\Programs\Startup"
        expect(not (startup / f"{name}.lnk").exists(), "no autostart when the task is off")

        print("2. reinstall same version (repair)")
        expect(run_setup(full, app_dir, work / "2.log") == 0, "reinstall exits 0")
        expect(installed_files_match(app_dir, files_json, name), "files intact after repair")

        print("3. delta for another base is refused")
        for delta in deltas:
            before = rl.scan_tree(app_dir)
            code = run_setup(delta, app_dir, work / "3.log")
            expect(code != 0, f"{delta.name} refused on {version} (exit {code})")
            expect(rl.scan_tree(app_dir) == before, "program files untouched")

        print("4. downgrade guard")
        reg_set(key, "DisplayVersion", "99.0.0")
        code = run_setup(full, app_dir, work / "4a.log")
        expect(code != 0, f"installing {version} over 99.0.0 is refused (exit {code})")
        expect(
            run_setup(full, app_dir, work / "4b.log", "/ALLOWDOWNGRADE") == 0,
            "/ALLOWDOWNGRADE overrides the guard",
        )
        expect(reg_get(key, "DisplayVersion") == version, "registry back to this version")

        print("5. uninstall keeps user data")
        marker = data_home / "verify-marker.txt"
        if created_home:
            data_home.mkdir(parents=True, exist_ok=True)
            marker.write_text("keep me", encoding="utf-8")
        uninstall(app_dir, key)
        if created_home:
            expect(marker.exists(), "silent uninstall kept the data folder")

        if args.previous_setup:
            print("6. previous release -> delta -> this release")
            matching = [d for d in deltas if "-Update-" in d.name]
            expect(bool(matching), "a delta package exists")
            expect(
                run_setup(args.previous_setup, app_dir, work / "6a.log") == 0,
                "previous release installs",
            )
            expect(run_setup(matching[0], app_dir, work / "6b.log") == 0, "delta applies")
            expect(reg_get(key, "DisplayVersion") == version, f"registry reports {version}")
            expect(
                installed_files_match(app_dir, files_json, name),
                "files after delta == full install of this release",
            )
            uninstall(app_dir, key)
    except VerifyError as exc:
        print(f"\nVERIFY FAILED: {exc}\nlogs: {work}", file=sys.stderr)
        if reg_get(key, "DisplayVersion") is not None:
            uninstall(app_dir, key)
        return 1
    finally:
        if created_home and data_home.exists():
            for f in sorted(data_home.rglob("*"), reverse=True):
                f.unlink() if f.is_file() else f.rmdir()
            data_home.rmdir()
    print("\ninstaller verification passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
