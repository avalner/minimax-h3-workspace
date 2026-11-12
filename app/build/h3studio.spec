# PyInstaller spec: python -m PyInstaller app/build/h3studio.spec --noconfirm --distpath app/build/dist --workpath app/build/work
from pathlib import Path
import re

APP = Path(SPECPATH).parent

# version metadata inside the exe: unsigned builds with no resource at all get flagged by AVs far more often
_ver = re.search(r'APP_VERSION\s*=\s*"(\d+)\.(\d+)\.(\d+)"', (APP / "version.py").read_text(encoding="utf-8"))
_v = tuple(int(x) for x in _ver.groups()) + (0,) if _ver else (1, 0, 0, 0)
VI = APP / "build" / "file_version_info.txt"
VI.write_text(f'''# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(filevers={_v}, prodvers={_v}, mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([
      StringTable('040904B0', [
        StringStruct('CompanyName', 'Minimax H3 Workspace'),
        StringStruct('FileDescription', 'Minimax H3 Workspace - video generation workspace'),
        StringStruct('FileVersion', '{_v[0]}.{_v[1]}.{_v[2]}.0'),
        StringStruct('InternalName', 'MinimaxH3Workspace'),
        StringStruct('OriginalFilename', 'MinimaxH3Workspace.exe'),
        StringStruct('ProductName', 'Minimax H3 Workspace'),
        StringStruct('ProductVersion', '{_v[0]}.{_v[1]}.{_v[2]}'),
        StringStruct('LegalCopyright', 'Open source'),
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
''', encoding="utf-8")

a = Analysis(
    [str(APP / "main.py")],
    pathex=[str(APP)],
    datas=[(str(APP / "ui" / "index.html"), "ui"), (str(APP / "ui" / "previews"), "ui/previews"), (str(APP / "skills"), "skills"), (str(APP / "bin"), "bin")],
    hiddenimports=["webview.platforms.edgechromium", "webview.platforms.winforms", "clr", "certifi"],
    excludes=["tkinter", "matplotlib", "numpy", "PIL", "pandas", "scipy", "IPython"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="MinimaxH3Workspace",
    icon=str(APP / "build" / "icon.ico"),
    version=str(VI),
    console=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="MinimaxH3Workspace")
