"""The storyboard: the reel told scene by scene in the language of the idea (what is seen, what is said, what is
heard), plus the English prompts every AI receives. It is shown as editable text; parse_storyboard() reads the
text back and apply_storyboard() merges it into plan.json, saying which scenes need new prompts.

plan.json:
    language, language_name, title, title_slug, on_screen_text, cover_prompt, music_prompt, search_terms,
    scenes: [{id, title, visual, narration, sound, duration,                  <- in the idea's language
              image_prompt, motion_prompt, search_query, sound_prompt}]       <- English, for the AIs
A scene with an empty narration is a scene without voice: it lasts `duration` seconds and carries its sound.
"""
import re
from .lib import *

WORDS_PER_SECOND = 2.4          # spoken words per second, to estimate lengths before the voice exists
SILENT_SECONDS = 3.0            # default length of a scene without narration
PROMPT_FIELDS = ("image_prompt", "motion_prompt", "search_query", "sound_prompt")
STORY_FIELDS = ("title", "visual", "narration", "sound", "duration")
# No "9:16"-style ratios: in a FLUX prompt sent through Forge they turn the image into noise. The image size
# already sets the shape.
FORMAT_WORDS = {"9:16": "vertical portrait composition", "16:9": "horizontal wide composition",
                "1:1": "square composition"}

STYLE_GUIDES = {
    "auto": "Choose the format that fits the idea best (narrated, top list, story, POV, tutorial...).",
    "narrated": "Documentary-style narration over strong, realistic visuals.",
    "animated": "Everything is a stylized 3D animation (Pixar-like): every image_prompt and motion_prompt describes a "
                "3D animated scene, with the same characters in every scene.",
    "cartoon": "2D cartoon illustration: bold outlines, flat colors, the same characters in every scene.",
    "talking_objects": "An everyday object, food or animal related to the idea is the narrator: the narration is its "
                       "first-person monologue; every image_prompt shows that same character with a cute, expressive "
                       "face talking to the camera; motion_prompts include its mouth and face moving.",
    "listicle": "A top list or countdown of facts or tips, clearly numbered in the narration, one item per scene.",
    "story": "A short story with a hook, a twist and a payoff.",
    "pov": "Second-person POV (\"POV: you are...\"); visuals in first-person perspective.",
    "tutorial": "A practical step-by-step tutorial, one step per scene, in the imperative.",
    "motivational": "A motivational speech that builds emotion, with inspiring cinematic visuals.",
    "cinematic": "Cinematic realism: film look, dramatic lighting and shallow depth of field in every image_prompt.",
}
PHOTOREAL_STYLES = ("auto", "narrated", "cinematic", "pov", "tutorial", "motivational", "listicle", "story")


def auto_scenes(duration):
    """About one scene every 7 seconds of narration, between 3 and 10."""
    return max(3, min(10, round(int(duration) / 7)))


def _scene_count(num_scenes, duration):
    return auto_scenes(duration) if num_scenes in (None, "", "auto", 0, "0") else int(num_scenes)


