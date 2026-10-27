# Arquitectura

## Piezas

```
notebook/YT-Channel-Studio.ipynb     8 celdas cortas: solo importan y llaman funciones
        │
        ▼
studio/
├── lib.py             rutas, claves (api_keys.toml), Drive, uv/venvs, registro de proveedores,
│                      validación previa (check_selection), voces, pruebas de conexión
├── pipeline.py        guion y kit (LLM), imágenes, clips, música, montaje (ffmpeg), publicación,
│                      produce_reel() y reassemble_reel()
├── control_center.py  interfaz Gradio: build_ui() y start()
├── jobs.py            trabajos en segundo plano (no se recarga con studio.reload())
├── setup.py           celdas 1 y 2
├── dashboard.py       celda 8
└── tools/             celdas 3-6: ollama, forge, comfyui, audio
```

`lib` no depende de nadie; `pipeline` depende de `lib`; `control_center` de ambos. `studio.reload()`
los recarga en ese orden después de un `git pull`.

## Procesos

El paquete corre en el Python de Colab. Forge (Python 3.10), ComfyUI y F5-TTS (Python 3.11) corren como
procesos aparte, cada uno con su venv en `/content/venvs`. El pipeline les habla por HTTP (Forge, ComfyUI)
o por línea de comandos (F5-TTS); nunca importa sus librerías, así que no hay choques de versiones de torch.
Cada interfaz se publica con un túnel de Cloudflare.

## Flujo de un reel

```
idea ─► guion (LLM) ─► kit de publicación (mismo LLM)
     ─► portada e imágenes (FLUX o API) ─► voz ─► tiempos de cada palabra (Whisper + ortografía del guion)
     ─► clips (stock, tuyos, IA local, IA de pago, fotos animadas) ─► música (tuya o IA)
     ─► montaje: cortes → transiciones → subtítulos ASS + gancho → voz + música con ducking → variantes
     ─► publicación (Upload-Post)
```

`produce_reel()` es un generador: cada paso emite un snapshot (etapa, porcentaje, registro, medios, videos).
`jobs.start()` lo ejecuta en un hilo propio y guarda el último snapshot; la interfaz solo lo **observa**
(`jobs.follow()`). Por eso cerrar la pestaña no detiene nada, varias pestañas pueden mirar a la vez y al
reabrir la página se vuelve a conectar sola. **⏹ Detener** marca el trabajo y este para al terminar el paso en
curso. Los pasos largos corren a su vez en un hilo (`_wait_for`) para que los snapshots sigan saliendo.

## Reanudar

Cada paso deja su archivo en la carpeta del reel y `run_state.json` guarda cómo terminó la última ejecución
(`running`, `done`, `failed`, `stopped`) y en qué paso. `produce_reel(settings, resume_dir=carpeta)` salta
todo paso cuyo archivo ya existe: guion, kit, portada, imágenes, voz, tiempos de palabras, cada clip (incluidos
los de pago) y la música; el montaje se rehace siempre. Una producción nueva usa siempre una carpeta nueva.

## La carpeta de cada reel

Todo lo que el montaje necesita queda en `reels/<idea>/`, con `assets.json` como índice (rutas relativas a la
carpeta cuando viven dentro de ella):

| Archivo | Contenido |
|---|---|
| `plan.json`, `script.txt` | guion, escenas, gancho, prompts de imagen, movimiento y música |
| `settings.json` | todo lo que elegiste (reutilizable o guardable como preset) |
| `run_state.json` | cómo terminó la última ejecución y en qué paso |
| `voice.mp3`, `words.json` | la voz y el tiempo de cada palabra |
| `clips/`, `images/`, `cover.png` | los medios generados o copiados |
| `assets.json` | voz, clips, canción, idioma, ganchos y el último montaje usado |
| `<idea>_FINAL.mp4`, `_FINAL_v2.mp4`… | el video y sus variantes |
| `subtitles.srt`, `publish_kit.json/.md` | subtítulos para subir y el kit de publicación |

Por eso **Volver a montar** no genera nada: lee `assets.json` y vuelve a ejecutar solo el montaje.

## Reglas que se mantienen

- Ningún proveedor depende de otro: cada uno solo necesita su clave o su herramienta corriendo.
  `check_selection()` lo verifica todo antes de empezar y muestra en rojo lo que falta.
- Las tareas de pago se crean una sola vez: si la creación falla, nunca se reintenta sola.
- El kit y la música son extras: si fallan, el reel sigue.
- Las rutas salen de `STUDIO_CONTENT_DIR` y `STUDIO_BASE_DIR`, así las pruebas corren en una carpeta temporal.

Ver también [ADR-0001](adr/0001-paquete-y-lanzador.md).
