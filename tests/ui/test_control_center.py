"""The real Gradio 6 interfaces: built from the package, handlers exercised, and (optionally) opened in Chromium."""
import json, os, time
import pytest
import studio.lib as L
import studio.pipeline as P
from studio import control_center as CC, jobs
from studio.tools import audio


@pytest.fixture(scope="module")
def ui(fonts):
    L.ensure_api_keys_file()
    blocks, components = CC.build_ui()
    yield blocks, components
    blocks.close()


@pytest.fixture(autouse=True)
def nothing_running(monkeypatch):
    monkeypatch.setattr(L, "service_ready", lambda name: False)


def test_layout(ui):
    blocks, c = ui
    assert len(blocks.blocks) > 150
    assert len(CC.KEY_COMPONENTS) == sum(len(cred["fields"]) for cred in L.CREDENTIALS.values())
    assert len(c["montage_inputs"]) == len(CC.MONTAGE_KEYS) == len(c["reel_montage_inputs"])
    assert "Anton" in CC.FONTS and c["prefs"]["montage"]["font"] == "Anton"


def test_cascading_pickers():
    assert [v for _, v in CC.on_clip_type_change("own")["choices"]] == ["upload", "footage"]
    assert len(CC.on_clip_type_change("external_ai")["choices"]) == 6
    paid, ai_box, keyframe, files, footage = CC.on_clip_change("upload")
    assert files["visible"] and not paid["visible"] and not footage["visible"]
    paid, ai_box, keyframe, files, footage = CC.on_clip_change("footage")
    assert footage["visible"] and "archivo(s)" in footage["value"]
    paid, ai_box, keyframe, files, footage = CC.on_clip_change("ofox")
    assert paid["visible"] and paid["value"] is False and not ai_box["visible"]
    paid, ai_box, keyframe, files, footage = CC.on_clip_change("ltxv")
    assert ai_box["visible"] and keyframe["visible"]
    voices, clone = CC.on_voice_provider_change("azure")
    assert voices["value"] == "es-MX-JorgeNeural" and not clone["visible"]
    assert CC.on_voice_provider_change("f5")[1]["visible"]


def test_status_box(keys):
    html = CC.validate("OpenAI (ChatGPT)", "pexels", "edge", "none", "none", "library", False, None)
    assert "⛔" in html and "OpenAI" in html and "Pexels" in html
    keys("openai", api_key="sk")
    keys("pexels", api_key="px")
    assert "✅ Todo listo" in CC.validate("OpenAI (ChatGPT)", "pexels", "edge", "none", "none", "none", False, None)
    assert "✅ Todo listo" in CC.validate("OpenAI (ChatGPT)", "upload", "edge", "none", "none", "none", False, ["/a.mp4"])


def test_save_keys_writes_only_edited_fields(ui, keys):
    keys("openai", api_key="sk")
    CC.KEY_SHOWN.update({(s, f): L.get_key(s, f) for s, f, _ in CC.KEY_COMPONENTS})
    values = ["NEW" if (s, f) == ("upload_post", "username") else L.get_key(s, f) for s, f, _ in CC.KEY_COMPONENTS]
    message = CC.save_all_keys(*values)
    assert "1 campo" in message and L.get_key("upload_post", "username") == "NEW" and L.get_key("openai", "api_key") == "sk"


KIT = P.normalize_kit({"virality": {"overall": 80}, "on_screen_hooks": ["h"], "youtube": {"title": "Título"},
                       "tiktok": {"caption": "tt"}, "instagram": {"caption": "ig"}, "facebook": {"caption": "fb"},
                       "seed_comments": ["c"], "teaser": "t"})


