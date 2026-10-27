"""Shared test setup. Every path of the package points to a temporary folder (STUDIO_* variables are set
BEFORE the package is imported), HTTP is faked per test, and ffmpeg is real."""
import os, sys, shutil, subprocess, tempfile
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="studio-tests-")
os.environ["STUDIO_CONTENT_DIR"] = f"{TMP}/content"
os.environ["STUDIO_BASE_DIR"] = f"{TMP}/drive"
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import requests
import studio.lib as L
from fakehttp import Router
from helpers import ffmpeg

L.ensure_project_dirs()
_REAL = (requests.get, requests.post, requests.request)


@pytest.fixture
def http():
    """A fake `requests`: routes registered per test, every call recorded. Restored afterwards."""
    router = Router()
    router.install()
    yield router
    requests.get, requests.post, requests.request = _REAL


@pytest.fixture
def keys():
    """A fresh api_keys.toml with defaults; returns a setter: keys(section, **fields)."""
    if os.path.exists(L.KEYS_FILE):
        os.remove(L.KEYS_FILE)
    L.ensure_api_keys_file()

    def set_fields(section, **fields):
        data = L.load_api_keys()
        data.setdefault(section, {}).update(fields)
        L.save_api_keys(data)
    return set_fields


@pytest.fixture(scope="session")
def fonts():
    """The subtitle fonts in Drive/fonts (downloaded once from Google Fonts)."""
    from studio import setup
    setup.download_fonts()
    if not os.path.exists(f"{L.FONTS_DIR}/Anton-Regular.ttf"):
        pytest.skip("subtitle fonts could not be downloaded")
    return L.FONTS_DIR


@pytest.fixture(scope="session")
def media():
    """Small real media files made with ffmpeg."""
    folder = f"{TMP}/media"
    os.makedirs(folder, exist_ok=True)
    files = {
        "portrait": ("-f", "lavfi", "-i", "testsrc2=size=720x1280:rate=30", "-t", "4", "-pix_fmt", "yuv420p"),
        "landscape": ("-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=25", "-t", "6", "-pix_fmt", "yuv420p"),
        "short_ai": ("-f", "lavfi", "-i", "mandelbrot=size=480x832:rate=24", "-t", "3", "-pix_fmt", "yuv420p"),
        "voice": ("-f", "lavfi", "-i", "sine=frequency=220:duration=6"),
        "tone_wav": ("-f", "lavfi", "-i", "sine=frequency=300:duration=1"),
        "tone_mp3": ("-f", "lavfi", "-i", "sine=frequency=300:duration=1"),
        "tone_pcm": ("-f", "lavfi", "-i", "sine=frequency=300:duration=1", "-f", "s16le", "-ar", "24000", "-ac", "1"),
        "image": ("-f", "lavfi", "-i", "testsrc=size=768x1344", "-frames:v", "1"),
    }
    ext = {"portrait": "mp4", "landscape": "mp4", "short_ai": "mp4", "voice": "mp3", "tone_wav": "wav",
           "tone_mp3": "mp3", "tone_pcm": "pcm", "image": "png"}
    paths = {}
    for name, args in files.items():
        paths[name] = f"{folder}/{name}.{ext[name]}"
        if not os.path.exists(paths[name]):
            ffmpeg(*args, paths[name])
    return paths
