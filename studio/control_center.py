"""Cell 7: the Control Center. A simple composer (idea, length, format and one engine per kind of content) with
every advanced option in a settings window that only shows what applies to the current choice.

A reel is made step by step: storyboard -> images -> audio -> clips -> assembly. A bar of steps shows where it is,
and only the panel of the step at hand is open; the results of the finished steps stay readable beside it, and
any item opens in a detail window. With «Review each step» on, it pauses after every step: the storyboard is
editable text (edit it, or regenerate it with notes), and every image, voice, sound and clip is a card with its
own prompt, its own «Regenerate» and a tick for «Regenerate selected». The finished reel, its kit and publishing
come last, with no platform ticked.
Jobs run in the background (the page only watches them), so a disconnection loses nothing; failed reels resume
from the failed step; any reel's settings or a saved preset can be reused. Also: API keys, saved reels,
re-assembly and publishing."""
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
SCENE_CHOICES = [("Automatic (from the length)", "auto")] + [(str(n), str(n)) for n in range(3, 11)]
TONE_CHOICES = [("Automatic", "auto")] + [(k.capitalize(), k) for k in TONES]
DEFAULT_PREFS = {
    "duration": 35, "style": "auto", "tone": "auto", "instructions": "", "num_scenes": "auto",
    "llm_provider": "OpenAI (ChatGPT)", "llm_model": "",
    "image_provider": "flux", "make_cover": True, "use_keyframes": True,
    "clip_type": "stock", "clip_provider": "pexels",
    "voice_type": "local", "voice_provider": "edge", "voice": "es-MX-JorgeNeural",
    "scene_sound": "none", "music_source": "library", "make_kit": True, "review_steps": True, "show_tech": False,
    "ai_width": 480, "ai_height": 832, "ai_frames": 121, "ai_steps": 20,
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
    saved = saved or {}
    prefs = json.loads(json.dumps(DEFAULT_PREFS))
    prefs.update({k: v for k, v in saved.items() if k in DEFAULT_PREFS and k != "montage" and v is not None})
    prefs["montage"].update(saved.get("montage") or {})
    prefs["idea"] = saved.get("idea", "")
    prefs["montage"] = montage_options(prefs["montage"])
    if prefs["montage"]["font"] not in FONTS:
        prefs["montage"]["font"] = "Anton" if "Anton" in FONTS else FONTS[0]
    if prefs["llm_provider"] not in llm_provider_choices():
        prefs["llm_provider"] = DEFAULT_PREFS["llm_provider"]
    prefs["llm_type"] = llm_kind(prefs["llm_provider"])
    if prefs["clip_provider"] not in CLIP_PROVIDERS:
        prefs["clip_provider"] = DEFAULT_PREFS["clip_provider"]
    prefs["clip_type"] = CLIP_PROVIDERS[prefs["clip_provider"]]["type"]
    if prefs["voice_provider"] not in VOICE_PROVIDERS:
        prefs.update(voice_provider="edge", voice="es-MX-JorgeNeural")
    prefs["voice_type"] = VOICE_PROVIDERS[prefs["voice_provider"]]["type"]
    prefs["num_scenes"] = str(prefs["num_scenes"])
    for key, allowed in (("image_provider", IMAGE_PROVIDERS), ("music_source", MUSIC_SOURCES),
                         ("scene_sound", SCENE_SOUNDS), ("style", STYLES), ("tone", ["auto"] + list(TONES)),
                         ("num_scenes", [v for _, v in SCENE_CHOICES])):
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


def save_show_tech(show):
    prefs = read_json(PREFS_FILE)
    prefs["show_tech"] = bool(show)
    save_prefs(prefs)


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------
def progress_bar_html(stage="Waiting", pct=0, failed=False, done=False, review=False):
    color = "#dc2626" if failed else ("#16a34a" if done else ("#d97706" if review else "#4f46e5"))
    icon = "❌" if failed else ("✅" if done else ("👀" if review else "⏳"))
    return (f"<div style='font-family:sans-serif;padding:4px 0'>"
            f"<div style='display:flex;justify-content:space-between;font-weight:600;margin-bottom:6px'>"
            f"<span>{icon} {html.escape(stage)}</span><span>{pct}%</span></div>"
            f"<div style='background:rgba(127,127,127,.25);border-radius:8px;height:14px;overflow:hidden'>"
            f"<div style='width:{pct}%;background:{color};height:100%;transition:width .4s'></div></div></div>")


def current_step(log, failed=False):
    """The one line shown under the progress bar: the latest step, or the short reason of a failure.
    The complete detail (tracebacks, HTTP bodies) is in the technical log."""
    lines = [l for l in (log or "").splitlines() if l.strip()]
    if not lines:
        return ""
    if failed:
        errors = [l for l in lines if "❌" in l]
        reason = (errors[-1] if errors else lines[-1])[:180]
        return f"{html.escape(reason)}<br>🔁 <b>Resume</b> continues from here without repeating what is done."
    return html.escape(lines[-1][:180])


def step_html(text):
    return f"<div class='step-line'>{text}</div>" if text else ""


def clock(seconds):
    seconds = int(seconds or 0)
    return f"{seconds // 60}:{seconds % 60:02d}"


def stage_label(snap):
    """The stage, plus how long it has been on it while a step runs, so a long wait (a model loading) never
    looks like a frozen page."""
    stage = snap.get("stage") or "Waiting"
    running = not (snap.get("failed") or snap.get("done") or snap.get("review"))
    since = snap.get("since") or 0
    return f"{stage} · {clock(since)} on this step" if running and since >= 5 else stage


# ---------------------------------------------------------------------------
# Steps: one bar on top, one panel for the step at hand, earlier results beside it
# ---------------------------------------------------------------------------
STEPS = [("storyboard", "Storyboard"), ("images", "Images"), ("audio", "Audio"), ("clips", "Clips"),
         ("montage", "Montage"), ("publish", "Publish")]
STEP_LABELS = dict(STEPS)
STEP_OF_STAGE = [   # (start of the stage the pipeline reports, step it belongs to)
    ("Checking", "storyboard"), ("Preparing", "storyboard"), ("Starting", "storyboard"),
    ("Writing the storyboard", "storyboard"), ("Applying your storyboard", "storyboard"),
    ("Storyboard", "storyboard"), ("Review the storyboard", "storyboard"), ("Publish kit", "images"),
    ("Cover", "images"), ("Image", "images"), ("Review the images", "images"),
    ("Voice", "audio"), ("Timing the words", "audio"), ("Sound", "audio"), ("Review the audio", "audio"),
    ("Clip", "clips"), ("Review the clips", "clips"),
    ("AI music", "montage"), ("Timing the subtitles", "montage"), ("Scene ", "montage"), ("Variant", "montage"),
]
ITEM_STEPS = {"image": "images", "cover": "images", "audio": "audio", "sound": "audio", "clip": "clips"}


def step_of(stage):
    """The step a pipeline stage belongs to, or None when the stage does not say (Stopped, Internal error)."""
    stage = re.sub(r"^Error in: ", "", stage or "")
    item = re.search(r"\bscene\d+_(image|audio|sound|clip)\b|\bcover\b", stage)
    if item:
        return ITEM_STEPS[item.group(1) or "cover"]
    for start, step in STEP_OF_STAGE:
        if stage.startswith(start):
            return step
    return None


def step_states(snap):
    """{step: 'done' | 'running' | 'review' | 'failed' | 'todo'} for a snapshot (None: nothing started)."""
    if not snap or not (snap.get("reel_dir") or snap.get("stage")):
        return {}
    if snap.get("done"):
        return {key: ("review" if key == "publish" else "done") for key, _ in STEPS}
    current = step_of(snap.get("stage"))
    if snap.get("review"):
        current = snap["review"].get("stage") or current
    if current is None and snap.get("reel_dir"):
        current = step_of(read_run_state(snap["reel_dir"]).get("stage")) or \
            read_run_state(snap["reel_dir"]).get("stage")
    if current not in STEP_LABELS:
        current = "storyboard"
    status = "failed" if snap.get("failed") else ("review" if snap.get("review") else "running")
    order = [key for key, _ in STEPS]
    at = order.index(current)
    return {key: ("done" if i < at else (status if i == at else "todo")) for i, key in enumerate(order)}


STEP_MARKS = {"done": "✓", "running": "…", "review": "!", "failed": "✕"}


def stepper_html(states):
    if not states:
        return ""
    chips = []
    for i, (key, label) in enumerate(STEPS, 1):
        state = states.get(key, "todo")
        current = " aria-current='step'" if state in ("running", "review", "failed") else ""
        chips.append(f"<li class='step step-{state}'{current}><span class='step-dot' aria-hidden='true'>"
                     f"{STEP_MARKS.get(state, i)}</span>{label}</li>")
    return f"<ol class='stepper' aria-label='Steps'>{''.join(chips)}</ol>"


def done_steps(states):
    """Finished steps that have something to look at, in order (the earlier-results picker)."""
    return [(STEP_LABELS[key], key) for key, _ in STEPS if states.get(key) == "done" and key != "publish"]


def summary_html(reel_dir):
    """The composer folded into one line once a reel is under way."""
    if not reel_dir:
        return ""
    idea = load_settings(reel_dir).get("idea") or os.path.basename(reel_dir).replace("-", " ")
    return (f"<div class='summary'><div class='summary-idea'>{html.escape(idea)}</div>"
            f"<div class='summary-folder'>Drive › reels › {html.escape(os.path.basename(reel_dir))}</div></div>")


# ---------------------------------------------------------------------------
# What a finished step made, and the window that shows one item in detail
# ---------------------------------------------------------------------------
def final_videos(reel_dir):
    return sorted(glob.glob(f"{reel_dir}/*_FINAL*.mp4")) if reel_dir else []


def step_items(reel_dir, step):
    """The items of a finished step, shaped like the review items ({id, label, kind, path, text, text_label})."""
    if not reel_dir or not os.path.isdir(reel_dir):
        return []
    if step == "storyboard":
        plan = load_plan(reel_dir) or {}
        items = []
        for s in plan.get("scenes", []):
            image = scene_image(reel_dir, s["id"])
            details = [f"**Visual:** {s.get('visual', '')}", f"**Sound:** {s.get('sound', '') or '—'}"]
            if s.get("image_prompt"):
                details.append(f"**Image prompt:** {s['image_prompt']}")
            items.append(dict(id=f"scene{s['id']}", label=f"Scene {s['id']} · {s.get('title', '')}",
                              kind="image" if os.path.exists(image) else "text",
                              path=image if os.path.exists(image) else None,
                              text=s.get("narration") or "—", text_label="Narration",
                              extra="\n\n".join(details)))
        return items
    if step == "montage":
        return [dict(id=os.path.basename(v), label=os.path.basename(v), kind="video", path=v, text="",
                     text_label="") for v in final_videos(reel_dir)]
    return review_items(reel_dir, step)


DETAIL_COUNT = 9     # see detail_updates


def detail_updates(items, index):
    """(state, window, title, image, video, audio, text, previous, next) for item `index` of `items`."""
    if not items:
        return (None, gr.update(visible=False)) + tuple(gr.update() for _ in range(DETAIL_COUNT - 2))
    index = max(0, min(index, len(items) - 1))
    item = items[index]
    kind, path = item.get("kind"), item.get("path")
    text = ""
    if item.get("text"):
        text = f"**{item.get('text_label') or 'Text'}**\n\n{item['text']}"
    if item.get("extra"):
        text += ("\n\n" if text else "") + item["extra"]
    return ({"items": items, "index": index}, gr.update(visible=True),
            gr.update(value=f"### {item.get('label', '')}\n{index + 1} of {len(items)}"),
            gr.update(value=path if kind == "image" else None, visible=kind == "image"),
            gr.update(value=path if kind == "video" else None, visible=kind == "video"),
            gr.update(value=path if kind == "audio" and path else None, visible=kind == "audio" and bool(path)),
            gr.update(value=text or "*(nothing to show)*"),
            gr.update(interactive=index > 0), gr.update(interactive=index < len(items) - 1))


def detail_step(state, delta):
    if not state:
        return detail_updates([], 0)
    return detail_updates(state["items"], state["index"] + delta)


def item_media(item, height):
    """The picture, clip or voice of one item (review card, earlier result)."""
    kind, path = item.get("kind"), item.get("path")
    if kind == "image" and path:
        gr.Image(path, show_label=False, height=height, interactive=False)
    elif kind == "video" and path:
        gr.Video(path, show_label=False, height=height, interactive=False)
    elif kind == "audio" and path:
        gr.Audio(path, show_label=False, interactive=False)
    else:
        gr.Markdown("*(no voice in this scene)*" if kind == "audio" else "*(no picture yet)*",
                    elem_classes=["card-empty"])


def close_detail():
    """Hides the window and empties its players, so a clip or a voice never keeps playing behind the page."""
    return (None, gr.update(visible=False), gr.update(), gr.update(value=None), gr.update(value=None),
            gr.update(value=None), gr.update(), gr.update(), gr.update())


def status_html(errors, warnings):
    """Compact: only what blocks the run (red) and what to know (amber)."""
    box = ""
    if errors:
        items = "".join(f"<li>{e}</li>" for e in errors)
        box = (f"<div style='border:1px solid #dc2626;background:rgba(220,38,38,.12);color:#ef4444;"
               f"padding:8px 12px;border-radius:10px'><b>⛔ Something is missing before producing:</b>"
               f"<ul style='margin:4px 0 0 18px'>{items}</ul></div>")
    if warnings:
        items = "".join(f"<li>{w}</li>" for w in warnings)
        box += (f"<div style='border:1px solid #d97706;background:rgba(217,119,6,.12);color:#f59e0b;"
                f"padding:6px 12px;border-radius:10px;margin-top:6px'><ul style='margin:0 0 0 18px'>{items}</ul></div>")
    return box or ("<div style='color:#22c55e;font-size:13px;padding:2px 4px'>✅ Ready to produce.</div>")


def message_html(ok, message):
    icon = {True: "✅", False: "❌"}.get(ok, "⚠️")
    color = {True: "#22c55e", False: "#ef4444"}.get(ok, "#f59e0b")
    return f"<span style='color:{color};font-weight:600'>{icon} {html.escape(message)}</span>"


def llm_key_for(llm_name):
    return llm_provider_choices().get(llm_name, "openai")


def validate(llm_name, clip_provider, voice_provider, image_provider, music_source, confirm_paid, own_files,
             make_cover, use_keyframes, scene_sound):
    errors, warnings = check_selection(llm_key_for(llm_name), clip_provider, voice_provider, image_provider,
                                       music_source, confirm_paid, len(own_files or []), make_cover, use_keyframes,
                                       scene_sound)
    return status_html(errors, warnings)


# ---------------------------------------------------------------------------
# Chips: the engine pickers of the composer show what is chosen
# ---------------------------------------------------------------------------
def short(text, n=22):
    text = str(text or "")
    return text if len(text) <= n else text[:n - 1] + "…"


def chip_labels(llm_name, model, image_provider, voice_provider, voice, clip_provider, aspect, duration, style):
    voice_name = dict((v, l) for l, v in voice_choices(voice_provider)).get(voice, voice) if voice_provider else ""
    voice_name = voice_name.split(" · ")[-1] if " · " in voice_name else voice_name
    return [f"🧠 {short(llm_name.split(' (')[0], 14)}" + (f" · {short(model, 18)}" if model else ""),
            f"🖼️ {short(IMAGE_PROVIDERS.get(image_provider, {}).get('label', image_provider).split(' (')[0], 16)}",
            f"🎙️ {short(VOICE_PROVIDERS[voice_provider]['label'].split(' (')[0], 12)} · {short(voice_name, 16)}",
            f"🎞️ {short(CLIP_PROVIDERS[clip_provider]['label'].split(' (')[0], 22)}",
            f"📐 {aspect}", f"⏱️ {int(duration)} s", f"🎭 {short(STYLES.get(style, style).split(' ', 1)[-1], 22)}"]


def refresh_chips(*values):
    return [gr.update(value=label) for label in chip_labels(*values)]


# ---------------------------------------------------------------------------
# Cascading pickers (they react to the user only, so loading saved settings is never undone by them)
# ---------------------------------------------------------------------------
def on_llm_type_change(llm_type):
    names = llm_names_by_kind(llm_type)
    return gr.update(choices=names, value=names[0] if names else None)


def on_llm_change(llm_name):
    """The provider's own model list (asked live), its configured default first."""
    if not llm_name:
        return gr.update()
    models = list_llm_models(llm_key_for(llm_name))
    return gr.update(choices=models, value=models[0] if models else "")


def refresh_models(llm_name, current):
    models = list_llm_models(llm_key_for(llm_name))
    if current and current not in models:
        models = [current] + models
    note = f"{len(models)} model(s) available." if len(models) > 1 else \
        "The list could not be fetched (missing key?): type the model name."
    return gr.update(choices=models, value=current or (models[0] if models else "")), note


def on_clip_type_change(clip_type):
    choices = clip_provider_choices(clip_type)
    return gr.update(choices=choices, value=choices[0][1])


def footage_note():
    return f"📂 `{FOOTAGE_DIR}` · {len(footage_files())} file(s). Used in name order, one per scene."


def clip_visibility(clip_provider):
    """Paid confirmation (external AI), file box (uploads), folder note (footage), the first-frame option
    (clips that accept an image) and the local AI settings (Wan/LTX)."""
    clip = CLIP_PROVIDERS[clip_provider]
    return (gr.update(visible=clip["type"] == "external_ai", value=False),
            gr.update(visible=clip_provider == "upload"),
            gr.update(visible=clip_provider == "footage", value=footage_note() if clip_provider == "footage" else ""),
            gr.update(visible=bool(clip.get("accepts_image"))),
            gr.update(visible=clip["type"] == "local_ai"))


def on_voice_type_change(voice_type):
    choices = voice_provider_choices(voice_type)
    return gr.update(choices=choices, value=choices[0][1])


def on_voice_provider_change(voice_provider):
    choices = voice_choices(voice_provider)
    first = choices[0][1] if choices else ""
    return gr.update(choices=choices, value=first), gr.update(visible=voice_provider == "f5")


# ---------------------------------------------------------------------------
# Montage controls (in the settings window and in «Re-assemble»)
# ---------------------------------------------------------------------------
MONTAGE_KEYS = list(DEFAULT_MONTAGE)


def build_montage_controls(m, aspect=None):
    """Returns the components in MONTAGE_KEYS order. `aspect`: an aspect component created elsewhere (the
    composer's format picker); without it the format is part of these controls."""
    c = {}
    if aspect is None:
        c["aspect"] = gr.Radio([("9:16 vertical", "9:16"), ("16:9 horizontal", "16:9"), ("1:1 square", "1:1")],
                               value=m["aspect"], label="Format")
    else:
        c["aspect"] = aspect
    c["fit"] = gr.Radio([(v, k) for k, v in FITS.items()], value=m["fit"], label="Clips of another shape")
    with gr.Row():
        c["transition"] = gr.Dropdown([(v, k) for k, v in TRANSITIONS.items()], value=m["transition"],
                                      label="Transition between scenes")
        c["transition_duration"] = gr.Slider(0.1, 1.0, value=m["transition_duration"], step=0.05,
                                             label="Transition length (s)")
    with gr.Row():
        c["clip_speed"] = gr.Slider(0.5, 2.0, value=m["clip_speed"], step=0.05, label="Clip speed")
        c["voice_speed"] = gr.Slider(0.75, 1.5, value=m["voice_speed"], step=0.05, label="Voice speed")
        c["voice_volume"] = gr.Slider(0.5, 2.0, value=m["voice_volume"], step=0.05, label="Voice volume")
    with gr.Row():
        c["clip_audio"] = gr.Checkbox(value=m["clip_audio"],
                                      label="Use the clips' own sound (AI video with audio, your files)")
        c["sound_volume"] = gr.Slider(0.0, 1.0, value=m["sound_volume"], step=0.05,
                                      label="Clip sound and effects under the voice")
    with gr.Row():
        c["music_volume"] = gr.Slider(0.0, 0.5, value=m["music_volume"], step=0.01, label="Music volume")
        c["ducking"] = gr.Checkbox(value=m["ducking"], label="Lower the music while the voice speaks")
    with gr.Row():
        c["subtitle_style"] = gr.Radio([(v, k) for k, v in SUBTITLE_STYLES.items()], value=m["subtitle_style"],
                                       label="Subtitles")
        c["subtitle_position"] = gr.Radio([(v, k) for k, v in SUBTITLE_POSITIONS.items()],
                                          value=m["subtitle_position"], label="Position")
    with gr.Row():
        c["font"] = gr.Dropdown(FONTS, value=m["font"] if m["font"] in FONTS else FONTS[0], allow_custom_value=True,
                                label="Font (add yours in Drive/fonts)")
        c["font_size"] = gr.Slider(40, 160, value=m["font_size"], step=2, label="Font size")
    with gr.Row():
        c["words_per_line"] = gr.Slider(1, 6, value=m["words_per_line"], step=1, label="Words per line")
        with gr.Column():
            c["uppercase"] = gr.Checkbox(value=m["uppercase"], label="UPPERCASE")
            c["box"] = gr.Checkbox(value=m["box"], label="Box behind the text")
    with gr.Row():
        c["text_color"] = gr.ColorPicker(value=m["text_color"], label="Text")
        c["highlight_color"] = gr.ColorPicker(value=m["highlight_color"], label="Highlight and hook")
        c["outline_color"] = gr.ColorPicker(value=m["outline_color"], label="Outline or box")
    with gr.Row():
        c["hook_overlay"] = gr.Checkbox(value=m["hook_overlay"], label="🪝 On-screen hook at the start")
        c["hook_seconds"] = gr.Slider(1, 5, value=m["hook_seconds"], step=0.5, label="Hook seconds")
    c["variants"] = gr.Slider(1, 3, value=m["variants"], step=1,
                              label="Variants (each with another hook, another song and other transitions)")
    return [c[k] for k in MONTAGE_KEYS]


# ---------------------------------------------------------------------------
# Publish kit panel (one per tab that shows a kit)
# ---------------------------------------------------------------------------
def copy_kwargs():
    """Copy button on text fields (Gradio 6)."""
    return {"buttons": ["copy"]}


KIT_FIELDS = [   # (label, kit path, hard limit, characters visible before "more")
    ("YouTube · Title", ("youtube", "title"), 100, None),
    ("YouTube · Description (with hashtags)", ("youtube", "description"), 5000, 100),
    ("YouTube · Tags (Tags field)", ("youtube", "tags"), 500, None),
    ("TikTok · Caption", ("tiktok", "caption"), 4000, 80),
    ("Instagram · Caption", ("instagram", "caption"), 2200, 125),
    ("Facebook · Caption", ("facebook", "caption"), 2200, 125),
]


def field_label(label, text, limit, visible):
    n = len(text or "")
    extra = f" · ~{visible} visible" if visible else ""
    return f"{label} ({n}/{limit}{extra})" + (" ⚠️ over the limit" if n > limit else "")


def kit_extras_markdown(kit):
    if not kit:
        return ""
    v = kit.get("virality", {})

    def score(key):
        return v.get(key, "?")
    yt = kit["youtube"]
    parts = [f"### 📊 Viral potential: {score('overall')}/100",
             f"{v.get('verdict', '')}",
             f"| Hook | Retention | Shareability | Commentability | Niche | Emotion |\n|---|---|---|---|---|---|\n"
             f"| {score('hook')} | {score('retention')} | {score('shareability')} | {score('commentability')} "
             f"| {score('niche_fit')} | {score('emotional')} |",
             f"**Strongest point:** {v.get('strongest', '')}  \n**Weakest point:** {v.get('weakest', '')}  \n"
             f"**Recommendation:** {v.get('recommendation', '')}",
             "### 🪝 On-screen text (first 2 s)", "\n".join(f"- {h}" for h in kit["on_screen_hooks"]),
             "### 🎬 Title variants (YouTube)", "\n".join(f"- {t}" for t in yt["title_variants"]),
             "### 💬 Seed comments (the first is posted as first comment if you choose so)",
             "\n".join(f"{i}. {c}" for i, c in enumerate(kit["seed_comments"], 1)),
             f"### ⚡ Story teaser\n{kit['teaser']}"]
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
        with gr.Tab("📊 Virality and extras"):
            comps[6] = gr.Markdown()
    return [comps[i] for i in range(7)]


# ---------------------------------------------------------------------------
# Publishing panel (Upload-Post). Uses the kit texts as they are in the fields, edits included.
# ---------------------------------------------------------------------------
def build_publish_panel():
    """Nothing is ticked and the visibility starts private: a reel is never posted by a stray click."""
    with gr.Row():
        platforms = gr.CheckboxGroup([(v, k) for k, v in PUBLISH_PLATFORMS.items()], value=[],
                                     label="Platforms (none chosen yet)")
    with gr.Row():
        schedule = gr.Textbox(label="Schedule (empty = now)", placeholder="2026-11-02 18:30")
        timezone = gr.Textbox(label="Time zone", value="America/Costa_Rica")
        yt_privacy = gr.Dropdown([(v, k) for k, v in YOUTUBE_PRIVACY.items()], value="private", label="YouTube")
        tt_privacy = gr.Dropdown([(v, k) for k, v in TIKTOK_PRIVACY.items()], value="SELF_ONLY", label="TikTok")
    first_comment = gr.Checkbox(value=True, label="💬 Post the first seed comment (Instagram, YouTube and Facebook)")
    button = gr.Button("📤 Publish this reel", variant="primary")
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
        yield "Produce or pick a reel first."
        return
    if not platforms:
        yield "Choose at least one platform first."
        return
    kit = load_publish_kit(folder)
    if kit:                                         # the fields win: they may have been edited by hand
        for (_, (platform, key), _, _), text in zip(KIT_FIELDS, kit_texts):
            if text:
                kit[platform][key] = text
    video = f"{folder}/{os.path.basename(folder)}_FINAL.mp4"
    names = ", ".join(PUBLISH_PLATFORMS[p] for p in platforms or [])
    yield f"⏳ Uploading the video to Upload-Post for {names}..."
    try:
        body = publish_reel(video, kit, platforms or [], schedule=schedule, timezone=timezone,
                            youtube_privacy=yt_privacy, tiktok_privacy=tt_privacy, first_comment=first_comment)
    except Exception as exc:
        yield f"❌ {html.escape(str(exc))}"
        return
    if body.get("job_id") or (schedule or "").strip():
        yield f"🗓️ Scheduled ({schedule} {timezone}). ID: `{body.get('job_id', body.get('request_id', '?'))}`"
        return
    request_id = body.get("request_id")
    if body.get("results") or not request_id:
        yield "\n\n".join(describe_publish(body)) or f"Answer: `{json.dumps(body)[:500]}`"
        return
    started = time.time()
    while time.time() - started < 900:              # each platform finishes on its own time
        time.sleep(10)
        try:
            status = publish_status(request_id)
        except Exception as exc:
            yield f"⏳ Publishing (ID `{request_id}`)... the status could not be checked: {html.escape(str(exc))}"
            continue
        state = str(status.get("status", "")).lower()
        if state == "completed" or status.get("results") and state not in ("pending", "queued", "processing",
                                                                            "in_progress"):
            yield "\n\n".join(describe_publish(status)) or f"Status: `{json.dumps(status)[:500]}`"
            return
        if state == "failed":
            yield f"❌ Upload-Post: {html.escape(str(status.get('message') or status.get('error') or status))}"
            return
        yield f"⏳ Publishing to {names}... ({int(time.time() - started)} s, status: {state or 'in progress'})"
    yield f"⏳ Still in progress. Check its status at app.upload-post.com with the ID `{request_id}`."


# ---------------------------------------------------------------------------
# Production: a background job the page only watches
# ---------------------------------------------------------------------------
PRODUCE_KEYS = ["idea", "duration", "style", "tone", "instructions", "num_scenes", "llm_provider", "llm_model",
                "image_provider", "make_cover", "use_keyframes", "clip_type", "clip_provider", "confirm_paid",
                "own_files", "voice_type", "voice_provider", "voice", "clone_sample", "scene_sound", "music_source",
                "make_kit", "review_steps", "ai_width", "ai_height", "ai_frames", "ai_steps"]
KIT_COUNT, REVIEW_COUNT = 7, 6
OUTPUT_NAMES = (["progress", "step", "stepper", "video", "variants"] + [f"kit{i}" for i in range(KIT_COUNT)] +
                ["tech", "reel", "review", "panel", "title", "storyboard_box", "storyboard_text", "items_box",
                 "final", "composer", "summary", "summary_text", "results_choice", "results_col"])
OUTPUT_COUNT = len(OUTPUT_NAMES)     # see snapshot_outputs
OUT = {name: i for i, name in enumerate(OUTPUT_NAMES)}


def form_settings(values):
    """The production form (PRODUCE_KEYS + montage controls, in that order) as a settings dict."""
    settings = dict(zip(PRODUCE_KEYS, values[:len(PRODUCE_KEYS)]))
    settings["montage"] = montage_options(dict(zip(MONTAGE_KEYS, values[len(PRODUCE_KEYS):])))
    for key in ("duration", "ai_width", "ai_height", "ai_frames", "ai_steps"):
        settings[key] = int(settings[key] or DEFAULT_PREFS[key])
    settings["num_scenes"] = str(settings.get("num_scenes") or "auto")
    return settings


STAGE_TITLES = {
    "storyboard": "👀 Review the storyboard",
    "images": "👀 Review the images",
    "audio": "👀 Review the audio",
    "clips": "👀 Review the clips",
}
STAGE_HINTS = {
    "storyboard": "Edit the text directly (what is seen, said and heard in each scene, in the language of your idea) "
                  "or regenerate it with notes. «Confirm» writes the English prompts of the scenes you changed and "
                  "goes on to the images.",
    "images": "🔍 opens an image in detail. Edit a prompt and press its «Regenerate», or tick several and press "
              "«Regenerate selected». «Confirm» goes on to the audio.",
    "audio": "Edit a narration (empty = a scene without voice) and regenerate it, alone or with others ticked. Each "
             "scene lasts as long as its narration. «Confirm» goes on to the clips, made to the length of each scene.",
    "clips": "🔍 plays a clip in detail. Edit a prompt or search and regenerate it, alone or with others ticked (a "
             "paid clip is charged again). «Confirm» assembles the reel.",
}


def review_updates(review):
    """(review state, panel, title, storyboard box, storyboard text, items box) for a review payload."""
    if not review:
        return (None, gr.update(visible=False), gr.update(), gr.update(visible=False), gr.update(),
                gr.update(visible=False))
    stage = review["stage"]
    storyboard = stage == "storyboard"
    title = f"### {STAGE_TITLES.get(stage, stage)}\n{STAGE_HINTS.get(stage, '')}"
    return (review, gr.update(visible=True), gr.update(value=title), gr.update(visible=storyboard),
            gr.update(value=review.get("storyboard", "")) if storyboard else gr.update(),
            gr.update(visible=not storyboard))


def pack(values):
    """{output name: value} -> the production outputs tuple; a name left out changes nothing."""
    return tuple(values.get(name, gr.update()) for name in OUTPUT_NAMES)


def snapshot_outputs(snap, shown):
    """One snapshot -> the production outputs. `shown` remembers what is already on screen (kit, review, step
    bar, earlier results), so it is only sent again when it changes and a storyboard you are editing is never
    overwritten."""
    review = snap.get("review")
    states = step_states(snap)
    reel = snap.get("reel_dir")
    finished = bool(snap.get("done") and snap.get("video"))
    values = {"progress": progress_bar_html(stage_label(snap), snap["pct"], snap["failed"], snap["done"],
                                            bool(review)),
              "step": step_html(current_step(snap["log"], snap["failed"]) + (
                  "<br><span class='hint'>You can close this tab: the job keeps running in Colab and this page "
                  "shows it again when you come back.</span>"
                  if not (snap["failed"] or snap["done"] or review) else "")),
              "video": snap["video"], "variants": snap["videos"] or None, "tech": snap["tech"],
              "reel": reel or gr.update()}
    if snap["kit"] is not shown.get("kit", "unset"):
        shown["kit"] = snap["kit"]
        values.update(zip([f"kit{i}" for i in range(KIT_COUNT)], kit_updates(snap["kit"])))
    key = json.dumps(review, sort_keys=True) if review else None
    if key != shown.get("review", "unset"):
        shown["review"] = key
        values.update(zip(["review", "panel", "title", "storyboard_box", "storyboard_text", "items_box"],
                          review_updates(review)))
    bar = stepper_html(states)
    if bar != shown.get("stepper"):
        shown["stepper"] = bar
        values["stepper"] = bar
    if finished != shown.get("final"):
        shown["final"] = finished
        values["final"] = gr.update(visible=finished)
    if reel and reel != shown.get("summary_reel"):        # a reel under way: the composer folds into one line
        shown["summary_reel"] = reel
        values.update(composer=gr.update(visible=False), summary=gr.update(visible=True),
                      summary_text=summary_html(reel))
    choices = done_steps(states)
    if choices != shown.get("results"):
        shown["results"] = choices
        values.update(results_choice=gr.update(choices=choices, value=choices[-1][1] if choices else None),
                      results_col=gr.update(visible=bool(choices)))
    return pack(values)


def no_change(message=None, failed=False):
    """Production outputs that only show a message in the progress bar."""
    return pack({"progress": progress_bar_html(message, 0, failed=failed)} if message else {})


def new_reel_ui():
    """«New reel»: the composer comes back; the finished or paused reel stays in «My reels»."""
    return pack({"progress": progress_bar_html(), "step": "", "stepper": "", "video": None, "variants": None,
                 "reel": None, "review": None, "panel": gr.update(visible=False), "final": gr.update(visible=False),
                 "composer": gr.update(visible=True), "summary": gr.update(visible=False), "summary_text": "",
                 "results_choice": gr.update(choices=[], value=None), "results_col": gr.update(visible=False)})


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
    yield pack(dict(zip(["review", "panel", "title", "storyboard_box", "storyboard_text", "items_box"],
                        review_updates(None)),
                    progress=progress_bar_html("Starting…", 0), step=step_html(f"Live log also in {ReelRun.LOG_FILE}"),
                    stepper=stepper_html({"storyboard": "running"}), video=None, variants=None, tech="",
                    final=gr.update(visible=False)))
    settings = form_settings(values)
    if drive_responds():                       # never block the run on a stale Drive just to remember choices
        save_prefs(settings)
    yield from start_job_ui("produce", settings["idea"][:60], lambda: produce_reel(settings))


def resume_ui(reel_dir, *values):
    """Continues the current reel with the form's settings: finished steps are reused, nothing is lost."""
    if not reel_dir or not os.path.isdir(reel_dir):
        yield no_change("There is no reel to resume: pick one in «📁 My reels».", failed=True)
        return
    settings = form_settings(values)
    settings["idea"] = load_settings(reel_dir).get("idea") or settings["idea"] or os.path.basename(reel_dir)
    yield from start_job_ui("produce", f"{os.path.basename(reel_dir)} (resumed)",
                            lambda: produce_reel(settings, resume_dir=reel_dir))


def confirm_ui(reel_dir, edited_storyboard=None):
    if not reel_dir or not os.path.isdir(reel_dir):
        yield no_change("There is no reel waiting for review.", failed=True)
        return
    yield from start_job_ui("produce", f"{os.path.basename(reel_dir)} (confirmed)",
                            lambda: continue_reel(reel_dir, edited_storyboard))


def regenerate_storyboard_ui(reel_dir, notes, edited):
    if not reel_dir or not os.path.isdir(reel_dir):
        yield no_change("There is no reel waiting for review.", failed=True)
        return
    yield from start_job_ui("regenerate", f"{os.path.basename(reel_dir)} · storyboard",
                            lambda: regenerate_storyboard(reel_dir, notes or "", edited or ""))


def regenerate_item_ui(reel_dir, item_id, text):
    if not reel_dir or not os.path.isdir(reel_dir):
        yield no_change("There is no reel waiting for review.", failed=True)
        return
    yield from start_job_ui("regenerate", f"{os.path.basename(reel_dir)} · {item_id}",
                            lambda: regenerate_item(reel_dir, item_id, text))


def regenerate_many_ui(reel_dir, pairs):
    """«Regenerate selected»: the chosen items one after the other in a single job, each with its own text."""
    if not reel_dir or not os.path.isdir(reel_dir):
        yield no_change("There is no reel waiting for review.", failed=True)
        return
    if not pairs:
        yield no_change("Select at least one item to regenerate.", failed=True)
        return

    def many():
        for item_id, text in pairs:
            yield from regenerate_item(reel_dir, item_id, text)
    yield from start_job_ui("regenerate", f"{os.path.basename(reel_dir)} · {len(pairs)} item(s)", many)


def attach_ui():
    """Shows the job running in the background (or the last one of this session), review panel included."""
    job = jobs.current()
    if job is None:
        yield no_change()
        return
    yield from follow_ui(job)


def stop_ui():
    job = jobs.current()
    if job is None or not job.running:
        return progress_bar_html("Nothing is running", 0)
    job.cancel()
    return progress_bar_html("Stopping… (the step in progress finishes first)", 0, failed=True)


# ---------------------------------------------------------------------------
# Reusable settings: presets (Drive/config/presets) and the settings.json of every reel
# ---------------------------------------------------------------------------
PRESETS_DIR = f"{CONFIG_DIR}/presets"


def list_presets():
    return sorted(f[:-5] for f in os.listdir(PRESETS_DIR) if f.endswith(".json")) if os.path.isdir(PRESETS_DIR) else []


def save_preset(name, settings):
    name = re.sub(r"\s+", " ", re.sub(r"[^\w .-]+", "", name or "", flags=re.UNICODE)).strip()
    if not name:
        return "Write a name for the preset.", gr.update()
    os.makedirs(PRESETS_DIR, exist_ok=True)
    data = {k: v for k, v in settings.items() if k not in SETTINGS_SKIPPED and not k.startswith("_")}
    data.pop("confirm_paid", None)                 # a preset never pre-approves a charge
    data.pop("idea", None)                         # a preset is a way of making reels, not one reel
    with open(f"{PRESETS_DIR}/{name}.json", "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return f"✅ Preset «{name}» saved in Drive.", gr.update(choices=list_presets(), value=name)


def save_preset_ui(name, *values):
    return save_preset(name, form_settings(values))


FORM_EXTRAS = 3      # after the form inputs: footage note, local-AI box, kit section


def settings_updates(saved, keep_idea=True):
    """Updates for every production input (PRODUCE_KEYS + montage, in order) plus the footage note, the
    local-AI settings box and the kit section."""
    p = normalize_settings(saved)
    clip = CLIP_PROVIDERS[p["clip_provider"]]
    kind = clip["type"]
    model = p["llm_model"] or default_model_for(llm_key_for(p["llm_provider"]))
    voices = voice_choices(p["voice_provider"])
    if p["voice"] and p["voice"] not in [v for _, v in voices]:
        voices = [(p["voice"], p["voice"])] + voices
    values = {
        "idea": gr.update(value=p["idea"]) if keep_idea and p["idea"] else gr.update(),
        "duration": gr.update(value=p["duration"]), "style": gr.update(value=p["style"]),
        "tone": gr.update(value=p["tone"]), "instructions": gr.update(value=p["instructions"]),
        "num_scenes": gr.update(value=p["num_scenes"]),
        "llm_provider": gr.update(choices=llm_names_by_kind(p["llm_type"]), value=p["llm_provider"]),
        "llm_model": gr.update(choices=[model] if model else [], value=model),
        "image_provider": gr.update(value=p["image_provider"]), "make_cover": gr.update(value=p["make_cover"]),
        "use_keyframes": gr.update(value=p["use_keyframes"], visible=bool(clip.get("accepts_image"))),
        "clip_type": gr.update(value=kind),
        "clip_provider": gr.update(choices=clip_provider_choices(kind), value=p["clip_provider"]),
        "confirm_paid": gr.update(visible=kind == "external_ai", value=False),   # a charge is never pre-approved
        "own_files": gr.update(visible=p["clip_provider"] == "upload", value=None),
        "voice_type": gr.update(value=p["voice_type"]),
        "voice_provider": gr.update(choices=voice_provider_choices(p["voice_type"]), value=p["voice_provider"]),
        "voice": gr.update(choices=voices, value=p["voice"] or (voices[0][1] if voices else "")),
        "clone_sample": gr.update(visible=p["voice_provider"] == "f5", value=None),
        "scene_sound": gr.update(value=p["scene_sound"]), "music_source": gr.update(value=p["music_source"]),
        "make_kit": gr.update(value=p["make_kit"]), "review_steps": gr.update(value=p["review_steps"]),
        "ai_width": gr.update(value=p["ai_width"]), "ai_height": gr.update(value=p["ai_height"]),
        "ai_frames": gr.update(value=p["ai_frames"]), "ai_steps": gr.update(value=p["ai_steps"]),
    }
    updates = [values[k] for k in PRODUCE_KEYS] + [gr.update(value=p["montage"][k]) for k in MONTAGE_KEYS]
    return updates + [gr.update(visible=p["clip_provider"] == "footage", value=footage_note()),
                      gr.update(visible=kind == "local_ai"), gr.update(visible=p["make_kit"])]


def blank_form(*messages):
    return [gr.update() for _ in range(len(PRODUCE_KEYS) + len(MONTAGE_KEYS) + FORM_EXTRAS)] + list(messages)


def load_preset_ui(name):
    if not name or name not in list_presets():
        return blank_form("Pick a saved preset.", gr.update())
    data = read_json(f"{PRESETS_DIR}/{name}.json")
    return settings_updates(data, keep_idea=False) + [f"✅ Settings of the preset «{name}» loaded.", gr.update()]


def use_reel_settings_ui(folder):
    """Loads a reel's settings into the production form and points «Resume» at that reel."""
    if not folder or not os.path.isdir(folder):
        return blank_form("Pick a reel first.", gr.update(), gr.update())
    saved = load_settings(folder)
    if not saved:
        return blank_form("This reel has no saved settings.", gr.update(), gr.update())
    return settings_updates(saved) + [f"✅ Settings of «{os.path.basename(folder)}» loaded in «🎬 Produce».",
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
        return "There were no changes to save."
    save_api_keys(keys)
    return f"✅ Saved in Drive ({len(changed)} field(s)): {', '.join(changed)}"


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
        return "Write a name for the service (for example groq, openrouter, together).", gr.update(), gr.update()
    if not base_url.strip():
        return "The Base URL is required (for example https://api.groq.com/openai/v1).", gr.update(), gr.update()
    keys = ensure_api_keys_file()
    keys["openai_compatible"][name] = {"api_key": api_key.strip(), "base_url": base_url.strip(),
                                       "chat_model": chat_model.strip()}
    save_api_keys(keys)
    return (f"✅ Saved [openai_compatible.{name}] in Drive.", gr.update(value="external"),
            gr.update(choices=llm_names_by_kind("external"), value=f"{name} (OpenAI-compatible)"))


# ---------------------------------------------------------------------------
# Reels tab
# ---------------------------------------------------------------------------
def list_reels():
    """Every reel folder, newest first, with how its last run ended (done, waiting for review, failed...)."""
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
        stage = f" · {state['stage']}" if status in ("failed", "stopped", "review") and state.get("stage") else ""
        out.append((f"{label} · {os.path.basename(folder)}{stage}", folder))
    return out


def final_video(folder):
    path = f"{folder}/{os.path.basename(folder)}_FINAL.mp4"
    return path if os.path.exists(path) else None


def show_reel(folder):
    if not folder or not os.path.isdir(folder):
        return (None, "", None, "", None) + tuple(kit_updates(None))
    cover = f"{folder}/cover.png" if os.path.exists(f"{folder}/cover.png") else None
    kit = load_publish_kit(folder)
    state = read_run_state(folder)
    notes = []
    if state.get("status") == "review":
        notes.append(f"**{RUN_STATES['review']}** ({state.get('stage', '?')}). Press **▶️ Open in «Produce»** "
                     "to review it and go on.")
    elif state.get("status") in ("failed", "stopped", "running"):
        notes.append(f"**{RUN_STATES.get(state['status'], state['status'])}** at «{state.get('stage', '?')}»"
                     + (f": {state['error']}" if state.get("error") else "")
                     + ". Press **▶️ Open in «Produce»** to continue from there without repeating what is done.")
    variants = len(glob.glob(f"{folder}/*_FINAL*.mp4"))
    if variants > 1:
        notes.append(f"{variants} variants in the reel folder.")
    if not kit and os.path.exists(f"{folder}/plan.json"):
        notes.append("This reel has no kit yet: use «Regenerate the kit».")
    return (final_video(folder), storyboard_text(folder), cover, "  \n".join(notes), load_settings(folder) or None) + \
        tuple(kit_updates(kit))


def regenerate_kit(path, llm_name, llm_model):
    """New kit for a saved reel, written by the LLM currently selected in the production tab."""
    folder = reel_folder(path)
    if not folder or not os.path.isdir(folder):
        return ("Pick a reel first.",) + tuple(gr.update() for _ in range(KIT_COUNT))
    try:
        plan = load_plan(folder)
        if not plan:
            raise RuntimeError("this reel has no storyboard yet.")
        idea = load_settings(folder).get("idea") or os.path.basename(folder).replace("-", " ")
        kit = generate_publish_kit(llm_key_for(llm_name), llm_model, idea, plan)
        save_publish_kit(kit, folder)
        save_assets(folder, hooks=kit.get("on_screen_hooks", []))
        return (f"✅ Kit regenerated with {llm_name} and saved in Drive.",) + tuple(kit_updates(kit))
    except Exception as exc:
        return (f"❌ Could not regenerate it: {exc}",) + tuple(gr.update() for _ in range(KIT_COUNT))


def open_reel_ui(folder):
    """▶️ Open in «Produce»: a reel waiting for review shows its review panel there; any other unfinished reel
    continues with its own settings. The first output switches to the Produce tab."""
    if not folder or not os.path.isdir(folder):
        yield (gr.update(),) + no_change("Pick a reel first.", failed=True)
        return
    yield (gr.Tabs(selected="produce"),) + no_change()
    state = read_run_state(folder)
    if state.get("status") == "review" and not (jobs.current() and jobs.current().running):
        review = review_payload(folder)
        snap = {"stage": f"Review the {STAGES.get(review['stage'], review['stage'])}", "pct": 0, "failed": False,
                "done": False, "log": f"👀 {os.path.basename(folder)} is waiting for your review.",
                "script": storyboard_text(folder), "media": [], "video": final_video(folder), "videos": [],
                "kit": load_publish_kit(folder), "tech": "", "reel_dir": folder, "review": review}
        yield (gr.update(),) + snapshot_outputs(snap, {})
        return
    if state.get("status") == "done" and final_video(folder):
        yield (gr.update(),) + no_change("This reel is done: re-assemble or publish it in «My reels».")
        return
    for outputs in start_job_ui("produce", f"{os.path.basename(folder)} (continued)", lambda: continue_reel(folder)):
        yield (gr.update(),) + outputs


def save_reel_preset_ui(folder, name):
    saved = load_settings(folder) if folder else {}
    if not saved:
        return "Pick a reel with saved settings.", gr.update()
    return save_preset(name, saved)


def reel_job_outputs(outputs):
    """Production outputs -> the reels tab's (progress bar, current step, video)."""
    video = outputs[OUT["video"]]
    return outputs[OUT["progress"]], outputs[OUT["step"]], video if video else gr.update()


def reassemble_ui(path, *montage_values):
    folder = reel_folder(path)
    yield progress_bar_html("Starting…", 0), "", gr.update()
    montage = montage_options(dict(zip(MONTAGE_KEYS, montage_values)))
    for outputs in start_job_ui("reassemble", f"{os.path.basename(folder or '')} (new montage)",
                                lambda: reassemble_reel(folder, montage)):
        yield reel_job_outputs(outputs)


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------
def modal(title):
    """A window over the page: returns (outer column, card column). Shown with visible=True."""
    outer = gr.Column(visible=False, elem_classes=["modal"])
    with outer:
        card = gr.Column(elem_classes=["modal-card"])
        with card:
            gr.Markdown(f"### {title}")
    return outer, card


def build_ui():
    """Builds the Control Center. Returns (blocks, components by name)."""
    FONTS[:] = font_families()
    KEY_COMPONENTS.clear()
    KEY_SHOWN.clear()
    prefs = load_prefs()
    m = prefs["montage"]
    initial_model = prefs["llm_model"] or default_model_for(llm_key_for(prefs["llm_provider"]))
    initial_voices = voice_choices(prefs["voice_provider"])
    initial_voice = prefs["voice"] if prefs["voice"] in [v for _, v in initial_voices] else \
        (initial_voices[0][1] if initial_voices else "")
    clip = CLIP_PROVIDERS[prefs["clip_provider"]]
    labels = chip_labels(prefs["llm_provider"], initial_model, prefs["image_provider"], prefs["voice_provider"],
                         initial_voice, prefs["clip_provider"], m["aspect"], prefs["duration"], prefs["style"])
    production_outputs = []          # filled below, once every output exists (the review rows refer to it)

    with gr.Blocks(title="YT-Channel-Studio") as control_center:
        with gr.Tabs() as tabs:
            # ============================================================= produce
            with gr.Tab("🎬 Produce", id="produce"):
                with gr.Column(elem_classes=["main"]):
                    with gr.Column(elem_classes=["composer"]) as composer_box:
                        idea_in = gr.Textbox(show_label=False, container=False, lines=2, max_lines=6,
                                             placeholder="Describe your reel idea, in the language the video will "
                                                         "speak… (e.g. why wolves hunt in packs)")
                        with gr.Row(elem_classes=["chips"]):
                            llm_chip, image_chip, voice_chip, video_chip, format_chip, duration_chip, style_chip = [
                                gr.Button(label, size="sm", elem_classes=["chip"]) for label in labels]
                            settings_chip = gr.Button("⚙️ Settings", size="sm", elem_classes=["chip"])
                            produce_btn = gr.Button("➤", variant="primary", size="sm", elem_classes=["send"])
                        status_box = gr.HTML()
                    with gr.Row(visible=False, elem_classes=["summary-row"]) as summary_box:
                        summary_out = gr.HTML()
                        new_reel_btn = gr.Button("✏️ New reel", size="sm", elem_classes=["new-reel"])
                    stepper_out = gr.HTML()

                    with gr.Row(equal_height=False, elem_classes=["workspace"]):
                        # ------------------------------------------- the step at hand (one panel at a time)
                        with gr.Column(scale=3, min_width=360, elem_classes=["active"]):
                            progress_box = gr.HTML(progress_bar_html())
                            step_md = gr.HTML()
                            with gr.Row(elem_classes=["actions"]):
                                stop_btn = gr.Button("⏹ Stop", size="sm")
                                resume_btn = gr.Button("🔁 Resume where it failed", size="sm")
                                attach_btn = gr.Button("📡 Show the job in progress", size="sm")
                                show_tech_cb = gr.Checkbox(value=prefs["show_tech"], label="🔧 Show the technical log",
                                                           container=False)
                            tech_out = gr.Textbox(show_label=False, lines=14, max_lines=14, autoscroll=True,
                                                  interactive=False, elem_classes=["tech-log"],
                                                  visible=prefs["show_tech"], **copy_kwargs())

                            review_state = gr.State(None)
                            reel_state = gr.State(None)
                            detail_state = gr.State(None)
                            with gr.Column(visible=False, elem_classes=["review"]) as review_panel:
                                review_title = gr.Markdown()
                                with gr.Column(visible=False) as storyboard_box:
                                    storyboard_tb = gr.Textbox(show_label=False, lines=22, max_lines=60,
                                                               interactive=True, elem_classes=["storyboard"],
                                                               **copy_kwargs())
                                    notes_tb = gr.Textbox(label="What should change? (optional, used by «Regenerate»)",
                                                          lines=1, placeholder="e.g. more humor, a scene with the cubs")
                                    with gr.Row(elem_classes=["actions"]):
                                        regen_storyboard_btn = gr.Button("🔄 Regenerate the storyboard", size="sm")
                                        confirm_storyboard_btn = gr.Button("✅ Confirm the storyboard",
                                                                           variant="primary", size="sm")
                                with gr.Column(visible=False) as items_box:
                                    @gr.render(inputs=[review_state])
                                    def review_rows(review):
                                        if not review or review.get("stage") == "storyboard":
                                            return
                                        items = review.get("items", [])
                                        checks, texts, ids = [], [], [item["id"] for item in items]
                                        with gr.Row(elem_classes=["actions"]):
                                            select_all = gr.Button("☑️ Select all", size="sm")
                                            regen_selected = gr.Button("🔄 Regenerate selected", size="sm",
                                                                       elem_classes=["regen-selected"])
                                        with gr.Row(elem_classes=["cards"]):
                                            for index, item in enumerate(items):
                                                with gr.Column(min_width=220, elem_classes=["card"]):
                                                    item_media(item, height=220)
                                                    gr.Markdown(f"**`{item['id']}`** · {html.escape(item['label'])}",
                                                                elem_classes=["card-label"])
                                                    checks.append(gr.Checkbox(value=False, label="Select",
                                                                              container=False))
                                                    text = gr.Textbox(value=item["text"], label=item["text_label"],
                                                                      lines=2, interactive=True)
                                                    texts.append(text)
                                                    with gr.Row(elem_classes=["actions"]):
                                                        view = gr.Button("🔍 View", size="sm",
                                                                         elem_classes=["view"])
                                                        button = gr.Button(f"🔄 Regenerate {item['id']}", size="sm",
                                                                           elem_classes=["regen"])
                                                    view.click(lambda _i=index, _items=items: detail_updates(_items, _i),
                                                               None, detail_outputs)

                                                    def regenerate(reel, new_text, _id=item["id"]):
                                                        yield from regenerate_item_ui(reel, _id, new_text)
                                                    button.click(regenerate, [reel_state, text], production_outputs,
                                                                 concurrency_limit=None)
                                        select_all.click(lambda: [gr.update(value=True) for _ in checks], None, checks)

                                        def regenerate_selected(reel, *values, _ids=tuple(ids)):
                                            flags, new_texts = values[:len(_ids)], values[len(_ids):]
                                            pairs = [(i, t) for i, f, t in zip(_ids, flags, new_texts) if f]
                                            yield from regenerate_many_ui(reel, pairs)
                                        regen_selected.click(regenerate_selected, [reel_state] + checks + texts,
                                                             production_outputs, concurrency_limit=None)
                                    confirm_items_btn = gr.Button("✅ Confirm and continue", variant="primary")

                            # ------------------------------------------- the finished reel and publishing
                            with gr.Column(visible=False, elem_classes=["final"]) as final_section:
                                gr.Markdown("### ✅ Your reel is ready\nThe fast way to get the file is your Drive "
                                            "(`YT-Channel-Studio/reels/`); the download button here goes through the "
                                            "tunnel and is slower.")
                                video_out = gr.Video(label="📱 Final reel", height=520)
                                variants_out = gr.File(label="🎞️ All variants", file_count="multiple",
                                                       interactive=False)
                                with gr.Column(visible=prefs["make_kit"]) as kit_section:
                                    with gr.Accordion("📣 Publish kit", open=False):
                                        kit_panel = build_kit_panel()
                                with gr.Accordion("📤 Publish (Upload-Post)", open=True):
                                    publish_btn, publish_inputs, publish_out = build_publish_panel()

                        # ------------------------------------------- earlier results, readable while a step runs
                        with gr.Column(scale=2, min_width=300, visible=False, elem_classes=["results"]) as results_col:
                            gr.Markdown("**Earlier results** · open a finished step without leaving this one",
                                        elem_classes=["results-title"])
                            results_choice = gr.Radio([], value=None, show_label=False, container=False,
                                                      elem_classes=["results-tabs"])

                            @gr.render(inputs=[reel_state, results_choice])
                            def results_view(reel, step):
                                items = step_items(reel, step) if reel and step else []
                                if not items:
                                    gr.Markdown("*(nothing to show yet)*")
                                    return
                                with gr.Row(elem_classes=["cards", "cards-small"]):
                                    for index, item in enumerate(items):
                                        with gr.Column(min_width=140, elem_classes=["card", "card-small"]):
                                            item_media(item, height=150)
                                            gr.Markdown(html.escape(item["label"]), elem_classes=["card-label"])
                                            view = gr.Button("🔍 View", size="sm", elem_classes=["view"])
                                            view.click(lambda _i=index, _items=items: detail_updates(_items, _i),
                                                       None, detail_outputs)

                    # ----------------------------------------------- one item in detail (images, clips, voices...)
                    with gr.Column(visible=False, elem_classes=["modal", "detail-modal"]) as detail_modal:
                        with gr.Column(elem_classes=["modal-card", "detail-card"]):
                            detail_title = gr.Markdown()
                            detail_image = gr.Image(show_label=False, interactive=False, visible=False, height=560)
                            detail_video = gr.Video(show_label=False, interactive=False, visible=False, height=560)
                            detail_audio = gr.Audio(show_label=False, interactive=False, visible=False)
                            detail_text = gr.Markdown(elem_classes=["detail-text"])
                            with gr.Row(elem_classes=["actions", "detail-nav"]):
                                detail_prev = gr.Button("← Previous", size="sm")
                                detail_close = gr.Button("✕ Close", size="sm", variant="primary",
                                                         elem_classes=["detail-close"])
                                detail_next = gr.Button("Next →", size="sm")
                    detail_outputs = [detail_state, detail_modal, detail_title, detail_image, detail_video,
                                      detail_audio, detail_text, detail_prev, detail_next]

                # ---------------------------------------------------- picker windows
                llm_modal, card = modal("🧠 Text: storyboard and publish kit")
                with card:
                    llm_type_rd = gr.Radio([(v, k) for k, v in KIND_LABELS.items()], value=prefs["llm_type"],
                                           label="Type")
                    with gr.Row():
                        llm_dd = gr.Dropdown(llm_names_by_kind(prefs["llm_type"]), value=prefs["llm_provider"],
                                             label="Provider")
                        model_dd = gr.Dropdown([initial_model] if initial_model else [], value=initial_model,
                                               label="Model (editable)", allow_custom_value=True)
                    with gr.Row():
                        models_btn = gr.Button("🔄 Show the available models", size="sm")
                        models_note = gr.Markdown()
                    llm_done = gr.Button("✔ Done", variant="primary")

                image_modal, card = modal("🖼️ Image")
                with card:
                    gr.Markdown("The same engine makes the cover, the first frame of the clips that accept one and "
                                "the AI photos.")
                    image_rd = gr.Radio([(v["label"], k) for k, v in IMAGE_PROVIDERS.items()],
                                        value=prefs["image_provider"], label="Image engine")
                    image_done = gr.Button("✔ Done", variant="primary")

                voice_modal, card = modal("🎙️ Voice")
                with card:
                    gr.Markdown("The voice reads the narration of every scene in the language of your idea: pick a "
                                "voice that speaks it (or a multilingual one).")
                    voice_type_rd = gr.Radio([(v, k) for k, v in VOICE_TYPES.items()], value=prefs["voice_type"],
                                             label="Type")
                    with gr.Row():
                        voice_provider_dd = gr.Dropdown(voice_provider_choices(prefs["voice_type"]),
                                                        value=prefs["voice_provider"], label="Provider")
                        voice_dd = gr.Dropdown(initial_voices, value=initial_voice, label="Voice (editable)",
                                               allow_custom_value=True)
                    clone_in = gr.Audio(label="🧬 5-10 s sample to clone", type="filepath",
                                        visible=prefs["voice_provider"] == "f5")
                    voice_done = gr.Button("✔ Done", variant="primary")

                video_modal, card = modal("🎞️ Video: where the clips come from")
                with card:
                    clip_type_rd = gr.Radio([(v, k) for k, v in CLIP_TYPES.items()], value=prefs["clip_type"],
                                            label="Type")
                    clip_dd = gr.Dropdown(clip_provider_choices(prefs["clip_type"]), value=prefs["clip_provider"],
                                          label="Provider")
                    paid_cb = gr.Checkbox(value=False, label="💳 I accept the charge of this provider for every clip",
                                          visible=clip["type"] == "external_ai")
                    own_files_in = gr.File(label="Your clips or photos (one per scene, in this order, repeating)",
                                           file_count="multiple", file_types=["video", "image"],
                                           visible=prefs["clip_provider"] == "upload")
                    footage_md = gr.Markdown(visible=prefs["clip_provider"] == "footage")
                    video_done = gr.Button("✔ Done", variant="primary")

                format_modal, card = modal("📐 Format")
                with card:
                    aspect_rd = gr.Radio([("9:16 vertical (Reels, Shorts, TikTok)", "9:16"),
                                          ("16:9 horizontal (YouTube)", "16:9"), ("1:1 square", "1:1")],
                                         value=m["aspect"], label="Video format")
                    format_done = gr.Button("✔ Done", variant="primary")

                duration_modal, card = modal("⏱️ Length")
                with card:
                    duration_sl = gr.Slider(15, 90, value=prefs["duration"], step=5, label="Seconds of narration")
                    duration_done = gr.Button("✔ Done", variant="primary")

                style_modal, card = modal("🎭 Style")
                with card:
                    style_rd = gr.Radio([(v, k) for k, v in STYLES.items()], value=prefs["style"],
                                        label="How the reel looks and is told")
                    style_done = gr.Button("✔ Done", variant="primary")

                settings_modal, card = modal("⚙️ Settings")
                with card:
                    with gr.Accordion("📂 Presets: saved settings to reuse", open=False):
                        with gr.Row():
                            preset_dd = gr.Dropdown(list_presets(), label="Preset", scale=3)
                            preset_load_btn = gr.Button("📥 Load", size="sm", scale=1)
                        with gr.Row():
                            preset_name = gr.Textbox(label="Save the current settings as", scale=3)
                            preset_save_btn = gr.Button("💾 Save preset", size="sm", scale=1)
                        preset_msg = gr.Markdown()
                    with gr.Group():
                        gr.Markdown("**🎬 Storyboard** · written in the language of your idea",
                                    elem_classes=["section-title"])
                        review_cb = gr.Checkbox(value=prefs["review_steps"],
                                                label="👀 Review each step before going on (storyboard, images, "
                                                      "audio, clips)")
                        with gr.Row():
                            tone_dd = gr.Dropdown(TONE_CHOICES, value=prefs["tone"], label="Tone")
                            scenes_dd = gr.Dropdown(SCENE_CHOICES, value=prefs["num_scenes"], label="Scenes")
                        instructions_in = gr.Textbox(label="Extra instructions (optional)", lines=1,
                                                     value=prefs["instructions"],
                                                     placeholder="e.g. end with a question; mention my channel @...")
                    with gr.Group():
                        gr.Markdown("**🖼️ Images and kit**", elem_classes=["section-title"])
                        cover_cb = gr.Checkbox(value=prefs["make_cover"], label="Make the cover")
                        keyframes_cb = gr.Checkbox(value=prefs["use_keyframes"],
                                                   label="Use an image as the first frame of each AI clip",
                                                   visible=bool(clip.get("accepts_image")))
                        kit_cb = gr.Checkbox(value=prefs["make_kit"],
                                             label="Make the publish kit (titles, captions, hashtags and hooks)")
                    with gr.Group(visible=clip["type"] == "local_ai") as ai_box:
                        gr.Markdown("**🖥️ Local AI video** · width and height snap to multiples of 32 and to the "
                                    "format; each clip is made as long as its scene, up to the frame limit (24 per "
                                    "second)", elem_classes=["section-title"])
                        with gr.Row():
                            ai_w = gr.Number(value=prefs["ai_width"], label="Width", precision=0)
                            ai_h = gr.Number(value=prefs["ai_height"], label="Height", precision=0)
                            ai_f = gr.Number(value=prefs["ai_frames"], label="Max frames per clip", precision=0)
                            ai_s = gr.Number(value=prefs["ai_steps"], label="Steps", precision=0)
                    with gr.Group():
                        gr.Markdown("**🔊 Sound of the scenes**", elem_classes=["section-title"])
                        sound_rd = gr.Radio([(v["label"], k) for k, v in SCENE_SOUNDS.items()],
                                            value=prefs["scene_sound"], show_label=False)
                    with gr.Group():
                        gr.Markdown("**🎵 Background music**", elem_classes=["section-title"])
                        music_rd = gr.Radio([(v["label"], k) for k, v in MUSIC_SOURCES.items()],
                                            value=prefs["music_source"], show_label=False)
                    with gr.Accordion("🛠️ Montage and subtitles", open=False):
                        montage_inputs = build_montage_controls(m, aspect=aspect_rd)
                    settings_done = gr.Button("✔ Done", variant="primary")

            # ============================================================= keys
            with gr.Tab("🔑 API keys", id="keys"):
                gr.Markdown(f"Saved in `{KEYS_FILE}` (plain text in your Drive). "
                            "**Test** uses what is typed, even before you save it.")
                saved_keys = ensure_api_keys_file()
                for category, category_label in CREDENTIAL_CATEGORIES:
                    sections = [s for s, c in CREDENTIALS.items() if c["category"] == category]
                    with gr.Accordion(category_label, open=category == "llm"):
                        for section in sections:
                            cred = CREDENTIALS[section]
                            gr.Markdown(f"**{cred['label']}** · [get a key]({cred['link']})")
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
                                test_btn = gr.Button("🔌 Test the connection", size="sm", scale=1)
                                test_out = gr.HTML(scale=4)
                            test_btn.click(make_test_handler(section), field_components, test_out)
                        if category == "llm":
                            gr.Markdown("**OpenAI-compatible services** (Groq, OpenRouter, DeepSeek, Together...). "
                                        "Pick one to edit it or type a new name to add it.")
                            first_compat = (compat_provider_names() or [""])[0]
                            with gr.Row():
                                compat_name = gr.Dropdown(compat_provider_names(), value=first_compat,
                                                          allow_custom_value=True, label="Service (name)")
                                compat_key = gr.Textbox(label="API key", value=get_compat(first_compat, "api_key"),
                                                        type="password")
                                compat_url = gr.Textbox(label="Base URL", value=get_compat(first_compat, "base_url"))
                                compat_chat = gr.Textbox(label="Default model",
                                                         value=get_compat(first_compat, "chat_model"))
                            with gr.Row():
                                compat_test_btn = gr.Button("🔌 Test the connection", size="sm", scale=1)
                                compat_save_btn = gr.Button("💾 Save this service", size="sm", scale=1)
                                compat_msg = gr.HTML(scale=3)
                save_btn = gr.Button("💾 Save every key in Google Drive", variant="primary")
                save_msg = gr.Textbox(label="Confirmation", lines=2, interactive=False)

            # ============================================================= reels
            with gr.Tab("📁 My reels", id="reels"):
                with gr.Row():
                    reels_dd = gr.Dropdown(list_reels(), scale=4,
                                           label="Reel saved in Drive (✅ done · 👀 waiting for review · ❌ failed · "
                                                 "⏹ stopped)")
                    reels_refresh = gr.Button("🔄 Refresh the list", scale=1)
                with gr.Row():
                    open_reel_btn = gr.Button("▶️ Open in «Produce» (review or continue it)", variant="primary",
                                              size="sm")
                    use_settings_btn = gr.Button("♻️ Use these settings in «Produce»", size="sm")
                reel_note = gr.Markdown()
                reel_progress = gr.HTML(progress_bar_html())
                reel_step = gr.HTML()
                with gr.Row():
                    reel_video = gr.Video(label="Reel")
                    with gr.Column():
                        reel_cover = gr.Image(label="Cover", height=260)
                        reel_script = gr.Textbox(label="Storyboard", lines=10, interactive=False, **copy_kwargs())
                with gr.Accordion("⚙️ Settings it was made with", open=False):
                    reel_settings = gr.JSON(show_label=False)
                    with gr.Row():
                        reel_preset_name = gr.Textbox(label="Save them as a preset named", scale=3)
                        reel_preset_btn = gr.Button("💾 Save preset", size="sm", scale=1)
                    reel_preset_msg = gr.Markdown()
                regen_btn = gr.Button("🔁 Regenerate the kit with the LLM chosen in «Produce»", size="sm")
                with gr.Accordion("🛠️ Re-assemble (another format, subtitles, transitions or volumes; nothing is "
                                  "generated again)", open=False):
                    reel_montage_inputs = build_montage_controls(m)
                    reassemble_btn = gr.Button("🎬 Re-assemble this reel", variant="primary")
                with gr.Accordion("📤 Publish (Upload-Post)", open=False):
                    reel_publish_btn, reel_publish_inputs, reel_publish_out = build_publish_panel()
                reel_kit_panel = build_kit_panel()

        # ----------------------------------------------------------------- wiring
        production_outputs += [progress_box, step_md, stepper_out, video_out, variants_out] + kit_panel + \
            [tech_out, reel_state, review_state, review_panel, review_title, storyboard_box, storyboard_tb, items_box,
             final_section, composer_box, summary_box, summary_out, results_choice, results_col]
        assert len(production_outputs) == OUTPUT_COUNT
        assert len(detail_outputs) == DETAIL_COUNT
        detail_prev.click(lambda state: detail_step(state, -1), detail_state, detail_outputs)
        detail_next.click(lambda state: detail_step(state, 1), detail_state, detail_outputs)
        detail_close.click(close_detail, None, detail_outputs)
        new_reel_btn.click(new_reel_ui, None, production_outputs)

        chips = [llm_chip, image_chip, voice_chip, video_chip, format_chip, duration_chip, style_chip]
        chip_inputs = [llm_dd, model_dd, image_rd, voice_provider_dd, voice_dd, clip_dd, aspect_rd, duration_sl, style_rd]
        for chip, window, done in ((llm_chip, llm_modal, llm_done), (image_chip, image_modal, image_done),
                                   (voice_chip, voice_modal, voice_done), (video_chip, video_modal, video_done),
                                   (format_chip, format_modal, format_done),
                                   (duration_chip, duration_modal, duration_done),
                                   (style_chip, style_modal, style_done),
                                   (settings_chip, settings_modal, settings_done)):
            chip.click(lambda: gr.update(visible=True), None, window)
            done.click(lambda: gr.update(visible=False), None, window).then(refresh_chips, chip_inputs, chips)
        control_center.load(refresh_chips, chip_inputs, chips)

        # The cascading pickers react to the USER only (.input), so loading saved settings into the form is
        # never undone by them; a type change chains to the provider handler explicitly.
        clip_outputs = [paid_cb, own_files_in, footage_md, keyframes_cb, ai_box]
        llm_type_rd.input(on_llm_type_change, llm_type_rd, llm_dd).then(on_llm_change, llm_dd, model_dd)
        llm_dd.input(on_llm_change, llm_dd, model_dd)
        models_btn.click(refresh_models, [llm_dd, model_dd], [model_dd, models_note])
        clip_type_rd.input(on_clip_type_change, clip_type_rd, clip_dd).then(clip_visibility, clip_dd, clip_outputs)
        clip_dd.input(clip_visibility, clip_dd, clip_outputs)
        voice_type_rd.input(on_voice_type_change, voice_type_rd, voice_provider_dd).then(
            on_voice_provider_change, voice_provider_dd, [voice_dd, clone_in])
        voice_provider_dd.input(on_voice_provider_change, voice_provider_dd, [voice_dd, clone_in])
        kit_cb.change(lambda show: gr.update(visible=show), kit_cb, kit_section)
        show_tech_cb.change(lambda show: (save_show_tech(show), gr.update(visible=show))[1], show_tech_cb, tech_out)

        check_inputs = [llm_dd, clip_dd, voice_provider_dd, image_rd, music_rd, paid_cb, own_files_in, cover_cb,
                        keyframes_cb, sound_rd]
        for comp in check_inputs:
            comp.change(validate, check_inputs, status_box)
        control_center.load(validate, check_inputs, status_box)
        control_center.load(clip_visibility, clip_dd, clip_outputs)

        produce_inputs = [idea_in, duration_sl, style_rd, tone_dd, instructions_in, scenes_dd, llm_dd, model_dd,
                          image_rd, cover_cb, keyframes_cb, clip_type_rd, clip_dd, paid_cb, own_files_in,
                          voice_type_rd, voice_provider_dd, voice_dd, clone_in, sound_rd, music_rd, kit_cb, review_cb,
                          ai_w, ai_h, ai_f, ai_s] + montage_inputs
        assert len(produce_inputs) == len(PRODUCE_KEYS) + len(MONTAGE_KEYS)
        form_outputs = produce_inputs + [footage_md, ai_box, kit_section]
        # Following a job is just watching it: several tabs may watch at once, and closing one stops nothing.
        produce_btn.click(produce_ui, produce_inputs, production_outputs, concurrency_limit=None)
        idea_in.submit(produce_ui, produce_inputs, production_outputs, concurrency_limit=None)
        resume_btn.click(resume_ui, [reel_state] + produce_inputs, production_outputs, concurrency_limit=None)
        attach_btn.click(attach_ui, None, production_outputs, concurrency_limit=None)
        control_center.load(attach_ui, None, production_outputs, concurrency_limit=None)   # page reopened: reconnect
        stop_btn.click(stop_ui, None, progress_box)
        confirm_storyboard_btn.click(confirm_ui, [reel_state, storyboard_tb], production_outputs, concurrency_limit=None)
        regen_storyboard_btn.click(regenerate_storyboard_ui, [reel_state, notes_tb, storyboard_tb], production_outputs,
                                   concurrency_limit=None)
        confirm_items_btn.click(confirm_ui, [reel_state], production_outputs, concurrency_limit=None)
        publish_btn.click(publish_ui, [reel_state] + publish_inputs + kit_panel[:6], publish_out)
        preset_save_btn.click(save_preset_ui, [preset_name] + produce_inputs, [preset_msg, preset_dd])
        preset_load_btn.click(load_preset_ui, preset_dd, form_outputs + [preset_msg, preset_dd]).then(
            validate, check_inputs, status_box).then(refresh_chips, chip_inputs, chips)

        save_btn.click(save_all_keys, [c for _, _, c in KEY_COMPONENTS], save_msg).then(
            validate, check_inputs, status_box).then(on_voice_provider_change, voice_provider_dd, [voice_dd, clone_in])
        compat_name.change(load_compat_ui, compat_name, [compat_key, compat_url, compat_chat])
        compat_test_btn.click(test_compat_ui, [compat_url, compat_key], compat_msg)
        compat_save_btn.click(save_compat_ui, [compat_name, compat_key, compat_url, compat_chat],
                              [compat_msg, llm_type_rd, llm_dd]).then(on_llm_change, llm_dd, model_dd).then(
            refresh_chips, chip_inputs, chips)

        reels_refresh.click(lambda: gr.update(choices=list_reels()), None, reels_dd)
        reels_dd.change(show_reel, reels_dd, [reel_video, reel_script, reel_cover, reel_note, reel_settings] + reel_kit_panel)
        open_reel_btn.click(open_reel_ui, reels_dd, [tabs] + production_outputs, concurrency_limit=None)
        use_settings_btn.click(use_reel_settings_ui, reels_dd, form_outputs + [reel_note, reel_state, tabs]).then(
            validate, check_inputs, status_box).then(refresh_chips, chip_inputs, chips)
        reel_preset_btn.click(save_reel_preset_ui, [reels_dd, reel_preset_name], [reel_preset_msg, preset_dd])
        regen_btn.click(regenerate_kit, [reels_dd, llm_dd, model_dd], [reel_note] + reel_kit_panel)
        reassemble_btn.click(reassemble_ui, [reels_dd] + reel_montage_inputs, [reel_progress, reel_step, reel_video],
                             concurrency_limit=None)
        reel_publish_btn.click(publish_ui, [reels_dd] + reel_publish_inputs + reel_kit_panel[:6], reel_publish_out)
    return control_center, dict(locals())


UI_CSS = """
.main {max-width: 1240px; margin: 0 auto !important; width: 100%;}
.composer {max-width: 980px; width: 100%; margin: 0 auto !important;}
.composer {border: 1px solid var(--border-color-primary) !important; border-radius: 24px !important;
           padding: 14px 16px 10px 16px !important; background: var(--block-background-fill) !important;}
.composer textarea {font-size: 17px !important; border: none !important; box-shadow: none !important;
                    background: transparent !important;}
.chips {gap: 6px !important; flex-wrap: wrap !important; align-items: center !important;}
.chips > * {flex: 0 0 auto !important; min-width: 0 !important; width: auto !important;}
button.chip {border-radius: 999px !important; padding: 4px 12px !important; font-size: 13px !important;}
button.send {border-radius: 999px !important; min-width: 42px !important; width: 42px !important;
             height: 42px !important; margin-left: auto !important; font-size: 18px !important;}
.actions {gap: 6px !important; flex-wrap: wrap !important; align-items: center !important;}
.actions > * {flex: 0 0 auto !important; min-width: 0 !important; width: auto !important;}
.step-line {font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 13px; opacity: .85;
            padding: 2px 4px 6px 4px;}
.review {border: 1px solid #d97706 !important; border-radius: 18px !important; padding: 12px 14px !important;
         background: rgba(217, 119, 6, .06) !important;}
.storyboard textarea {font-family: ui-monospace, Menlo, Consolas, monospace !important; font-size: 13px !important;
                      line-height: 1.5 !important;}
button.regen {width: auto !important; align-self: flex-start;}
.modal {position: fixed !important; inset: 0 !important; z-index: 1000 !important; margin: 0 !important;
        max-width: none !important; background: rgba(0, 0, 0, .55) !important; overflow-y: auto !important;
        padding: 6vh 16px !important;}
.modal-card {max-width: 760px !important; width: 100% !important; margin: 0 auto !important; flex: 0 0 auto !important;
             background: var(--background-fill-primary) !important; border: 1px solid var(--border-color-primary) !important;
             border-radius: 18px !important; padding: 18px !important; box-shadow: 0 20px 60px rgba(0, 0, 0, .45) !important;}
.tech-log textarea {font-family: ui-monospace, Menlo, Consolas, monospace !important; font-size: 12px !important;}
.section-title {padding: 8px 12px 4px 12px !important;}
.summary-row {align-items: center !important; gap: 10px !important; border: 1px solid var(--border-color-primary);
              border-radius: 14px; padding: 10px 14px !important; background: var(--block-background-fill);}
.summary-row > * {min-width: 0 !important;}
.summary-row > .new-reel, button.new-reel {flex: 0 0 auto !important; width: auto !important;}
.summary-idea {font-weight: 600; font-size: 16px;}
.summary-folder {font-size: 12px; opacity: .7;}
.stepper {display: flex; flex-wrap: wrap; gap: 8px; list-style: none; margin: 4px 0 !important; padding: 0 !important;}
.stepper .step {display: flex; align-items: center; gap: 8px; min-height: 40px; padding: 0 14px; border-radius: 999px;
                font-size: 13px; border: 1px solid var(--border-color-primary); opacity: .7; margin: 0 !important;}
.stepper .step-dot {width: 20px; height: 20px; border-radius: 999px; display: inline-flex; align-items: center;
                    justify-content: center; font-size: 11px; font-weight: 700;
                    background: var(--border-color-primary);}
.stepper .step-done {opacity: 1;}
.stepper .step-done .step-dot {background: #15803d; color: #fff;}
.stepper .step-running {opacity: 1; border-color: #4f46e5; font-weight: 600;}
.stepper .step-running .step-dot {background: #4f46e5; color: #fff;}
.stepper .step-review {opacity: 1; border-color: #d97706; font-weight: 600;}
.stepper .step-review .step-dot {background: #d97706; color: #1c1206;}
.stepper .step-failed {opacity: 1; border-color: #dc2626; font-weight: 600;}
.stepper .step-failed .step-dot {background: #dc2626; color: #fff;}
.workspace {gap: 16px !important; align-items: flex-start !important; flex-wrap: wrap !important;}
.results {border: 1px solid var(--border-color-primary) !important; border-radius: 18px !important;
          padding: 12px !important; position: sticky; top: 8px;}
.results-tabs .wrap {gap: 6px !important;}
.cards {display: grid !important; grid-template-columns: repeat(auto-fill, minmax(230px, 1fr)) !important;
        gap: 12px !important;}
.cards-small {grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)) !important; gap: 8px !important;}
.card {border: 1px solid var(--border-color-primary) !important; border-radius: 14px !important;
       padding: 10px !important; gap: 6px !important; min-width: 0 !important; width: auto !important;}
.card-label, .card-label .prose {font-size: 13px !important;}
button.view {width: auto !important;}
.final {border: 1px solid #16a34a !important; border-radius: 18px !important; padding: 12px 14px !important;}
.detail-card {max-width: 920px !important;}
.detail-text {max-height: 30vh; overflow-y: auto;}
.detail-nav {justify-content: space-between !important;}
.step-line .hint {font-family: var(--font); font-size: 12px; opacity: .75;}
.section-title > *, .section-title .prose {padding: 0 !important; margin: 0 !important;}
"""


def start():
    start_cell("Cell 7 · Control Center")
    ensure_api_keys_file()
    blocks, _ = build_ui()
    launch_gradio(blocks, PORTS["control_center"], css=UI_CSS)
    url = start_cloudflare_tunnel(PORTS["control_center"], "control_center_tunnel", "Control Center",
                                  "Type an idea, review each step, and the reel is saved in Drive")
    tools = "Tools running: " + ", ".join(
        f"{'🟢' if service_ready(name) else '⚪'} {svc['label']}" for name, svc in SERVICES.items())
    problems = [] if is_public_url(url) else [f"No public link yet: {url}"]
    finish_cell(not problems, [f"CONTROL CENTER: {url}", tools,
                               f"Video encoding: {'GPU (NVENC)' if nvenc_available() else 'CPU (libx264)'}",
                               f"Subtitle fonts: {', '.join(FONTS)}",
                               "(⚪ = not running; the form tells you when a choice needs it)"], problems)
