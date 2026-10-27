"""Cell 5: ComfyUI (local AI video: Wan 2.2 / LTX-Video) in a Python 3.11 venv."""
from ..lib import *

COMFY_DIR = f"{APPS_DIR}/ComfyUI"
COMFY_LOG = log_path("comfy_server")
CUSTOM_NODES = [
    "https://github.com/city96/ComfyUI-GGUF.git",
    "https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite.git",
    "https://github.com/Lightricks/ComfyUI-LTXVideo.git",
]
VIDEO_MODELS = {   # name -> list of (url, Drive sub-folder, file name)
    "wan22": [
        ("https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/diffusion_models/wan2.2_ti2v_5B_fp16.safetensors", "video", "wan2.2_ti2v_5B_fp16.safetensors"),
        ("https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/vae/wan2.2_vae.safetensors", "vae", "wan2.2_vae.safetensors"),
        ("https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged/resolve/main/split_files/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors", "clip", "umt5_xxl_fp8_e4m3fn_scaled.safetensors"),
    ],
    "ltxv": [   # its T5 text encoder is the one Cell 4 downloads (t5xxl_fp8_e4m3fn)
        ("https://huggingface.co/Lightricks/LTX-Video/resolve/main/ltx-video-2b-v0.9.5.safetensors", "video", "ltx-video-2b-v0.9.5.safetensors"),
        ("https://huggingface.co/comfyanonymous/flux_text_encoders/resolve/main/t5xxl_fp8_e4m3fn.safetensors", "clip", "t5xxl_fp8_e4m3fn.safetensors"),
    ],
}
DEFAULT_MODELS = list(VIDEO_MODELS)


def node_dir(repo):
    return f"{COMFY_DIR}/custom_nodes/{repo.rstrip('/').split('/')[-1].replace('.git', '')}"


def start(models=None):
    models = list(models or DEFAULT_MODELS)
    start_cell("Cell 5 · ComfyUI (AI video)")
    os.makedirs(APPS_DIR, exist_ok=True)
    if not os.path.exists(COMFY_DIR):
        run(f"git clone -q https://github.com/comfyanonymous/ComfyUI.git {COMFY_DIR}", "Cloning ComfyUI...")
    for repo in CUSTOM_NODES:
        if not os.path.exists(node_dir(repo)):
            run(f"git clone -q {repo} {node_dir(repo)}", f"Cloning {os.path.basename(node_dir(repo))}...")

    # Isolated Python 3.11 venv (shares the torch wheel cache with the other 3.11 venvs)
    comfy_venv, comfy_python = ensure_venv("comfy", "3.11")
    uv_install(comfy_python, TORCH_SPEC, "Installing PyTorch 2.7.1 + CUDA 12.8...")
    uv_install(comfy_python, f"-r {COMFY_DIR}/requirements.txt", "Installing ComfyUI requirements...")
    for repo in CUSTOM_NODES:
        req = f"{node_dir(repo)}/requirements.txt"
        if os.path.exists(req):
            try:
                uv_install(comfy_python, f"-r {req}", f"Installing requirements for {os.path.basename(node_dir(repo))}...")
            except RuntimeError:
                print(f"  requirements of {os.path.basename(node_dir(repo))} failed; the node may load with reduced features.")
    check_torch(comfy_python, "ComfyUI")

    for name in models:
        for url, subdir, filename in VIDEO_MODELS[name]:
            download_if_missing(url, f"{MODELS_DIR}/{subdir}", filename)
    for src, dst in (("unet", "unet"), ("qwen_edit", "unet"), ("video", "diffusion_models"), ("video", "checkpoints"),
                     ("vae", "vae"), ("clip", "clip"), ("clip", "text_encoders")):
        link_models(f"{MODELS_DIR}/{src}", f"{COMFY_DIR}/models/{dst}")
    print("Models linked into ComfyUI.")

    print("Starting ComfyUI...")             # --enable-cors-header avoids the 403 behind the tunnel
    kill_port(PORTS["comfy"])
    proc = start_background([comfy_python, "main.py", "--listen", "0.0.0.0", "--port", str(PORTS["comfy"]),
                             "--lowvram", "--enable-cors-header", "*"],
                            COMFY_LOG, cwd=COMFY_DIR, env=venv_env(comfy_venv))
    problems, url = [], ""
    if wait_for_http(f"http://127.0.0.1:{PORTS['comfy']}/", proc, COMFY_LOG, timeout=300, label="ComfyUI"):
        url = start_cloudflare_tunnel(PORTS["comfy"], "comfy_tunnel", "ComfyUI (nodes)",
                                      "AI video (Wan 2.2 / LTX-Video) and advanced workflows")
        if not is_public_url(url):
            problems.append(f"No public link yet: {url}")
    else:
        problems.append(f"ComfyUI did not respond: see {COMFY_LOG}")
    finish_cell(not problems, [f"COMFYUI: {url}"] if url else None, problems)
