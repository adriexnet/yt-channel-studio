"""Writes notebook/YT-Channel-Studio.ipynb: a thin launcher whose cells only call the studio package.

Run from the repository root:  python scripts/build_notebook.py
"""
import json, os

REPO_URL = "https://github.com/adriexnet/yt-channel-studio.git"

CELL_1 = f'''# Downloads (or updates) the code from GitHub, then connects Google Drive: approve the popup.
REPO = "{REPO_URL}"
VERSION = "main"          # a branch or a release tag, e.g. "v2.0.0", to stay on a fixed version

import os, sys, shutil, subprocess, importlib
CODE = "/content/yt-channel-studio"
DRIVE_COPY = "/content/temp_drive/MyDrive/YT-Channel-Studio/code"   # last working copy, kept in your Drive
git = lambda *args: subprocess.run(["git", *args], capture_output=True, text=True)

if os.path.isdir(f"{{CODE}}/.git"):
    result = git("-C", CODE, "fetch", "-q", "--tags", "--force", "origin")
    if result.returncode == 0:
        is_branch = git("-C", CODE, "ls-remote", "--heads", "origin", VERSION).stdout.strip()
        result = git("-C", CODE, "checkout", "-q", "--force", "--detach", f"origin/{{VERSION}}" if is_branch else VERSION)
else:
    result = git("clone", "-q", REPO, CODE)
    if result.returncode == 0:
        result = git("-C", CODE, "checkout", "-q", "--detach", VERSION)
if result.returncode != 0:
    if not os.path.isdir(CODE):
        from google.colab import drive
        drive.mount("/content/temp_drive")
        if not os.path.isdir(DRIVE_COPY):
            raise RuntimeError("Could not download the code from GitHub and there is no copy in Drive yet:\\n" + result.stderr)
        shutil.copytree(DRIVE_COPY, CODE)
    print("⚠️  GitHub did not answer; using the copy already available:", result.stderr.strip()[-200:])

subprocess.run([sys.executable, "-m", "pip", "install", "-q", "toml"], check=True)
sys.dont_write_bytecode = True   # no cached .pyc: an update made in the same second is never skipped
sys.path.insert(0, CODE)
import studio
importlib.reload(studio)
studio.reload()           # picks up the new code without restarting the runtime
print("Code version:", studio.__version__, git("-C", CODE, "log", "-1", "--format=%h %cd", "--date=short").stdout.strip())

from studio import setup
setup.base()'''

CELLS = [
    ("## 📚 Cell 1 · Base\nDownloads or updates the code from GitHub, connects Google Drive (approve the popup), "
     "creates the project folders and `api_keys.toml`. Run first, and again whenever you want the latest code.", CELL_1),
    ("## 📦 Cell 2 · Environment\nSystem tools, cloudflared, uv, the notebook packages and the subtitle fonts. "
     "Without a GPU it tells you what is unavailable instead of stopping.",
     "from studio import setup\nsetup.environment()"),
    ("## 🧠 Cell 3 · Ollama *(optional)*\nLocal LLMs (weights persist in Drive) + chat UI. "
     "Skip it if you use an external LLM.",
     'from studio.tools import ollama\nollama.start(models=["qwen2.5:14b", "deepseek-r1:14b"])'),
    ("## 🎨 Cell 4 · Forge WebUI *(optional)*\nFLUX images in a Python 3.10 venv: local covers, base images "
     "and AI photos.",
     "from studio.tools import forge\n"
     "forge.start()   # models: flux_dev, flux_fill, flux_vae, clip_l, t5xxl, qwen_edit (all by default)"),
    ("## 🎬 Cell 5 · ComfyUI *(optional)*\nLocal AI video (Wan 2.2 / LTX-Video) in a Python 3.11 venv.",
     'from studio.tools import comfyui\ncomfyui.start(models=["wan22", "ltxv"])'),
    ("## 🎙️ Cell 6 · Audio Studio *(optional)*\nTry any voice before using it in a reel. "
     "`f5=False` skips the F5-TTS voice-cloning install.",
     "from studio.tools import audio\naudio.start(f5=True)"),
    ("## 🚀 Cell 7 · Control Center\nIdea → script → images → voice → clips → music → montage → publishing, "
     "with live progress, API keys and your saved reels.",
     "from studio import control_center\ncontrol_center.start()"),
    ("## 📋 Cell 8 · Dashboard\nEvery running tool and its public link.",
     "from studio import dashboard\ndashboard.show()"),
]


def main():
    cells = []
    for i, (markdown, code) in enumerate(CELLS, 1):
        cells.append({"cell_type": "markdown", "metadata": {"id": f"md_cell_{i}"}, "source": markdown})
        cells.append({"cell_type": "code", "metadata": {"id": f"code_cell_{i}"}, "execution_count": None,
                      "outputs": [], "source": code})
    notebook = {
        "nbformat": 4, "nbformat_minor": 0,
        "metadata": {"colab": {"provenance": [], "gpuType": "T4", "toc_visible": True},
                     "kernelspec": {"name": "python3", "display_name": "Python 3"},
                     "language_info": {"name": "python"}, "accelerator": "GPU"},
        "cells": cells,
    }
    os.makedirs("notebook", exist_ok=True)
    with open("notebook/YT-Channel-Studio.ipynb", "w", encoding="utf-8") as f:
        json.dump(notebook, f, ensure_ascii=False, indent=1)
        f.write("\n")
    print(f"Wrote notebook/YT-Channel-Studio.ipynb ({len(CELLS)} cells)")


if __name__ == "__main__":
    main()
