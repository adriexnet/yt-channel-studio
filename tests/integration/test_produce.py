"""Full productions (fake HTTP for every service, real ffmpeg): storyboard -> images -> audio -> clips -> montage,
with or without a pause for review after each step, single-item regeneration and resume after a failure."""
import base64, copy, glob, json, os, time
import pytest
import studio.lib as L
import studio.pipeline as P
from fakehttp import FakeResponse
from helpers import ffmpeg, probe

SCENE_PROMPTS = dict(image_prompt="a dog", motion_prompt="slow pan", search_query="dog", sound_prompt="")
PLAN = {"language": "es", "language_name": "Spanish", "title": "Tu perro entiende", "title_slug": "perros",
        "on_screen_text": "Tu perro sabe esto", "cover_prompt": "dog cover", "music_prompt": "warm acoustic",
        "search_terms": ["dog"],
        "scenes": [dict(SCENE_PROMPTS, title="El gancho", visual="Un perro mira a cámara",
                        narration="¿Sabías que tu perro entiende más de cien palabras?", sound=""),
                   dict(SCENE_PROMPTS, title="Silencio", visual="El perro ladea la cabeza", narration="",
                        sound="un ladrido suave", duration=2, sound_prompt="a soft dog bark", search_query="dog head"),
                   dict(SCENE_PROMPTS, title="Cierre", visual="El perro corre feliz",
                        narration="Es verdad, y lo puedes comprobar hoy mismo.", sound="", search_query="dog run")]}
KIT = {"virality": {"overall": 80}, "on_screen_hooks": ["Hook A", "Hook B"],
       "youtube": {"title": "T", "description": "D", "hashtags": ["#a"], "tags": ["t"]},
       "tiktok": {"caption": "c"}, "instagram": {"caption": "c"}, "facebook": {"caption": "c"},
       "seed_comments": ["s"], "teaser": "t"}
BASE = dict(idea="Por qué tu perro entiende palabras", duration=20, tone="fun", instructions="", num_scenes=3,
            llm_provider="OpenAI (ChatGPT)", llm_model="gpt-test", confirm_paid=True, own_files=[],
            image_provider="image_api", make_cover=False, use_keyframes=False, style="auto", voice_provider="openai_tts",
            voice="onyx", clone_sample=None, scene_sound="none", music_source="library", make_kit=True,
            review_steps=False, ai_width=480, ai_height=832, ai_frames=121, ai_steps=20, montage={})
VOICE = 6.0                      # the fake TTS always answers with 6 s of audio
NARRATED = VOICE + P.BREATH      # a narrated scene lasts its voice plus a breath


def fake_words(audio, language="es", script=""):
    words, t = [], 0.2
    for w in script.split():
        words.append({"word": w, "start": round(t, 2), "end": round(t + 0.3, 2)})
        t += 0.35
    return words


