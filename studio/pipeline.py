"""Reel production pipeline used by the Control Center.

idea -> script + publish kit (any LLM) -> images (FLUX or image API) -> voice (any provider) + word timings
     -> clips (stock, your own footage, local AI, external AI, AI photos) -> music (your songs or AI)
     -> montage (format, cuts, transitions, karaoke subtitles, on-screen hook, ducking, variants)
     -> publishing (Upload-Post: TikTok, Instagram, YouTube, Facebook)
Every kind of content picks its own provider, local or external; any combination works because every step
leaves a file in the reel's Drive folder. That is also what lets "Mis reels" re-assemble a reel later.

produce_reel() and reassemble_reel() are generators: every step yields a snapshot dict (log, stage, pct,
script, media, videos...) so the UI can show progress live. They also work from any code cell.
"""
import base64, uuid, glob, random, math, difflib, unicodedata, traceback, threading
from datetime import datetime
from .lib import *

OLLAMA_URL = f"http://127.0.0.1:{PORTS['ollama_api']}"
FORGE_URL = f"http://127.0.0.1:{PORTS['forge']}"
COMFY_URL = f"http://127.0.0.1:{PORTS['comfy']}"
NEGATIVE_VIDEO_PROMPT = "blurry, low quality, static image, distorted, deformed, watermark, text, subtitles"

FORMATS = {"9:16": (1080, 1920), "16:9": (1920, 1080), "1:1": (1080, 1080)}
FORMAT_WORDS = {"9:16": "vertical 9:16", "16:9": "horizontal 16:9", "1:1": "square 1:1"}
FPS = 30


def bearer(key):
    return {"Authorization": f"Bearer {key}"}


def is_image(path):
    return str(path).lower().endswith(MEDIA_IMAGE_EXTS)


# ---------------------------------------------------------------------------
# 1. Plan generation (LLM)
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


