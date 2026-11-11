"""Reel production: the providers of every kind of content and the step-by-step production.

idea -> storyboard (any LLM; in the idea's language, with English prompts for the AIs) -> publish kit
     -> images (FLUX, Nano Banana or an image API) -> audio (any voice, one narration per scene; optional
     ElevenLabs sound effects) -> clips (stock, your files, local AI, external AI, AI photos) -> music
     -> montage, scene by scene (media.py) -> publishing (Upload-Post: TikTok, Instagram, YouTube, Facebook)
Every step leaves its files in the reel's Drive folder, so a reel can pause for your review after each step,
regenerate any single item, resume after a failure or a disconnection, and be re-assembled later.

produce_reel(), continue_reel(), regenerate_item(), regenerate_storyboard() and reassemble_reel() are
generators: every step yields a snapshot dict (log, stage, pct, storyboard, media, videos, review...) so the UI
can show progress live. They also work from any code cell.
"""
import base64, uuid, glob, random, math, unicodedata, traceback, threading
from datetime import datetime
from .lib import *
from .storyboard import *
from .media import *

OLLAMA_URL = f"http://127.0.0.1:{PORTS['ollama_api']}"
FORGE_URL = f"http://127.0.0.1:{PORTS['forge']}"
COMFY_URL = f"http://127.0.0.1:{PORTS['comfy']}"
NEGATIVE_VIDEO_PROMPT = "blurry, low quality, static image, distorted, deformed, watermark, text, subtitles"


def bearer(key):
    return {"Authorization": f"Bearer {key}"}


# ---------------------------------------------------------------------------
# 1. Text (LLM): storyboard, scene prompts and publish kit
# ---------------------------------------------------------------------------
def list_ollama_models():
    try:
        return [m["name"] for m in requests.get(f"{OLLAMA_URL}/api/tags", timeout=5).json().get("models", [])]
    except Exception:
        return ["qwen2.5:14b", "deepseek-r1:14b"]


def default_model_for(provider_key):
    if provider_key == "ollama":
        models = list_ollama_models()
        return models[0] if models else "qwen2.5:14b"
    if provider_key.startswith("compat:"):
        return get_compat(provider_key.split(":", 1)[1], "chat_model")
    return get_key(provider_key, "chat_model")


_NOT_CHAT = ("embed", "tts", "image", "banana", "audio", "whisper", "dall-e", "omni", "veo", "moderation",
             "transcribe", "realtime", "search", "imagen", "aqa", "learnlm")


def list_llm_models(provider_key):
    """Chat models the provider offers right now (asked to its API), the configured default first.
    Falls back to the default alone when the provider cannot be reached or has no key yet."""
    default = default_model_for(provider_key)
    found = []
    try:
        if provider_key == "ollama":
            found = list_ollama_models()
        elif provider_key == "gemini":
            r = requests.get("https://generativelanguage.googleapis.com/v1beta/models", timeout=15,
                             params={"key": get_key("gemini", "api_key"), "pageSize": 1000})
            r.raise_for_status()
            found = [m["name"].split("/", 1)[-1] for m in r.json().get("models", [])
                     if "generateContent" in m.get("supportedGenerationMethods", [])]
        elif provider_key == "anthropic":
            r = requests.get("https://api.anthropic.com/v1/models", timeout=15, params={"limit": 100},
                             headers={"x-api-key": get_key("anthropic", "api_key"), "anthropic-version": "2023-06-01"})
            r.raise_for_status()
            found = [m["id"] for m in r.json().get("data", [])]
        else:
            if provider_key == "openai":
                base, key = "https://api.openai.com/v1", get_key("openai", "api_key")
            else:
                name = provider_key.split(":", 1)[1]
                base, key = get_compat(name, "base_url"), get_compat(name, "api_key")
            r = requests.get(f"{base.rstrip('/')}/models", headers=bearer(key), timeout=15)
            r.raise_for_status()
            found = sorted(m["id"] for m in r.json().get("data", []))
    except Exception:
        found = []
    found = [m for m in found if not any(word in m.lower() for word in _NOT_CHAT)]
    return ([default] if default else []) + [m for m in found if m != default]


def llm_complete(provider_key, model, system_prompt, user_prompt, json_mode=True):
    """Unified chat completion. Returns the raw text answer."""
    if provider_key == "ollama":
        # num_predict: in JSON mode a local model can fall into an endless run of blank space and hold the reel
        # until the timeout; a cap ends it. keep_alive 0 frees the VRAM for Forge/ComfyUI afterwards.
        payload = {"model": model, "stream": False, "keep_alive": 0, "options": {"num_predict": 4096},
                   "messages": [{"role": "system", "content": system_prompt},
                                {"role": "user", "content": user_prompt}]}
        if json_mode:
            payload["format"] = "json"
        r = requests.post(f"{OLLAMA_URL}/api/chat", json=payload, timeout=600)
        r.raise_for_status()
        return r.json()["message"]["content"]

    if provider_key == "openai" or provider_key.startswith("compat:"):
        if provider_key == "openai":
            label, api_key, base_url = "[openai]", get_key("openai", "api_key"), "https://api.openai.com/v1"
        else:
            name = provider_key.split(":", 1)[1]
            label, api_key, base_url = f"[openai_compatible.{name}]", get_compat(name, "api_key"), get_compat(name, "base_url")
        if not api_key:
            raise RuntimeError(f"Missing api_key in {label} of {KEYS_FILE}")
        if not base_url:
            raise RuntimeError(f"Missing base_url in {label} of {KEYS_FILE}")
        payload = {"model": model, "messages": [{"role": "system", "content": system_prompt},
                                                {"role": "user", "content": user_prompt}]}
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        r = requests.post(f"{base_url.rstrip('/')}/chat/completions", json=payload, timeout=300, headers=bearer(api_key))
        if r.status_code == 400 and json_mode and "response_format" in r.text:
            payload.pop("response_format")     # some compatible APIs reject JSON mode; the parser copes
            r = requests.post(f"{base_url.rstrip('/')}/chat/completions", json=payload, timeout=300,
                              headers=bearer(api_key))
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]

    if provider_key == "anthropic":
        api_key = get_key("anthropic", "api_key")
        if not api_key:
            raise RuntimeError(f"Missing [anthropic] api_key in {KEYS_FILE}")
        r = requests.post("https://api.anthropic.com/v1/messages", timeout=300,
                          headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
                          json={"model": model, "max_tokens": 4000, "system": system_prompt,
                                "messages": [{"role": "user", "content": user_prompt}]})
        r.raise_for_status()
        return "".join(block.get("text", "") for block in r.json()["content"])

    if provider_key == "gemini":
        api_key = get_key("gemini", "api_key")
        if not api_key:
            raise RuntimeError(f"Missing [gemini] api_key in {KEYS_FILE}")
        payload = {"system_instruction": {"parts": [{"text": system_prompt}]},
                   "contents": [{"role": "user", "parts": [{"text": user_prompt}]}]}
        if json_mode:
            payload["generationConfig"] = {"responseMimeType": "application/json"}
        r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                          params={"key": api_key}, json=payload, timeout=300)
        r.raise_for_status()
        return "".join(p.get("text", "") for p in r.json()["candidates"][0]["content"]["parts"])

    raise RuntimeError(f"Unknown LLM provider: {provider_key}")


def extract_json(text):
    """Tolerates <think> blocks, markdown fences and chatter around the JSON object."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    text = re.sub(r"```(?:json)?", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("The model did not return a JSON object:\n" + text[:800])
    return json.loads(text[start:end + 1])



# ---------------------------------------------------------------------------
# 2. Images: FLUX through Forge's API, Nano Banana (Gemini) or any OpenAI-compatible image API
# ---------------------------------------------------------------------------
FLUX_SIZES = {"9:16": (768, 1344), "16:9": (1344, 768), "1:1": (1024, 1024)}
IMAGE_API_SIZES = {"16:9": "1536x1024", "1:1": "1024x1024"}     # 9:16 uses the size saved in your keys


ASPECT_RATIO_TEXT = re.compile(r",?\s*\b(?:(?:vertical|horizontal|square|portrait|landscape)\s+)?"
                               r"(?:9:16|16:9|1:1|4:5|5:4|3:4|4:3|2:3|3:2|21:9)\b", re.I)


def forge_prompt(prompt):
    """Removes ratios such as "vertical 9:16": Forge turns a FLUX image with one into noise (seen on Colab).
    Plans saved before this fix still carry them in their image prompts."""
    return ASPECT_RATIO_TEXT.sub("", prompt).strip(" ,")


def forge_txt2img(prompt, output_path, width=768, height=1344, steps=20, seed=-1):
    """One FLUX image. The flux1-dev-fp8 checkpoint already contains VAE and text encoders."""
    prompt = forge_prompt(prompt)
    payload = {
        "prompt": prompt, "negative_prompt": "", "width": width, "height": height,
        "steps": steps, "cfg_scale": 1, "distilled_cfg_scale": 3.5,
        "sampler_name": "Euler", "scheduler": "Simple", "seed": seed, "batch_size": 1,
        "override_settings": {"sd_model_checkpoint": "flux1-dev-fp8.safetensors",
                              "forge_preset": "flux", "forge_additional_modules": []},
        "override_settings_restore_afterwards": False,
    }
    r = requests.post(f"{FORGE_URL}/sdapi/v1/txt2img", json=payload, timeout=1800)
    r.raise_for_status()
    with open(output_path, "wb") as f:
        f.write(base64.b64decode(r.json()["images"][0]))
    return output_path


def forge_unload():
    try:
        requests.post(f"{FORGE_URL}/sdapi/v1/unload-checkpoint", timeout=60)
    except Exception:
        pass


def image_api_generate(prompt, output_path, size=None):
    """Text-to-image through an OpenAI-compatible /images/generations endpoint."""
    base = get_key("image_api", "base_url").rstrip("/")
    r = requests.post(f"{base}/images/generations", timeout=600, headers=bearer(get_key("image_api", "api_key")),
                      json={"model": get_key("image_api", "model"), "prompt": prompt, "n": 1,
                            "size": size or get_key("image_api", "size", "1024x1536")})
    r.raise_for_status()
    item = r.json()["data"][0]
    data = base64.b64decode(item["b64_json"]) if item.get("b64_json") else requests.get(item["url"], timeout=300).content
    with open(output_path, "wb") as f:
        f.write(data)
    return output_path


GEMINI_API = "https://generativelanguage.googleapis.com/v1beta"


def gemini_interaction(model, content, response_format, timeout=900):
    """One synchronous call to Gemini's Interactions API (used by Nano Banana and Omni)."""
    r = requests.post(f"{GEMINI_API}/interactions", timeout=timeout,
                      headers={"x-goog-api-key": get_key("gemini", "api_key")},
                      json={"model": model, "input": content, "response_format": response_format,
                            "store": False, "stream": False, "background": False})
    if not r.ok:
        raise RuntimeError(f"Gemini ({model}) rejected the request: {api_error(r)}")
    return r.json()


def find_media(body, kind):
    """The first {"type": kind, "data" or "uri": ...} item anywhere in an Interactions response."""
    if isinstance(body, dict):
        if body.get("type") == kind and (body.get("data") or body.get("uri")):
            return body
        items = body.values()
    elif isinstance(body, list):
        items = body
    else:
        return None
    for item in items:
        found = find_media(item, kind)
        if found:
            return found
    return None


def save_gemini_media(item, output_path):
    if item.get("data"):
        data = base64.b64decode(item["data"])
    else:
        uri = item["uri"]
        if "/files/" in uri and ":download" not in uri:
            uri = f"{uri}:download"
        r = requests.get(uri, params={"alt": "media"}, headers={"x-goog-api-key": get_key("gemini", "api_key")},
                         timeout=600)
        r.raise_for_status()
        data = r.content
    with open(output_path, "wb") as f:
        f.write(data)
    return output_path


