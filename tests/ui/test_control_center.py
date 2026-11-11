"""The real Gradio 6 interfaces: built from the package, handlers exercised, and opened in Chromium."""
import json, os, shutil, time
import pytest
import studio.lib as L
import studio.pipeline as P
from studio import control_center as CC, jobs
from studio.tools import audio
from helpers import ffmpeg

FORM = ["idea_in", "duration_sl", "style_rd", "tone_dd", "instructions_in", "scenes_dd", "llm_dd", "model_dd",
        "image_rd", "cover_cb", "keyframes_cb", "clip_type_rd", "clip_dd", "paid_cb", "own_files_in",
        "voice_type_rd", "voice_provider_dd", "voice_dd", "clone_in", "sound_rd", "music_rd", "kit_cb", "review_cb",
        "ai_w", "ai_h", "ai_f", "ai_s"]
OUT = CC.OUT


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
    assert len(blocks.blocks) > 150 and len(FORM) == len(CC.PRODUCE_KEYS) and len(OUT) == CC.OUTPUT_COUNT
    assert len(CC.KEY_COMPONENTS) == sum(len(cred["fields"]) for cred in L.CREDENTIALS.values())
    assert len(c["montage_inputs"]) == len(CC.MONTAGE_KEYS) == len(c["reel_montage_inputs"])
    assert c["montage_inputs"][0] is c["aspect_rd"]           # the format picker IS the montage format
    assert "Anton" in CC.FONTS and c["prefs"]["montage"]["font"] == "Anton"
    assert c["review_cb"].value is True and c["sound_rd"].value == "none" and c["ai_f"].value == 121
    assert c["review_panel"].visible is False and c["final_section"].visible is False
    assert c["results_col"].visible is False and c["summary_box"].visible is False and c["composer_box"].visible
    assert c["publish_inputs"][0].value == [] and c["publish_inputs"][3].value == "private"   # nothing posts by itself
    for window in ("detail_modal", "llm_modal", "image_modal", "voice_modal", "video_modal", "format_modal", "duration_modal",
                   "style_modal", "settings_modal"):
        assert c[window].visible is False                     # every window starts closed


def test_chips_summarize_the_choices():
    labels = CC.chip_labels("Google Gemini", "gemini-3.8-flash", "nano_banana", "azure", "es-ES-ElviraNeural",
                            "gemini_omni", "9:16", 45, "talking_objects")
    assert labels[0] == "🧠 Google Gemini · gemini-3.8-flash" and labels[1].startswith("🖼️ Nano Banana")
    assert labels[2] == "🎙️ Azure Speech · Elvira (female)" and labels[3].startswith("🎞️ Google Gemini Omni")
    assert labels[4:] == ["📐 9:16", "⏱️ 45 s", "🎭 Talking objects"]
    assert CC.chip_labels("OpenAI (ChatGPT)", "", "flux", "edge", "es-MX-JorgeNeural", "pexels", "1:1", 30,
                          "auto")[0] == "🧠 OpenAI"


def test_current_step_line():
    log = "[00:01] Checking...\n[01:40] Scene 5: image with FLUX (local)..."
    assert CC.current_step(log) == "[01:40] Scene 5: image with FLUX (local)..."
    failed = log + "\n[02:00] ❌ open() got an unexpected keyword argument\n[02:00] TypeError (full detail in the technical log)"
    line = CC.current_step(failed, failed=True)
    assert "❌ open() got an unexpected keyword argument" in line and "Resume" in line and "TypeError" not in line


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
    html = CC.validate("OpenAI (ChatGPT)", "pexels", "edge", "flux", "library", False, None, True, True, "none")
    assert "⛔" in html and "OpenAI" in html and "Pexels" in html and "Forge" in html
    keys("openai", api_key="sk")
    keys("pexels", api_key="px")
    assert "✅ Ready" in CC.validate("OpenAI (ChatGPT)", "pexels", "edge", "flux", "none", False, None, False, True, "none")
    html = CC.validate("OpenAI (ChatGPT)", "pexels", "edge", "flux", "none", False, None, False, True, "elevenlabs")
    assert "ElevenLabs" in html and "the sound effects" in html


