"""Google: Nano Banana images and Gemini Omni video (Interactions API), live model lists, styles."""
import base64, json, os
import pytest
import studio.lib as L
import studio.pipeline as P
from fakehttp import FakeResponse


def interaction(kind, data=None, uri=None):
    """A completed Interactions answer with one media item in its model_output step."""
    item = {"type": kind, "mime_type": "video/mp4" if kind == "video" else "image/png"}
    item.update({"data": base64.b64encode(data).decode()} if data else {"uri": uri})
    return {"id": "i1", "status": "completed", "model": "m", "object": "interaction",
            "steps": [{"type": "user_input"}, {"type": "thought"}, {"type": "model_output", "content": [item]}]}


@pytest.fixture
def gemini(keys):
    keys("gemini", api_key="g-key", video_resolution="1080p")


def test_nano_banana_image(gemini, http, tmp_path):
    http.add("POST", r"v1beta/interactions$", FakeResponse(json=interaction("image", b"PNGDATA")))
    out = P.generate_image("nano_banana", "a wolf at dawn", str(tmp_path / "a.png"), "9:16")
    assert open(out, "rb").read() == b"PNGDATA"
    call = http.last("interactions")
    assert call["headers"]["x-goog-api-key"] == "g-key"
    body = call["json"]
    assert body["model"] == "gemini-nano-banana-2.1" and body["input"] == "a wolf at dawn"
    assert body["response_format"] == {"type": "image", "mime_type": "image/png", "aspect_ratio": "9:16", "image_size": "2K"}
    assert body["store"] is False and body["stream"] is False and body["background"] is False


def test_media_delivered_as_a_file_uri(gemini, http, tmp_path):
    http.add("POST", r"v1beta/interactions$", FakeResponse(json=interaction(
        "image", uri="https://generativelanguage.googleapis.com/v1beta/files/abc")))
    http.add("GET", r"files/abc:download$", FakeResponse(content=b"FROMURI"))
    out = P.generate_image("nano_banana", "x", str(tmp_path / "b.png"))
    assert open(out, "rb").read() == b"FROMURI"
    call = http.last("files/abc")
    assert call["params"] == {"alt": "media"} and call["headers"]["x-goog-api-key"] == "g-key"


def test_filtered_image_is_explained(gemini, http, tmp_path):
    http.add("POST", r"v1beta/interactions$", FakeResponse(json={"status": "completed", "steps": []}))
    with pytest.raises(RuntimeError, match="no devolvió imagen"):
        P.generate_image("nano_banana", "x", str(tmp_path / "c.png"))
    http.add("POST", r"v1beta/interactions$", FakeResponse(status=400, json={"error": {"message": "bad model"}}))
    with pytest.raises(RuntimeError, match="bad model"):
        P.generate_image("nano_banana", "x", str(tmp_path / "c.png"))


def test_omni_text_and_image_to_video(gemini, http, tmp_path):
    http.add("POST", r"v1beta/interactions$", FakeResponse(json=interaction("video", b"MP4")))
    out = P.generate_ai_video("gemini_omni", "a wolf running", 6.4, "1:1", str(tmp_path / "v.mp4"))
    assert open(out, "rb").read() == b"MP4"
    body = http.last("interactions")["json"]
    assert body["model"] == "gemini-omni-1.1-flash" and "About 6 seconds" in body["input"]
    assert body["response_format"] == {"type": "video", "aspect_ratio": "9:16", "resolution": "1080p"}
    image = tmp_path / "frame.png"
    image.write_bytes(b"IMG")
    P.generate_ai_video("gemini_omni", "a wolf running", 5, "16:9", str(tmp_path / "w.mp4"), image_path=str(image))
    body = http.last("interactions")["json"]
    assert body["input"][0] == {"type": "image", "data": base64.b64encode(b"IMG").decode(), "mime_type": "image/png"}
    assert body["input"][1]["type"] == "text" and body["response_format"]["aspect_ratio"] == "16:9"


def test_live_model_lists(keys, http):
    keys("gemini", api_key="g")
    keys("openai", api_key="o")
    http.add("GET", r"generativelanguage.googleapis.com/v1beta/models$", FakeResponse(json={"models": [
        {"name": "models/gemini-3.8-flash", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/gemini-4-argon", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/gemini-3.8-flash-tts", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/gemini-nano-banana-2.1", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/text-embedding-005", "supportedGenerationMethods": ["embedContent"]}]}))
    assert P.list_llm_models("gemini") == ["gemini-3.8-flash", "gemini-4-argon"]
    http.add("GET", r"api.openai.com/v1/models$", FakeResponse(json={"data": [
        {"id": "gpt-6.1-sol"}, {"id": "gpt-6.1-mini"}, {"id": "text-embedding-3-large"}, {"id": "tts-1-hd"}]}))
    assert P.list_llm_models("openai") == ["gpt-6.1-sol", "gpt-6.1-mini"]
    http.add("GET", r"api.anthropic.com/v1/models$", FakeResponse(status=401, json={}))
    assert P.list_llm_models("anthropic") == ["claude-sonnet-5-5"]          # no key yet: just the default


def test_style_and_automatic_choices_reach_the_prompt():
    prompt = P.build_plan_prompt("auto", "es", 42, "auto", "", "9:16", "talking_objects")
    assert "exactly 6 scenes" in prompt and "first-person monologue" in prompt
    assert "fits the idea and the style best" in prompt and "in the chosen visual style" in prompt
    assert "photorealistic" in P.build_plan_prompt(4, style="narrated") and "exactly 4 scenes" in P.build_plan_prompt(4)
    assert [P.auto_scenes(d) for d in (15, 35, 90)] == [3, 5, 10]
