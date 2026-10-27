# 🎬 YT-Channel-Studio

[![Pruebas](https://github.com/adriexnet/yt-channel-studio/actions/workflows/tests.yml/badge.svg)](https://github.com/adriexnet/yt-channel-studio/actions/workflows/tests.yml)
[![Abrir en Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/adriexnet/yt-channel-studio/blob/main/notebook/YT-Channel-Studio.ipynb)

Produce, monta y publica videos cortos (Reels, Shorts, TikTok) desde Google Colab: escribes una idea y el
estudio genera el guion, la voz, los clips, la música, los subtítulos y el kit de publicación, y los sube a tus redes.
Cada parte elige su proveedor por separado, local (gratis, en tu GPU de Colab) o externo (por API).

## Inicio rápido

1. Abre el notebook con el botón **Abrir en Colab** de arriba.
2. **Entorno de ejecución → Cambiar tipo de entorno → GPU** (T4 o L4). Sin GPU también funciona, con menos opciones.
3. Ejecuta la **Celda 1** y acepta el permiso de Google Drive. Luego la **Celda 2**.
4. Ejecuta la **Celda 7** y abre el link del **Centro de Control** que aparece al final.
5. En la pestaña **🔑 Claves API** guarda al menos un modelo de lenguaje (OpenAI, Gemini, Groq…) y la clave
   gratuita de [Pexels](https://www.pexels.com/api/). Pulsa **Probar conexión** para verificarlas.
6. Vuelve a **🎬 Producir reel**, escribe una idea y pulsa **🚀 PRODUCIR REEL**.

El reel, su guion, la voz, los clips y el kit de publicación quedan en tu Drive, en `YT-Channel-Studio/reels/`.

## Las celdas

El notebook solo llama funciones de este repositorio; el código vive aquí y se actualiza con la Celda 1.

| Celda | Qué hace | ¿Obligatoria? |
|---|---|---|
| 1 · Base | Descarga o actualiza el código, conecta Drive, crea carpetas y `api_keys.toml` | Sí, siempre primero |
| 2 · Entorno | ffmpeg, cloudflared, paquetes y fuentes de subtítulos | Sí |
| 3 · Ollama | LLMs locales y su chat | Solo si usas un LLM local |
| 4 · Forge | Imágenes FLUX (portadas, imagen base, fotos IA) | Solo para imágenes locales |
| 5 · ComfyUI | Video IA local (Wan 2.2, LTX-Video) | Solo para video IA local |
| 6 · Estudio de voz | Probar voces y clonar la tuya con F5-TTS | Opcional |
| 7 · Centro de Control | Producir, montar de nuevo, publicar y guardar claves | Sí |
| 8 · Dashboard | Todos los links públicos activos | Opcional |

Las herramientas pesadas (Forge, ComfyUI, F5-TTS) se instalan cada una en su propio entorno virtual con `uv`,
así sus versiones de PyTorch nunca chocan. Los modelos se guardan en Drive y solo se descargan una vez.

## Proveedores

| Contenido | Local | Externo |
|---|---|---|
| Guion y kit | Ollama | OpenAI, Anthropic, Gemini y cualquier API compatible con OpenAI (Groq, OpenRouter, DeepSeek…) |
| Clips | Wan 2.2, LTX-Video, fotos FLUX animadas, tus propios clips | Pexels, Pixabay, Coverr (gratis); WaveSpeed, Seedance, OFox, MuAPI, Metaso, Infistar (de pago, con confirmación) |
| Voz | Edge-TTS, F5-TTS (clonación) | ElevenLabs, OpenAI, Azure, Gemini, SiliconFlow, MiMo, MiniMax, Fish Audio, VoxCPM |
| Imágenes | FLUX (Forge) | Cualquier API de imágenes compatible con OpenAI |
| Música | Tus canciones en `Drive/music` | ElevenLabs Music |
| Publicación | — | Upload-Post (TikTok, Instagram, YouTube, Facebook) |

## Montaje

Formatos 9:16, 16:9 y 1:1 · ritmo de cortes y transiciones · subtítulos karaoke, palabra por palabra o por
frases con tu fuente y colores · gancho en pantalla · velocidad y volumen de la voz · la música baja sola
cuando habla la voz · hasta 3 variantes. En **📁 Mis reels → Volver a montar** cambias todo eso sin generar
nada de nuevo.

## Si algo se corta o falla

- **Se cerró la pestaña o se cayó el link:** la producción sigue en segundo plano. Abre el link de nuevo (o
  pulsa **📡 Ver el progreso en curso**) y vuelves a verla donde va.
- **Falló un paso** (una clave, un servicio caído, un error de red): pulsa **🔁 Reanudar**. Cada paso deja su
  archivo en Drive, así que se reutiliza todo lo que ya salió bien (guion, kit, imágenes, voz, subtítulos,
  clips ya pagados, música) y se sigue desde el paso que falló. Puedes cambiar el proveedor de ese paso antes
  de reanudar. En **📁 Mis reels** cada reel muestra ✅ listo, ❌ falló o ⏹ detenido, con el paso en el que quedó.
- **Producir de nuevo la misma idea** crea otra carpeta (`idea-2`, `idea-3`…): nunca se pisa un reel anterior.

## Reutilizar ajustes

Cada reel guarda en `settings.json` todo lo que elegiste. En **📁 Mis reels**:
**♻️ Usar estos ajustes** los carga en el formulario y **💾 Guardar preset** los guarda con un nombre. En
**🎬 Producir reel → 📂 Presets** cargas cualquier preset o guardas los ajustes actuales. Un preset nunca
trae marcada la confirmación de cobro.

## Tu carpeta en Drive

```
YT-Channel-Studio/
├── config/api_keys.toml   tus claves (solo en tu Drive, nunca en este repositorio)
├── config/presets/        tus presets de ajustes
├── reels/<idea>/          cada reel: video, variantes, guion, voz, clips, subtítulos, kit, ajustes y estado
├── music/                 tus canciones (music/ai: las generadas con IA)
├── footage/               tus clips y fotos para la fuente «Mis clips»
├── fonts/                 fuentes de subtítulos; agrega las tuyas aquí
├── voices/                muestras para clonar tu voz
├── models/                pesos de Ollama, FLUX, Wan, LTX y F5-TTS
└── code/                  copia del código, por si GitHub no responde
```

## Actualizar o fijar una versión

Vuelve a ejecutar la **Celda 1**: trae la última versión sin reiniciar Colab. Para quedarte en una versión
estable, cambia `VERSION = "main"` por una etiqueta de [Releases](https://github.com/adriexnet/yt-channel-studio/releases),
por ejemplo `VERSION = "v2.1.0"`. Si una actualización cambia las dependencias de una herramienta, vuelve a
ejecutar su celda.

## Desarrollo

```bash
pip install -r requirements.txt -r requirements-dev.txt
python -m playwright install --with-deps chromium   # solo para la prueba con navegador
python -m pytest                                     # todas las pruebas (~4 min, usan ffmpeg real)
python -m pytest tests/unit                          # solo las rápidas (< 1 s)
python scripts/build_notebook.py                     # regenera notebook/YT-Channel-Studio.ipynb
```

- `studio/` el paquete: `lib.py` (rutas, claves, proveedores, voces), `pipeline.py` (guion, clips, montaje,
  publicación), `control_center.py` (interfaz), `setup.py` (celdas 1 y 2), `tools/` (celdas 3 a 6).
- `tests/` unitarias, de integración (HTTP simulado y ffmpeg real) y de interfaz (Gradio y Chromium).
- `docs/arquitectura.md` cómo encajan las piezas · `docs/adr/` decisiones de diseño.

## Licencia

[MIT](LICENSE)
