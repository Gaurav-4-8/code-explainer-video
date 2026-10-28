#!/usr/bin/env python3
"""
code_explainer_video.py
Turn a text file containing a CODE SNIPPET + your VOICEOVER TEXT into a video:
syntax-highlighted code in the background, English AI voiceover, captions, background music,
and (optionally) an upload to YouTube.  Everything used is free.

SCRIPT FILE FORMAT  (put it in the scripts/ folder, e.g. scripts/walrus.txt)

  TITLE: Stop Calculating Twice in Python
  HEADLINE: The Walrus Operator  :=          <- optional big text shown above the code
  TAGS: python, walrus operator, python tips
  LANG: python                                <- optional, language for colouring (default python)
  FILE: main.py                               <- optional, name shown on the code window
  === CODE ===
  # The Old Way
  data = fetch_api()
  ...
  === VOICE ===
  Stop calculating the same value twice in Python.
  [lines 1-4] Look at this standard code: we fetch data, ...
  [lines 6-8] Instead, use Python's Walrus operator ...
  [all] Cleaner code, zero duplicate calculations.

  HIGHLIGHT MARKERS (optional): start a line in the VOICE part with [lines 2-4] or [lines 1,3,7-9]
  and from that moment the narration highlights those code lines (the rest is dimmed).
  The highlight stays until the next marker. [all] shows the whole code normally again.
  Backticks around words in the voice text (like `n`) are removed before speaking.

SETUP
  1. Install ffmpeg (ffmpeg and ffprobe must work in your terminal).
  2. pip install --upgrade edge-tts pillow pygments google-api-python-client google-auth-oauthlib
  3. Music: put a few royalty-free tracks in the music/ folder (YouTube Audio Library is free).
  4. Fonts: Windows/Mac/Linux system fonts are found automatically (Consolas, Menlo, DejaVu...).
  5. For upload: Google Cloud -> enable YouTube Data API v3 -> OAuth client (Desktop app) ->
     save as client_secret.json next to this script.

USAGE
  python code_explainer_video.py --no-upload              # make a vertical Short, test first
  python code_explainer_video.py                          # make + upload (private)
  python code_explainer_video.py --format long            # 16:9 video
  python code_explainer_video.py --voice en-IN-PrabhatNeural --privacy public
  Options: --file path.txt  --count N  --no-music  --no-captions  --keep-script
"""

import argparse
import math
import random
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

# ----------------------------- CONFIG -----------------------------------------
BASE = Path(__file__).resolve().parent
SCRIPTS_DIR = BASE / "scripts"
MUSIC_DIR = BASE / "music"
FONTS_DIR = BASE / "fonts"
OUT_DIR = BASE / "output"
CLIENT_SECRET = BASE / "client_secret.json"
TOKEN_FILE = BASE / "token.json"

VOICE = "en-US-GuyNeural"          # Indian English: en-IN-PrabhatNeural / en-IN-NeerjaNeural
VOICE_RATE = "+0%"
MUSIC_VOLUME = 0.10
FPS = 30
SCENE_GAP = 0.25                   # seconds of pause after each scene
TAIL_SECONDS = 1.5                 # music-only ending
CATEGORY_ID = "27"                 # Education
DEFAULT_TAGS = ["python", "coding", "programming", "learn python", "python tips"]

FORMATS = {
    "short": dict(w=1080, h=1920, words=5, cap_font=62, cap_y=0.79, head_font=74, head_y=0.05,
                  card_top=0.20, card_bot=0.67, code_max=54, chrome=74, scene_chars=200),
    "long": dict(w=1920, h=1080, words=8, cap_font=50, cap_y=0.89, head_font=56, head_y=0.035,
                 card_top=0.14, card_bot=0.79, code_max=44, chrome=64, scene_chars=260),
}

