# -*- mode: python ; coding: utf-8 -*-

import os
from PyInstaller.utils.hooks import collect_all

block_cipher = None
webview_datas, webview_binaries, webview_hiddenimports = collect_all('webview')

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=webview_binaries,
    datas=[('ui', 'ui')] + webview_datas,
    hiddenimports=['seeddata'] + webview_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='ResearchBench',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file='macos/entitlements.plist',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='ResearchBench',
)

app = BUNDLE(
    coll,
    name='ResearchBench-v2.4.4.app',
    icon='assets/ResearchBench.icns' if os.path.exists('assets/ResearchBench.icns') else None,
    bundle_identifier='io.researchbench.desktop',
    version='2.4.4',
    info_plist={
        'CFBundleDisplayName': '科研工作台',
        'CFBundleShortVersionString': '2.4.4',
        'CFBundleVersion': '2.4.4',
        'LSMinimumSystemVersion': '11.0',
        'NSHighResolutionCapable': True,
        'NSHumanReadableCopyright': 'ResearchBench contributors',
    },
)
