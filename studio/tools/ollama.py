"""Cell 3: Ollama (local LLMs, weights persist in Drive) + a chat UI."""
from ..lib import *

DEFAULT_MODELS = ["qwen2.5:14b", "deepseek-r1:14b"]
OLLAMA_URL = f"http://127.0.0.1:{PORTS['ollama_api']}"
OLLAMA_LOG = log_path("ollama_server")


def pull_model(model):
    installed = subprocess.run(["ollama", "list"], capture_output=True, text=True).stdout
    if model in installed:
        print(f"[exists in Drive] {model}")
        return
    print(f"Pulling {model} into Drive...")
    bar, last_digest = None, None
    with requests.post(f"{OLLAMA_URL}/api/pull", json={"name": model, "stream": True}, stream=True) as r:
        for line in r.iter_lines():
            if not line:
                continue
            data = json.loads(line)
            if "total" in data and "completed" in data:
                if last_digest != data.get("digest"):
                    if bar:
                        bar.close()
                    last_digest = data.get("digest")
                    bar = tqdm(total=data["total"], unit="B", unit_scale=True,
                               desc=f"{model} ({data.get('status', '')[:15]})")
                bar.n = data["completed"]
                bar.refresh()
    if bar:
        bar.close()
    print(f"[done] {model}")


def chat(message, history, model):
    """Streaming chat with Gradio's messages history."""
    messages = [{"role": m["role"], "content": m["content"]} for m in history if isinstance(m.get("content"), str)]
    messages.append({"role": "user", "content": message})
    partial = ""
    with requests.post(f"{OLLAMA_URL}/api/chat", json={"model": model, "messages": messages, "stream": True},
                       stream=True) as r:
        for line in r.iter_lines():
            if line:
                partial += json.loads(line).get("message", {}).get("content", "")
                yield partial


def build_ui(models):
    import gradio as gr
    with gr.Blocks(title="Ollama Chat") as ui:
        gr.Markdown("### 💬 Chat con tus modelos locales (Ollama)")
        model_dd = gr.Dropdown(models, value=models[0], label="🧠 Modelo", allow_custom_value=True)
        gr.ChatInterface(fn=chat, additional_inputs=[model_dd])
    return ui


def start(models=None):
    models = list(models or DEFAULT_MODELS)
    start_cell("Cell 3 · Ollama (local LLMs + chat UI)")
    problems = []
    os.environ["OLLAMA_MODELS"] = f"{MODELS_DIR}/ollama"   # model weights persist in Drive
    os.environ["OLLAMA_HOST"] = f"0.0.0.0:{PORTS['ollama_api']}"
    if shutil.which("ollama") is None:
        run("curl -fsSL https://ollama.com/install.sh | sh", "Installing Ollama...")
    subprocess.run(["killall", "-9", "ollama"], stderr=subprocess.DEVNULL)
    proc = start_background(["ollama", "serve"], OLLAMA_LOG)
    if not wait_for_http(f"{OLLAMA_URL}/", proc, OLLAMA_LOG, timeout=90, label="Ollama"):
        problems.append(f"Ollama did not start: see {OLLAMA_LOG}")
    else:
        for model in models:
            pull_model(model)

    launch_gradio(build_ui(models), PORTS["ollama_chat"])
    url_api = start_cloudflare_tunnel(PORTS["ollama_api"], "ollama_api_tunnel", "Ollama API",
                                      f"REST API for local LLMs ({', '.join(models)})")
    url_chat = start_cloudflare_tunnel(PORTS["ollama_chat"], "ollama_chat_tunnel", "Ollama Chat UI",
                                       "ChatGPT-style chat with the local models")
    for label, url in (("Ollama API", url_api), ("Ollama chat UI", url_chat)):
        if not is_public_url(url):
            problems.append(f"{label} has no public link yet: {url}")
    finish_cell(not problems, [f"OLLAMA API:     {url_api}", f"OLLAMA CHAT UI: {url_chat}"], problems)