MONO_FONTS = [
    *sorted(FONTS_DIR.glob("*[Mm]ono*.ttf")), *sorted(FONTS_DIR.glob("*[Cc]ode*.ttf")),
    "C:/Windows/Fonts/consola.ttf", "C:/Windows/Fonts/cour.ttf",
    "/System/Library/Fonts/Menlo.ttc", "/System/Library/Fonts/Monaco.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
]
UI_FONTS = [
    "C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/segoeuib.ttf", "C:/Windows/Fonts/calibrib.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf", "/Library/Fonts/Arial Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    *[p for p in sorted(FONTS_DIR.glob("*.ttf")) if "mono" not in p.name.lower()],
]

SENT_END = ".!?"
SOFT_END = ",;:\u2014"


# ----------------------------- FONTS ------------------------------------------
_cache = {}
_working = {}


def _load(kind, paths, size):
    key = (kind, size)
    if key in _cache:
        return _cache[key]
    order = ([_working[kind]] if kind in _working else []) + [p for p in paths if p != _working.get(kind)]
    for p in order:
        if not Path(p).exists():
            continue
        try:
            f = ImageFont.truetype(str(p), size)
            f.getlength("Ag")                      # make sure the font really works
        except Exception as e:
            print(f"    (skipping unusable font {Path(p).name}: {e})")
            continue
        _working[kind] = p
        _cache[key] = f
        return f
    sys.exit(f"No usable {kind} font found. Put a normal .ttf font (e.g. Consolas, Arial or "
             f"DejaVu Sans Mono) into the fonts/ folder.")


def mono_font(size):
    return _load("monospace", MONO_FONTS, size)


def ui_font(size):
    return _load("bold", UI_FONTS, size)


# ----------------------------- SCRIPT PARSING ---------------------------------
MARKER_RE = re.compile(r"^\[\s*(?:lines?\s*)?(all|none|[\d,\s\-\u2013]+)\s*\]\s*(.*)$", re.I)


def parse_marker(line):
    """Returns (found, active_line_set_or_None, remaining_text)."""
    m = MARKER_RE.match(line)
    if not m:
        return False, None, line
    spec = m.group(1).strip().lower()
    if spec in ("all", "none"):
        return True, None, m.group(2)
    active = set()
    for part in spec.replace("\u2013", "-").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            if a.strip().isdigit() and b.strip().isdigit():
                active.update(range(int(a), int(b) + 1))
        elif part.isdigit():
            active.add(int(part))
    return True, (active or None), m.group(2)


def parse_script(path: Path):
    raw = path.read_text(encoding="utf-8-sig")
    meta = {"title": None, "headline": None, "tags": [], "lang": "python", "file": "main.py"}
    section, code_lines, voice_lines = None, [], []
    for line in raw.splitlines():
        m = re.match(r"^\s*={2,}\s*(CODE|VOICE|VOICEOVER|SCRIPT)\s*={2,}\s*$", line, re.I)
        if m:
            section = "code" if m.group(1).upper() == "CODE" else "voice"
            continue
        if section is None:
            s = line.strip()
            u = s.upper()
            if u.startswith("TITLE:"):
                meta["title"] = s[6:].strip()
            elif u.startswith("HEADLINE:"):
                meta["headline"] = s[9:].strip()
            elif u.startswith("TAGS:"):
                meta["tags"] = [t.strip() for t in s[5:].split(",") if t.strip()]
            elif u.startswith("LANG:"):
                meta["lang"] = s[5:].strip().lower() or "python"
            elif u.startswith("FILE:"):
                meta["file"] = s[5:].strip() or "main.py"
        elif section == "code":
            if not line.strip().startswith("```"):
                code_lines.append(line.expandtabs(4).rstrip())
        else:
            voice_lines.append(line)

    while code_lines and not code_lines[0].strip():
        code_lines.pop(0)
    while code_lines and not code_lines[-1].strip():
        code_lines.pop()
    if not code_lines:
        raise RuntimeError("No code found. Put your code after a line that says === CODE ===")

    # voice -> list of (text, active_lines)
    units, current, buf = [], None, []

    def flush():
        text = " ".join(buf).strip()
        if text:
            units.append((text, current))
        buf.clear()

    for line in voice_lines:
        s = line.strip()
        if not s:
            flush()
            continue
        found, act, rest = parse_marker(s)
        if found:
            flush()
            current = act
            s = rest.strip()
        if s:
            buf.append(s)
    flush()
    if not units:
        raise RuntimeError("No voiceover text found. Put it after a line that says === VOICE ===")

    clean = []
    for text, act in units:
        text = text.replace("`", "").replace("**", "").replace("*", "")
        clean.append((re.sub(r"\s+", " ", text).strip(), act))
    if not meta["title"]:
        meta["title"] = " ".join(clean[0][0].split()[:8]).rstrip(".,;:!?")
    return meta, code_lines, clean


