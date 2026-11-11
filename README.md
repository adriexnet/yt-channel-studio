# 🎬 YT-Channel-Studio

[![Tests](https://github.com/adriexnet/yt-channel-studio/actions/workflows/tests.yml/badge.svg)](https://github.com/adriexnet/yt-channel-studio/actions/workflows/tests.yml)
[![Open in Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/adriexnet/yt-channel-studio/blob/main/notebook/YT-Channel-Studio.ipynb)

Make, assemble and publish short videos (Reels, Shorts, TikTok) from Google Colab. Write an idea; the studio
writes a storyboard and makes the images, the voice, the clips, the music, the subtitles and the publish kit,
then uploads the reel to your networks. You can review and adjust every step before it goes on. Each kind of
content picks its own provider: local (free, on your Colab GPU) or external (through an API).

## Quick start

1. Open the notebook with the **Open in Colab** button above.
2. **Runtime → Change runtime type → GPU** (T4 or L4). It also works without a GPU, with fewer options.
3. Run **Cell 1** and approve the Google Drive permission. Then run **Cell 2**.
4. Run **Cell 7** and open the **Control Center** link printed at the end.
5. In the **🔑 API keys** tab, save at least one language model (OpenAI, Gemini, Groq…) and the free
   [Pexels](https://www.pexels.com/api/) key. Press **Test the connection** to check them.
6. Back in **🎬 Produce**, type your idea and press **➤**.

## How it works

Type the idea in the language the video should speak. Under it, each button shows what is chosen and opens its
picker: **🧠 text** (LLM and model, with the list of models your provider offers), **🖼️ image**, **🎙️ voice**,
**🎞️ video** (where the clips come from), **📐 format**, **⏱️ length** and **🎭 style** (automatic, animated,
talking objects, top list, story, POV, tutorial…). Everything else is in **⚙️ Settings**, which only shows what
applies to your choice (frames and steps only for local AI video, for example).

The reel is made step by step, and with **👀 Review each step** on (the default) it waits for you after each one:

1. **Storyboard.** Scene by scene: title, what is seen, what is said and what is heard, in the language of
   your idea. Edit the text directly, or regenerate it with notes («more humor»). A scene whose narration is
   `—` has no voice: it lasts its `Duration` and carries its sound. On confirm, the English prompts for the AI
   tools are written for the scenes you changed.
2. **Images.** The cover and the image of each scene (`scene3_image`), each one with its prompt and its own
   **Regenerate** button.
3. **Audio.** One narration per scene (`scene2_audio`), plus a sound effect per scene if you chose ElevenLabs
   sound effects. Each scene lasts as long as its narration.
4. **Clips.** One clip per scene (`scene4_clip`), requested at the exact length of its scene.
5. **Assembly.** Each scene is built with its own sound before the scenes are joined, so a picture never
   drifts from the sound it belongs to; the music goes over the whole reel and ducks under the voice.

A bar of steps on top shows where the reel is, and only the panel of the step at hand is open. The results of the finished steps stay beside it while the next one works, and **🔍 View** opens any image, voice or clip in a detail window. In a review, tick several items and press **🔄 Regenerate selected** to redo them in one go. Under the progress bar you see the current step and how long it has been on it; **🔧 Show the technical log** opens the full detail. The finished reel, its kit and publishing come last, with no platform ticked and private visibility by default.
Everything (video, storyboard, voices, clips, kit, settings) is saved in your Drive under `YT-Channel-Studio/reels/`.

## The cells

The notebook only calls functions of this repository; the code lives here and Cell 1 updates it.

| Cell | What it does | Required? |
|---|---|---|
| 1 · Base | Downloads or updates the code, connects Drive, creates folders and `api_keys.toml` | Yes, always first |
| 2 · Environment | ffmpeg, cloudflared, packages and subtitle fonts | Yes |
| 3 · Ollama | Local LLMs and their chat | Only for a local LLM |
| 4 · Forge | FLUX images (cover, first frames, AI photos) | Only for local images |
| 5 · ComfyUI | Local AI video (Wan 2.2, LTX-Video) | Only for local AI video |
| 6 · Voice studio | Try voices and clone yours with F5-TTS | Optional |
| 7 · Control Center | Produce, review, re-assemble, publish and save keys | Yes |
| 8 · Dashboard | Every active public link | Optional |

The heavy tools (Forge, ComfyUI, F5-TTS) each get their own virtual environment with `uv`, so their PyTorch
versions never clash. Each tool cell downloads its own models to Colab's local disk when it runs, so nothing is
downloaded for a tool you do not use. They used to live in Drive, but loading them from there kept the GPU
waiting for minutes; a fresh download is faster.

## Providers

| Content | Local | External |
|---|---|---|
| Storyboard and kit | Ollama | OpenAI, Anthropic, Google Gemini and any OpenAI-compatible API (Groq, OpenRouter, DeepSeek…) |
| Clips | Wan 2.2, LTX-Video, animated photos, your own clips | Pexels, Pixabay, Coverr (free); Google Gemini Omni (Veo's successor in the Gemini API, with sound), WaveSpeed, Seedance, OFox, MuAPI, Metaso, Infistar (paid, with confirmation) |
| Voice | Edge-TTS, F5-TTS (cloning) | ElevenLabs, OpenAI, Azure, Gemini TTS, SiliconFlow, MiMo, MiniMax, Fish Audio, VoxCPM |
| Images (cover, first frame, AI photos) | FLUX (Forge) | Nano Banana (Gemini) and any OpenAI-compatible image API |
| Scene sound | The clip's own sound (AI video with audio, your files) | ElevenLabs sound effects |
| Music | Your songs in `Drive/music` | ElevenLabs Music |
| Publishing | — | Upload-Post (TikTok, Instagram, YouTube, Facebook) |

## Montage

9:16, 16:9 and 1:1 · transitions between scenes that start exactly where the next scene's sound starts ·
karaoke, word-by-word or phrase subtitles with your font and colors · on-screen hook · voice speed and volume ·
the clips' own sound and the sound effects under the voice · music that ducks while the voice speaks · up to 3
variants. In **📁 My reels → Re-assemble** you change all of this without generating anything again.

## If something stops or fails

- **The tab closed or the link dropped:** the job keeps running in the background. Open the link again (or
  press **📡 Show the job in progress**) and you see it where it is, review panel included.
- **A step failed** (a key, a service down, a network error): press **🔁 Resume where it failed**. Every step
  leaves its files in Drive, so everything that went well is reused (storyboard, kit, images, voices, clips
  already paid, music) and it goes on from the failed step. You can switch that step's provider first.
- **📁 My reels** shows every reel as ✅ done, 👀 waiting for your review, ❌ failed or ⏹ stopped, with its
  step. **▶️ Open in «Produce»** shows its review panel or continues it with its own settings.
- **Producing the same idea again** creates another folder (`idea-2`, `idea-3`…): an earlier reel is never
  overwritten.

## Reusing settings

Every reel saves everything you chose in `settings.json`. In **📁 My reels**, **♻️ Use these settings** loads
them into the form and **💾 Save preset** stores them under a name. In **⚙️ Settings → 📂 Presets** you load
any preset or save the current settings. A preset never comes with the charge confirmation ticked.

## Your Drive folder

```
YT-Channel-Studio/
├── config/api_keys.toml   your keys (only in your Drive, never in this repository)
├── config/presets/        your settings presets
├── reels/<idea>/          each reel: plan.json (storyboard + prompts), images/, audio/, clips/, the video and
│                          its variants, subtitles, kit, settings, review and run state
├── music/                 your songs (music/ai: the ones made with AI)
├── footage/               your clips and photos for the «My clips» source
├── fonts/                 subtitle fonts; add yours here
├── voices/                samples to clone your voice
└── code/                  a copy of the code, in case GitHub does not answer
```

A `models/` folder left by an earlier version is no longer used: you can delete it to free Drive space.

## Updating or pinning a version

Run **Cell 1** again: it brings the latest version without restarting Colab. To stay on a stable version,
change `VERSION = "main"` to a tag from [Releases](https://github.com/adriexnet/yt-channel-studio/releases),
for example `VERSION = "v3.0.0"`. If an update changes a tool's dependencies, run that tool's cell again.

## Development

```bash
pip install -r requirements.txt -r requirements-dev.txt
python -m playwright install --with-deps chromium   # only for the browser tests
python -m pytest                                     # every test (~9 min, real ffmpeg)
python -m pytest tests/unit                          # only the fast ones (< 1 s)
python scripts/build_notebook.py                     # rebuilds notebook/YT-Channel-Studio.ipynb
```

- `studio/` the package: `lib.py` (paths, keys, providers, voices), `storyboard.py` (the storyboard as text and
  its prompts), `media.py` (word timing, subtitles, scene-by-scene montage), `pipeline.py` (providers and the
  step-by-step production), `control_center.py` (interface), `jobs.py` (background jobs), `setup.py` (cells 1
  and 2), `tools/` (cells 3 to 6).
- `tests/` unit, integration (fake HTTP, real ffmpeg) and interface tests (Gradio and Chromium).
- `docs/architecture.md` how the pieces fit · `docs/adr/` design decisions.

## License

[MIT](LICENSE)
