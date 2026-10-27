"""Cell 4: Forge WebUI (FLUX images, inpainting, covers) in a Python 3.10 venv."""
from ..lib import *

FORGE_DIR = f"{APPS_DIR}/stable-diffusion-webui-forge"
FORGE_LOG = log_path("forge_server")
CLIP_ZIP = "https://github.com/openai/CLIP/archive/d50d76daa670286dd6cacf3bcd80b5e4823fc8e1.zip"

FLUX_MODELS = {   # name -> (url, Drive sub-folder, file name)
    "flux_dev": ("https://huggingface.co/Comfy-Org/flux1-dev/resolve/main/flux1-dev-fp8.safetensors", "unet", "flux1-dev-fp8.safetensors"),
    "flux_fill": ("https://huggingface.co/YarvixPA/FLUX.1-Fill-dev-GGUF/resolve/main/flux1-fill-dev-Q8_0.gguf", "unet", "flux1-fill-dev-Q8_0.gguf"),
    "flux_vae": ("https://huggingface.co/Comfy-Org/Lumina_Image_2.0_Repackaged/resolve/main/split_files/vae/ae.safetensors", "vae", "ae.safetensors"),
    "clip_l": ("https://huggingface.co/comfyanonymous/flux_text_encoders/resolve/main/clip_l.safetensors", "clip", "clip_l.safetensors"),
    "t5xxl": ("https://huggingface.co/comfyanonymous/flux_text_encoders/resolve/main/t5xxl_fp8_e4m3fn.safetensors", "clip", "t5xxl_fp8_e4m3fn.safetensors"),
    "qwen_edit": ("https://huggingface.co/Comfy-Org/Qwen-Image-Edit_ComfyUI/resolve/main/split_files/diffusion_models/qwen_image_edit_fp8_e4m3fn.safetensors", "qwen_edit", "qwen_image_edit.safetensors"),
}
DEFAULT_MODELS = list(FLUX_MODELS)


def install(forge_python):
    """Torch, CLIP, Forge's pinned requirements and the ControlNet extras, all under Forge's pins."""
    constraints = f"-c {FORGE_DIR}/requirements_versions.txt"
    uv_install(forge_python, '"setuptools<70" wheel pip', "Installing compatible setuptools...")
    uv_install(forge_python, TORCH_SPEC, "Installing PyTorch 2.7.1 + CUDA 12.8 (first time: 2-4 min)...")
    uv_install(forge_python, f"--no-build-isolation {CLIP_ZIP}", "Installing CLIP...")
    uv_install(forge_python, f"-r {FORGE_DIR}/requirements_versions.txt", "Installing Forge requirements...")
    uv_install(forge_python, f"{constraints} joblib", "Installing joblib (inpainting extensions)...")
    # Forge's built-in ControlNet extensions pip-install these at launch WITHOUT version pins, and today's
    # mediapipe/opencv pull numpy 2, which breaks scikit-image ("numpy.dtype size changed"). Installing them
    # here under Forge's pins makes Forge find them already present and skip its own unpinned install.
    # onnxruntime (CPU) instead of onnxruntime-gpu: Forge checks for the "onnxruntime" package by name.
    try:
        uv_install(forge_python, f'{constraints} fvcore mediapipe onnxruntime "opencv-python>=4.8.0" svglib',
                   "Pre-installing ControlNet requirements under Forge's pins...")
    except RuntimeError:
        print("Some ControlNet requirements could not be installed; FLUX generation is not affected.")
    try:
        uv_install(forge_python, f"{constraints} insightface", "Installing insightface for ControlNet (compiles, 1-2 min)...")
    except RuntimeError:
        print("insightface could not be installed; Forge still starts, only that feature is missing.")


def imports_ok(forge_python):
    """numpy, scikit-image and OpenCV must load together; a mismatch is what crashes Forge at startup."""
    r = subprocess.run([forge_python, "-c", "import numpy, skimage, cv2; print(numpy.__version__, skimage.__version__, cv2.__version__)"],
                       capture_output=True, text=True)
    return r.returncode == 0, (r.stdout.strip() or r.stderr.strip()[-300:])


def repair_numpy(forge_python):
    """Forge can pip-install unpinned packages on a later launch in the same session; this puts numpy,
    scikit-image and OpenCV back to Forge's versions without recreating the venv."""
    uv_install(forge_python, f'-c {FORGE_DIR}/requirements_versions.txt --reinstall-package numpy '
                             f'--reinstall-package scikit-image --reinstall-package opencv-python numpy scikit-image '
                             f'"opencv-python>=4.8.0"', "Reinstalling numpy, scikit-image and OpenCV with Forge's versions...")


def start(models=None):
    models = list(models or DEFAULT_MODELS)
    start_cell("Cell 4 · Forge WebUI (FLUX images)")
    os.makedirs(APPS_DIR, exist_ok=True)
    if not os.path.exists(FORGE_DIR):
        run(f"git clone -q https://github.com/lllyasviel/stable-diffusion-webui-forge.git {FORGE_DIR}", "Cloning Forge WebUI...")

    # Isolated Python 3.10 venv (Forge pins packages that have no wheels for Colab's newer Python)
    forge_venv, forge_python = ensure_venv("forge", "3.10")
    install(forge_python)
    ok, info = imports_ok(forge_python)
    if not ok:
        print(f"numpy/scikit-image mismatch detected, repairing: {info}")
        repair_numpy(forge_python)
        ok, info = imports_ok(forge_python)
    print(f"numpy / scikit-image / OpenCV: {info}")
    check_torch(forge_python, "Forge")

    for name in models:                                   # skipped when already in Drive
        url, subdir, filename = FLUX_MODELS[name]
        download_if_missing(url, f"{MODELS_DIR}/{subdir}", filename)
    link_models(f"{MODELS_DIR}/unet", f"{FORGE_DIR}/models/Stable-diffusion")
    link_models(f"{MODELS_DIR}/vae", f"{FORGE_DIR}/models/VAE")
    link_models(f"{MODELS_DIR}/clip", f"{FORGE_DIR}/models/text_encoder")

    print("Starting Forge WebUI...")
    kill_port(PORTS["forge"])
    env = venv_env(forge_venv, {
        "TORCH_COMMAND": f"pip install {TORCH_SPEC}",                      # in case Forge tries to reinstall torch
        "PIP_CONSTRAINT": f"{FORGE_DIR}/requirements_versions.txt",        # any pip install Forge runs keeps its pins
    })
    proc = start_background([forge_python, "launch.py", "--api", "--listen", "--port", str(PORTS["forge"]),
                             "--enable-insecure-extension-access", "--cors-allow-origins=*"],
                            FORGE_LOG, cwd=FORGE_DIR, env=env)
    problems = [] if ok else [f"numpy/scikit-image still incompatible: {info}"]
    url = ""
    if wait_for_http(f"http://127.0.0.1:{PORTS['forge']}/", proc, FORGE_LOG, timeout=600, label="Forge"):
        url = start_cloudflare_tunnel(PORTS["forge"], "forge_tunnel", "Forge WebUI (FLUX)",
                                      "Image generation, covers, inpainting/outpainting")
        if not is_public_url(url):
            problems.append(f"No public link yet: {url}")
    else:
        problems.append(f"Forge did not respond: see {FORGE_LOG}")
    finish_cell(not problems, [f"FORGE WEBUI: {url}"] if url else None, problems)
