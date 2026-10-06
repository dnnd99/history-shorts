#!/usr/bin/env python3
"""History Shorts: topik -> script (Gemini) -> suara (Edge TTS) -> gambar (Wikimedia)
-> render FFmpeg 1080x1920 -> upload YouTube (private). Satu video per jalan."""
import argparse, asyncio, json, math, os, random, re, subprocess, sys, time
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()
BASE = Path(__file__).parent
OUT = BASE / "out"
STATE = BASE / "state.json"
UA = {"User-Agent": "HistoryShortsBot/1.0 (personal project; contact: owner)"}

GEMINI_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
VOICE = os.getenv("VOICE", "en-US-AndrewNeural")
FONT = os.getenv("FONT", "DejaVu Sans")
PRIVACY = os.getenv("PRIVACY", "private")
ALLOW_CC_BY = os.getenv("ALLOW_CC_BY", "false").lower() == "true"
AUTO_REFILL = os.getenv("AUTO_REFILL", "true").lower() == "true"
WIKI_API = "https://en.wikipedia.org/w/api.php"
FPS = 30
MAX_SECONDS = 58.0

ANGLES = [
    "a little-known turning point in their life",
    "the rivalry or betrayal that defined them",
    "the moment everything could have gone differently",
    "a surprising contrast between legend and reality",
    "how a small decision changed history",
    "their rise from nothing",
    "their downfall and the final days",
]


def log(*a):
    print("[pipeline]", *a, flush=True)