def test_save_keys_writes_only_edited_fields(ui, keys):
    keys("openai", api_key="sk")
    CC.KEY_SHOWN.update({(s, f): L.get_key(s, f) for s, f, _ in CC.KEY_COMPONENTS})
    values = ["NEW" if (s, f) == ("upload_post", "username") else L.get_key(s, f) for s, f, _ in CC.KEY_COMPONENTS]
    message = CC.save_all_keys(*values)
    assert "1 field" in message and L.get_key("upload_post", "username") == "NEW" and L.get_key("openai", "api_key") == "sk"


KIT = P.normalize_kit({"virality": {"overall": 80}, "on_screen_hooks": ["h"], "youtube": {"title": "Título"},
                       "tiktok": {"caption": "tt"}, "instagram": {"caption": "ig"}, "facebook": {"caption": "fb"},
                       "seed_comments": ["c"], "teaser": "t"})


def snapshot(**changes):
    base = {"log": "[00:01] a", "stage": "Storyboard", "pct": 10, "script": "S", "media": [], "video": None,
            "videos": [], "failed": False, "done": False, "kit": None, "tech": "", "reel_dir": None, "review": None}
    base.update(changes)
    return base


def test_production_wrapper(ui, monkeypatch):
    _, c = ui
    seen = {}

    def fake_produce(settings, resume_dir=None):
        seen.update(settings)
        yield snapshot()
        yield snapshot(stage="Done", pct=100, done=True, kit=KIT, tech="TECH", video="/v.mp4",
                       videos=["/v.mp4", "/v2.mp4"], reel_dir="/reels/x", log="[00:01] a\n[00:09] DONE.")
    monkeypatch.setattr(CC, "produce_reel", fake_produce)
    monkeypatch.setattr(CC, "drive_responds", lambda timeout=10: True)
    values = form_values(c, idea="Idea", style="animated", scene_sound="elevenlabs")
    values[-1] = 2                                                # variants
    outputs = list(CC.produce_ui(*values))
    assert "Starting" in outputs[0][0] and all(len(o) == CC.OUTPUT_COUNT for o in outputs)
    assert not jobs.current().running
    assert seen["style"] == "animated" and seen["num_scenes"] == "auto" and seen["tone"] == "auto"
    assert seen["make_cover"] is True and seen["make_kit"] is True and seen["image_provider"] == "flux"
    assert seen["review_steps"] is True and seen["scene_sound"] == "elevenlabs" and "language" not in seen
    assert seen["montage"]["variants"] == 2 and seen["montage"]["font"] == "Anton" and seen["duration"] == 35
    last = outputs[-1]
    assert "DONE." in last[OUT["step"]] and last[OUT["variants"]] == ["/v.mp4", "/v2.mp4"]
    assert last[OUT["tech"]] == "TECH" and last[OUT["reel"]] == "/reels/x"
    assert last[OUT["kit0"]]["value"] == "Título" and "(6/100)" in last[OUT["kit0"]]["label"]
    assert last[OUT["panel"]]["visible"] is False
    assert last[OUT["final"]]["visible"] is True and last[OUT["composer"]]["visible"] is False
    assert "step-review" in last[OUT["stepper"]] and last[OUT["stepper"]].count("step-done") == 5
    prefs = json.load(open(CC.PREFS_FILE))
    assert prefs["montage"]["variants"] == 2 and prefs["style"] == "animated" and prefs["scene_sound"] == "elevenlabs"


STORYBOARD_REVIEW = {"reel_dir": "/reels/x", "stage": "storyboard", "storyboard": "Title: T\n\n## Scene 1: A\nVisual: v",
                     "items": []}
AUDIO_REVIEW = {"reel_dir": "/reels/x", "stage": "audio", "storyboard": "...",
                "items": [{"id": "scene1_audio", "label": "Scene 1 · voice · 6.3 s", "kind": "audio", "path": None,
                           "text": "Hola", "text_label": "Narration"}]}