def find_scripts():
    return sorted(SCRIPTS_DIR.glob("*.txt"))


def split_sentences(text):
    return [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]


def build_scenes(units, fmt, n_lines):
    """Group consecutive sentences that share the same highlight into scenes."""
    sents = []
    for text, act in units:
        if act:
            act = {a for a in act if 1 <= a <= n_lines} or None
        for s in split_sentences(text):
            sents.append((s, frozenset(act) if act else None))
    scenes, cur_text, cur_act = [], "", None
    for s, act in sents:
        if cur_text and (act != cur_act or len(cur_text) + len(s) > fmt["scene_chars"]):
            scenes.append({"text": cur_text, "active": cur_act})
            cur_text = ""
        cur_text = (cur_text + " " + s).strip()
        cur_act = act
    if cur_text:
        scenes.append({"text": cur_text, "active": cur_act})
    return scenes


# ----------------------------- CODE RENDERING ---------------------------------
def theme():
    from pygments.token import Comment, Keyword, Name, Number, Operator, Punctuation, String, Token
    return {
        Token: (248, 248, 242), Comment: (118, 135, 170), Keyword: (255, 121, 198),
        Keyword.Constant: (189, 147, 249), Name.Builtin: (139, 233, 253),
        Name.Function: (80, 250, 123), Name.Class: (80, 250, 123), Name.Decorator: (80, 250, 123),
        Number: (189, 147, 249), Operator: (255, 121, 198), Operator.Word: (255, 121, 198),
        String: (241, 250, 140), String.Interpol: (139, 233, 253), String.Escape: (255, 184, 108),
        Punctuation: (248, 248, 242), Name: (248, 248, 242),
    }


def tokenize_lines(code_lines, lang):
    try:
        from pygments import lex
        from pygments.lexers import get_lexer_by_name
        from pygments.util import ClassNotFound
    except ImportError:
        sys.exit("Missing package: pip install pygments")
    try:
        lexer = get_lexer_by_name(lang)
    except ClassNotFound:
        lexer = get_lexer_by_name("text")
    colors = theme()
    from pygments.token import Token
    default = colors[Token]

    def color_for(tt):
        while tt is not None:
            if tt in colors:
                return colors[tt]
            tt = tt.parent
        return default

    lines = [[]]
    for tt, val in lex("\n".join(code_lines), lexer):
        col = color_for(tt)
        for k, part in enumerate(val.split("\n")):
            if k > 0:
                lines.append([])
            if part:
                lines[-1].append((part, col))
    while len(lines) > len(code_lines):
        lines.pop()
    while len(lines) < len(code_lines):
        lines.append([])
    return lines


def mix(c1, c2, t):
    return tuple(int(a * (1 - t) + b * t) for a, b in zip(c1, c2))


def wrap(draw, text, font, max_w):
    lines, cur = [], ""
    for word in text.split():
        trial = (cur + " " + word).strip()
        if draw.textlength(trial, font=font) <= max_w:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


CARD = (30, 32, 54)
CHROME = (22, 23, 40)