def build_storyboard_prompt(num_scenes="auto", duration=35, tone="auto", instructions="", aspect="9:16",
                            style="auto"):
    """System prompt of the storyboard. The idea (and, when regenerating, the previous storyboard and the
    requested changes) goes in the user message."""
    words = int(duration * WORDS_PER_SECOND)
    shape = FORMAT_WORDS.get(aspect, FORMAT_WORDS["9:16"])
    look = "photorealistic" if style in PHOTOREAL_STYLES else "in the chosen visual style"
    tone_text = "the one that fits the idea and the style best" if tone in ("auto", "", None) else TONES.get(tone, tone)
    scenes = _scene_count(num_scenes, duration)
    extra = f"\nExtra instructions from the creator (follow them): {instructions.strip()}" if (instructions or "").strip() else ""
    return f"""You are an expert director of viral short-form videos (YouTube Shorts, Reels, TikTok).
Write the storyboard of a ~{duration}-second reel, scene by scene.

LANGUAGE: detect the language the idea is written in. Everything the viewer reads or hears ("title",
"on_screen_text", and each scene's "title", "visual", "narration" and "sound") is written in THAT language, in a
neutral variety understood by the widest audience. Every prompt for the AI tools is in English.

Answer ONLY with a valid JSON object with exactly this structure:
{{
  "language": "ISO 639-1 code of the idea's language, e.g. es",
  "language_name": "that language's name in English, e.g. Spanish",
  "title": "short title of the reel",
  "title_slug": "short-ascii-slug-for-a-folder",
  "on_screen_text": "max 6 words shown big on screen during the first seconds",
  "cover_prompt": "English prompt for a {shape} thumbnail about this topic, {look}, high contrast",
  "music_prompt": "English description of instrumental background music: genre, mood, tempo, instruments, no vocals",
  "search_terms": ["five", "english", "stock-footage", "search", "keywords"],
  "scenes": [
    {{
      "title": "2-4 words naming the scene",
      "visual": "one sentence: what the viewer sees",
      "narration": "the exact words spoken in this scene (1-2 sentences), or \\"\\" for a scene without voice",
      "sound": "background sound or effects heard in this scene, or \\"\\"",
      "duration": "seconds, only for a scene without narration (2-6)",
      "image_prompt": "detailed English prompt for image generation, {shape}, {look}",
      "motion_prompt": "detailed English prompt for a video model: camera movement and subject action",
      "search_query": "2-4 English words to find this shot on a stock-footage site",
      "sound_prompt": "English description of the sound for a sound-effects model, or \\"\\""
    }}
  ]
}}
Rules:
- Exactly {scenes} scenes, in the order they are shown. All narrations together are about {words} words.
- Scene 1's narration is the hook (it grabs attention in the first 3 seconds); the last scene ends with a reason
  to comment, follow or share.
- Narration: only the spoken words (no stage directions, emojis or hashtags). Use a scene without narration only
  when silence or pure sound adds impact.
- "visual", "image_prompt" and "motion_prompt" of a scene describe the same shot; the visual matches what the
  narration of that scene says.
Style: {STYLE_GUIDES.get(style, STYLE_GUIDES["auto"])}
Tone: {tone_text}.{extra}"""


def storyboard_request(idea, previous_text="", notes=""):
    """User message of the storyboard call (a regeneration includes the previous storyboard and the notes)."""
    text = f"Reel idea: {idea}"
    if previous_text:
        text += f"\n\nPrevious storyboard (write a new, better one):\n{previous_text}"
    if (notes or "").strip():
        text += f"\n\nChanges the creator asked for: {notes.strip()}"
    return text


def build_scene_prompts_prompt(aspect="9:16", style="auto"):
    """System prompt that writes the English prompts of some scenes from the storyboard as it was edited."""
    shape = FORMAT_WORDS.get(aspect, FORMAT_WORDS["9:16"])
    look = "photorealistic" if style in PHOTOREAL_STYLES else "in the chosen visual style"
    return f"""You turn a reel's storyboard into prompts for AI tools. Answer ONLY with valid JSON:
{{"scenes": [{{"id": 1, "image_prompt": "detailed English prompt for image generation, {shape}, {look}",
              "motion_prompt": "English prompt for a video model: camera movement and subject action",
              "search_query": "2-4 English words to find this shot on a stock-footage site",
              "sound_prompt": "English description of the scene's sound for a sound-effects model, or \\"\\""}}]}}
Write prompts ONLY for the scene ids you are asked for. Each prompt shows exactly the scene's "visual" (and its
"sound"), keeps characters and look consistent with the other scenes, and never includes text or subtitles.
Style: {STYLE_GUIDES.get(style, STYLE_GUIDES["auto"])}"""


def scene_prompts_request(plan, ids):
    story = [{"id": s["id"], **{k: s.get(k, "") for k in ("title", "visual", "narration", "sound")}}
             for s in plan["scenes"]]
    return (f"Reel title: {plan.get('title', '')}\nStoryboard:\n{json.dumps(story, ensure_ascii=False, indent=1)}\n\n"
            f"Write the prompts for scene ids: {', '.join(str(i) for i in ids)}")


