"""Media engine (ffmpeg): word timing for subtitles, styled subtitles, and the montage, built scene by scene.

Every scene is rendered on its own: its video is fitted to the scene's length and its audio (the narration, the
clip's own sound, a sound effect) is mixed to exactly that length. The scenes are then joined in order and the
music is laid over the whole reel, ducking under the voice. A picture can never drift from the sound it belongs
to, because both are cut to the same scene length before anything is joined.

    scene length = its narration (at the chosen voice speed) + a short breath, or the storyboard duration for a
                   scene without narration
"""
import glob, math, random, difflib, unicodedata
from .lib import *

FORMATS = {"9:16": (1080, 1920), "16:9": (1920, 1080), "1:1": (1080, 1080)}
FPS = 30
BREATH = 0.3                 # seconds of air after each narrated scene
MAX_STRETCH = 1.35           # an AI clip shorter than its scene is slowed down this much at most, then held


def is_image(path):
    return str(path).lower().endswith(MEDIA_IMAGE_EXTS)


def media_duration(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=noprint_wrappers=1:nokey=1", path], capture_output=True, text=True)
    try:
        return float(r.stdout.strip() or 0)
    except ValueError:
        return 0.0


def has_audio(path):
    if not path or is_image(path) or not os.path.exists(path):
        return False
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
                        "-of", "csv=p=0", path], capture_output=True, text=True)
    return bool(r.stdout.strip())


def ffmpeg(args):
    r = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"] + args, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("ffmpeg failed:\n" + r.stderr[-1500:])


# ---------------------------------------------------------------------------
# Word timing: Whisper word timestamps, corrected with the script's own spelling
# ---------------------------------------------------------------------------
def _plain(word):
    word = unicodedata.normalize("NFKD", word).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]", "", word)


def align_to_script(words, script):
    """Whisper hears the words; the script knows how they are written (accents, names, punctuation).
    Matching runs of words take the script's spelling; anything uncertain keeps Whisper's."""
    tokens = script.split()
    a, b = [_plain(w["word"]) for w in words], [_plain(t) for t in tokens]
    out = [dict(w) for w in words]
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal" or (tag == "replace" and i2 - i1 == j2 - j1):
            for k in range(i2 - i1):
                out[i1 + k]["word"] = tokens[j1 + k]
    return out


_WHISPER = {}


def load_audio_16k(path):
    """Mono 16 kHz float32 samples decoded by ffmpeg. faster-whisper's own decoder depends on PyAV, whose
    API changes between versions (Colab's PyAV rejects its metadata_errors argument); ffmpeg never does."""
    import numpy as np
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-f", "f32le", "-ac", "1", "-ar", "16000", "-"],
                       capture_output=True)
    if r.returncode != 0 or not r.stdout:
        raise RuntimeError("ffmpeg could not read the voice for the subtitles: " + r.stderr.decode(errors="replace")[-300:])
    return np.frombuffer(r.stdout, np.float32)


def transcribe_words(audio_path, language="en", script=""):
    """Word-level timings on CPU, so Whisper never competes with Forge/ComfyUI for VRAM."""
    if "small" not in _WHISPER:
        from faster_whisper import WhisperModel
        # Local disk, not Drive: the Hugging Face download it makes relies on symlinks, which Drive lacks, and a
        # copy saved there came back unreadable ("Unsupported model binary version"). It is 0.5 GB, quick to fetch.
        _WHISPER["small"] = WhisperModel("small", device="cpu", compute_type="int8",
                                         download_root=f"{LOCAL_MODELS_DIR}/whisper")
    segments, _ = _WHISPER["small"].transcribe(load_audio_16k(audio_path), language=language or None,
                                               vad_filter=True, word_timestamps=True,
                                               initial_prompt=script[:400] or None)
    words = [{"word": w.word.strip(), "start": round(w.start, 3), "end": round(w.end, 3)}
             for seg in segments for w in (seg.words or []) if w.word.strip()]
    return align_to_script(words, script) if script else words