def test_the_review_panel_follows_the_step(monkeypatch):
    shown = {}
    out = CC.snapshot_outputs(snapshot(review=STORYBOARD_REVIEW, stage="Review the storyboard"), shown)
    assert "👀" in out[OUT["progress"]] and out[OUT["review"]] == STORYBOARD_REVIEW
    assert out[OUT["panel"]]["visible"] and out[OUT["storyboard_box"]]["visible"]
    assert out[OUT["storyboard_text"]]["value"].startswith("Title: T") and not out[OUT["items_box"]]["visible"]
    assert "Review the storyboard" in out[OUT["title"]]["value"]
    again = CC.snapshot_outputs(snapshot(review=STORYBOARD_REVIEW), shown)
    assert again[OUT["storyboard_text"]] == {"__type__": "update"}        # what you are editing is not overwritten
    out = CC.snapshot_outputs(snapshot(review=AUDIO_REVIEW), shown)
    assert out[OUT["items_box"]]["visible"] and not out[OUT["storyboard_box"]]["visible"]
    out = CC.snapshot_outputs(snapshot(), shown)
    assert out[OUT["review"]] is None and out[OUT["panel"]]["visible"] is False

    calls = []

    def fake_continue(reel_dir, edited=None):
        calls.append(("continue", reel_dir, edited))
        yield snapshot(stage="Done", done=True, pct=100, reel_dir=reel_dir)

    def fake_item(reel_dir, item_id, text=None):
        calls.append(("item", reel_dir, item_id, text))
        yield snapshot(review=AUDIO_REVIEW, reel_dir=reel_dir)

    def fake_storyboard(reel_dir, notes="", edited=""):
        calls.append(("storyboard", reel_dir, notes, edited))
        yield snapshot(review=STORYBOARD_REVIEW, reel_dir=reel_dir)
    monkeypatch.setattr(CC, "continue_reel", fake_continue)
    monkeypatch.setattr(CC, "regenerate_item", fake_item)
    monkeypatch.setattr(CC, "regenerate_storyboard", fake_storyboard)
    reel = L.REELS_DIR
    list(CC.confirm_ui(reel, "Title: edited"))
    list(CC.regenerate_item_ui(reel, "scene1_audio", "Hola de nuevo"))
    list(CC.regenerate_storyboard_ui(reel, "más humor", "Title: T"))
    list(CC.confirm_ui(reel))
    assert calls == [("continue", reel, "Title: edited"), ("item", reel, "scene1_audio", "Hola de nuevo"),
                     ("storyboard", reel, "más humor", "Title: T"), ("continue", reel, None)]
    assert "no reel waiting" in list(CC.confirm_ui(None))[-1][0]


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
    assert "Uploading" in steps[0] and "✅" in steps[-1] and "https://t/1" in steps[-1]


PLAN = {"language": "es", "language_name": "Spanish", "title": "Demo", "on_screen_text": "",
        "scenes": [{"id": 1, "title": "Uno", "visual": "Un perro", "narration": "Hola perro", "sound": "",
                    "duration": 3.0, "image_prompt": "a dog", "motion_prompt": "pan", "search_query": "dog",
                    "sound_prompt": ""}]}


@pytest.fixture
def saved_reel():
    folder = f"{L.REELS_DIR}/demo"
    shutil.rmtree(folder, ignore_errors=True)
    os.makedirs(f"{folder}/images")
    open(f"{folder}/demo_FINAL.mp4", "wb").write(b"x")
    json.dump(PLAN, open(f"{folder}/plan.json", "w"))
    P.save_publish_kit(KIT, folder)
    return folder


def test_reels_tab(saved_reel, monkeypatch):
    labels = dict((value, label) for label, value in CC.list_reels())
    assert "✅" in labels[saved_reel] and "demo" in labels[saved_reel]
    shown = CC.show_reel(saved_reel)
    assert shown[0].endswith("demo_FINAL.mp4") and "## Scene 1: Uno" in shown[1] and shown[5]["value"] == "Título"
    seen = {}

    def fake_reassemble(folder, montage):
        seen["args"] = (folder, montage)
        yield snapshot(stage="Done", pct=100, done=True, video=f"{folder}/demo_FINAL.mp4", reel_dir=folder)
    monkeypatch.setattr(CC, "reassemble_reel", fake_reassemble)
    outputs = list(CC.reassemble_ui(saved_reel, *P.DEFAULT_MONTAGE.values()))
    assert seen["args"][0] == saved_reel and outputs[-1][2].endswith("demo_FINAL.mp4")


