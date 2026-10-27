"""Subtitle timing and styling, cut planning and montage options (pure functions)."""
import pytest
import studio.pipeline as P

SCRIPT = "¿Sabías que los pastores alemanes aprenden una orden nueva en solo cinco repeticiones? Son increíbles, compártelo."


def heard_words():
    """What Whisper would return: right timing, plain spelling."""
    words, t = [], 0.3
    for w in SCRIPT.split():
        words.append({"word": w.lower().strip("¿?,."), "start": round(t, 2), "end": round(t + 0.45, 2)})
        t += 0.55
    return words


def test_alignment_restores_the_script_spelling():
    aligned = P.align_to_script(heard_words(), SCRIPT)
    assert aligned[0]["word"] == "¿Sabías" and aligned[-1]["word"] == "compártelo."
    assert [w["start"] for w in aligned] == [w["start"] for w in heard_words()]


def test_alignment_keeps_whisper_when_unsure():
    words = [{"word": "hola", "start": 0, "end": 0.3}, {"word": "extra", "start": 0.4, "end": 0.6}]
    assert [w["word"] for w in P.align_to_script(words, "Hola")] == ["Hola", "extra"]


def test_caption_lines_break_at_punctuation_and_pauses():
    groups = P.group_words(P.align_to_script(heard_words(), SCRIPT), 3, 18)
    assert all(len(g) <= 3 for g in groups)
    assert any(g[-1]["word"].endswith("?") for g in groups)
    paused = [{"word": "a", "start": 0, "end": 0.2}, {"word": "b", "start": 1.5, "end": 1.7}]
    assert len(P.group_words(paused, 3, 18)) == 2


@pytest.mark.parametrize("value,expected", [("#FFD400", "&H0000D4FF"), ("#fff", "&H00FFFFFF"),
                                            ("rgba(255, 0, 16, 1)", "&H001000FF")])
def test_ass_colors(value, expected):
    assert P.ass_color(value) == expected


def test_ass_karaoke_and_hook():
    words = [{"word": "{hola}\\", "start": 0.1, "end": 0.5}, {"word": "mundo", "start": 0.6, "end": 1.0}]
    ass = P.build_ass(words, 1080, 1920, P.montage_options({}), "Mira esto")
    assert "\\c&H0000D4FF" in ass and "Style: Hook" in ass and "MIRA ESTO" in ass
    assert "(HOLA)/" in ass                                     # braces and backslashes escaped
    assert ass.count("Dialogue:") == 3                          # one per spoken word + the hook


def test_ass_styles_and_off():
    words = [{"word": "uno", "start": 0.1, "end": 0.4}, {"word": "dos", "start": 0.5, "end": 0.9}]
    pop = P.build_ass(words, 1080, 1920, P.montage_options({"subtitle_style": "word_pop", "hook_overlay": False}))
    assert pop.count("Dialogue:") == 2 and "\\t(0,90" in pop
    off = P.build_ass(words, 1080, 1920, P.montage_options({"subtitle_style": "off"}), "Gancho")
    assert off.count("Dialogue:") == 1                          # only the hook
    boxed = P.build_ass(words, 1920, 1080, P.montage_options({"box": True, "subtitle_position": "top"}))
    assert ",3," in [l for l in boxed.splitlines() if l.startswith("Style: Sub")][0]


def test_montage_options_are_clamped():
    opt = P.montage_options({"variants": 9, "voice_speed": 5, "aspect": "4:3", "pace": "7", "font_size": "88"})
    assert opt["variants"] == 3 and opt["voice_speed"] == 2.0 and opt["aspect"] == "9:16"
    assert opt["pace"] == "auto" and opt["font_size"] == 88


def test_cut_planning_covers_the_voice(monkeypatch):
    durations = {"long.mp4": 6.0, "short.mp4": 3.0}
    monkeypatch.setattr(P, "media_duration", lambda path: durations[path])
    segments, transition = P.plan_segments(["long.mp4", "short.mp4", "photo.png"], 9.0, "auto", 0.35, 1.0)
    covered = sum(s[2] for s in segments) / P.FPS - (len(segments) - 1) * transition
    assert 9.0 <= covered < 9.2
    assert segments[0][2] / P.FPS <= 3.4                       # never stretches the 3 s clip into a loop
    segments, _ = P.plan_segments(["long.mp4"], 9.0, "2", 0.35, 1.0)
    assert len(segments) == 5 and segments[1][1] > 0           # later cuts start further into the clip
