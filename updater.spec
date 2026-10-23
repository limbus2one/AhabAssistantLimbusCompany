# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['updater.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'numpy', 'pandas',
        # 更新器不联网（下载在主程序 check_update 完成）、不校验 TLS/哈希，
        # 排除 OpenSSL/libcrypto 与其他重载荷，实测体积 8.29MB -> 6.01MB（-27%）
        'ssl', '_ssl', 'hashlib', '_hashlib', 'pyexpat', 'unicodedata',
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='AALC Updater',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    # 参考 MAA.Updater：更新器不依赖控制台，全程通过日志文件与原生进度窗反馈
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets\\logo\\Updater.ico'],
)