def nano_banana_generate(prompt, output_path, aspect="9:16"):
    model = get_key("gemini", "image_model", "gemini-nano-banana-2.1")
    body = gemini_interaction(model, prompt, {"type": "image", "mime_type": "image/png", "aspect_ratio": aspect,
                                              "image_size": "2K"}, timeout=300)
    item = find_media(body, "image")
    if not item:
        raise RuntimeError(f"Nano Banana returned no image (content filter?): {json.dumps(body)[:300]}")
    return save_gemini_media(item, output_path)


def omni_duration(seconds):
    """Omni takes whole seconds from 3 to 15; the montage trims or holds the rest."""
    return f"{max(3, min(15, math.ceil(seconds)))}s"


def omni_generate_video(prompt, seconds, aspect, output_path, image_path=None):
    """Gemini Omni Flash (what the Gemini API offers since the Veo 3.1 previews were shut down): video with
    native sound, optionally starting from an image. The length goes in `duration` (3-15 s); 1:1 is made in 9:16
    and cropped by the montage."""
    model = get_key("gemini", "video_model", "gemini-omni-1.2-flash")
    text = f"{prompt}. No subtitles or text on screen."
    content = text
    if image_path:
        with open(image_path, "rb") as f:
            image = base64.b64encode(f.read()).decode()
        mime = "image/jpeg" if image_path.lower().endswith((".jpg", ".jpeg")) else "image/png"
        content = [{"type": "image", "data": image, "mime_type": mime}, {"type": "text", "text": text}]
    body = gemini_interaction(model, content, {"type": "video", "aspect_ratio": "16:9" if aspect == "16:9" else "9:16",
                                               "resolution": get_key("gemini", "video_resolution", "720p"),
                                               "duration": omni_duration(seconds)})
    item = find_media(body, "video")
    if not item:
        raise RuntimeError(f"Gemini Omni returned no video (content filter?): {json.dumps(body)[:300]}")
    return save_gemini_media(item, output_path)


def generate_image(provider, prompt, output_path, aspect="9:16"):
    if provider == "nano_banana":
        return nano_banana_generate(prompt, output_path, aspect)
    if provider == "flux":
        width, height = FLUX_SIZES.get(aspect, FLUX_SIZES["9:16"])
        return forge_txt2img(prompt, output_path, width, height)
    if provider == "image_api":
        return image_api_generate(prompt, output_path, IMAGE_API_SIZES.get(aspect))
    raise RuntimeError(f"Unknown image provider: {provider}")


# ---------------------------------------------------------------------------
# 3. Local AI video through ComfyUI. Frames are saved with the core SaveImage node and encoded
#    with ffmpeg here, so the result never depends on ComfyUI's video-saving nodes.
# ---------------------------------------------------------------------------
def comfy_free():
    try:
        requests.post(f"{COMFY_URL}/free", json={"unload_models": True, "free_memory": True}, timeout=60)
    except Exception:
        pass


def build_wan22_workflow(image_name, motion_prompt, width, height, frames, steps, seed, prefix):
    """Wan 2.2 TI2V-5B: image-to-video when image_name is given, text-to-video otherwise
    (start_image is optional in comfy_extras/nodes_wan.py)."""
    latent_inputs = {"vae": ["3", 0], "width": width, "height": height, "length": frames, "batch_size": 1}
    graph = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "wan2.2_ti2v_5B_fp16.safetensors", "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": "umt5_xxl_fp8_e4m3fn_scaled.safetensors", "type": "wan"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": "wan2.2_vae.safetensors"}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"text": motion_prompt, "clip": ["2", 0]}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"text": NEGATIVE_VIDEO_PROMPT, "clip": ["2", 0]}},
        "7": {"class_type": "Wan22ImageToVideoLatent", "inputs": latent_inputs},
        "8": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["1", 0], "shift": 8.0}},
        "9": {"class_type": "KSampler", "inputs": {"model": ["8", 0], "seed": seed, "steps": steps, "cfg": 5.0,
                                                   "sampler_name": "uni_pc", "scheduler": "simple",
                                                   "positive": ["4", 0], "negative": ["5", 0],
                                                   "latent_image": ["7", 0], "denoise": 1.0}},
        "10": {"class_type": "VAEDecode", "inputs": {"samples": ["9", 0], "vae": ["3", 0]}},
        "11": {"class_type": "SaveImage", "inputs": {"images": ["10", 0], "filename_prefix": prefix}},
    }
    if image_name:
        graph["6"] = {"class_type": "LoadImage", "inputs": {"image": image_name}}
        latent_inputs["start_image"] = ["6", 0]
    return graph


def build_ltxv_workflow(image_name, motion_prompt, width, height, frames, steps, seed, prefix, fps):
    """LTX-Video 2B v0.9.5: LTXVImgToVideo with a start image, EmptyLTXVLatentVideo (text-to-video) without
    (node inputs checked against comfy_extras/nodes_lt.py)."""
    graph = {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "ltx-video-2b-v0.9.5.safetensors"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": "t5xxl_fp8_e4m3fn.safetensors", "type": "ltxv"}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"text": motion_prompt, "clip": ["2", 0]}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"text": NEGATIVE_VIDEO_PROMPT, "clip": ["2", 0]}},
        "9": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
        "11": {"class_type": "VAEDecode", "inputs": {"samples": ["10", 0], "vae": ["1", 2]}},
        "12": {"class_type": "SaveImage", "inputs": {"images": ["11", 0], "filename_prefix": prefix}},
    }
    if image_name:
        graph["5"] = {"class_type": "LoadImage", "inputs": {"image": image_name}}
        graph["6"] = {"class_type": "LTXVImgToVideo", "inputs": {"positive": ["3", 0], "negative": ["4", 0], "vae": ["1", 2],
                                                                 "image": ["5", 0], "width": width, "height": height,
                                                                 "length": frames, "batch_size": 1, "strength": 1.0}}
        positive, negative, latent = ["6", 0], ["6", 1], ["6", 2]
    else:
        graph["6"] = {"class_type": "EmptyLTXVLatentVideo", "inputs": {"width": width, "height": height,
                                                                       "length": frames, "batch_size": 1}}
        positive, negative, latent = ["3", 0], ["4", 0], ["6", 0]
    graph["7"] = {"class_type": "LTXVConditioning", "inputs": {"positive": positive, "negative": negative,
                                                               "frame_rate": float(fps)}}
    graph["8"] = {"class_type": "LTXVScheduler", "inputs": {"steps": steps, "max_shift": 2.05, "base_shift": 0.95,
                                                            "stretch": True, "terminal": 0.1, "latent": latent}}
    graph["10"] = {"class_type": "SamplerCustom", "inputs": {"model": ["1", 0], "add_noise": True, "noise_seed": seed,
                                                             "cfg": 3.0, "positive": ["7", 0], "negative": ["7", 1],
                                                             "sampler": ["9", 0], "sigmas": ["8", 0], "latent_image": latent}}
    return graph


