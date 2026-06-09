#!/usr/bin/env python3
"""
Myanmar TikTok Processing API — FREE version
Uses Gemini Flash (free) + gTTS (free, no key needed)
Only requires: GEMINI_API_KEY (free from aistudio.google.com)
"""

import os, json, subprocess, uuid, re, shutil
from typing import Optional

import requests
from fastapi import FastAPI, HTTPException, Header
from fastapi.responses import FileResponse
from pydantic import BaseModel
from gtts import gTTS

app = FastAPI(title="Myanmar TikTok API", version="2.0.0")

GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")
API_SECRET  = os.environ.get("API_SECRET", "")


def parse_vtt(text: str) -> str:
    seen, out = set(), []
    for line in text.split("\n"):
        line = line.strip()
        if not line or "-->" in line or line in ("WEBVTT",):
            continue
        if __import__("re").match(r"^\d+$", line) or line.startswith("NOTE"):
            continue
        clean = __import__("re").sub(r"<[^>]+>", "", line)
        if clean and clean not in seen:
            seen.add(clean)
            out.append(clean)
    return " ".join(out[:500])


def gemini_myanmar(transcript: str, title: str) -> dict:
    prompt = (
        "You are a Myanmar children's content creator. "
        "Create a 1-minute Myanmar-language children's story video script.\n"
        "Output ONLY valid JSON with these keys:\n"
        '  \"title\"     : Myanmar title (string)\n'
        '  \"narration\" : Full Myanmar narration (~150-200 words, simple, age 4-10)\n'
        '  \"srt\"       : Complete SRT file in Myanmar Unicode, 10-16 subtitle blocks, 60s total\n'
        '  \"summary\"   : One-sentence English summary\n\n'
        f"Story source — Title: {title}\nTranscript: {transcript[:2000]}"
    )
    resp = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_KEY}",
        json={"contents":[{"parts":[{"text":prompt}]}],"generationConfig":{"responseMimeType":"application/json","temperature":0.7,"maxOutputTokens":2048}},
        timeout=90,
    )
    resp.raise_for_status()
    raw = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
    return json.loads(raw)


def free_tts(text: str, path: str) -> None:
    gTTS(text=text, lang="my", slow=False).save(path)


class ProcessRequest(BaseModel):
    youtube_url: str


@app.post("/process")
async def process_video(req: ProcessRequest, x_api_key: Optional[str] = Header(None)):
    if API_SECRET and x_api_key != API_SECRET:
        raise HTTPException(401, "Unauthorized")
    if not GEMINI_KEY:
        raise HTTPException(500, "GEMINI_API_KEY not configured")

    job_id = str(uuid.uuid4())[:8]
    tmp = f"/tmp/myjob_{job_id}"
    os.makedirs(tmp, exist_ok=True)
    output_path = f"{tmp}/output.mp4"

    try:
        info = subprocess.run(["yt-dlp","--dump-json","--skip-download",req.youtube_url],capture_output=True,text=True,timeout=30)
        title = ""
        if info.returncode == 0:
            try: title = json.loads(info.stdout).get("title","")
            except: pass

        subprocess.run(["yt-dlp","--skip-download","--write-auto-subs","--sub-lang","en","--sub-format","vtt","-o",f"{tmp}/vid.%(ext)s",req.youtube_url],capture_output=True,timeout=60)
        vtt = ""
        for f in os.listdir(tmp):
            if f.endswith(".vtt"): vtt = open(f"{tmp}/{f}",encoding="utf-8").read(); break
        transcript = parse_vtt(vtt) if vtt else f"Children's story: {title}"

        script = gemini_myanmar(transcript, title)
        narration = script.get("narration","")
        srt_text  = script.get("srt","")
        if not narration: raise RuntimeError("Gemini returned empty narration")

        audio_path = f"{tmp}/narration.mp3"
        free_tts(narration, audio_path)

        srt_path = f"{tmp}/subs.srt"
        open(srt_path,"w",encoding="utf-8").write(srt_text)

        subprocess.run(["yt-dlp","-f","bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720]","--merge-output-format","mp4","-o",f"{tmp}/src.%(ext)s",req.youtube_url],capture_output=True,timeout=180)
        src_video = next((f"{tmp}/{f}" for f in os.listdir(tmp) if f.startswith("src.") and f.endswith((".mp4",".webm",".mkv"))),None)

        sub_style = "FontName=Noto Sans Myanmar,FontSize=22,PrimaryColour=&Hffffff,OutlineColour=&H000000,Outline=2,Alignment=2"
        srt_esc = srt_path.replace("\\","\\\\").replace(":","\\:")

        if src_video and os.path.exists(src_video):
            ffcmd = ["ffmpeg","-y","-i",src_video,"-i",audio_path,"-vf",f"scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,subtitles={srt_esc}:force_style='{sub_style}'","-map","0:v:0","-map","1:a:0","-c:v","libx264","-preset","fast","-crf","23","-c:a","aac","-b:a","128k","-t","60","-movflags","+faststart","-pix_fmt","yuv420p",output_path]
        else:
            ffcmd = ["ffmpeg","-y","-f","lavfi","-i","color=c=0x1B2A6B:size=720x1280:rate=25","-i",audio_path,"-vf",f"subtitles={srt_esc}:force_style='{sub_style}'","-map","0:v","-map","1:a","-c:v","libx264","-preset","fast","-crf","23","-c:a","aac","-b:a","128k","-t","60","-movflags","+faststart","-pix_fmt","yuv420p",output_path]

        ff = subprocess.run(ffcmd, capture_output=True, timeout=300)
        if not os.path.exists(output_path) or os.path.getsize(output_path) < 1000:
            raise RuntimeError(f"FFmpeg failed:\n{ff.stderr.decode()[-600:]}")

        return FileResponse(output_path, media_type="video/mp4", filename="myanmar_tiktok.mp4")

    except HTTPException: raise
    except Exception as exc:
        shutil.rmtree(tmp, ignore_errors=True)
        raise HTTPException(500, str(exc))


@app.get("/health")
async def health():
    return {"status":"ok","gemini":bool(GEMINI_KEY),"ffmpeg":subprocess.run(["which","ffmpeg"],capture_output=True).returncode==0,"yt_dlp":subprocess.run(["which","yt-dlp"],capture_output=True).returncode==0,"gtts":True}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
