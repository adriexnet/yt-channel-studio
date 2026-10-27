"""Paid AI-video clients, ElevenLabs Music and Upload-Post against a fake HTTP layer."""
import os, time
import pytest
import studio.pipeline as P
from fakehttp import FakeResponse


@pytest.fixture(autouse=True)
def fast_sleep(monkeypatch):
    real = time.sleep
    monkeypatch.setattr(time, "sleep", lambda s: real(min(s, 0.001)))


@pytest.fixture
def video_keys(keys):
    for s in ("wavespeed", "volcengine_seedance", "ofox", "muapi", "metaso_minimax", "infistar", "elevenlabs"):
        keys(s, api_key=f"key-{s}")
    keys("upload_post", api_key="up-key", username="mi_canal")


def sequence(*responses):
    queue = list(responses)
    return lambda call: queue.pop(0) if len(queue) > 1 else queue[0]


CASES = {   # provider: (create url, create answer, poll url, poll answers, download url)
    "wavespeed": (r"api\.wavespeed\.ai/api/v3/bytedance/", {"code": 200, "data": {"id": "w1"}}, r"predictions/w1/result",
                  [{"code": 200, "data": {"status": "processing"}},
                   {"code": 200, "data": {"status": "completed", "outputs": ["https://cdn.x/w1.mp4"]}}], r"cdn\.x/w1"),
    "volcengine_seedance": (r"contents/generations/tasks$", {"id": "s1"}, r"tasks/s1$",
                            [{"status": "running"}, {"status": "succeeded", "content": {"video_url": "https://cdn.x/s1.mp4"}}],
                            r"cdn\.x/s1"),
    "ofox": (r"api\.ofox\.ai/v1/videos$", {"id": "o1"}, r"videos/o1$",
             [{"status": "in_progress"}, {"status": "completed", "mirror_urls": ["https://cdn.x/o1.mp4"]}], r"cdn\.x/o1"),
    "muapi": (r"api\.muapi\.ai/api/v1/seedance-lite-t2v$", {"request_id": "m1"}, r"predictions/m1/result",
              [{"status": "processing"}, {"status": "completed", "outputs": ["https://cdn.x/m1.mp4"]}], r"cdn\.x/m1"),
    "metaso_minimax": (r"minimax/v2/video_generation$", {"task_id": "t1"}, r"query/video_generation/t1",
                       [{"task": {"status": "queued"}}, {"task": {"status": "succeeded", "content": {"url": "https://cdn.x/t1.mp4"}}}],
                       r"cdn\.x/t1"),
    "infistar": (r"infistar\.cc/v1/videos$", {"id": "i1"}, r"videos/i1$",
                 [{"status": "queued"}, {"status": "completed"}], r"videos/i1/content"),
}


@pytest.mark.parametrize("provider", list(CASES))
def test_paid_clip_is_created_once_polled_and_downloaded(provider, video_keys, http):
    create_rx, created, poll_rx, polls, download_rx = CASES[provider]
    http.add("POST", create_rx, FakeResponse(json=created))
    http.add("GET", poll_rx, sequence(*[FakeResponse(json=p) for p in polls]))
    http.add("GET", download_rx, FakeResponse(content=b"video-bytes"))
    out, raw = f"{P.WORK_DIR}/{provider}.mp4", []
    P.generate_ai_video(provider, "a dog running on a beach", 20, "9:16", out, on_raw=raw.append)
    posts = [c for c in http.calls if c["method"] == "POST"]
    assert len(posts) == 1 and open(out, "rb").read() == b"video-bytes" and any("created" in r for r in raw)
    body = posts[0]["json"]
    low, high = P.AI_VIDEO_CLIENTS[provider][1:]
    assert low <= int(body.get("duration") or body.get("seconds")) <= high
    assert (body.get("aspect_ratio") or body.get("ratio") or body.get("size")) in ("9:16", "720x1280")
    if provider == "infistar":
        assert http.last("videos/i1/content")["headers"]["Authorization"] == "Bearer key-infistar"


def test_a_rejected_paid_task_is_never_retried(video_keys, http):
    http.add("POST", r"wavespeed", FakeResponse(status=502, text="bad gateway"))
    with pytest.raises(RuntimeError, match="rechazó"):
        P.generate_ai_video("wavespeed", "x", 5, "9:16", f"{P.WORK_DIR}/x.mp4")
    assert len([c for c in http.calls if c["method"] == "POST"]) == 1


def test_polling_survives_transient_errors_and_reports_the_reason(video_keys, http):
    http.add("POST", r"ofox", FakeResponse(json={"id": "o2"}))
    http.add("GET", r"videos/o2$", sequence(FakeResponse(status=503), FakeResponse(status=503),
                                            FakeResponse(json={"status": "failed", "error": {"message": "content policy"}})))
    with pytest.raises(RuntimeError, match="content policy"):
        P.generate_ai_video("ofox", "x", 5, "9:16", f"{P.WORK_DIR}/x.mp4")


def test_elevenlabs_music_request(video_keys, http):
    http.add("POST", r"api\.elevenlabs\.io/v1/music$", FakeResponse(content=b"mp3"))
    P.elevenlabs_music("lofi chill", 1.0, f"{P.WORK_DIR}/m.mp3")
    body = http.last("v1/music")["json"]
    assert body["force_instrumental"] is True and body["music_length_ms"] == 3000 and body["model_id"] == "music_v1"
    http.add("POST", r"api\.elevenlabs\.io/v1/music$", FakeResponse(status=402, json={"detail": {"message": "paid plan"}}))
    with pytest.raises(RuntimeError, match="402"):
        P.elevenlabs_music("x", 10, f"{P.WORK_DIR}/m.mp3")


def test_publish_sends_the_video_with_the_api_key(video_keys, http, media):
    kit = P.normalize_kit({"youtube": {"title": "T"}, "tiktok": {"caption": "c"}, "instagram": {"caption": "c"},
                           "facebook": {"caption": "c"}})
    http.add("POST", r"api\.upload-post\.com/api/upload$", FakeResponse(json={"success": True, "request_id": "r1"}))
    body = P.publish_reel(media["portrait"], kit, ["tiktok"])
    call = http.last("api/upload")
    assert call["headers"]["Authorization"] == "Apikey up-key" and "video" in call["files"] and body["request_id"] == "r1"
    with pytest.raises(RuntimeError, match="kit"):
        P.publish_reel(media["portrait"], None, ["tiktok"])
    with pytest.raises(RuntimeError, match="plataforma"):
        P.publish_reel(media["portrait"], kit, [])