# ---------------------------------------------------------------------------
# Plan helpers
# ---------------------------------------------------------------------------
def _seconds(value, default=SILENT_SECONDS):
    match = re.search(r"\d+(?:[.,]\d+)?", str(value or ""))
    return min(max(float(match.group().replace(",", ".")), 1.0), 30.0) if match else default


def normalize_plan(raw, idea=""):
    """Defaults for whatever the model left out, ids 1..n, durations as numbers."""
    plan = dict(raw or {})
    scenes = plan.get("scenes") or []
    if not scenes:
        raise ValueError("The storyboard has no scenes.")
    plan["language"] = str(plan.get("language") or "en").lower().split("-")[0][:3]
    plan["language_name"] = plan.get("language_name") or LANGUAGE_NAMES.get(plan["language"], plan["language"])
    plan.setdefault("title", idea[:60])
    plan.setdefault("title_slug", "")
    plan.setdefault("on_screen_text", "")
    plan.setdefault("cover_prompt", "")
    plan.setdefault("music_prompt", "")
    plan["search_terms"] = plan.get("search_terms") or [idea]
    out = []
    for i, scene in enumerate(scenes, 1):
        scene = dict(scene or {})
        scene["id"] = i
        for key in ("title", "visual", "narration", "sound") + PROMPT_FIELDS:
            scene[key] = str(scene.get(key) or "").strip()
        if scene["narration"] in ("-", "—", "–"):
            scene["narration"] = ""
        scene["duration"] = _seconds(scene.get("duration"))
        scene["image_prompt"] = scene["image_prompt"] or plan["cover_prompt"] or scene["visual"] or idea
        scene["motion_prompt"] = scene["motion_prompt"] or "slow cinematic camera push-in"
        scene["search_query"] = scene["search_query"] or plan["search_terms"][0]
        out.append(scene)
    plan["scenes"] = out
    return plan


def plan_script(plan):
    """Everything the voice says, in order (the publish kit and script.txt use it)."""
    return " ".join(s["narration"] for s in plan["scenes"] if s.get("narration"))


def estimate_seconds(scene):
    words = len(scene.get("narration", "").split())
    return words / WORDS_PER_SECOND + 0.3 if words else float(scene.get("duration") or SILENT_SECONDS)


