"""The montage engine with real ffmpeg: formats, fits, transitions, subtitle styles, ducking and variants."""
import glob, json, os, shutil
import pytest
import studio.lib as L
import studio.pipeline as P
from helpers import ffmpeg, probe

SCRIPT = "¿Sabías que los pastores alemanes aprenden una orden nueva en solo cinco repeticiones? Son increíbles, compártelo."
EXPECTED = {"9:16": (1080, 1920), "16:9": (1920, 1080), "1:1": (1080, 1080)}


@pytest.fixture(scope="module")
def reel(media, fonts):
    folder = f"{L.REELS_DIR}/montage-test"
    shutil.rmtree(folder, ignore_errors=True)
    os.makedirs(f"{folder}/clips")
    for name in ("landscape", "short_ai"):
        shutil.copy(media[name], f"{folder}/clips/{name}.mp4")
    shutil.copy(media["image"], f"{folder}/clips/photo.png")
    ffmpeg("-f", "lavfi", "-i", "sine=frequency=300:duration=9", "-af", "volume=0.6", f"{folder}/voice.mp3")
    for i, freq in enumerate((600, 800), 1):
        ffmpeg("-f", "lavfi", "-i", f"sine=frequency={freq}:duration=4", f"{L.MUSIC_DIR}/song{i}.mp3")
    words, t = [], 0.3
    for w in SCRIPT.split():
        words.append({"word": w, "start": round(t, 2), "end": round(t + 0.45, 2)})
        t += 0.55
    json.dump(words, open(f"{folder}/words.json", "w"))
    json.dump({"script": SCRIPT, "hook": "Esto no lo sabías", "on_screen_text": "Perros genios", "scenes": [{}]},
              open(f"{folder}/plan.json", "w"))
    P.save_assets(folder, voice=f"{folder}/voice.mp3", music=f"{L.MUSIC_DIR}/song1.mp3", music_source="library",
                  clips=[f"{folder}/clips/landscape.mp4", f"{folder}/clips/short_ai.mp4", f"{folder}/clips/photo.png"],
                  language="es", hooks=["Hook dos", "Hook tres"])
    return folder


def assemble(reel, montage):
    run = P.ReelRun()
    snapshots = list(P.assemble_reel(run, reel, montage, pct_start=5))
    return run, snapshots


def test_assets_are_relative_to_the_reel(reel):
    assets = P.load_assets(reel)
    assert assets["voice"] == "voice.mp3" and assets["clips"][0] == "clips/landscape.mp4"
    assert assets["music"].endswith("song1.mp3")


CASES = [
    dict(aspect="9:16", fit="cover", transition="fade", subtitle_style="karaoke", ducking=True),
    dict(aspect="16:9", fit="contain_blur", transition="random", subtitle_style="word_pop", box=True, font="Bebas Neue"),
    dict(aspect="1:1", transition="none", subtitle_style="phrase", subtitle_position="top", pace="1.5",
         voice_speed=1.25, clip_speed=1.5, ducking=False),
]


@pytest.mark.parametrize("montage", CASES, ids=lambda m: f"{m['aspect']}-{m['subtitle_style']}")
def test_montage_formats(reel, montage):
    run, _ = assemble(reel, montage)
    assert not run.failed, run.lines[-3:]
    width, height, duration, has_audio, rate = probe(run.videos[0])
    assert (width, height) == EXPECTED[montage["aspect"]]
    assert abs(duration - 9.0 / montage.get("voice_speed", 1.0)) < 0.25
    assert has_audio and rate == "30/1"
    assert os.path.getsize(f"{reel}/subtitles.srt") > 50


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
    last = list(P.reassemble_reel(f"{reel}-missing", {}))[-1]
    assert last["failed"] and "Elige un reel" in last["log"]
