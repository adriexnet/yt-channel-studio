"""Shared library: paths, API keys, Drive, shell and venv helpers, the provider registry, voices and
connection tests. Every other module does `from studio.lib import *`.

All paths derive from two environment variables so the same code runs in Colab and in tests:
    STUDIO_CONTENT_DIR  local scratch disk (default /content)
    STUDIO_BASE_DIR     project folder in Google Drive (default <content>/drive/MyDrive/YT-Channel-Studio)
"""
import os, re, json, time, shutil, subprocess, urllib.request, urllib.error
import requests, toml
from tqdm.notebook import tqdm

# ---------------------------------------------------------------------------
# Cell progress logging (every cell opens with start_cell and closes with finish_cell)
# ---------------------------------------------------------------------------
_CELL_STATE = {"title": "", "t0": 0.0}


def resource_summary():
    """One line with free RAM, free disk and GPU memory, so a crash can be traced to what ran out."""
    parts = []
    try:
        info = {l.split(":")[0]: int(l.split()[1]) for l in open("/proc/meminfo")}
        parts.append(f"RAM free {info['MemAvailable'] / 1048576:.1f}/{info['MemTotal'] / 1048576:.1f} GB")
    except Exception:
        pass
    try:
        disk = shutil.disk_usage(CONTENT_DIR)
        parts.append(f"disk free {disk.free / 1e9:.0f}/{disk.total / 1e9:.0f} GB")
    except Exception:
        pass
    if shutil.which("nvidia-smi"):
        r = subprocess.run(["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
                           capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip():
            used, total = [float(x) for x in r.stdout.strip().splitlines()[0].split(",")]
            parts.append(f"VRAM used {used / 1024:.1f}/{total / 1024:.1f} GB")
    return " · ".join(parts)


def start_cell(title):
    _CELL_STATE.update(title=title, t0=time.time())
    print(f"\n▶️  {title}")
    print(f"   {resource_summary()}")
    print("-" * 60)


def finish_cell(ok=True, details=None, problems=None):
    """Closing line of a cell. ok=False prints a warning instead of DONE so failures are never hidden.
    details: list of 'label: value' lines (links, paths). problems: list of what went wrong."""
    elapsed = time.time() - _CELL_STATE["t0"]
    minutes, seconds = divmod(int(elapsed), 60)
    took = f"{minutes}m {seconds:02d}s" if minutes else f"{seconds}s"
    print("-" * 60)
    if ok:
        print(f"✅ DONE · {_CELL_STATE['title']} ({took})")
    else:
        print(f"⚠️  NOT COMPLETED · {_CELL_STATE['title']} ({took})")
    print(f"   {resource_summary()}")
    for line in (details or []):
        print(f"   {line}")
    for line in (problems or []):
        print(f"   ❌ {line}")


def is_public_url(url):
    return isinstance(url, str) and url.startswith("https://")


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
CONTENT_DIR = os.environ.get("STUDIO_CONTENT_DIR", "/content")
DRIVE_MOUNT_POINT = f"{CONTENT_DIR}/temp_drive"
BASE_DIR = os.environ.get("STUDIO_BASE_DIR", f"{CONTENT_DIR}/drive/MyDrive/YT-Channel-Studio")
REAL_DRIVE_DIR = f"{DRIVE_MOUNT_POINT}/MyDrive/YT-Channel-Studio"
CODE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # this repository
CODE_BACKUP_DIR = f"{BASE_DIR}/code"     # last working copy, used when GitHub cannot be reached
MODELS_DIR = f"{BASE_DIR}/models"
REELS_DIR = f"{BASE_DIR}/reels"
CONFIG_DIR = f"{BASE_DIR}/config"
VOICES_DIR = f"{BASE_DIR}/voices"
MUSIC_DIR = f"{BASE_DIR}/music"          # background songs: yours, plus the ones generated with AI (music/ai)
MUSIC_AI_DIR = f"{MUSIC_DIR}/ai"
FOOTAGE_DIR = f"{BASE_DIR}/footage"      # your own clips and photos for the "My clips" source
FONTS_DIR = f"{BASE_DIR}/fonts"          # subtitle fonts (Cell 2 adds a few; drop your own .ttf/.otf here)
TTS_MODELS_DIR = f"{MODELS_DIR}/tts"
KEYS_FILE = f"{CONFIG_DIR}/api_keys.toml"
LINKS_FILE = f"{CONTENT_DIR}/active_links.json"
WORK_DIR = f"{CONTENT_DIR}/work"          # fast local scratch; finals are copied to Drive
VENVS_DIR = f"{CONTENT_DIR}/venvs"        # one isolated venv per app (recreated each session)
UV_CACHE_DIR = f"{CONTENT_DIR}/uv_cache"  # shared wheel cache so torch downloads once per Python version
HF_CACHE_DIR = f"{CONTENT_DIR}/hf_cache"  # HuggingFace cache MUST be local: Drive has no symlinks and corrupts it
APPS_DIR = f"{CONTENT_DIR}/apps"          # Forge and ComfyUI source code (cloned each session)


def log_path(name):
    """Server and tunnel logs live on the local disk: `!tail -f` them from any cell."""
    return f"{CONTENT_DIR}/{name}.log"

MODEL_SUBDIRS = ["ollama", "unet", "clip", "vae", "qwen_edit", "video", "tts"]

# Same torch for every app; cu128 covers T4, L4, A100, H100 and Blackwell GPUs.
TORCH_SPEC = ("torch==2.7.1 torchvision==0.22.1 torchaudio==2.7.1 "
              "--extra-index-url https://download.pytorch.org/whl/cu128")

# Ports Colab itself uses (Jupyter 9000, file shim 3453, its web proxy on 8080). Never bind or kill these:
# killing 8080 disconnects and resets the runtime.
COLAB_RESERVED_PORTS = {8080, 9000, 3453}

PORTS = {
    "ollama_api": 11434, "ollama_chat": 7861, "forge": 7860, "comfy": 8188,
    "tts_studio": 7862, "f5_native": 7863, "control_center": 7864,
}

# ---------------------------------------------------------------------------
# API keys (TOML in Drive)
# ---------------------------------------------------------------------------
DEFAULT_API_KEYS = {
    "openai": {"api_key": "", "chat_model": "gpt-6.1-sol", "tts_model": "tts-1-hd", "tts_voice": "onyx"},
    "anthropic": {"api_key": "", "chat_model": "claude-sonnet-5-5"},
    "gemini": {"api_key": "", "chat_model": "gemini-3.8-flash", "tts_model": "gemini-3.8-flash-tts",
               "image_model": "gemini-nano-banana-2.1", "video_model": "gemini-omni-1.1-flash",
               "video_resolution": "720p"},
    # One sub-table per OpenAI-compatible service: [openai_compatible.groq], [openai_compatible.openrouter], ...
    "openai_compatible": {
        "groq": {"api_key": "", "base_url": "https://api.groq.com/openai/v1", "chat_model": "llama-3.3-70b-versatile"},
        "openrouter": {"api_key": "", "base_url": "https://openrouter.ai/api/v1", "chat_model": "openai/gpt-4o-mini"},
        "deepseek": {"api_key": "", "base_url": "https://api.deepseek.com/v1", "chat_model": "deepseek-chat"},
    },
    "pexels": {"api_key": ""},
    "pixabay": {"api_key": ""},
    "coverr": {"api_key": ""},
    "wavespeed": {"api_key": "", "model": "bytedance/seedance-2.0-fast/text-to-video"},
    "volcengine_seedance": {"api_key": "", "model": "doubao-seedance-1-0-pro-250528", "resolution": "720p"},
    "ofox": {"api_key": "", "model": "bytedance/seedance-2.0-fast", "resolution": "720p"},
    "muapi": {"api_key": "", "model": "seedance-lite-t2v", "resolution": "480p"},
    "metaso_minimax": {"api_key": "", "resolution": "768P"},
    "infistar": {"api_key": "", "model": "doubao-seedance-2-0-fast-260128"},
    "image_api": {"api_key": "", "base_url": "https://api.openai.com/v1", "model": "gpt-image-1", "size": "1024x1536"},
    "elevenlabs": {"api_key": "", "voice_id": "JBFqnCBsd6RMkjVDRZzb", "tts_model": "eleven_multilingual_v2",
                   "music_model": "music_v1"},
    "azure": {"speech_key": "", "speech_region": "eastus", "voice": "es-MX-JorgeNeural"},
    "siliconflow": {"api_key": ""},
    "mimo": {"api_key": "", "model": "mimo-v2.5-tts"},
    "minimax": {"api_key": "", "voice_id": "", "model": "speech-2.8-hd", "base_url": "https://api.minimax.io/v1/t2a_v2"},
    "fish_audio": {"api_key": "", "model": "s2.1-pro-free", "voice_id": ""},
    "voxcpm": {"api_key": "", "model_id": "", "base_url": "https://api.modelbest.cn/v1"},
    "upload_post": {"api_key": "", "username": "", "facebook_page_id": ""},
    "huggingface": {"token": ""},
}


def load_api_keys():
    if os.path.exists(KEYS_FILE):
        with open(KEYS_FILE) as f:
            return toml.load(f)
    return {}


def save_api_keys(keys):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(KEYS_FILE, "w") as f:
        toml.dump(keys, f)


def _merge_defaults(target, defaults):
    """Recursively add missing fields; never overwrite a value the user already has."""
    for field, default in defaults.items():
        if isinstance(default, dict):
            if not isinstance(target.get(field), dict):
                target[field] = {}
            _merge_defaults(target[field], default)
        else:
            target.setdefault(field, default)


def ensure_api_keys_file():
    """Create the TOML if missing, or add any new sections/fields without touching existing values."""
    keys = load_api_keys()
    _merge_defaults(keys, DEFAULT_API_KEYS)
    save_api_keys(keys)
    return keys


def compat_provider_names():
    """Names of the configured OpenAI-compatible services, e.g. ['groq', 'openrouter', 'deepseek']."""
    compat = load_api_keys().get("openai_compatible", {})
    return [name for name, cfg in compat.items() if isinstance(cfg, dict)]


def get_compat(name, field, default=""):
    cfg = load_api_keys().get("openai_compatible", {}).get(name, {})
    return cfg.get(field, default) or default


def get_key(section, field, default=""):
    return load_api_keys().get(section, {}).get(field, default) or default


# ---------------------------------------------------------------------------
# Drive + project layout
# ---------------------------------------------------------------------------
def _move_aside(path, reason):
    """Rename a local folder that is NOT Drive so nothing in it is lost, and say so."""
    backup = f"{path}_local_backup_{int(time.time())}"
    shutil.move(path, backup)
    print(f"WARNING: {path} was a local folder, not Google Drive ({reason}).")
    print(f"         Moved to {backup} so you can recover anything saved there.")


def mount_project_drive():
    """Mount Drive (verified, never assumed) and expose only the project folder at BASE_DIR."""
    from google.colab import drive

    # 1. Mount. A folder that merely EXISTS is not proof of a mount: check the real mount table.
    if not os.path.ismount(DRIVE_MOUNT_POINT):
        if os.path.isdir(DRIVE_MOUNT_POINT) and os.listdir(DRIVE_MOUNT_POINT):
            _move_aside(DRIVE_MOUNT_POINT, "left over from a previous failed mount")
        print("Mounting Google Drive (approve the permission popup if it appears)...")
        drive.mount(DRIVE_MOUNT_POINT)

    if not os.path.ismount(DRIVE_MOUNT_POINT) or not os.path.isdir(f"{DRIVE_MOUNT_POINT}/MyDrive"):
        raise RuntimeError("Google Drive is NOT mounted. Re-run this cell and accept the permission popup; "
                           "nothing will be saved until Drive is connected.")

    # 2. Expose only the project folder at BASE_DIR.
    os.makedirs(REAL_DRIVE_DIR, exist_ok=True)
    if os.path.islink(BASE_DIR):
        os.unlink(BASE_DIR)
    elif os.path.isdir(BASE_DIR):
        if os.path.ismount(f"{CONTENT_DIR}/drive"):
            # Drive was also mounted from the Colab sidebar: BASE_DIR already IS the real Drive folder.
            print(f"Drive mounted at {DRIVE_MOUNT_POINT} and from the Colab sidebar; using the real folder.")
            return
        _move_aside(BASE_DIR, "created while Drive was not connected")
    os.makedirs(os.path.dirname(BASE_DIR), exist_ok=True)
    os.symlink(REAL_DRIVE_DIR, BASE_DIR)
    print(f"Google Drive mounted and verified. Project folder: {BASE_DIR} -> {REAL_DRIVE_DIR}")


def ensure_project_dirs():
    for sub in MODEL_SUBDIRS:
        os.makedirs(f"{MODELS_DIR}/{sub}", exist_ok=True)
    for d in [REELS_DIR, CONFIG_DIR, VOICES_DIR, MUSIC_DIR, MUSIC_AI_DIR, FOOTAGE_DIR, FONTS_DIR,
              WORK_DIR, VENVS_DIR, UV_CACHE_DIR, HF_CACHE_DIR]:
        os.makedirs(d, exist_ok=True)


# ---------------------------------------------------------------------------
# Shell helpers
# ---------------------------------------------------------------------------
def run(cmd, label=None, check=True, cwd=None, env=None):
    """Run a shell command quietly; on failure print the tail of its output and raise."""
    if label:
        print(label)
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd=cwd, env=env)
    if r.returncode != 0 and check:
        print(r.stdout[-3000:])
        print(r.stderr[-3000:])
        raise RuntimeError(f"Command failed: {cmd}")
    return r


_PROTECTED_PROCESS_MARKERS = ("datalab", "jupyter", "colab", "ipykernel", "kernel_gateway")


def kill_port(port):
    """Stop whatever WE left listening on a port, but never a Colab system process."""
    if port in COLAB_RESERVED_PORTS:
        print(f"Refusing to free port {port}: Colab uses it internally (killing it resets the runtime).")
        return
    r = subprocess.run(["fuser", f"{port}/tcp"], capture_output=True, text=True)
    for pid in re.findall(r"\d+", r.stdout):
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                cmdline = f.read().replace(b"\0", b" ").decode(errors="replace").lower()
        except OSError:
            continue
        if int(pid) == os.getpid() or any(m in cmdline for m in _PROTECTED_PROCESS_MARKERS):
            print(f"Port {port} is held by a Colab process (pid {pid}); not touching it.")
            continue
        subprocess.run(["kill", "-9", pid], stderr=subprocess.DEVNULL)


def start_background(cmd, log_path, cwd=None, env=None):
    """Start a long-running server and send its output to a log file."""
    log = open(log_path, "w")
    return subprocess.Popen(cmd, stdout=log, stderr=log, cwd=cwd, env=env)


def drive_responds(timeout=10):
    """Google Drive can go stale after hours of session; a read then blocks forever instead of failing.
    Probe it in a thread so the caller can report it instead of hanging."""
    import threading
    box = {}

    def probe():
        try:
            box["ok"] = bool(os.listdir(CONFIG_DIR) is not None)
        except Exception as exc:
            box["error"] = exc

    t = threading.Thread(target=probe, daemon=True)
    t.start()
    t.join(timeout)
    return box.get("ok", False)


def http_ok(url, timeout=5):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status == 200
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
        return False


def tail(path, n=40):
    if not os.path.exists(path):
        return ""
    with open(path, errors="replace") as f:
        lines = [l for l in f.read().splitlines() if l.strip()]
    return "\n".join(lines[-n:])


def wait_for_http(url, process=None, log_path=None, timeout=600, label="server", interval=5):
    """Poll until the URL answers HTTP 200. Returns True on success, False (and prints the log tail) otherwise."""
    for i in range(int(timeout / interval)):
        time.sleep(interval)
        if http_ok(url):
            print(f"{label} is up (HTTP 200).")
            return True
        if process is not None and process.poll() is not None:
            print(f"{label} exited with code {process.returncode}.")
            break
        if log_path and i % 3 == 0:
            last = tail(log_path, 1)
            print(f"  waiting for {label}: {last[:150] if last else 'starting...'}")
    print(f"{label} did not answer in time. Last log lines:")
    if log_path:
        print(tail(log_path))
    return False


# ---------------------------------------------------------------------------
# uv-managed virtual environments
# ---------------------------------------------------------------------------
def ensure_uv():
    if shutil.which("uv") is None:
        run("pip install -q uv", "Installing uv...")


def venv_paths(name):
    venv_dir = f"{VENVS_DIR}/{name}"
    return venv_dir, f"{venv_dir}/bin/python"


def venv_is_valid(python, version):
    if not os.path.exists(python):
        return False
    r = subprocess.run([python, "-c", "import sys, pip; print('%d.%d' % sys.version_info[:2])"],
                       capture_output=True, text=True)
    return r.returncode == 0 and r.stdout.strip() == version


def ensure_venv(name, version):
    """Create (or recreate if broken/wrong version) an isolated venv with its own Python. Returns (venv_dir, python)."""
    ensure_uv()
    venv_dir, python = venv_paths(name)
    if not venv_is_valid(python, version):
        shutil.rmtree(venv_dir, ignore_errors=True)
        run(f"uv venv --seed --python {version} {venv_dir}",
            f"Creating venv '{name}' with Python {version}...", env=_uv_env())
    r = subprocess.run([python, "--version"], capture_output=True, text=True)
    print(f"venv '{name}': {r.stdout.strip()}")
    return venv_dir, python


def _uv_env():
    env = dict(os.environ)
    env["UV_CACHE_DIR"] = UV_CACHE_DIR
    # Gentler installs: fewer files unpacked at once keeps RAM peaks low when other tools are already running.
    env["UV_CONCURRENT_INSTALLS"] = "2"
    env["UV_CONCURRENT_BUILDS"] = "1"
    return env


def uv_install(python, args, label=None, check=True):
    """uv pip install into the venv that owns `python`."""
    return run(f"uv pip install --python {python} {args}", label, check=check, env=_uv_env())


def venv_env(venv_dir, extra=None):
    """Environment for processes launched inside a venv, isolated from Colab's own site-packages."""
    env = dict(os.environ)
    env["VIRTUAL_ENV"] = venv_dir
    env["PATH"] = f"{venv_dir}/bin:" + env.get("PATH", "")
    env.pop("PYTHONPATH", None)       # never mix Colab's packages into the venv
    env["MPLBACKEND"] = "Agg"         # Colab sets a matplotlib backend that does not exist in the venv
    env["PYTHONUNBUFFERED"] = "1"     # logs are written immediately
    env["HF_HOME"] = HF_CACHE_DIR     # local, never Drive (symlink-based cache); re-downloaded per session
    hf_token = get_key("huggingface", "token")
    if hf_token:
        env["HF_TOKEN"] = hf_token    # avoids anonymous rate limits (the warning seen in f5_native.log)
    if extra:
        env.update(extra)
    return env


def check_torch(python, label):
    r = subprocess.run([python, "-c", "import torch; print(torch.__version__, torch.cuda.is_available())"],
                       capture_output=True, text=True)
    print(f"{label} torch: {r.stdout.strip() or r.stderr.strip()[-200:]}")
    if "True" not in r.stdout:
        print("  CUDA not available: check that the Colab runtime has a GPU.")


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
def download_if_missing(url, dest_dir, filename, hf_token=None):
    """Resumable download into Drive; skipped when the file already exists."""
    os.makedirs(dest_dir, exist_ok=True)
    final_path = os.path.join(dest_dir, filename)
    part_path = final_path + ".part"
    if os.path.exists(final_path) and os.path.getsize(final_path) > 1024 * 1024:
        print(f"[exists in Drive] {filename} ({os.path.getsize(final_path) / 1024**3:.2f} GB)")
        return final_path
    hf_token = hf_token or get_key("huggingface", "token")
    headers = {"Authorization": f"Bearer {hf_token}"} if hf_token else {}
    downloaded = os.path.getsize(part_path) if os.path.exists(part_path) else 0
    if downloaded:
        headers["Range"] = f"bytes={downloaded}-"
    print(f"Downloading {filename} to Drive...")
    with requests.get(url, headers=headers, stream=True) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0)) + downloaded
        with open(part_path, "ab" if downloaded else "wb") as f, tqdm(
            total=total, initial=downloaded, unit="B", unit_scale=True, unit_divisor=1024,
            desc=filename[:25]) as bar:
            for chunk in r.iter_content(chunk_size=4 * 1024 * 1024):
                if chunk:
                    f.write(chunk)
                    bar.update(len(chunk))
    shutil.move(part_path, final_path)
    print(f"[done] {filename}")
    return final_path