def test_a_reel_waiting_for_review_opens_its_panel(saved_reel, monkeypatch):
    ffmpeg("-f", "lavfi", "-i", "color=c=red:s=64x64", "-frames:v", "1", f"{saved_reel}/images/scene_01.png")
    P.save_settings(saved_reel, {"idea": "Idea demo", "clip_provider": "ai_photos", "llm_provider": "OpenAI (ChatGPT)",
                                 "voice_provider": "edge", "make_cover": False})
    P.write_run_state(saved_reel, "review", "images")
    label = dict((v, l) for l, v in CC.list_reels())[saved_reel]
    assert label.startswith("👀") and label.endswith("· images")
    assert "Open in «Produce»" in CC.show_reel(saved_reel)[3]
    outputs = list(CC.open_reel_ui(saved_reel))
    assert outputs[0][0].selected == "produce"
    review = outputs[-1][1 + OUT["review"]]
    assert review["stage"] == "images" and [i["id"] for i in review["items"]] == ["scene1_image"]
    assert review["items"][0]["text"] == "a dog" and outputs[-1][1 + OUT["reel"]] == saved_reel


def test_a_failed_reel_continues_with_its_own_settings(saved_reel, monkeypatch):
    P.write_run_state(saved_reel, "failed", "Clip 2/3", "boom")
    os.remove(f"{saved_reel}/demo_FINAL.mp4")
    label = dict((v, l) for l, v in CC.list_reels())[saved_reel]
    assert label.startswith("❌") and "Clip 2/3" in label
    note = CC.show_reel(saved_reel)[3]
    assert "continue from there" in note and "boom" in note
    seen = {}

    def fake_continue(reel_dir, edited=None):
        seen["reel"] = reel_dir
        yield snapshot(stage="Done", pct=100, done=True, reel_dir=reel_dir)
    monkeypatch.setattr(CC, "continue_reel", fake_continue)
    outputs = list(CC.open_reel_ui(saved_reel))
    assert seen["reel"] == saved_reel and "Done" in outputs[-1][1]


def test_reel_settings_fill_the_form(saved_reel):
    P.save_settings(saved_reel, {"idea": "Lobos", "llm_provider": "Ollama (local)", "tone": "dramatic",
                                 "llm_model": "qwen2.5:14b", "image_provider": "nano_banana", "make_cover": False,
                                 "clip_provider": "gemini_omni", "confirm_paid": True, "style": "talking_objects",
                                 "voice_provider": "azure", "voice": "es-ES-ElviraNeural", "music_source": "none",
                                 "scene_sound": "elevenlabs", "review_steps": False, "make_kit": False,
                                 "montage": {"aspect": "16:9", "font": "Bangers", "variants": 2, "clip_audio": False}})
    updates = CC.use_reel_settings_ui(saved_reel)
    form = dict(zip(CC.PRODUCE_KEYS + CC.MONTAGE_KEYS, updates))
    assert form["idea"]["value"] == "Lobos" and form["tone"]["value"] == "dramatic"
    assert form["style"]["value"] == "talking_objects" and form["image_provider"]["value"] == "nano_banana"
    assert form["make_cover"]["value"] is False and form["use_keyframes"]["visible"]
    assert form["llm_provider"]["value"] == "Ollama (local)" and form["llm_provider"]["choices"] == ["Ollama (local)"]
    assert form["llm_model"]["value"] == "qwen2.5:14b"
    assert form["clip_type"]["value"] == "external_ai" and form["clip_provider"]["value"] == "gemini_omni"
    assert form["confirm_paid"]["visible"] and form["confirm_paid"]["value"] is False   # never pre-confirmed
    assert form["voice_type"]["value"] == "external" and form["voice"]["value"] == "es-ES-ElviraNeural"
    assert form["scene_sound"]["value"] == "elevenlabs" and form["review_steps"]["value"] is False
    assert form["aspect"]["value"] == "16:9" and form["variants"]["value"] == 2 and form["clip_audio"]["value"] is False
    footage, ai_box, kit_section, note, reel_state, tabs = updates[-6:]
    assert kit_section["visible"] is False and ai_box["visible"] is False
    assert reel_state == saved_reel and "loaded" in note


