"""Full productions (fake HTTP for every service, real ffmpeg): each content kind picks its provider
independently and the reel folder keeps everything needed to re-assemble it."""
import base64, glob, json, os, time
import pytest
import studio.lib as L
import studio.pipeline as P
from fakehttp import FakeResponse
from helpers import probe

PLAN = {"title_slug": "perros", "hook": "¿Sabías esto de tu perro?", "on_screen_text": "Tu perro sabe esto",
        "script": "¿Sabías que tu perro entiende más de cien palabras? Es verdad, y lo puedes comprobar hoy mismo.",
        "search_terms": ["dog"], "cover_prompt": "dog", "music_prompt": "warm acoustic",
        "scenes": [{"id": i, "search_query": f"dog {i}", "image_prompt": "dog", "motion_prompt": "pan"} for i in (1, 2, 3)]}
KIT = {"virality": {"overall": 80}, "on_screen_hooks": ["Hook A", "Hook B"],
       "youtube": {"title": "T", "description": "D", "hashtags": ["#a"], "tags": ["t"]},
       "tiktok": {"caption": "c"}, "instagram": {"caption": "c"}, "facebook": {"caption": "c"},
       "seed_comments": ["s"], "teaser": "t"}
BASE = dict(idea="Por qué tu perro entiende palabras", language="es", duration=20, tone="divertido", instructions="",
            num_scenes=3, llm_provider="OpenAI (ChatGPT)", llm_model="gpt-test", confirm_paid=True, own_files=[],
            keyframe_provider="none", voice_provider="openai_tts", voice="onyx", clone_sample=None,
            cover_provider="none", music_source="library", make_kit=True, ai_width=480, ai_height=832,
            ai_frames=49, ai_steps=20, montage={})


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
    for s in ("openai", "pexels", "wavespeed", "elevenlabs", "image_api"):
        keys(s, api_key=f"key-{s}")
    if not os.path.exists(f"{L.MUSIC_DIR}/song.mp3"):
        os.system(f"ffmpeg -loglevel error -f lavfi -i sine=frequency=500:duration=3 {L.MUSIC_DIR}/song.mp3")
    data = {k: open(media[k], "rb").read() for k in ("portrait", "landscape", "voice", "image")}
    seen = {"orientation": [], "portrait": data["portrait"]}

    def chat(call):
        payload = PLAN if "director" in call["json"]["messages"][0]["content"] else KIT
        return FakeResponse(json={"choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}]})

    def pexels(call):
        orientation = call["params"]["orientation"]
        seen["orientation"].append(orientation)
        w, h = (1920, 1080) if orientation == "landscape" else (1080, 1920)
        return FakeResponse(json={"videos": [{"video_files": [{"link": f"https://pex/{orientation}/{i}.mp4", "width": w,
                                                               "height": h}]} for i in range(3)]})
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
    return seen


def produce(**changes):
    snapshots = list(P.produce_reel(dict(BASE, **changes)))
    return snapshots, snapshots[-1]


def test_stock_reel_end_to_end(services, http):
    snapshots, last = produce(clip_provider="pexels")
    assert last["done"] and not last["failed"], last["log"][-800:]
    assert all(a["pct"] <= b["pct"] for a, b in zip(snapshots, snapshots[1:]))
    assert set(services["orientation"]) == {"portrait"}
    width, height, duration, _, _ = probe(last["video"])
    assert (width, height) == (1080, 1920) and abs(duration - 6.0) < 0.3
    reel = last["reel_dir"]
    assets = P.load_assets(reel)
    assert assets["voice"].startswith("voice") and len(assets["clips"]) == 3 and assets["music"].startswith(L.MUSIC_DIR)
    assert assets["language"] == "es" and assets["hooks"] == ["Hook A", "Hook B"]
    for name in ("words.json", "plan.json", "script.txt", "publish_kit.json", "publish_kit.md", "subtitles.srt"):
        assert os.path.exists(f"{reel}/{name}"), name
    prompt = http.calls[0]["json"]["messages"][0]["content"]
    assert "~20 seconds" in prompt and "fun and witty" in prompt


def test_landscape_variants_and_failed_ai_music(services):
    _, last = produce(clip_provider="pexels", montage={"aspect": "16:9", "variants": 2}, music_source="elevenlabs")
    assert last["done"] and "AI music failed" in last["log"]
    assert set(services["orientation"]) == {"landscape"}
    assert len(last["videos"]) == 2 and probe(last["videos"][1])[:2] == (1920, 1080)


def test_own_uploaded_clips(services, media):
    _, last = produce(clip_provider="upload", own_files=[media["portrait"], media["image"]], idea="Mis propios clips")
    assert last["done"] and len(glob.glob(f"{last['reel_dir']}/clips/own_*")) == 2


def test_paid_ai_clips_one_per_scene(services, http):
    _, last = produce(clip_provider="wavespeed", idea="Clips de pago", music_source="none")
    assert last["done"]
    assert len([c for c in http.calls if c["method"] == "POST" and "wavespeed" in c["url"]]) == 3


def test_ai_photos_with_cover(services):
    _, last = produce(clip_provider="api_photos", idea="Fotos IA", cover_provider="image_api")
    assert last["done"] and os.path.exists(f"{last['reel_dir']}/cover.png")
    assert len(glob.glob(f"{last['reel_dir']}/images/*.png")) == 3


def test_paid_clips_without_confirmation_spend_nothing(services, http):
    _, last = produce(clip_provider="wavespeed", confirm_paid=False, idea="Sin confirmar")
    assert last["failed"] and "Confirmo el cobro" in last["log"]
    assert not any(c["method"] == "POST" for c in http.calls)


def test_broken_kit_does_not_stop_the_reel(services, http):
    http.add("POST", r"api\.openai\.com/v1/chat/completions", lambda call: FakeResponse(json={"choices": [{"message": {
        "content": json.dumps(PLAN) if "director" in call["json"]["messages"][0]["content"] else "lo siento"}}]}))
    _, last = produce(clip_provider="pexels", idea="Kit roto")
    assert last["done"] and last["kit"] is None and "Publish kit failed" in last["log"]


def test_resume_continues_from_the_failed_step_without_paying_twice(services, http):
    fail = {"scene": 3}

    def poll(call):
        task = call["url"].split("/predictions/")[1].split("/")[0]
        if task == "w3" and fail["scene"] == 3:
            return FakeResponse(json={"code": 200, "data": {"status": "failed", "error": "provider overloaded"}})
        return FakeResponse(json={"code": 200, "data": {"status": "completed", "outputs": [f"https://cdn.x/{task}.mp4"]}})
    created = iter(f"w{i}" for i in range(1, 99))
    http.add("POST", r"wavespeed\.ai/api/v3/", lambda call: FakeResponse(json={"code": 200, "data": {"id": next(created)}}))
    http.add("GET", r"predictions/w\d+/result", poll)
    http.add("GET", r"cdn\.x/w\d+", FakeResponse(content=services["portrait"]))
    _, last = produce(clip_provider="wavespeed", idea="Lobos que ayudan", music_source="none")
    reel = last["reel_dir"]
    assert last["failed"] and "Reanudar" in last["log"]
    assert P.read_run_state(reel)["status"] == "failed" and "Clip IA 3" in P.read_run_state(reel)["stage"]
    assert sorted(os.path.basename(p) for p in glob.glob(f"{reel}/clips/*")) == ["ai_01.mp4", "ai_02.mp4"]
    settings = P.load_settings(reel)
    assert settings["idea"] == "Lobos que ayudan" and settings["clip_provider"] == "wavespeed" and "_meta" in settings

    fail["scene"] = None
    http.calls.clear()
    snapshots = list(P.produce_reel(dict(BASE, clip_provider="wavespeed", idea="Lobos que ayudan", music_source="none"),
                                    resume_dir=reel))
    last = snapshots[-1]
    assert last["done"] and last["reel_dir"] == reel
    paid = [c for c in http.calls if c["method"] == "POST" and "wavespeed" in c["url"]]
    assert len(paid) == 1                                     # only the clip that failed
    assert not any("chat/completions" in c["url"] or "audio/speech" in c["url"] for c in http.calls)
    assert last["log"].count("♻️") >= 5                      # script, kit, voice, word timings, clips 1-2
    assert P.read_run_state(reel)["status"] == "done"


def test_a_new_production_never_overwrites_an_earlier_reel(services):
    _, first = produce(clip_provider="pexels", idea="Misma idea", music_source="none")
    _, second = produce(clip_provider="pexels", idea="Misma idea", music_source="none")
    assert first["done"] and second["done"]
    assert second["reel_dir"] == first["reel_dir"] + "-2" and os.path.exists(first["video"])


def test_stopping_marks_the_reel_as_stopped(services):
    generator = P.produce_reel(dict(BASE, clip_provider="pexels", idea="Detener a mitad", music_source="none"))
    for snapshot in generator:
        if snapshot["stage"] == "Guion listo":
            break
    generator.close()
    assert P.read_run_state(snapshot["reel_dir"])["status"] == "stopped"