@pytest.fixture
def services(http, keys, media, fonts, monkeypatch):
    real_sleep = time.sleep
    monkeypatch.setattr(time, "sleep", lambda s: real_sleep(min(s, 0.001)))
    monkeypatch.setattr(P, "transcribe_words", fake_words)
    monkeypatch.setattr(P, "drive_responds", lambda timeout=10: True)
    for s in ("openai", "pexels", "wavespeed", "elevenlabs", "image_api", "gemini"):
        keys(s, api_key=f"key-{s}")
    if not os.path.exists(f"{L.MUSIC_DIR}/song.mp3"):
        os.system(f"ffmpeg -loglevel error -f lavfi -i sine=frequency=500:duration=3 {L.MUSIC_DIR}/song.mp3")
    data = {k: open(media[k], "rb").read() for k in ("portrait", "landscape", "voice", "image", "with_sound", "tone_mp3")}
    state = {"orientation": [], "queries": [], "plan": copy.deepcopy(PLAN), "data": data, "chats": []}

    def chat(call):
        system = call["json"]["messages"][0]["content"]
        state["chats"].append(call["json"]["messages"])
        if "director" in system:
            payload = state["plan"]
        elif "into prompts" in system:
            payload = {"scenes": [{"id": 2, "image_prompt": "a dog sleeping", "motion_prompt": "zoom out",
                                   "search_query": "dog sleeping", "sound_prompt": "snoring"}]}
        else:
            payload = KIT
        return FakeResponse(json={"choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}]})

    def pexels(call):
        orientation = call["params"]["orientation"]
        state["orientation"].append(orientation)
        state["queries"].append(call["params"]["query"])
        w, h = (1920, 1080) if orientation == "landscape" else (1080, 1920)
        slug = call["params"]["query"].replace(" ", "_")
        return FakeResponse(json={"videos": [{"video_files": [{"link": f"https://pex/{orientation}/{slug}/{i}.mp4",
                                                               "width": w, "height": h}]} for i in range(3)]})
    http.add("POST", r"api\.openai\.com/v1/chat/completions", chat)
    http.add("POST", r"api\.openai\.com/v1/audio/speech", FakeResponse(content=data["voice"]))
    http.add("GET", r"api\.pexels\.com/videos/search", pexels)
    http.add("GET", r"https://pex/portrait/", FakeResponse(content=data["portrait"]))
    http.add("GET", r"https://pex/landscape/", FakeResponse(content=data["landscape"]))
    http.add("POST", r"wavespeed\.ai/api/v3/", FakeResponse(json={"code": 200, "data": {"id": "w9"}}))
    http.add("GET", r"predictions/w9/result", FakeResponse(json={"code": 200, "data": {"status": "completed",
                                                                                       "outputs": ["https://cdn.x/w9.mp4"]}}))
    http.add("GET", r"cdn\.x/w9", FakeResponse(content=data["portrait"]))
    http.add("POST", r"images/generations", FakeResponse(json={"data": [{"b64_json": base64.b64encode(data["image"]).decode()}]}))
    http.add("POST", r"api\.elevenlabs\.io/v1/music", FakeResponse(status=402, json={"detail": {"message": "paid plan"}}))
    http.add("POST", r"api\.elevenlabs\.io/v1/sound-generation", FakeResponse(content=data["tone_mp3"]))
    return state


def produce(**changes):
    snapshots = list(P.produce_reel(dict(BASE, **changes)))
    return snapshots, snapshots[-1]


def test_stock_reel_end_to_end(services, http):
    snapshots, last = produce(clip_provider="pexels")
    assert last["done"] and not last["failed"], last["log"][-800:]
    assert all(a["pct"] <= b["pct"] for a, b in zip(snapshots, snapshots[1:]))
    assert set(services["orientation"]) == {"portrait"}
    assert services["queries"] == ["dog", "dog head", "dog run"]               # one stock search per scene
    width, height, duration, has_audio, _ = probe(last["video"])
    assert (width, height) == (1080, 1920) and has_audio
    assert abs(duration - (2 * NARRATED + 2)) < 0.15                          # two narrated scenes + a 2 s silent one
    reel = last["reel_dir"]
    assets = P.load_assets(reel)
    assert assets["clips"] == [f"clips/scene_0{i}.mp4" for i in (1, 2, 3)] and assets["clip_fill"] == ["loop"] * 3
    assert assets["language"] == "es" and assets["hooks"] == ["Hook A", "Hook B"]
    assert assets["music"].startswith(L.MUSIC_DIR)
    for name in ("plan.json", "script.txt", "publish_kit.json", "publish_kit.md", "subtitles.srt",
                 "audio/scene_01_voice.mp3", "audio/scene_01_words.json", "audio/scene_03_voice.mp3"):
        assert os.path.exists(f"{reel}/{name}"), name
    assert not glob.glob(f"{reel}/audio/scene_02_voice.*")                     # the silent scene has no voice
    assert open(f"{reel}/script.txt", encoding="utf-8").read() == \
        "¿Sabías que tu perro entiende más de cien palabras? Es verdad, y lo puedes comprobar hoy mismo."
    assert "## Scene 2: Silencio (0:06–0:08)" in last["script"] and "Duration: 2s" in last["script"]
    system, user = services["chats"][0][0]["content"], services["chats"][0][1]["content"]
    assert "~20-second" in system and "fun and witty" in system and "Exactly 3 scenes" in system
    assert "detect the language the idea is written in" in system and user == f"Reel idea: {BASE['idea']}"
    kit_user = services["chats"][1][1]["content"]
    assert "Final narration:\n¿Sabías que" in kit_user and "Spanish-speaking" in services["chats"][1][0]["content"]
    subtitles = open(f"{reel}/subtitles.srt", encoding="utf-8").read()
    assert "\n00:00:08,5" in subtitles                # scene 3 starts its first word at 6.3 + 2 + 0.2 s


def test_every_step_waits_for_review_and_items_regenerate_alone(services, http):
    _, last = produce(clip_provider="pexels", review_steps=True, idea="Lobos que ayudan")
    reel = last["reel_dir"]
    assert last["review"]["stage"] == "storyboard" and not last["done"]
    assert P.read_run_state(reel)["status"] == "review"
    text = last["review"]["storyboard"]
    assert text.startswith("Title: Tu perro entiende") and "Narration: —" in text and "Sound: un ladrido suave" in text
    assert not glob.glob(f"{reel}/audio/*") and not os.path.exists(f"{reel}/publish_kit.json")

    edited = text.replace("El perro ladea la cabeza", "El perro duerme en el sofá")
    edited = edited.replace("Es verdad, y lo puedes comprobar hoy mismo.", "Compruébalo hoy.")
    http.calls.clear()
    last = list(P.continue_reel(reel, edited))[-1]
    plan = P.load_plan(reel)
    assert plan["scenes"][1]["visual"] == "El perro duerme en el sofá" and plan["scenes"][1]["search_query"] == "dog sleeping"
    assert plan["scenes"][0]["search_query"] == "dog" and plan["scenes"][2]["narration"] == "Compruébalo hoy."
    asked = [m for m in services["chats"] if "into prompts" in m[0]["content"]]
    assert len(asked) == 1 and asked[0][1]["content"].endswith("scene ids: 2")   # only the edited scene
    assert last["review"]["stage"] == "audio"                       # stock and no cover: no images to review
    items = last["review"]["items"]
    assert [i["id"] for i in items] == ["scene1_audio", "scene2_audio", "scene3_audio"]
    assert items[1]["path"] is None and "no narration" in items[1]["label"]

    tts = lambda: [c for c in http.calls if "audio/speech" in c["url"]]
    before = len(tts())
    last = list(P.regenerate_item(reel, "scene3_audio", "Compruébalo hoy mismo con tu perro."))[-1]
    assert len(tts()) == before + 1 and tts()[-1]["json"]["input"] == "Compruébalo hoy mismo con tu perro."
    assert last["review"]["stage"] == "audio" and P.read_run_state(reel)["status"] == "review"
    assert P.load_plan(reel)["scenes"][2]["narration"] == "Compruébalo hoy mismo con tu perro."

    last = list(P.continue_reel(reel))[-1]
    assert last["review"]["stage"] == "clips"
    assert [i["id"] for i in last["review"]["items"]] == ["scene1_clip", "scene2_clip", "scene3_clip"]
    services["queries"].clear()
    last = list(P.regenerate_item(reel, "scene2_clip", "sleepy puppy"))[-1]
    assert services["queries"] == ["sleepy puppy"] and not last["failed"]
    assert P.load_plan(reel)["scenes"][1]["search_query"] == "sleepy puppy"

    last = list(P.continue_reel(reel))[-1]
    assert last["done"] and last["review"] is None and P.read_run_state(reel)["status"] == "done"
    assert P.approved_stages(reel) == ["storyboard", "audio", "clips"]


def test_regenerating_the_storyboard_uses_your_notes(services):
    _, last = produce(clip_provider="pexels", review_steps=True, idea="Gatos curiosos")
    reel = last["reel_dir"]
    services["plan"]["title"] = "Gatos, versión 2"
    last = list(P.regenerate_storyboard(reel, "más humor"))[-1]
    assert last["review"]["stage"] == "storyboard" and last["review"]["storyboard"].startswith("Title: Gatos, versión 2")
    user = services["chats"][-1][1]["content"]
    assert "Previous storyboard" in user and "Changes the creator asked for: más humor" in user


def test_a_bad_storyboard_edit_is_explained_and_nothing_is_lost(services):
    _, last = produce(clip_provider="pexels", review_steps=True, idea="Edición rota")
    reel = last["reel_dir"]
    last = list(P.continue_reel(reel, "Title: sin escenas"))[-1]
    assert last["failed"] and "No scenes found" in last["log"]
    assert P.load_plan(reel)["scenes"][0]["title"] == "El gancho"


def test_scene_sound_effects_and_clip_sound_in_silent_scenes(services, http):
    calls = []

    def interactions(call):
        calls.append(call["json"])
        item = {"type": "video", "data": base64.b64encode(services["data"]["with_sound"]).decode()}
        return FakeResponse(json={"status": "completed", "steps": [{"type": "model_output", "content": [item]}]})
    http.add("POST", r"v1beta/interactions$", interactions)
    _, last = produce(clip_provider="gemini_omni", use_keyframes=False, scene_sound="elevenlabs", music_source="none",
                      idea="Sonido por escena")
    assert last["done"], last["log"][-600:]
    sfx = [c for c in http.calls if "sound-generation" in c["url"]]
    assert len(sfx) == 1 and sfx[0]["json"]["duration_seconds"] == 2 and sfx[0]["json"]["text"] == "a soft dog bark"
    durations = [c["response_format"]["duration"] for c in calls]          # clips ask for the scene length
    assert durations[1] == "3s" and durations[0] == durations[2] and durations[0] in ("6s", "7s")
    reel = last["reel_dir"]
    assert os.path.exists(f"{reel}/audio/scene_02_sound.mp3") and P.load_assets(reel)["clip_audio"] == [True] * 3
    _, _, duration, has_audio, _ = probe(last["video"])
    assert has_audio and abs(duration - (2 * NARRATED + 2)) < 0.15


def test_resume_continues_from_the_failed_step_without_paying_twice(services, http):
    fail = {"on": True}

    def poll(call):
        task = call["url"].split("/predictions/")[1].split("/")[0]
        if task == "w3" and fail["on"]:
            return FakeResponse(json={"code": 200, "data": {"status": "failed", "error": "provider overloaded"}})
        return FakeResponse(json={"code": 200, "data": {"status": "completed", "outputs": [f"https://cdn.x/{task}.mp4"]}})
    created = iter(f"w{i}" for i in range(1, 99))
    http.add("POST", r"wavespeed\.ai/api/v3/", lambda call: FakeResponse(json={"code": 200, "data": {"id": next(created)}}))
    http.add("GET", r"predictions/w\d+/result", poll)
    http.add("GET", r"cdn\.x/w\d+", FakeResponse(content=services["data"]["portrait"]))
    _, last = produce(clip_provider="wavespeed", idea="Lobos que ayudan", music_source="none")
    reel = last["reel_dir"]
    assert last["failed"] and "Resume" in last["log"]
    assert P.read_run_state(reel)["status"] == "failed" and "Clip 3" in P.read_run_state(reel)["stage"]
    assert sorted(os.path.basename(p) for p in glob.glob(f"{reel}/clips/*")) == ["scene_01.mp4", "scene_02.mp4"]
    settings = P.load_settings(reel)
    assert settings["idea"] == "Lobos que ayudan" and settings["clip_provider"] == "wavespeed" and "_meta" in settings

    fail["on"] = False
    http.calls.clear()
    last = list(P.continue_reel(reel))[-1]
    assert last["done"] and last["reel_dir"] == reel
    paid = [c for c in http.calls if c["method"] == "POST" and "wavespeed" in c["url"]]
    assert len(paid) == 1                                     # only the clip that failed
    assert not any("chat/completions" in c["url"] or "audio/speech" in c["url"] for c in http.calls)
    assert last["log"].count("♻️") >= 4                      # storyboard, voices 1 and 3, clips 1-2
    assert P.read_run_state(reel)["status"] == "done"


def test_paid_clips_without_confirmation_spend_nothing(services, http):
    _, last = produce(clip_provider="wavespeed", confirm_paid=False, idea="Sin confirmar")
    assert last["failed"] and "I accept the charge" in last["log"]
    assert not any(c["method"] == "POST" for c in http.calls)


def test_own_files_are_used_in_order_and_repeat(services, media):
    _, last = produce(clip_provider="upload", own_files=[media["portrait"], media["image"]], idea="Mis propios clips")
    assert last["done"], last["log"][-600:]
    assets = P.load_assets(last["reel_dir"])
    assert assets["clips"] == ["clips/own_01.mp4", "clips/own_02.png", "clips/own_01.mp4"]
    assert assets["clip_fill"] == ["loop"] * 3


def test_ai_photos_with_cover_and_landscape_variants(services):
    _, last = produce(clip_provider="ai_photos", idea="Fotos IA", make_cover=True, music_source="elevenlabs",
                      montage={"aspect": "16:9", "variants": 2})
    assert last["done"] and "AI music failed" in last["log"]
    reel = last["reel_dir"]
    assert os.path.exists(f"{reel}/cover.png") and len(glob.glob(f"{reel}/images/scene_*.png")) == 3
    assert len(last["videos"]) == 2 and probe(last["videos"][1])[:2] == (1920, 1080)


def test_broken_kit_does_not_stop_the_reel(services, http):
    http.add("POST", r"api\.openai\.com/v1/chat/completions", lambda call: FakeResponse(json={"choices": [{"message": {
        "content": json.dumps(PLAN) if "director" in call["json"]["messages"][0]["content"] else "sorry"}}]}))
    _, last = produce(clip_provider="pexels", idea="Kit roto")
    assert last["done"] and last["kit"] is None and "Publish kit failed" in last["log"]


def test_a_new_production_never_overwrites_an_earlier_reel(services):
    _, first = produce(clip_provider="pexels", idea="Misma idea", music_source="none")
    _, second = produce(clip_provider="pexels", idea="Misma idea", music_source="none")
    assert first["done"] and second["done"]
    assert second["reel_dir"] == first["reel_dir"] + "-2" and os.path.exists(first["video"])


def test_stopping_marks_the_reel_as_stopped(services):
    generator = P.produce_reel(dict(BASE, clip_provider="pexels", idea="Detener a mitad", music_source="none"))
    for snapshot in generator:
        if snapshot["stage"] == "Storyboard ready":
            break
    generator.close()
    assert P.read_run_state(snapshot["reel_dir"])["status"] == "stopped"


def test_gemini_omni_clips_start_from_nano_banana_frames(services, http):
    calls = []

    def interactions(call):
        body = call["json"]
        calls.append(body)
        kind = body["response_format"]["type"]
        data = services["data"]["portrait"] if kind == "video" else services["data"]["image"]
        item = {"type": kind, "data": base64.b64encode(data).decode()}
        return FakeResponse(json={"status": "completed", "steps": [{"type": "model_output", "content": [item]}]})
    http.add("POST", r"v1beta/interactions$", interactions)
    _, last = produce(clip_provider="gemini_omni", image_provider="nano_banana", make_cover=True, use_keyframes=True,
                      idea="Lobos con Omni", music_source="none")
    assert last["done"], last["log"][-600:]
    images = [c for c in calls if c["response_format"]["type"] == "image"]
    videos = [c for c in calls if c["response_format"]["type"] == "video"]
    assert len(images) == 4 and len(videos) == 3                       # cover + one frame per scene; 3 clips
    assert all(isinstance(v["input"], list) and v["input"][0]["type"] == "image" for v in videos)
