"""The quick-start cell runs the environment, the ticked tools and the Control Center; a failed tool is skipped."""
from studio import setup, control_center
from studio.tools import ollama, forge, comfyui, audio


def test_quick_start_runs_what_is_ticked(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(setup, "environment", lambda: calls.append("env"))
    monkeypatch.setattr(ollama, "start", lambda: calls.append("ollama"))
    monkeypatch.setattr(forge, "start", lambda: (_ for _ in ()).throw(RuntimeError("no GPU")))
    monkeypatch.setattr(comfyui, "start", lambda models: calls.append(("comfy", models)))
    monkeypatch.setattr(audio, "start", lambda: calls.append("audio"))
    monkeypatch.setattr(control_center, "start", lambda: calls.append("cc"))
    setup.quick_start(ollama=True, forge=True, wan=True, ltx=True, audio=False)
    assert calls == ["env", "ollama", ("comfy", ["wan22", "ltxv"]), "cc"]
    assert "Forge did not start: no GPU" in capsys.readouterr().out
    calls.clear()
    setup.quick_start(ollama=False, forge=False, wan=False, ltx=False)
    assert calls == ["env", "cc"]
