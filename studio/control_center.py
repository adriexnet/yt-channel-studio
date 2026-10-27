"""Cell 7: the Control Center. Pick every provider, see in red what is missing, test and save the API keys,
follow each reel live (it keeps running if the page disconnects), resume a failed reel from the failed step,
reuse the settings of any reel or preset, re-assemble saved reels and publish them."""
import html
import gradio as gr
from .lib import *
from .pipeline import *
from . import jobs

# ---------------------------------------------------------------------------
# Remembered choices (Drive), so the form opens the way you left it
# ---------------------------------------------------------------------------
PREFS_FILE = f"{CONFIG_DIR}/control_center_prefs.json"
FONTS = ["DejaVu Sans"]      # refreshed from Drive/fonts every time the UI is built
DEFAULT_PREFS = {
    "language": "es", "duration": 35, "tone": "informativo", "instructions": "", "num_scenes": 5,
    "llm_provider": "OpenAI (ChatGPT)", "llm_model": "",
    "clip_type": "stock", "clip_provider": "pexels", "keyframe_provider": "flux",
    "voice_type": "local", "voice_provider": "edge", "voice": "es-MX-JorgeNeural",
    "cover_provider": "none", "music_source": "library", "make_kit": True,
    "ai_width": 480, "ai_height": 832, "ai_frames": 49, "ai_steps": 20,
    "montage": dict(DEFAULT_MONTAGE),
}


def read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def load_prefs():
    return normalize_settings(read_json(PREFS_FILE))


def normalize_settings(saved):
    """Defaults + saved values (form prefs, a preset or a reel's settings.json), never a choice that no
    longer exists. Also used to fill the form when settings are loaded."""
    prefs = json.loads(json.dumps(DEFAULT_PREFS))
    prefs.update({k: v for k, v in (saved or {}).items() if k in DEFAULT_PREFS and k != "montage" and v is not None})
    prefs["montage"].update((saved or {}).get("montage") or {})
    prefs["idea"] = (saved or {}).get("idea", "")
    prefs["montage"] = montage_options(prefs["montage"])
    if prefs["montage"]["font"] not in FONTS:
        prefs["montage"]["font"] = "Anton" if "Anton" in FONTS else FONTS[0]
    # Never reopen with a choice that no longer exists.
    if prefs["llm_provider"] not in llm_provider_choices():
        prefs["llm_provider"] = DEFAULT_PREFS["llm_provider"]
    prefs["llm_type"] = llm_kind(prefs["llm_provider"])
    if prefs["clip_provider"] not in CLIP_PROVIDERS:
        prefs["clip_provider"] = "pexels"
    prefs["clip_type"] = CLIP_PROVIDERS[prefs["clip_provider"]]["type"]
    if prefs["voice_provider"] not in VOICE_PROVIDERS:
        prefs.update(voice_provider="edge", voice="es-MX-JorgeNeural")
    prefs["voice_type"] = VOICE_PROVIDERS[prefs["voice_provider"]]["type"]
    for key, allowed in (("cover_provider", IMAGE_PROVIDERS), ("keyframe_provider", IMAGE_PROVIDERS),
                         ("music_source", MUSIC_SOURCES), ("language", LANGUAGES), ("tone", TONES)):
        if prefs[key] not in allowed:
            prefs[key] = DEFAULT_PREFS[key]
    return prefs


