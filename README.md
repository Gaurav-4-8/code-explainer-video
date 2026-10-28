# Code Explainer Video Generator

An automated Python pipeline that converts code snippets and narration text into syntax-highlighted, animated coding videos (Vertical Shorts or 16:9 Long-form) with AI voiceovers, timed captions, dynamic line-dimming, and optional direct YouTube uploads.

---

## Features

- **Automated Syntax Highlighting:** Pygments-powered IDE card with line numbers and custom window chrome.
- **Dynamic Line Highlighting:** Dim or focus specific lines of code synced to the voiceover cues (`[lines 2-4]`, `[all]`).
- **Neural Voiceover:** Free high-quality text-to-speech powered by `edge-tts` (supports multi-language accents).
- **Auto-Captions:** Smart pacing and sentence-weighted subtitle overlays.
- **Background Music:** Automatic looping, volume ducking, and fade-in/fade-out muxing.
- **YouTube API Integration:** Optional direct upload with automatic title, description, and tag population.

---

## Prerequisites

1. **Python 3.9+**
2. **FFmpeg & FFprobe:** Must be installed and accessible in your system `PATH`.
   - **Windows:** `winget install Gyan.FFmpeg`
   - **macOS:** `brew install ffmpeg`
   - **Linux (Ubuntu/Debian):** `sudo apt update && sudo apt install ffmpeg`

---

## Installation

1. Clone the repository:
   ```bash
   git clone [https://github.com/your-username/code-explainer-video.git](https://github.com/your-username/code-explainer-video.git)
   cd code-explainer-video
