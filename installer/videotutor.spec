# PyInstaller spec -- invoked by tools/build.py, not by hand.
#
# One-folder build (not one-file): starts faster, nothing is unpacked to
# %TEMP% on every launch, and antivirus products flag it far less often.
# Product identity is read from product.toml, never written here.
import tomllib
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs
from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo,
    StringFileInfo,
    StringStruct,
    StringTable,
    VarFileInfo,
    VarStruct,
    VSVersionInfo,
)

ROOT = Path(SPECPATH).parent  # noqa: F821 -- injected by PyInstaller
BACKEND = ROOT / "backend"
PKG = BACKEND / "videotutor"

with (ROOT / "product.toml").open("rb") as f:
    PRODUCT = tomllib.load(f)["product"]

numbers = tuple(int(x) for x in PRODUCT["version"].split(".")) + (0,)
version_info = VSVersionInfo(
    ffi=FixedFileInfo(filevers=numbers, prodvers=numbers),
    kids=[
        StringFileInfo([StringTable("080404B0", [
            StringStruct("CompanyName", PRODUCT["publisher"]),
            StringStruct("FileDescription", PRODUCT["display_name"]),
            StringStruct("FileVersion", PRODUCT["version"]),
            StringStruct("InternalName", PRODUCT["name"]),
            StringStruct("OriginalFilename", f"{PRODUCT['name']}.exe"),
            StringStruct("ProductName", PRODUCT["display_name"]),
            StringStruct("ProductVersion", PRODUCT["version"]),
        ])]),
        VarFileInfo([VarStruct("Translation", [0x0804, 1200])]),
    ],
)

a = Analysis(
    [str(BACKEND / "launcher.py")],
    pathex=[str(BACKEND)],
    datas=[
        (str(ROOT / "product.toml"), "."),
        (str(PKG / "ui" / "web"), "videotutor/ui/web"),
        (str(PKG / "assets"), "videotutor/assets"),
        # Speech recognition (run by the same exe with --asr-worker): the
        # Silero VAD model ships inside faster_whisper/assets.
        *collect_data_files("faster_whisper"),
    ],
    binaries=[
        *collect_dynamic_libs("ctranslate2"),  # ctranslate2.dll, the cuDNN loader, OpenMP
    ],
    hiddenimports=[
        "pystray._win32",          # chosen at runtime by pystray's backend loader
    ],
    # The GPU runtime (cuBLAS / cuDNN) is never bundled: it is a 1.3 GB
    # optional download (infra/asr/catalog.py).
    excludes=["tkinter", "unittest", "pytest", "mypy", "ruff", "IPython", "torch", "tensorflow",
              "hf_xet"],  # huggingface_hub's download accelerator: we never download through it
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=PRODUCT["name"],
    console=False,
    icon=str(PKG / "assets" / "app.ico"),
    version=version_info,
    upx=False,                     # UPX-packed exes are a classic antivirus false positive
)
coll = COLLECT(exe, a.binaries, a.datas, name=PRODUCT["name"], upx=False)
