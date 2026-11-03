from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import release_lib as rl  # tools/ is on pythonpath


def _tree(root: Path, files: dict[str, bytes]) -> Path:
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return root


def test_scan_tree_hashes_every_file(tmp_path: Path) -> None:
    root = _tree(tmp_path / "app", {"a.exe": b"exe", "_internal/b.dll": b"dll"})
    entries = rl.scan_tree(root)
    assert sorted(entries) == ["_internal/b.dll", "a.exe"]
    assert entries["a.exe"].size == 3
    assert entries["a.exe"].sha256 == hashlib.sha256(b"exe").hexdigest()


def test_files_manifest_roundtrip(tmp_path: Path) -> None:
    entries = rl.scan_tree(_tree(tmp_path / "app", {"x.txt": b"1"}))
    doc = rl.files_manifest("VideoTutor", "1.2.3", entries)
    version, parsed = rl.parse_files_manifest(doc, "VideoTutor")
    assert version == "1.2.3"
    assert parsed == entries


@pytest.mark.parametrize(
    ("patch", "message"),
    [
        ({"schema": 99}, "schema"),
        ({"product": "Other"}, "not 'VideoTutor'"),
        ({"version": "1.2"}, "bad version"),
    ],
)
def test_files_manifest_rejects_foreign_documents(patch: dict[str, object], message: str) -> None:
    doc = {**rl.files_manifest("VideoTutor", "1.0.0", {}), **patch}
    with pytest.raises(ValueError, match=message):
        rl.parse_files_manifest(doc, "VideoTutor")


def test_diff_finds_added_changed_and_removed(tmp_path: Path) -> None:
    old = rl.scan_tree(_tree(tmp_path / "old", {"same": b"s", "mod": b"1", "gone": b"g"}))
    new = rl.scan_tree(_tree(tmp_path / "new", {"same": b"s", "mod": b"2", "added": b"a"}))
    delta = rl.diff(old, new)
    assert delta.changed == ("added", "mod")
    assert delta.removed == ("gone",)
    assert not delta.empty
    assert rl.diff(old, old).empty


def test_delta_iss_escapes_braces_and_maps_folders() -> None:
    delta = rl.Delta(
        changed=("VideoTutor.exe", "_internal/{odd}.pyd"), removed=("_internal/old.dll",)
    )
    iss = rl.render_delta_iss(delta)
    assert 'Source: "{#DistDir}\\VideoTutor.exe"; DestDir: "{app}";' in iss
    assert 'Source: "{#DistDir}\\_internal\\{{odd}.pyd"; DestDir: "{app}\\_internal";' in iss
    assert 'Type: files; Name: "{app}\\_internal\\old.dll"' in iss


def test_version_tuple_orders_numerically() -> None:
    assert rl.version_tuple("0.10.0") > rl.version_tuple("0.9.9")
    with pytest.raises(ValueError, match=r"X\.Y\.Z"):
        rl.version_tuple("1.0")


def test_release_manifest_and_checksums(tmp_path: Path) -> None:
    pkg = tmp_path / "VideoTutor-Setup-1.0.0.exe"
    pkg.write_bytes(b"installer")
    entry = rl.package_entry("full", pkg)
    assert entry == {
        "kind": "full",
        "file": pkg.name,
        "size": 9,
        "sha256": hashlib.sha256(b"installer").hexdigest(),
    }
    doc = rl.release_manifest("VideoTutor", "1.0.0", "abc", [entry])
    assert doc["schema"] == rl.RELEASE_SCHEMA and doc["packages"] == [entry]
    assert rl.sha256sums([entry]) == f"{entry['sha256']}  {pkg.name}\n"
    out = tmp_path / "m.json"
    rl.write_json(out, doc)
    sums = tmp_path / "SHA256SUMS.txt"
    rl.write_text_lf(sums, rl.sha256sums([entry]))
    for f in (out, sums):
        data = f.read_bytes()
        assert data.endswith(b"\n"), f.name
        assert b"\r" not in data, f"{f.name} must use LF so `sha256sum -c` works"