def render_code_frame(tok_lines, active, fmt, headline, filename):
    W, H = fmt["w"], fmt["h"]
    grad = Image.linear_gradient("L").resize((W, H))
    img = ImageOps.colorize(grad, black=(12, 13, 24), white=(36, 30, 66)).convert("RGB")
    draw = ImageDraw.Draw(img)

    if headline:
        hf = ui_font(fmt["head_font"])
        y = int(H * fmt["head_y"])
        for l in wrap(draw, headline, hf, int(W * 0.88)):
            x = (W - draw.textlength(l, font=hf)) // 2
            draw.text((x, y), l, font=hf, fill=(255, 255, 255), stroke_width=3, stroke_fill=(0, 0, 0))
            y += int(fmt["head_font"] * 1.25)

    x0, x1 = int(W * 0.05), int(W * 0.95)
    zt, zb = int(H * fmt["card_top"]), int(H * fmt["card_bot"])
    pad, chrome = int(W * 0.025), fmt["chrome"]
    plain = ["".join(t for t, _ in ln) for ln in tok_lines]
    n, maxcols = len(plain), max(1, max(len(p) for p in plain))
    avail_w = (x1 - x0) - 2 * pad
    avail_h = (zb - zt) - chrome - 2 * pad

    size = 18
    for size in range(fmt["code_max"], 17, -2):
        mf = mono_font(size)
        cw = mf.getlength("M")
        if maxcols * cw + cw * 3.4 <= avail_w and n * int(size * 1.55) <= avail_h:
            break
    mf = mono_font(size)
    cw = mf.getlength("M")
    gutter = cw * 3.4
    line_h = int(size * 1.55)

    card_h = chrome + 2 * pad + n * line_h
    y0 = zt + max(0, ((zb - zt) - card_h) // 2)
    draw.rounded_rectangle([x0 + 10, y0 + 14, x1 + 10, y0 + card_h + 14], radius=30, fill=(6, 6, 14))  # shadow
    draw.rounded_rectangle([x0, y0, x1, y0 + card_h], radius=30, fill=CARD)
    draw.rounded_rectangle([x0, y0, x1, y0 + chrome], radius=30, fill=CHROME, corners=(True, True, False, False))

    r = chrome * 0.17
    for k, col in enumerate([(255, 95, 86), (255, 189, 46), (39, 201, 63)]):
        cx, cy = x0 + pad + r + k * r * 3.3, y0 + chrome / 2
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=col)
    ff = ui_font(int(chrome * 0.38))
    draw.text(((x0 + x1) / 2, y0 + chrome / 2), filename, font=ff, fill=(150, 156, 190), anchor="mm")

    body_top = y0 + chrome + pad
    dim_target = CARD
    for i, toks in enumerate(tok_lines):
        ly = body_top + i * line_h
        lit = active is None or (i + 1) in active
        if active is not None and lit:
            draw.rounded_rectangle([x0 + 12, ly, x1 - 12, ly + line_h], radius=10, fill=(52, 58, 104))
            draw.rectangle([x0 + 12, ly + 4, x0 + 18, ly + line_h - 4], fill=(139, 233, 253))
        mid = ly + line_h / 2
        draw.text((x0 + pad + gutter - cw * 1.0, mid), str(i + 1), font=mf, anchor="rm",
                  fill=(120, 128, 165) if lit else mix((120, 128, 165), dim_target, 0.55))
        x = x0 + pad + gutter
        for text, col in toks:
            c = col if lit else mix(col, dim_target, 0.62)
            draw.text((x, mid), text, font=mf, fill=c, anchor="lm")
            x += mf.getlength(text)
    return img