def _clock(seconds):
    seconds = int(round(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


# ---------------------------------------------------------------------------
# Storyboard as editable text
# ---------------------------------------------------------------------------
def render_storyboard(plan, lengths=None):
    """The storyboard as text. `lengths`: real seconds per scene once the audio exists (estimates otherwise)."""
    lengths = lengths or [estimate_seconds(s) for s in plan["scenes"]]
    lines = [f"Title: {plan.get('title', '')}", f"On-screen text: {plan.get('on_screen_text', '')}", ""]
    t = 0.0
    for scene, seconds in zip(plan["scenes"], lengths):
        lines.append(f"## Scene {scene['id']}: {scene['title']} ({_clock(t)}–{_clock(t + seconds)})")
        lines.append(f"Visual: {scene['visual']}")
        lines.append(f"Narration: {scene['narration'] or '—'}")
        if scene.get("sound"):
            lines.append(f"Sound: {scene['sound']}")
        if not scene["narration"]:
            lines.append(f"Duration: {scene['duration']:g}s")
        lines.append("")
        t += seconds
    return "\n".join(lines).rstrip() + "\n"


_HEADER = re.compile(r"^#{1,3}\s*(?:scene|escena|scène|cena|szene)\s*\d*\s*[:.\-—–]?\s*(.*)$", re.IGNORECASE)
_FIELD = re.compile(r"^(title|on-screen text|visual|narration|sound|duration)\s*:\s*(.*)$", re.IGNORECASE)
_TIMES = re.compile(r"\s*\(\s*[\d:.,]+\s*s?\s*[–\-—]\s*[\d:.,]+\s*s?\s*\)\s*$")


def parse_storyboard(text):
    """Text -> {title, on_screen_text, scenes: [{title, visual, narration, sound, duration}]}.
    Lines without a label continue the field above them. Raises ValueError with a readable reason."""
    head, scenes, current, field = {"title": "", "on_screen_text": ""}, [], None, None
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        header = _HEADER.match(line)
        if header:
            current = {"title": _TIMES.sub("", header.group(1)).strip(), "visual": "", "narration": "",
                       "sound": "", "duration": ""}
            scenes.append(current)
            field = None
            continue
        match = _FIELD.match(line)
        if match:
            name, value = match.group(1).lower(), match.group(2).strip()
            if current is None:
                key = "on_screen_text" if name == "on-screen text" else name
                if key in head:
                    head[key], field = value, (head, key)
                continue
            key = {"on-screen text": None}.get(name, name)
            if key in current:
                current[key], field = value, (current, key)
            continue
        if field:                                         # a continuation line
            target, key = field
            target[key] = (target[key] + " " + line).strip()
    if not scenes:
        raise ValueError("No scenes found: each scene starts with a line like «## Scene 1: Title».")
    for i, scene in enumerate(scenes, 1):
        narration = scene["narration"].strip().strip('"“”«»').strip()
        scene["narration"] = "" if narration in ("", "-", "—", "–") else narration
        scene["duration"] = _seconds(scene["duration"])
        if not scene["visual"]:
            raise ValueError(f"Scene {i} has no «Visual:» line: describe what is seen.")
    return {**head, "scenes": scenes}


def apply_storyboard(plan, parsed):
    """Merge an edited storyboard into the plan. Returns (new plan, ids of the scenes that need new prompts):
    scenes whose title, visual or sound changed, and new scenes. A changed narration needs no new prompt."""
    new = json.loads(json.dumps(plan))
    new["title"] = parsed.get("title") or plan.get("title", "")
    new["on_screen_text"] = parsed.get("on_screen_text", plan.get("on_screen_text", ""))
    old_scenes, scenes, changed = plan["scenes"], [], []
    for i, edited in enumerate(parsed["scenes"], 1):
        old = old_scenes[i - 1] if i <= len(old_scenes) else None
        scene = dict(old or {}, **{k: edited[k] for k in STORY_FIELDS}, id=i)
        if old is None or any(old.get(k, "") != edited[k] for k in ("title", "visual", "sound")):
            changed.append(i)
            for key in PROMPT_FIELDS:
                scene.setdefault(key, "")
        scenes.append(scene)
    new["scenes"] = scenes
    return new, changed


ENGLISH_WORDS = {"the", "of", "with", "and", "at", "is", "an", "its", "from", "by", "on", "under", "against"}


def looks_english(text):
    """False for a prompt a local model wrote in the idea's language (FLUX and Wan follow English far better).
    A cheap check: no accented letters, and at least one everyday English word in a prompt of 5+ words."""
    words = re.findall(r"[^\W\d_]+", (text or "").lower())
    if any(ord(ch) > 127 for word in words for ch in word):
        return False
    return len(words) < 5 or bool(ENGLISH_WORDS & set(words))


def scenes_to_translate(plan):
    """Ids of the scenes whose image or motion prompt is not in English."""
    return [s["id"] for s in plan.get("scenes", [])
            if not (looks_english(s.get("image_prompt")) and looks_english(s.get("motion_prompt")))]


def merge_scene_prompts(plan, answer):
    """Puts the prompts the model wrote ({"scenes": [{id, image_prompt, ...}]}) into the plan."""
    by_id = {int(s.get("id", 0)): s for s in (answer or {}).get("scenes", []) if str(s.get("id", "")).isdigit()}
    for scene in plan["scenes"]:
        written = by_id.get(scene["id"])
        if written:
            for key in PROMPT_FIELDS:
                if written.get(key) is not None:
                    scene[key] = str(written[key]).strip()
    return normalize_plan(plan)