def test_presets_round_trip(ui):
    _, c = ui
    values = form_values(c, duration=60, idea="not saved")
    message, _ = CC.save_preset_ui("My style / wolves", *values)
    assert "saved" in message and "My style wolves" in CC.list_presets()
    updates = CC.load_preset_ui("My style wolves")
    form = dict(zip(CC.PRODUCE_KEYS + CC.MONTAGE_KEYS, updates))
    assert form["duration"]["value"] == 60 and "loaded" in updates[-2]
    assert "value" not in form["idea"]                              # loading a preset keeps the idea you typed
    saved = json.load(open(f"{CC.PRESETS_DIR}/My style wolves.json"))
    assert "confirm_paid" not in saved and "own_files" not in saved and "idea" not in saved


def test_background_job_survives_a_disconnected_page(monkeypatch):
    gate = {"go": False}

    def slow_produce(settings, resume_dir=None):
        yield snapshot(reel_dir="/r")
        while not gate["go"]:
            time.sleep(0.01)
        yield snapshot(stage="Done", pct=100, done=True, reel_dir="/r")
    monkeypatch.setattr(CC, "produce_reel", slow_produce)
    monkeypatch.setattr(CC, "drive_responds", lambda timeout=10: False)
    empty = [""] * len(CC.PRODUCE_KEYS) + list(P.DEFAULT_MONTAGE.values())
    page = CC.produce_ui(*empty)
    next(page)                                       # "Starting…"
    assert "Storyboard" in next(page)[0]
    page.close()                                     # the browser tab is closed / the tunnel drops
    assert jobs.current().running                    # ...and the production keeps going
    blocked = list(CC.produce_ui(*empty))
    assert "A job is already running" in blocked[-1][0]
    gate["go"] = True
    reopened = list(CC.attach_ui())                  # the page is opened again
    assert "Done" in reopened[-1][0] and not jobs.current().running


def test_stop_cancels_the_job(monkeypatch):
    def endless(settings, resume_dir=None):
        while True:
            yield snapshot(stage="Clips", pct=40)
            time.sleep(0.01)
    monkeypatch.setattr(CC, "produce_reel", endless)
    page = CC.produce_ui(*([""] * len(CC.PRODUCE_KEYS) + list(P.DEFAULT_MONTAGE.values())))
    next(page), next(page)
    assert "Stopping" in CC.stop_ui()
    last = list(page)[-1]
    assert "Stopped by you" in last[0] and not jobs.current().running


def test_audio_studio():
    ui = audio.build_ui()
    assert ui is not None
    clip, message = audio.speak("hola", "minimax", "v", None, "")
    assert clip is None and "is missing" in message
    assert {"azure", "gemini_tts", "voxcpm"} <= {v for _, v in audio.on_type("external")["choices"]}
    ui.close()


