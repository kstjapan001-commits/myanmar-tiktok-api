#!/usr/bin/env python3
"""
Myanmar TikTok API â 100% FREE, zero API keys needed
Pipeline: yt-dlp â googletrans (free) â gTTS (free) â FFmpeg
No signup, no credit card, no API keys required.
"""

import os, json, subprocess, uuid, re, shutil, time
from typing import Optional

from fastapi import FastAPI, HTTPException, Header
from fastapi.responses import FileResponse
from pydantic import BaseModel
from gtts import gTTS

app = FastAPI(title="Myanmar TikTok API â Free", version="3.0.0")

API_SECRET = os.environ.get("API_SECRET", "")


# ââ Helpers âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

def parse_vtt(text: str) -> list[str]:
    """Extract subtitle lines from VTT, return list of clean strings."""
    seen, out = set(), []
    for line in text.split("\n"):
        line = line.strip()
        if not line or "-->" in line or line in ("WEBVTT",):
            continue
        if re.match(r"^\d+$", line) or line.startswith("NOTE"):
            continue
        clean = re.sub(r"<[^>]+>", "", line)
        if clean and clean not in seen:
            seen.add(clean)
            out.append(clean)
    return out[:80]  # max 80 lines


def translate_to_myanmar(lines: list[str]) -> list[str]:
    """Translate a list of English lines to Myanmar using deep-translator (free)."""
    from deep_translator import GoogleTranslator
    translator = GoogleTranslator(source="en", target="my")
    result = []
    batch_size = 10
    for i in range(0, len(lines), batch_size):
        batch = lines[i:i+batch_size]
        try:
            translations = translator.translate_batch(batch)
            result.extend(t or batch[j] for j, t in enumerate(translations))
        except Exception:
            result.extend(batch)
        time.sleep(0.3)
    return result


def build_srt(myanmar_lines: list[str], duration: int = 60) -> str:
    """Build an SRT subtitle file from Myanmar lines spread over duration seconds."""
    if not myanmar_lines:
        return ""
    # Distribute lines evenly over duration
    n = min(len(myanmar_lines), 16)
    lines = myanmar_lines[:n]
    interval = duration / n
    srt_blocks = []
    for i, line in enumerate(lines):
        start = i * interval
        end = start + interval - 0.2
        def fmt(s):
            h, r = divmod(int(s), 3600)
            m, sec = divmod(r, 60)
            ms = int((s - int(s)) * 1000)
            return f"{h:02d}:{m:02d}:{sec:02d},{ms:03d}"
        srt_blocks.append(f"{i+1}\n{fmt(start)} --> {fmt(end)}\n{line}\n")
    return "\n".join(srt_blocks)


def make_narration(myanmar_lines: list[str]) -> str:
    """Join translated lines into a narration for TTS."""
    return " ".join(myanmar_lines[:50])


# ââ Request model âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

class ProcessRequest(BaseModel):
    youtube_url: str


# ââ Main endpoint âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

@app.post("/process")
async def process_video(req: ProcessRequest, x_api_key: Optional[str] = Header(None)):
    if API_SECRET and x_api_key != API_SECRET:
        raise HTTPException(401, "Unauthorized")

    job_id = str(uuid.uuid4())[:8]
    tmp = f"/tmp/myjob_{job_id}"
    os.makedirs(tmp, exist_ok=True)
    output_path = f"{tmp}/output.mp4"

    try:
        # 1 â Download subtitles (English auto-subs)
        subprocess.run(
            ["yt-dlp", "--skip-download",
             "--write-auto-subs", "--sub-lang", "en",
             "--sub-format", "vtt",
             "-o", f"{tmp}/vid.%(ext)s",
             req.youtube_url],
            capture_output=True, timeout=60,
        )

        vtt = ""
        for f in os.listdir(tmp):
            if f.endswith(".vtt"):
                vtt = open(f"{tmp}/{f}", encoding="utf-8").read()
                break

        if not vtt:
            raise RuntimeError("No subtitles found â try a YouTube video with English captions")

        en_lines = parse_vtt(vtt)
        if not en_lines:
            raise RuntimeError("Could not parse subtitles")

        # 2 â Translate to Myanmar (googletrans, free)
        my_lines = translate_to_myanmar(en_lines)

        # 3 â Build SRT & narration
        srt_text  = build_srt(my_lines, duration=60)
        narration = make_narration(my_lines)

        if not narration.strip():
            raise RuntimeError("Translation produced empty narration")

        # 4 â Myanmar TTS via gTTS (free)
        audio_path = f"{tmp}/narration.mp3"
        gTTS(text=narration, lang="my", slow=False).save(audio_path)

        # 5 â Save SRT
        srt_path = f"{tmp}/subs.srt"
        open(srt_path, "w", encoding="utf-8").write(srt_text)

        # 6 â Download source video
        subprocess.run(
            ["yt-dlp",
             "-f", "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720]",
             "--merge-output-format", "mp4",
             "-o", f"{tmp}/src.%(ext)s",
             req.youtube_url],
            capture_output=True, timeout=180,
        )
        src_video = next(
            (f"{tmp}/{f}" for f in os.listdir(tmp)
             if f.startswith("src.") and f.endswith((".mp4", ".webm", ".mkv"))),
            None,
        )

        # 7 â FFmpeg: scale to 9:16, burn subtitles, replace audio, trim 60s
        sub_style = (
            "FontName=Noto Sans Myanmar,FontSize=22,"
            "PrimaryColour=&Hffffff,OutlineColour=&H000000,Outline=2,Alignment=2"
        )
        srt_esc = srt_path.replace("\\", "\\\\").replace(":", "\\:")

        if src_video and os.path.exists(src_video):
            ffcmd = [
                "ffmpeg", "-y",
                "-i", src_video, "-i", audio_path,
                "-vf", (
                    f"scale=720:1280:force_original_aspect_ratio=increase,"
                    f"crop=720:1280,"
                    f"subtitles={srt_esc}:force_style='{sub_style}'"
                ),
                "-map", "0:v:0", "-map", "1:a:0",
                "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                "-c:a", "aac", "-b:a", "128k",
                "-t", "60", "-movflags", "+faststart", "-pix_fmt", "yuv420p",
                output_path,
            ]
        else:
            ffcmd = [
                "ffmpeg", "-y",
                "-f", "lavfi", "-i", "color=c=0x1B2A6B:size=720x1280:rate=25",
                "-i", audio_path,
                "-vf", f"subtitles={srt_esc}:force_style='{sub_style}'",
                "-map", "0:v", "-map", "1:a",
                "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                "-c:a", "aac", "-b:a", "128k",
                "-t", "60", "-movflags", "+faststart", "-pix_fmt", "yuv420p",
                output_path,
            ]

        ff = subprocess.run(ffcmd, capture_output=True, timeout=300)
        if not os.path.exists(output_path) or os.path.getsize(output_path) < 1000:
            raise RuntimeError(f"FFmpeg failed:\n{ff.stderr.decode()[-600:]}")

        return FileResponse(
            output_path,
            media_type="video/mp4",
            filename="myanmar_tiktok.mp4",
            headers={"X-Video-Size": str(os.path.getsize(output_path))},
        )

    except HTTPException:
        raise
    except Exception as exc:
        shutil.rmtree(tmp, ignore_errors=True)
        raise HTTPException(500, str(exc))


# ââ Health check ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "mode": "100% free â no API keys needed",
        "ffmpeg": subprocess.run(["which", "ffmpeg"],  capture_output=True).returncode == 0,
        "yt_dlp": subprocess.run(["which", "yt-dlp"],  capture_output=True).returncode == 0,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