def group_words(words, max_words=3, max_chars=18):
    """Caption lines: a few words each, broken at punctuation and at pauses."""
    groups, current = [], []
    for w in words:
        joined = " ".join(x["word"] for x in current + [w])
        if current and (len(current) >= max_words or len(joined) > max_chars or w["start"] - current[-1]["end"] > 0.6):
            groups.append(current)
            current = []
        current.append(w)
        if re.search(r"[.!?…:;]$", w["word"]) or (w["word"].endswith(",") and len(current) >= 2):
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return groups


def _srt_time(seconds):
    ms = int(round(max(seconds, 0) * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def write_srt(words, path, max_words=7):
    """Plain captions file to upload to YouTube or any editor."""
    with open(path, "w", encoding="utf-8") as f:
        for i, g in enumerate(group_words(words, max_words, 42), 1):
            f.write(f"{i}\n{_srt_time(g[0]['start'])} --> {_srt_time(g[-1]['end'])}\n"
                    f"{' '.join(w['word'] for w in g)}\n\n")
    return path


# ---------------------------------------------------------------------------
# Montage options
# ---------------------------------------------------------------------------
TRANSITIONS = {"none": "Direct cut", "fade": "Fade", "dissolve": "Dissolve", "slideleft": "Slide",
               "wipeleft": "Wipe", "smoothleft": "Smooth wipe", "fadeblack": "Fade to black", "circleopen": "Circle",
               "random": "Random"}
# Only transitions present in every ffmpeg >= 4.3 (Colab ships 4.4).
RANDOM_TRANSITIONS = ["fade", "dissolve", "slideleft", "wipeleft", "smoothleft", "fadeblack", "circleopen"]
SUBTITLE_STYLES = {"karaoke": "Karaoke (spoken word highlighted)", "word_pop": "One word at a time (pop)",
                   "phrase": "Phrases", "off": "No subtitles"}
SUBTITLE_POSITIONS = {"bottom": "Bottom", "center": "Center", "top": "Top"}
FITS = {"cover": "Crop to fill", "contain_blur": "Whole clip over a blurred background"}

DEFAULT_MONTAGE = dict(
    aspect="9:16", fit="cover", transition="fade", transition_duration=0.35, clip_speed=1.0,
    voice_speed=1.0, voice_volume=1.0, clip_audio=True, sound_volume=0.35, music_volume=0.18, ducking=True,
    subtitle_style="karaoke", subtitle_position="bottom", font="Anton", font_size=90, words_per_line=3,
    text_color="#FFFFFF", highlight_color="#FFD400", outline_color="#000000", box=False, uppercase=True,
    hook_overlay=True, hook_seconds=2.5, variants=1,
)


def montage_options(raw=None):
    """Defaults + whatever was chosen, with types and ranges enforced."""
    opt = dict(DEFAULT_MONTAGE)
    opt.update({k: v for k, v in (raw or {}).items() if k in DEFAULT_MONTAGE and v is not None})
    for key in ("transition_duration", "clip_speed", "voice_speed", "voice_volume", "sound_volume", "music_volume",
                "hook_seconds"):
        opt[key] = float(opt[key])
    for key in ("font_size", "words_per_line", "variants"):
        opt[key] = int(opt[key])
    for key in ("clip_audio", "ducking", "box", "uppercase", "hook_overlay"):
        opt[key] = bool(opt[key])
    opt["voice_speed"] = min(max(opt["voice_speed"], 0.5), 2.0)
    opt["clip_speed"] = min(max(opt["clip_speed"], 0.25), 4.0)
    opt["variants"] = min(max(opt["variants"], 1), 3)
    for key, allowed in (("aspect", FORMATS), ("fit", FITS), ("transition", TRANSITIONS),
                         ("subtitle_style", SUBTITLE_STYLES), ("subtitle_position", SUBTITLE_POSITIONS)):
        opt[key] = str(opt[key])
        if opt[key] not in allowed:
            opt[key] = DEFAULT_MONTAGE[key]
    return opt


def font_families():
    """Fonts the subtitles can use: every family in Drive/fonts plus DejaVu Sans (always installed)."""
    families = set()
    if os.path.isdir(FONTS_DIR):
        if shutil.which("fc-scan"):
            r = subprocess.run(["fc-scan", "--format", "%{family[0]}\n", FONTS_DIR], capture_output=True, text=True)
            families.update(l.strip() for l in r.stdout.splitlines() if l.strip())
        else:
            families.update(os.path.splitext(f)[0].split("-")[0] for f in os.listdir(FONTS_DIR)
                            if f.lower().endswith((".ttf", ".otf")))
    families.add("DejaVu Sans")
    return sorted(families)


# ---------------------------------------------------------------------------
# Styled subtitles (ASS, rendered by libass)
# ---------------------------------------------------------------------------
def ass_color(value, alpha=0):
    """'#RRGGBB' (or Gradio's 'rgba(r, g, b, a)') -> ASS '&HAABBGGRR'."""
    value = str(value or "#FFFFFF").strip()
    m = re.match(r"rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)", value)
    if m:
        r, g, b = (int(float(x)) for x in m.groups())
    else:
        h = value.lstrip("#")
        h = "".join(c * 2 for c in h) if len(h) == 3 else (h + "000000")[:6]
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"&H{alpha:02X}{b:02X}{g:02X}{r:02X}"


def _ass_time(t):
    cs = int(round(max(t, 0) * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02}:{s:02}.{cs:02}"


def _ass_text(text):
    return str(text).replace("\\", "/").replace("{", "(").replace("}", ")").replace("\n", " ")


def build_ass(words, width, height, opt, hook_text=""):
    """Karaoke highlight with a pop on the spoken word, one word at a time, or plain phrases; plus the
    on-screen hook for the first seconds."""
    scale = min(width, height) / 1080
    size = max(20, int(opt["font_size"] * scale))
    position = opt["subtitle_position"]
    align = {"bottom": 2, "center": 5, "top": 8}[position]
    margin_v = {"bottom": int(height * 0.2), "center": 0, "top": int(height * 0.14)}[position]
    side = int(width * 0.07)
    bold = -1 if opt["font"] == "DejaVu Sans" else 0
    text_c, high_c = ass_color(opt["text_color"]), ass_color(opt["highlight_color"])
    if opt["box"]:
        border, outline, shadow, outline_c = 3, max(6, int(size * 0.16)), 0, ass_color(opt["outline_color"], 0x40)
    else:
        border, outline, shadow, outline_c = 1, max(3, int(size * 0.08)), max(1, int(size * 0.03)), ass_color(opt["outline_color"])
    hook_align, hook_margin = (5, 0) if position == "top" else (8, int(height * 0.12))
    hook_size = int(size * 0.85)
    upper = (lambda s: s.upper()) if opt["uppercase"] else (lambda s: s)

    lines = ["[Script Info]", "ScriptType: v4.00+", f"PlayResX: {width}", f"PlayResY: {height}", "WrapStyle: 0",
             "ScaledBorderAndShadow: yes", "", "[V4+ Styles]",
             "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, "
             "Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
             "MarginL, MarginR, MarginV, Encoding",
             f"Style: Sub,{opt['font']},{size},{text_c},{high_c},{outline_c},&H80000000,{bold},0,0,0,100,100,0,0,"
             f"{border},{outline},{shadow},{align},{side},{side},{margin_v},1",
             f"Style: Hook,{opt['font']},{hook_size},&H00101010,{text_c},{high_c},&H80000000,{bold},0,0,0,100,100,0,0,"
             f"3,{max(8, int(hook_size * 0.22))},0,{hook_align},{side},{side},{hook_margin},1",
             "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]

    def event(start, end, style, text, layer=0):
        if end - start >= 0.02:
            lines.append(f"Dialogue: {layer},{_ass_time(start)},{_ass_time(end)},{style},,0,0,0,,{text}")

    style = opt["subtitle_style"]
    if style != "off" and words:
        max_chars = max(8, int(width * 0.86 / (size * 0.6)))
        groups = group_words(words, 1 if style == "word_pop" else opt["words_per_line"], max_chars)
        for gi, group in enumerate(groups):
            next_start = groups[gi + 1][0]["start"] if gi + 1 < len(groups) else group[-1]["end"] + 0.6
            group_end = min(group[-1]["end"] + 0.25, next_start)
            texts = [_ass_text(upper(w["word"])) for w in group]
            if style == "phrase":
                event(group[0]["start"], group_end, "Sub", "{\\fad(80,60)}" + " ".join(texts))
            elif style == "word_pop":
                event(group[0]["start"], group_end, "Sub",
                      "{\\fscx60\\fscy60\\t(0,90,\\fscx112\\fscy112)\\t(90,170,\\fscx100\\fscy100)}" + texts[0])
            else:                                                   # karaoke: the spoken word lights up and pops
                for i, w in enumerate(group):
                    start = group[0]["start"] if i == 0 else w["start"]
                    end = group[i + 1]["start"] if i + 1 < len(group) else group_end
                    parts = [("{\\c" + high_c + "\\fscx122\\fscy122\\t(0,120,\\fscx108\\fscy108)}" + t + "{\\r}")
                             if j == i else t for j, t in enumerate(texts)]
                    event(start, end, "Sub", " ".join(parts))
    if hook_text and opt["hook_overlay"]:
        event(0, opt["hook_seconds"], "Hook",
              "{\\fad(120,200)\\fscx80\\fscy80\\t(0,160,\\fscx100\\fscy100)}" + _ass_text(upper(hook_text)), layer=1)
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# One scene: its picture fitted to the scene length, its sound mixed to exactly that length
# ---------------------------------------------------------------------------
KEN_BURNS = [   # zoompan expressions; {n} = frames of the scene
    "z='1+0.15*on/{n}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'",
    "z='1.15-0.15*on/{n}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'",
    "z='1.15':x='(iw-iw/zoom)*on/{n}':y='ih/2-(ih/zoom/2)'",
    "z='1.15':x='(iw-iw/zoom)*(1-on/{n})':y='ih/2-(ih/zoom/2)'",
]


def fit_filter(width, height, fit):
    if fit == "contain_blur":
        return (f"split=2[bg][fg];[bg]scale={width}:{height}:force_original_aspect_ratio=increase,"
                f"crop={width}:{height},boxblur=24:2,eq=brightness=-0.06[bgb];"
                f"[fg]scale={width}:{height}:force_original_aspect_ratio=decrease[fgs];"
                f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2")
    return f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}"


def scene_lengths(scenes, voices, voice_speed=1.0):
    """Seconds of every scene: its narration at the chosen speed plus a breath, or the storyboard duration."""
    out = []
    for scene, voice in zip(scenes, voices):
        if voice and os.path.exists(voice):
            out.append(round(media_duration(voice) / voice_speed + BREATH, 3))
        else:
            out.append(float(scene.get("duration") or 3.0))
    return out


def plan_fit(clip_seconds, need, fill="hold"):
    """How a video of `clip_seconds` covers `need` seconds of source time.
    Returns (tempo, hold): tempo < 1 slows the clip down; hold = seconds of frozen last frame.
    fill='loop' (stock, your files): repeat the clip instead of slowing it."""
    if not clip_seconds or clip_seconds >= need or fill == "loop":
        return 1.0, 0.0
    stretch = need / clip_seconds
    if stretch <= MAX_STRETCH:
        return 1 / stretch, 0.0
    return 1 / MAX_STRETCH, need - clip_seconds * MAX_STRETCH


def render_scene_video(src, dst, seconds, width, height, fit="cover", speed=1.0, fill="hold", motion=0):
    """The picture of one scene, `seconds` long, at the final size and 30 fps. Photos get a slow Ken Burns move;
    a video longer than the scene is trimmed, a shorter one is looped (fill='loop') or slowed down a little and
    then held on its last frame (fill='hold', for AI clips). Returns (tempo, hold) for the clip's own sound."""
    frames = max(2, int(round(seconds * FPS)))
    if is_image(src):
        zoom = KEN_BURNS[motion % len(KEN_BURNS)].format(n=frames)
        vf = (f"{fit_filter(width * 2, height * 2, fit)},zoompan={zoom}:d={frames}:s={width}x{height}:fps={FPS},"
              "setsar=1,format=yuv420p")
        ffmpeg(["-i", src, "-vf", vf, "-frames:v", str(frames), "-an"] + video_encoder_args() + [dst])
        return 1.0, 0.0
    tempo, hold = plan_fit(media_duration(src), seconds * speed, fill)
    factor = speed * tempo                         # >1 faster, <1 slower than the source
    vf = f"setpts=PTS/{factor:.4f},{fit_filter(width, height, fit)},fps={FPS},setsar=1"
    if hold:
        vf += f",tpad=stop_mode=clone:stop_duration={hold / speed + 1:.3f}"
    vf += ",format=yuv420p"
    loop = ["-stream_loop", "-1"] if fill == "loop" else []
    ffmpeg(loop + ["-i", src, "-vf", vf, "-frames:v", str(frames), "-an"] + video_encoder_args() + [dst])
    return factor, hold


def _tempo_filters(factor):
    """atempo chain for any factor (each atempo accepts 0.5-2.0)."""
    filters, rest = [], factor
    while rest > 2.0:
        filters.append("atempo=2.0")
        rest /= 2.0
    while rest < 0.5:
        filters.append("atempo=0.5")
        rest /= 0.5
    if abs(rest - 1) > 1e-3:
        filters.append(f"atempo={rest:.4f}")
    return filters


AUDIO_FORMAT = "aresample=48000,aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"


def render_scene_audio(dst_mix, dst_voice, seconds, opt, voice=None, clip=None, clip_tempo=1.0, clip_loop=False,
                       sound=None):
    """The sound of one scene, exactly `seconds` long (48 kHz stereo WAV):
    dst_mix = narration + the clip's own sound + the scene's sound effect; dst_voice = the narration alone
    (the music ducks under it). Under a narration the clip sound and the effect play at opt['sound_volume'];
    in a scene without narration they play at full volume."""
    inputs, chains, labels = [], [], []
    ambient = opt["sound_volume"] if voice else 1.0
    fade = f"afade=t=out:st={max(seconds - 0.3, 0):.3f}:d=0.3"
    if voice:
        inputs += ["-i", voice]
        speed = ",".join(_tempo_filters(opt["voice_speed"]) + [f"volume={opt['voice_volume']:.3f}"])
        chains.append(f"[{len(labels)}:a]{speed},{AUDIO_FORMAT},asplit=2[v][vk]")
        labels.append("v")
    if clip:
        inputs += (["-stream_loop", "-1"] if clip_loop else []) + ["-i", clip]
        tempo = ",".join(_tempo_filters(clip_tempo) + [f"volume={ambient:.3f}"])
        chains.append(f"[{len(labels)}:a]{tempo},{AUDIO_FORMAT},atrim=0:{seconds:.3f},{fade}[c]")
        labels.append("c")
    if sound:
        inputs += ["-i", sound]
        chains.append(f"[{len(labels)}:a]volume={ambient:.3f},{AUDIO_FORMAT},atrim=0:{seconds:.3f},{fade}[s]")
        labels.append("s")
    tail = f"apad,atrim=0:{seconds:.3f},asetpts=N/SR/TB"
    if not labels:
        ffmpeg(["-f", "lavfi", "-t", f"{seconds:.3f}", "-i", "anullsrc=r=48000:cl=stereo",
                "-c:a", "pcm_s16le", dst_mix])
        shutil.copy(dst_mix, dst_voice)
        return dst_mix, dst_voice
    n = len(labels)
    mix = "".join(f"[{l}]" for l in labels)
    chains.append(f"{mix}amix=inputs={n}:duration=longest:dropout_transition=0,volume={n},{tail}[mix]" if n > 1
                  else f"[{labels[0]}]{tail}[mix]")
    outputs = ["-map", "[mix]", "-c:a", "pcm_s16le", dst_mix]
    if voice:
        chains.append(f"[vk]{tail}[key]")
        outputs += ["-map", "[key]", "-c:a", "pcm_s16le", dst_voice]
    ffmpeg(inputs + ["-filter_complex", ";".join(chains)] + outputs)
    if not voice:
        ffmpeg(["-f", "lavfi", "-t", f"{seconds:.3f}", "-i", "anullsrc=r=48000:cl=stereo",
                "-c:a", "pcm_s16le", dst_voice])
    return dst_mix, dst_voice


def join_audio(parts, dst):
    """Concatenate WAV parts sample-accurately."""
    args = []
    for p in parts:
        args += ["-i", p]
    chain = "".join(f"[{i}:a]" for i in range(len(parts))) + f"concat=n={len(parts)}:v=0:a=1[a]"
    ffmpeg(args + ["-filter_complex", chain, "-map", "[a]", "-c:a", "pcm_s16le", dst])
    return dst


def _filter_path(path):
    return path.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def render_final(scene_videos, lengths, transition, transition_duration, mix, voice_key, music, ass_path, output,
                 opt, seed=0):
    """Joins the scene videos (each one, except the last, carries `transition_duration` extra seconds so the
    transition starts exactly where the next scene's sound starts), burns the subtitles and lays the music over
    the scene sound, ducking under the voice. One encode."""
    rng = random.Random(seed)
    n, total = len(scene_videos), sum(lengths)
    args = []
    for v in scene_videos:
        args += ["-i", v]
    args += ["-i", mix, "-i", voice_key]
    if music:
        args += ["-stream_loop", "-1", "-i", music]
    chains = []
    if n == 1:
        chains.append("[0:v]null[vc]")
    elif transition == "none" or transition_duration <= 0:
        chains.append("".join(f"[{i}:v]" for i in range(n)) + f"concat=n={n}:v=1:a=0[vc]")
    else:
        previous, start = "0:v", 0.0
        for i in range(1, n):
            start += lengths[i - 1]
            name = rng.choice(RANDOM_TRANSITIONS) if transition == "random" else transition
            label = "vc" if i == n - 1 else f"x{i}"
            chains.append(f"[{previous}][{i}:v]xfade=transition={name}:duration={transition_duration:.3f}:"
                          f"offset={start:.3f}[{label}]")
            previous = label
    video_label = "vc"
    if ass_path:
        chains.append(f"[vc]ass={_filter_path(ass_path)}:fontsdir={_filter_path(FONTS_DIR)}[v]")
        video_label = "v"
    if music:
        chains.append(f"[{n + 2}:a]{AUDIO_FORMAT},volume={opt['music_volume']:.3f},afade=t=in:d=1,"
                      f"afade=t=out:st={max(total - 2, 0):.2f}:d=2[m]")
        if opt["ducking"]:
            chains += [f"[{n + 1}:a]{AUDIO_FORMAT}[key]",
                       "[m][key]sidechaincompress=threshold=0.02:ratio=10:attack=15:release=400[md]"]
            music_label = "md"
        else:
            music_label = "m"
        chains.append(f"[{n}:a]{AUDIO_FORMAT}[sc];[sc][{music_label}]amix=inputs=2:duration=first:"
                      "dropout_transition=0,volume=2[a]")
        audio_map = "[a]"
    else:
        audio_map = f"{n}:a"
    args += ["-filter_complex", ";".join(chains), "-map", f"[{video_label}]", "-map", audio_map,
             "-t", f"{total:.3f}", "-r", str(FPS)] + video_encoder_args() + \
            ["-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", output]
    ffmpeg(args)
    return output


# ---------------------------------------------------------------------------
# assets.json: what the montage needs, paths relative to the reel folder when they live inside it
# ---------------------------------------------------------------------------
def reel_relative(reel_dir, path):
    path = os.path.abspath(path)
    return os.path.relpath(path, reel_dir) if path.startswith(os.path.abspath(reel_dir) + os.sep) else path


def reel_absolute(reel_dir, path):
    return path if not path or os.path.isabs(path) else os.path.join(reel_dir, path)


def load_assets(reel_dir):
    try:
        with open(f"{reel_dir}/assets.json", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_assets(reel_dir, **changes):
    assets = load_assets(reel_dir)
    for key, value in changes.items():
        if key == "music":
            value = reel_relative(reel_dir, value) if value else ""
        assets[key] = value
    with open(f"{reel_dir}/assets.json", "w", encoding="utf-8") as f:
        json.dump(assets, f, indent=2, ensure_ascii=False)
    return assets