@pytest.mark.browser
def test_opens_in_a_browser_and_reviews_items_one_by_one(ui, monkeypatch, tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    blocks, _ = ui
    reel = f"{L.REELS_DIR}/browser-review"
    os.makedirs(f"{reel}/images", exist_ok=True)
    items = []
    for i, color in ((1, "red"), (2, "blue")):
        ffmpeg("-f", "lavfi", "-i", f"color=c={color}:s=96x160", "-frames:v", "1", f"{reel}/images/scene_0{i}.png")
        items.append({"id": f"scene{i}_image", "label": f"Scene {i} · image", "kind": "image",
                      "path": f"{reel}/images/scene_0{i}.png", "text": f"prompt {i}", "text_label": "Image prompt"})
    review = {"reel_dir": reel, "stage": "images", "storyboard": "", "items": items}
    calls = []

    def fake_produce(settings, resume_dir=None):
        yield snapshot(reel_dir=reel, review=review, stage="Review the images")

    def fake_item(reel_dir, item_id, text=None):
        calls.append((item_id, text))
        changed = dict(review, items=[dict(i, text=text if i["id"] == item_id else i["text"]) for i in items])
        yield snapshot(reel_dir=reel_dir, review=changed, stage="Review the images")

    def fake_continue(reel_dir, edited=None):
        calls.append(("confirm", reel_dir))
        yield snapshot(reel_dir=reel_dir, stage="Done", pct=100, done=True)
    monkeypatch.setattr(CC, "produce_reel", fake_produce)
    monkeypatch.setattr(CC, "regenerate_item", fake_item)
    monkeypatch.setattr(CC, "continue_reel", fake_continue)
    monkeypatch.setattr(CC, "drive_responds", lambda timeout=10: False)
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
        assert texts[4] == "📐 9:16" and texts[5] == "⏱️ 35 s" and texts[7] == "⚙️ Settings"
        for i in range(8):                                   # every picker opens and closes
            chips.nth(i).click()
            page.wait_for_selector(".modal .modal-card", state="visible", timeout=10000)
            page.get_by_role("button", name="✔ Done").locator("visible=true").first.click()
            page.wait_for_selector(".modal .modal-card", state="hidden", timeout=10000)

        page.locator(".composer textarea").fill("Una idea")
        page.locator("button.send").click()
        page.wait_for_selector("button.regen", timeout=20000)
        assert page.locator("button.regen").count() == 2 and "Review the images" in page.inner_text(".review")
        assert "Images" in page.inner_text(".stepper") and page.locator(".stepper .step-review").count() == 1
        assert page.locator(".final").first.is_hidden()                      # the reel and publishing come last
        page.locator(".review button.view").first.click()                   # 🔍 opens the item in detail
        page.wait_for_selector(".detail-modal .detail-card", state="visible", timeout=10000)
        assert "1 of 2" in page.inner_text(".detail-card")
        page.get_by_role("button", name="Next →").click()
        page.wait_for_function("() => document.querySelector('.detail-card').innerText.includes('2 of 2')",
                               timeout=10000)
        page.screenshot(path=str(tmp_path / "detail.png"))
        page.get_by_role("button", name="✕ Close").click()
        page.wait_for_selector(".detail-modal .detail-card", state="hidden", timeout=10000)
        box = page.get_by_label("Image prompt").nth(1)
        box.fill("a blue whale")
        page.get_by_role("button", name="🔄 Regenerate scene2_image").click()
        page.wait_for_function("() => [...document.querySelectorAll('textarea')].some(t => t.value === 'a blue whale')"
                               " && document.querySelectorAll('button.regen').length === 2", timeout=20000)
        time.sleep(1)
        page.get_by_role("button", name="🔄 Regenerate scene1_image").click()        # buttons still work after re-render
        page.wait_for_timeout(1500)
        page.screenshot(path=str(tmp_path / "review.png"), full_page=True)
        page.get_by_role("button", name="✅ Confirm and continue").click()
        page.wait_for_function("() => !document.querySelector('.review') || "
                               "getComputedStyle(document.querySelector('.review')).display === 'none' || "
                               "document.querySelector('.review').offsetParent === null", timeout=20000)
        browser.close()
    assert calls[:2] == [("scene2_image", "a blue whale"), ("scene1_image", "prompt 1")]
    assert calls[2] == ("confirm", reel)
    assert not errors


def test_the_step_bar_follows_the_pipeline():
    assert CC.step_of("Writing the storyboard") == "storyboard" and CC.step_of("Image 3/5") == "images"
    assert CC.step_of("Timing the words 2/5") == "audio" and CC.step_of("Clip 1/5") == "clips"
    assert CC.step_of("Scene 2/5: picture and sound") == "montage" and CC.step_of("Error in: Clip 4/5") == "clips"
    assert CC.step_of("Regenerating scene2_image") == "images" and CC.step_of("cover regenerated") == "images"
    assert CC.step_of("Publish kit") == "images" and CC.step_of("Stopped by you") is None
    states = CC.step_states(snapshot(stage="Clip 2/5", reel_dir="/reels/x"))
    assert [states[k] for k, _ in CC.STEPS] == ["done", "done", "done", "running", "todo", "todo"]
    states = CC.step_states(snapshot(stage="Review the audio", review=AUDIO_REVIEW, reel_dir="/reels/x"))
    assert states["audio"] == "review" and states["images"] == "done"
    assert CC.step_states(snapshot(stage="Error in: Voice 1/3", failed=True, reel_dir="/r"))["audio"] == "failed"
    assert CC.done_steps(states) == [("Storyboard", "storyboard"), ("Images", "images")]
    assert CC.step_states(None) == {} and CC.stepper_html({}) == ""


def test_a_long_wait_shows_its_time():
    assert CC.stage_label(snapshot(stage="Writing the storyboard", since=135)) == \
        "Writing the storyboard · 2:15 on this step"
    assert CC.stage_label(snapshot(stage="Writing the storyboard", since=2)) == "Writing the storyboard"
    assert CC.stage_label(snapshot(stage="Review the audio", review=AUDIO_REVIEW, since=300)) == "Review the audio"


def test_earlier_results_and_the_detail_window(saved_reel):
    ffmpeg("-f", "lavfi", "-i", "color=c=red:s=64x64", "-frames:v", "1", f"{saved_reel}/images/scene_01.png")
    scenes = CC.step_items(saved_reel, "storyboard")
    assert scenes[0]["label"] == "Scene 1 · Uno" and scenes[0]["text"] == "Hola perro"
    assert scenes[0]["kind"] == "image" and "Un perro" in scenes[0]["extra"]
    assert [i["kind"] for i in CC.step_items(saved_reel, "montage")] == ["video"]
    assert CC.step_items(None, "images") == []
    items = [{"id": "a", "label": "A", "kind": "image", "path": "/a.png", "text": "pa", "text_label": "Prompt"},
             {"id": "b", "label": "B", "kind": "video", "path": "/b.mp4", "text": "pb", "text_label": "Motion"}]
    state, window, title, image, video, audio, text, prev, nxt = CC.detail_updates(items, 0)
    assert window["visible"] and image["visible"] and not video["visible"] and "1 of 2" in title["value"]
    assert "pa" in text["value"] and prev["interactive"] is False and nxt["interactive"] is True
    state, window, title, image, video, *_ = CC.detail_step(state, 1)
    assert state["index"] == 1 and video["visible"] and video["value"] == "/b.mp4" and not image["visible"]
    assert CC.detail_step(state, 5)[0]["index"] == 1                         # never past the last item
    closed = CC.close_detail()
    assert closed[0] is None and closed[1]["visible"] is False and closed[4]["value"] is None


def test_regenerate_selected_runs_each_item(monkeypatch):
    calls = []

    def fake_item(reel_dir, item_id, text=None):
        calls.append((item_id, text))
        yield snapshot(review=AUDIO_REVIEW, reel_dir=reel_dir)
    monkeypatch.setattr(CC, "regenerate_item", fake_item)
    list(CC.regenerate_many_ui(L.REELS_DIR, [("scene1_audio", "uno"), ("scene3_audio", "tres")]))
    assert calls == [("scene1_audio", "uno"), ("scene3_audio", "tres")]
    assert "Select at least one" in list(CC.regenerate_many_ui(L.REELS_DIR, []))[-1][0]


def test_new_reel_brings_the_composer_back():
    out = CC.new_reel_ui()
    assert out[OUT["composer"]]["visible"] and not out[OUT["summary"]]["visible"]
    assert not out[OUT["final"]]["visible"] and out[OUT["reel"]] is None and out[OUT["stepper"]] == ""


def test_publishing_needs_a_platform(saved_reel):
    assert "at least one platform" in list(CC.publish_ui(saved_reel, [], "", "UTC", "private", "SELF_ONLY", True))[-1]