def render_caption(text, fmt, path: Path):
    W, H = fmt["w"], fmt["h"]
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    font = ui_font(fmt["cap_font"])
    lines = wrap(draw, text, font, int(W * 0.84))
    line_h = int(fmt["cap_font"] * 1.4)
    block_h = line_h * len(lines)
    y0 = int(H * fmt["cap_y"]) - block_h // 2
    widest = max(draw.textlength(l, font=font) for l in lines)
    pad = 26
    draw.rounded_rectangle([(W - widest) // 2 - pad, y0 - pad // 2, (W + widest) // 2 + pad,
                            y0 + block_h + pad // 2], radius=24, fill=(0, 0, 0, 165))
    y = y0
    for l in lines:
        x = (W - draw.textlength(l, font=font)) // 2
        draw.text((x, y), l, font=font, fill=(255, 255, 255, 255), stroke_width=2, stroke_fill=(0, 0, 0, 255))
        y += line_h
    img.save(path)


def split_chunks(text, words_per_chunk):
    chunks, cur = [], []
    for w in text.split():
        cur.append(w)
        if len(cur) >= words_per_chunk or (len(cur) >= 3 and w[-1] in SENT_END + SOFT_END):
            chunks.append(" ".join(cur))
            cur = []
    if cur:
        if chunks and len(cur) < 2:
            chunks[-1] += " " + " ".join(cur)
        else:
            chunks.append(" ".join(cur))
    return chunks


def chunk_spans(chunks, total):
    weights = [len(c) + (8 if c[-1] in SENT_END else 3 if c[-1] in SOFT_END else 0) for c in chunks]
    s, t, spans = sum(weights), 0.0, []
    for w in weights:
        d = total * w / s
        spans.append((t, t + d))
        t += d
    return spans


# ----------------------------- AUDIO / FFMPEG ---------------------------------
async def _tts(text, out_path, voice):
    import edge_tts
    await edge_tts.Communicate(text, voice, rate=VOICE_RATE).save(str(out_path))


def make_voice(text, out_path: Path, voice):
    import asyncio
    for attempt in range(3):
        try:
            asyncio.run(_tts(text, out_path, voice))
            if out_path.exists() and out_path.stat().st_size > 0:
                return
        except Exception as e:
            if attempt == 2:
                raise RuntimeError(f"Text-to-speech failed: {e}")
            time.sleep(2)


def audio_duration(path: Path) -> float:
    out = subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                   "-of", "csv=p=0", str(path)])
    return float(out.decode().strip())


def run(cmd):
    r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    if r.returncode != 0:
        raise RuntimeError("ffmpeg failed:\n" + r.stderr.decode(errors="ignore")[-1500:])


def pick_music():
    files = [p for p in MUSIC_DIR.glob("*") if p.suffix.lower() in (".mp3", ".wav", ".m4a", ".ogg")]
    return random.choice(files) if files else None


def render_scene_video(idx, frame_png, frames, cap_pngs, spans, work: Path, fade_in, fade_out):
    D = frames / FPS
    seg = work / f"seg_{idx:03d}.mp4"
    cmd = ["ffmpeg", "-y", "-loop", "1", "-framerate", str(FPS), "-i", str(frame_png)]
    graph, cur = ["[0:v]format=yuv420p[b0]"], "b0"
    for j, (png, (a, b)) in enumerate(zip(cap_pngs, spans)):
        cmd += ["-i", str(png)]
        graph.append(f"[{cur}][{j + 1}:v]overlay=0:0:enable='between(t,{a:.3f},{b:.3f})'[b{j + 1}]")
        cur = f"b{j + 1}"
    tail = []
    if fade_in:
        tail.append("fade=t=in:st=0:d=0.4")
    if fade_out:
        tail.append(f"fade=t=out:st={max(0, D - 0.8):.3f}:d=0.8")
    tail.append("format=yuv420p")
    graph.append(f"[{cur}]{','.join(tail)}[vout]")
    cmd += ["-filter_complex", ";".join(graph), "-map", "[vout]", "-frames:v", str(frames),
            "-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-an", str(seg)]
    run(cmd)
    return seg