def link_models(src_dir, dst_dir):
    """Symlink every model file in a Drive folder into an app's model folder."""
    os.makedirs(dst_dir, exist_ok=True)
    for name in os.listdir(src_dir):
        if name.endswith(".part"):
            continue
        src, dst = os.path.join(src_dir, name), os.path.join(dst_dir, name)
        if os.path.islink(dst):
            os.unlink(dst)
        elif os.path.exists(dst):
            continue
        os.symlink(src, dst)


# ---------------------------------------------------------------------------
# Video encoding: NVENC on the GPU when it really works, libx264 otherwise
# ---------------------------------------------------------------------------
_NVENC = {}


def nvenc_available():
    """True if this machine can encode H.264 on the GPU (NVENC). Tested once with a real encode,
    never assumed: no GPU, no driver support or an ffmpeg without NVENC all fall back to the CPU (libx264)."""
    if "ok" not in _NVENC:
        ok = False
        if shutil.which("ffmpeg") and shutil.which("nvidia-smi"):
            r = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                                "color=c=black:s=256x256:d=0.2", "-c:v", "h264_nvenc", "-f", "null", "-"],
                               capture_output=True, text=True)
            ok = r.returncode == 0
        _NVENC["ok"] = ok
    return _NVENC["ok"]


def video_encoder_args():
    """ffmpeg output args for H.264: NVENC on the GPU when available, libx264 on the CPU otherwise."""
    if nvenc_available():
        return ["-c:v", "h264_nvenc", "-preset", "fast", "-cq", "21", "-pix_fmt", "yuv420p"]
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p"]


