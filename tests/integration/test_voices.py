"""Native voice clients against a fake HTTP layer, with real ffmpeg conversions, and the key tests."""
import base64, json
import pytest
import studio.lib as L
from helpers import media_duration
from fakehttp import FakeResponse


@pytest.fixture
def voice_keys(keys):
    for s in ("elevenlabs", "openai", "gemini", "siliconflow", "mimo", "minimax", "fish_audio", "voxcpm"):
        keys(s, api_key=f"key-{s}")
    keys("azure", speech_key="az-key", speech_region="westeurope")
    keys("voxcpm", model_id="voxcpm-1")
    keys("minimax", voice_id="my-voice")


@pytest.fixture
def audio(media):
    return {name: open(media[f"tone_{name}"], "rb").read() for name in ("mp3", "wav", "pcm")}


def sse(chunks, done=True):
    lines = []
    for chunk in chunks:
        lines += ["data: " + json.dumps({"type": "speech.audio.delta", "audio": base64.b64encode(chunk).decode()}), ""]
    if done:
        lines += ["data: " + json.dumps({"type": "speech.audio.done"}), ""]
    return lines


@pytest.fixture
def voice_api(http, audio):
    mp3, wav, pcm = audio["mp3"], audio["wav"], audio["pcm"]
    half = len(wav) // 2
    http.add("POST", r"api\.elevenlabs\.io/v1/text-to-speech/", FakeResponse(content=mp3))
    http.add("POST", r"api\.openai\.com/v1/audio/speech", FakeResponse(content=mp3))
    http.add("POST", r"tts\.speech\.microsoft\.com/cognitiveservices/v1", FakeResponse(content=mp3))
    http.add("POST", r"generativelanguage\.googleapis\.com/.*:generateContent", FakeResponse(json={"candidates": [
        {"content": {"parts": [{"inlineData": {"mimeType": "audio/L16;codec=pcm;rate=24000",
                                               "data": base64.b64encode(pcm).decode()}}]}}]}))
    http.add("POST", r"api\.siliconflow\.cn/v1/audio/speech", FakeResponse(content=mp3))
    http.add("POST", r"api\.xiaomimimo\.com/v1/chat/completions", FakeResponse(json={"choices": [
        {"message": {"audio": {"data": base64.b64encode(wav).decode()}}}]}))
    http.add("POST", r"api\.minimax\.io/v1/t2a_v2", FakeResponse(json={"base_resp": {"status_code": 0},
                                                                     "data": {"status": 2, "audio": mp3.hex()}}))
    http.add("POST", r"api\.fish\.audio/v1/tts", FakeResponse(content=mp3))
    http.add("POST", r"api\.modelbest\.cn/v1/audio/speech", FakeResponse(lines=sse([wav[:half], wav[half:]])))
    return http


EXTERNAL = [p for p in L.VOICE_PROVIDERS if p not in ("edge", "f5")]


@pytest.mark.parametrize("provider", EXTERNAL)
def test_every_external_voice_gives_a_playable_file(provider, voice_keys, voice_api):
    voices = L.voice_choices(provider)
    path, message = L.synthesize_voice("Hola mundo, esto es una prueba.", provider, voices[0][1] if voices else "",
                                       output_path=f"{L.WORK_DIR}/{provider}.mp3")
    assert path, message
    assert 0.8 < media_duration(path) < 1.3


def test_request_shapes(voice_keys, voice_api):
    for provider in EXTERNAL:
        voices = L.voice_choices(provider)
        L.synthesize_voice("Hola", provider, voices[0][1] if voices else "", output_path=f"{L.WORK_DIR}/{provider}.mp3")
    http = voice_api
    c = http.last("text-to-speech")
    assert c["url"].endswith("/JBFqnCBsd6RMkjVDRZzb") and c["json"]["model_id"] == "eleven_multilingual_v2"
    assert c["headers"]["xi-api-key"] == "key-elevenlabs"
    c = http.last("microsoft")
    assert b'<voice name="es-MX-JorgeNeural">' in c["data"] and "westeurope." in c["url"]
    assert c["headers"]["Ocp-Apim-Subscription-Key"] == "az-key" and "mp3" in c["headers"]["X-Microsoft-OutputFormat"]
    c = http.last("generateContent")
    config = c["json"]["generationConfig"]
    assert config["responseModalities"] == ["AUDIO"] and "gemini-2.5-flash-preview-tts" in c["url"]
    assert config["speechConfig"]["voiceConfig"]["prebuiltVoiceConfig"]["voiceName"] == "Charon"
    c = http.last("siliconflow")
    assert c["json"]["model"] == "FunAudioLLM/CosyVoice2-0.5B" and c["json"]["voice"].endswith(":alex")
    c = http.last("xiaomimimo")
    assert c["json"]["messages"][1] == {"role": "assistant", "content": "Hola"}
    assert c["json"]["audio"] == {"format": "wav", "voice": "Milo"}
    c = http.last("minimax")
    assert c["json"]["output_format"] == "hex" and c["json"]["voice_setting"]["voice_id"] == "my-voice"
    c = http.last("fish.audio")
    assert c["headers"]["model"] == "s2.1-pro-free" and c["json"]["reference_id"] == "7b6131ba75ba47c98a46c847db729ab6"
    c = http.last("modelbest")
    assert c["json"]["stream"] is True and c["json"]["model"] == "voxcpm-1"


def test_voice_errors_are_explained(voice_keys, http, audio):
    http.add("POST", r"api\.minimax\.io/v1/t2a_v2", FakeResponse(json={"base_resp": {"status_code": 1004, "status_msg": "auth failed"}}))
    path, message = L.synthesize_voice("x", "minimax", "v", output_path=f"{L.WORK_DIR}/mm.mp3")
    assert path is None and "auth failed" in message
    http.add("POST", r"api\.fish\.audio/v1/tts", FakeResponse(status=402, text="payment required"))
    path, message = L.synthesize_voice("x", "fish_audio", "", output_path=f"{L.WORK_DIR}/f.mp3")
    assert path is None and "HTTP 402" in message
    http.add("POST", r"api\.modelbest\.cn/v1/audio/speech", FakeResponse(lines=sse([audio["wav"][:100]], done=False)))
    path, message = L.synthesize_voice("x", "voxcpm", "default", output_path=f"{L.WORK_DIR}/v.mp3")
    assert path is None and "cortó" in message
    path, message = L.synthesize_voice("x", "f5", "", output_path=f"{L.WORK_DIR}/f5.mp3")
    assert path is None and "Celda 6" in message


def test_connection_tests(http):
    def auth_aware(call):
        good = call["headers"].get("Authorization") == "Apikey real"
        return FakeResponse(200 if good else 401, json={"ok": good})
    http.add("GET", r"upload-post\.com/api/uploadposts/history", auth_aware)
    assert L.test_credential("upload_post", {"api_key": "real", "username": "me"})[0] is True
    ok, message = L.test_credential("upload_post", {"api_key": "real", "username": ""})
    assert ok is False and "Usuario" in message
    http.add("GET", r"elevenlabs\.io/v1/user/subscription", FakeResponse(401, text='{"detail":{"status":"missing_permissions"}}'))
    assert L.test_credential("elevenlabs", {"api_key": "x"})[0] is True
    http.add("GET", r"modelbest\.cn/v1/models", FakeResponse(200, json={}))
    ok, _ = L.test_credential("voxcpm", {"api_key": "x", "model_id": "m", "base_url": "https://api.modelbest.cn/v1"})
    assert ok is None                                          # an open endpoint never "confirms" a key
