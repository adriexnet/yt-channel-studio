"""The real Gradio 6 interfaces: built from the package, handlers exercised, and opened in Chromium."""
import json, os, time
import pytest
import studio.lib as L
import studio.pipeline as P
from studio import control_center as CC, jobs
from studio.tools import audio

FORM = ["idea_in", "language_dd", "duration_sl", "style_rd", "tone_dd", "instructions_in", "scenes_dd", "llm_dd",
        "model_dd", "image_rd", "cover_cb", "keyframes_cb", "clip_type_rd", "clip_dd", "paid_cb", "own_files_in",
        "voice_type_rd", "voice_provider_dd", "voice_dd", "clone_in", "music_rd", "kit_cb", "ai_w", "ai_h", "ai_f",
        "ai_s"]


@pytest.fixture(scope="module")
def ui(fonts):
    L.ensure_api_keys_file()
    blocks, components = CC.build_ui()
    yield blocks, components
    blocks.close()


@pytest.fixture(autouse=True)
def nothing_running(monkeypatch):
    monkeypatch.setattr(L, "service_ready", lambda name: False)


def form_values(c, **changes):
    values = dict(zip(CC.PRODUCE_KEYS, [c[n].value for n in FORM]))
    values.update(changes)
    return [values[k] for k in CC.PRODUCE_KEYS] + [comp.value for comp in c["montage_inputs"]]


def test_layout(ui):
    blocks, c = ui
    assert len(blocks.blocks) > 150
    assert [CC.PRODUCE_KEYS[i] for i in range(len(FORM))] == CC.PRODUCE_KEYS
    assert len(CC.KEY_COMPONENTS) == sum(len(cred["fields"]) for cred in L.CREDENTIALS.values())
    assert len(c["montage_inputs"]) == len(CC.MONTAGE_KEYS) == len(c["reel_montage_inputs"])
    assert c["montage_inputs"][0] is c["aspect_rd"]           # the format picker IS the montage format
    assert "Anton" in CC.FONTS and c["prefs"]["montage"]["font"] == "Anton"
    for window in ("llm_modal", "image_modal", "voice_modal", "video_modal", "format_modal", "duration_modal",
                   "style_modal", "settings_modal"):
        assert c[window].visible is False                     # every window starts closed


def test_chips_summarize_the_choices():
    labels = CC.chip_labels("Google Gemini", "gemini-3.8-flash", "nano_banana", "azure", "es-ES-ElviraNeural",
                            "gemini_omni", "9:16", 45, "talking_objects")
    assert labels[0] == "🧠 Google Gemini · gemini-3.8-flash" and labels[1].startswith("🖼️ Nano Banana")
    assert "Elvira" in labels[2] and labels[3].startswith("🎞️ Google Gemini Omni")
    assert labels[4:] == ["📐 9:16", "⏱️ 45 s", "🎭 Objetos que hablan"]
    assert CC.chip_labels("OpenAI (ChatGPT)", "", "flux", "edge", "es-MX-JorgeNeural", "pexels", "1:1", 30,
                          "auto")[0] == "🧠 OpenAI"


def test_current_step_line():
    log = "[00:01] Checking...\n[01:40] Scene 5: image with FLUX (local)..."
    assert CC.current_step(log) == "[01:40] Scene 5: image with FLUX (local)..."
    failed = log + "\n[02:00] ❌ open() got an unexpected keyword argument\n[02:00] TypeError (detalle completo en el log técnico)"
    line = CC.current_step(failed, failed=True)
    assert "❌ open() got an unexpected keyword argument" in line and "Reanudar" in line and "TypeError" not in line


def test_contextual_options():
    paid, files, footage, keyframes, ai_box = CC.clip_visibility("upload")
    assert files["visible"] and not paid["visible"] and not keyframes["visible"] and not ai_box["visible"]
    paid, files, footage, keyframes, ai_box = CC.clip_visibility("gemini_omni")
    assert paid["visible"] and paid["value"] is False and keyframes["visible"] and not ai_box["visible"]
    paid, files, footage, keyframes, ai_box = CC.clip_visibility("ltxv")
    assert keyframes["visible"] and ai_box["visible"]                # frames and steps only for local AI
    paid, files, footage, keyframes, ai_box = CC.clip_visibility("pexels")
    assert not any(u["visible"] for u in (paid, files, footage, keyframes, ai_box))
    assert [v for _, v in CC.on_clip_type_change("own")["choices"]] == ["upload", "footage"]
    assert "gemini_omni" in [v for _, v in CC.on_clip_type_change("external_ai")["choices"]]
    voices, clone = CC.on_voice_provider_change("azure")
    assert voices["value"] == "es-MX-JorgeNeural" and not clone["visible"]
    assert CC.on_voice_provider_change("f5")[1]["visible"]


