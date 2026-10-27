"""Cell 6: Audio Studio. Try any voice before using it in a reel; F5-TTS voice cloning runs in its own
Python 3.11 venv (its torch pins would otherwise fight Colab's)."""
from ..lib import *

F5_LOG = log_path("f5_native")

# Pre-download and VERIFY the models F5-TTS loads at startup. A truncated download (HF rate limit,
# interrupted session) makes torch.load fail with "pop from empty list"; this catches that and retries.
F5_WARMUP = r"""
import os, sys, torch
from huggingface_hub import hf_hub_download
def fetch(repo, filename, min_bytes, verify_torch=False):
    # min_bytes per file: config.yaml is legitimately a few hundred bytes, the checkpoints are hundreds of MB.
    for attempt in (1, 2):
        path = hf_hub_download(repo, filename)
        real = os.path.realpath(path)
        size = os.path.getsize(real)
        ok = size >= min_bytes
        if not ok:
            print(f"  {filename} too small ({size} bytes, expected >= {min_bytes}); deleting and retrying")
        elif verify_torch:
            try:
                torch.load(real, map_location="cpu", weights_only=True)
            except Exception as exc:
                print(f"  corrupted {filename} ({exc}); deleting and retrying"); ok = False
        if ok:
            print(f"  ok {repo}/{filename} ({size / 1e6:.1f} MB)"); return
        os.remove(real)
    sys.exit(f"could not get a valid {repo}/{filename}")
fetch("charactr/vocos-mel-24khz", "config.yaml", min_bytes=50)
fetch("charactr/vocos-mel-24khz", "pytorch_model.bin", min_bytes=10_000_000, verify_torch=True)
fetch("SWivid/F5-TTS", "F5TTS_v1_Base/model_1250000.safetensors", min_bytes=500_000_000)
fetch("SWivid/F5-TTS", "F5TTS_v1_Base/vocab.txt", min_bytes=1_000)
"""


def install_f5():
    """Returns the problems found (empty list = F5-TTS ready)."""
    f5_venv, f5_python = ensure_venv("f5tts", "3.11")
    uv_install(f5_python, TORCH_SPEC, "Installing PyTorch for F5-TTS...")
    uv_install(f5_python, "f5-tts", "Installing F5-TTS (voice cloning)...")
    check_torch(f5_python, "F5-TTS")
    warm = subprocess.run([f5_python, "-c", F5_WARMUP], capture_output=True, text=True, env=venv_env(f5_venv))
    print(warm.stdout.strip())
    return [] if warm.returncode == 0 else ["F5-TTS model download failed: " + (warm.stderr or warm.stdout)[-800:]]


def speak(text, voice_provider, voice, ref_audio, ref_text):
    missing = [s for s in VOICE_PROVIDERS[voice_provider].get("creds", []) if credential_missing(s)]
    if missing:
        return None, (f"Falta la clave de {', '.join(CREDENTIALS[s]['label'] for s in missing)}: "
                      "agrégala en la pestaña 🔑 Claves API del Centro de Control.")
    return synthesize_voice(text, voice_provider, voice, ref_audio, ref_text,
                            output_path=f"{WORK_DIR}/tts_test_{int(time.time())}.mp3")


def on_type(voice_type):
    import gradio as gr
    choices = voice_provider_choices(voice_type)
    return gr.update(choices=choices, value=choices[0][1])


def on_provider(voice_provider):
    import gradio as gr
    choices = voice_choices(voice_provider)
    is_f5 = voice_provider == "f5"
    return (gr.update(choices=choices, value=choices[0][1] if choices else ""),
            gr.update(visible=is_f5), gr.update(visible=is_f5))


def build_ui():
    import gradio as gr
    with gr.Blocks(title="Audio Studio") as ui:
        gr.Markdown("### 🎙️ Estudio de Voz\nPrueba cualquier voz antes de usarla en un reel. "
                    "Las claves se configuran en la pestaña 🔑 Claves API del Centro de Control (Celda 7).")
        text_in = gr.Textbox(label="Texto a narrar", lines=3,
                             value="¿Sabías que los pastores alemanes pueden aprender una orden nueva en solo cinco repeticiones?")
        type_rd = gr.Radio([(v, k) for k, v in VOICE_TYPES.items()], value="local", label="Tipo")
        with gr.Row():
            provider_dd = gr.Dropdown(voice_provider_choices("local"), value="edge", label="Proveedor")
            voice_dd = gr.Dropdown(voice_choices("edge"), value=EDGE_VOICES[0][1], label="Voz (editable)",
                                   allow_custom_value=True)
        ref_audio_in = gr.Audio(label="Muestra de 5-10 s para clonar", type="filepath", visible=False)
        ref_text_in = gr.Textbox(label="Transcripción de la muestra (opcional)", visible=False)
        speak_btn = gr.Button("🎙️ Generar audio de prueba", variant="primary")
        status_out = gr.Textbox(label="Estado")
        audio_out = gr.Audio(label="Resultado")
        type_rd.change(on_type, type_rd, provider_dd)
        provider_dd.change(on_provider, provider_dd, [voice_dd, ref_audio_in, ref_text_in])
        speak_btn.click(speak, [text_in, provider_dd, voice_dd, ref_audio_in, ref_text_in], [audio_out, status_out])
    return ui


def start(f5=True):
    """f5=False skips the voice-cloning install (no GPU, or you only use Edge-TTS and external voices)."""
    start_cell("Cell 6 · Audio Studio (voices)")
    problems = install_f5() if f5 else []
    launch_gradio(build_ui(), PORTS["tts_studio"])
    url_tts = start_cloudflare_tunnel(PORTS["tts_studio"], "tts_tunnel", "Audio Studio",
                                      "Test every voice: Edge-TTS, F5-TTS cloning and the external voice APIs")
    url_f5 = ""
    if f5 and not problems:            # the native F5-TTS app (multi-speech, voice chat) from the same venv
        kill_port(PORTS["f5_native"])
        proc = start_background([f"{F5_VENV_DIR}/bin/f5-tts_infer-gradio", "--port", str(PORTS["f5_native"]),
                                 "--host", "0.0.0.0"], F5_LOG, env=venv_env(F5_VENV_DIR))
        if wait_for_http(f"http://127.0.0.1:{PORTS['f5_native']}/", proc, F5_LOG, timeout=420, label="F5-TTS UI"):
            url_f5 = start_cloudflare_tunnel(PORTS["f5_native"], "f5_native_tunnel", "F5-TTS native UI",
                                             "Full local voice-cloning interface")
        else:
            problems.append(f"F5-TTS native UI did not start: see {F5_LOG} (every other voice still works)")
    if not is_public_url(url_tts):
        problems.append(f"Audio Studio has no public link yet: {url_tts}")
    finish_cell(not problems, [f"AUDIO STUDIO: {url_tts}"] + ([f"F5-TTS NATIVE UI: {url_f5}"] if url_f5 else []), problems)