# ---------------------------------------------------------------------------
# Public links (cloudflared) + registry used by the dashboard
# ---------------------------------------------------------------------------
def register_tool_link(name, usage, port, url):
    data = {}
    if os.path.exists(LINKS_FILE):
        try:
            with open(LINKS_FILE) as f:
                data = json.load(f)
        except Exception:
            data = {}
    data[name] = {"usage": usage, "port": port, "url": url}
    with open(LINKS_FILE, "w") as f:
        json.dump(data, f, indent=2)


def start_cloudflare_tunnel(port, log_name, tool_name=None, tool_usage=""):
    log_file = log_path(log_name)
    if os.path.exists(log_file):
        os.remove(log_file)
    start_background(["cloudflared", "tunnel", "--url", f"http://localhost:{port}",
                      "--http-host-header", f"localhost:{port}"], log_file)
    url = f"not ready, see {log_file}"
    for _ in range(25):
        time.sleep(1)
        urls = re.findall(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com", tail(log_file, 200))
        if urls:
            url = urls[0]
            break
    if tool_name:
        register_tool_link(tool_name, tool_usage, port, url)
    return url


def launch_gradio(blocks, port, css=None):
    """Launch a Gradio app headless: nothing rendered in the cell, no gradio.live share link."""
    import gradio as gr
    kill_port(port)
    kwargs = dict(server_name="0.0.0.0", server_port=port, share=False, inline=False,
                  quiet=True, prevent_thread_lock=True,
                  allowed_paths=[CONTENT_DIR, BASE_DIR])   # lets the UI show clips, images and reels in Drive
    blocks.launch(theme=gr.themes.Soft(), css=css, **kwargs)    # Gradio 6 takes theme and css here


# ---------------------------------------------------------------------------
# Credentials: one per [section] of api_keys.toml. The Control Center builds its key tab from this.
# ---------------------------------------------------------------------------
CREDENTIAL_CATEGORIES = [
    ("llm", "🧠 Modelos de lenguaje"),
    ("stock", "🎞️ Clips de stock"),
    ("ai_video", "🎬 Video con IA externa (de pago)"),
    ("image", "🖼️ Imagen con IA"),
    ("voice", "🎙️ Voz y música"),
    ("publish", "📤 Publicación"),
    ("other", "⚙️ Otros"),
]

FIELD_LABELS = {
    "api_key": "API key", "speech_key": "Speech key", "speech_region": "Región", "token": "Token",
    "chat_model": "Modelo de chat", "tts_model": "Modelo TTS", "tts_voice": "Voz TTS por defecto",
    "base_url": "Base URL", "model": "Modelo", "size": "Tamaño de imagen", "resolution": "Resolución",
    "voice_id": "Voice ID por defecto", "voice": "Voz por defecto", "model_id": "Model ID",
    "music_model": "Modelo de música", "username": "Usuario (perfil de Upload-Post)",
    "image_model": "Modelo de imagen", "video_model": "Modelo de video", "video_resolution": "Resolución de video",
    "facebook_page_id": "Facebook Page ID (opcional)",
}
SECRET_FIELDS = {"api_key", "speech_key", "token"}

# section -> label, category, editable fields, required fields, where to get the key
CREDENTIALS = {
    "openai": dict(label="OpenAI (ChatGPT, voz TTS)", category="llm",
                   fields=["api_key", "chat_model", "tts_model", "tts_voice"], required=["api_key"],
                   link="https://platform.openai.com/api-keys"),
    "anthropic": dict(label="Anthropic (Claude)", category="llm", fields=["api_key", "chat_model"],
                      required=["api_key"], link="https://console.anthropic.com/settings/keys"),
    "gemini": dict(label="Google Gemini (LLM, voz, imagen Nano Banana y video Omni)", category="llm",
                   fields=["api_key", "chat_model", "tts_model", "image_model", "video_model", "video_resolution"],
                   required=["api_key"],
                   link="https://aistudio.google.com/apikey"),
    "pexels": dict(label="Pexels", category="stock", fields=["api_key"], required=["api_key"],
                   link="https://www.pexels.com/api/"),
    "pixabay": dict(label="Pixabay", category="stock", fields=["api_key"], required=["api_key"],
                    link="https://pixabay.com/api/docs/"),
    "coverr": dict(label="Coverr", category="stock", fields=["api_key"], required=["api_key"],
                   link="https://api.coverr.co/docs/"),
    "wavespeed": dict(label="WaveSpeed AI", category="ai_video", fields=["api_key", "model"],
                      required=["api_key", "model"], link="https://wavespeed.ai/accesskey"),
    "volcengine_seedance": dict(label="Volcano Engine Seedance", category="ai_video",
                                fields=["api_key", "model", "resolution"], required=["api_key", "model"],
                                link="https://console.volcengine.com/ark"),
    "ofox": dict(label="OFox AI Video", category="ai_video", fields=["api_key", "model", "resolution"],
                 required=["api_key", "model"], link="https://ofox.ai"),
    "muapi": dict(label="MuAPI AI Video", category="ai_video", fields=["api_key", "model", "resolution"],
                  required=["api_key", "model"], link="https://muapi.ai"),
    "metaso_minimax": dict(label="Metaso · MiniMax H3", category="ai_video", fields=["api_key", "resolution"],
                           required=["api_key"], link="https://metaso.cn"),
    "infistar": dict(label="Infistar AI Video", category="ai_video", fields=["api_key", "model"],
                     required=["api_key", "model"], link="https://infistar.cc"),
    "image_api": dict(label="Texto a imagen compatible con OpenAI", category="image",
                      fields=["base_url", "api_key", "model", "size"], required=["api_key", "base_url"],
                      link="https://platform.openai.com/api-keys"),
    "elevenlabs": dict(label="ElevenLabs (voz y música IA)", category="voice",
                       fields=["api_key", "voice_id", "tts_model", "music_model"], required=["api_key"],
                       link="https://elevenlabs.io/app/settings/api-keys"),
    "azure": dict(label="Azure Speech", category="voice", fields=["speech_key", "speech_region", "voice"],
                  required=["speech_key", "speech_region"],
                  link="https://portal.azure.com/#view/Microsoft_Azure_ProjectOxford/CognitiveServicesHub/~/SpeechServices"),
    "siliconflow": dict(label="SiliconFlow TTS", category="voice", fields=["api_key"], required=["api_key"],
                        link="https://cloud.siliconflow.cn/account/ak"),
    "mimo": dict(label="Xiaomi MiMo TTS", category="voice", fields=["api_key", "model"], required=["api_key"],
                 link="https://platform.xiaomimimo.com"),
    "minimax": dict(label="MiniMax TTS", category="voice", fields=["api_key", "voice_id", "model", "base_url"],
                    required=["api_key", "base_url"],
                    link="https://platform.minimax.io/user-center/basic-information/interface-key"),
    "fish_audio": dict(label="Fish Audio TTS", category="voice", fields=["api_key", "model", "voice_id"],
                       required=["api_key"], link="https://fish.audio/app/developers"),
    "voxcpm": dict(label="VoxCPM TTS (ModelBest)", category="voice", fields=["api_key", "model_id", "base_url"],
                   required=["api_key", "model_id", "base_url"], link="https://platform.modelbest.cn/console/"),
    "upload_post": dict(label="Upload-Post (publica en TikTok, Instagram, YouTube y Facebook)", category="publish",
                        fields=["api_key", "username", "facebook_page_id"], required=["api_key", "username"],
                        link="https://app.upload-post.com/api-keys"),
    "huggingface": dict(label="HuggingFace (descargas de modelos)", category="other", fields=["token"],
                        required=["token"], link="https://huggingface.co/settings/tokens"),
}


def credential_missing(section):
    """Required fields of a credential that are still empty."""
    return [f for f in CREDENTIALS[section]["required"] if not get_key(section, f)]


# ---------------------------------------------------------------------------
# Services started by the tool cells (used to warn before a run instead of failing halfway)
# ---------------------------------------------------------------------------
SERVICES = {
    "ollama": dict(label="Ollama", cell="Celda 3", url=f"http://127.0.0.1:{PORTS['ollama_api']}/"),
    "forge": dict(label="Forge (FLUX)", cell="Celda 4", url=f"http://127.0.0.1:{PORTS['forge']}/sdapi/v1/options"),
    "comfy": dict(label="ComfyUI", cell="Celda 5", url=f"http://127.0.0.1:{PORTS['comfy']}/"),
    "f5": dict(label="F5-TTS", cell="Celda 6", url=None),
}
F5_VENV_DIR, F5_PYTHON = venv_paths("f5tts")


def service_ready(name):
    if name == "f5":
        return os.path.exists(f"{F5_VENV_DIR}/bin/f5-tts_infer-cli")
    return http_ok(SERVICES[name]["url"], timeout=3)


# ---------------------------------------------------------------------------
# Provider registry. Every kind of content (text, images, clips, voice, music) picks its provider on its
# own, local or external; the assembly accepts any combination because everything becomes a file first.
# ---------------------------------------------------------------------------
KIND_LABELS = {"local": "🖥️ Local", "external": "☁️ Externo"}
BASE_LLM_PROVIDERS = {
    "Ollama (local)": "ollama",
    "OpenAI (ChatGPT)": "openai",
    "Anthropic (Claude)": "anthropic",
    "Google Gemini": "gemini",
}


def llm_provider_choices():
    """Display name -> provider key. Every [openai_compatible.<name>] in the TOML becomes its own entry."""
    choices = dict(BASE_LLM_PROVIDERS)
    for name in compat_provider_names():
        choices[f"{name} (OpenAI-compatible)"] = f"compat:{name}"
    return choices


def llm_names_by_kind(kind):
    return [name for name, key in llm_provider_choices().items() if (key == "ollama") == (kind == "local")]


def llm_kind(name):
    return "local" if llm_provider_choices().get(name) == "ollama" else "external"


LANGUAGES = {   # code -> (label, name used in prompts)
    "es": ("Español", "neutral Latin American Spanish"), "en": ("English", "English"),
    "pt": ("Português", "Brazilian Portuguese"), "fr": ("Français", "French"),
    "it": ("Italiano", "Italian"), "de": ("Deutsch", "German"),
}
TONES = {
    "informativo": "informative and clear", "divertido": "fun and witty", "inspirador": "inspiring and emotional",
    "dramatico": "dramatic and suspenseful", "misterioso": "mysterious, full of intrigue",
    "educativo": "educational, step by step", "polemico": "bold and provocative (but factual)",
}

CLIP_TYPES = {
    "stock": "📦 Stock (gratis)",
    "own": "📂 Mis clips",
    "local_ai": "🖥️ IA local (ComfyUI)",
    "external_ai": "☁️ IA externa (de pago)",
    "ai_image": "🖼️ Fotos IA con movimiento",
}
CLIP_PROVIDERS = {
    "pexels": dict(type="stock", label="Pexels", creds=["pexels"]),
    "pixabay": dict(type="stock", label="Pixabay", creds=["pixabay"]),
    "coverr": dict(type="stock", label="Coverr", creds=["coverr"]),
    "upload": dict(type="own", label="Archivos que subo aquí"),
    "footage": dict(type="own", label="Carpeta footage de mi Drive"),
    # accepts_image: the clip can start from an image made by your image engine (image-to-video).
    "wan22": dict(type="local_ai", label="Wan 2.2 TI2V-5B", services=["comfy"], accepts_image=True),
    "ltxv": dict(type="local_ai", label="LTX-Video 2B", services=["comfy"], accepts_image=True),
    "gemini_omni": dict(type="external_ai", label="Google Gemini Omni (sucesor de Veo, con audio)", creds=["gemini"],
                        accepts_image=True),
    # Text-to-video services: one paid generation per scene (create task -> poll -> download).
    "wavespeed": dict(type="external_ai", label="WaveSpeed AI", creds=["wavespeed"]),
    "volcengine_seedance": dict(type="external_ai", label="Volcano Engine Seedance", creds=["volcengine_seedance"]),
    "ofox": dict(type="external_ai", label="OFox AI Video", creds=["ofox"]),
    "muapi": dict(type="external_ai", label="MuAPI AI Video", creds=["muapi"]),
    "metaso_minimax": dict(type="external_ai", label="Metaso · MiniMax H3", creds=["metaso_minimax"]),
    "infistar": dict(type="external_ai", label="Infistar AI Video", creds=["infistar"]),
    "ai_photos": dict(type="ai_image", label="Fotos de tu motor de imagen, con movimiento"),
}

MEDIA_VIDEO_EXTS = (".mp4", ".mov", ".m4v", ".webm", ".mkv")
MEDIA_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")


def footage_files():
    """Your clips and photos in Drive/footage (sub-folders included), in name order."""
    found = []
    for root, _, files in os.walk(FOOTAGE_DIR):
        found += [os.path.join(root, f) for f in files if f.lower().endswith(MEDIA_VIDEO_EXTS + MEDIA_IMAGE_EXTS)]
    return sorted(found)


EDGE_VOICES = [
    ("México · Jorge (hombre)", "es-MX-JorgeNeural"), ("México · Dalia (mujer)", "es-MX-DaliaNeural"),
    ("Costa Rica · Juan (hombre)", "es-CR-JuanNeural"), ("Costa Rica · María (mujer)", "es-CR-MariaNeural"),
    ("España · Álvaro (hombre)", "es-ES-AlvaroNeural"), ("España · Elvira (mujer)", "es-ES-ElviraNeural"),
    ("EE. UU. · Alonso (hombre)", "es-US-AlonsoNeural"), ("EE. UU. · Paloma (mujer)", "es-US-PalomaNeural"),
    ("English · Andrew (man)", "en-US-AndrewNeural"), ("English · Ava (woman)", "en-US-AvaNeural"),
    ("Português · Antonio (homem)", "pt-BR-AntonioNeural"), ("Português · Francisca (mulher)", "pt-BR-FranciscaNeural"),
]
AZURE_VOICES = [(label, value) for label, value in EDGE_VOICES] + [
    ("Multilingüe · Andrew", "en-US-AndrewMultilingualNeural"), ("Multilingüe · Ava", "en-US-AvaMultilingualNeural"),
]
VOICE_TYPES = {"local": "🆓 Local / gratis", "external": "☁️ API externa"}
# voices: (label, value). voice_from: a default voice read from the keys file.
VOICE_PROVIDERS = {
    "edge": dict(type="local", label="Edge-TTS", voices=EDGE_VOICES),
    "f5": dict(type="local", label="F5-TTS (clona tu voz)", services=["f5"],
               voices=[("Muestra subida o la primera de Drive/voices", "sample")]),
    "elevenlabs": dict(type="external", label="ElevenLabs", creds=["elevenlabs"],
                       voices=[], voice_from=("elevenlabs", "voice_id")),
    "openai_tts": dict(type="external", label="OpenAI TTS", creds=["openai"],
                       voices=[(v.capitalize(), v) for v in ("onyx", "alloy", "echo", "fable", "nova", "shimmer")]),
    "azure": dict(type="external", label="Azure Speech", creds=["azure"], voices=AZURE_VOICES,
                  voice_from=("azure", "voice")),
    "gemini_tts": dict(type="external", label="Google Gemini TTS", creds=["gemini"],
                       voices=[(f"{v} · {s}", v) for v, s in (
                           ("Charon", "Informative"), ("Kore", "Firm"), ("Puck", "Upbeat"), ("Zephyr", "Bright"),
                           ("Fenrir", "Excitable"), ("Leda", "Youthful"), ("Orus", "Firm"), ("Aoede", "Breezy"))]),
    "siliconflow": dict(type="external", label="SiliconFlow TTS", creds=["siliconflow"],
                        voices=[(f"{v.capitalize()} ({g})", f"FunAudioLLM/CosyVoice2-0.5B:{v}") for v, g in (
                            ("alex", "hombre"), ("benjamin", "hombre"), ("charles", "hombre"), ("david", "hombre"),
                            ("anna", "mujer"), ("bella", "mujer"), ("claire", "mujer"), ("diana", "mujer"))]),
    "mimo": dict(type="external", label="Xiaomi MiMo TTS", creds=["mimo"],
                 voices=[(f"{v} ({g})", v) for v, g in (
                     ("Milo", "hombre"), ("Dean", "hombre"), ("Mia", "mujer"), ("Chloe", "mujer"),
                     ("mimo_default", "mujer"))]),
    "minimax": dict(type="external", label="MiniMax TTS", creds=["minimax"],
                    voices=[], voice_from=("minimax", "voice_id")),
    "fish_audio": dict(type="external", label="Fish Audio TTS", creds=["fish_audio"], voice_from=("fish_audio", "voice_id"),
                       voices=[("Clear Male", "7b6131ba75ba47c98a46c847db729ab6"),
                               ("Clear Female", "2324c907b9a94c64ab4afb941e5b3408")]),
    "voxcpm": dict(type="external", label="VoxCPM TTS", creds=["voxcpm"], voices=[("Default", "default")]),
}

IMAGE_PROVIDERS = {   # the image engine: cover, first frame of each clip and AI photos all use the same one
    "flux": dict(label="FLUX (local)", kind="local", services=["forge"]),
    "nano_banana": dict(label="Nano Banana (Google Gemini)", kind="external", creds=["gemini"]),
    "image_api": dict(label="API compatible con OpenAI (externa)", kind="external", creds=["image_api"]),
}
STYLES = {   # how the reel looks and is told; the LLM adapts script, images and motion to it
    "auto": "✨ Automático", "narrated": "🎥 Narrado con imágenes", "animated": "🧸 Animado (3D tipo Pixar)",
    "cartoon": "🖍️ Caricatura 2D", "talking_objects": "🗣️ Objetos que hablan", "listicle": "🔢 Top / datos curiosos",
    "story": "📖 Historia (storytime)", "pov": "👀 POV", "tutorial": "🛠️ Tutorial paso a paso",
    "motivational": "🔥 Motivacional", "cinematic": "🎬 Cinemático realista",
}
MUSIC_SOURCES = {
    "none": dict(label="Sin música"),
    "library": dict(label="Mis canciones (Drive/music)"),
    "elevenlabs": dict(label="IA · ElevenLabs Music (de pago)", creds=["elevenlabs"]),
}


def clip_provider_choices(clip_type):
    return [(p["label"], pid) for pid, p in CLIP_PROVIDERS.items() if p["type"] == clip_type]


def voice_provider_choices(voice_type):
    return [(p["label"], pid) for pid, p in VOICE_PROVIDERS.items() if p["type"] == voice_type]


def voice_choices(provider):
    """(label, value) list for the voice dropdown; providers with a configurable default read it from the keys."""
    p = VOICE_PROVIDERS[provider]
    choices = list(p["voices"])
    if p.get("voice_from"):
        voice_id = get_key(*p["voice_from"])
        if voice_id and voice_id not in [v for _, v in choices]:
            choices.insert(0, (f"{voice_id} (de tus claves)", voice_id))
    return choices


def music_files():
    """Your songs in Drive/music, including the ones generated with AI (Drive/music/ai)."""
    exts = (".mp3", ".m4a", ".wav", ".ogg")
    found = []
    for folder in (MUSIC_DIR, MUSIC_AI_DIR):
        if os.path.isdir(folder):
            found += [os.path.join(folder, f) for f in os.listdir(folder) if f.lower().endswith(exts)]
    return sorted(found)


def check_selection(llm_key, clip_provider, voice_provider, image_provider, music_source, confirm_paid=False,
                    own_clips=0, make_cover=True, use_keyframes=True):
    """Everything a run needs, checked up front. Returns (errors, warnings); errors block the run.
    No provider depends on another: each one only needs its own key or its own tool running.
    image_provider: the image engine, used for the cover, the first frame of clips that accept one and AI photos.
    own_clips: how many files were uploaded for the "upload" clip source."""
    errors, warnings = [], []
    needed_services, needed_creds = set(), {}

    def need(entry, what):
        for s in entry.get("creds", []):
            needed_creds.setdefault(s, [])
            if what not in needed_creds[s]:
                needed_creds[s].append(what)
        needed_services.update(entry.get("services", []))

    # Text
    if llm_key == "ollama":
        needed_services.add("ollama")
    elif llm_key.startswith("compat:"):
        name = llm_key.split(":", 1)[1]
        if not get_compat(name, "api_key"):
            errors.append(f"Falta la API key de <b>{name}</b> (servicio compatible con OpenAI) para el guion.")
        if not get_compat(name, "base_url"):
            errors.append(f"Falta la Base URL de <b>{name}</b>.")
    else:
        need({"creds": [llm_key]}, "el guion")

    # Clips
    clip = CLIP_PROVIDERS[clip_provider]
    need(clip, f"los clips ({clip['label']})")
    if clip["type"] == "external_ai":
        if confirm_paid:
            warnings.append(f"{clip['label']} cobra por cada clip generado (uno por escena).")
        else:
            errors.append(f"{clip['label']} cobra por cada clip: marca «Confirmo el cobro» para continuar.")
    if clip.get("accepts_image") and not use_keyframes and clip["type"] == "local_ai":
        warnings.append(f"Sin imagen base: {clip['label']} generará cada clip solo desde el texto (texto a video).")
    if clip_provider == "upload" and not own_clips:
        errors.append("Sube al menos un clip o foto en «Mis clips».")
    if clip_provider == "footage" and not footage_files():
        errors.append(f"La carpeta <b>footage</b> de tu Drive está vacía: copia tus clips o fotos en {FOOTAGE_DIR}.")

    # Image engine: only when something actually uses it
    uses = [what for what, used in (("la portada", make_cover),
                                     ("la imagen base de cada clip", clip.get("accepts_image") and use_keyframes),
                                     ("las fotos de los clips", clip["type"] == "ai_image")) if used]
    if uses:
        need(IMAGE_PROVIDERS[image_provider], " y ".join(uses))

    # Voice
    voice = VOICE_PROVIDERS[voice_provider]
    need(voice, f"la voz ({voice['label']})")

    # Music
    need(MUSIC_SOURCES[music_source], "la música IA")
    if music_source == "library" and not music_files():
        warnings.append(f"No hay canciones en {MUSIC_DIR}: sube tus MP3 ahí o elige música IA. "
                        "El reel saldrá sin música.")
    if music_source == "elevenlabs":
        warnings.append("ElevenLabs Music cobra por canción y requiere un plan de pago; "
                        "la canción queda en Drive/music/ai para reutilizarla gratis.")

    for section, uses in needed_creds.items():
        missing = credential_missing(section)
        if missing:
            fields = ", ".join(FIELD_LABELS.get(f, f) for f in missing)
            what = ", ".join(uses[:-1]) + " y " + uses[-1] if len(uses) > 1 else uses[0]
            errors.append(f"Falta {fields} de <b>{CREDENTIALS[section]['label']}</b> para {what}. "
                          f"Agrégala en la pestaña 🔑 Claves API.")
    for name in sorted(needed_services):
        if not service_ready(name):
            svc = SERVICES[name]
            verb = "no está instalado" if name == "f5" else "no está corriendo"
            errors.append(f"<b>{svc['label']}</b> {verb}: ejecuta la {svc['cell']}.")
    return errors, warnings


# ---------------------------------------------------------------------------
# Voice synthesis (shared by the Audio Studio and the Control Center). Every provider is called directly.
# ---------------------------------------------------------------------------
def _save_bytes(data, path):
    with open(path, "wb") as f:
        f.write(data)
    return path


def _convert_audio(data, output_path, input_args=()):
    """Audio bytes in any format ffmpeg reads (raw PCM needs input_args like -f s16le -ar 24000 -ac 1)."""
    source = output_path + ".source"
    _save_bytes(data, source)
    r = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *input_args, "-i", source,
                        output_path], capture_output=True, text=True)
    os.remove(source)
    if r.returncode != 0 or not os.path.exists(output_path):
        raise RuntimeError("ffmpeg no pudo convertir el audio: " + r.stderr[-300:])
    return output_path