def test_status_box(keys):
    html = CC.validate("OpenAI (ChatGPT)", "pexels", "edge", "flux", "library", False, None, True, True)
    assert "⛔" in html and "OpenAI" in html and "Pexels" in html and "Forge" in html
    keys("openai", api_key="sk")
    keys("pexels", api_key="px")
    assert "✅ Todo listo" in CC.validate("OpenAI (ChatGPT)", "pexels", "edge", "flux", "none", False, None, False, True)
    assert "✅ Todo listo" in CC.validate("OpenAI (ChatGPT)", "upload", "edge", "flux", "none", False, ["/a.mp4"],
                                         False, True)


def test_save_keys_writes_only_edited_fields(ui, keys):
    keys("openai", api_key="sk")
    CC.KEY_SHOWN.update({(s, f): L.get_key(s, f) for s, f, _ in CC.KEY_COMPONENTS})
    values = ["NEW" if (s, f) == ("upload_post", "username") else L.get_key(s, f) for s, f, _ in CC.KEY_COMPONENTS]
    message = CC.save_all_keys(*values)
    assert "1 campo" in message and L.get_key("upload_post", "username") == "NEW" and L.get_key("openai", "api_key") == "sk"


KIT = P.normalize_kit({"virality": {"overall": 80}, "on_screen_hooks": ["h"], "youtube": {"title": "Título"},
                       "tiktok": {"caption": "tt"}, "instagram": {"caption": "ig"}, "facebook": {"caption": "fb"},
                       "seed_comments": ["c"], "teaser": "t"})


def snapshot(**changes):
    base = {"log": "[00:01] a", "stage": "Guion", "pct": 10, "script": "S", "media": [], "video": None, "videos": [],
            "failed": False, "done": False, "kit": None, "tech": "", "reel_dir": None}
    base.update(changes)
    return base


def test_production_wrapper(ui, monkeypatch):
    _, c = ui
    seen = {}

    def fake_produce(settings, resume_dir=None):
        seen.update(settings)
        yield snapshot()
        yield snapshot(stage="Listo", pct=100, done=True, kit=KIT, tech="TECH", video="/v.mp4",
                       videos=["/v.mp4", "/v2.mp4"], reel_dir="/reels/x", log="[00:01] a\n[00:09] DONE.")
    monkeypatch.setattr(CC, "produce_reel", fake_produce)
    monkeypatch.setattr(CC, "drive_responds", lambda timeout=10: True)
    values = form_values(c, idea="Idea", style="animated")
    values[-1] = 2                                                # variants
    outputs = list(CC.produce_ui(*values))
    assert "Iniciando" in outputs[0][0] and all(len(o) == 15 for o in outputs)
    assert not jobs.current().running
    assert seen["style"] == "animated" and seen["num_scenes"] == "auto" and seen["tone"] == "auto"
    assert seen["make_cover"] is True and seen["make_kit"] is True and seen["image_provider"] == "flux"
    assert seen["montage"]["variants"] == 2 and seen["montage"]["font"] == "Anton" and seen["duration"] == 35
    assert "DONE." in outputs[-1][1] and outputs[-1][5] == ["/v.mp4", "/v2.mp4"]
    assert outputs[-1][13] == "TECH" and outputs[-1][14] == "/reels/x"
    assert outputs[-1][6]["value"] == "Título" and "(6/100)" in outputs[-1][6]["label"]
    prefs = json.load(open(CC.PREFS_FILE))
    assert prefs["montage"]["variants"] == 2 and prefs["style"] == "animated"


@pytest.fixture
def saved_reel():
    folder = f"{L.REELS_DIR}/demo"
    os.makedirs(folder, exist_ok=True)
    open(f"{folder}/demo_FINAL.mp4", "wb").write(b"x")
    open(f"{folder}/script.txt", "w").write("guion demo")
    P.save_publish_kit(KIT, folder)
    return folder