def test_production_wrapper(ui, monkeypatch):
    _, c = ui
    seen = {}

    def fake_produce(settings):
        seen.update(settings)
        base = {"log": "a", "stage": "Guion", "pct": 10, "script": "S", "media": [], "video": None, "videos": [],
                "failed": False, "done": False, "kit": None, "tech": "", "reel_dir": None}
        yield base
        yield dict(base, stage="Listo", pct=100, done=True, kit=KIT, tech="TECH", video="/v.mp4",
                   videos=["/v.mp4", "/v2.mp4"], reel_dir="/reels/x")
    monkeypatch.setattr(CC, "produce_reel", lambda settings, resume_dir=None: fake_produce(settings))
    monkeypatch.setattr(CC, "drive_responds", lambda timeout=10: True)
    names = ["idea_in", "language_dd", "duration_sl", "tone_dd", "instructions_in", "scenes_sl", "llm_dd", "model_dd",
             "clip_type_rd", "clip_dd", "paid_cb", "own_files_in", "keyframe_rd", "voice_type_rd", "voice_provider_dd",
             "voice_dd", "clone_in", "cover_rd", "music_rd", "kit_cb", "ai_w", "ai_h", "ai_f", "ai_s"]
    values = [c[n].value for n in names] + [comp.value for comp in c["montage_inputs"]]
    values[0], values[-1] = "Idea", 2
    outputs = list(CC.produce_ui(*values))
    assert "Iniciando" in outputs[0][0] and all(len(o) == 15 for o in outputs)
    assert not jobs.current().running
    assert seen["montage"]["variants"] == 2 and seen["montage"]["font"] == "Anton" and seen["duration"] == 35
    assert outputs[-1][5] == ["/v.mp4", "/v2.mp4"] and outputs[-1][13] == "TECH" and outputs[-1][14] == "/reels/x"
    assert outputs[-1][6]["value"] == "Título" and "(6/100)" in outputs[-1][6]["label"]
    prefs = json.load(open(CC.PREFS_FILE))
    assert prefs["montage"]["variants"] == 2 and prefs["language"] == "es"


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
    assert "Primero produce" in list(CC.publish_ui(None, ["tiktok"], "", "", "public", "PUBLIC_TO_EVERYONE", True,
                                                   *[""] * 6))[-1]


def test_reels_tab(saved_reel, monkeypatch):
    labels = dict((value, label) for label, value in CC.list_reels())
    assert "✅" in labels[saved_reel] and "demo" in labels[saved_reel]
    shown = CC.show_reel(saved_reel)
    assert shown[0].endswith("demo_FINAL.mp4") and shown[1] == "guion demo" and shown[5]["value"] == "Título"
    seen = {}

    def fake_reassemble(folder, montage):
        seen["args"] = (folder, montage)
        yield {"stage": "Listo", "pct": 100, "failed": False, "done": True, "log": "ok", "script": "", "media": [],
               "video": f"{folder}/demo_FINAL.mp4", "videos": [], "kit": None, "tech": "", "reel_dir": folder}
    monkeypatch.setattr(CC, "reassemble_reel", fake_reassemble)
    outputs = list(CC.reassemble_ui(saved_reel, *P.DEFAULT_MONTAGE.values()))
    assert seen["args"][0] == saved_reel and outputs[-1][2].endswith("demo_FINAL.mp4")


def test_audio_studio():
    ui = audio.build_ui()
    assert ui is not None
    clip, message = audio.speak("hola", "minimax", "v", None, "")
    assert clip is None and "Falta la clave" in message
    assert {"azure", "gemini_tts", "voxcpm"} <= {v for _, v in audio.on_type("external")["choices"]}
    ui.close()


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
        yield {"stage": "Listo", "pct": 100, "failed": False, "done": True, "log": "ok", "script": "", "media": [],
               "video": None, "videos": [], "kit": None, "tech": "", "reel_dir": resume_dir}
    monkeypatch.setattr(CC, "produce_reel", fake_produce)
    list(CC.resume_reel_ui(saved_reel))
    assert seen["resume_dir"] == saved_reel and seen["settings"]["idea"] == "Idea demo"
    assert seen["settings"]["confirm_paid"] is True                 # the reel was already confirmed once
    assert seen["settings"]["montage"]["aspect"] == "1:1"


def test_reel_settings_fill_the_form(saved_reel):
    P.save_settings(saved_reel, {"idea": "Lobos", "language": "pt", "llm_provider": "Ollama (local)",
                                 "llm_model": "qwen2.5:14b", "clip_provider": "ofox", "confirm_paid": True,
                                 "voice_provider": "azure", "voice": "es-ES-ElviraNeural", "music_source": "none",
                                 "montage": {"aspect": "16:9", "font": "Bangers", "variants": 2}})
    updates = CC.use_reel_settings_ui(saved_reel)
    form = dict(zip(CC.PRODUCE_KEYS + CC.MONTAGE_KEYS, updates))
    assert form["idea"]["value"] == "Lobos" and form["language"]["value"] == "pt"
    assert form["llm_provider"]["value"] == "Ollama (local)" and form["llm_provider"]["choices"] == ["Ollama (local)"]
    assert form["llm_model"]["value"] == "qwen2.5:14b"
    assert form["clip_type"]["value"] == "external_ai" and form["clip_provider"]["value"] == "ofox"
    assert form["confirm_paid"]["visible"] and form["confirm_paid"]["value"] is False   # never pre-confirmed
    assert form["voice_type"]["value"] == "external" and form["voice"]["value"] == "es-ES-ElviraNeural"
    assert form["aspect"]["value"] == "16:9" and form["variants"]["value"] == 2
    ai_box, footage, note, reel_state, tabs = updates[-5:]
    assert reel_state == saved_reel and "cargados" in note


