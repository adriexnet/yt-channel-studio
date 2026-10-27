"""The real Gradio 6 interfaces: built from the package, handlers exercised, and (optionally) opened in Chromium."""
import json, os, time
import pytest
import studio.lib as L
import studio.pipeline as P
from studio import control_center as CC
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
    monkeypatch.setattr(CC, "produce_reel", fake_produce)
    monkeypatch.setattr(CC, "drive_responds", lambda timeout=10: True)
    names = ["idea_in", "language_dd", "duration_sl", "tone_dd", "instructions_in", "scenes_sl", "llm_dd", "model_dd",
             "clip_type_rd", "clip_dd", "paid_cb", "own_files_in", "keyframe_rd", "voice_type_rd", "voice_provider_dd",
             "voice_dd", "clone_in", "cover_rd", "music_rd", "kit_cb", "ai_w", "ai_h", "ai_f", "ai_s"]
    values = [c[n].value for n in names] + [comp.value for comp in c["montage_inputs"]]
    values[0], values[-1] = "Idea", 2
    outputs = list(CC.produce_ui(*values))
    assert "Iniciando" in outputs[0][0] and all(len(o) == 15 for o in outputs)
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
    assert CC.list_reels()[0][0] == "demo"
    shown = CC.show_reel(f"{saved_reel}/demo_FINAL.mp4")
    assert shown[1] == "guion demo" and shown[4]["value"] == "Título"
    seen = {}

    def fake_reassemble(folder, montage):
        seen["args"] = (folder, montage)
        yield {"stage": "Listo", "pct": 100, "failed": False, "done": True, "log": "ok", "video": f"{folder}/demo_FINAL.mp4"}
    monkeypatch.setattr(CC, "reassemble_reel", fake_reassemble)
    outputs = list(CC.reassemble_ui(f"{saved_reel}/demo_FINAL.mp4", *P.DEFAULT_MONTAGE.values()))
    assert seen["args"][0] == saved_reel and outputs[-1][2].endswith("demo_FINAL.mp4")


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