def test_publish_wrapper_uses_edited_fields(saved_reel, monkeypatch):
    sent = {}

    def fake_publish(video, kit, platforms, **options):
        sent.update(video=video, kit=kit)
        return {"success": True, "request_id": "r1"}
    answers = iter([{"status": "processing"},
                    {"status": "completed", "results": {"tiktok": {"success": True, "url": "https://t/1"}}}])
    monkeypatch.setattr(CC, "publish_reel", fake_publish)
    monkeypatch.setattr(CC, "publish_status", lambda request_id: next(answers))
    monkeypatch.setattr(time, "sleep", lambda s: None)
    steps = list(CC.publish_ui(saved_reel, ["tiktok"], "", "America/Costa_Rica", "public", "PUBLIC_TO_EVERYONE", True,
                               "Título editado", "D", "t", "TikTok editado", "ig", "fb"))
    assert sent["kit"]["tiktok"]["caption"] == "TikTok editado" and sent["kit"]["youtube"]["title"] == "Título editado"
    assert sent["video"].endswith("demo_FINAL.mp4")
    assert "Subiendo" in steps[0] and "✅" in steps[-1] and "https://t/1" in steps[-1]


def test_reels_tab(saved_reel, monkeypatch):
    labels = dict((value, label) for label, value in CC.list_reels())
    assert "✅" in labels[saved_reel] and "demo" in labels[saved_reel]
    shown = CC.show_reel(saved_reel)
    assert shown[0].endswith("demo_FINAL.mp4") and shown[1] == "guion demo" and shown[5]["value"] == "Título"
    seen = {}

    def fake_reassemble(folder, montage):
        seen["args"] = (folder, montage)
        yield snapshot(stage="Listo", pct=100, done=True, video=f"{folder}/demo_FINAL.mp4", reel_dir=folder)
    monkeypatch.setattr(CC, "reassemble_reel", fake_reassemble)
    outputs = list(CC.reassemble_ui(saved_reel, *P.DEFAULT_MONTAGE.values()))
    assert seen["args"][0] == saved_reel and outputs[-1][2].endswith("demo_FINAL.mp4")


def test_failed_reel_is_listed_with_its_stage_and_can_be_resumed(saved_reel, monkeypatch):
    P.write_run_state(saved_reel, "failed", "Sincronizando subtítulos", "boom")
    P.save_settings(saved_reel, {"idea": "Idea demo", "clip_provider": "wavespeed", "confirm_paid": True,
                                 "llm_provider": "OpenAI (ChatGPT)", "voice_provider": "edge", "montage": {"aspect": "1:1"}})
    label = dict((v, l) for l, v in CC.list_reels())[saved_reel]
    assert label.startswith("❌") and "Sincronizando subtítulos" in label
    note = CC.show_reel(saved_reel)[3]
    assert "Reanudar" in note and "boom" in note
    seen = {}

    def fake_produce(settings, resume_dir=None):
        seen.update(settings=settings, resume_dir=resume_dir)
        yield snapshot(stage="Listo", pct=100, done=True, reel_dir=resume_dir)
    monkeypatch.setattr(CC, "produce_reel", fake_produce)
    list(CC.resume_reel_ui(saved_reel))
    assert seen["resume_dir"] == saved_reel and seen["settings"]["idea"] == "Idea demo"
    assert seen["settings"]["confirm_paid"] is True                 # the reel was already confirmed once
    assert seen["settings"]["montage"]["aspect"] == "1:1"


def test_reel_settings_fill_the_form(saved_reel):
    P.save_settings(saved_reel, {"idea": "Lobos", "language": "pt", "llm_provider": "Ollama (local)",
                                 "llm_model": "qwen2.5:14b", "image_provider": "nano_banana", "make_cover": False,
                                 "clip_provider": "gemini_omni", "confirm_paid": True, "style": "talking_objects",
                                 "voice_provider": "azure", "voice": "es-ES-ElviraNeural", "music_source": "none",
                                 "make_kit": False, "montage": {"aspect": "16:9", "font": "Bangers", "variants": 2}})
    updates = CC.use_reel_settings_ui(saved_reel)
    form = dict(zip(CC.PRODUCE_KEYS + CC.MONTAGE_KEYS, updates))
    assert form["idea"]["value"] == "Lobos" and form["language"]["value"] == "pt"
    assert form["style"]["value"] == "talking_objects" and form["image_provider"]["value"] == "nano_banana"
    assert form["make_cover"]["value"] is False and form["use_keyframes"]["visible"]
    assert form["llm_provider"]["value"] == "Ollama (local)" and form["llm_provider"]["choices"] == ["Ollama (local)"]
    assert form["llm_model"]["value"] == "qwen2.5:14b"
    assert form["clip_type"]["value"] == "external_ai" and form["clip_provider"]["value"] == "gemini_omni"
    assert form["confirm_paid"]["visible"] and form["confirm_paid"]["value"] is False   # never pre-confirmed
    assert form["voice_type"]["value"] == "external" and form["voice"]["value"] == "es-ES-ElviraNeural"
    assert form["aspect"]["value"] == "16:9" and form["variants"]["value"] == 2
    footage, ai_box, kit_section, note, reel_state, tabs = updates[-6:]
    assert kit_section["visible"] is False and ai_box["visible"] is False
    assert reel_state == saved_reel and "cargados" in note


