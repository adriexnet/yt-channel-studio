# Architecture

## Pieces

```
notebook/YT-Channel-Studio.ipynb     8 short cells: they only import and call functions
        │
        ▼
studio/
├── lib.py             paths, keys (api_keys.toml), Drive, uv/venvs, provider registry, up-front checks
│                      (check_selection), voices, connection tests
├── storyboard.py      the storyboard: its prompt, its text format (render / parse), merging your edits
├── media.py           word timing (Whisper), subtitles (ASS/SRT), the scene-by-scene montage (ffmpeg)
├── pipeline.py        providers (LLM, images, clips, music, sound effects, kit, publishing) and the
│                      step-by-step production: produce_reel, continue_reel, regenerate_item,
│                      regenerate_storyboard, reassemble_reel
├── control_center.py  Gradio interface: build_ui() and start()
├── jobs.py            background jobs (not reloaded by studio.reload())
├── setup.py           cells 1 and 2
├── dashboard.py       cell 8
└── tools/             cells 3-6: ollama, forge, comfyui, audio
```

`lib` depends on nothing; `storyboard` and `media` depend on `lib`; `pipeline` on those three;
`control_center` on all of them. `studio.reload()` reloads them in that order after a `git pull`.

## Interface

The **🎬 Produce** tab is a composer: the idea and a row of buttons (text, image, voice, video, format, length,
style and ⚙️ Settings). Each button opens a window (`modal()` in `control_center.py`: a column with
`position: fixed`) and its label sums up the choice (`chip_labels`). Options that depend on the choice (charge,
own files, first frame, local AI frames and steps) are shown or hidden by `clip_visibility`. Cascading pickers
listen to the user only (`.input`), so loading saved settings never undoes them.

Under the progress bar, the review panel appears when a step waits for you: the storyboard as an editable text
box, or the items of the step (images, voices, sounds, clips) drawn with `@gr.render` from the review payload,
each with its prompt and its **Regenerate** button.

## Processes

The package runs in Colab's Python. Forge (Python 3.10), ComfyUI and F5-TTS (Python 3.11) run as separate
processes, each in its venv under `/content/venvs`. The pipeline talks to them over HTTP (Forge, ComfyUI) or
the command line (F5-TTS); it never imports their libraries, so torch versions never clash. Every interface is
published through a Cloudflare tunnel.

## A reel, step by step

```
idea ─► storyboard (LLM; in the idea's language, with English prompts) ──────────── 👀 review
     ─► publish kit (same LLM, from the confirmed narration)
     ─► cover and scene images (FLUX, Nano Banana or an image API) ─────────────────── 👀 review
     ─► one narration per scene + word timings (Whisper) + optional sound effects ─── 👀 review
     ─► one clip per scene, as long as its scene (stock, yours, local AI, paid AI) ── 👀 review
     ─► music ─► montage: each scene's picture + sound → join with transitions → subtitles + hook
        → music ducking under the voice → variants
     ─► publishing (Upload-Post)
```

Every generator emits snapshots (stage, percentage, log, storyboard, media, videos, review payload).
`jobs.start()` runs it in its own thread and keeps the last snapshot; the interface only **watches** it
(`jobs.follow()`). Closing the tab stops nothing, several tabs can watch at once, and reopening the page
reconnects by itself. **⏹ Stop** flags the job, which stops when the step in progress ends. Long steps run in
a thread of their own (`_wait_for`) so snapshots keep flowing.

## Review and regeneration

With `review_steps` on, `Production.pause(stage)` stops after a step that has something to review and writes
`run_state.json` = `review` + the step. `continue_reel()` («Confirm») applies the edited storyboard (only the
scenes whose title, visual or sound changed get new prompts), records the step in `review.json` and goes on
to the next pause. `regenerate_item()` makes one item again (`cover`, `sceneN_image`, `sceneN_audio`,
`sceneN_sound`, `sceneN_clip`), with its prompt or narration replaced, and leaves the reel in review.

## Sync

The montage never cuts a single voice track into pieces. Each scene lasts its narration (at the chosen voice
speed) plus a short breath, or its storyboard `duration` when it has no voice. Its picture is fitted to that
length (a longer clip is trimmed; a shorter AI clip is slowed down a little and then held; stock and your
files loop) and its sound — narration, the clip's own sound, its sound effect — is mixed to exactly that
length. Each scene video, except the last, carries the transition length on top, so the transition starts
exactly where the next scene's sound starts. The scene sounds are then concatenated sample-accurately.

## Resume

Every step leaves its files in the reel folder and `run_state.json` records how the last run ended
(`running`, `review`, `done`, `failed`, `stopped`) and at which step. Resuming reuses every file that exists:
storyboard, kit, cover, images, voices, word timings, every clip (paid ones included) and the music; the
montage is always redone. A new production always uses a new folder.

## The folder of each reel

Everything the montage needs stays in `reels/<idea>/`, with `assets.json` as the index:

| File | Content |
|---|---|
| `plan.json`, `script.txt` | the storyboard (in the idea's language) with the English prompts; the narration |
| `settings.json` | everything you chose (reusable, or saved as a preset) |
| `run_state.json`, `review.json` | how the last run ended, and the steps you confirmed |
| `images/scene_NN.png`, `cover.png` | the images |
| `audio/scene_NN_voice.*`, `_words.json`, `_sound.mp3` | each scene's narration, word timings and sound effect |
| `clips/scene_NN.mp4`, `clips/own_NN.*` | each scene's clip, or your files |
| `assets.json` | clip of each scene and how to fit it, song, language, hooks and the last montage |
| `<idea>_FINAL.mp4`, `_FINAL_v2.mp4`… | the video and its variants |
| `subtitles.srt`, `publish_kit.json/.md` | subtitles to upload and the publish kit |

That is why **Re-assemble** generates nothing: it reads these files and runs only the montage.

## Rules that hold

- No provider depends on another: each one only needs its own key or its own tool running.
  `check_selection()` checks everything before starting and shows in red what is missing.
- Paid tasks are created once: when creating one fails, it is never retried on its own. Regenerating a paid
  clip is always your explicit choice.
- The kit and the music are extras: if they fail, the reel goes on.
- Paths come from `STUDIO_CONTENT_DIR` and `STUDIO_BASE_DIR`, so the tests run in a temporary folder.

See also [ADR-0001](adr/0001-package-and-launcher.md) and [ADR-0002](adr/0002-storyboard-and-scene-montage.md).