def save_prefs(settings):
    keep = {k: settings[k] for k in DEFAULT_PREFS if k in settings}
    try:
        with open(PREFS_FILE, "w", encoding="utf-8") as f:
            json.dump(keep, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------
def progress_bar_html(stage="Esperando", pct=0, failed=False, done=False):
    color = "#dc2626" if failed else ("#16a34a" if done else "#4f46e5")
    icon = "❌" if failed else ("✅" if done else "⏳")
    return (f"<div style='font-family:sans-serif;padding:4px 0'>"
            f"<div style='display:flex;justify-content:space-between;font-weight:600;margin-bottom:6px'>"
            f"<span>{icon} {html.escape(stage)}</span><span>{pct}%</span></div>"
            f"<div style='background:rgba(127,127,127,.25);border-radius:8px;height:14px;overflow:hidden'>"
            f"<div style='width:{pct}%;background:{color};height:100%;transition:width .4s'></div></div></div>")


def status_html(errors, warnings):
    if errors:
        items = "".join(f"<li>{e}</li>" for e in errors)
        box = (f"<div style='border:1px solid #dc2626;background:rgba(220,38,38,.12);color:#ef4444;"
               f"padding:10px 14px;border-radius:8px'><b>⛔ Falta algo antes de producir:</b>"
               f"<ul style='margin:6px 0 0 18px'>{items}</ul></div>")
    else:
        box = ("<div style='border:1px solid #16a34a;background:rgba(22,163,74,.12);color:#22c55e;"
               "padding:10px 14px;border-radius:8px'><b>✅ Todo listo para producir.</b></div>")
    if warnings:
        items = "".join(f"<li>{w}</li>" for w in warnings)
        box += (f"<div style='border:1px solid #d97706;background:rgba(217,119,6,.12);color:#f59e0b;"
                f"padding:8px 14px;border-radius:8px;margin-top:6px'><ul style='margin:0 0 0 18px'>{items}</ul></div>")
    return box


def message_html(ok, message):
    icon = {True: "✅", False: "❌"}.get(ok, "⚠️")
    color = {True: "#22c55e", False: "#ef4444"}.get(ok, "#f59e0b")
    return f"<span style='color:{color};font-weight:600'>{icon} {html.escape(message)}</span>"


def llm_key_for(llm_name):
    return llm_provider_choices().get(llm_name, "openai")


def validate(llm_name, clip_provider, voice_provider, cover_provider, keyframe_provider, music_source,
             confirm_paid, own_files):
    errors, warnings = check_selection(llm_key_for(llm_name), clip_provider, voice_provider, cover_provider,
                                       keyframe_provider, music_source, confirm_paid, len(own_files or []))
    return status_html(errors, warnings)


# ---------------------------------------------------------------------------
# Cascading pickers
# ---------------------------------------------------------------------------
def on_llm_type_change(llm_type):
    names = llm_names_by_kind(llm_type)
    return gr.update(choices=names, value=names[0] if names else None)


def on_llm_change(llm_name):
    if not llm_name:
        return gr.update()
    key = llm_key_for(llm_name)
    if key == "ollama":
        models = list_ollama_models()
        return gr.update(choices=models, value=models[0] if models else "")
    model = default_model_for(key)
    return gr.update(choices=[model] if model else [], value=model)


def on_clip_type_change(clip_type):
    choices = clip_provider_choices(clip_type)
    return gr.update(choices=choices, value=choices[0][1])


def on_clip_change(clip_provider):
    """Paid confirmation only for external AI; base image and settings only for local AI (Wan/LTX accept a
    first frame); the upload box only for "files I upload"; the folder hint only for Drive/footage."""
    kind = CLIP_PROVIDERS[clip_provider]["type"]
    found = len(footage_files()) if clip_provider == "footage" else 0
    return (gr.update(visible=kind == "external_ai", value=False), gr.update(visible=kind == "local_ai"),
            gr.update(visible=kind == "local_ai"), gr.update(visible=clip_provider == "upload"),
            gr.update(visible=clip_provider == "footage",
                      value=f"📂 `{FOOTAGE_DIR}` · {found} archivo(s). Se usan en orden de nombre."))


def on_voice_type_change(voice_type):
    choices = voice_provider_choices(voice_type)
    return gr.update(choices=choices, value=choices[0][1])


def on_voice_provider_change(voice_provider):
    choices = voice_choices(voice_provider)
    first = choices[0][1] if choices else ""
    return gr.update(choices=choices, value=first), gr.update(visible=voice_provider == "f5")


# ---------------------------------------------------------------------------
# Montage controls (built twice: for new reels and for "Volver a montar")
# ---------------------------------------------------------------------------
MONTAGE_KEYS = list(DEFAULT_MONTAGE)


def build_montage_controls(m):
    """Returns the components in MONTAGE_KEYS order."""
    c = {}
    with gr.Row():
        c["aspect"] = gr.Radio([("9:16 vertical", "9:16"), ("16:9 horizontal", "16:9"), ("1:1 cuadrado", "1:1")],
                               value=m["aspect"], label="Formato")
        c["fit"] = gr.Radio([(v, k) for k, v in FITS.items()], value=m["fit"], label="Clips con otra forma")
    with gr.Row():
        c["pace"] = gr.Dropdown([(v, k) for k, v in PACES.items()], value=m["pace"], label="Ritmo de cortes")
        c["transition"] = gr.Dropdown([(v, k) for k, v in TRANSITIONS.items()], value=m["transition"],
                                      label="Transición")
    with gr.Row():
        c["transition_duration"] = gr.Slider(0.1, 1.0, value=m["transition_duration"], step=0.05,
                                             label="Duración de la transición (s)")
        c["clip_speed"] = gr.Slider(0.5, 2.0, value=m["clip_speed"], step=0.05, label="Velocidad de los clips")
    with gr.Row():
        c["voice_speed"] = gr.Slider(0.75, 1.5, value=m["voice_speed"], step=0.05, label="Velocidad de la voz")
        c["voice_volume"] = gr.Slider(0.5, 2.0, value=m["voice_volume"], step=0.05, label="Volumen de la voz")
    with gr.Row():
        c["music_volume"] = gr.Slider(0.0, 0.5, value=m["music_volume"], step=0.01, label="Volumen de la música")
        c["ducking"] = gr.Checkbox(value=m["ducking"], label="Bajar la música cuando habla la voz")
    with gr.Row():
        c["subtitle_style"] = gr.Radio([(v, k) for k, v in SUBTITLE_STYLES.items()], value=m["subtitle_style"],
                                       label="Subtítulos")
        c["subtitle_position"] = gr.Radio([(v, k) for k, v in SUBTITLE_POSITIONS.items()],
                                          value=m["subtitle_position"], label="Posición")
    with gr.Row():
        c["font"] = gr.Dropdown(FONTS, value=m["font"] if m["font"] in FONTS else FONTS[0], allow_custom_value=True,
                                label="Fuente (agrega las tuyas en Drive/fonts)")
        c["font_size"] = gr.Slider(40, 160, value=m["font_size"], step=2, label="Tamaño de letra")
    with gr.Row():
        c["words_per_line"] = gr.Slider(1, 6, value=m["words_per_line"], step=1, label="Palabras por línea")
        with gr.Column():
            c["uppercase"] = gr.Checkbox(value=m["uppercase"], label="MAYÚSCULAS")
            c["box"] = gr.Checkbox(value=m["box"], label="Caja detrás del texto")
    with gr.Row():
        c["text_color"] = gr.ColorPicker(value=m["text_color"], label="Texto")
        c["highlight_color"] = gr.ColorPicker(value=m["highlight_color"], label="Resaltado y gancho")
        c["outline_color"] = gr.ColorPicker(value=m["outline_color"], label="Borde o caja")
    with gr.Row():
        c["hook_overlay"] = gr.Checkbox(value=m["hook_overlay"], label="🪝 Gancho en pantalla al inicio")
        c["hook_seconds"] = gr.Slider(1, 5, value=m["hook_seconds"], step=0.5, label="Segundos del gancho")
    c["variants"] = gr.Slider(1, 3, value=m["variants"], step=1,
                              label="Variantes (cada una con otro orden de clips, otro gancho y otra canción)")
    return [c[k] for k in MONTAGE_KEYS]


# ---------------------------------------------------------------------------
# Publish kit panel (one per tab that shows a kit)
# ---------------------------------------------------------------------------
def copy_kwargs():
    """Copy button on text fields (Gradio 6)."""
    return {"buttons": ["copy"]}


KIT_FIELDS = [   # (label, kit path, hard limit, characters visible before "more")
    ("YouTube · Título", ("youtube", "title"), 100, None),
    ("YouTube · Descripción (con hashtags)", ("youtube", "description"), 5000, 100),
    ("YouTube · Etiquetas (campo Tags)", ("youtube", "tags"), 500, None),
    ("TikTok · Caption", ("tiktok", "caption"), 4000, 80),
    ("Instagram · Caption", ("instagram", "caption"), 2200, 125),
    ("Facebook · Caption", ("facebook", "caption"), 2200, 125),
]


def field_label(label, text, limit, visible):
    n = len(text or "")
    extra = f" · visibles ~{visible}" if visible else ""
    return f"{label} ({n}/{limit}{extra})" + (" ⚠️ excede el límite" if n > limit else "")


def kit_extras_markdown(kit):
    if not kit:
        return ""
    v = kit.get("virality", {})

    def score(key):
        return v.get(key, "?")
    yt = kit["youtube"]
    parts = [f"### 📊 Potencial viral: {score('overall')}/100",
             f"{v.get('verdict', '')}",
             f"| Gancho | Retención | Compartible | Comentable | Nicho | Emoción |\n|---|---|---|---|---|---|\n"
             f"| {score('hook')} | {score('retention')} | {score('shareability')} | {score('commentability')} "
             f"| {score('niche_fit')} | {score('emotional')} |",
             f"**Punto fuerte:** {v.get('strongest', '')}  \n**Punto débil:** {v.get('weakest', '')}  \n"
             f"**Recomendación:** {v.get('recommendation', '')}",
             "### 🪝 Texto en pantalla (primeros 2 s)", "\n".join(f"- {h}" for h in kit["on_screen_hooks"]),
             "### 🎬 Variantes de título (YouTube)", "\n".join(f"- {t}" for t in yt["title_variants"]),
             "### 💬 Comentarios semilla (el primero se publica como primer comentario si lo eliges)",
             "\n".join(f"{i}. {c}" for i, c in enumerate(kit["seed_comments"], 1)),
             f"### ⚡ Teaser para historias\n{kit['teaser']}"]
    return "\n\n".join(p for p in parts if p)


def kit_updates(kit):
    """Values + live character counts for the kit panel."""
    updates = []
    for label, (platform, key), limit, visible in KIT_FIELDS:
        text = kit[platform][key] if kit else ""
        updates.append(gr.update(value=text, label=field_label(label, text, limit, visible)))
    updates.append(gr.update(value=kit_extras_markdown(kit)))
    return updates


def build_kit_panel():
    """Tabs with one copyable field per platform; returns the 7 components in KIT_FIELDS order + extras."""
    comps = {}
    with gr.Tabs():
        with gr.Tab("▶️ YouTube"):
            for i in (0, 1, 2):
                label, _, limit, visible = KIT_FIELDS[i]
                comps[i] = gr.Textbox(label=field_label(label, "", limit, visible), lines=1 if i != 1 else 6,
                                      interactive=True, **copy_kwargs())
        for i, tab in ((3, "🎵 TikTok"), (4, "📸 Instagram"), (5, "📘 Facebook")):
            with gr.Tab(tab):
                label, _, limit, visible = KIT_FIELDS[i]
                comps[i] = gr.Textbox(label=field_label(label, "", limit, visible), lines=6,
                                      interactive=True, **copy_kwargs())
        with gr.Tab("📊 Viralidad y extras"):
            comps[6] = gr.Markdown()
    return [comps[i] for i in range(7)]


# ---------------------------------------------------------------------------
# Publishing panel (Upload-Post). Uses the kit texts as they are in the fields, edits included.
# ---------------------------------------------------------------------------
def build_publish_panel():
    with gr.Row():
        platforms = gr.CheckboxGroup([(v, k) for k, v in PUBLISH_PLATFORMS.items()], value=["tiktok", "instagram",
                                                                                        "youtube", "facebook"],
                                     label="Plataformas")
    with gr.Row():
        schedule = gr.Textbox(label="Programar (vacío = ahora)", placeholder="2026-11-02 18:30")
        timezone = gr.Textbox(label="Zona horaria", value="America/Costa_Rica")
        yt_privacy = gr.Dropdown([(v, k) for k, v in YOUTUBE_PRIVACY.items()], value="public", label="YouTube")
        tt_privacy = gr.Dropdown([(v, k) for k, v in TIKTOK_PRIVACY.items()], value="PUBLIC_TO_EVERYONE",
                                 label="TikTok")
    first_comment = gr.Checkbox(value=True, label="💬 Publicar el primer comentario semilla (Instagram, YouTube y Facebook)")
    button = gr.Button("📤 Publicar este reel", variant="primary")
    result = gr.Markdown()
    return button, [platforms, schedule, timezone, yt_privacy, tt_privacy, first_comment], result


def reel_folder(ref):
    """The reel folder from either the folder itself or a video inside it."""
    if not ref:
        return None
    return ref if os.path.isdir(ref) else os.path.dirname(ref)


def publish_ui(ref, platforms, schedule, timezone, yt_privacy, tt_privacy, first_comment, *kit_texts):
    folder = reel_folder(ref)
    if not folder:
        yield "Primero produce o elige un reel."
        return
    kit = load_publish_kit(folder)
    if kit:                                         # the fields win: they may have been edited by hand
        for (_, (platform, key), _, _), text in zip(KIT_FIELDS, kit_texts):
            if text:
                kit[platform][key] = text
    video = f"{folder}/{os.path.basename(folder)}_FINAL.mp4"
    names = ", ".join(PUBLISH_PLATFORMS[p] for p in platforms or [])
    yield f"⏳ Subiendo el video a Upload-Post para {names}..."
    try:
        body = publish_reel(video, kit, platforms or [], schedule=schedule, timezone=timezone,
                            youtube_privacy=yt_privacy, tiktok_privacy=tt_privacy, first_comment=first_comment)
    except Exception as exc:
        yield f"❌ {html.escape(str(exc))}"
        return
    if body.get("job_id") or (schedule or "").strip():
        yield f"🗓️ Programado ({schedule} {timezone}). ID: `{body.get('job_id', body.get('request_id', '?'))}`"
        return
    request_id = body.get("request_id")
    if body.get("results") or not request_id:
        yield "\n\n".join(describe_publish(body)) or f"Respuesta: `{json.dumps(body)[:500]}`"
        return
    started = time.time()
    while time.time() - started < 900:              # each platform finishes on its own time
        time.sleep(10)
        try:
            status = publish_status(request_id)
        except Exception as exc:
            yield f"⏳ Publicando (ID `{request_id}`)... no se pudo consultar el estado: {html.escape(str(exc))}"
            continue
        state = str(status.get("status", "")).lower()
        if state == "completed" or status.get("results") and state not in ("pending", "queued", "processing",
                                                                            "in_progress"):
            yield "\n\n".join(describe_publish(status)) or f"Estado: `{json.dumps(status)[:500]}`"
            return
        if state == "failed":
            yield f"❌ Upload-Post: {html.escape(str(status.get('message') or status.get('error') or status))}"
            return
        yield f"⏳ Publicando en {names}... ({int(time.time() - started)} s, estado: {state or 'en curso'})"
    yield f"⏳ Sigue en proceso. Revisa el estado en app.upload-post.com con el ID `{request_id}`."


# ---------------------------------------------------------------------------
# Production
# ---------------------------------------------------------------------------
PRODUCE_KEYS = ["idea", "language", "duration", "tone", "instructions", "num_scenes", "llm_provider", "llm_model",
                "clip_type", "clip_provider", "confirm_paid", "own_files", "keyframe_provider", "voice_type",
                "voice_provider", "voice", "clone_sample", "cover_provider", "music_source", "make_kit",
                "ai_width", "ai_height", "ai_frames", "ai_steps"]


def gallery_items(media):
    return [(path, caption) for path, caption in media if os.path.exists(path)]


def form_settings(values):
    """The production form (PRODUCE_KEYS + montage controls, in that order) as a settings dict."""
    settings = dict(zip(PRODUCE_KEYS, values[:len(PRODUCE_KEYS)]))
    settings["montage"] = montage_options(dict(zip(MONTAGE_KEYS, values[len(PRODUCE_KEYS):])))
    for key in ("duration", "num_scenes", "ai_width", "ai_height", "ai_frames", "ai_steps"):
        settings[key] = int(settings[key] or DEFAULT_PREFS[key])
    return settings


def snapshot_outputs(snap, shown):
    """One snapshot -> the 15 production outputs. `shown` remembers the kit already on screen."""
    outputs = (progress_bar_html(snap["stage"], snap["pct"], snap["failed"], snap["done"]),
               snap["script"], gallery_items(snap["media"]), snap["log"], snap["video"], snap["videos"] or None)
    if snap["kit"] is not shown.get("kit", "unset"):          # only refresh the kit fields when the kit changes
        shown["kit"] = snap["kit"]
        kit_part = tuple(kit_updates(snap["kit"]))
    else:
        kit_part = tuple(gr.update() for _ in range(7))
    return outputs + kit_part + (snap["tech"], snap["reel_dir"] or gr.update())


def no_change(message=None, failed=False):
    """Production outputs that only show a message in the progress bar."""
    bar = progress_bar_html(message, 0, failed=failed) if message else gr.update()
    return (bar,) + tuple(gr.update() for _ in range(14))


def follow_ui(job):
    shown = {}
    for snap in jobs.follow(job):
        yield snapshot_outputs(snap, shown)


def start_job_ui(kind, label, factory):
    try:
        job = jobs.start(kind, label, factory)
    except RuntimeError as exc:
        yield no_change(str(exc), failed=True)
        return
    yield from follow_ui(job)


def produce_ui(*values):
    # Instant feedback: if even this does not show up, the problem is the connection to the UI, not the pipeline.
    yield (progress_bar_html("Iniciando…", 0), "", [], f"Starting... (live copy of this log: {ReelRun.LOG_FILE})",
           None, None) + tuple(gr.update() for _ in range(7)) + ("", gr.update())
    settings = form_settings(values)
    if drive_responds():                       # never block the run on a stale Drive just to remember choices
        save_prefs(settings)
    yield from start_job_ui("produce", settings["idea"][:60], lambda: produce_reel(settings))


def resume_ui(reel_dir, *values):
    """Continues the selected reel with the form's settings: finished steps are reused, nothing is lost."""
    if not reel_dir or not os.path.isdir(reel_dir):
        yield no_change("No hay un reel para reanudar: elige uno en «📁 Mis reels» → «♻️ Usar estos ajustes».",
                        failed=True)
        return
    settings = form_settings(values)
    settings["idea"] = load_settings(reel_dir).get("idea") or settings["idea"] or os.path.basename(reel_dir)
    yield from start_job_ui("produce", f"{os.path.basename(reel_dir)} (reanudado)",
                            lambda: produce_reel(settings, resume_dir=reel_dir))


def attach_ui():
    """Shows the production running in the background (or the last one of this session)."""
    job = jobs.current()
    if job is None:
        yield no_change()
        return
    yield from follow_ui(job)


def stop_ui():
    job = jobs.current()
    if job is None or not job.running:
        return progress_bar_html("No hay nada en curso", 0)
    job.cancel()
    return progress_bar_html("Deteniendo… (termina el paso que está en curso)", 0, failed=True)


# ---------------------------------------------------------------------------
# Reusable settings: presets (Drive/config/presets) and the settings.json of every reel
# ---------------------------------------------------------------------------
PRESETS_DIR = f"{CONFIG_DIR}/presets"


def list_presets():
    return sorted(f[:-5] for f in os.listdir(PRESETS_DIR) if f.endswith(".json")) if os.path.isdir(PRESETS_DIR) else []


def save_preset(name, settings):
    name = re.sub(r"\s+", " ", re.sub(r"[^\w .-]+", "", name or "", flags=re.UNICODE)).strip()
    if not name:
        return "Escribe un nombre para el preset.", gr.update()
    os.makedirs(PRESETS_DIR, exist_ok=True)
    data = {k: v for k, v in settings.items() if k not in SETTINGS_SKIPPED and not k.startswith("_")}
    data.pop("confirm_paid", None)                 # a preset never pre-approves a charge
    with open(f"{PRESETS_DIR}/{name}.json", "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return f"✅ Preset «{name}» guardado en Drive.", gr.update(choices=list_presets(), value=name)


def save_preset_ui(name, *values):
    return save_preset(name, form_settings(values))


def settings_updates(saved, keep_confirmation=False):
    """Updates for every production input (PRODUCE_KEYS + montage, in order) plus the AI-settings box and the
    footage note, so the whole form shows these settings. Programmatic updates do not fire the cascading
    pickers (they listen to user input only), so nothing here is overwritten afterwards."""
    p = normalize_settings(saved)
    kind = CLIP_PROVIDERS[p["clip_provider"]]["type"]
    model = p["llm_model"] or default_model_for(llm_key_for(p["llm_provider"]))
    voices = voice_choices(p["voice_provider"])
    if p["voice"] and p["voice"] not in [v for _, v in voices]:
        voices = [(p["voice"], p["voice"])] + voices
    values = {
        "idea": gr.update(value=p["idea"]), "language": gr.update(value=p["language"]),
        "duration": gr.update(value=p["duration"]), "tone": gr.update(value=p["tone"]),
        "instructions": gr.update(value=p["instructions"]), "num_scenes": gr.update(value=p["num_scenes"]),
        "llm_provider": gr.update(choices=llm_names_by_kind(p["llm_type"]), value=p["llm_provider"]),
        "llm_model": gr.update(choices=[model] if model else [], value=model),
        "clip_type": gr.update(value=kind),
        "clip_provider": gr.update(choices=clip_provider_choices(kind), value=p["clip_provider"]),
        "confirm_paid": gr.update(visible=kind == "external_ai",
                                  value=bool(keep_confirmation and (saved or {}).get("confirm_paid"))),
        "own_files": gr.update(visible=p["clip_provider"] == "upload", value=None),
        "keyframe_provider": gr.update(visible=kind == "local_ai", value=p["keyframe_provider"]),
        "voice_type": gr.update(value=p["voice_type"]),
        "voice_provider": gr.update(choices=voice_provider_choices(p["voice_type"]), value=p["voice_provider"]),
        "voice": gr.update(choices=voices, value=p["voice"] or (voices[0][1] if voices else "")),
        "clone_sample": gr.update(visible=p["voice_provider"] == "f5", value=None),
        "cover_provider": gr.update(value=p["cover_provider"]), "music_source": gr.update(value=p["music_source"]),
        "make_kit": gr.update(value=p["make_kit"]),
        "ai_width": gr.update(value=p["ai_width"]), "ai_height": gr.update(value=p["ai_height"]),
        "ai_frames": gr.update(value=p["ai_frames"]), "ai_steps": gr.update(value=p["ai_steps"]),
    }
    updates = [values[k] for k in PRODUCE_KEYS] + [gr.update(value=p["montage"][k]) for k in MONTAGE_KEYS]
    footage = f"📂 `{FOOTAGE_DIR}` · {len(footage_files())} archivo(s). Se usan en orden de nombre."
    return updates + [gr.update(visible=kind == "local_ai"),
                      gr.update(visible=p["clip_provider"] == "footage", value=footage)]


def load_preset_ui(name):
    if not name or name not in list_presets():
        return [gr.update() for _ in range(len(PRODUCE_KEYS) + len(MONTAGE_KEYS) + 2)] + \
               ["Elige un preset guardado.", gr.update()]
    data = read_json(f"{PRESETS_DIR}/{name}.json")
    return settings_updates(data) + [f"✅ Ajustes del preset «{name}» cargados.", gr.update()]


def use_reel_settings_ui(folder):
    """Loads a reel's settings into the production form and points «Reanudar» at that reel."""
    if not folder or not os.path.isdir(folder):
        return [gr.update() for _ in range(len(PRODUCE_KEYS) + len(MONTAGE_KEYS) + 2)] + \
               ["Elige un reel primero.", gr.update(), gr.update()]
    saved = load_settings(folder)
    if not saved:
        return [gr.update() for _ in range(len(PRODUCE_KEYS) + len(MONTAGE_KEYS) + 2)] + \
               ["Este reel no tiene ajustes guardados.", gr.update(), gr.update()]
    return settings_updates(saved) + [f"✅ Ajustes de «{os.path.basename(folder)}» cargados en «🎬 Producir reel».",
                                      folder, gr.Tabs(selected="produce")]


# ---------------------------------------------------------------------------
# API keys tab
# ---------------------------------------------------------------------------
KEY_COMPONENTS = []      # (section, field, component) in the order they are passed to save_all_keys
KEY_SHOWN = {}           # (section, field) -> value the form currently shows as saved


def save_all_keys(*values):
    """Write only the fields you edited, so a key changed elsewhere meanwhile is never overwritten."""
    keys = ensure_api_keys_file()
    changed = []
    for (section, field, _), value in zip(KEY_COMPONENTS, values):
        value = (value or "").strip()
        if value == KEY_SHOWN.get((section, field), ""):
            continue
        keys.setdefault(section, {})[field] = value
        KEY_SHOWN[(section, field)] = value
        changed.append(f"{CREDENTIALS[section]['label']} · {FIELD_LABELS.get(field, field)}")
    if not changed:
        return "No había cambios que guardar."
    save_api_keys(keys)
    return f"✅ Guardado en Drive ({len(changed)} campo(s)): {', '.join(changed)}"


def make_test_handler(section):
    fields = CREDENTIALS[section]["fields"]

    def handler(*values):
        return message_html(*test_credential(section, dict(zip(fields, values))))
    return handler


def test_compat_ui(base_url, api_key):
    return message_html(*test_compat(base_url or "", api_key or ""))


def load_compat_ui(name):
    """Fill the fields when an EXISTING service is picked; leave them untouched for a new name being typed."""
    name = (name or "").strip()
    if name not in compat_provider_names():
        return gr.update(), gr.update(), gr.update()
    return get_compat(name, "api_key"), get_compat(name, "base_url"), get_compat(name, "chat_model")


def save_compat_ui(name, api_key, base_url, chat_model):
    name = re.sub(r"[^a-z0-9_-]+", "-", (name or "").strip().lower()).strip("-")
    if not name:
        return "Escribe un nombre para el servicio (por ejemplo groq, openrouter, together).", gr.update(), gr.update()
    if not base_url.strip():
        return "La Base URL es obligatoria (por ejemplo https://api.groq.com/openai/v1).", gr.update(), gr.update()
    keys = ensure_api_keys_file()
    keys["openai_compatible"][name] = {"api_key": api_key.strip(), "base_url": base_url.strip(),
                                       "chat_model": chat_model.strip()}
    save_api_keys(keys)
    return (f"✅ Guardado [openai_compatible.{name}] en Drive.", gr.update(value="external"),
            gr.update(choices=llm_names_by_kind("external"), value=f"{name} (OpenAI-compatible)"))


# ---------------------------------------------------------------------------
# Reels tab
# ---------------------------------------------------------------------------
def list_reels():
    """Every reel folder, newest first, with how its last run ended (finished, failed, stopped...)."""
    folders = [d for d in glob.glob(f"{REELS_DIR}/*") if os.path.isdir(d)]
    folders.sort(key=os.path.getmtime, reverse=True)
    running = jobs.current()
    out = []
    for folder in folders:
        state = read_run_state(folder)
        status = state.get("status", "failed")
        if status == "running" and not (running and running.running and running.snapshot
                                         and running.snapshot.get("reel_dir") == folder):
            status = "stopped"                             # the session that was producing it ended
        label = RUN_STATES.get(status, status)
        stage = f" · {state['stage']}" if status in ("failed", "stopped") and state.get("stage") else ""
        out.append((f"{label} · {os.path.basename(folder)}{stage}", folder))
    return out


def final_video(folder):
    path = f"{folder}/{os.path.basename(folder)}_FINAL.mp4"
    return path if os.path.exists(path) else None


def show_reel(folder):
    if not folder or not os.path.isdir(folder):
        return (None, "", None, "", None) + tuple(kit_updates(None))
    script = ""
    if os.path.exists(f"{folder}/script.txt"):
        with open(f"{folder}/script.txt", encoding="utf-8") as f:
            script = f.read()
    cover = f"{folder}/cover.png" if os.path.exists(f"{folder}/cover.png") else None
    kit = load_publish_kit(folder)
    state = read_run_state(folder)
    notes = []
    if state.get("status") in ("failed", "stopped", "running"):
        notes.append(f"**{RUN_STATES.get(state['status'], state['status'])}** en «{state.get('stage', '?')}»"
                     + (f": {state['error']}" if state.get("error") else "")
                     + ". Pulsa **🔁 Reanudar** para seguir desde ahí sin repetir lo ya hecho.")
    variants = len(glob.glob(f"{folder}/*_FINAL*.mp4"))
    if variants > 1:
        notes.append(f"{variants} variantes en la carpeta del reel.")
    if not kit:
        notes.append("Este reel no tiene kit todavía: usa «Regenerar kit».")
    return (final_video(folder), script, cover, "  \n".join(notes), load_settings(folder) or None) + \
        tuple(kit_updates(kit))


def resume_reel_ui(folder):
    """Continues a reel with the settings it was made with."""
    saved = load_settings(folder) if folder else {}
    if not saved:
        yield progress_bar_html("Este reel no tiene ajustes guardados: usa «♻️ Usar estos ajustes».", 0, failed=True), \
            "", gr.update()
        return
    settings = normalize_settings(saved)
    settings["confirm_paid"] = bool(saved.get("confirm_paid"))
    for snap_outputs in start_job_ui("produce", f"{os.path.basename(folder)} (reanudado)",
                                     lambda: produce_reel(settings, resume_dir=folder)):
        yield snap_outputs[0], snap_outputs[3], snap_outputs[4]


def save_reel_preset_ui(folder, name):
    saved = load_settings(folder) if folder else {}
    if not saved:
        return "Elige un reel con ajustes guardados.", gr.update()
    return save_preset(name, saved)


def regenerate_kit(path, llm_name, llm_model):
    """New kit for a saved reel, written by the LLM currently selected in the production tab."""
    folder = reel_folder(path)
    if not folder or not os.path.isdir(folder):
        return ("Elige un reel primero.",) + tuple(gr.update() for _ in range(7))
    try:
        with open(f"{folder}/plan.json", encoding="utf-8") as f:
            plan = json.load(f)
        idea, language = os.path.basename(folder).replace("-", " "), "es"
        try:
            with open(f"{folder}/settings.json", encoding="utf-8") as f:
                saved = json.load(f)
            idea, language = saved.get("idea", idea), saved.get("language", language)
        except Exception:
            pass
        kit = generate_publish_kit(llm_key_for(llm_name), llm_model, idea, plan, language)
        save_publish_kit(kit, folder)
        save_assets(folder, hooks=kit.get("on_screen_hooks", []))
        return (f"✅ Kit regenerado con {llm_name} y guardado en Drive.",) + tuple(kit_updates(kit))
    except Exception as exc:
        return (f"❌ No se pudo regenerar: {exc}",) + tuple(gr.update() for _ in range(7))


def reassemble_ui(path, *montage_values):
    folder = reel_folder(path)
    yield progress_bar_html("Iniciando…", 0), "Starting...", gr.update()
    montage = montage_options(dict(zip(MONTAGE_KEYS, montage_values)))
    for outputs in start_job_ui("reassemble", f"{os.path.basename(folder or '')} (nuevo montaje)",
                                lambda: reassemble_reel(folder, montage)):
        snap_video = outputs[4]
        yield outputs[0], outputs[3], snap_video if snap_video else gr.update()


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------
def build_ui():
    """Builds the Control Center. Returns (blocks, components by name)."""
    FONTS[:] = font_families()
    KEY_COMPONENTS.clear()
    KEY_SHOWN.clear()
    prefs = load_prefs()
    initial_model = prefs["llm_model"] or default_model_for(llm_key_for(prefs["llm_provider"]))
    initial_voices = voice_choices(prefs["voice_provider"])
    initial_voice = prefs["voice"] if prefs["voice"] in [v for _, v in initial_voices] else (initial_voices[0][1] if initial_voices else "")
    initial_clip_kind = CLIP_PROVIDERS[prefs["clip_provider"]]["type"]

    with gr.Blocks(title="YT-Channel-Studio") as control_center:
        gr.Markdown("## 🎬 YT-Channel-Studio · Centro de Control")
        with gr.Tabs() as tabs:
            # ------------------------------------------------------------- produce
            with gr.Tab("🎬 Producir reel", id="produce"):
                with gr.Row():
                    with gr.Column(scale=5):
                        with gr.Accordion("📂 Presets: ajustes guardados para reutilizar", open=False):
                            with gr.Row():
                                preset_dd = gr.Dropdown(list_presets(), label="Preset", scale=3)
                                preset_load_btn = gr.Button("📥 Cargar", size="sm", scale=1)
                            with gr.Row():
                                preset_name = gr.Textbox(label="Nombre para guardar los ajustes actuales", scale=3)
                                preset_save_btn = gr.Button("💾 Guardar preset", size="sm", scale=1)
                            preset_msg = gr.Markdown()
                        idea_in = gr.Textbox(label="💡 Idea del reel", lines=2,
                                             placeholder="Ej: Por qué los pastores alemanes son tan fieles")
                        with gr.Group():
                            gr.Markdown("**📝 Guion**", elem_classes=["section-title"])
                            with gr.Row():
                                language_dd = gr.Dropdown([(v[0], k) for k, v in LANGUAGES.items()],
                                                          value=prefs["language"], label="Idioma")
                                duration_sl = gr.Slider(15, 90, value=prefs["duration"], step=5, label="Duración (s)")
                                tone_dd = gr.Dropdown([(k.capitalize().replace("Dramatico", "Dramático")
                                                        .replace("Polemico", "Polémico"), k) for k in TONES],
                                                      value=prefs["tone"], label="Tono")
                            instructions_in = gr.Textbox(label="Instrucciones extra (opcional)", lines=1,
                                                         value=prefs["instructions"],
                                                         placeholder="Ej: termina con una pregunta; menciona mi canal @...")
                            scenes_sl = gr.Slider(3, 8, value=prefs["num_scenes"], step=1, label="Número de escenas")
                        with gr.Group():
                            gr.Markdown("**🧠 Texto** (guion y kit de publicación)", elem_classes=["section-title"])
                            llm_type_rd = gr.Radio([(v, k) for k, v in KIND_LABELS.items()], value=prefs["llm_type"],
                                                   label="Tipo")
                            with gr.Row():
                                llm_dd = gr.Dropdown(llm_names_by_kind(prefs["llm_type"]), value=prefs["llm_provider"],
                                                     label="Proveedor")
                                model_dd = gr.Dropdown([initial_model] if initial_model else [], value=initial_model,
                                                       label="Modelo (editable)", allow_custom_value=True)
                        with gr.Group():
                            gr.Markdown("**🎞️ Clips**", elem_classes=["section-title"])
                            clip_type_rd = gr.Radio([(v, k) for k, v in CLIP_TYPES.items()], value=prefs["clip_type"],
                                                    label="Tipo")
                            clip_dd = gr.Dropdown(clip_provider_choices(prefs["clip_type"]), value=prefs["clip_provider"],
                                                  label="Proveedor")
                            paid_cb = gr.Checkbox(value=False, label="💳 Confirmo el cobro de este proveedor por cada clip",
                                                  visible=initial_clip_kind == "external_ai")
                            own_files_in = gr.File(label="Tus clips o fotos (se usan en este orden)", file_count="multiple",
                                                   file_types=["video", "image"],
                                                   visible=prefs["clip_provider"] == "upload")
                            footage_md = gr.Markdown(visible=prefs["clip_provider"] == "footage",
                                                     elem_classes=["section-title"])
                            keyframe_rd = gr.Radio([("Sin imagen (texto a video)", "none"), ("FLUX (local)", "flux"),
                                                    ("API compatible con OpenAI (externa)", "image_api")],
                                                   value=prefs["keyframe_provider"],
                                                   label="🖼️ Imagen base de cada clip (primer fotograma)",
                                                   visible=initial_clip_kind == "local_ai")
                        with gr.Group():
                            gr.Markdown("**🎙️ Voz**", elem_classes=["section-title"])
                            voice_type_rd = gr.Radio([(v, k) for k, v in VOICE_TYPES.items()], value=prefs["voice_type"],
                                                     label="Tipo")
                            with gr.Row():
                                voice_provider_dd = gr.Dropdown(voice_provider_choices(prefs["voice_type"]),
                                                                value=prefs["voice_provider"], label="Proveedor")
                                voice_dd = gr.Dropdown(initial_voices, value=initial_voice, label="Voz (editable)",
                                                       allow_custom_value=True)
                            clone_in = gr.Audio(label="🧬 Muestra de 5-10 s para clonar", type="filepath",
                                                visible=prefs["voice_provider"] == "f5")
                        with gr.Group():
                            gr.Markdown("**🖼️ Portada**", elem_classes=["section-title"])
                            cover_rd = gr.Radio([("Sin portada", "none"), ("FLUX (local)", "flux"),
                                                 ("API compatible con OpenAI (externa)", "image_api")],
                                                value=prefs["cover_provider"], label="Proveedor")
                        with gr.Group():
                            gr.Markdown("**🎵 Música de fondo**", elem_classes=["section-title"])
                            music_rd = gr.Radio([(v["label"], k) for k, v in MUSIC_SOURCES.items()],
                                                value=prefs["music_source"], label="Origen")
                        with gr.Accordion("🛠️ Montaje y subtítulos", open=False):
                            montage_inputs = build_montage_controls(prefs["montage"])
                        kit_cb = gr.Checkbox(value=prefs["make_kit"],
                                             label="📣 Generar kit de publicación (títulos, captions, hashtags y hooks)")
                        with gr.Accordion("⚙️ Ajustes de video IA local", open=False,
                                          visible=initial_clip_kind == "local_ai") as ai_box:
                            gr.Markdown("Ancho y alto se ajustan a múltiplos de 32 y al formato elegido. "
                                        "Frames: Wan usa 4n+1, LTX 8n+1 (se corrigen solos).")
                            with gr.Row():
                                ai_w = gr.Number(value=prefs["ai_width"], label="Ancho", precision=0)
                                ai_h = gr.Number(value=prefs["ai_height"], label="Alto", precision=0)
                                ai_f = gr.Number(value=prefs["ai_frames"], label="Frames", precision=0)
                                ai_s = gr.Number(value=prefs["ai_steps"], label="Pasos", precision=0)
                        status_box = gr.HTML()
                        with gr.Row():
                            recheck_btn = gr.Button("🔄 Revisar de nuevo", size="sm")
                            produce_btn = gr.Button("🚀 PRODUCIR REEL", variant="primary")
                            stop_btn = gr.Button("⏹ Detener", variant="stop")
                        with gr.Row():
                            resume_btn = gr.Button("🔁 Reanudar el último reel desde donde falló", size="sm")
                            attach_btn = gr.Button("📡 Ver el progreso en curso", size="sm")
                    with gr.Column(scale=5):
                        progress_box = gr.HTML(progress_bar_html())
                        with gr.Accordion("🔧 Log técnico (ComfyUI, APIs, ffmpeg, errores)", open=False):
                            tech_out = gr.Textbox(show_label=False, lines=14, max_lines=14, autoscroll=True,
                                                  interactive=False, elem_classes=["tech-log"], **copy_kwargs())
                        script_out = gr.Textbox(label="📝 Guion", lines=5, interactive=False, **copy_kwargs())
                        with gr.Accordion("📣 Kit de publicación (aparece apenas el guion está listo)", open=True):
                            kit_panel = build_kit_panel()
                        gallery_out = gr.Gallery(label="🖼️ Imágenes y clips (se llenan a medida que salen)",
                                                 columns=4, height=260, object_fit="cover")
                        log_out = gr.Textbox(label="📋 Registro paso a paso", lines=14, max_lines=14,
                                             autoscroll=True, interactive=False)
                        video_out = gr.Video(label="📱 Reel final")
                        variants_out = gr.File(label="🎞️ Todas las variantes", file_count="multiple", interactive=False)
                        reel_state = gr.State(None)
                        with gr.Accordion("📤 Publicar (Upload-Post)", open=False):
                            publish_btn, publish_inputs, publish_out = build_publish_panel()

            # ------------------------------------------------------------- keys
            with gr.Tab("🔑 Claves API", id="keys"):
                gr.Markdown(f"Se guardan en `{KEYS_FILE}` (texto plano en tu Drive). "
                            "**Probar** usa lo que está escrito, aunque aún no lo hayas guardado.")
                saved_keys = ensure_api_keys_file()
                for category, category_label in CREDENTIAL_CATEGORIES:
                    sections = [s for s, c in CREDENTIALS.items() if c["category"] == category]
                    with gr.Accordion(category_label, open=category == "llm"):
                        for section in sections:
                            cred = CREDENTIALS[section]
                            gr.Markdown(f"**{cred['label']}** · [obtener clave]({cred['link']})")
                            field_components = []
                            with gr.Row():
                                for field in cred["fields"]:
                                    comp = gr.Textbox(label=FIELD_LABELS.get(field, field),
                                                      value=saved_keys.get(section, {}).get(field, ""),
                                                      type="password" if field in SECRET_FIELDS else "text")
                                    KEY_COMPONENTS.append((section, field, comp))
                                    KEY_SHOWN[(section, field)] = (comp.value or "").strip()
                                    field_components.append(comp)
                            with gr.Row():
                                test_btn = gr.Button("🔌 Probar conexión", size="sm", scale=1)
                                test_out = gr.HTML(scale=4)
                            test_btn.click(make_test_handler(section), field_components, test_out)
                        if category == "llm":
                            gr.Markdown("**Servicios compatibles con OpenAI** (Groq, OpenRouter, DeepSeek, Together...). "
                                        "Elige uno para editarlo o escribe un nombre nuevo para añadirlo.")
                            first_compat = (compat_provider_names() or [""])[0]
                            with gr.Row():
                                compat_name = gr.Dropdown(compat_provider_names(), value=first_compat,
                                                          allow_custom_value=True, label="Servicio (nombre)")
                                compat_key = gr.Textbox(label="API key", value=get_compat(first_compat, "api_key"),
                                                        type="password")
                                compat_url = gr.Textbox(label="Base URL", value=get_compat(first_compat, "base_url"))
                                compat_chat = gr.Textbox(label="Modelo por defecto",
                                                         value=get_compat(first_compat, "chat_model"))
                            with gr.Row():
                                compat_test_btn = gr.Button("🔌 Probar conexión", size="sm", scale=1)
                                compat_save_btn = gr.Button("💾 Guardar este servicio", size="sm", scale=1)
                                compat_msg = gr.HTML(scale=3)
                save_btn = gr.Button("💾 Guardar todas las claves en Google Drive", variant="primary")
                save_msg = gr.Textbox(label="Confirmación", lines=2, interactive=False)

            # ------------------------------------------------------------- reels
            with gr.Tab("📁 Mis reels", id="reels"):
                with gr.Row():
                    reels_dd = gr.Dropdown(list_reels(), label="Reel guardado en Drive (✅ listo · ❌ falló · ⏹ detenido)",
                                           scale=4)
                    reels_refresh = gr.Button("🔄 Actualizar lista", scale=1)
                with gr.Row():
                    resume_reel_btn = gr.Button("🔁 Reanudar con sus ajustes", variant="primary", size="sm")
                    use_settings_btn = gr.Button("♻️ Usar estos ajustes en «Producir reel»", size="sm")
                reel_note = gr.Markdown()
                reel_progress = gr.HTML(progress_bar_html())
                with gr.Row():
                    reel_video = gr.Video(label="Reel")
                    with gr.Column():
                        reel_cover = gr.Image(label="Portada", height=260)
                        reel_script = gr.Textbox(label="Guion", lines=8, interactive=False, **copy_kwargs())
                reel_log = gr.Textbox(label="Registro", lines=8, max_lines=8, autoscroll=True, interactive=False)
                with gr.Accordion("⚙️ Ajustes con los que se hizo", open=False):
                    reel_settings = gr.JSON(show_label=False)
                    with gr.Row():
                        reel_preset_name = gr.Textbox(label="Guardarlos como preset con el nombre", scale=3)
                        reel_preset_btn = gr.Button("💾 Guardar preset", size="sm", scale=1)
                    reel_preset_msg = gr.Markdown()
                regen_btn = gr.Button("🔁 Regenerar kit con el LLM elegido en «Producir reel»", size="sm")
                with gr.Accordion("🛠️ Volver a montar (otro formato, subtítulos, ritmo o volumen; sin generar nada)",
                                  open=False):
                    reel_montage_inputs = build_montage_controls(prefs["montage"])
                    reassemble_btn = gr.Button("🎬 Volver a montar este reel", variant="primary")
                with gr.Accordion("📤 Publicar (Upload-Post)", open=False):
                    reel_publish_btn, reel_publish_inputs, reel_publish_out = build_publish_panel()
                reel_kit_panel = build_kit_panel()

        # ----------------------------------------------------------------- wiring
        # The cascading pickers react to the USER only (.input), so loading saved settings into the form is
        # never undone by them; a type change chains to the provider handler explicitly.
        clip_outputs = [paid_cb, ai_box, keyframe_rd, own_files_in, footage_md]
        check_inputs = [llm_dd, clip_dd, voice_provider_dd, cover_rd, keyframe_rd, music_rd, paid_cb, own_files_in]
        llm_type_rd.input(on_llm_type_change, llm_type_rd, llm_dd).then(on_llm_change, llm_dd, model_dd)
        llm_dd.input(on_llm_change, llm_dd, model_dd)
        clip_type_rd.input(on_clip_type_change, clip_type_rd, clip_dd).then(on_clip_change, clip_dd, clip_outputs)
        clip_dd.input(on_clip_change, clip_dd, clip_outputs)
        voice_type_rd.input(on_voice_type_change, voice_type_rd, voice_provider_dd).then(
            on_voice_provider_change, voice_provider_dd, [voice_dd, clone_in])
        voice_provider_dd.input(on_voice_provider_change, voice_provider_dd, [voice_dd, clone_in])
        for comp in check_inputs:
            comp.change(validate, check_inputs, status_box)
        recheck_btn.click(validate, check_inputs, status_box)
        control_center.load(validate, check_inputs, status_box)
        control_center.load(on_clip_change, clip_dd, clip_outputs)

        produce_inputs = [idea_in, language_dd, duration_sl, tone_dd, instructions_in, scenes_sl, llm_dd, model_dd,
                          clip_type_rd, clip_dd, paid_cb, own_files_in, keyframe_rd, voice_type_rd, voice_provider_dd,
                          voice_dd, clone_in, cover_rd, music_rd, kit_cb, ai_w, ai_h, ai_f, ai_s] + montage_inputs
        production_outputs = [progress_box, script_out, gallery_out, log_out, video_out, variants_out] + kit_panel + \
            [tech_out, reel_state]
        form_outputs = produce_inputs + [ai_box, footage_md]
        # Following a job is just watching it: several tabs may watch at once, and closing one stops nothing.
        produce_btn.click(produce_ui, produce_inputs, production_outputs, concurrency_limit=None)
        resume_btn.click(resume_ui, [reel_state] + produce_inputs, production_outputs, concurrency_limit=None)
        attach_btn.click(attach_ui, None, production_outputs, concurrency_limit=None)
        control_center.load(attach_ui, None, production_outputs, concurrency_limit=None)   # page reopened: reconnect
        stop_btn.click(stop_ui, None, progress_box)
        publish_btn.click(publish_ui, [reel_state] + publish_inputs + kit_panel[:6], publish_out)
        preset_save_btn.click(save_preset_ui, [preset_name] + produce_inputs, [preset_msg, preset_dd])
        preset_load_btn.click(load_preset_ui, preset_dd, form_outputs + [preset_msg, preset_dd]).then(
            validate, check_inputs, status_box)

        save_btn.click(save_all_keys, [c for _, _, c in KEY_COMPONENTS], save_msg).then(
            validate, check_inputs, status_box).then(on_voice_provider_change, voice_provider_dd, [voice_dd, clone_in])
        compat_name.change(load_compat_ui, compat_name, [compat_key, compat_url, compat_chat])
        compat_test_btn.click(test_compat_ui, [compat_url, compat_key], compat_msg)
        compat_save_btn.click(save_compat_ui, [compat_name, compat_key, compat_url, compat_chat],
                              [compat_msg, llm_type_rd, llm_dd]).then(on_llm_change, llm_dd, model_dd)

        reels_refresh.click(lambda: gr.update(choices=list_reels()), None, reels_dd)
        reels_dd.change(show_reel, reels_dd, [reel_video, reel_script, reel_cover, reel_note, reel_settings] + reel_kit_panel)
        resume_reel_btn.click(resume_reel_ui, reels_dd, [reel_progress, reel_log, reel_video], concurrency_limit=None)
        use_settings_btn.click(use_reel_settings_ui, reels_dd, form_outputs + [reel_note, reel_state, tabs]).then(
            validate, check_inputs, status_box)
        reel_preset_btn.click(save_reel_preset_ui, [reels_dd, reel_preset_name], [reel_preset_msg, preset_dd])
        regen_btn.click(regenerate_kit, [reels_dd, llm_dd, model_dd], [reel_note] + reel_kit_panel)
        reassemble_btn.click(reassemble_ui, [reels_dd] + reel_montage_inputs, [reel_progress, reel_log, reel_video],
                             concurrency_limit=None)
        reel_publish_btn.click(publish_ui, [reels_dd] + reel_publish_inputs + reel_kit_panel[:6], reel_publish_out)
    return control_center, dict(locals())


UI_CSS = """
.tech-log textarea {font-family: ui-monospace, Menlo, Consolas, monospace !important; font-size: 12px !important;}
.section-title {padding: 8px 12px 4px 12px !important;}
.section-title > *, .section-title .prose {padding: 0 !important; margin: 0 !important;}
"""


def start():
    start_cell("Cell 7 · Control Center")
    ensure_api_keys_file()
    blocks, _ = build_ui()
    launch_gradio(blocks, PORTS["control_center"], css=UI_CSS)
    url = start_cloudflare_tunnel(PORTS["control_center"], "control_center_tunnel", "Control Center",
                                  "Type an idea and run the whole flow until the reel is saved in Drive")
    tools = "Tools running: " + ", ".join(
        f"{'🟢' if service_ready(name) else '⚪'} {svc['label']}" for name, svc in SERVICES.items())
    problems = [] if is_public_url(url) else [f"No public link yet: {url}"]
    finish_cell(not problems, [f"CONTROL CENTER: {url}", tools,
                               f"Video encoding: {'GPU (NVENC)' if nvenc_available() else 'CPU (libx264)'}",
                               f"Subtitle fonts: {', '.join(FONTS)}",
                               "(⚪ = not running; the form tells you when a choice needs it)"], problems)
