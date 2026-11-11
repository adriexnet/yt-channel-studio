"""The storyboard as editable text: render, parse back, merge edits, and which scenes need new prompts."""
import pytest
from studio.storyboard import (apply_storyboard, auto_scenes, build_storyboard_prompt, merge_scene_prompts,
                               normalize_plan, parse_storyboard, plan_script, render_storyboard)

RAW = {"language": "es", "title": "El poder de la manada", "on_screen_text": "Juntos son invencibles",
       "cover_prompt": "wolf pack at dawn", "search_terms": ["wolves"],
       "scenes": [{"title": "El gancho", "visual": "Lobo alfa aullando en una cumbre nevada.",
                   "narration": "Un lobo solo es fuerte, pero en manada es invencible.", "sound": "viento",
                   "image_prompt": "alpha wolf howling", "motion_prompt": "slow push-in", "search_query": "wolf howl",
                   "sound_prompt": "icy wind"},
                  {"title": "Silencio", "visual": "La manada avanza entre los pinos.", "narration": "—",
                   "sound": "pasos en la nieve", "duration": "4 s", "image_prompt": "pack in pines",
                   "motion_prompt": "tracking shot", "search_query": "wolves forest", "sound_prompt": "snow steps"},
                  {"title": "Cierre", "visual": "La manada corre hacia la cámara.",
                   "narration": "¡Síguenos para más curiosidades!", "image_prompt": "pack running"}]}


@pytest.fixture
def plan():
    return normalize_plan(RAW, "por qué los lobos cazan en manada")


def test_normalize_fills_defaults(plan):
    assert [s["id"] for s in plan["scenes"]] == [1, 2, 3]
    assert plan["language_name"] == "Spanish" and plan["scenes"][1]["narration"] == ""
    assert plan["scenes"][1]["duration"] == 4.0 and plan["scenes"][2]["search_query"] == "wolves"
    assert plan_script(plan) == "Un lobo solo es fuerte, pero en manada es invencible. ¡Síguenos para más curiosidades!"
    with pytest.raises(ValueError, match="no scenes"):
        normalize_plan({"scenes": []})


def test_render_and_parse_round_trip(plan):
    text = render_storyboard(plan)
    assert text.startswith("Title: El poder de la manada\nOn-screen text: Juntos son invencibles\n")
    assert "## Scene 2: Silencio (0:04–0:08)" in text and "Narration: —\nSound: pasos en la nieve\nDuration: 4s" in text
    parsed = parse_storyboard(text)
    assert parsed["title"] == "El poder de la manada" and len(parsed["scenes"]) == 3
    for scene, original in zip(parsed["scenes"], plan["scenes"]):
        for key in ("title", "visual", "narration", "sound"):
            assert scene[key] == original[key]
    assert render_storyboard(plan, [5.0, 4.0, 3.25]).count("(0:09–0:12)") == 1     # real lengths once audio exists


def test_parse_is_forgiving():
    text = """Title: Lobos
## Escena 1 — El gancho (0-10s)
Visual: Lobo aullando
en la cumbre nevada.
Narration: "Un lobo solo es fuerte."
## Scene 2: Sin voz
visual: La manada
NARRATION:
Duration: 5
"""
    parsed = parse_storyboard(text)
    first, second = parsed["scenes"]
    assert first["title"] == "El gancho" and first["visual"] == "Lobo aullando en la cumbre nevada."
    assert first["narration"] == "Un lobo solo es fuerte."
    assert second["narration"] == "" and second["duration"] == 5.0


def test_parse_errors_are_readable():
    with pytest.raises(ValueError, match="No scenes found"):
        parse_storyboard("Title: nada")
    with pytest.raises(ValueError, match="Scene 2 has no «Visual:» line"):
        parse_storyboard("## Scene 1: A\nVisual: x\n## Scene 2: B\nNarration: hola")


def test_only_scenes_whose_picture_or_sound_changed_need_new_prompts(plan):
    text = render_storyboard(plan)
    text = text.replace("Un lobo solo es fuerte", "Un lobo solo es valiente")         # narration only
    text = text.replace("La manada avanza entre los pinos.", "La manada descansa.")    # the picture
    text += "\n## Scene 4: Extra\nVisual: Un cachorro juega.\nNarration: Bonus.\n"
    new, changed = apply_storyboard(plan, parse_storyboard(text))
    assert changed == [2, 4]
    assert new["scenes"][0]["image_prompt"] == "alpha wolf howling"                    # kept
    assert new["scenes"][0]["narration"].startswith("Un lobo solo es valiente")
    assert new["scenes"][1]["image_prompt"] == "pack in pines"                         # until rewritten
    merged = merge_scene_prompts(new, {"scenes": [{"id": 2, "image_prompt": "pack resting", "motion_prompt": "static",
                                                    "search_query": "wolves resting", "sound_prompt": "calm wind"},
                                                   {"id": 4, "image_prompt": "wolf pup playing"}]})
    assert merged["scenes"][1]["image_prompt"] == "pack resting" and merged["scenes"][3]["image_prompt"] == "wolf pup playing"
    assert merged["scenes"][3]["motion_prompt"] == "slow cinematic camera push-in"      # default for what is missing
    assert plan["scenes"][1]["image_prompt"] == "pack in pines"                         # the original is untouched


def test_prompt_asks_for_the_idea_language_and_english_prompts():
    prompt = build_storyboard_prompt("auto", 42, "auto", "menciona @canal", "9:16", "talking_objects")
    assert "Exactly 6 scenes" in prompt and "about 100 words" in prompt and "first-person monologue" in prompt
    assert "detect the language the idea is written in" in prompt and "Every prompt for the AI tools is in English" in prompt
    assert "fits the idea and the style best" in prompt and "menciona @canal" in prompt
    assert "photorealistic" in build_storyboard_prompt(4, style="narrated") and "Exactly 4 scenes" in build_storyboard_prompt(4)
    assert [auto_scenes(d) for d in (15, 35, 90)] == [3, 5, 10]


def test_format_words_have_no_ratio():
    """A "9:16"-style ratio in a FLUX prompt makes Forge return noise, so the prompts never ask for one."""
    from studio.storyboard import FORMAT_WORDS, build_scene_prompts_prompt
    for aspect, words in FORMAT_WORDS.items():
        assert ":" not in words
        assert aspect not in build_scene_prompts_prompt(aspect)


def test_prompts_not_in_english_are_found():
    from studio.storyboard import looks_english, scenes_to_translate
    assert looks_english("A wolf howling at the moon in an open field, cinematic light")
    assert not looks_english("Un lobo aullando fuertemente en un campo abierto a la luz de la luna")
    assert not looks_english("Lobos en un bosque comunicándose")
    assert looks_english("slow push-in")
    plan = {"scenes": [{"id": 1, "image_prompt": "A wolf on a hill with the moon behind", "motion_prompt": "slow pan"},
                       {"id": 2, "image_prompt": "Un lobo marcando su territorio con una postura fuerte",
                        "motion_prompt": "slow pan"}]}
    assert scenes_to_translate(plan) == [2]
