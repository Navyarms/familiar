# PyInstaller spec for the Familiar brain (one-folder). Build: uv run pyinstaller familiar-brain.spec --noconfirm
# Output: dist/familiar-brain/familiar-brain.exe (+ _internal). The shell looks for it there in dev and under
# resources/brain/ when packaged.
from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_dynamic_libs, collect_submodules

block_cipher = None

datas, binaries, hiddenimports = [], [], []
for pkg in ("fastapi", "uvicorn", "starlette", "anyio", "websockets", "kokoro_onnx", "sherpa_onnx", "faster_whisper",
            "ctranslate2", "tokenizers", "onnxruntime", "uiautomation", "comtypes", "pycaw", "sounddevice", "PIL",
            "misaki", "espeakng_loader", "phonemizer", "language_tags", "num2words", "spacy", "en_core_web_sm"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass
hiddenimports += collect_submodules("familiar_brain") + collect_submodules("familiar_protocol") + ["pydantic", "psutil", "httpx", "numpy"]
binaries += collect_dynamic_libs("sounddevice")
datas += collect_data_files("certifi")

a = Analysis(
    ["brain_entry.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["torch", "torchaudio", "torchvision", "tensorflow", "matplotlib", "tkinter", "PyQt5", "PySide2", "IPython", "notebook", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="familiar-brain", debug=False, bootloader_ignore_signals=False,
          strip=False, upx=False, console=True, icon=None)
coll = COLLECT(exe, a.binaries, a.zipfiles, a.datas, strip=False, upx=False, name="familiar-brain")