# ----------------------------- BUILD ------------------------------------------
def build_video(meta, code_lines, scenes, fmt, voice, use_music, use_captions, work: Path,
                final_path: Path, thumb_path: Path):
    work.mkdir(parents=True, exist_ok=True)
    tok_lines = tokenize_lines(code_lines, meta["lang"])
    headline = meta["headline"] if meta["headline"] is not None else meta["title"]
    frames_cache = {}

    def frame_for(active):
        if active not in frames_cache:
            p = work / f"code_{len(frames_cache):02d}.png"
            render_code_frame(tok_lines, set(active) if active else None, fmt, headline,
                              meta["file"]).save(p)
            frames_cache[active] = p
        return frames_cache[active]

    # thumbnail = whole code, nothing dimmed
    render_code_frame(tok_lines, None, fmt, headline, meta["file"]).save(thumb_path)

    segs, wavs, total = [], [], 0.0
    for i, sc in enumerate(scenes):
        print(f"    scene {i + 1}/{len(scenes)}")
        mp3 = work / f"voice_{i:03d}.mp3"
        make_voice(sc["text"], mp3, voice)
        dur = audio_duration(mp3)
        is_last = i == len(scenes) - 1
        frames = math.ceil((dur + SCENE_GAP + (TAIL_SECONDS if is_last else 0)) * FPS)
        D = frames / FPS
        wav = work / f"voice_{i:03d}.wav"
        run(["ffmpeg", "-y", "-i", str(mp3), "-af", f"apad=whole_dur={D:.4f}", "-t", f"{D:.4f}",
             "-ar", "44100", "-ac", "2", str(wav)])
        wavs.append(wav)
        total += D

        caps, spans = [], []
        if use_captions:
            chunks = split_chunks(sc["text"], fmt["words"])
            spans = chunk_spans(chunks, dur)
            for j, ch in enumerate(chunks):
                p = work / f"cap_{i:03d}_{j:02d}.png"
                render_caption(ch, fmt, p)
                caps.append(p)
        segs.append(render_scene_video(i, frame_for(sc["active"]), frames, caps, spans, work,
                                       i == 0, is_last))

    vlist, alist = work / "video_list.txt", work / "audio_list.txt"
    vlist.write_text("".join(f"file '{s.as_posix()}'\n" for s in segs))
    alist.write_text("".join(f"file '{w.as_posix()}'\n" for w in wavs))
    voice_wav = work / "voice_all.wav"
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(alist), "-c", "copy", str(voice_wav)])

    music = pick_music() if use_music else None
    if use_music and not music:
        print("    (no music files in music/ - continuing without background music)")
    cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(vlist), "-i", str(voice_wav)]
    if music:
        cmd += ["-stream_loop", "-1", "-i", str(music)]
        fs = max(0.0, total - 2.5)
        flt = (f"[2:a]volume={MUSIC_VOLUME},afade=t=in:st=0:d=1.5,afade=t=out:st={fs:.2f}:d=2.5[a1];"
               f"[1:a][a1]amix=inputs=2:duration=first:dropout_transition=0,volume=2[aout]")
    else:
        flt = "[1:a]anull[aout]"
    cmd += ["-filter_complex", flt, "-map", "0:v", "-map", "[aout]", "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k", "-t", f"{total:.3f}", "-movflags", "+faststart",
            str(final_path)]
    run(cmd)
    return total


# ----------------------------- YOUTUBE ----------------------------------------
def youtube_client():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    scopes = ["https://www.googleapis.com/auth/youtube.upload"]
    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), scopes)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not CLIENT_SECRET.exists():
                sys.exit("client_secret.json not found. See the SETUP notes at the top of the script.")
            creds = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET), scopes) \
                .run_local_server(port=0)
        TOKEN_FILE.write_text(creds.to_json())
    return build("youtube", "v3", credentials=creds)


def yt_safe(s):
    """YouTube rejects < and > in titles/descriptions."""
    return s.replace("<", "\uff1c").replace(">", "\uff1e")


def trunc_bytes(s, limit):
    return s.encode("utf-8")[:limit].decode("utf-8", errors="ignore")


