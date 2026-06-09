#!/usr/bin/env python3
"""
Myanmar TikTok API — 100% FREE, zero API keys needed
Pipeline: yt-dlp -> googletrans (free) -> gTTS (free) -> FFmpeg
"""
import os, json, subprocess, uuid, re, shutil, time
from typing import Optional
from fastapi import FastAPI, HTTPException, Header
from fastapi.responses import FileResponse
from pydantic import BaseModel
from gtts import gTTS

app = FastAPI(title="Myanmar TikTok API Free", version="3.0.0")
API_SECRET = os.environ.get("API_SECRET", "")

def parse_vtt(text):
    seen, out = set(), []
    for line in text.split("\n"):
        line = line.strip()
        if not line or "-->" in line or line in ("WEBVTT",): continue
        if re.match(r"^\d+$", line) or line.startswith("NOTE"): continue
        clean = re.sub(r"<[^>]+>", "", line)
        if clean and clean not in seen:
            seen.add(clean); out.append(clean)
    return out[:80]

def translate_to_myanmar(lines):
    from googletrans import Translator
    translator = Translator()
    result = []
    for i in range(0, len(lines), 10):
        batch = lines[i:i+10]
        try:
            translations = translator.translate(batch, src="en", dest="my")
            for t in translations: result.append(t.text)
        except: result.extend(batch)
        time.sleep(0.3)
    return result

def build_srt(my_lines, duration=60):
    if not my_lines: return ""
    n = min(len(my_lines), 16); lines = my_lines[:n]; interval = duration / n
    blocks = []
    for i, line in enumerate(lines):
        s = i * interval; e = s + interval - 0.2
        def fmt(x):
            h,r = divmod(int(x),3600); m,sec = divmod(r,60); ms = int((x-int(x))*1000)
            return f"{h:02d}:{m:02d}:{sec:02d},{ms:03d}"
        blocks.append(f"{i+1}\n{fmt(s)} --> {fmt(e)}\n{line}\n")
    return "\n".join(blocks)

class ProcessRequest(BaseModel):
    youtube_url: str

@app.post("/process")
async def process_video(req: ProcessRequest, x_api_key: Optional[str] = Header(None)):
    if API_SECRET and x_api_key != API_SECRET:
        raise HTTPException(401, "Unauthorized")
    job_id = str(uuid.uuid4())[:8]
    tmp = f"/tmp/myjob_{job_id}"
    os.makedirs(tmp, exist_ok=True)
    output_path = f"{tmp}/output.mp4"
    try:
        subprocess.run(["yt-dlp","--skip-download","--write-auto-subs","--sub-lang","en","--sub-format","vtt","-o",f"{tmp}/vid.%(ext)s",req.youtube_url],capture_output=True,timeout=60)
        vtt = ""
        for f in os.listdir(tmp):
            if f.endswith(".vtt"): vtt = open(f"{tmp}/{f}",encoding="utf-8").read(); break
        if not vtt: raise RuntimeError("No subtitles found")
        en_lines = parse_vtt(vtt)
        if not en_lines: raise RuntimeError("Could not parse subtitles")
        my_lines = translate_to_myanmar(en_lines)
        srt_text = build_srt(my_lines, 60)
        narration = " ".join(my_lines[:50])
        if not narration.strip(): raise RuntimeError("Translation empty")
        audio_path = f"{tmp}/narration.mp3"
        gTTS(text=narration, lang="my", slow=False).save(audio_path)
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
            raise RuntimeError(f"FFmpeg failed: {ff.stderr.decode()[-400:]}")
        return FileResponse(output_path, media_type="video/mp4", filename="myanmar_tiktok.mp4")
    except HTTPException: raise
    except Exception as exc:
        shutil.rmtree(tmp, ignore_errors=True); raise HTTPException(500, str(exc))

@app.get("/health")
async def health():
    return {"status":"ok","mode":"100% free - no API keys","ffmpeg":subprocess.run(["which","ffmpeg"],capture_output=True).returncode==0,"yt_dlp":subprocess.run(["which","yt-dlp"],capture_output=True).returncode==0}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