def test_presets_round_trip(ui):
    _, c = ui
    values = form_values(c, duration=60, idea="no se guarda")
    message, _ = CC.save_preset_ui("Mi estilo / lobos", *values)
    assert "guardado" in message and "Mi estilo lobos" in CC.list_presets()
    updates = CC.load_preset_ui("Mi estilo lobos")
    form = dict(zip(CC.PRODUCE_KEYS + CC.MONTAGE_KEYS, updates))
    assert form["duration"]["value"] == 60 and "cargados" in updates[-2]
    assert "value" not in form["idea"]                              # loading a preset keeps the idea you typed
    saved = json.load(open(f"{CC.PRESETS_DIR}/Mi estilo lobos.json"))
    assert "confirm_paid" not in saved and "own_files" not in saved and "idea" not in saved


def test_background_job_survives_a_disconnected_page(monkeypatch):
    gate = {"go": False}

    def slow_produce(settings, resume_dir=None):
        yield snapshot(reel_dir="/r")
        while not gate["go"]:
            time.sleep(0.01)
        yield snapshot(stage="Listo", pct=100, done=True, reel_dir="/r")
    monkeypatch.setattr(CC, "produce_reel", slow_produce)
    monkeypatch.setattr(CC, "drive_responds", lambda timeout=10: False)
    empty = [""] * len(CC.PRODUCE_KEYS) + list(P.DEFAULT_MONTAGE.values())
    page = CC.produce_ui(*empty)
    next(page)                                       # "Iniciando…"
    assert "Guion" in next(page)[0]
    page.close()                                     # the browser tab is closed / the tunnel drops
    assert jobs.current().running                    # ...and the production keeps going
    blocked = list(CC.produce_ui(*empty))
    assert "Ya hay un trabajo en curso" in blocked[-1][0]
    gate["go"] = True
    reopened = list(CC.attach_ui())                  # the page is opened again
    assert "Listo" in reopened[-1][0] and not jobs.current().running


def test_stop_cancels_the_job(monkeypatch):
    def endless(settings, resume_dir=None):
        while True:
            yield snapshot(stage="Clips", pct=40)
            time.sleep(0.01)
    monkeypatch.setattr(CC, "produce_reel", endless)
    page = CC.produce_ui(*([""] * len(CC.PRODUCE_KEYS) + list(P.DEFAULT_MONTAGE.values())))
    next(page), next(page)
    assert "Deteniendo" in CC.stop_ui()
    last = list(page)[-1]
    assert "Detenido por ti" in last[0] and not jobs.current().running


def test_audio_studio():
    ui = audio.build_ui()
    assert ui is not None
    clip, message = audio.speak("hola", "minimax", "v", None, "")
    assert clip is None and "Falta la clave" in message
    assert {"azure", "gemini_tts", "voxcpm"} <= {v for _, v in audio.on_type("external")["choices"]}
    ui.close()


@pytest.mark.browser
def test_opens_in_a_browser(ui, monkeypatch, tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    blocks, _ = ui
    monkeypatch.setattr(L, "kill_port", lambda port: None)
    L.launch_gradio(blocks, 7998, css=CC.UI_CSS)
    errors = []
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900}, color_scheme="dark")
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto("http://127.0.0.1:7998/", wait_until="load")
        page.wait_for_selector("button.send", timeout=60000)
        time.sleep(1)
        chips = page.locator("button.chip")
        assert chips.count() == 8 and not page.locator(".modal").first.is_visible()
        texts = [chips.nth(i).inner_text() for i in range(8)]
        assert texts[4] == "📐 9:16" and texts[5] == "⏱️ 35 s" and texts[7] == "⚙️ Ajustes"
        for i in range(8):                                   # every picker opens and closes
            chips.nth(i).click()
            page.wait_for_selector(".modal .modal-card", state="visible", timeout=10000)
            page.get_by_role("button", name="✔ Listo").locator("visible=true").first.click()
            page.wait_for_selector(".modal .modal-card", state="hidden", timeout=10000)
        page.screenshot(path=str(tmp_path / "control_center.png"))
        browser.close()
    assert not errors