def upload(video_path: Path, title, description, tags, privacy):
    from googleapiclient.http import MediaFileUpload

    yt = youtube_client()
    body = {
        "snippet": {"title": yt_safe(title)[:100], "description": trunc_bytes(yt_safe(description), 4500),
                    "tags": tags[:15], "categoryId": CATEGORY_ID,
                    "defaultLanguage": "en", "defaultAudioLanguage": "en"},
        "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False},
    }
    media = MediaFileUpload(str(video_path), chunksize=-1, resumable=True, mimetype="video/mp4")
    req = yt.videos().insert(part="snippet,status", body=body, media_body=media)
    resp = None
    while resp is None:
        _, resp = req.next_chunk()
    print(f"    Uploaded: https://www.youtube.com/watch?v={resp['id']}  ({privacy})")


# ----------------------------- MAIN -------------------------------------------
def process(script: Path, args, fmt):
    meta, code_lines, units = parse_script(script)
    mono_font(30), ui_font(30)
    print(f"    fonts: code={Path(_working['monospace']).name}, text={Path(_working['bold']).name}")
    scenes = build_scenes(units, fmt, len(code_lines))
    print(f"    {len(code_lines)} code lines, {len(scenes)} scenes")

    stamp = time.strftime("%Y%m%d_%H%M%S")
    final = OUT_DIR / f"{script.stem}_{stamp}.mp4"
    thumb = OUT_DIR / f"{script.stem}_{stamp}_thumb.png"
    work = OUT_DIR / f"work_{script.stem}_{stamp}"
    try:
        total = build_video(meta, code_lines, scenes, fmt, args.voice, not args.no_music,
                            not args.no_captions, work, final, thumb)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print(f"    Video saved: {final}  ({total:.0f}s)")

    if not args.no_upload:
        suffix = " #Shorts" if args.format == "short" else ""
        title = meta["title"][:100 - len(suffix)].rstrip() + suffix
        voice_text = " ".join(t for t, _ in units)
        tags_line = ("#Shorts " if args.format == "short" else "") + "#python #coding #programming"
        description = (f"{voice_text}\n\nCode:\n" + "\n".join(code_lines) + f"\n\n{tags_line}")
        upload(final, title, description, meta["tags"] + DEFAULT_TAGS, args.privacy)

    if not args.keep_script:
        done = SCRIPTS_DIR / "done"
        done.mkdir(exist_ok=True)
        shutil.move(str(script), str(done / script.name))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", help="process this specific script file")
    ap.add_argument("--count", type=int, default=1, help="how many scripts to process")
    ap.add_argument("--format", choices=["short", "long"], default="short")
    ap.add_argument("--voice", default=VOICE)
    ap.add_argument("--no-upload", action="store_true")
    ap.add_argument("--no-music", action="store_true")
    ap.add_argument("--no-captions", action="store_true")
    ap.add_argument("--debug", action="store_true", help="show full error details")
    ap.add_argument("--keep-script", action="store_true", help="do not move the script to scripts/done/")
    ap.add_argument("--privacy", default="private", choices=["private", "unlisted", "public"])
    args = ap.parse_args()

    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            sys.exit(f"{tool} not found. Install ffmpeg first.")
    for d in (SCRIPTS_DIR, MUSIC_DIR, FONTS_DIR, OUT_DIR):
        d.mkdir(exist_ok=True)

    scripts = [Path(args.file)] if args.file else find_scripts()[:args.count]
    if not scripts:
        sys.exit(f"No .txt scripts found in {SCRIPTS_DIR}.")

    fmt = FORMATS[args.format]
    for n, sc in enumerate(scripts, 1):
        print(f"[{n}/{len(scripts)}] {sc.name}")
        try:
            process(sc, args, fmt)
        except Exception as e:
            print(f"    FAILED: {e}")
            if args.debug:
                import traceback
                traceback.print_exc()


if __name__ == "__main__":
    main()