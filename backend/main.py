# backend/main.py
import os
import uuid
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import yt_dlp
import ffmpeg
from faster_whisper import WhisperModel
import spacy
from googletrans import Translator
from indic_transliteration import sanscript
from indic_transliteration.sanscript import transliterate
import requests
from deta import Deta

app = FastAPI()
deta = Deta()
db = deta.Base("youtube_transcripts")
tasks_db = deta.Base("processing_tasks")

# CORS Configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize models
whisper_model = WhisperModel("base", device="cpu", compute_type="int8")
nlp = spacy.load("en_core_web_sm")
translator = Translator()

# LLM Providers configuration
LLM_PROVIDERS = [
    {
        "name": "deepseek",
        "url": "https://api.deepseek.com/v1/chat/completions",
        "headers": {"Authorization": f"Bearer {os.getenv('DEEPSEEK_API_KEY')}"},
        "prompts": {
            "translation": "Translate to accurate Hindi: {text}",
            "pronunciation": "Convert to Hindi phonetic (Devanagari): {text}"
        }
    },
    {
        "name": "huggingface",
        "url": "https://api-inference.huggingface.co/models/HuggingFaceH4/zephyr-7b-beta",
        "headers": {"Authorization": f"Bearer {os.getenv('HF_API_KEY')}"},
        "prompts": {
            "translation": "<|system|>Translate English to Hindi</s><|user|>{text}</s>",
            "pronunciation": "<|system|>Convert to Hindi pronunciation</s><|user|>{text}</s>"
        }
    }
]

class VideoRequest(BaseModel):
    url: str

def get_video_info(url: str) -> dict:
    ydl_opts = {'quiet': True, 'extract_flat': True}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        return {
            'id': info['id'],
            'title': info['title'],
            'duration': info['duration'],
            'audio_stream_url': next(f['url'] for f in info['formats'] if f.get('acodec') != 'none')
        }

def split_into_chunks(duration: int, chunk_size: int = 30) -> list:
    chunks = []
    start = 0
    while start < duration:
        end = min(start + chunk_size, duration)
        chunks.append({"start": start, "end": end})
        start = end
    return chunks

def transcribe_audio_chunk(audio_stream_url: str, start: int, end: int):
    try:
        audio_input = ffmpeg.input(audio_stream_url, ss=start, t=end-start)
        audio_output = audio_input.output('pipe:', format='wav', acodec='pcm_s16le', ac=1, ar=16000)
        out, _ = ffmpeg.run(audio_output, capture_stdout=True, capture_stderr=True)
        
        segments, _ = whisper_model.transcribe(
            out,
            word_timestamps=True,
            vad_filter=True
        )
        return list(segments)
    except Exception as e:
        print(f"Transcription failed: {str(e)}")
        return []

def split_into_sentences(text: str, words: list) -> list:
    try:
        doc = nlp(text)
        if len(list(doc.sents)) > 1:
            sentences = []
            for sent in doc.sents:
                sent_text = sent.text.strip()
                if sent_text:
                    sent_words = [w for w in words if w['text'] in sent_text]
                    if sent_words:
                        start = min(w['start'] for w in sent_words)
                        end = max(w['end'] for w in sent_words)
                        sentences.append({"text": sent_text, "start": start, "end": end})
            return sentences
    except:
        pass
    
    return [{"text": text, "start": words[0]['start'], "end": words[-1]['end']}]

def translate_sentence(text: str) -> str:
    try:
        return translator.translate(text, src='en', dest='hi').text
    except:
        for provider in LLM_PROVIDERS:
            try:
                prompt = provider['prompts']['translation'].format(text=text)
                response = requests.post(
                    provider['url'],
                    headers=provider.get('headers', {}),
                    json={"inputs": prompt} if "huggingface" in provider['url'] else {
                        "model": provider.get('model', ''),
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": 200
                    },
                    timeout=15
                )
                if "huggingface" in provider['url']:
                    return response.json()[0]['generated_text']
                else:
                    return response.json()["choices"][0]["message"]["content"]
            except:
                continue
        return "Translation unavailable"

def generate_pronunciation(text: str) -> str:
    for provider in LLM_PROVIDERS:
        try:
            prompt = provider['prompts']['pronunciation'].format(text=text)
            response = requests.post(
                provider['url'],
                headers=provider.get('headers', {}),
                json={"inputs": prompt} if "huggingface" in provider['url'] else {
                    "model": provider.get('model', ''),
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 200
                },
                timeout=15
            )
            if "huggingface" in provider['url']:
                return response.json()[0]['generated_text']
            else:
                return response.json()["choices"][0]["message"]["content"]
        except:
            continue
    
    return transliterate(text, sanscript.ITRANS, sanscript.DEVANAGARI)

def format_time(seconds: float) -> str:
    return str(datetime.utcfromtimestamp(seconds).strftime('%H:%M:%S'))

def process_chunk(task_id: str, video_info: dict, chunk: dict):
    segments = transcribe_audio_chunk(video_info['audio_stream_url'], chunk['start'], chunk['end'])
    
    for segment in segments:
        words = [{"text": word.word, "start": word.start, "end": word.end} for word in segment.words]
        full_text = " ".join(word['text'] for word in words)
        
        sentences = split_into_sentences(full_text, words)
        
        for sentence in sentences:
            translation = translate_sentence(sentence['text'])
            pronunciation = generate_pronunciation(sentence['text'])
            
            db.put({
                "key": str(uuid.uuid4()),
                "task_id": task_id,
                "english": sentence['text'],
                "pronunciation_hindi": pronunciation,
                "translation_hindi": translation,
                "start_time": format_time(sentence['start']),
                "end_time": format_time(sentence['end']),
                "start_time_float": sentence['start']
            })
    
    task = tasks_db.get(task_id)
    if task:
        task['processed_chunks'] += 1
        if task['processed_chunks'] == task['total_chunks']:
            task['status'] = 'completed'
        tasks_db.put(task)

def process_video_background(task_id: str, url: str):
    try:
        video_info = get_video_info(url)
        chunks = split_into_chunks(video_info['duration'])
        
        task_data = {
            "key": task_id,
            "video_id": video_info['id'],
            "status": "processing",
            "processed_chunks": 0,
            "total_chunks": len(chunks),
            "created_at": time.time()
        }
        tasks_db.put(task_data)
        
        with ThreadPoolExecutor(max_workers=3) as executor:
            for chunk in chunks:
                executor.submit(process_chunk, task_id, video_info, chunk)
                
    except Exception as e:
        task = tasks_db.get(task_id)
        task['status'] = 'failed'
        task['error'] = str(e)
        tasks_db.put(task)

@app.post("/api/start_processing")
async def start_processing(request: VideoRequest, background_tasks: BackgroundTasks):
    task_id = str(uuid.uuid4())
    background_tasks.add_task(process_video_background, task_id, request.url)
    return {"task_id": task_id}

@app.get("/api/task_status/{task_id}")
async def get_task_status(task_id: str):
    task = tasks_db.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task

@app.get("/api/transcript/{task_id}")
async def get_transcript(task_id: str, last_key: str = None):
    query = {"task_id": task_id}
    if last_key:
        query["__key__ >"] = last_key
        
    res = db.fetch(query, limit=20, sort="start_time_float")
    return {
        "sentences": res.items,
        "last_key": res.last
}
