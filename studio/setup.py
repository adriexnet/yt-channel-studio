"""Cells 1 and 2: Google Drive, project folders, keys file and the notebook-level environment."""
from .lib import *

SUBTITLE_FONTS = [   # open licenses, from the Google Fonts repository
    "ofl/anton/Anton-Regular.ttf", "ofl/bebasneue/BebasNeue-Regular.ttf", "ofl/archivoblack/ArchivoBlack-Regular.ttf",
    "ofl/bangers/Bangers-Regular.ttf", "ofl/poppins/Poppins-ExtraBold.ttf", "apache/luckiestguy/LuckiestGuy-Regular.ttf",
]


def backup_code():
    """Keep a copy of this repository in Drive: the launcher falls back to it when GitHub cannot be reached."""
    if os.path.abspath(CODE_DIR) == os.path.abspath(CODE_BACKUP_DIR):
        return
    shutil.copytree(CODE_DIR, CODE_BACKUP_DIR, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"))


def base():
    """Cell 1: connect Google Drive (approve the popup), create the folders and api_keys.toml."""
    import studio
    start_cell(f"Cell 1 · Base (YT-Channel-Studio {studio.__version__})")
    mount_project_drive()      # opens the Drive permission popup; stops with an error if Drive is not mounted
    ensure_project_dirs()
    ensure_api_keys_file()
    backup_code()
    finish_cell(True, [f"Project folder: {BASE_DIR}", f"API keys file: {KEYS_FILE}",
                       f"Code: {CODE_DIR} (copy in {CODE_BACKUP_DIR})", "Next: run Cell 2 (Environment)"])


def detect_gpu():
    if not shutil.which("nvidia-smi"):
        return ""
    gpu = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
                         capture_output=True, text=True)
    return gpu.stdout.strip() if gpu.returncode == 0 else ""


def download_fonts():
    """Subtitle fonts saved once in Drive/fonts; drop your own .ttf/.otf there too. Returns the problems."""
    problems = []
    for font in SUBTITLE_FONTS:
        target = f"{FONTS_DIR}/{os.path.basename(font)}"
        if os.path.exists(target):
            continue
        try:
            r = requests.get(f"https://raw.githubusercontent.com/google/fonts/main/{font}", timeout=60)
            r.raise_for_status()
            with open(target, "wb") as f:
                f.write(r.content)
        except Exception as exc:
            problems.append(f"Font {os.path.basename(font)} not downloaded ({exc}); DejaVu Sans still works")
    return problems


def environment():
    """Cell 2: system tools, cloudflared, notebook-level packages and subtitle fonts.
    Without a GPU it explains what is unavailable instead of stopping."""
    start_cell("Cell 2 · Environment")
    if not os.path.ismount(DRIVE_MOUNT_POINT):
        raise RuntimeError("Google Drive is not connected: run Cell 1 and accept the popup.")

    gpu_name = detect_gpu()
    if gpu_name:
        print("GPU:", gpu_name)
    else:
        print("⚠️  No GPU detected. This is NOT a blocker; the notebook keeps working with these limits:")
        print("   ❌ Cell 3 Ollama: local LLMs would run on CPU (very slow). Use an external LLM instead")
        print("      (OpenAI, Anthropic, Gemini, Groq...) from the Control Center.")
        print("   ❌ Cell 4 Forge and Cell 5 ComfyUI: FLUX images and local AI clips need a GPU. Skip them.")
        print("   ❌ Cell 6 F5-TTS voice cloning needs a GPU. Edge-TTS and every external voice still work.")
        print("   ✅ Stock clips, your own clips, external AI video, Whisper subtitles and the montage work on CPU.")
        print("   To enable everything: Runtime > Change runtime type > GPU, then run Cells 1-2 again.")

    if shutil.which("ffmpeg") is None or shutil.which("fc-scan") is None:
        run("apt-get update -qq && apt-get install -y -qq ffmpeg fontconfig aria2 libgl1 zstd pciutils psmisc "
            "espeak-ng fonts-dejavu", "Installing system packages...")
    if shutil.which("cloudflared") is None:
        run("wget -q -nc https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb "
            "&& dpkg -i cloudflared-linux-amd64.deb", "Installing cloudflared...")
    # Notebook-level packages only (Gradio UIs, Edge-TTS, Whisper subtitles).
    # Every heavy app (Forge, ComfyUI, F5-TTS) gets its own uv venv instead.
    run(f"pip install -q -r {CODE_DIR}/requirements.txt", "Installing notebook-level Python packages...")

    problems = download_fonts()
    fonts = sorted(f for f in os.listdir(FONTS_DIR) if f.lower().endswith((".ttf", ".otf")))
    finish_cell(True, [f"GPU: {gpu_name or 'none (CPU mode: Cells 3, 4, 5 and F5-TTS unavailable)'}",
                       f"Subtitle fonts in Drive: {len(fonts)}",
                       "Next: run Cells 3-6 for the local tools you need, then Cell 7" if gpu_name else
                       "Next: run Cell 7 and configure an external LLM in the Control Center"], problems)
