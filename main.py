#!/usr/bin/env python3
"""Myanmar TikTok Processing API"""
import os, json, subprocess, uuid, re, base64, shutil
from typing import Optional
import requests
from fastapi import FastAPI, HTTPException, Header
from fastapi.responses import FileResponse
from pydantic import BaseModel

app = FastAPI(title="Myanmar TikTok API", version="1.0.0")

OPENAI_KEY = os.environ.get("OPENAI_API_KEY", "")
GOOGLE_KEY = os.environ.get("GOOGLE_TTS_API_KEY", "")
API_SECRET = os.environ.get("API_SECRET", "")

def parse_vtt(text):
    seen, out = set(), []
    for line in text.split("\n"):
        line = line.strip()
        if not line or "-->" in line or line == "WEBVTT": continue
        if re.match(r"^\d+$", line) or line.startswith("NOTE"): continue
        clean = re.sub(r"<[^>]+>", "", line)
        if clean and clean not in seen:
            seen.add(clean); out.append(clean)
    return " ".join(out[:500])

def gpt4o_myanmar(transcript, title):
    system = ('You are a Myanmar children content creator. Create a 1-minute Myanmar children story video script. '
              'Output ONLY valid JSON with keys: "title" (Myanmar title), "narration" (Myanmar narration ~150-200 words), '
              '"srt" (complete SRT file Myanmar Unicode 10-16 blocks 60s total), "summary" (English summary)')
    resp = requests.post("https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {OPENAI_KEY}", "Content-Type": "application/json"},
        json={"model": "gpt-4o", "messages": [{"role":"system","content":system},
              {"role":"user","content":f"Title: {title}\nTranscript: {transcript[:2000]}"}],
              "response_format":{"type":"json_object"}, "max_tokens":2000, "temperature":0.7}, timeout=90)
    resp.raise_for_status()
    return json.loads(resp.json()["choices"][0]["message"]["content"])

def google_tts(text):
    resp = requests.post(f"https://texttospeech.googleapis.com/v1/text:synthesize?key={GOOGLE_KEY}",
        json={"input":{"text":text},"voice":{"languageCode":"my-MM","name":"my-MM-Standard-A"},
              "audioConfig":{"audioEncoding":"MP3"}}, timeout=30)
    resp.raise_for_status()
    b64 = resp.json().get("audioContent","")
    if not b64: raise RuntimeError("TTS returned no audio")
    return base64.b64decode(b64)

class ProcessRequest(BaseModel):
    youtube_url: str

@app.post("/process")
async def process_video(req: ProcessRequest, x_api_key: Optional[str] = Header(None)):
    if API_SECRET and x_api_key != API_SECRET: raise HTTPException(401, "Unauthorized")
    if not OPENAI_KEY: raise HTTPException(500, "OPENAI_API_KEY not set")
    if not GOOGLE_KEY: raise HTTPException(500, "GOOGLE_TTS_API_KEY not set")
    job_id = str(uuid.uuid4())[:8]
    tmp = f"/tmp/job_{job_id}"; os.makedirs(tmp, exist_ok=True)
    output_path = f"{tmp}/output.mp4"
    try:
        info = subprocess.run(["yt-dlp","--dump-json","--skip-download",req.youtube_url], capture_output=True, text=True, timeout=30)
        title = ""
        if info.returncode == 0:
            try: title = json.loads(info.stdout).get("title","")
            except: pass
        subprocess.run(["yt-dlp","--skip-download","--write-auto-subs","--sub-lang","en","--sub-format","vtt","-o",f"{tmp}/vid.%(ext)s",req.youtube_url], capture_output=True, timeout=60)
        vtt = ""
        for f in os.listdir(tmp):
            if f.endswith(".vtt"): vtt = open(f"{tmp}/{f}", encoding="utf-8").read(); break
        transcript = parse_vtt(vtt) if vtt else f"Children story: {title}"
        script = gpt4o_myanmar(transcript, title)
        narration = script.get("narration",""); srt_text = script.get("srt","")
        if not narration: raise RuntimeError("GPT-4o empty narration")
        mp3 = google_tts(narration)
        audio_path = f"{tmp}/narration.mp3"
        open(audio_path,"wb").write(mp3)
        srt_path = f"{tmp}/subs.srt"
        open(srt_path,"w",encoding="utf-8").write(srt_text)
        subprocess.run(["yt-dlp","-f","bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720]","--merge-output-format","mp4","-o",f"{tmp}/src.%(ext)s",req.youtube_url], capture_output=True, timeout=180)
        src_video = next((f"{tmp}/{f}" for f in os.listdir(tmp) if f.startswith("src.") and f.endswith((".mp4",".webm",".mkv"))), None)
        style = "FontName=Noto Sans Myanmar,FontSize=22,PrimaryColour=&Hffffff,OutlineColour=&H000000,Outline=2,Alignment=2"
        srt_esc = srt_path.replace("\\","\\\\").replace(":","\\:")
        if src_video and os.path.exists(src_video):
            ffcmd = ["ffmpeg","-y","-i",src_video,"-i",audio_path,"-vf",f"scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,subtitles={srt_esc}:force_style='{style}'","-map","0:v:0","-map","1:a:0","-c:v","libx264","-preset","fast","-crf","23","-c:a","aac","-b:a","128k","-t","60","-movflags","+faststart","-pix_fmt","yuv420p",output_path]
        else:
            ffcmd = ["ffmpeg","-y","-f","lavfi","-i","color=c=0x1B2A6B:size=720x1280:rate=25","-i",audio_path,"-vf",f"subtitles={srt_esc}:force_style='{style}'","-map","0:v","-map","1:a","-c:v","libx264","-preset","fast","-crf","23","-c:a","aac","-b:a","128k","-t","60","-movflags","+faststart","-pix_fmt","yuv420p",output_path]
        ff = subprocess.run(ffcmd, capture_output=True, timeout=300)
        if not os.path.exists(output_path) or os.path.getsize(output_path) < 1000:
            raise RuntimeError(f"FFmpeg failed: {ff.stderr.decode()[-400:]}")
        return FileResponse(output_path, media_type="video/mp4", filename="myanmar_tiktok.mp4", headers={"X-Video-Size":str(os.path.getsize(output_path))})
    except HTTPException: raise
    except Exception as e: shutil.rmtree(tmp,ignore_errors=True); raise HTTPException(500, str(e))

@app.get("/health")
async def health():
    return {"status":"ok","ffmpeg":subprocess.run(["which","ffmpeg"],capture_output=True).returncode==0,"yt_dlp":subprocess.run(["which","yt-dlp"],capture_output=True).returncode==0,"openai":bool(OPENAI_KEY),"tts":bool(GOOGLE_KEY)}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT",8000)))
