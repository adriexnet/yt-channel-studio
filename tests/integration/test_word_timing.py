"""Word timing: the voice is decoded by ffmpeg (never by PyAV) and Whisper gets the samples directly."""
import sys, types
import numpy as np
import studio.pipeline as P

SCRIPT = "Hola, ¿cómo estás? Muy bien."


class FakeWhisper:
    received = []

    def __init__(self, *args, **kwargs):
        pass

    def transcribe(self, audio, **options):
        FakeWhisper.received.append((audio, options))
        word = lambda w, s, e: types.SimpleNamespace(word=w, start=s, end=e)
        return [types.SimpleNamespace(words=[word(" hola", 0.1, 0.4), word(" como", 0.5, 0.7), word(" estas", 0.7, 0.9),
                                             word(" muy", 1.2, 1.4), word(" bien", 1.4, 1.7)])], None


def test_whisper_receives_16k_samples_and_the_script_spelling_wins(media, monkeypatch):
    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=FakeWhisper))
    monkeypatch.setattr(P, "_WHISPER", {})
    words = P.transcribe_words(media["voice"], "es", SCRIPT)
    audio, options = FakeWhisper.received[-1]
    assert isinstance(audio, np.ndarray) and audio.dtype == np.float32
    assert abs(len(audio) / 16000 - 6.0) < 0.1                # 6 s of voice at 16 kHz, mono
    assert options["word_timestamps"] and options["language"] == "es" and options["initial_prompt"] == SCRIPT
    assert [w["word"] for w in words] == ["Hola,", "¿cómo", "estás?", "Muy", "bien."]
    P.transcribe_words(media["voice"], "es", SCRIPT)
    assert len(FakeWhisper.received) == 2                      # the model is loaded once and reused


def test_unreadable_voice_is_explained(tmp_path):
    bad = tmp_path / "voice.mp3"
    bad.write_bytes(b"not audio")
    try:
        P.load_audio_16k(str(bad))
        assert False, "should fail"
    except RuntimeError as exc:
        assert "ffmpeg could not read the voice" in str(exc)
