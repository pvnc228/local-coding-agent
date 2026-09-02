from PyInstaller.utils.hooks import collect_data_files, collect_submodules


hidden_imports = collect_submodules("local_coding_agent")
data_files = collect_data_files("local_coding_agent")

analysis = Analysis(
    ["tools/sidecar_entry.py"],
    pathex=["."],
    binaries=[],
    datas=data_files,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
python_archive = PYZ(analysis.pure)

executable = EXE(
    python_archive,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="local-agent-sidecar",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
)