def slugify(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


# ---------- state ----------
def load_state():
    if STATE.exists():
        return json.loads(STATE.read_text())
    return {"done": []}


def save_state(st):
    STATE.write_text(json.dumps(st, indent=2))


def canonical_titles(titles):
    """Judul input -> judul kanonik Wikipedia (setelah redirect). None kalau tidak ada / halaman disambiguasi."""
    result = {}
    titles = list(dict.fromkeys(titles))
    for i in range(0, len(titles), 40):
        batch = titles[i:i + 40]
        r = requests.get(WIKI_API, params={
            "action": "query", "titles": "|".join(batch), "redirects": 1,
            "prop": "pageprops", "ppprop": "disambiguation", "format": "json"},
            headers=UA, timeout=30)
        r.raise_for_status()
        q = r.json()["query"]
        norm = {n["from"]: n["to"] for n in q.get("normalized", [])}
        redir = {x["from"]: x["to"] for x in q.get("redirects", [])}
        pages = {p["title"]: p for p in q["pages"].values()}
        for t in batch:
            c = norm.get(t, t)
            c = redir.get(c, c)
            pg = pages.get(c)
            bad = (not pg) or ("missing" in pg) or ("pageprops" in pg)
            result[t] = None if bad else c
    return result


def _topic_lines():
    lines = [l.strip() for l in (BASE / "topics.txt").read_text().splitlines()]
    return [l for l in lines if l and not l.startswith("#")]


def _todo(st):
    """Topik belum dipakai, tanpa duplikat (termasuk duplikat lewat redirect, mis. Napoleon = Napoleon Bonaparte)."""
    todo = [l for l in _topic_lines() if l not in st["done"]]
    try:
        canon = canonical_titles(st["done"] + todo)
    except Exception as e:
        log("cek duplikat Wikipedia gagal, pakai pencocokan teks biasa:", e)
        return todo
    seen = {canon[d].lower() for d in st["done"] if canon.get(d)}
    out = []
    for t in todo:
        c = canon.get(t)
        if not c:
            log("lewati (tidak ada artikel Wikipedia / halaman disambiguasi):", t)
            continue
        if c.lower() in seen:
            log("lewati (duplikat):", t)
            continue
        seen.add(c.lower())
        out.append(t)
    return out


def refill_topics(st, n=30):
    """Minta Gemini mengusulkan tokoh baru, verifikasi ke Wikipedia, buang yang sudah ada, tambah ke topics.txt."""
    existing = list(dict.fromkeys(_topic_lines() + st["done"]))
    canon = canonical_titles(existing) if existing else {}
    known = {c.lower() for c in canon.values() if c} | {e.lower() for e in existing}
    added = []
    for rnd in range(4):
        need = n - len(added)
        if need <= 0:
            break
        avoid = existing + added
        prompt = f"""List {need + 15} historical figures from world history who would make compelling YouTube Shorts.
Mix eras and regions (Europe, Asia, Africa, the Americas, Middle East), rulers, scientists, explorers, generals,
and women. Use the exact title of each person's English Wikipedia article.
Do NOT include any of these: {json.dumps(avoid, ensure_ascii=False)}
Return JSON: {{"figures": ["...", "..."]}}"""
        try:
            cands = [c.strip() for c in gemini_json(prompt).get("figures", []) if isinstance(c, str) and c.strip()]
        except Exception as e:
            log("Gemini gagal:", e)
            break
        cc = canonical_titles(cands)
        for t in cands:
            c = cc.get(t)
            if not c or c.lower() in known or t.lower() in known:
                continue
            known.add(c.lower())
            known.add(t.lower())
            added.append(c)
            if len(added) >= n:
                break
    if not added:
        sys.exit("Gagal menambah topik baru. Isi topics.txt manual.")
    with open(BASE / "topics.txt", "a", encoding="utf-8") as f:
        f.write(f"\n# ditambah otomatis {time.strftime('%Y-%m-%d')}\n" + "\n".join(added) + "\n")
    log(f"{len(added)} topik baru ditambahkan ke topics.txt")


def pick_topic(arg, st):
    if arg:
        return arg
    todo = _todo(st)
    if not todo and AUTO_REFILL:
        log("stok topik habis, minta Gemini mengusulkan tokoh baru...")
        refill_topics(st)
        todo = _todo(st)
    if not todo:
        sys.exit("Semua topik sudah dipakai. Tambah topik baru di topics.txt.")
    return todo[0]


# ---------- sumber fakta ----------
def wiki_source(topic):
    r = requests.get(
        "https://en.wikipedia.org/w/api.php",
        params={"action": "query", "prop": "extracts", "explaintext": 1,
                "exchars": 6000, "redirects": 1, "titles": topic, "format": "json"},
        headers=UA, timeout=30)
    r.raise_for_status()
    page = next(iter(r.json()["query"]["pages"].values()))
    text = page.get("extract", "").strip()
    if len(text) < 300:
        raise RuntimeError(f"Artikel Wikipedia '{topic}' tidak ditemukan / terlalu pendek")
    return text


# ---------- script (Gemini) ----------
def gemini_json(prompt):
    if not GEMINI_KEY:
        sys.exit("GEMINI_API_KEY belum diisi di .env")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
    r = requests.post(
        url, headers={"x-goog-api-key": GEMINI_KEY, "Content-Type": "application/json"},
        json={"contents": [{"parts": [{"text": prompt}]}],
              "generationConfig": {"responseMimeType": "application/json", "temperature": 0.8}},
        timeout=120)
    r.raise_for_status()
    text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
    return json.loads(text)


def gen_script(topic, source):
    angle = random.choice(ANGLES)
    prompt = f"""You write narration for a YouTube Short (under 55 seconds) about the historical figure: {topic}.
Angle: {angle}.

STRICT RULES
- Use ONLY facts found in the SOURCE below. Do not invent dates, quotes, numbers or events. If unsure, leave it out.
- Narration: 105 to 125 words, plain spoken English, no emojis, no stage directions.
- Structure: a gripping first sentence (hook), the story in short punchy sentences, a surprising final line.
- Do not start with "Did you know".
- image_queries: 8 short search terms for finding public-domain historical images on Wikimedia Commons
  (portraits, paintings, maps, places, artifacts related to the story). Include "{topic}" in at least 3.

Return JSON with keys: title (max 70 chars, curiosity-driven, no clickbait lies), description (2 sentences),
tags (array of 8 strings), narration (string), image_queries (array of 8 strings).

SOURCE:
{source}"""
    d = gemini_json(prompt)
    for k in ("title", "description", "tags", "narration", "image_queries"):
        if k not in d:
            raise RuntimeError(f"Output Gemini kurang field: {k}")
    return d


# ---------- suara ----------
async def _tts(text, voice, rate, mp3_path):
    import edge_tts
    try:
        com = edge_tts.Communicate(text, voice, rate=rate, boundary="WordBoundary")
    except TypeError:  # versi lama: default sudah WordBoundary
        com = edge_tts.Communicate(text, voice, rate=rate)
    words = []
    with open(mp3_path, "wb") as f:
        async for ch in com.stream():
            if ch["type"] == "audio":
                f.write(ch["data"])
            elif ch["type"] == "WordBoundary":
                s = ch["offset"] / 1e7
                words.append((s, s + ch["duration"] / 1e7, ch["text"]))
    return words


def audio_duration(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True).stdout.strip()
    return float(out)


# ---------- gambar ----------
def _license_ok(lic):
    l = lic.lower()
    if l.startswith("pd") or "public domain" in l or "cc0" in l:
        return True
    return ALLOW_CC_BY and l.startswith("cc by")


def _strip_html(s):
    return re.sub(r"<[^>]+>", "", s or "").strip()


def commons_search(query, limit=10):
    r = requests.get(
        "https://commons.wikimedia.org/w/api.php",
        params={"action": "query", "generator": "search", "gsrsearch": f"filetype:bitmap {query}",
                "gsrnamespace": 6, "gsrlimit": limit, "prop": "imageinfo",
                "iiprop": "url|size|mime|extmetadata", "iiurlwidth": 1600, "format": "json"},
        headers=UA, timeout=30)
    r.raise_for_status()
    pages = r.json().get("query", {}).get("pages", {})
    res = []
    for p in sorted(pages.values(), key=lambda x: x.get("index", 0)):
        ii = (p.get("imageinfo") or [{}])[0]
        meta = ii.get("extmetadata", {})
        lic = meta.get("LicenseShortName", {}).get("value", "")
        if ii.get("mime") not in ("image/jpeg", "image/png"):
            continue
        if ii.get("width", 0) < 700 or not _license_ok(lic):
            continue
        res.append({
            "url": ii.get("thumburl") or ii.get("url"),
            "title": p.get("title", ""),
            "license": lic,
            "artist": _strip_html(meta.get("Artist", {}).get("value", "")),
        })
    return res


def collect_images(queries, topic, need, workdir):
    seen, picked = set(), []
    for q in list(queries) + [topic, f"{topic} portrait", f"{topic} painting"]:
        if len(picked) >= need:
            break
        try:
            cands = commons_search(q)
        except Exception as e:
            log("search gagal:", q, e)
            continue
        time.sleep(1)
        for c in cands:
            if c["url"] in seen:
                continue
            seen.add(c["url"])
            ext = ".png" if c["url"].lower().endswith(".png") else ".jpg"
            path = workdir / f"img{len(picked):02d}{ext}"
            try:
                rr = requests.get(c["url"], headers=UA, timeout=60)
                rr.raise_for_status()
                path.write_bytes(rr.content)
            except Exception as e:
                log("download gagal:", c["url"], e)
                continue
            c["path"] = path
            picked.append(c)
            time.sleep(1)
            if len(picked) >= need:
                break
    return picked


# ---------- render ----------
def run(cmd, cwd=None):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("ffmpeg gagal:\n" + r.stderr[-1500:])


def make_clip(img, dur, idx, out):
    frames = int(dur * FPS) + 1
    step = 0.2 / frames
    if idx % 2 == 0:
        z = f"min(zoom+{step:.6f},1.2)"
    else:
        z = f"if(eq(on,0),1.2,max(zoom-{step:.6f},1.0))"
    vf = (f"scale=1620:2880:force_original_aspect_ratio=increase,crop=1620:2880,"
          f"zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s=1080x1920:fps={FPS},"
          f"format=yuv420p")
    run(["ffmpeg", "-y", "-i", str(img), "-vf", vf, "-frames:v", str(frames),
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", str(out)])


def _ass_time(t):
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def build_ass(words, narration, total, path):
    if not words:  # fallback: bagi rata
        toks = narration.split()
        step = total / max(len(toks), 1)
        words = [(i * step, (i + 1) * step, w) for i, w in enumerate(toks)]
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{FONT},84,&H00FFFFFF,&H000000FF,&H00000000,&H64000000,1,0,0,0,100,100,0,0,1,6,2,2,60,60,520,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    chunks = [words[i:i + 3] for i in range(0, len(words), 3)]
    lines = []
    for i, ch in enumerate(chunks):
        start = ch[0][0]
        end = chunks[i + 1][0][0] if i + 1 < len(chunks) else min(ch[-1][1] + 0.4, total)
        text = " ".join(w[2] for w in ch).replace("{", "(").replace("}", ")")
        lines.append(f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Default,,0,0,0,,{text}")
    Path(path).write_text(header + "\n".join(lines) + "\n", encoding="utf-8")


def render(images, words, narration, workdir, voice_mp3, dur):
    tail = 0.6
    total = dur + tail
    n = len(images)
    seg = total / n
    clips = []
    for i, im in enumerate(images):
        out = workdir / f"clip{i:02d}.mp4"
        make_clip(im["path"], seg, i, out)
        clips.append(out)
    (workdir / "list.txt").write_text("".join(f"file '{c.name}'\n" for c in clips))
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "list.txt", "-c", "copy", "silent.mp4"], cwd=workdir)
    build_ass(words, narration, dur, workdir / "subs.ass")

    music_dir = BASE / "music"
    tracks = [p for p in music_dir.glob("*") if p.suffix.lower() in (".mp3", ".m4a", ".wav")] if music_dir.exists() else []
    cmd = ["ffmpeg", "-y", "-i", "silent.mp4", "-i", voice_mp3.name]
    if tracks:
        cmd += ["-stream_loop", "-1", "-i", str(random.choice(tracks))]
        filt = ("[0:v]subtitles=subs.ass[v];[1:a]apad=pad_dur=0.6[vo];[2:a]volume=0.10[m];"
                "[vo][m]amix=inputs=2:duration=first:normalize=0[a]")
    else:
        filt = "[0:v]subtitles=subs.ass[v];[1:a]apad=pad_dur=0.6[a]"
    cmd += ["-filter_complex", filt, "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "veryfast",
            "-crf", "23", "-c:a", "aac", "-b:a", "160k", "-shortest", "-movflags", "+faststart", "video.mp4"]
    run(cmd, cwd=workdir)
    return workdir / "video.mp4"


# ---------- main ----------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--topic", help="paksa topik tertentu")
    ap.add_argument("--no-upload", action="store_true", help="render saja, jangan upload")
    ap.add_argument("--refill", action="store_true", help="tambah ~30 tokoh baru ke topics.txt lalu keluar")
    args = ap.parse_args()

    st = load_state()
    if args.refill:
        refill_topics(st)
        return
    topic = pick_topic(args.topic, st)
    workdir = OUT / slugify(topic)
    workdir.mkdir(parents=True, exist_ok=True)
    log("topik:", topic)

    source = wiki_source(topic)
    data = gen_script(topic, source)
    narration = re.sub(r"\s+", " ", data["narration"]).strip()
    log("kata:", len(narration.split()))

    voice_mp3 = workdir / "voice.mp3"
    rate = "+0%"
    for attempt in range(3):
        words = asyncio.run(_tts(narration, VOICE, rate, voice_mp3))
        dur = audio_duration(voice_mp3)
        log(f"durasi suara {dur:.1f}s (rate {rate})")
        if dur <= MAX_SECONDS:
            break
        rate = f"+{10 * (attempt + 1)}%"
    else:
        raise RuntimeError("Narasi terlalu panjang untuk Shorts, coba jalankan ulang")

    need = max(5, min(10, math.ceil(dur / 6)))
    images = collect_images(data["image_queries"], topic, need, workdir)
    if len(images) < 3:
        raise RuntimeError(f"Gambar bebas lisensi terlalu sedikit ({len(images)}). Coba topik lain / ALLOW_CC_BY=true")
    log("gambar:", len(images))

    video = render(images, words, narration, workdir, voice_mp3, dur)

    credits = sorted({f"{i['title'].replace('File:', '')} ({i['license']})"
                      + (f" - {i['artist']}" if i["artist"] else "") for i in images})
    desc = (data["description"].strip() + f"\n\nSource: Wikipedia - {topic}\nImages: Wikimedia Commons\n"
            + "\n".join(credits[:15]) + "\n\n#history #Shorts")
    meta = {"topic": topic, "title": data["title"][:100], "description": desc[:4900],
            "tags": data["tags"][:15], "narration": narration}
    (workdir / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False))
    log("video jadi:", video)

    if not args.no_upload:
        from upload import upload
        vid = upload(video, meta["title"], meta["description"], meta["tags"], PRIVACY)
        log("terupload:", f"https://youtube.com/shorts/{vid}")
    st["done"].append(topic)
    save_state(st)


if __name__ == "__main__":
    main()