def llm_complete(provider_key, model, system_prompt, user_prompt, json_mode=True):
    """Unified chat completion. Returns the raw text answer."""
    if provider_key == "ollama":
        payload = {"model": model, "stream": False, "keep_alive": 0,   # free VRAM for Forge/ComfyUI afterwards
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


def language_name(code):
    return LANGUAGES.get(code, LANGUAGES["es"])[1]


def build_plan_prompt(num_scenes, language="es", duration=35, tone="informativo", instructions="", aspect="9:16"):
    lang = language_name(language)
    words = int(duration * 2.4)                 # ~2.4 spoken words per second
    shape = FORMAT_WORDS.get(aspect, "vertical 9:16")
    extra = f"\nExtra instructions from the creator (follow them): {instructions.strip()}" if instructions.strip() else ""
    return f"""You are an expert director of viral short-form videos (YouTube Shorts, Reels, TikTok).
Answer ONLY with a valid JSON object with exactly this structure:
{{
  "title_slug": "short-title-for-folder",
  "hook": "Opening line spoken in the first 3 seconds, in {lang}",
  "on_screen_text": "Max 6 words shown big on screen during the first seconds, in {lang}",
  "script": "Voice-over narration in {lang}, about {words} words (~{duration} seconds read aloud). Only the spoken words: no stage directions, no emojis, no hashtags.",
  "search_terms": ["five", "english", "stock-footage", "search", "keywords"],
  "cover_prompt": "Detailed cinematic English prompt for a {shape} thumbnail about this topic, photorealistic, high contrast",
  "music_prompt": "English description of instrumental background music for this video: genre, mood, tempo, instruments, no vocals",
  "scenes": [
    {{
      "id": 1,
      "search_query": "2-4 English words to find this shot on a stock-footage site",
      "image_prompt": "Detailed English prompt for image generation, {shape}, photorealistic",
      "motion_prompt": "Detailed English motion prompt for a video model: camera movement and subject action"
    }}
  ]
}}
Tone of the script: {TONES.get(tone, tone)}. The script opens with the hook and ends with a reason to comment,
follow or share. "script", "hook" and "on_screen_text" in {lang}; everything else in English.
Generate exactly {num_scenes} scenes, in the same order as the narration.{extra}"""


def generate_plan(idea, provider_key, model, num_scenes, language="es", duration=35, tone="informativo",
                  instructions="", aspect="9:16"):
    prompt = build_plan_prompt(num_scenes, language, duration, tone, instructions, aspect)
    plan = extract_json(llm_complete(provider_key, model, prompt, f"Reel idea: {idea}"))
    for field in ("script", "scenes"):
        if field not in plan:
            raise ValueError(f"The plan is missing '{field}':\n{json.dumps(plan, indent=2, ensure_ascii=False)[:800]}")
    if not plan["scenes"]:
        raise ValueError("The plan has no scenes.")
    plan.setdefault("search_terms", [idea])
    plan.setdefault("cover_prompt", "")
    plan.setdefault("music_prompt", "")
    plan.setdefault("hook", "")
    plan.setdefault("on_screen_text", " ".join(plan["hook"].split()[:6]))
    for scene in plan["scenes"]:
        scene.setdefault("search_query", (plan["search_terms"] or [idea])[0])
        scene.setdefault("image_prompt", plan.get("cover_prompt") or idea)
        scene.setdefault("motion_prompt", "slow cinematic camera push-in")
    return plan


# ---------------------------------------------------------------------------
# 2. Images: FLUX through Forge's API, or any OpenAI-compatible image API
# ---------------------------------------------------------------------------
FLUX_SIZES = {"9:16": (768, 1344), "16:9": (1344, 768), "1:1": (1024, 1024)}
IMAGE_API_SIZES = {"16:9": "1536x1024", "1:1": "1024x1024"}     # 9:16 uses the size saved in your keys


def forge_txt2img(prompt, output_path, width=768, height=1344, steps=20, seed=-1):
    """One FLUX image. The flux1-dev-fp8 checkpoint already contains VAE and text encoders."""
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


def generate_image(provider, prompt, output_path, aspect="9:16"):
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
        raise RuntimeError(f"{label}: no hubo respuesta al crear la tarea ({exc}). Puede que se haya cobrado: "
                           "revisa tu panel antes de reintentar.")
    if not 200 <= r.status_code < 300:
        raise RuntimeError(f"{label} rechazó la tarea: {api_error(r)}")
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
        raise RuntimeError(f"WaveSpeed no creó la tarea: {str(body)[:300]}")
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


def generate_ai_video(provider, prompt, seconds, aspect, output_path, on_raw=None, timeout=1800):
    """One paid text-to-video clip. seconds is clamped to what the provider accepts."""
    label = CLIP_PROVIDERS[provider]["label"]
    create, low, high = AI_VIDEO_CLIENTS[provider]
    seconds = int(min(max(round(seconds), low), high))
    raw = on_raw or (lambda m: None)
    task, poll, download_headers = create(prompt, seconds, aspect)
    if not task:
        raise RuntimeError(f"{label} no devolvió el ID de la tarea.")
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
                raise RuntimeError(f"{label}: no se pudo consultar la tarea {task}. Revisa tu panel: puede haberse cobrado.")
            continue
        if state == "done":
            if not info:
                raise RuntimeError(f"{label} terminó la tarea {task} sin un video descargable.")
            download_file(info, output_path, download_headers)
            raw(f"{label} | task {task} done in {int(time.time() - started)} s")
            return output_path
        if state == "failed":
            raise RuntimeError(f"{label}: la tarea {task} falló: {info}")
        if time.time() - last_note >= 30:
            last_note = time.time()
            raw(f"{label} | task {task}: {info or 'running'} ({int(time.time() - started)} s)")
    raise RuntimeError(f"{label}: la tarea {task} no terminó en {timeout // 60} min; revísala en tu panel.")


# ---------------------------------------------------------------------------
# 6. Music: your songs (Drive/music) or a new instrumental from ElevenLabs Music, saved for reuse
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


# ---------------------------------------------------------------------------
# 7. Voice timing: Whisper word timestamps, corrected with the script's own spelling
# ---------------------------------------------------------------------------
def media_duration(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=noprint_wrappers=1:nokey=1", path], capture_output=True, text=True)
    return float(r.stdout.strip() or 0)


def ffmpeg(args):
    r = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"] + args, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("ffmpeg failed:\n" + r.stderr[-1500:])


def _plain(word):
    word = unicodedata.normalize("NFKD", word).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]", "", word)


def align_to_script(words, script):
    """Whisper hears the words; the script knows how they are written (accents, names, punctuation).
    Matching runs of words take the script's spelling; anything uncertain keeps Whisper's."""
    tokens = script.split()
    a, b = [_plain(w["word"]) for w in words], [_plain(t) for t in tokens]
    out = [dict(w) for w in words]
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal" or (tag == "replace" and i2 - i1 == j2 - j1):
            for k in range(i2 - i1):
                out[i1 + k]["word"] = tokens[j1 + k]
    return out


def transcribe_words(audio_path, language="es", script=""):
    """Word-level timings on CPU, so Whisper never competes with Forge/ComfyUI for VRAM."""
    from faster_whisper import WhisperModel
    model = WhisperModel("small", device="cpu", compute_type="int8", download_root=TTS_MODELS_DIR)
    segments, _ = model.transcribe(audio_path, language=language, vad_filter=True, word_timestamps=True,
                                   initial_prompt=script[:400] or None)
    words = [{"word": w.word.strip(), "start": round(w.start, 3), "end": round(w.end, 3)}
             for seg in segments for w in (seg.words or []) if w.word.strip()]
    return align_to_script(words, script) if script else words


def group_words(words, max_words=3, max_chars=18):
    """Caption lines: a few words each, broken at punctuation and at pauses."""
    groups, current = [], []
    for w in words:
        joined = " ".join(x["word"] for x in current + [w])
        if current and (len(current) >= max_words or len(joined) > max_chars or w["start"] - current[-1]["end"] > 0.6):
            groups.append(current)
            current = []
        current.append(w)
        if re.search(r"[.!?…:;]$", w["word"]) or (w["word"].endswith(",") and len(current) >= 2):
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return groups


def _srt_time(seconds):
    ms = int(round(max(seconds, 0) * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def write_srt(words, path, max_words=7):
    """Plain captions file to upload to YouTube or any editor."""
    with open(path, "w", encoding="utf-8") as f:
        for i, g in enumerate(group_words(words, max_words, 42), 1):
            f.write(f"{i}\n{_srt_time(g[0]['start'])} --> {_srt_time(g[-1]['end'])}\n"
                    f"{' '.join(w['word'] for w in g)}\n\n")
    return path


# ---------------------------------------------------------------------------
# 8. Montage engine (ffmpeg): format, cuts, transitions, styled subtitles, hook, voice and music mix
# ---------------------------------------------------------------------------
TRANSITIONS = {"none": "Corte directo", "fade": "Fundido", "dissolve": "Disolver", "slideleft": "Deslizar",
               "wipeleft": "Barrido", "smoothleft": "Barrido suave", "fadeblack": "Fundido a negro", "circleopen": "Círculo",
               "random": "Aleatoria"}
# Only transitions present in every ffmpeg >= 4.3 (Colab ships 4.4).
RANDOM_TRANSITIONS = ["fade", "dissolve", "slideleft", "wipeleft", "smoothleft", "fadeblack", "circleopen"]
SUBTITLE_STYLES = {"karaoke": "Karaoke (palabra resaltada)", "word_pop": "Palabra por palabra (pop)",
                   "phrase": "Frases", "off": "Sin subtítulos"}
SUBTITLE_POSITIONS = {"bottom": "Abajo", "center": "Centro", "top": "Arriba"}
PACES = {"auto": "Automático", "1.5": "Cada 1.5 s", "2": "Cada 2 s", "2.5": "Cada 2.5 s", "3": "Cada 3 s",
         "4": "Cada 4 s", "5": "Cada 5 s"}
FITS = {"cover": "Recortar y llenar", "contain_blur": "Completo con fondo desenfocado"}

DEFAULT_MONTAGE = dict(
    aspect="9:16", fit="cover", transition="fade", transition_duration=0.35, pace="auto", clip_speed=1.0,
    voice_speed=1.0, voice_volume=1.0, music_volume=0.18, ducking=True,
    subtitle_style="karaoke", subtitle_position="bottom", font="Anton", font_size=90, words_per_line=3,
    text_color="#FFFFFF", highlight_color="#FFD400", outline_color="#000000", box=False, uppercase=True,
    hook_overlay=True, hook_seconds=2.5, variants=1,
)


def montage_options(raw=None):
    """Defaults + whatever was chosen, with types and ranges enforced."""
    opt = dict(DEFAULT_MONTAGE)
    opt.update({k: v for k, v in (raw or {}).items() if k in DEFAULT_MONTAGE and v is not None})
    for key in ("transition_duration", "clip_speed", "voice_speed", "voice_volume", "music_volume", "hook_seconds"):
        opt[key] = float(opt[key])
    for key in ("font_size", "words_per_line", "variants"):
        opt[key] = int(opt[key])
    opt["voice_speed"] = min(max(opt["voice_speed"], 0.5), 2.0)
    opt["clip_speed"] = min(max(opt["clip_speed"], 0.25), 4.0)
    opt["variants"] = min(max(opt["variants"], 1), 3)
    for key, allowed in (("aspect", FORMATS), ("fit", FITS), ("transition", TRANSITIONS), ("pace", PACES),
                         ("subtitle_style", SUBTITLE_STYLES), ("subtitle_position", SUBTITLE_POSITIONS)):
        opt[key] = str(opt[key])
        if opt[key] not in allowed:
            opt[key] = DEFAULT_MONTAGE[key]
    return opt


def font_families():
    """Fonts the subtitles can use: every family in Drive/fonts plus DejaVu Sans (always installed)."""
    families = set()
    if os.path.isdir(FONTS_DIR):
        if shutil.which("fc-scan"):
            r = subprocess.run(["fc-scan", "--format", "%{family[0]}\n", FONTS_DIR], capture_output=True, text=True)
            families.update(l.strip() for l in r.stdout.splitlines() if l.strip())
        else:
            families.update(os.path.splitext(f)[0].split("-")[0] for f in os.listdir(FONTS_DIR)
                            if f.lower().endswith((".ttf", ".otf")))
    families.add("DejaVu Sans")
    return sorted(families)


def ass_color(value, alpha=0):
    """'#RRGGBB' (or Gradio's 'rgba(r, g, b, a)') -> ASS '&HAABBGGRR'."""
    value = str(value or "#FFFFFF").strip()
    m = re.match(r"rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)", value)
    if m:
        r, g, b = (int(float(x)) for x in m.groups())
    else:
        h = value.lstrip("#")
        h = "".join(c * 2 for c in h) if len(h) == 3 else (h + "000000")[:6]
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"&H{alpha:02X}{b:02X}{g:02X}{r:02X}"


def _ass_time(t):
    cs = int(round(max(t, 0) * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02}:{s:02}.{cs:02}"


def _ass_text(text):
    return str(text).replace("\\", "/").replace("{", "(").replace("}", ")").replace("\n", " ")


def build_ass(words, width, height, opt, hook_text=""):
    """Styled subtitles (ASS, rendered by libass): karaoke highlight with a pop on the spoken word,
    one word at a time, or plain phrases; plus the on-screen hook for the first seconds."""
    scale = min(width, height) / 1080
    size = max(20, int(opt["font_size"] * scale))
    position = opt["subtitle_position"]
    align = {"bottom": 2, "center": 5, "top": 8}[position]
    margin_v = {"bottom": int(height * 0.2), "center": 0, "top": int(height * 0.14)}[position]
    side = int(width * 0.07)
    bold = -1 if opt["font"] == "DejaVu Sans" else 0
    text_c, high_c = ass_color(opt["text_color"]), ass_color(opt["highlight_color"])
    if opt["box"]:
        border, outline, shadow, outline_c = 3, max(6, int(size * 0.16)), 0, ass_color(opt["outline_color"], 0x40)
    else:
        border, outline, shadow, outline_c = 1, max(3, int(size * 0.08)), max(1, int(size * 0.03)), ass_color(opt["outline_color"])
    hook_align, hook_margin = (5, 0) if position == "top" else (8, int(height * 0.12))
    hook_size = int(size * 0.85)
    upper = (lambda s: s.upper()) if opt["uppercase"] else (lambda s: s)

    lines = ["[Script Info]", "ScriptType: v4.00+", f"PlayResX: {width}", f"PlayResY: {height}", "WrapStyle: 0",
             "ScaledBorderAndShadow: yes", "", "[V4+ Styles]",
             "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, "
             "Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
             "MarginL, MarginR, MarginV, Encoding",
             f"Style: Sub,{opt['font']},{size},{text_c},{high_c},{outline_c},&H80000000,{bold},0,0,0,100,100,0,0,"
             f"{border},{outline},{shadow},{align},{side},{side},{margin_v},1",
             f"Style: Hook,{opt['font']},{hook_size},&H00101010,{text_c},{high_c},&H80000000,{bold},0,0,0,100,100,0,0,"
             f"3,{max(8, int(hook_size * 0.22))},0,{hook_align},{side},{side},{hook_margin},1",
             "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]

    def event(start, end, style, text, layer=0):
        if end - start >= 0.02:
            lines.append(f"Dialogue: {layer},{_ass_time(start)},{_ass_time(end)},{style},,0,0,0,,{text}")

    style = opt["subtitle_style"]
    if style != "off" and words:
        max_chars = max(8, int(width * 0.86 / (size * 0.6)))
        groups = group_words(words, 1 if style == "word_pop" else opt["words_per_line"], max_chars)
        for gi, group in enumerate(groups):
            next_start = groups[gi + 1][0]["start"] if gi + 1 < len(groups) else group[-1]["end"] + 0.6
            group_end = min(group[-1]["end"] + 0.25, next_start)
            texts = [_ass_text(upper(w["word"])) for w in group]
            if style == "phrase":
                event(group[0]["start"], group_end, "Sub", "{\\fad(80,60)}" + " ".join(texts))
            elif style == "word_pop":
                event(group[0]["start"], group_end, "Sub",
                      "{\\fscx60\\fscy60\\t(0,90,\\fscx112\\fscy112)\\t(90,170,\\fscx100\\fscy100)}" + texts[0])
            else:                                                   # karaoke: the spoken word lights up and pops
                for i, w in enumerate(group):
                    start = group[0]["start"] if i == 0 else w["start"]
                    end = group[i + 1]["start"] if i + 1 < len(group) else group_end
                    parts = [("{\\c" + high_c + "\\fscx122\\fscy122\\t(0,120,\\fscx108\\fscy108)}" + t + "{\\r}")
                             if j == i else t for j, t in enumerate(texts)]
                    event(start, end, "Sub", " ".join(parts))
    if hook_text and opt["hook_overlay"]:
        event(0, opt["hook_seconds"], "Hook",
              "{\\fad(120,200)\\fscx80\\fscy80\\t(0,160,\\fscx100\\fscy100)}" + _ass_text(upper(hook_text)), layer=1)
    return "\n".join(lines) + "\n"


def plan_segments(clips, total, pace="auto", transition_duration=0.35, clip_speed=1.0):
    """Decide the cuts. Returns (segments, transition): segments = [(clip, start_s, frames, index)] whose
    lengths, minus the overlaps of the transitions, add up to the voice duration `total`.
    Clips are reused in order when there are more cuts than clips, starting further into each video."""
    info = [(c, None if is_image(c) else media_duration(c)) for c in clips]
    if pace == "auto":
        target = min(max(total / len(info), 1.5), 5.0)
        video_lengths = [d / clip_speed for _, d in info if d]
        if video_lengths:
            target = min(target, max(min(video_lengths), 1.5))     # never stretch a short AI clip into a loop
    else:
        target = float(pace)
    n = max(1, math.ceil(total / target - 1e-6))
    transition = min(transition_duration, total / n * 0.4) if n > 1 else 0.0
    length = (total + (n - 1) * transition) / n
    frames = max(2, math.ceil(length * FPS))
    need = frames / FPS * clip_speed                          # seconds of source used by one cut
    segments, uses = [], {}
    for k in range(n):
        path, duration = info[k % len(info)]
        used = uses.get(path, 0)
        uses[path] = used + 1
        start = 0.0
        if duration and duration > need:
            start = (used * need) % max(duration - need, 0.01)
        segments.append((path, round(start, 2), frames, k))
    return segments, transition


KEN_BURNS = [   # zoompan expressions; {n} = frames of the cut
    "z='1+0.15*on/{n}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'",
    "z='1.15-0.15*on/{n}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'",
    "z='1.15':x='(iw-iw/zoom)*on/{n}':y='ih/2-(ih/zoom/2)'",
    "z='1.15':x='(iw-iw/zoom)*(1-on/{n})':y='ih/2-(ih/zoom/2)'",
]


def fit_filter(width, height, fit):
    if fit == "contain_blur":
        return (f"split=2[bg][fg];[bg]scale={width}:{height}:force_original_aspect_ratio=increase,"
                f"crop={width}:{height},boxblur=24:2,eq=brightness=-0.06[bgb];"
                f"[fg]scale={width}:{height}:force_original_aspect_ratio=decrease[fgs];"
                f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2")
    return f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}"


def render_segment(src, dst, start, frames, width, height, fit="cover", speed=1.0, motion=0):
    """One cut, already at the final size and 30 fps: video clips are trimmed/looped and sped up or down,
    photos get a slow Ken Burns move (zoom in, zoom out or pan, alternating)."""
    if is_image(src):
        zoom = KEN_BURNS[motion % len(KEN_BURNS)].format(n=frames)
        vf = (f"{fit_filter(width * 2, height * 2, fit)},zoompan={zoom}:d={frames}:s={width}x{height}:fps={FPS},"
              "setsar=1,format=yuv420p")
        args = ["-i", src, "-vf", vf]
    else:
        vf = f"setpts=PTS/{speed:.4f},{fit_filter(width, height, fit)},fps={FPS},setsar=1,format=yuv420p"
        args = ["-stream_loop", "-1", "-ss", f"{start:.2f}", "-i", src, "-vf", vf]
    ffmpeg(args + ["-frames:v", str(frames), "-an"] + video_encoder_args() + [dst])
    return dst


def prepare_voice(src, dst, speed=1.0, volume=1.0):
    """Voice speed (atempo keeps the pitch) and volume, as 48 kHz stereo WAV."""
    filters, rest = [], speed
    while rest > 2.0:
        filters.append("atempo=2.0")
        rest /= 2.0
    while rest < 0.5:
        filters.append("atempo=0.5")
        rest /= 0.5
    if abs(rest - 1) > 1e-3:
        filters.append(f"atempo={rest:.4f}")
    if abs(volume - 1) > 1e-3:
        filters.append(f"volume={volume:.3f}")
    filters.append("aresample=48000")
    ffmpeg(["-i", src, "-af", ",".join(filters), "-ac", "2", "-c:a", "pcm_s16le", dst])
    return dst


def _filter_path(path):
    return path.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def render_final(cuts, frames, transition_duration, transition, voice, music, ass_path, output, opt, total, seed=0):
    """Joins the cuts (direct cuts or xfade transitions), burns the subtitles and mixes the voice with the
    music, which ducks under the voice (sidechain compression) when ducking is on. One encode."""
    rng = random.Random(seed)
    n = len(cuts)
    args = []
    for c in cuts:
        args += ["-i", c]
    args += ["-i", voice]
    if music:
        args += ["-stream_loop", "-1", "-i", music]
    chains = []
    if n == 1:
        chains.append("[0:v]null[vc]")
    elif transition == "none" or transition_duration <= 0:
        chains.append("".join(f"[{i}:v]" for i in range(n)) + f"concat=n={n}:v=1:a=0[vc]")
    else:
        length, previous = frames[0] / FPS, "0:v"
        for i in range(1, n):
            name = rng.choice(RANDOM_TRANSITIONS) if transition == "random" else transition
            label = "vc" if i == n - 1 else f"x{i}"
            chains.append(f"[{previous}][{i}:v]xfade=transition={name}:duration={transition_duration:.3f}:"
                          f"offset={length - transition_duration:.3f}[{label}]")
            previous, length = label, length + frames[i] / FPS - transition_duration
    video_label = "vc"
    if ass_path:
        chains.append(f"[vc]ass={_filter_path(ass_path)}:fontsdir={_filter_path(FONTS_DIR)}[v]")
        video_label = "v"
    audio_format = "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"
    if music:
        chains.append(f"[{n + 1}:a]{audio_format},volume={opt['music_volume']:.3f},afade=t=in:d=1,"
                      f"afade=t=out:st={max(total - 2, 0):.2f}:d=2[m]")
        if opt["ducking"]:
            chains += [f"[{n}:a]{audio_format},asplit=2[va][vs]",
                       "[m][vs]sidechaincompress=threshold=0.02:ratio=10:attack=15:release=400[md]",
                       "[va][md]amix=inputs=2:duration=first:dropout_transition=0,volume=2[a]"]
        else:
            chains += [f"[{n}:a]{audio_format}[va]",
                       "[va][m]amix=inputs=2:duration=first:dropout_transition=0,volume=2[a]"]
        audio_map = "[a]"
    else:
        audio_map = f"{n}:a"
    args += ["-filter_complex", ";".join(chains), "-map", f"[{video_label}]", "-map", audio_map,
             "-t", f"{total:.3f}", "-r", str(FPS)] + video_encoder_args() + \
            ["-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", output]
    ffmpeg(args)
    return output


# Every reel folder keeps assets.json: the files the montage needs (paths relative to the folder when they
# live inside it). That is what makes "re-assemble" possible without generating anything again.
def save_assets(reel_dir, **changes):
    assets = load_assets(reel_dir)
    for key, value in changes.items():
        if key in ("voice", "music"):
            value = _relative(reel_dir, value) if value else ""
        elif key == "clips":
            value = [_relative(reel_dir, v) for v in value]
        assets[key] = value
    with open(f"{reel_dir}/assets.json", "w", encoding="utf-8") as f:
        json.dump(assets, f, indent=2, ensure_ascii=False)
    return assets


def load_assets(reel_dir):
    try:
        with open(f"{reel_dir}/assets.json", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _relative(reel_dir, path):
    path = os.path.abspath(path)
    return os.path.relpath(path, reel_dir) if path.startswith(os.path.abspath(reel_dir) + os.sep) else path


def _absolute(reel_dir, path):
    return path if os.path.isabs(path) else os.path.join(reel_dir, path)


def assemble_reel(run, reel_dir, montage=None, pct_start=72):
    """Generator: renders every variant from the files saved in the reel folder. Used at the end of a
    production and by "Volver a montar" in Mis reels. Finals land in run.videos."""
    opt = montage_options(montage)
    assets = load_assets(reel_dir)
    with open(f"{reel_dir}/plan.json", encoding="utf-8") as f:
        plan = json.load(f)
    slug = os.path.basename(os.path.normpath(reel_dir))
    work = os.path.join(WORK_DIR, slug, "montage")
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work, exist_ok=True)
    width, height = FORMATS[opt["aspect"]]

    clips = [_absolute(reel_dir, c) for c in assets.get("clips", [])]
    clips = [c for c in clips if os.path.exists(c)]
    voice_src = _absolute(reel_dir, assets.get("voice", ""))
    if not clips:
        raise RuntimeError("Este reel no tiene clips guardados para montar.")
    if not assets.get("voice") or not os.path.exists(voice_src):
        raise RuntimeError("Este reel no tiene la voz guardada.")

    run.set("Preparando la voz", pct_start)
    run.log(f"Montage: {opt['aspect']}, {FITS[opt['fit']].lower()}, cuts {PACES[opt['pace']].lower()}, "
            f"transition {TRANSITIONS[opt['transition']].lower()}, subtitles {SUBTITLE_STYLES[opt['subtitle_style']].lower()}.")
    yield run.snapshot()
    voice = prepare_voice(voice_src, f"{work}/voice.wav", opt["voice_speed"], opt["voice_volume"])
    total = media_duration(voice)

    words_path = f"{reel_dir}/words.json"
    if os.path.exists(words_path):
        with open(words_path, encoding="utf-8") as f:
            words = json.load(f)
    else:
        run.log("Timing every word with Whisper...")
        yield run.snapshot()
        yield from _wait_for(run, lambda: transcribe_words(voice_src, assets.get("language", "es"), plan["script"]))
        words = run.result
        with open(words_path, "w", encoding="utf-8") as f:
            json.dump(words, f, ensure_ascii=False)
    speed = opt["voice_speed"]
    words = [dict(w, start=w["start"] / speed, end=w["end"] / speed) for w in words]
    write_srt(words, f"{reel_dir}/subtitles.srt")

    hooks = [h for h in [plan.get("on_screen_text", "")] + list(assets.get("hooks", [])) if h] or [""]
    music_source = assets.get("music_source", "none")
    first_song = _absolute(reel_dir, assets["music"]) if assets.get("music") else None
    songs = music_files() if music_source == "library" else []

    cache, outputs = {}, []
    span = (99 - pct_start) / opt["variants"]
    for v in range(opt["variants"]):
        base_pct = pct_start + span * v
        order = list(clips)
        if v:
            random.Random(f"{slug}-{v}").shuffle(order)
        segments, transition = plan_segments(order, total, opt["pace"],
                                             opt["transition_duration"] if opt["transition"] != "none" else 0,
                                             opt["clip_speed"])
        files = []
        for i, (path, start, frames, k) in enumerate(segments):
            key = (path, start, frames, k % len(KEN_BURNS) if is_image(path) else 0)
            if key not in cache:
                run.set(f"Variante {v + 1}: corte {i + 1}/{len(segments)}", base_pct + span * 0.6 * i / len(segments))
                cache[key] = render_segment(path, f"{work}/cut_{len(cache):03}.mp4", start, frames, width, height,
                                            opt["fit"], opt["clip_speed"], k)
                yield run.snapshot()
            files.append(cache[key])
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
        run.set(f"Variante {v + 1}: render final", base_pct + span * 0.65)
        run.log(f"Variant {v + 1}: {len(segments)} cuts of {segments[0][2] / FPS:.1f} s"
                + (f", music {os.path.basename(music)}" if music else ", no music")
                + (f", hook «{hook}»" if hook and opt["hook_overlay"] else "") + ".")
        yield run.snapshot()
        local = f"{work}/final_v{v + 1}.mp4"
        yield from _wait_for(run, lambda: render_final(files, [s[2] for s in segments], transition, opt["transition"],
                                                       voice, music, ass_path, local, opt, total, seed=v))
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


# ---------------------------------------------------------------------------
# 9. Publish kit: per-platform titles, captions, hashtags, hooks (same LLM as the script)
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


def build_kit_prompt(language="es"):
    lang = language_name(language)
    return f"""You are a senior short-form video strategist for {lang}-speaking audiences.
From the reel's idea and final script, write everything needed to publish it. Answer ONLY with valid JSON:
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
- All text in {lang}, in the same tone as the script. Max 1-2 emojis per caption.
- Titles must deliver what the video promises (no empty clickbait). Use concrete numbers from the script.
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


def generate_publish_kit(provider_key, model, idea, plan, language="es"):
    user = (f"Reel idea: {idea}\nOpening hook: {plan.get('hook', '')}\n\nFinal script:\n{plan['script']}\n\n"
            f"Keywords: {', '.join(plan.get('search_terms', []))}")
    return normalize_kit(extract_json(llm_complete(provider_key, model, build_kit_prompt(language), user)))


def kit_markdown(kit):
    """A readable copy of the kit saved next to the reel in Drive (publish_kit.md)."""
    v = kit.get("virality", {})
    yt = kit["youtube"]
    lines = ["# Kit de publicación", "",
             f"**Potencial viral: {v.get('overall', '?')}/100** · {v.get('verdict', '')}",
             f"- Gancho {v.get('hook', '?')} · Retención {v.get('retention', '?')} · Compartible {v.get('shareability', '?')}"
             f" · Comentable {v.get('commentability', '?')} · Nicho {v.get('niche_fit', '?')} · Emoción {v.get('emotional', '?')}",
             f"- Punto fuerte: {v.get('strongest', '')}", f"- Punto débil: {v.get('weakest', '')}",
             f"- Recomendación: {v.get('recommendation', '')}", "",
             "## Texto en pantalla (primeros 2 s)", *[f"- {h}" for h in kit["on_screen_hooks"]], "",
             "## YouTube Shorts", f"**Título:** {yt['title']}", *[f"- Variante: {t}" for t in yt["title_variants"]],
             "", "**Descripción:**", yt["description"], "", f"**Etiquetas:** {yt['tags']}", ""]
    for platform, label in (("tiktok", "TikTok"), ("instagram", "Instagram Reels"), ("facebook", "Facebook Reels")):
        lines += [f"## {label}", kit[platform]["caption"], ""]
    lines += ["## Comentarios semilla", *[f"{i}. {c}" for i, c in enumerate(kit["seed_comments"], 1)], "",
              "## Teaser para historias", kit["teaser"]]
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
# 10. Publishing through Upload-Post: one upload, each platform with its own text from the kit
# ---------------------------------------------------------------------------
UPLOAD_POST_API = "https://api.upload-post.com/api"
PUBLISH_PLATFORMS = {"tiktok": "🎵 TikTok", "instagram": "📸 Instagram Reels", "youtube": "▶️ YouTube Shorts",
                     "facebook": "📘 Facebook Reels"}
YOUTUBE_PRIVACY = {"public": "Público", "unlisted": "No listado", "private": "Privado"}
TIKTOK_PRIVACY = {"PUBLIC_TO_EVERYONE": "Público", "MUTUAL_FOLLOW_FRIENDS": "Amigos", "FOLLOWER_OF_CREATOR": "Seguidores",
                  "SELF_ONLY": "Solo yo"}


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
    raise RuntimeError("Fecha de programación inválida: usa el formato 2026-11-02 18:30.")


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
        raise RuntimeError("Elige al menos una plataforma.")
    missing = credential_missing("upload_post")
    if missing:
        raise RuntimeError("Falta " + ", ".join(FIELD_LABELS.get(f, f) for f in missing) +
                           " de Upload-Post: agrégalo en la pestaña 🔑 Claves API.")
    if not kit:
        raise RuntimeError("Este reel no tiene kit de publicación: genéralo primero.")
    if not video_path or not os.path.exists(video_path):
        raise RuntimeError("No encuentro el video final.")
    data = upload_post_form(kit, platforms, **options)
    with open(video_path, "rb") as f:
        r = requests.post(f"{UPLOAD_POST_API}/upload", data=data, timeout=900,
                          headers={"Authorization": f"Apikey {get_key('upload_post', 'api_key')}"},
                          files={"video": (os.path.basename(video_path), f, "video/mp4")})
    if not 200 <= r.status_code < 300:
        raise RuntimeError(f"Upload-Post rechazó la publicación: {api_error(r)}")
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
            lines.append(f"✅ {label}: publicado {link}".strip())
        else:
            lines.append(f"❌ {label}: {entry.get('error') or entry.get('message') or 'falló'}")
    return lines


# ---------------------------------------------------------------------------
# 11. Orchestration with live progress
# ---------------------------------------------------------------------------
def slugify(text):
    """'Por qué los perros...' -> 'por-que-los-perros' (accents stripped, ASCII only)."""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")[:40] or "reel"


class ReelRun:
    """Live state of one production; snapshot() is what the UI renders."""
    LOG_FILE = f"{WORK_DIR}/current_run.log"     # local disk: `!tail -f` it from any cell

    def __init__(self):
        self.t0 = time.time()
        self.lines, self.media, self.tech_lines, self.videos = [], [], [], []
        self.stage, self.pct = "Preparando", 0
        self.script, self.video, self.failed, self.done = "", None, False, False
        self.kit, self.result, self.reel_dir = None, None, None
        try:
            os.makedirs(os.path.dirname(self.LOG_FILE), exist_ok=True)
            open(self.LOG_FILE, "w").close()
        except Exception:
            pass

    def tech(self, msg):
        """Technical log: raw tool output, HTTP error bodies, tracebacks (collapsible in the UI)."""
        stamp = time.strftime("%H:%M:%S")
        for line in str(msg).splitlines() or [""]:
            self.tech_lines.append(f"{stamp} | {line}")
        del self.tech_lines[:-800]               # keep the UI light on long runs

    def log(self, msg):
        elapsed = int(time.time() - self.t0)
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
        self.set(f"Error en: {self.stage}")
        self.log(f"❌ {exc}")
        response = getattr(exc, "response", None)
        if response is not None:
            self.tech(f"HTTP {getattr(response, 'status_code', '?')} {getattr(response, 'url', '')}\n"
                      f"{getattr(response, 'text', '')[:2000]}")
        self.tech(traceback.format_exc())
        if not isinstance(exc, RuntimeError):     # our own errors already explain the cause
            self.log(traceback.format_exc()[-1200:])

    def snapshot(self):
        return {"log": "\n".join(self.lines), "stage": self.stage, "pct": self.pct, "script": self.script,
                "media": list(self.media), "video": self.video, "videos": list(self.videos), "failed": self.failed,
                "done": self.done, "kit": self.kit, "tech": "\n".join(self.tech_lines), "reel_dir": self.reel_dir}


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


def produce_reel(settings):
    """Generator. Each kind of content uses its own provider, local or external, with no cross-conditions.
    settings keys: idea, language, duration, tone, instructions, num_scenes, llm_provider, llm_model,
    clip_provider, confirm_paid, own_files, keyframe_provider, voice_provider, voice, clone_sample,
    cover_provider, music_source, make_kit, ai_width, ai_height, ai_frames, ai_steps, montage (dict)."""
    run = ReelRun()
    s = dict(settings)
    try:
        idea = (s.get("idea") or "").strip()
        if not idea:
            raise RuntimeError("Escribe una idea para el reel.")
        llm_key = llm_provider_choices().get(s["llm_provider"])
        if not llm_key:
            raise RuntimeError(f"Proveedor LLM desconocido: {s['llm_provider']}")
        clip_provider, voice_provider = s["clip_provider"], s["voice_provider"]
        cover_provider, keyframe_provider = s.get("cover_provider", "none"), s.get("keyframe_provider", "none")
        music_source, language = s.get("music_source", "none"), s.get("language", "es")
        montage = montage_options(s.get("montage"))
        aspect = montage["aspect"]
        clip = CLIP_PROVIDERS[clip_provider]
        own_files = [f for f in (s.get("own_files") or []) if f and os.path.exists(f)]

        # --- Preflight -----------------------------------------------------
        run.set("Comprobando proveedores", 1)
        run.log("Checking Google Drive, providers, keys and tools...")
        yield run.snapshot()
        if not drive_responds():
            raise RuntimeError("Google Drive no responde (pasa tras horas de sesión). Vuelve a ejecutar la Celda 1 "
                               "para reconectarlo y luego la Celda 7.")
        errors, warnings = check_selection(llm_key, clip_provider, voice_provider, cover_provider, keyframe_provider,
                                           music_source, s.get("confirm_paid"), len(own_files))
        for w in warnings:
            run.log(f"⚠️ {re.sub('<[^>]+>', '', w)}")
        if errors:
            raise RuntimeError("No se puede empezar:\n- " + "\n- ".join(re.sub("<[^>]+>", "", e) for e in errors))

        slug = slugify(idea)
        reel_dir = run.reel_dir = os.path.join(REELS_DIR, slug)
        work_dir = os.path.join(WORK_DIR, slug)
        for d in (reel_dir, f"{reel_dir}/images", f"{reel_dir}/clips", work_dir):
            os.makedirs(d, exist_ok=True)
        with open(f"{reel_dir}/settings.json", "w", encoding="utf-8") as f:
            json.dump({k: v for k, v in s.items() if k not in ("clone_sample", "own_files")}, f, indent=2,
                      ensure_ascii=False)
        save_assets(reel_dir, language=language, music_source=music_source, montage=montage)
        run.log(f"Reel folder in Drive: {reel_dir}")

        # --- 1. Script (text) -------------------------------------------------
        run.set("Escribiendo guion", 3)
        run.log(f"Script with {s['llm_provider']} ({s['llm_model']}): {LANGUAGES.get(language, ('?',))[0]}, "
                f"~{s.get('duration', 35)} s, tone {s.get('tone', 'informativo')}...")
        yield run.snapshot()
        plan = generate_plan(idea, llm_key, s["llm_model"], int(s.get("num_scenes", 5)), language,
                             int(s.get("duration", 35)), s.get("tone", "informativo"), s.get("instructions", ""), aspect)
        with open(f"{reel_dir}/plan.json", "w", encoding="utf-8") as f:
            json.dump(plan, f, indent=2, ensure_ascii=False)
        with open(f"{reel_dir}/script.txt", "w", encoding="utf-8") as f:
            f.write(plan["script"])
        run.script = plan["script"]
        scenes = plan["scenes"]
        run.set("Guion listo", 10)
        run.log(f"Script ready: {len(plan['script'].split())} words, {len(scenes)} scenes.")
        yield run.snapshot()

        # --- 2. Publish kit (same LLM; ready to copy while the video renders) ----------
        if s.get("make_kit", True):
            run.set("Kit de publicación", 11)
            run.log("Writing titles, captions, hashtags and hooks for YouTube, TikTok, Instagram and Facebook...")
            yield run.snapshot()
            try:
                run.kit = generate_publish_kit(llm_key, s["llm_model"], idea, plan, language)
                save_publish_kit(run.kit, reel_dir)
                save_assets(reel_dir, hooks=run.kit.get("on_screen_hooks", []))
                score = run.kit.get("virality", {}).get("overall", "?")
                run.log(f"Publish kit ready (viral potential {score}/100). Saved as publish_kit.md in Drive.")
            except Exception as exc:     # the kit is a bonus: never lose the reel because of it
                run.log(f"⚠️ Publish kit failed, the reel continues: {exc}")
            yield run.snapshot()

        # --- 3. Images: cover, first frames for local AI, or the photos themselves -------
        photo_source = clip.get("image_provider")                 # set for "AI photos with motion"
        keyframe_source = keyframe_provider if clip["type"] == "local_ai" and keyframe_provider != "none" else None
        scene_image_source = photo_source or keyframe_source
        if cover_provider != "none" and plan.get("cover_prompt"):
            run.set("Portada", 12)
            run.log(f"Cover with {IMAGE_PROVIDERS[cover_provider]['label']}...")
            yield run.snapshot()
            cover = generate_image(cover_provider, plan["cover_prompt"], f"{reel_dir}/cover.png", aspect)
            run.media.append((cover, "Portada"))
            run.log("Cover saved.")
            yield run.snapshot()
        images = []
        if scene_image_source:
            for i, scene in enumerate(scenes, 1):
                run.set(f"Imagen {i}/{len(scenes)}", 13 + 12 * (i - 1) / len(scenes))
                run.log(f"Scene {i}: image with {IMAGE_PROVIDERS[scene_image_source]['label']}...")
                yield run.snapshot()
                path = generate_image(scene_image_source, scene["image_prompt"],
                                      f"{reel_dir}/images/scene_{i:02}.png", aspect)
                images.append(path)
                run.media.append((path, f"Escena {i}"))
                yield run.snapshot()
        if "flux" in (cover_provider, scene_image_source) and clip["type"] == "local_ai":
            forge_unload()          # free VRAM before ComfyUI loads the video model

        # --- 4. Voice (audio) + the timing of every word ----------------------------
        voice_label = VOICE_PROVIDERS[voice_provider]["label"]
        run.set(f"Voz ({voice_label})", 26)
        run.log(f"Voice with {voice_label}...")
        yield run.snapshot()
        yield from _wait_for(run, lambda: synthesize_voice(plan["script"], voice_provider, s.get("voice", ""),
                                                           s.get("clone_sample"), "", f"{work_dir}/voice.mp3"))
        audio_path, message = run.result
        if not audio_path:
            raise RuntimeError(message)
        voice_copy = f"{reel_dir}/voice{os.path.splitext(audio_path)[1]}"
        shutil.copy(audio_path, voice_copy)
        save_assets(reel_dir, voice=voice_copy)
        duration = media_duration(audio_path) / montage["voice_speed"]
        run.log(f"{message} ({duration:.1f} s at the chosen speed)")
        run.set("Sincronizando subtítulos", 28)
        yield run.snapshot()
        yield from _wait_for(run, lambda: transcribe_words(audio_path, language, plan["script"]))
        with open(f"{reel_dir}/words.json", "w", encoding="utf-8") as f:
            json.dump(run.result, f, ensure_ascii=False)
        run.log(f"Word timings ready ({len(run.result)} words).")
        yield run.snapshot()

        # --- 5. Clips (video) ---------------------------------------------------------
        clips = []
        if clip["type"] == "stock":
            search, orientation = STOCK_SEARCH[clip_provider], ORIENTATIONS[aspect]
            used = set()
            for i, scene in enumerate(scenes, 1):
                run.set(f"Clip {i}/{len(scenes)}", 32 + 38 * (i - 1) / len(scenes))
                query = scene.get("search_query") or idea
                urls = [u for u in (search(query, orientation) or search(idea, orientation)) if u not in used]
                if not urls:
                    raise RuntimeError(f"{clip['label']} no encontró video para «{query}».")
                used.add(urls[0])
                path = download_file(urls[0], f"{reel_dir}/clips/stock_{i:02}.mp4")
                clips.append(path)
                run.media.append((path, f"Clip {i}: {query}"))
                run.log(f"Clip {i} from {clip['label']}: '{query}'")
                yield run.snapshot()
        elif clip_provider == "upload":
            for i, src in enumerate(own_files, 1):
                path = f"{reel_dir}/clips/own_{i:02}{os.path.splitext(src)[1].lower()}"
                shutil.copy(src, path)
                clips.append(path)
                run.media.append((path, f"Tu clip {i}"))
            run.log(f"{len(clips)} of your files copied to the reel folder.")
        elif clip_provider == "footage":
            clips = footage_files()
            run.media += [(p, os.path.basename(p)) for p in clips[:12]]
            run.log(f"{len(clips)} files from {FOOTAGE_DIR}.")
        elif clip["type"] == "local_ai":
            mode = "image-to-video" if images else "text-to-video"
            width, height = int(s.get("ai_width", 480)), int(s.get("ai_height", 832))
            if aspect == "16:9" and width < height or aspect == "9:16" and width > height:
                width, height = height, width
            if aspect == "1:1":
                width = height = min(width, height)
            for i, scene in enumerate(scenes, 1):
                run.set(f"Clip IA {i}/{len(scenes)}", 32 + 38 * (i - 1) / len(scenes))
                run.log(f"Scene {i}: {clip['label']} {mode} ({width}x{height}, {s.get('ai_frames', 49)} frames)...")
                yield run.snapshot()
                path = f"{reel_dir}/clips/scene_{i:02}.mp4"
                yield from _wait_for(run, lambda: comfy_generate_video(
                    clip_provider, images[i - 1] if images else None, scene["motion_prompt"], path, width, height,
                    s.get("ai_frames", 49), s.get("ai_steps", 20), on_raw=run.tech))
                clips.append(path)
                run.media.append((path, f"Clip {i}"))
                run.log(f"Scene {i}: clip ready.")
                yield run.snapshot()
            comfy_free()
        elif clip["type"] == "external_ai":
            seconds = duration / len(scenes)
            for i, scene in enumerate(scenes, 1):
                run.set(f"Clip IA {i}/{len(scenes)} ({clip['label']})", 32 + 38 * (i - 1) / len(scenes))
                run.log(f"Scene {i}: {clip['label']} text-to-video (paid)...")
                yield run.snapshot()
                path = f"{reel_dir}/clips/ai_{i:02}.mp4"
                prompt = f"{scene['image_prompt']}. {scene['motion_prompt']}"
                yield from _wait_for(run, lambda: generate_ai_video(clip_provider, prompt, seconds, aspect, path,
                                                                    on_raw=run.tech))
                clips.append(path)
                run.media.append((path, f"Clip {i}"))
                run.log(f"Scene {i}: clip ready.")
                yield run.snapshot()
        else:                                                      # AI photos with motion
            clips = images
        save_assets(reel_dir, clips=clips)
        run.set("Clips listos", 70)
        yield run.snapshot()

        # --- 6. Music -----------------------------------------------------------------
        song = None
        if music_source == "library" and music_files():
            song = random.choice(music_files())
            run.log(f"Background music: {os.path.basename(song)}")
        elif music_source == "elevenlabs":
            run.set("Música IA (ElevenLabs)", 71)
            run.log(f"Composing an instrumental with ElevenLabs Music: {plan.get('music_prompt', '')[:120]}")
            yield run.snapshot()
            target = f"{MUSIC_AI_DIR}/{slug}.mp3"
            try:
                yield from _wait_for(run, lambda: elevenlabs_music(plan.get("music_prompt", ""), duration + 2, target))
                song = target
                run.log(f"Music saved for reuse: {target}")
            except Exception as exc:          # music is a bonus: never lose the reel because of it
                run.log(f"⚠️ AI music failed, the reel continues without music: {exc}")
                run.tech(str(exc))
        save_assets(reel_dir, music=song or "")
        yield run.snapshot()

        # --- 7. Montage ----------------------------------------------------------------
        yield from assemble_reel(run, reel_dir, montage, pct_start=72)
        run.done = True
        run.set("Listo", 100)
        run.log(f"DONE. {len(run.videos)} video(s) saved in Drive: {reel_dir}")
        yield run.snapshot()
    except Exception as exc:
        run.fail(exc)
        yield run.snapshot()


def reassemble_reel(reel_dir, montage):
    """Generator: a new montage of a saved reel (other format, subtitles, cuts, music volume...) without
    generating script, voice or clips again."""
    run = ReelRun()
    run.reel_dir = reel_dir
    try:
        if not reel_dir or not os.path.isdir(reel_dir):
            raise RuntimeError("Elige un reel primero.")
        run.kit = load_publish_kit(reel_dir)
        try:
            with open(f"{reel_dir}/script.txt", encoding="utf-8") as f:
                run.script = f.read()
        except Exception:
            pass
        run.log(f"Re-assembling {os.path.basename(reel_dir)} from its saved files...")
        yield from assemble_reel(run, reel_dir, montage, pct_start=5)
        run.done = True
        run.set("Listo", 100)
        run.log("DONE.")
        yield run.snapshot()
    except Exception as exc:
        run.fail(exc)
        yield run.snapshot()
