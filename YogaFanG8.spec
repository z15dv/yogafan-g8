# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

project_root = Path(SPECPATH)
source_root = project_root / "src"

a = Analysis(
    [str(source_root / "main.py")],
    pathex=[str(source_root)],
    binaries=[],
    datas=[
        (str(project_root / "WinRing0x64.sys"), "."),
        (str(project_root / "assets" / "yogafan-g8.png"), "assets"),
        (str(project_root / "assets" / "yogafan-g8.ico"), "assets"),
        (str(project_root / "assets" / "check.svg"), "assets"),
        (str(project_root / "assets" / "cpu.svg"), "assets"),
        (str(project_root / "assets" / "gpu.svg"), "assets"),
        (str(project_root / "assets" / "leaf.svg"), "assets"),
        (str(project_root / "assets" / "mode-auto.svg"), "assets"),
        (str(project_root / "assets" / "mode-manual.svg"), "assets"),
        (str(project_root / "assets" / "mode-curve.svg"), "assets"),
        (str(project_root / "assets" / "modes" / "intelligent.png"), "assets/modes"),
        (str(project_root / "assets" / "modes" / "performance.png"), "assets/modes"),
        (str(project_root / "assets" / "modes" / "unknown.svg"), "assets/modes"),
    ],
    hiddenimports=["PySide6.QtNetwork", "PySide6.QtSvg", "pynvml"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="YogaFanG8",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    uac_admin=False,
    icon=str(project_root / "assets" / "yogafan-g8.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="YogaFanG8",
)
