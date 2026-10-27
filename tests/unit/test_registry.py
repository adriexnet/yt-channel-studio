"""Provider registry, keys file and the up-front checks that decide whether a production may start."""
import os, re
import pytest
import studio.lib as L

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_no_legacy_code_left():
    for folder, _, files in os.walk(f"{ROOT}/studio"):
        for name in files:
            if name.endswith(".py"):
                text = open(os.path.join(folder, name), encoding="utf-8").read()
                found = re.findall(r"MoneyPrinter|\bmpt_|\bMPT\b|migrat|older version|legacy|show_copy_button", text)
                assert not found, f"{name}: {found}"


@pytest.mark.parametrize("section", list(L.CREDENTIALS))
def test_credential_definitions(section):
    cred = L.CREDENTIALS[section]
    assert all(f in L.DEFAULT_API_KEYS[section] for f in cred["fields"])
    assert set(cred["required"]) <= set(cred["fields"])
    assert all(f in L.FIELD_LABELS for f in cred["fields"])
    assert cred["category"] in dict(L.CREDENTIAL_CATEGORIES)


def test_providers_reference_known_keys_and_services():
    for registry in (L.CLIP_PROVIDERS, L.VOICE_PROVIDERS, L.IMAGE_PROVIDERS, L.MUSIC_SOURCES):
        for pid, p in registry.items():
            assert all(c in L.CREDENTIALS for c in p.get("creds", [])), pid
            assert all(s in L.SERVICES for s in p.get("services", [])), pid
    assert all(p["type"] in L.CLIP_TYPES for p in L.CLIP_PROVIDERS.values())
    assert all(L.clip_provider_choices(t) for t in L.CLIP_TYPES)
    assert all(pid == "f5" or pid in L.VOICE_ENGINES for pid in L.VOICE_PROVIDERS)


def test_keys_file_is_merged_never_overwritten(keys):
    data = L.load_api_keys()
    assert set(L.DEFAULT_API_KEYS) <= set(data)
    data["openai"]["api_key"] = "sk-mine"
    data["minimax"] = {"api_key": "mm"}           # an incomplete section from an earlier file
    data["custom_section"] = {"x": "1"}
    L.save_api_keys(data)
    data = L.ensure_api_keys_file()
    assert data["openai"]["api_key"] == "sk-mine" and data["minimax"]["api_key"] == "mm"
    assert data["minimax"]["base_url"].startswith("https://api.minimax.io")
    assert data["custom_section"] == {"x": "1"}


@pytest.fixture
def services(monkeypatch):
    monkeypatch.setattr(L, "service_ready", lambda name: name in ("forge", "comfy"))


def test_every_provider_is_checked_without_errors(keys, services):
    """Each provider of each kind, swapped into a baseline selection (the kinds never depend on each other)."""
    base = ["openai", "pexels", "edge", "none", "none", "none", False, 0]
    options = [["openai", "ollama", "compat:groq", "anthropic", "gemini"], list(L.CLIP_PROVIDERS),
               list(L.VOICE_PROVIDERS), list(L.IMAGE_PROVIDERS), list(L.IMAGE_PROVIDERS), list(L.MUSIC_SOURCES),
               [False, True], [0, 2]]
    for position, values in enumerate(options):
        for value in values:
            selection = list(base)
            selection[position] = value
            errors, warnings = L.check_selection(*selection)
            assert isinstance(errors, list) and isinstance(warnings, list)


def test_paid_clips_need_confirmation_and_their_key(keys, services):
    errors, _ = L.check_selection("openai", "wavespeed", "edge", "none", "none", "none", False)
    assert any("Confirmo el cobro" in e for e in errors)
    errors, warnings = L.check_selection("openai", "wavespeed", "edge", "none", "none", "none", True)
    assert any("cobra" in w for w in warnings) and any("WaveSpeed" in e for e in errors)


def test_own_clips_need_files(keys, services):
    errors, _ = L.check_selection("openai", "upload", "edge", "none", "none", "none", False, 0)
    assert any("Sube al menos" in e for e in errors)
    errors, _ = L.check_selection("openai", "footage", "edge", "none", "none", "none")
    assert any("footage" in e for e in errors)
    open(f"{L.FOOTAGE_DIR}/clip.mp4", "w").close()
    try:
        errors, _ = L.check_selection("openai", "footage", "edge", "none", "none", "none")
        assert not any("footage" in e for e in errors)
    finally:
        os.remove(f"{L.FOOTAGE_DIR}/clip.mp4")


def test_music_sources(keys, services, monkeypatch):
    monkeypatch.setattr(L, "music_files", lambda: [])
    _, warnings = L.check_selection("openai", "pexels", "edge", "none", "none", "library")
    assert any("No hay canciones" in w for w in warnings)
    errors, _ = L.check_selection("openai", "pexels", "edge", "none", "none", "elevenlabs")
    assert any("ElevenLabs" in e for e in errors)


def test_local_tools_that_are_not_running_are_named(keys, services):
    errors, _ = L.check_selection("ollama", "wan22", "f5", "flux", "flux", "none")
    assert any("Ollama" in e for e in errors) and any("F5-TTS" in e for e in errors)
    assert not any("Forge" in e for e in errors)
    _, warnings = L.check_selection("openai", "wan22", "edge", "none", "none", "none")
    assert any("texto a video" in w for w in warnings)
