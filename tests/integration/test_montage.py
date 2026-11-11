"""The montage engine with real ffmpeg, scene by scene: every scene's picture lasts exactly as long as its sound,
transitions start where the next scene's sound starts, and formats, subtitles, ducking and variants work."""
import glob, json, os, shutil, subprocess
import pytest
import studio.lib as L
import studio.pipeline as P
from helpers import ffmpeg, probe

EXPECTED = {"9:16": (1080, 1920), "16:9": (1920, 1080), "1:1": (1080, 1080)}
COLORS = {"red": (255, 0, 0), "lime": (0, 255, 0), "blue": (0, 0, 255)}
NARRATION = ["¿Sabías que los lobos cazan en equipo?", "", "Síguenos para más."]


@pytest.fixture(scope="module")
def reel(media, fonts):
    """Scene 1: 3 s of voice over a 1 s red AI clip (slowed, then held). Scene 2: no voice, 2 s, a green clip
    with its own sound. Scene 3: 2 s of voice over a blue photo."""
    folder = f"{L.REELS_DIR}/montage-test"
    shutil.rmtree(folder, ignore_errors=True)
    for sub in ("clips", "audio", "images"):
        os.makedirs(f"{folder}/{sub}")
    ffmpeg("-f", "lavfi", "-i", "color=c=red:s=480x832:r=24", "-t", "1", "-pix_fmt", "yuv420p", f"{folder}/clips/scene_01.mp4")
    ffmpeg("-f", "lavfi", "-i", "color=c=lime:s=720x1280:r=30", "-f", "lavfi", "-i", "sine=frequency=900:duration=5",
           "-t", "5", "-pix_fmt", "yuv420p", "-shortest", f"{folder}/clips/scene_02.mp4")
    ffmpeg("-f", "lavfi", "-i", "color=c=blue:s=768x1344", "-frames:v", "1", f"{folder}/images/scene_03.png")
    ffmpeg("-f", "lavfi", "-i", "sine=frequency=300:duration=3", f"{folder}/audio/scene_01_voice.wav")
    ffmpeg("-f", "lavfi", "-i", "sine=frequency=400:duration=2", f"{folder}/audio/scene_03_voice.wav")
    for i, freq in enumerate((600, 800), 1):
        ffmpeg("-f", "lavfi", "-i", f"sine=frequency={freq}:duration=4", f"{L.MUSIC_DIR}/song{i}.mp3")
    for i in (1, 3):
        words = [{"word": w, "start": 0.1 + 0.3 * k, "end": 0.35 + 0.3 * k} for k, w in enumerate(NARRATION[i - 1].split())]
        json.dump(words, open(f"{folder}/audio/scene_0{i}_words.json", "w"))
    scenes = [{"id": i, "title": f"S{i}", "visual": "v", "narration": n, "sound": "", "duration": 2.0,
               "image_prompt": "", "motion_prompt": "", "search_query": "", "sound_prompt": ""}
              for i, n in enumerate(NARRATION, 1)]
    json.dump({"language": "es", "language_name": "Spanish", "title": "Lobos", "on_screen_text": "Lobos en equipo",
               "scenes": scenes}, open(f"{folder}/plan.json", "w"))
    P.save_assets(folder, music=f"{L.MUSIC_DIR}/song1.mp3", music_source="library", language="es",
                  clips=["clips/scene_01.mp4", "clips/scene_02.mp4", "images/scene_03.png"],
                  clip_fill=["hold", "hold", "hold"], clip_audio=[True, True, False], hooks=["Hook dos", "Hook tres"])
    return folder


def assemble(reel, montage):
    run = P.ReelRun()
    snapshots = list(P.assemble_reel(run, reel, montage, pct_start=5))
    return run, snapshots


def color_at(video, seconds):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{seconds:.3f}", "-i", video, "-frames:v", "1",
                          "-vf", "scale=1:1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
    return tuple(raw[:3])


def closest(rgb):
    return min(COLORS, key=lambda name: sum((a - b) ** 2 for a, b in zip(rgb, COLORS[name])))