def _edge_tts(text, voice, output_path):
    import asyncio, edge_tts

    async def speak():
        await edge_tts.Communicate(text, voice or "es-MX-JorgeNeural").save(output_path)
    try:
        asyncio.get_running_loop()
        loop_running = True             # Jupyter's main thread already runs an event loop
    except RuntimeError:
        loop_running = False
    if loop_running:
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(1) as pool:
            pool.submit(lambda: asyncio.run(speak())).result()
    else:
        asyncio.run(speak())
    return output_path


def _elevenlabs_tts(text, voice, output_path):
    voice_id = voice or get_key("elevenlabs", "voice_id")
    if not voice_id:
        raise RuntimeError("Escribe un Voice ID de ElevenLabs (o guárdalo en la pestaña 🔑 Claves API).")
    r = requests.post(f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}", timeout=300,
                      params={"output_format": "mp3_44100_128"},
                      headers={"xi-api-key": get_key("elevenlabs", "api_key")},
                      json={"text": text, "model_id": get_key("elevenlabs", "tts_model", "eleven_multilingual_v2"),
                            "voice_settings": {"stability": 0.5, "similarity_boost": 0.75}})
    r.raise_for_status()
    return _save_bytes(r.content, output_path)


def _openai_tts(text, voice, output_path):
    r = requests.post("https://api.openai.com/v1/audio/speech", timeout=300,
                      headers={"Authorization": f"Bearer {get_key('openai', 'api_key')}"},
                      json={"model": get_key("openai", "tts_model", "tts-1-hd"), "input": text,
                            "voice": voice or get_key("openai", "tts_voice", "onyx"), "response_format": "mp3"})
    r.raise_for_status()
    return _save_bytes(r.content, output_path)


