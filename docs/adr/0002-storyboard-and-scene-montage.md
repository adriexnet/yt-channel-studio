# ADR-0002: Storyboard, review after each step, and a scene-by-scene montage

**Status:** Accepted
**Date:** 2026-10-27
**Decided by:** Adriel

## Context

A reel was produced in one run from a single script: the LLM wrote the narration as one block plus a list of
scenes with English prompts, the voice was one audio file, and the montage cut the clips at a regular pace
over that voice. Three problems followed:

- What was seen did not follow what was said: a scene's picture could appear while the voice was still on the
  previous idea, because the cuts were evenly spaced, not tied to the narration.
- Nothing could be corrected halfway. A bad image or a mispronounced sentence meant redoing the whole reel,
  and paying again for paid clips.
- A scene without voice (only ambient sound) had no place in the model.

## Decision

1. **Storyboard.** The LLM writes scenes, each with a title, what is seen, what is said (possibly nothing) and
   what is heard, in the language of the idea, plus the English prompts for the AI tools. It is shown and
   edited as plain text (`storyboard.py`); only scenes whose picture or sound changed get new prompts.
2. **Steps with review.** The production is split into storyboard → images → audio → clips → assembly. With
   review on (the default), it pauses after each step; every item can be regenerated alone, with its prompt or
   narration edited. The state lives in the reel folder (`run_state.json`, `review.json`), so a pause survives
   a disconnection or a new Colab session.
3. **One narration per scene, montage per scene.** Each scene gets its own voice file; its length sets the
   scene length (a scene without voice uses its storyboard duration). The clip of each scene is requested at
   that length. The montage builds every scene's picture and sound to the same length before joining them.
4. **Everything in English** (interface, logs, code, docs, prompts); what the viewer reads or hears stays in
   the language of the idea, which the storyboard detects.

## Options considered

### A: Keep one voice track and align the cuts to Whisper's word timings

**For:** one TTS call per reel.
**Against:** depends on matching scene text to the transcription; a single regenerated sentence means
re-voicing the whole reel; silent scenes still have no natural place.

### B: One voice per scene, scene-by-scene montage (chosen)

**For:** sync by construction; any scene's voice, image or clip can be redone alone; silent scenes and per-scene
sound effects fit naturally; paid clips are asked for at the exact length they need.
**Against:** one TTS call per scene; intonation can vary slightly between scenes; the montage renders one
file per scene before the final encode.

## Consequences

- **Easier:** correcting a single item, trusting the sync, adding sound per scene, explaining the folder.
- **Harder:** more files per reel; reels made with 2.x (single `voice.mp3`, no storyboard) cannot be
  re-assembled by 3.0.
- **To revisit:** a review step for the music; letting a regenerated image also redo its clip in one action.
