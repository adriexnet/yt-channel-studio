"""Media helpers shared by the tests (real ffmpeg / ffprobe)."""
import json, subprocess


def ffmpeg(*args):
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args], check=True)


def probe(path):
    """(width, height, duration, has_audio, frame_rate) of a video."""
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                        "stream=codec_type,width,height,r_frame_rate:format=duration", "-of", "json", path],
                       capture_output=True, text=True)
    data = json.loads(r.stdout)
    video = [s for s in data["streams"] if s["codec_type"] == "video"][0]
    has_audio = any(s["codec_type"] == "audio" for s in data["streams"])
    return video["width"], video["height"], float(data["format"]["duration"]), has_audio, video["r_frame_rate"]


def media_duration(path):
    return probe(path)[2] if path.endswith(".mp4") else float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
        capture_output=True, text=True).stdout.strip() or 0)