def test_presets_round_trip(ui):
    _, c = ui
    values = [c[n].value for n in ["idea_in", "language_dd", "duration_sl", "tone_dd", "instructions_in", "scenes_sl",
                                   "llm_dd", "model_dd", "clip_type_rd", "clip_dd", "paid_cb", "own_files_in",
                                   "keyframe_rd", "voice_type_rd", "voice_provider_dd", "voice_dd", "clone_in",
                                   "cover_rd", "music_rd", "kit_cb", "ai_w", "ai_h", "ai_f", "ai_s"]]
    values += [comp.value for comp in c["montage_inputs"]]
    values[2] = 60                                   # duration
    message, dropdown = CC.save_preset_ui("Mi estilo / lobos", *values)
    assert "guardado" in message and "Mi estilo lobos" in CC.list_presets()
    name = "Mi estilo lobos"
    updates = CC.load_preset_ui(name)
    form = dict(zip(CC.PRODUCE_KEYS + CC.MONTAGE_KEYS, updates))
    assert form["duration"]["value"] == 60 and "cargados" in updates[-2]
    saved = json.load(open(f"{CC.PRESETS_DIR}/{name}.json"))
    assert "confirm_paid" not in saved and "own_files" not in saved


def test_background_job_survives_a_disconnected_page(monkeypatch):
    gate = {"go": False}

    def slow_produce(settings, resume_dir=None):
        base = {"stage": "Guion", "pct": 10, "failed": False, "done": False, "log": "a", "script": "", "media": [],
                "video": None, "videos": [], "kit": None, "tech": "", "reel_dir": "/r"}
        yield base
        while not gate["go"]:
            time.sleep(0.01)
        yield dict(base, stage="Listo", pct=100, done=True)
    monkeypatch.setattr(CC, "produce_reel", slow_produce)
    monkeypatch.setattr(CC, "drive_responds", lambda timeout=10: False)
    page = CC.produce_ui(*([""] * len(CC.PRODUCE_KEYS) + list(P.DEFAULT_MONTAGE.values())))
    next(page)                                       # "Iniciando…"
    assert "Guion" in next(page)[0]
    page.close()                                     # the browser tab is closed / the tunnel drops
    assert jobs.current().running                    # ...and the production keeps going
    blocked = list(CC.produce_ui(*([""] * len(CC.PRODUCE_KEYS) + list(P.DEFAULT_MONTAGE.values()))))
    assert "Ya hay un trabajo en curso" in blocked[-1][0]
    gate["go"] = True
    reopened = list(CC.attach_ui())                  # the page is opened again
    assert "Listo" in reopened[-1][0] and not jobs.current().running


def test_stop_cancels_the_job(monkeypatch):
    def endless(settings, resume_dir=None):
        while True:
            yield {"stage": "Clips", "pct": 40, "failed": False, "done": False, "log": "x", "script": "", "media": [],
                   "video": None, "videos": [], "kit": None, "tech": "", "reel_dir": None}
            time.sleep(0.01)
    monkeypatch.setattr(CC, "produce_reel", endless)
    page = CC.produce_ui(*([""] * len(CC.PRODUCE_KEYS) + list(P.DEFAULT_MONTAGE.values())))
    next(page), next(page)
    assert "Deteniendo" in CC.stop_ui()
    last = list(page)[-1]
    assert "Detenido por ti" in last[0] and not jobs.current().running


@pytest.mark.browser
def test_opens_in_a_browser(ui, monkeypatch, tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    blocks, _ = ui
    monkeypatch.setattr(L, "kill_port", lambda port: None)
    L.launch_gradio(blocks, 7998, css=CC.UI_CSS)
    errors = []
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1400, "height": 2600}, color_scheme="dark")
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto("http://127.0.0.1:7998/", wait_until="load")
        page.wait_for_selector("text=🛠️ Montaje y subtítulos", timeout=60000)
        time.sleep(1)
        xs = {t: round(page.locator(f"text={t}").first.bounding_box()["x"])
              for t in ("📝 Guion", "🧠 Texto", "🎞️ Clips", "🎙️ Voz", "🎵 Música de fondo")}
        page.screenshot(path=str(tmp_path / "control_center.png"), full_page=True)
        browser.close()
    assert len(set(xs.values())) == 1, xs           # every section title starts at the same x
    assert not errors
