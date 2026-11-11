# Emballage PyInstaller de l'application de bureau (Windows).
# Lancer depuis la racine du projet :  powershell -File desktop\build.ps1
import shutil
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path(SPECPATH).parent

datas = [
    (str(ROOT / "static"), "static"),
    (str(ROOT / "desktop" / "icon.ico"), "desktop"),
    (str(ROOT / "bot" / "index.js"), "bot"),
    (str(ROOT / "bot" / "package.json"), "bot"),
    (str(ROOT / "bot" / "node_modules"), "bot/node_modules"),
]
binaries = []
hiddenimports = collect_submodules("uvicorn") + collect_submodules("app")

# Node.js embarqué pour le bot Discord : l'utilisateur n'a rien à installer.
node = shutil.which("node")
if node:
    binaries.append((node, "node"))
else:
    print("ATTENTION : Node.js introuvable, le bot Discord ne sera pas inclus.")

for package in ["faster_whisper", "ctranslate2", "av", "onnxruntime", "tokenizers",
                "huggingface_hub", "webview", "anthropic"]:
    d, b, h = collect_all(package)
    datas += d
    binaries += b
    hiddenimports += h

icon = ROOT / "desktop" / "icon.ico"

a = Analysis(
    [str(ROOT / "desktop" / "main.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "matplotlib", "PyQt5", "PyQt6", "PySide2", "PySide6", "IPython", "pytest"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="DiscordTranscript",
    console=False,
    icon=str(icon) if icon.exists() else None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="DiscordTranscript")
