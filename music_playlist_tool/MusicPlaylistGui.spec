# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import tkinter as _tkinter
from PyInstaller.utils.hooks import collect_submodules

TOOL_DIR = Path(SPECPATH).resolve()
PROJECT_ROOT = TOOL_DIR.parent
TKINTER_DIR = Path(_tkinter.__file__).resolve().parent
HIDDEN_IMPORTS = [
    'argparse',
    'base64',
    'dataclasses',
    'datetime',
    'json',
    'queue',
    'secrets',
    'socket',
    'struct',
    'subprocess',
    'tempfile',
    'threading',
    'time',
    'traceback',
    'urllib.parse',
    'urllib.request',
    'uuid',
    'pystray',
    'pystray._win32',
    'PIL.Image',
    'PIL.ImageDraw',
    'tkinter.filedialog',
    'tkinter.messagebox',
    'tkinter.ttk',
]
HIDDEN_IMPORTS += collect_submodules('mutagen')
HIDDEN_IMPORTS += collect_submodules('opencc')

a = Analysis(
    [str(TOOL_DIR / 'gui_app.py')],
    pathex=[str(PROJECT_ROOT)],
    binaries=[],
    datas=[
        (str(TOOL_DIR / 'auto.py'), '.'),
        (str(TOOL_DIR / 'qq-auto.py'), '.'),
        (str(PROJECT_ROOT / 'runtime'), 'runtime'),
        (str(TKINTER_DIR / 'filedialog.py'), 'tkinter'),
        (str(TKINTER_DIR / 'messagebox.py'), 'tkinter'),
        (str(TKINTER_DIR / 'commondialog.py'), 'tkinter'),
        (str(TKINTER_DIR / 'dialog.py'), 'tkinter'),
        (str(TKINTER_DIR / 'simpledialog.py'), 'tkinter'),
        (str(TKINTER_DIR / 'ttk.py'), 'tkinter'),
    ],
    hiddenimports=HIDDEN_IMPORTS,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='MusicPlaylistGui',
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
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='MusicPlaylistGui',
)