def comfy_generate_video(model, image_path, motion_prompt, output_path, width=480, height=832, frames=49,
                         steps=20, fps=24, timeout=3600, on_raw=None):
    """Wan 2.2 or LTX-Video; image_path=None means text-to-video. Frames are encoded to MP4 with ffmpeg."""
    width, height = max(32, int(width) // 32 * 32), max(32, int(height) // 32 * 32)
    step = 8 if model == "ltxv" else 4                      # LTX wants 8n+1 frames, Wan 4n+1
    frames = max(step + 1, (int(frames) - 1) // step * step + 1)
    image_name = None
    if image_path:
        with open(image_path, "rb") as f:
            up = requests.post(f"{COMFY_URL}/upload/image", data={"overwrite": "true"}, timeout=120,
                               files={"image": (os.path.basename(image_path), f, "image/png")})
        up.raise_for_status()
        image_name = up.json()["name"]

    seed = int.from_bytes(os.urandom(4), "big")
    prefix = f"studio/{uuid.uuid4().hex[:10]}"
    if model == "ltxv":
        workflow = build_ltxv_workflow(image_name, motion_prompt, width, height, frames, int(steps), seed, prefix, fps)
    else:
        workflow = build_wan22_workflow(image_name, motion_prompt, width, height, frames, int(steps), seed, prefix)
    r = requests.post(f"{COMFY_URL}/prompt", json={"prompt": workflow, "client_id": str(uuid.uuid4())}, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"ComfyUI rejected the workflow: {r.text[:1500]}")
    prompt_id = r.json()["prompt_id"]
    raw = on_raw or (lambda m: None)
    raw(f"ComfyUI | queued prompt {prompt_id}: {model}, {width}x{height}, {frames} frames, {steps} steps, "
        f"{'image-to-video' if image_name else 'text-to-video'}")

    started, last_note = time.time(), 0
    while time.time() - started < timeout:
        time.sleep(5)
        history = requests.get(f"{COMFY_URL}/history/{prompt_id}", timeout=30).json()
        if prompt_id not in history:
            if time.time() - last_note >= 30:
                last_note = time.time()
                raw(f"ComfyUI | still rendering ({int(time.time() - started)} s)")
            continue
        entry = history[prompt_id]
        status = entry.get("status", {})
        if status.get("status_str") == "error":
            raise RuntimeError("ComfyUI error: " + json.dumps(status.get("messages", []))[-1500:])
        images = [img for out in entry.get("outputs", {}).values() for img in out.get("images", [])]
        if not images:
            raise RuntimeError("ComfyUI finished but returned no frames.")
        frames_dir = output_path + "_frames"
        os.makedirs(frames_dir, exist_ok=True)
        for i, img in enumerate(sorted(images, key=lambda x: x["filename"])):
            params = {"filename": img["filename"], "subfolder": img.get("subfolder", ""), "type": img.get("type", "output")}
            data = requests.get(f"{COMFY_URL}/view", params=params, timeout=120).content
            with open(f"{frames_dir}/frame_{i:05d}.png", "wb") as f:
                f.write(data)
        ffmpeg(["-framerate", str(fps), "-i", f"{frames_dir}/frame_%05d.png",
                "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2"] + video_encoder_args() + [output_path])
        shutil.rmtree(frames_dir, ignore_errors=True)
        raw(f"ComfyUI | done in {int(time.time() - started)} s, {len(images)} frames -> {os.path.basename(output_path)}")
        return output_path
    raise RuntimeError("ComfyUI timed out.")


# ---------------------------------------------------------------------------
# 4. Stock clips downloaded directly (Pexels, Pixabay, Coverr) in the reel's orientation
# ---------------------------------------------------------------------------
ORIENTATIONS = {"9:16": "portrait", "16:9": "landscape", "1:1": "square"}


def _best_rendition(files, orientation="portrait"):
    """files: (url, width, height). Prefer the right orientation and the smallest rendition >= 1080 px on
    its short side; otherwise the largest one."""
    files = [f for f in files if f[0]]
    if orientation == "portrait":
        shaped = [f for f in files if f[2] >= f[1]]
    elif orientation == "landscape":
        shaped = [f for f in files if f[1] >= f[2]]
    else:
        shaped = files
    shaped = shaped or files
    if not shaped:
        return None
    short_side = lambda f: min(f[1], f[2])
    hd = [f for f in shaped if short_side(f) >= 1080]
    return (min(hd, key=short_side) if hd else max(shaped, key=short_side))[0]


def pexels_search_videos(query, orientation="portrait", per_page=5):
    r = requests.get("https://api.pexels.com/videos/search", headers={"Authorization": get_key("pexels", "api_key")},
                     params={"query": query, "orientation": orientation, "per_page": per_page}, timeout=60)
    r.raise_for_status()
    urls = []
    for video in r.json().get("videos", []):
        url = _best_rendition([(f.get("link"), f.get("width") or 0, f.get("height") or 0)
                               for f in video.get("video_files", [])], orientation)
        if url:
            urls.append(url)
    return urls


def pixabay_search_videos(query, orientation="portrait", per_page=20):
    r = requests.get("https://pixabay.com/api/videos/", timeout=60,
                     params={"key": get_key("pixabay", "api_key"), "q": query, "per_page": per_page, "video_type": "all"})
    r.raise_for_status()
    urls = []
    for hit in r.json().get("hits", []):
        sizes = (hit.get("videos") or {}).values()
        url = _best_rendition([(v.get("url"), v.get("width") or 0, v.get("height") or 0) for v in sizes], orientation)
        if url:
            urls.append(url)
    return urls


def coverr_search_videos(query, orientation="portrait", per_page=20):
    params = {"query": query, "page_size": per_page, "urls": "true"}
    if orientation == "portrait":
        params["filter"] = "is_vertical:true"
    r = requests.get("https://api.coverr.co/videos", headers=bearer(get_key("coverr", "api_key")),
                     params=params, timeout=60)
    r.raise_for_status()
    return [h["urls"]["mp4_download"] for h in r.json().get("hits", []) if (h.get("urls") or {}).get("mp4_download")]


STOCK_SEARCH = {"pexels": pexels_search_videos, "pixabay": pixabay_search_videos, "coverr": coverr_search_videos}


def download_file(url, dest, headers=None):
    with requests.get(url, stream=True, timeout=300, headers=headers or {}) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(1024 * 1024):
                f.write(chunk)
    return dest


# ---------------------------------------------------------------------------
# 5. External AI video (paid): create a task, poll it, download the clip. The POST that creates a paid task
#    is NEVER retried automatically (a lost answer may still have been charged); polling is.
# ---------------------------------------------------------------------------
def api_error(r):
    try:
        body = r.json()
    except ValueError:
        return f"HTTP {r.status_code}: {r.text[:300]}"
    detail = body
    if isinstance(body, dict):
        err = body.get("error")
        detail = (err.get("message") or err.get("code")) if isinstance(err, dict) else (
            err or body.get("message") or body.get("detail") or body)
    return f"HTTP {r.status_code}: {str(detail)[:300]}"


def _create_task(label, url, headers, payload):
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=(30, 120))
    except requests.RequestException as exc:
        raise RuntimeError(f"{label}: no answer when creating the task ({exc}). It may have been charged: "
                           "check your dashboard before trying again.")
    if not 200 <= r.status_code < 300:
        raise RuntimeError(f"{label} rejected the task: {api_error(r)}")
    return r.json()


def _get_json(url, headers):
    r = requests.get(url, headers=headers, timeout=60)
    r.raise_for_status()
    return r.json()


def _first_url(*candidates):
    for c in candidates:
        for url in ([c] if isinstance(c, str) else (c or [])):
            if isinstance(url, str) and url.startswith(("http://", "https://")):
                return url
    return None


FAILED = {"failed", "error", "cancelled", "canceled", "expired", "timeout"}


def _wavespeed_task(prompt, seconds, aspect):
    h = bearer(get_key("wavespeed", "api_key"))
    base = "https://api.wavespeed.ai/api/v3"
    body = _create_task("WaveSpeed", f"{base}/{get_key('wavespeed', 'model')}", h,
                        {"prompt": prompt, "aspect_ratio": aspect, "duration": seconds})
    if body.get("code") != 200 or not (body.get("data") or {}).get("id"):
        raise RuntimeError(f"WaveSpeed did not create the task: {str(body)[:300]}")
    task = body["data"]["id"]

    def poll():
        data = _get_json(f"{base}/predictions/{task}/result", h).get("data") or {}
        status = str(data.get("status") or "").lower()
        if status == "completed":
            return "done", _first_url(data.get("outputs"))
        return ("failed", data.get("error") or status) if status in FAILED else ("running", status)
    return task, poll, None


def _seedance_task(prompt, seconds, aspect):
    h = bearer(get_key("volcengine_seedance", "api_key"))
    tasks = "https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks"
    body = _create_task("Seedance", tasks, h, {
        "model": get_key("volcengine_seedance", "model"), "content": [{"type": "text", "text": prompt}],
        "ratio": aspect, "duration": seconds, "resolution": get_key("volcengine_seedance", "resolution", "720p"),
        "watermark": False})
    task = body.get("id")

    def poll():
        data = _get_json(f"{tasks}/{task}", h)
        status = str(data.get("status") or "").lower()
        if status == "succeeded":
            return "done", _first_url((data.get("content") or {}).get("video_url"))
        return ("failed", data.get("error") or status) if status in FAILED else ("running", status)
    return task, poll, None


def _ofox_task(prompt, seconds, aspect):
    h = bearer(get_key("ofox", "api_key"))
    model = get_key("ofox", "model")
    payload = {"model": model, "prompt": prompt, "duration": seconds,
               "resolution": get_key("ofox", "resolution", "720p"), "aspect_ratio": aspect}
    if model.startswith("bytedance/"):
        payload["provider"] = {"type": "byteplus"}
    body = _create_task("OFox", "https://api.ofox.ai/v1/videos", h, payload)
    task = body.get("id")

    def poll():
        data = _get_json(f"https://api.ofox.ai/v1/videos/{task}", h)
        status = str(data.get("status") or "").lower()
        if status in ("completed", "succeeded"):
            return "done", _first_url(data.get("mirror_urls"), data.get("unsigned_urls"), data.get("url"))
        return ("failed", data.get("error") or status) if status in FAILED else ("running", status)
    return task, poll, None


def _muapi_task(prompt, seconds, aspect):
    h = {"x-api-key": get_key("muapi", "api_key")}
    base = "https://api.muapi.ai/api/v1"
    body = _create_task("MuAPI", f"{base}/{get_key('muapi', 'model')}", h, {
        "prompt": prompt, "aspect_ratio": aspect, "resolution": get_key("muapi", "resolution", "480p"),
        "duration": seconds})
    task = body.get("request_id") or body.get("id")

    def poll():
        data = _get_json(f"{base}/predictions/{task}/result", h)
        status = str(data.get("status") or "").lower()
        if status == "completed":
            return "done", _first_url(data.get("outputs"))
        return ("failed", data.get("error") or status) if status in FAILED else ("running", status)
    return task, poll, None


def _metaso_task(prompt, seconds, aspect):
    h = bearer(get_key("metaso_minimax", "api_key"))
    base = "https://metaso.cn/api/minimax"
    body = _create_task("Metaso", f"{base}/v2/video_generation", h, {
        "model": "MiniMax-H3", "content": [{"type": "text", "text": prompt}],
        "resolution": get_key("metaso_minimax", "resolution", "768P"), "duration": seconds, "ratio": aspect})
    task = body.get("task_id")

    def poll():
        data = _get_json(f"{base}/v2/query/video_generation/{task}", h).get("task") or {}
        status = str(data.get("status") or "").lower()
        if status == "succeeded":
            return "done", _first_url((data.get("content") or {}).get("url"))
        return ("failed", data.get("error") or status) if status in FAILED else ("running", status)
    return task, poll, None


INFISTAR_MODELS = {"doubao-seedance-2-0-fast-260128": 5, "MiniMax-H3": 6, "kling-v2-6": 5}   # fixed clip seconds


def _infistar_task(prompt, seconds, aspect):
    h = bearer(get_key("infistar", "api_key"))
    base = "https://infistar.cc/v1"
    model = get_key("infistar", "model")
    size = "768p" if model == "MiniMax-H3" else ("1280x720" if aspect == "16:9" else "720x1280")
    body = _create_task("Infistar", f"{base}/videos", h, {
        "model": model, "prompt": prompt, "seconds": str(INFISTAR_MODELS.get(model, 5)), "size": size})
    task = body.get("id")

    def poll():
        data = _get_json(f"{base}/videos/{task}", h)
        status = str(data.get("status") or "").lower()
        if status in ("completed", "succeeded"):
            return "done", f"{base}/videos/{task}/content"
        return ("failed", data.get("error") or status) if status in FAILED else ("running", status)
    return task, poll, h                      # the content endpoint needs the key too


AI_VIDEO_CLIENTS = {   # provider -> (create task, min seconds, max seconds)
    "wavespeed": (_wavespeed_task, 4, 15), "volcengine_seedance": (_seedance_task, 2, 12),
    "ofox": (_ofox_task, 4, 15), "muapi": (_muapi_task, 3, 12), "metaso_minimax": (_metaso_task, 4, 15),
    "infistar": (_infistar_task, 5, 6),
}


def generate_ai_video(provider, prompt, seconds, aspect, output_path, on_raw=None, timeout=1800, image_path=None):
    """One paid clip. seconds is clamped to what the provider accepts. image_path (providers that accept a
    first frame): image-to-video."""
    label = CLIP_PROVIDERS[provider]["label"]
    raw = on_raw or (lambda m: None)
    if provider == "gemini_omni":
        raw(f"{label} | generating {'image' if image_path else 'text'}-to-video, ~{seconds:.0f} s, {aspect}")
        started = time.time()
        omni_generate_video(prompt, seconds, aspect, output_path, image_path)
        raw(f"{label} | done in {int(time.time() - started)} s")
        return output_path
    create, low, high = AI_VIDEO_CLIENTS[provider]
    seconds = int(min(max(round(seconds), low), high))
    task, poll, download_headers = create(prompt, seconds, aspect)
    if not task:
        raise RuntimeError(f"{label} returned no task ID.")
    raw(f"{label} | task {task} created ({seconds} s, {aspect})")
    started, errors, last_note = time.time(), 0, 0
    while time.time() - started < timeout:
        time.sleep(5)
        try:
            state, info = poll()
            errors = 0
        except (requests.RequestException, ValueError) as exc:
            errors += 1
            raw(f"{label} | status check failed ({exc}); retry {errors}/5")
            if errors >= 5:
                raise RuntimeError(f"{label}: could not check task {task}. Check your dashboard: it may have been charged.")
            continue
        if state == "done":
            if not info:
                raise RuntimeError(f"{label} finished task {task} without a downloadable video.")
            download_file(info, output_path, download_headers)
            raw(f"{label} | task {task} done in {int(time.time() - started)} s")
            return output_path
        if state == "failed":
            raise RuntimeError(f"{label}: task {task} failed: {info}")
        if time.time() - last_note >= 30:
            last_note = time.time()
            raw(f"{label} | task {task}: {info or 'running'} ({int(time.time() - started)} s)")
    raise RuntimeError(f"{label}: task {task} did not finish in {timeout // 60} min; check it in your dashboard.")


# ---------------------------------------------------------------------------
# 6. Music and sound: your songs (Drive/music), a new instrumental from ElevenLabs Music (saved for reuse) and
#    ElevenLabs sound effects for the scenes
# ---------------------------------------------------------------------------
def elevenlabs_music(prompt, seconds, output_path):
    r = requests.post("https://api.elevenlabs.io/v1/music", timeout=600,
                      params={"output_format": "mp3_44100_128"},
                      headers={"xi-api-key": get_key("elevenlabs", "api_key")},
                      json={"prompt": (prompt or "uplifting modern instrumental background music")[:4000],
                            "music_length_ms": int(min(max(seconds * 1000, 3000), 600000)),
                            "model_id": get_key("elevenlabs", "music_model", "music_v1"),
                            "force_instrumental": True})
    if not r.ok:
        raise RuntimeError(f"ElevenLabs Music: {api_error(r)}")
    with open(output_path, "wb") as f:
        f.write(r.content)
    return output_path


def elevenlabs_sound(prompt, seconds, output_path):
    """A sound effect of exactly `seconds` (0.5-30 s) for one scene, from ElevenLabs' sound generation."""
    r = requests.post("https://api.elevenlabs.io/v1/sound-generation", timeout=300,
                      params={"output_format": "mp3_44100_128"},
                      headers={"xi-api-key": get_key("elevenlabs", "api_key")},
                      json={"text": prompt[:450], "duration_seconds": round(min(max(seconds, 0.5), 30.0), 2),
                            "prompt_influence": 0.4,
                            "model_id": get_key("elevenlabs", "sfx_model", "eleven_text_to_sound_v2")})
    if not r.ok:
        raise RuntimeError(f"ElevenLabs sound effects: {api_error(r)}")
    with open(output_path, "wb") as f:
        f.write(r.content)
    return output_path

# ---------------------------------------------------------------------------
# 7. Publish kit: per-platform titles, captions, hashtags, hooks (same LLM as the storyboard)
#    Limits verified Oct 2026: Instagram caps hashtags at 5 per reel (Dec 2025); TikTok captions allow
#    4,000 chars but show ~80 in the feed; YouTube Shorts title 100 / description 5,000, first 3 hashtags
#    shown above the title, >60 hashtags = all ignored, tags field 500 chars; Facebook Reels 2,200 chars,
#    ~125 visible before "more".
# ---------------------------------------------------------------------------
PLATFORM_RULES = {
    "youtube": dict(title_max=100, description_max=5000, hashtags=3, tags_max_chars=500),
    "tiktok": dict(caption_max=4000, visible=80, hashtags=5),
    "instagram": dict(caption_max=2200, visible=125, hashtags=5),      # hard platform limit since Dec 2025
    "facebook": dict(caption_max=2200, visible=125, hashtags=5),
}


def build_kit_prompt(lang="English"):
    return f"""You are a senior short-form video strategist for {lang}-speaking audiences.
From the reel's idea and final narration, write everything needed to publish it. Answer ONLY with valid JSON:
{{
  "virality": {{"hook": 0-100, "retention": 0-100, "shareability": 0-100, "commentability": 0-100,
               "niche_fit": 0-100, "emotional": 0-100, "overall": 0-100,
               "verdict": "one direct line", "strongest": "...", "weakest": "...", "recommendation": "one concrete action"}},
  "on_screen_hooks": ["3 alternative text overlays for the first 2 seconds, max 6 words each"],
  "youtube": {{"title": "...", "title_variants": ["curiosity angle", "data or conflict angle"],
              "description": "...", "hashtags": ["#niche1", "#niche2"], "tags": ["search phrase", "..."]}},
  "tiktok": {{"caption": "...", "hashtags": ["#..."]}},
  "instagram": {{"caption": "...", "hashtags": ["#..."]}},
  "facebook": {{"caption": "...", "hashtags": ["#..."]}},
  "seed_comments": ["3 comments the creator can post first to start the conversation"],
  "teaser": "max 10 words for a story announcing the reel"
}}
Rules:
- All text in {lang}, in the same tone as the narration. Max 1-2 emojis per caption.
- Titles must deliver what the video promises (no empty clickbait). Use concrete numbers from the narration.
- YouTube Shorts: title ideally under 60 characters (hard max 100) with the main keyword in the first 30;
  description: first line is a hook under 100 characters (shown above the fold), then 2-3 short lines of
  context and a call to action; NO hashtags inside the description; "hashtags": exactly 2 specific niche
  hashtags (#Shorts is added automatically); "tags": 8-15 search phrases people would type.
- TikTok: the first 80 characters of the caption must hook on their own (that is all the feed shows);
  whole caption under 300 characters; end with a question or a reason to comment; 3-5 hashtags.
- Instagram Reels: the first 125 characters carry the hook and the value; total under 600 characters;
  end with a call to save, share or send to someone; AT MOST 5 hashtags (Instagram's hard limit).
- Facebook Reels: the first 125 characters are visible; conversational, end with a question; under 400
  characters; 3-5 hashtags.
- Hashtags: no spaces, order from specific niche to broader community; avoid saturated empty tags like
  #viral #fyp #parati #foryou. Never put hashtags inside captions; they go in the "hashtags" arrays.
- Seed comments must sound genuine (follow-up question, extra fact, personal experience), never promotional."""


def clean_hashtags(tags, limit):
    out, seen = [], set()
    for tag in tags or []:
        tag = re.sub(r"[^\w]", "", str(tag).replace("#", ""), flags=re.UNICODE)
        if tag and tag.lower() not in seen:
            seen.add(tag.lower())
            out.append(f"#{tag}")
    return out[:limit]


def fit_text(text, limit):
    """Trim to `limit` characters at a word boundary."""
    text = re.sub(r"\n{3,}", "\n\n", str(text or "").strip())
    if len(text) <= limit:
        return text
    cut = text[:limit - 1].rsplit(" ", 1)[0].rstrip(",.;: ")
    return cut + "…"


def strip_inline_hashtags(text):
    return re.sub(r"(?:^|\s)#\w+", "", str(text or ""), flags=re.UNICODE).strip()


def normalize_kit(raw):
    """Enforce every platform limit in code, whatever the model returned."""
    kit = {"virality": raw.get("virality") or {}, "on_screen_hooks": [fit_text(h, 60) for h in raw.get("on_screen_hooks", [])][:3],
           "seed_comments": [fit_text(c, 300) for c in raw.get("seed_comments", [])][:3],
           "teaser": fit_text(raw.get("teaser", ""), 80)}

    yt, rules = raw.get("youtube") or {}, PLATFORM_RULES["youtube"]
    yt_tags = [t for t in clean_hashtags(yt.get("hashtags"), 60) if t.lower() != "#shorts"][:2] + ["#Shorts"]
    tags = []
    for tag in yt.get("tags") or []:
        tag = str(tag).replace(",", " ").strip()
        if tag and tag not in tags and len(", ".join(tags + [tag])) <= rules["tags_max_chars"]:
            tags.append(tag)
    description = strip_inline_hashtags(yt.get("description"))
    hashtag_line = " ".join(yt_tags)
    kit["youtube"] = {
        "title": fit_text(strip_inline_hashtags(yt.get("title")), rules["title_max"]),
        "title_variants": [fit_text(t, rules["title_max"]) for t in (yt.get("title_variants") or [])][:2],
        "description": fit_text(description, rules["description_max"] - len(hashtag_line) - 2) + "\n\n" + hashtag_line,
        "tags": ", ".join(tags),
    }
    for platform in ("tiktok", "instagram", "facebook"):
        data, rules = raw.get(platform) or {}, PLATFORM_RULES[platform]
        hashtags = " ".join(clean_hashtags(data.get("hashtags"), rules["hashtags"]))
        caption = fit_text(strip_inline_hashtags(data.get("caption")), rules["caption_max"] - len(hashtags) - 2)
        kit[platform] = {"caption": (caption + ("\n\n" + hashtags if hashtags else "")).strip(),
                         "visible": rules["visible"]}
    return kit


def generate_publish_kit(provider_key, model, idea, plan):
    user = (f"Reel idea: {idea}\nTitle: {plan.get('title', '')}\nOn-screen text: {plan.get('on_screen_text', '')}"
            f"\n\nFinal narration:\n{plan_script(plan)}\n\nKeywords: {', '.join(plan.get('search_terms', []))}")
    lang = plan.get("language_name") or LANGUAGE_NAMES.get(plan.get("language", "en"), "English")
    return normalize_kit(extract_json(llm_complete(provider_key, model, build_kit_prompt(lang), user)))


def kit_markdown(kit):
    """A readable copy of the kit saved next to the reel in Drive (publish_kit.md)."""
    v = kit.get("virality", {})
    yt = kit["youtube"]
    lines = ["# Publish kit", "",
             f"**Viral potential: {v.get('overall', '?')}/100** · {v.get('verdict', '')}",
             f"- Hook {v.get('hook', '?')} · Retention {v.get('retention', '?')} · Shareability {v.get('shareability', '?')}"
             f" · Commentability {v.get('commentability', '?')} · Niche fit {v.get('niche_fit', '?')} · Emotion {v.get('emotional', '?')}",
             f"- Strongest point: {v.get('strongest', '')}", f"- Weakest point: {v.get('weakest', '')}",
             f"- Recommendation: {v.get('recommendation', '')}", "",
             "## On-screen text (first 2 s)", *[f"- {h}" for h in kit["on_screen_hooks"]], "",
             "## YouTube Shorts", f"**Title:** {yt['title']}", *[f"- Variant: {t}" for t in yt["title_variants"]],
             "", "**Description:**", yt["description"], "", f"**Tags:** {yt['tags']}", ""]
    for platform, label in (("tiktok", "TikTok"), ("instagram", "Instagram Reels"), ("facebook", "Facebook Reels")):
        lines += [f"## {label}", kit[platform]["caption"], ""]
    lines += ["## Seed comments", *[f"{i}. {c}" for i, c in enumerate(kit["seed_comments"], 1)], "",
              "## Story teaser", kit["teaser"]]
    return "\n".join(lines)


def save_publish_kit(kit, reel_dir):
    with open(f"{reel_dir}/publish_kit.json", "w", encoding="utf-8") as f:
        json.dump(kit, f, indent=2, ensure_ascii=False)
    with open(f"{reel_dir}/publish_kit.md", "w", encoding="utf-8") as f:
        f.write(kit_markdown(kit))


def load_publish_kit(reel_dir):
    try:
        with open(f"{reel_dir}/publish_kit.json", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 8. Publishing through Upload-Post: one upload, each platform with its own text from the kit
# ---------------------------------------------------------------------------
UPLOAD_POST_API = "https://api.upload-post.com/api"
PUBLISH_PLATFORMS = {"tiktok": "🎵 TikTok", "instagram": "📸 Instagram Reels", "youtube": "▶️ YouTube Shorts",
                     "facebook": "📘 Facebook Reels"}
YOUTUBE_PRIVACY = {"public": "Public", "unlisted": "Unlisted", "private": "Private"}
TIKTOK_PRIVACY = {"PUBLIC_TO_EVERYONE": "Public", "MUTUAL_FOLLOW_FRIENDS": "Friends", "FOLLOWER_OF_CREATOR": "Followers",
                  "SELF_ONLY": "Only me"}


def parse_schedule(text):
    """'2026-11-02 18:30' -> '2026-11-02T18:30:00' (empty = publish now)."""
    text = (text or "").strip()
    if not text:
        return ""
    for pattern in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text, pattern).strftime("%Y-%m-%dT%H:%M:%S")
        except ValueError:
            continue
    raise RuntimeError("Invalid schedule date: use the format 2026-11-02 18:30.")


def upload_post_form(kit, platforms, schedule="", timezone="", youtube_privacy="public",
                     tiktok_privacy="PUBLIC_TO_EVERYONE", first_comment=True):
    """The multipart fields for Upload-Post's /upload (video file not included)."""
    yt = kit["youtube"]
    title = yt.get("title") or fit_text(kit["tiktok"]["caption"], 100)
    data = [("user", get_key("upload_post", "username")), ("title", title), ("async_upload", "true")]
    data += [("platform[]", p) for p in platforms]
    seed = (kit.get("seed_comments") or [""])[0] if first_comment else ""
    if "youtube" in platforms:
        data += [("youtube_title", title), ("youtube_description", yt.get("description", "")),
                 ("privacyStatus", youtube_privacy), ("selfDeclaredMadeForKids", "false"),
                 ("containsSyntheticMedia", "true")]
        data += [("tags[]", t.strip()) for t in (yt.get("tags") or "").split(",") if t.strip()]
        if seed:
            data.append(("youtube_first_comment", seed))
    if "tiktok" in platforms:
        data += [("tiktok_title", kit["tiktok"]["caption"]), ("privacy_level", tiktok_privacy)]
    if "instagram" in platforms:
        data.append(("instagram_title", kit["instagram"]["caption"]))
        if seed:
            data.append(("instagram_first_comment", seed))
    if "facebook" in platforms:
        data.append(("facebook_description", kit["facebook"]["caption"]))
        if get_key("upload_post", "facebook_page_id"):
            data.append(("facebook_page_id", get_key("upload_post", "facebook_page_id")))
        if seed:
            data.append(("facebook_first_comment", seed))
    when = parse_schedule(schedule)
    if when:
        data += [("scheduled_date", when), ("timezone", timezone or "UTC")]
    return data


def publish_reel(video_path, kit, platforms, **options):
    """Sends the video once; Upload-Post publishes (or schedules) it on every chosen platform."""
    if not platforms:
        raise RuntimeError("Pick at least one platform.")
    missing = credential_missing("upload_post")
    if missing:
        raise RuntimeError("Upload-Post is missing " + ", ".join(FIELD_LABELS.get(f, f) for f in missing) +
                           ": add it in the 🔑 API keys tab.")
    if not kit:
        raise RuntimeError("This reel has no publish kit: generate it first.")
    if not video_path or not os.path.exists(video_path):
        raise RuntimeError("The final video was not found.")
    data = upload_post_form(kit, platforms, **options)
    with open(video_path, "rb") as f:
        r = requests.post(f"{UPLOAD_POST_API}/upload", data=data, timeout=900,
                          headers={"Authorization": f"Apikey {get_key('upload_post', 'api_key')}"},
                          files={"video": (os.path.basename(video_path), f, "video/mp4")})
    if not 200 <= r.status_code < 300:
        raise RuntimeError(f"Upload-Post rejected the post: {api_error(r)}")
    return r.json()


def publish_status(request_id):
    r = requests.get(f"{UPLOAD_POST_API}/uploadposts/status", params={"request_id": request_id}, timeout=60,
                     headers={"Authorization": f"Apikey {get_key('upload_post', 'api_key')}"})
    r.raise_for_status()
    return r.json()


def describe_publish(body):
    """Readable lines from an Upload-Post answer: one per platform, with its link or its error."""
    results = body.get("results")
    entries = list(results.items()) if isinstance(results, dict) else [
        (e.get("platform", "?"), e) for e in (results or []) if isinstance(e, dict)]
    lines = []
    for platform, entry in entries:
        entry = entry if isinstance(entry, dict) else {"success": bool(entry)}
        label = PUBLISH_PLATFORMS.get(platform, platform)
        if entry.get("success"):
            link = entry.get("url") or entry.get("post_url") or ""
            lines.append(f"✅ {label}: published {link}".strip())
        else:
            lines.append(f"❌ {label}: {entry.get('error') or entry.get('message') or 'failed'}")
    return lines


# ---------------------------------------------------------------------------
# 9. Production, step by step: storyboard -> images -> audio -> clips -> assembly. With "review each step"
#     on, the reel pauses after every step until you confirm it; any scene's image, voice, sound or clip can be
#     regenerated on its own (with its prompt or text edited) before confirming.
# ---------------------------------------------------------------------------
def slugify(text):
    """'Por qué los perros...' -> 'por-que-los-perros' (accents stripped, ASCII only)."""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")[:40] or "reel"


class ReelRun:
    """Live state of one job; snapshot() is what the UI renders."""
    LOG_FILE = f"{WORK_DIR}/current_run.log"     # local disk: `!tail -f` it from any cell

    def __init__(self):
        self.t0 = self.last_line = time.time()
        self.lines, self.media, self.tech_lines, self.videos = [], [], [], []
        self.stage, self.pct = "Preparing", 0
        self.script, self.video, self.failed, self.done = "", None, False, False
        self.kit, self.result, self.reel_dir, self.review = None, None, None, None
        try:
            os.makedirs(os.path.dirname(self.LOG_FILE), exist_ok=True)
            open(self.LOG_FILE, "w").close()
        except Exception:
            pass

    def tech(self, msg):
        """Technical log: raw tool output, HTTP error bodies, tracebacks."""
        stamp = time.strftime("%H:%M:%S")
        for line in str(msg).splitlines() or [""]:
            self.tech_lines.append(f"{stamp} | {line}")
        del self.tech_lines[:-800]               # keep the UI light on long runs

    def log(self, msg):
        self.last_line = time.time()
        elapsed = int(self.last_line - self.t0)
        line = f"[{elapsed // 60:02d}:{elapsed % 60:02d}] {msg}"
        self.lines.append(line)
        self.tech(f"STEP | {msg}")
        print(line)
        try:
            with open(self.LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass

    def set(self, stage=None, pct=None):
        if stage:
            self.stage = stage
        if pct is not None:
            self.pct = max(self.pct, min(100, int(pct)))

    def fail(self, exc):
        self.failed = True
        self.set(f"Error in: {self.stage}")
        self.log(f"❌ {exc}")
        response = getattr(exc, "response", None)
        if response is not None:
            self.tech(f"HTTP {getattr(response, 'status_code', '?')} {getattr(response, 'url', '')}\n"
                      f"{getattr(response, 'text', '')[:2000]}")
        self.tech(traceback.format_exc())      # the full traceback goes to the technical log only
        if not isinstance(exc, RuntimeError):     # our own errors already explain the cause
            self.log(f"{type(exc).__name__} (full detail in the technical log)")

    def snapshot(self):
        return {"log": "\n".join(self.lines), "stage": self.stage, "pct": self.pct, "script": self.script,
                "media": list(self.media), "video": self.video, "videos": list(self.videos), "failed": self.failed,
                "done": self.done, "kit": self.kit, "tech": "\n".join(self.tech_lines), "reel_dir": self.reel_dir,
                "review": self.review, "elapsed": int(time.time() - self.t0),
                "since": int(time.time() - self.last_line)}   # seconds on the current line: shown while waiting


def _wait_for(run, job):
    """Run a long blocking job in a thread while the UI keeps receiving snapshots; result in run.result."""
    box = {}

    def worker():
        try:
            box["value"] = job()
        except Exception as exc:
            box["error"] = exc

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    while thread.is_alive():
        thread.join(2)
        yield run.snapshot()
    if "error" in box:
        raise box["error"]
    run.result = box.get("value")


# --- State of a reel folder ---------------------------------------------------------
RUN_STATES = {"running": "⏳ running", "review": "👀 waiting for your review", "done": "✅ done",
              "failed": "❌ failed", "stopped": "⏹ stopped"}
STAGES = {"storyboard": "storyboard", "images": "images", "audio": "audio", "clips": "clips"}


def write_run_state(reel_dir, status, stage="", error=""):
    try:
        with open(f"{reel_dir}/run_state.json", "w", encoding="utf-8") as f:
            json.dump({"status": status, "stage": stage, "error": str(error)[:500],
                       "updated": time.strftime("%Y-%m-%d %H:%M:%S")}, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def read_run_state(reel_dir):
    try:
        with open(f"{reel_dir}/run_state.json", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"status": "done" if glob.glob(f"{reel_dir}/*_FINAL.mp4") else "failed", "stage": ""}


def approved_stages(reel_dir):
    try:
        with open(f"{reel_dir}/review.json", encoding="utf-8") as f:
            return json.load(f).get("approved", [])
    except Exception:
        return []


def approve_stage(reel_dir, stage):
    approved = approved_stages(reel_dir)
    if stage not in approved:
        approved.append(stage)
    with open(f"{reel_dir}/review.json", "w", encoding="utf-8") as f:
        json.dump({"approved": approved}, f, indent=2)


def unique_reel_dir(slug):
    """A new production never overwrites an earlier reel with the same idea: por-que-x, por-que-x-2, ..."""
    folder, n = os.path.join(REELS_DIR, slug), 2
    while os.path.exists(folder):
        folder, n = os.path.join(REELS_DIR, f"{slug}-{n}"), n + 1
    return folder


SETTINGS_SKIPPED = ("clone_sample", "own_files")     # temporary uploads: their copies live in the reel folder


def save_settings(reel_dir, settings):
    """settings.json: everything chosen for this reel, reusable for another one (or as a preset)."""
    data = {k: v for k, v in settings.items() if k not in SETTINGS_SKIPPED and not k.startswith("_")}
    data["montage"] = montage_options(data.get("montage"))
    previous = load_settings(reel_dir)
    data["_meta"] = {"created": previous.get("_meta", {}).get("created") or time.strftime("%Y-%m-%d %H:%M:%S"),
                     "updated": time.strftime("%Y-%m-%d %H:%M:%S"), "reel": os.path.basename(reel_dir)}
    with open(f"{reel_dir}/settings.json", "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def load_settings(reel_dir):
    try:
        with open(f"{reel_dir}/settings.json", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def load_plan(reel_dir):
    try:
        with open(f"{reel_dir}/plan.json", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _exists(path):
    return bool(path) and os.path.exists(path) and os.path.getsize(path) > 0


# --- Files of every scene ---------------------------------------------------------------
def scene_image(reel_dir, i):
    return f"{reel_dir}/images/scene_{i:02}.png"


def scene_voice(reel_dir, i):
    found = sorted(glob.glob(f"{reel_dir}/audio/scene_{i:02}_voice.*"))
    return found[0] if found else None


def scene_sound(reel_dir, i):
    return f"{reel_dir}/audio/scene_{i:02}_sound.mp3"


def scene_words(reel_dir, i):
    return f"{reel_dir}/audio/scene_{i:02}_words.json"


def scene_clip(reel_dir, i):
    clips = load_assets(reel_dir).get("clips") or []
    return reel_absolute(reel_dir, clips[i - 1]) if i <= len(clips) and clips[i - 1] else None


def reel_lengths(reel_dir, plan, voice_speed=None):
    """Real seconds of every scene once all its narrations exist; None before that."""
    voice_speed = voice_speed or montage_options(load_settings(reel_dir).get("montage"))["voice_speed"]
    voices = [scene_voice(reel_dir, s["id"]) for s in plan["scenes"]]
    if any(s["narration"] and not v for s, v in zip(plan["scenes"], voices)):
        return None
    return scene_lengths(plan["scenes"], voices, voice_speed)


def storyboard_text(reel_dir, plan=None):
    plan = plan or load_plan(reel_dir)
    return render_storyboard(plan, reel_lengths(reel_dir, plan)) if plan else ""


# --- What can be reviewed and regenerated, step by step ---------------------------------
def review_items(reel_dir, stage, plan=None, settings=None):
    """The items of a step, in scene order: {id, label, kind, path, text, text_label}."""
    plan = plan or load_plan(reel_dir)
    settings = settings or load_settings(reel_dir)
    if not plan or stage == "storyboard":
        return []
    clip = CLIP_PROVIDERS.get(settings.get("clip_provider", ""), {})
    items = []
    if stage == "images":
        if settings.get("make_cover", True) and _exists(f"{reel_dir}/cover.png"):
            items.append(dict(id="cover", label="Cover", kind="image", path=f"{reel_dir}/cover.png",
                              text=plan.get("cover_prompt", ""), text_label="Image prompt (English)"))
        for s in plan["scenes"]:
            path = scene_image(reel_dir, s["id"])
            if _exists(path):
                items.append(dict(id=f"scene{s['id']}_image", label=f"Scene {s['id']} · image · {s['title']}",
                                  kind="image", path=path, text=s["image_prompt"], text_label="Image prompt (English)"))
    elif stage == "audio":
        lengths = reel_lengths(reel_dir, plan) or [estimate_seconds(s) for s in plan["scenes"]]
        for s, seconds in zip(plan["scenes"], lengths):
            voice = scene_voice(reel_dir, s["id"])
            note = f"{seconds:.1f} s" + ("" if s["narration"] else " · no narration")
            items.append(dict(id=f"scene{s['id']}_audio", label=f"Scene {s['id']} · voice · {note}", kind="audio",
                              path=voice, text=s["narration"], text_label="Narration (empty = scene without voice)"))
            if settings.get("scene_sound") == "elevenlabs" and _exists(scene_sound(reel_dir, s["id"])):
                items.append(dict(id=f"scene{s['id']}_sound", label=f"Scene {s['id']} · sound", kind="audio",
                                  path=scene_sound(reel_dir, s["id"]), text=s["sound_prompt"],
                                  text_label="Sound prompt (English)"))
    elif stage == "clips" and clip.get("type") in ("stock", "local_ai", "external_ai"):
        stock = clip["type"] == "stock"
        for s in plan["scenes"]:
            path = scene_clip(reel_dir, s["id"])
            if _exists(path):
                items.append(dict(id=f"scene{s['id']}_clip", label=f"Scene {s['id']} · clip · {s['title']}",
                                  kind="video", path=path, text=s["search_query"] if stock else s["motion_prompt"],
                                  text_label="Stock search (English)" if stock else "Motion prompt (English)"))
    return items


def review_payload(reel_dir):
    """What the review panel shows for a reel waiting for review (None otherwise)."""
    state = read_run_state(reel_dir)
    if state.get("status") != "review":
        return None
    stage = state.get("stage", "")
    plan = load_plan(reel_dir)
    return {"reel_dir": reel_dir, "stage": stage, "storyboard": storyboard_text(reel_dir, plan) if plan else "",
            "items": review_items(reel_dir, stage, plan)}


# --- One production ------------------------------------------------------------------------
class Production:
    """A reel being made: its settings, its folder, its storyboard and every file of every scene. Each make_*
    method reuses the file when it already exists, unless force=True (a regeneration)."""

    def __init__(self, settings, run, reel_dir):
        self.s, self.run, self.dir = dict(settings), run, reel_dir
        self.idea = (self.s.get("idea") or "").strip()
        self.llm_key = llm_provider_choices().get(self.s.get("llm_provider", ""))
        if not self.llm_key:
            raise RuntimeError(f"Unknown LLM provider: {self.s.get('llm_provider')}")
        self.model = self.s.get("llm_model") or default_model_for(self.llm_key)
        self.montage = montage_options(self.s.get("montage"))
        self.aspect = self.montage["aspect"]
        self.clip_provider = self.s.get("clip_provider", "pexels")
        self.clip = CLIP_PROVIDERS[self.clip_provider]
        self.image_provider = self.s.get("image_provider", "flux")
        self.voice_provider = self.s.get("voice_provider", "edge")
        self.work = os.path.join(WORK_DIR, os.path.basename(reel_dir))
        self.plan = load_plan(reel_dir)

    # -- setup -------------------------------------------------------------------------------
    @classmethod
    def open(cls, settings, run, reel_dir=None):
        """Checks everything up front, creates (or reopens) the reel folder and copies the uploads into it."""
        s = dict(settings)
        if not (s.get("idea") or "").strip() and reel_dir:
            s["idea"] = load_settings(reel_dir).get("idea") or os.path.basename(reel_dir).replace("-", " ")
        if not (s.get("idea") or "").strip():
            raise RuntimeError("Write an idea for the reel.")
        if reel_dir and not os.path.isdir(reel_dir):
            raise RuntimeError(f"The reel folder to continue was not found: {reel_dir}")
        run.set("Checking providers", 1)
        yield run.snapshot()
        if not drive_responds():
            raise RuntimeError("Google Drive is not answering (it happens after hours of session). Run Cell 1 "
                               "again to reconnect it, then Cell 7.")
        own_files = [f for f in (s.get("own_files") or []) if f and os.path.exists(f)]
        saved_own = sorted(glob.glob(f"{reel_dir}/clips/own_*")) if reel_dir else []
        llm_key = llm_provider_choices().get(s.get("llm_provider", ""))
        if not llm_key:
            raise RuntimeError(f"Unknown LLM provider: {s.get('llm_provider')}")
        errors, warnings = check_selection(llm_key, s["clip_provider"], s["voice_provider"],
                                           s.get("image_provider", "flux"), s.get("music_source", "none"),
                                           s.get("confirm_paid"), len(own_files) or len(saved_own),
                                           s.get("make_cover", True), s.get("use_keyframes", True),
                                           s.get("scene_sound", "none"))
        for w in warnings:
            run.log(f"⚠️ {re.sub('<[^>]+>', '', w)}")
        if errors:
            raise RuntimeError("Cannot start:\n- " + "\n- ".join(re.sub("<[^>]+>", "", e) for e in errors))
        reel_dir = run.reel_dir = reel_dir or unique_reel_dir(slugify(s["idea"]))
        for d in (reel_dir, f"{reel_dir}/images", f"{reel_dir}/audio", f"{reel_dir}/clips",
                  os.path.join(WORK_DIR, os.path.basename(reel_dir))):
            os.makedirs(d, exist_ok=True)
        if own_files:                                   # new uploads replace the earlier ones
            for old in saved_own:
                os.remove(old)
            for i, src in enumerate(own_files, 1):
                shutil.copy(src, f"{reel_dir}/clips/own_{i:02}{os.path.splitext(src)[1].lower()}")
        sample = s.get("clone_sample")
        if sample and os.path.exists(sample):
            for old in glob.glob(f"{reel_dir}/voice_sample.*"):
                os.remove(old)
            shutil.copy(sample, f"{reel_dir}/voice_sample{os.path.splitext(sample)[1].lower()}")
        write_run_state(reel_dir, "running", run.stage)
        save_settings(reel_dir, s)
        save_assets(reel_dir, music_source=s.get("music_source", "none"))
        run.log(f"Reel folder in Drive: {reel_dir}")
        run.result = cls(s, run, reel_dir)

    def log_reuse(self, what):
        self.run.log(f"♻️ {what}: already made, reused.")

    def save_plan(self):
        with open(f"{self.dir}/plan.json", "w", encoding="utf-8") as f:
            json.dump(self.plan, f, indent=2, ensure_ascii=False)
        with open(f"{self.dir}/script.txt", "w", encoding="utf-8") as f:
            f.write(plan_script(self.plan))
        save_assets(self.dir, language=self.plan.get("language", "en"))
        self.run.script = storyboard_text(self.dir, self.plan)

    def scene(self, i):
        return self.plan["scenes"][i - 1]

    def needs_scene_images(self):
        return self.clip["type"] == "ai_image" or (self.clip.get("accepts_image") and self.s.get("use_keyframes", True))

    def lengths(self):
        voices = [scene_voice(self.dir, s["id"]) for s in self.plan["scenes"]]
        return scene_lengths(self.plan["scenes"], voices, self.montage["voice_speed"])

    def pause(self, stage):
        """True when the reel waits for your review after `stage` (nothing to review = no pause)."""
        if not self.s.get("review_steps", True) or stage in approved_stages(self.dir):
            return False
        if stage != "storyboard" and not review_items(self.dir, stage, self.plan, self.s):
            return False
        write_run_state(self.dir, "review", stage)
        self.run.review = review_payload(self.dir)
        self.run.set(f"Review the {STAGES[stage]}")
        self.run.log(f"👀 The {STAGES[stage]} are ready: review them and press «Confirm» to continue."
                     if stage != "storyboard" else "👀 Storyboard ready: edit it or regenerate it, then confirm.")
        return True

    # -- 1. storyboard -------------------------------------------------------------------------
    def storyboard(self, previous_text="", notes=""):
        run = self.run
        run.set("Writing the storyboard", 3)
        if self.plan and not previous_text and not notes:
            self.log_reuse("Storyboard")
            run.script = storyboard_text(self.dir, self.plan)
            return
        s = self.s
        run.log(f"Storyboard with {s['llm_provider']} ({self.model}): ~{s.get('duration', 35)} s, "
                f"style {s.get('style', 'auto')}, tone {s.get('tone', 'auto')}, scenes {s.get('num_scenes', 'auto')}...")
        yield run.snapshot()
        yield from _wait_for(run, lambda: generate_storyboard(self.idea, self.llm_key, self.model, s, self.aspect,
                                                              previous_text, notes))
        self.plan = run.result
        self.save_plan()
        run.set("Storyboard ready", 10)
        run.log(f"Storyboard ready: {len(self.plan['scenes'])} scenes in {self.plan['language_name']}, "
                f"{len(plan_script(self.plan).split())} words of narration.")
        yield run.snapshot()

    def apply_storyboard_text(self, text):
        """Your edited storyboard becomes the plan; scenes whose picture or sound changed get new prompts."""
        run = self.run
        run.set("Applying your storyboard", 5)
        parsed = parse_storyboard(text)
        plan, changed = apply_storyboard(self.plan, parsed)
        changed = sorted(set(changed) | set(scenes_to_translate(plan)))      # a prompt not in English is redone
        if changed:
            run.log(f"Writing the English prompts of scene(s): {', '.join(map(str, changed))}...")
            yield run.snapshot()
            yield from _wait_for(run, lambda: write_scene_prompts(plan, changed, self.llm_key, self.model,
                                                                  self.aspect, self.s.get("style", "auto")))
            plan = run.result
        self.plan = normalize_plan(plan, self.idea)
        self.save_plan()
        run.log(f"Storyboard confirmed: {len(self.plan['scenes'])} scenes"
                + (f", {len(changed)} with new prompts." if changed else ", prompts unchanged."))
        yield run.snapshot()

    # -- publish kit (needs the final narration) ------------------------------------------------
    def kit(self):
        run = self.run
        if not self.s.get("make_kit", True):
            return
        run.set("Publish kit", 11)
        if _exists(f"{self.dir}/publish_kit.json"):
            run.kit = load_publish_kit(self.dir)
            return
        run.log("Writing titles, captions, hashtags and hooks for YouTube, TikTok, Instagram and Facebook...")
        yield run.snapshot()
        try:
            yield from _wait_for(run, lambda: generate_publish_kit(self.llm_key, self.model, self.idea, self.plan))
            run.kit = run.result
            save_publish_kit(run.kit, self.dir)
            save_assets(self.dir, hooks=run.kit.get("on_screen_hooks", []))
            run.log(f"Publish kit ready (viral potential {run.kit.get('virality', {}).get('overall', '?')}/100).")
        except Exception as exc:     # the kit is a bonus: never lose the reel because of it
            run.log(f"⚠️ Publish kit failed, the reel continues: {exc}")
        yield run.snapshot()

    # -- 2. images -----------------------------------------------------------------------------
    def make_cover(self, force=False):
        path = f"{self.dir}/cover.png"
        if _exists(path) and not force:
            return self.log_reuse("Cover")
        label = IMAGE_PROVIDERS[self.image_provider]["label"]
        self.run.log(f"Cover with {label}...")
        yield self.run.snapshot()
        yield from _wait_for(self.run, lambda: generate_image(self.image_provider, self.plan["cover_prompt"], path,
                                                              self.aspect))
        self.run.media.append((path, "Cover"))

    def make_image(self, i, force=False):
        path = scene_image(self.dir, i)
        if _exists(path) and not force:
            return self.log_reuse(f"Scene {i} image")
        self.run.log(f"Scene {i}: image with {IMAGE_PROVIDERS[self.image_provider]['label']}...")
        yield self.run.snapshot()
        yield from _wait_for(self.run, lambda: generate_image(self.image_provider, self.scene(i)["image_prompt"],
                                                              path, self.aspect))
        self.run.media.append((path, f"Scene {i}"))

    def images(self):
        run, n = self.run, len(self.plan["scenes"])
        if self.s.get("make_cover", True) and self.plan.get("cover_prompt"):
            run.set("Cover", 12)
            yield from self.make_cover()
            yield run.snapshot()
        if self.needs_scene_images():
            for i in range(1, n + 1):
                run.set(f"Image {i}/{n}", 13 + 17 * (i - 1) / n)
                yield from self.make_image(i)
                yield run.snapshot()
        if self.image_provider == "flux" and self.clip["type"] == "local_ai":
            forge_unload()          # free VRAM before ComfyUI loads the video model

    # -- 3. audio: the narration of every scene, its word timings, its sound effect ---------------
    def voice_sample(self):
        found = sorted(glob.glob(f"{self.dir}/voice_sample.*"))
        return found[0] if found else None

    def make_voice(self, i, force=False):
        scene, old = self.scene(i), scene_voice(self.dir, i)
        if not scene["narration"]:
            for path in (old, scene_words(self.dir, i)):
                if path and os.path.exists(path):
                    os.remove(path)
            return
        if old and not force:
            return self.log_reuse(f"Scene {i} voice")
        label = VOICE_PROVIDERS[self.voice_provider]["label"]
        self.run.log(f"Scene {i}: voice with {label}...")
        yield self.run.snapshot()
        work = f"{self.work}/scene_{i:02}_voice.mp3"
        yield from _wait_for(self.run, lambda: synthesize_voice(scene["narration"], self.voice_provider,
                                                                self.s.get("voice", ""), self.voice_sample(), "", work))
        audio_path, message = self.run.result
        if not audio_path:
            raise RuntimeError(message)
        for path in glob.glob(f"{self.dir}/audio/scene_{i:02}_voice.*") + [scene_words(self.dir, i)]:
            if os.path.exists(path):
                os.remove(path)
        shutil.copy(audio_path, f"{self.dir}/audio/scene_{i:02}_voice{os.path.splitext(audio_path)[1]}")

    def make_words(self, i, force=False):
        voice, path = scene_voice(self.dir, i), scene_words(self.dir, i)
        if not voice or (_exists(path) and not force):
            return
        language, narration = self.plan.get("language", "en"), self.scene(i)["narration"]
        yield from _wait_for(self.run, lambda: transcribe_words(voice, language, narration))
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.run.result, f, ensure_ascii=False)

    def make_sound(self, i, seconds, force=False):
        scene, path = self.scene(i), scene_sound(self.dir, i)
        if self.s.get("scene_sound") != "elevenlabs" or not scene.get("sound_prompt"):
            return
        if _exists(path) and not force:
            return self.log_reuse(f"Scene {i} sound")
        self.run.log(f"Scene {i}: sound effect with ElevenLabs ({seconds:.1f} s): {scene['sound_prompt'][:80]}")
        yield self.run.snapshot()
        yield from _wait_for(self.run, lambda: elevenlabs_sound(scene["sound_prompt"], seconds, path))

    def audio(self):
        run, n = self.run, len(self.plan["scenes"])
        language = self.plan.get("language", "en")
        voice = self.s.get("voice", "")
        if re.match(r"^[a-z]{2}-[A-Z]{2}-", voice or "") and not voice.lower().startswith(language) \
                and "Multilingual" not in voice:
            run.log(f"⚠️ The voice {voice} speaks {voice[:5]}, but the storyboard is in {self.plan['language_name']}.")
        for i in range(1, n + 1):
            run.set(f"Voice {i}/{n}", 31 + 10 * (i - 1) / n)
            yield from self.make_voice(i)
            run.set(f"Timing the words {i}/{n}", 31 + 10 * (i - 0.5) / n)
            yield from self.make_words(i)
            yield run.snapshot()
        lengths = self.lengths()
        for i in range(1, n + 1):
            run.set(f"Sound {i}/{n}", 42 + 3 * (i - 1) / n)
            yield from self.make_sound(i, lengths[i - 1])
        run.script = storyboard_text(self.dir, self.plan)
        run.log(f"Audio ready: {sum(lengths):.1f} s in {n} scenes ({', '.join(f'{x:.1f}' for x in lengths)} s).")
        yield run.snapshot()

    # -- 4. clips -------------------------------------------------------------------------------
    def clip_paths(self):
        """The clip of every scene (one per scene; your files and footage are used in order, repeating)."""
        n, kind = len(self.plan["scenes"]), self.clip["type"]
        if kind == "ai_image":
            return [scene_image(self.dir, i) for i in range(1, n + 1)]
        if self.clip_provider in ("upload", "footage"):
            files = sorted(glob.glob(f"{self.dir}/clips/own_*")) if self.clip_provider == "upload" else footage_files()
            if not files:
                raise RuntimeError("There are no files of yours to use as clips.")
            return [files[(i - 1) % len(files)] for i in range(1, n + 1)]
        return [f"{self.dir}/clips/scene_{i:02}.mp4" for i in range(1, n + 1)]

    def save_clip_assets(self):
        kind = self.clip["type"]
        paths = self.clip_paths()
        save_assets(self.dir, clips=[reel_relative(self.dir, p) for p in paths], clip_type=kind,
                    clip_fill=["loop" if kind in ("stock", "own") else "hold"] * len(paths),
                    clip_audio=[kind != "stock" and not is_image(p) for p in paths])

    def make_clip(self, i, seconds, force=False):
        run, kind, scene = self.run, self.clip["type"], self.scene(i)
        path = f"{self.dir}/clips/scene_{i:02}.mp4"
        if kind not in ("stock", "local_ai", "external_ai"):
            return
        if _exists(path) and not force:
            return self.log_reuse(f"Scene {i} clip" + (" (already paid)" if kind == "external_ai" else ""))
        label = self.clip["label"]
        image = scene_image(self.dir, i) if self.clip.get("accepts_image") and self.s.get("use_keyframes", True) else None
        image = image if image and _exists(image) else None
        if kind == "stock":
            assets = load_assets(self.dir)
            used = set(assets.get("stock_used", []))
            search, orientation = STOCK_SEARCH[self.clip_provider], ORIENTATIONS[self.aspect]
            query = scene["search_query"] or self.idea
            urls = [u for u in search(query, orientation) if u not in used] or \
                [u for u in search(self.plan["search_terms"][0], orientation) if u not in used]
            if not urls:
                raise RuntimeError(f"{label} found no new video for «{query}».")
            download_file(urls[0], path)
            save_assets(self.dir, stock_used=sorted(used | {urls[0]}))
            run.log(f"Scene {i}: clip from {label} for «{query}».")
        elif kind == "local_ai":
            width, height = int(self.s.get("ai_width", 480)), int(self.s.get("ai_height", 832))
            if self.aspect == "16:9" and width < height or self.aspect == "9:16" and width > height:
                width, height = height, width
            if self.aspect == "1:1":
                width = height = min(width, height)
            frames = min(int(self.s.get("ai_frames", 121)), math.ceil(seconds * 24) + 1)
            run.log(f"Scene {i}: {label} {'image' if image else 'text'}-to-video ({width}x{height}, {frames} frames "
                    f"≈ {frames / 24:.1f} s for a {seconds:.1f} s scene)...")
            yield run.snapshot()
            yield from _wait_for(run, lambda: comfy_generate_video(
                self.clip_provider, image, scene["motion_prompt"], path, width, height, frames,
                self.s.get("ai_steps", 20), on_raw=run.tech))
        else:
            run.log(f"Scene {i}: {label} {'image' if image else 'text'}-to-video, {seconds:.1f} s (paid)...")
            yield run.snapshot()
            prompt = f"{scene['image_prompt']}. {scene['motion_prompt']}"
            yield from _wait_for(run, lambda: generate_ai_video(self.clip_provider, prompt, seconds, self.aspect, path,
                                                                on_raw=run.tech, image_path=image))
        run.media.append((path, f"Clip {i}"))

    def clips(self):
        run, n = self.run, len(self.plan["scenes"])
        lengths = self.lengths()
        for i in range(1, n + 1):
            run.set(f"Clip {i}/{n}", 46 + 29 * (i - 1) / n)
            yield from self.make_clip(i, lengths[i - 1])
            yield run.snapshot()
        if self.clip["type"] == "local_ai":
            comfy_free()
        self.save_clip_assets()
        run.set("Clips ready", 75)
        yield run.snapshot()

    # -- 5. music and assembly -------------------------------------------------------------------
    def music(self):
        run, source = self.run, self.s.get("music_source", "none")
        saved = load_assets(self.dir).get("music")
        saved = reel_absolute(self.dir, saved) if saved else ""
        song = None
        if source == "none":
            pass
        elif _exists(saved) and (source != "elevenlabs" or saved.startswith(MUSIC_AI_DIR)):
            song = saved
            self.log_reuse(f"Music {os.path.basename(song)}")
        elif source == "library" and music_files():
            song = random.choice(music_files())
            run.log(f"Background music: {os.path.basename(song)}")
        elif source == "elevenlabs":
            run.set("AI music (ElevenLabs)", 76)
            run.log(f"Composing an instrumental with ElevenLabs Music: {self.plan.get('music_prompt', '')[:120]}")
            yield run.snapshot()
            target = f"{MUSIC_AI_DIR}/{os.path.basename(self.dir)}.mp3"
            try:
                yield from _wait_for(run, lambda: elevenlabs_music(self.plan.get("music_prompt", ""),
                                                                   sum(self.lengths()) + 2, target))
                song = target
                run.log(f"Music saved for reuse: {target}")
            except Exception as exc:          # music is a bonus: never lose the reel because of it
                run.log(f"⚠️ AI music failed, the reel continues without music: {exc}")
                run.tech(str(exc))
        save_assets(self.dir, music=song or "")
        yield run.snapshot()

    def run_steps(self):
        """From wherever the reel is to the next pause for review, or to the end."""
        run = self.run
        steps = ((self.storyboard, "storyboard"), (self.kit, None), (self.images, "images"), (self.audio, "audio"),
                 (self.clips, "clips"))
        for step, stage in steps:
            yield from step()
            if stage and self.pause(stage):
                yield run.snapshot()
                return
        yield from self.music()
        yield from assemble_reel(run, self.dir, self.montage, pct_start=78)
        run.done, run.review = True, None
        run.set("Done", 100)
        run.log(f"DONE. {len(run.videos)} video(s) saved in Drive: {self.dir}")
        write_run_state(self.dir, "done", "Done")
        yield run.snapshot()


def generate_storyboard(idea, provider_key, model, settings, aspect="9:16", previous_text="", notes=""):
    system = build_storyboard_prompt(settings.get("num_scenes", "auto"), int(settings.get("duration", 35)),
                                     settings.get("tone", "auto"), settings.get("instructions", ""), aspect,
                                     settings.get("style", "auto"))
    raw = extract_json(llm_complete(provider_key, model, system, storyboard_request(idea, previous_text, notes)))
    return normalize_plan(raw, idea)


def write_scene_prompts(plan, ids, provider_key, model, aspect="9:16", style="auto"):
    answer = extract_json(llm_complete(provider_key, model, build_scene_prompts_prompt(aspect, style),
                                       scene_prompts_request(plan, ids)))
    return merge_scene_prompts(plan, answer)


def _guarded(run, body):
    """Runs a step generator; a stop marks the reel stopped, an error marks it failed (resumable)."""
    try:
        yield from body()
    except GeneratorExit:
        if run.reel_dir:
            write_run_state(run.reel_dir, "stopped", run.stage)
        raise
    except Exception as exc:
        run.fail(exc)
        if run.reel_dir:
            write_run_state(run.reel_dir, "failed", run.stage, exc)
            run.log("🔁 Press «Resume» to continue from here: nothing already made is generated again.")
        yield run.snapshot()


def produce_reel(settings, resume_dir=None):
    """Generator: a new reel (or `resume_dir` continued with these settings), up to the next pause or the end.
    settings keys: idea, duration, style, tone, instructions, num_scenes, llm_provider, llm_model, image_provider,
    make_cover, use_keyframes, clip_provider, confirm_paid, own_files, voice_provider, voice, clone_sample,
    scene_sound, music_source, make_kit, review_steps, ai_width, ai_height, ai_frames, ai_steps, montage (dict)."""
    run = ReelRun()

    def body():
        run.log("Resuming: what is already made is reused..." if resume_dir else
                "Checking Google Drive, providers, keys and tools...")
        yield from Production.open(settings, run, resume_dir)
        yield from run.result.run_steps()
    yield from _guarded(run, body)


def continue_reel(reel_dir, edited_storyboard=None):
    """Generator: «Confirm». Approves the step under review (applying your edited storyboard first) and goes on
    with the reel's own settings. On a reel that is not under review it simply resumes it."""
    run = ReelRun()
    run.reel_dir = reel_dir

    def body():
        settings = load_settings(reel_dir)
        if not settings:
            raise RuntimeError("This reel has no saved settings.")
        state = read_run_state(reel_dir)
        yield from Production.open(settings, run, reel_dir)
        prod = run.result
        if state.get("status") == "review":
            stage = state.get("stage", "")
            if stage == "storyboard" and edited_storyboard is not None:
                yield from prod.apply_storyboard_text(edited_storyboard)
            approve_stage(reel_dir, stage)
            run.log(f"✅ {STAGES.get(stage, stage).capitalize()} confirmed.")
        yield from prod.run_steps()
    yield from _guarded(run, body)


def _back_to_review(run, reel_dir, stage):
    write_run_state(reel_dir, "review", stage)
    run.review = review_payload(reel_dir)
    run.script = storyboard_text(reel_dir)
    run.set(f"Review the {STAGES.get(stage, stage)}")


def regenerate_storyboard(reel_dir, notes="", edited_text=""):
    """Generator: a new storyboard for a reel under storyboard review (your notes and edits are taken into
    account); it waits for your review again."""
    run = ReelRun()
    run.reel_dir, run.review = reel_dir, review_payload(reel_dir)     # the panel stays while it works
    try:
        yield from Production.open(load_settings(reel_dir), run, reel_dir)
        prod = run.result
        previous = edited_text or storyboard_text(reel_dir, prod.plan)
        yield from prod.storyboard(previous_text=previous or "(none)", notes=notes)
        for name in ("publish_kit.json", "publish_kit.md"):        # the kit follows the new narration
            if os.path.exists(f"{reel_dir}/{name}"):
                os.remove(f"{reel_dir}/{name}")
    except GeneratorExit:
        write_run_state(reel_dir, "review", "storyboard")
        raise
    except Exception as exc:
        run.fail(exc)
    _back_to_review(run, reel_dir, "storyboard")
    yield run.snapshot()


ITEM_ID = re.compile(r"^(?:cover|scene(\d+)_(image|audio|sound|clip))$")


def regenerate_item(reel_dir, item_id, text=None):
    """Generator: makes one item of the step under review again (cover, sceneN_image, sceneN_audio,
    sceneN_sound, sceneN_clip), with its prompt or narration replaced by `text` when given."""
    run = ReelRun()
    run.reel_dir, run.review = reel_dir, review_payload(reel_dir)     # the panel stays while it works
    stage = read_run_state(reel_dir).get("stage", "")
    try:
        match = ITEM_ID.match(item_id or "")
        if not match:
            raise RuntimeError(f"Unknown item: {item_id}")
        yield from Production.open(load_settings(reel_dir), run, reel_dir)
        prod = run.result
        number, kind = (int(match.group(1)), match.group(2)) if match.group(1) else (0, "cover")
        if number and not 1 <= number <= len(prod.plan["scenes"]):
            raise RuntimeError(f"The reel has no scene {number}.")
        field = {"cover": "cover_prompt", "image": "image_prompt", "audio": "narration", "sound": "sound_prompt",
                 "clip": "search_query" if prod.clip["type"] == "stock" else "motion_prompt"}[kind]
        target = prod.plan if kind == "cover" else prod.scene(number)
        if text is not None and text.strip() != target.get(field, ""):
            target[field] = text.strip()
            prod.save_plan()
            run.log(f"{item_id}: {field.replace('_', ' ')} updated.")
        run.set(f"Regenerating {item_id}", 10)
        if kind == "cover":
            yield from prod.make_cover(force=True)
        elif kind == "image":
            yield from prod.make_image(number, force=True)
        elif kind == "audio":
            yield from prod.make_voice(number, force=True)
            yield from prod.make_words(number, force=True)
        elif kind == "sound":
            yield from prod.make_sound(number, prod.lengths()[number - 1], force=True)
        else:
            yield from prod.make_clip(number, prod.lengths()[number - 1], force=True)
            prod.save_clip_assets()
        run.set(f"{item_id} regenerated", 100)
        run.log(f"✅ {item_id} regenerated.")
    except GeneratorExit:
        write_run_state(reel_dir, "review", stage)
        raise
    except Exception as exc:
        run.fail(exc)
    _back_to_review(run, reel_dir, stage)
    yield run.snapshot()


# --- Assembly ----------------------------------------------------------------------------------
def assemble_reel(run, reel_dir, montage=None, pct_start=78):
    """Generator: renders every variant from the files saved in the reel folder, scene by scene. Used at the
    end of a production and by «Re-assemble» in My reels. Finals land in run.videos."""
    opt = montage_options(montage)
    assets, plan = load_assets(reel_dir), load_plan(reel_dir)
    if not plan:
        raise RuntimeError("This reel has no storyboard (plan.json).")
    scenes, n = plan["scenes"], len(plan["scenes"])
    slug = os.path.basename(os.path.normpath(reel_dir))
    work = os.path.join(WORK_DIR, slug, "montage")
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work, exist_ok=True)
    width, height = FORMATS[opt["aspect"]]
    clips = [reel_absolute(reel_dir, c) for c in assets.get("clips", [])]
    if len(clips) != n or not all(os.path.exists(c) for c in clips):
        raise RuntimeError("This reel is missing the clip of some scene: resume it to make it.")
    voices = [scene_voice(reel_dir, s["id"]) for s in scenes]
    if any(s["narration"] and not v for s, v in zip(scenes, voices)):
        raise RuntimeError("This reel is missing the voice of some scene: resume it to make it.")
    lengths = scene_lengths(scenes, voices, opt["voice_speed"])
    starts = [sum(lengths[:i]) for i in range(n)]
    transition = opt["transition"]
    tail = min(opt["transition_duration"], min(lengths) * 0.4) if n > 1 and transition != "none" else 0.0
    fills = assets.get("clip_fill") or ["hold"] * n
    clip_sound = assets.get("clip_audio") or [False] * n

    run.set("Timing the subtitles", pct_start)
    run.log(f"Montage: {opt['aspect']}, {n} scenes ({sum(lengths):.1f} s), transition "
            f"{TRANSITIONS[transition].lower()}, subtitles {SUBTITLE_STYLES[opt['subtitle_style']].lower()}.")
    yield run.snapshot()
    words = []
    for i, (scene, voice) in enumerate(zip(scenes, voices), 1):
        if not voice:
            continue
        path = scene_words(reel_dir, i)
        if not _exists(path):
            yield from _wait_for(run, lambda: transcribe_words(voice, plan.get("language", "en"), scene["narration"]))
            with open(path, "w", encoding="utf-8") as f:
                json.dump(run.result, f, ensure_ascii=False)
        with open(path, encoding="utf-8") as f:
            words += [dict(w, start=starts[i - 1] + w["start"] / opt["voice_speed"],
                           end=starts[i - 1] + w["end"] / opt["voice_speed"]) for w in json.load(f)]
    write_srt(words, f"{reel_dir}/subtitles.srt")

    videos, mixes, keys = [], [], []
    span = 20
    for i, (scene, clip) in enumerate(zip(scenes, clips), 1):
        run.set(f"Scene {i}/{n}: picture and sound", pct_start + span * (i - 1) / n)
        seconds = lengths[i - 1] + (tail if i < n else 0.0)
        yield from _wait_for(run, lambda: render_scene_video(clip, f"{work}/scene_{i:02}.mp4", seconds, width, height,
                                                             opt["fit"], opt["clip_speed"], fills[i - 1], i - 1))
        tempo, _ = run.result
        own_sound = clip if opt["clip_audio"] and clip_sound[i - 1] and has_audio(clip) else None
        sound = scene_sound(reel_dir, i)
        yield from _wait_for(run, lambda: render_scene_audio(
            f"{work}/scene_{i:02}_mix.wav", f"{work}/scene_{i:02}_voice.wav", lengths[i - 1], opt, voices[i - 1],
            own_sound, tempo, fills[i - 1] == "loop", sound if _exists(sound) else None))
        videos.append(f"{work}/scene_{i:02}.mp4")
        mixes.append(f"{work}/scene_{i:02}_mix.wav")
        keys.append(f"{work}/scene_{i:02}_voice.wav")
        yield run.snapshot()
    mix, key = join_audio(mixes, f"{work}/mix.wav"), join_audio(keys, f"{work}/voice_key.wav")

    hooks = [h for h in [plan.get("on_screen_text", "")] + list(assets.get("hooks", [])) if h] or [""]
    first_song = reel_absolute(reel_dir, assets["music"]) if assets.get("music") else None
    songs = music_files() if assets.get("music_source") == "library" else []
    outputs = []
    base = pct_start + span
    per = (99 - base) / opt["variants"]
    for v in range(opt["variants"]):
        music = first_song
        if v and songs:                                   # each extra variant gets the next song of your library
            music = songs[((songs.index(first_song) if first_song in songs else 0) + v) % len(songs)]
        if music and not os.path.exists(music):
            music = None
        hook = hooks[v % len(hooks)]
        ass_path = None
        if opt["subtitle_style"] != "off" or (hook and opt["hook_overlay"]):
            ass_path = f"{work}/subtitles_v{v + 1}.ass"
            with open(ass_path, "w", encoding="utf-8") as f:
                f.write(build_ass(words, width, height, opt, hook))
        run.set(f"Variant {v + 1}: final render", base + per * v)
        run.log(f"Variant {v + 1}: {n} scenes" + (f", music {os.path.basename(music)}" if music else ", no music")
                + (f", hook «{hook}»" if hook and opt["hook_overlay"] else "") + ".")
        yield run.snapshot()
        local = f"{work}/final_v{v + 1}.mp4"
        yield from _wait_for(run, lambda: render_final(videos, lengths, transition, tail, mix, key, music, ass_path,
                                                       local, opt, seed=v))
        final = f"{reel_dir}/{slug}_FINAL{'' if v == 0 else f'_v{v + 1}'}.mp4"
        shutil.copy(local, final)
        outputs.append(final)
        run.videos, run.video = list(outputs), outputs[0]
        run.log(f"Variant {v + 1} saved: {final}")
        yield run.snapshot()
    for extra in glob.glob(f"{reel_dir}/{slug}_FINAL_v*.mp4"):         # variants from an earlier montage
        if extra not in outputs:
            os.remove(extra)
    save_assets(reel_dir, montage=opt)
    settings = load_settings(reel_dir)
    if settings:                                   # settings.json always shows the montage of the current video
        settings["montage"] = opt
        save_settings(reel_dir, settings)


def reassemble_reel(reel_dir, montage):
    """Generator: a new montage of a saved reel (other format, subtitles, transitions, volumes...) without
    generating anything again."""
    run = ReelRun()
    run.reel_dir = reel_dir
    try:
        if not reel_dir or not os.path.isdir(reel_dir):
            raise RuntimeError("Pick a reel first.")
        run.kit = load_publish_kit(reel_dir)
        run.script = storyboard_text(reel_dir)
        run.log(f"Re-assembling {os.path.basename(reel_dir)} from its saved files...")
        yield from assemble_reel(run, reel_dir, montage, pct_start=5)
        run.done = True
        run.set("Done", 100)
        write_run_state(reel_dir, "done", "Done")
        run.log("DONE.")
        yield run.snapshot()
    except Exception as exc:
        run.fail(exc)
        yield run.snapshot()