def loudness(video, start, length):
    r = subprocess.run(["ffmpeg", "-v", "info", "-ss", f"{start:.3f}", "-t", f"{length:.3f}", "-i", video, "-af",
                        "volumedetect", "-f", "null", "-"], capture_output=True, text=True)
    return float(r.stderr.split("mean_volume:")[1].split("dB")[0])


def test_scene_lengths_follow_the_voice():
    voices = [None, None]
    assert P.scene_lengths([{"duration": 2.5}, {"duration": 4}], voices) == [2.5, 4.0]
    assert P.plan_fit(1.0, 3.3) == (1 / P.MAX_STRETCH, pytest.approx(3.3 - P.MAX_STRETCH))   # slowed, then held
    assert P.plan_fit(3.0, 3.3) == (pytest.approx(3.0 / 3.3), 0.0)                       # just slowed a little
    assert P.plan_fit(5.0, 3.3) == (1.0, 0.0) and P.plan_fit(1.0, 3.3, "loop") == (1.0, 0.0)


@pytest.mark.parametrize("transition", ["fade", "none"])
def test_every_picture_stays_with_its_sound(reel, transition):
    run, _ = assemble(reel, dict(transition=transition, transition_duration=0.4, subtitle_style="off",
                                 hook_overlay=False, music_volume=0.0))
    assert not run.failed, run.lines[-3:]
    video = run.videos[0]
    lengths = [3 + P.BREATH, 2.0, 2 + P.BREATH]
    assert abs(probe(video)[2] - sum(lengths)) < 0.1
    starts = [0, lengths[0], lengths[0] + lengths[1]]
    tail = 0.4 if transition == "fade" else 0
    for start, length, color in zip(starts, lengths, ("red", "lime", "blue")):
        assert closest(color_at(video, start + tail + 0.1)) == color          # right after its transition
        assert closest(color_at(video, start + length - 0.1)) == color        # until its very end
    assert loudness(video, starts[1] + 0.2, 1.5) > -40                         # the green clip's own sound plays
    assert loudness(video, starts[0] + 3.05, 0.2) < -60                        # the breath after a narration is silent


CASES = [
    dict(aspect="9:16", fit="cover", transition="fade", subtitle_style="karaoke", ducking=True),
    dict(aspect="16:9", fit="contain_blur", transition="random", subtitle_style="word_pop", box=True, font="Bebas Neue"),
    dict(aspect="1:1", transition="none", subtitle_style="phrase", subtitle_position="top", voice_speed=1.25,
         clip_speed=1.5, ducking=False, clip_audio=False),
]


@pytest.mark.parametrize("montage", CASES, ids=lambda m: f"{m['aspect']}-{m['subtitle_style']}")
def test_montage_formats(reel, montage):
    run, _ = assemble(reel, montage)
    assert not run.failed, run.lines[-3:]
    width, height, duration, has_audio, rate = probe(run.videos[0])
    speed = montage.get("voice_speed", 1.0)
    assert (width, height) == EXPECTED[montage["aspect"]]
    assert abs(duration - (3 / speed + 2 + 2 / speed + 2 * P.BREATH)) < 0.1
    assert has_audio and rate == "30/1"
    srt = open(f"{reel}/subtitles.srt", encoding="utf-8").read()
    assert "Síguenos para más." in srt and "lobos" in srt


def test_variants_and_cleanup(reel):
    run, _ = assemble(reel, dict(subtitle_style="off", hook_overlay=False, variants=3))
    assert len(run.videos) == 3 and all(os.path.exists(v) for v in run.videos)
    assert any("song2.mp3" in line for line in run.lines)            # each variant gets another song
    run, _ = assemble(reel, dict(variants=1))
    assert sorted(os.path.basename(p) for p in glob.glob(f"{reel}/*_FINAL*.mp4")) == ["montage-test_FINAL.mp4"]
    assert P.load_assets(reel)["montage"]["variants"] == 1


def test_reassemble(reel):
    last = list(P.reassemble_reel(reel, dict(aspect="16:9")))[-1]
    assert last["done"] and not last["failed"] and probe(last["video"])[:2] == (1920, 1080)
    assert "## Scene 2: S2 (0:03–0:05)" in last["script"]
    last = list(P.reassemble_reel(f"{reel}-missing", {}))[-1]
    assert last["failed"] and "Pick a reel" in last["log"]