def _azure_tts(text, voice, output_path):
    """Azure Speech REST API with SSML (no SDK)."""
    from xml.sax.saxutils import escape
    voice = voice or get_key("azure", "voice", "es-MX-JorgeNeural")
    locale = "-".join(voice.split("-")[:2])
    ssml = (f'<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="{escape(locale)}">'
            f'<voice name="{escape(voice)}">{escape(text)}</voice></speak>')
    r = requests.post(f"https://{get_key('azure', 'speech_region')}.tts.speech.microsoft.com/cognitiveservices/v1",
                      data=ssml.encode("utf-8"), timeout=300,
                      headers={"Ocp-Apim-Subscription-Key": get_key("azure", "speech_key"),
                               "Content-Type": "application/ssml+xml",
                               "X-Microsoft-OutputFormat": "audio-24khz-96kbitrate-mono-mp3",
                               "User-Agent": "YT-Channel-Studio"})
    r.raise_for_status()
    return _save_bytes(r.content, output_path)


def _gemini_tts(text, voice, output_path):
    """Gemini returns raw 16-bit PCM (24 kHz mono) in base64; ffmpeg turns it into MP3."""
    import base64
    model = get_key("gemini", "tts_model", "gemini-3.8-flash-tts")
    r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                      params={"key": get_key("gemini", "api_key")}, timeout=600,
                      json={"contents": [{"parts": [{"text": text}]}],
                            "generationConfig": {"responseModalities": ["AUDIO"], "speechConfig": {
                                "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice or "Kore"}}}}})
    r.raise_for_status()
    parts = r.json()["candidates"][0]["content"]["parts"]
    inline = next((p.get("inlineData") or p.get("inline_data") for p in parts
                   if p.get("inlineData") or p.get("inline_data")), None)
    if not inline:
        raise RuntimeError("Gemini no devolvió audio.")
    rate = re.search(r"rate=(\d+)", inline.get("mimeType") or inline.get("mime_type") or "")
    return _convert_audio(base64.b64decode(inline["data"]), output_path,
                          ["-f", "s16le", "-ar", rate.group(1) if rate else "24000", "-ac", "1"])


def _siliconflow_tts(text, voice, output_path):
    """voice = 'model:speaker', e.g. FunAudioLLM/CosyVoice2-0.5B:alex."""
    model = voice.rsplit(":", 1)[0] if ":" in voice else "FunAudioLLM/CosyVoice2-0.5B"
    r = requests.post("https://api.siliconflow.cn/v1/audio/speech", timeout=300,
                      headers={"Authorization": f"Bearer {get_key('siliconflow', 'api_key')}"},
                      json={"model": model, "input": text, "voice": voice, "response_format": "mp3",
                            "sample_rate": 32000, "stream": False, "speed": 1.0, "gain": 0})
    r.raise_for_status()
    return _save_bytes(r.content, output_path)


def _mimo_tts(text, voice, output_path):
    """OpenAI-style chat completion: the text to read goes as the assistant turn, WAV comes back in base64."""
    import base64
    r = requests.post("https://api.xiaomimimo.com/v1/chat/completions", timeout=300,
                      headers={"Authorization": f"Bearer {get_key('mimo', 'api_key')}"},
                      json={"model": get_key("mimo", "model", "mimo-v2.5-tts"),
                            "messages": [{"role": "user",
                                          "content": "Read this in a natural, clear voice for a short video."},
                                         {"role": "assistant", "content": text}],
                            "audio": {"format": "wav", "voice": voice or "mimo_default"}})
    r.raise_for_status()
    audio = (r.json()["choices"][0]["message"].get("audio") or {}).get("data")
    if not audio:
        raise RuntimeError("MiMo no devolvió audio.")
    return _convert_audio(base64.b64decode(audio), output_path)


def _minimax_tts(text, voice, output_path):
    voice_id = voice or get_key("minimax", "voice_id")
    if not voice_id:
        raise RuntimeError("Escribe un Voice ID de MiniMax (o guárdalo en la pestaña 🔑 Claves API).")
    r = requests.post(get_key("minimax", "base_url", "https://api.minimax.io/v1/t2a_v2"), timeout=300,
                      headers={"Authorization": f"Bearer {get_key('minimax', 'api_key')}"},
                      json={"model": get_key("minimax", "model", "speech-2.8-hd"), "text": text, "stream": False,
                            "language_boost": "auto", "output_format": "hex",
                            "voice_setting": {"voice_id": voice_id, "speed": 1.0, "vol": 1.0, "pitch": 0},
                            "audio_setting": {"sample_rate": 32000, "bitrate": 128000, "format": "mp3", "channel": 1}})
    r.raise_for_status()
    body = r.json()
    base = body.get("base_resp") or {}
    if base.get("status_code") != 0:
        raise RuntimeError(f"MiniMax: {base.get('status_msg') or body}")
    audio = (body.get("data") or {}).get("audio")
    if not audio:
        raise RuntimeError("MiniMax no devolvió audio.")
    return _save_bytes(bytes.fromhex(audio), output_path)


def _fish_audio_tts(text, voice, output_path):
    payload = {"text": text, "format": "mp3"}
    reference = voice or get_key("fish_audio", "voice_id")
    if reference:
        payload["reference_id"] = reference
    r = requests.post("https://api.fish.audio/v1/tts", json=payload, timeout=300,
                      headers={"Authorization": f"Bearer {get_key('fish_audio', 'api_key')}",
                               "model": get_key("fish_audio", "model", "s2.1-pro-free")})
    r.raise_for_status()
    return _save_bytes(r.content, output_path)


def _voxcpm_tts(text, voice, output_path):
    """ModelBest streams base64 WAV chunks as server-sent events; joined they form one WAV file."""
    import base64
    base = get_key("voxcpm", "base_url", "https://api.modelbest.cn/v1").rstrip("/")
    chunks, done = [], False
    with requests.post(f"{base}/audio/speech", stream=True, timeout=600,
                       headers={"Authorization": f"Bearer {get_key('voxcpm', 'api_key')}",
                                "Accept": "text/event-stream"},
                       json={"model": get_key("voxcpm", "model_id"), "input": text, "voice": voice or "default",
                             "response_format": "wav", "stream": True}) as r:
        r.raise_for_status()
        for line in r.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            event = json.loads(data)
            if event.get("type") == "speech.audio.delta" and event.get("audio"):
                chunks.append(base64.b64decode(event["audio"]))
            elif event.get("type") == "speech.audio.done":
                done = True
                break
            elif event.get("error"):
                raise RuntimeError(f"VoxCPM: {event['error']}")
    if not chunks or not done:
        raise RuntimeError("VoxCPM cortó el audio antes de terminar.")
    return _convert_audio(b"".join(chunks), output_path)


def _f5_tts(text, ref_audio, ref_text, output_path):
    cli = f"{F5_VENV_DIR}/bin/f5-tts_infer-cli"
    if not os.path.exists(cli):
        raise RuntimeError("F5-TTS no está instalado: ejecuta la Celda 6 (Estudio de voz).")
    if not ref_audio or not os.path.exists(ref_audio):
        samples = sorted(os.path.join(VOICES_DIR, f) for f in os.listdir(VOICES_DIR) if f.endswith((".wav", ".mp3")))
        if not samples:
            raise RuntimeError(f"Sube una muestra de 5-10 s o guarda una en {VOICES_DIR}")
        ref_audio = samples[0]
    wav_path = os.path.splitext(output_path)[0] + ".wav"
    cmd = [cli, "--ref_audio", ref_audio, "--gen_text", text,
           "--output_dir", os.path.dirname(wav_path), "--output_file", os.path.basename(wav_path)]
    if (ref_text or "").strip():
        cmd += ["--ref_text", ref_text.strip()]
    r = subprocess.run(cmd, capture_output=True, text=True, env=venv_env(F5_VENV_DIR))
    if r.returncode != 0 or not os.path.exists(wav_path):
        raise RuntimeError("F5-TTS falló:\n" + (r.stderr or r.stdout)[-1500:])
    return wav_path


VOICE_ENGINES = {
    "edge": _edge_tts, "elevenlabs": _elevenlabs_tts, "openai_tts": _openai_tts, "azure": _azure_tts,
    "gemini_tts": _gemini_tts, "siliconflow": _siliconflow_tts, "mimo": _mimo_tts, "minimax": _minimax_tts,
    "fish_audio": _fish_audio_tts, "voxcpm": _voxcpm_tts,
}


def synthesize_voice(text, provider, voice, ref_audio=None, ref_text="", output_path=None):
    """Return (audio_path, status_message); audio_path is None on failure."""
    output_path = output_path or f"{WORK_DIR}/speech.mp3"
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    label = VOICE_PROVIDERS[provider]["label"]
    try:
        if provider == "f5":
            return _f5_tts(text, ref_audio, ref_text, output_path), f"Voz clonada con {label}."
        path = VOICE_ENGINES[provider](text, (voice or "").strip(), output_path)
        if not os.path.exists(path) or os.path.getsize(path) < 1000:
            return None, f"{label} devolvió un audio vacío."
        return path, f"Voz generada con {label}."
    except requests.HTTPError as exc:
        return None, f"{label} rechazó la petición: HTTP {exc.response.status_code} {exc.response.text[:300]}"
    except Exception as exc:
        return None, f"{label} falló: {exc}"


# ---------------------------------------------------------------------------
# Connection tests for the key tab. Each returns (status, message): True ok, False rejected, None unknown.
# Unfamiliar services are probed twice (real key and a fake one), so a public endpoint that answers
# 200 to anybody is never mistaken for a valid key.
# ---------------------------------------------------------------------------
_BOGUS_KEY = "invalid-key-for-connection-test"


def _probe(url, headers=None, params=None):
    try:
        r = requests.get(url, headers=headers or {}, params=params or {}, timeout=15)
        return r.status_code, r.text[:300]
    except requests.RequestException as exc:
        return None, str(exc)[:200]


def _verdict(code, text, ok_message="Conexión correcta: la clave funciona."):
    if code is None:
        return None, f"Sin conexión con el servicio: {text}"
    if 200 <= code < 300:
        return True, ok_message
    if code in (401, 403) or (code == 400 and "key" in text.lower()):
        return False, f"Clave rechazada (HTTP {code}). Revisa que esté completa y activa."
    if code == 429:
        return None, "El servicio respondió «demasiadas peticiones» (HTTP 429): la clave existe, reintenta en un rato."
    return None, f"No se pudo verificar: el servicio respondió HTTP {code}."


def _probe_auth_sensitive(url, make_headers, key):
    code, text = _probe(url, make_headers(key))
    if code is None or code in (401, 403):
        return _verdict(code, text)
    bogus_code, _ = _probe(url, make_headers(_BOGUS_KEY))
    if 200 <= (code or 0) < 300 and bogus_code in (401, 403):
        return True, "Conexión correcta: la clave funciona."
    if 200 <= (code or 0) < 300:
        return None, "El servicio responde, pero ese endpoint no exige clave: no se puede confirmar sin generar (con costo)."
    return None, f"No se pudo verificar sin generar contenido (HTTP {code}). La clave queda guardada."


def _bearer(key):
    return {"Authorization": f"Bearer {key}"}


def test_credential(section, values):
    """values: the fields as currently typed in the UI (tested before saving)."""
    v = {k: (values.get(k) or "").strip() for k in CREDENTIALS[section]["fields"]}
    missing = [FIELD_LABELS.get(f, f) for f in CREDENTIALS[section]["required"] if not v.get(f)]
    if missing:
        return False, "Falta: " + ", ".join(missing)
    key = v.get("api_key") or v.get("speech_key") or v.get("token")

    if section == "openai":
        return _verdict(*_probe("https://api.openai.com/v1/models", _bearer(key)))
    if section == "anthropic":
        return _verdict(*_probe("https://api.anthropic.com/v1/models",
                                {"x-api-key": key, "anthropic-version": "2023-06-01"}))
    if section == "gemini":
        return _verdict(*_probe("https://generativelanguage.googleapis.com/v1beta/models", params={"key": key}))
    if section == "pexels":
        return _verdict(*_probe("https://api.pexels.com/videos/search", {"Authorization": key},
                                {"query": "nature", "per_page": 1}))
    if section == "pixabay":
        return _verdict(*_probe("https://pixabay.com/api/videos/", params={"key": key, "q": "nature", "per_page": 3}))
    if section == "coverr":
        return _probe_auth_sensitive("https://api.coverr.co/videos?query=nature&page_size=1", _bearer, key)
    if section == "image_api":
        return _verdict(*_probe(f"{v['base_url'].rstrip('/')}/models", _bearer(key)))
    if section == "elevenlabs":
        code, text = _probe("https://api.elevenlabs.io/v1/user/subscription", {"xi-api-key": key})
        if code == 401 and "missing_permissions" in text:
            return True, "La clave es válida (sin permiso para ver la suscripción, pero sí puede generar si lo tiene)."
        return _verdict(code, text)
    if section == "azure":
        return _verdict(*_probe(f"https://{v['speech_region']}.tts.speech.microsoft.com/cognitiveservices/voices/list",
                                {"Ocp-Apim-Subscription-Key": key}))
    if section == "siliconflow":
        return _verdict(*_probe("https://api.siliconflow.cn/v1/models", _bearer(key)))
    if section == "huggingface":
        return _verdict(*_probe("https://huggingface.co/api/whoami-v2", _bearer(key)))
    endpoints = {
        "mimo": ("https://api.xiaomimimo.com/v1/models", _bearer),
        "minimax": ("https://api.minimax.io/v1/models", _bearer),
        "fish_audio": ("https://api.fish.audio/model?page_size=1", _bearer),
        "voxcpm": (f"{v.get('base_url', '').rstrip('/')}/models", _bearer),
        "ofox": ("https://api.ofox.ai/v1/models", _bearer),
        "volcengine_seedance": ("https://ark.cn-beijing.volces.com/api/v3/models", _bearer),
        "infistar": ("https://infistar.cc/v1/models", _bearer),
        "metaso_minimax": ("https://metaso.cn/api/minimax/models", _bearer),
        "wavespeed": ("https://api.wavespeed.ai/api/v3/balance", _bearer),
        "muapi": ("https://api.muapi.ai/api/v1/account/balance", lambda k: {"x-api-key": k}),
        "upload_post": ("https://api.upload-post.com/api/uploadposts/history?limit=1",
                        lambda k: {"Authorization": f"Apikey {k}"}),
    }
    if section in endpoints:
        url, make_headers = endpoints[section]
        return _probe_auth_sensitive(url, make_headers, key)
    return None, "Este proveedor no tiene prueba de conexión."


def test_compat(base_url, api_key):
    if not base_url.strip() or not api_key.strip():
        return False, "Falta: " + ", ".join(n for n, x in (("Base URL", base_url), ("API key", api_key)) if not x.strip())
    return _verdict(*_probe(f"{base_url.strip().rstrip('/')}/models", _bearer(api_key.strip())))
