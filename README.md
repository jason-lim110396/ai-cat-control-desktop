# 🐱 NekoDesk Agent - Desktop Automation & Keep-Alive Assistant

An intelligent desktop automation agent with an animated desktop cat HUD, remote web control, physical keep-alive mouse jiggler, and multi-model AI routing (LM Studio / Gemini / OpenRouter / Groq).

![Python](https://img.shields.io/badge/Python-3.10%2B-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115-green)
![License](https://img.shields.io/badge/License-MIT-purple)

---

## ✨ Features

- 🐾 **Animated Desktop Cat HUD**:
  - Live pixel-art cat sprite animations that reflect system status:
    - **Idle**: Sitting, blinking, and cozy sleeping frames.
    - **Patrol / Keep-Alive**: Active walking cat stroll.
    - **Running / Working**: High-energy typing & running paws with sparks.
    - **Done**: Happy jumping celebration.
  - One-click **🛑 STOP** emergency button directly on desktop.
  - One-click **☕ Keep: ON / OFF** toggle button.
- ☕ **MoveMyCursor-Style Keep-Alive**:
  - Smooth circular mouse jiggler.
  - Automatically avoids locking or sleeping via native Windows power state assertions (`SetThreadExecutionState`).
  - **Polite Human Detection**: Instantly pauses for 10 seconds whenever you move or click the physical mouse.
- 🌐 **Web Control Interface**:
  - Real-time desktop streaming with live cursor position and ripple feedback.
  - Task input console and live logs.
  - Endpoint & model configuration manager.
- 🤖 **Dual & Custom AI Engines**:
  - Local LM Studio (Qwen 2.5 Coder 7B).
  - Google Gemini 3.5 Flash (Vision).
  - Custom endpoints (OpenRouter DeepSeek R1, Groq Llama 3.3 70B, SiliconFlow, etc.).
- 📱 **Telegram Bot Integration (Optional)**:
  - Command `/status` with instant screenshots.
  - Direct execution from Telegram chats.

---

## 🚀 Quick Start

### 1. Clone & Install Dependencies
```bash
git clone https://github.com/jason-lim110396/nekodesk-agent.git
cd nekodesk-agent

pip install -r requirements.txt
```

### 2. Configure Environment
Copy `.env.example` to `.env` and fill in your keys (all optional if using LM Studio locally):
```bash
cp .env.example .env
```

### 3. Run the Application
```bash
python main.py
```
- Open your browser at: `http://localhost:8000`
- The animated cat HUD will appear on your desktop.

---

## 📂 Project Structure

```text
├── cat_frames/          # Pre-sliced animated cat sprite frames
├── templates/
│   └── index.html       # Web remote control console & live stream
├── cat_overlay.py       # Desktop Tkinter overlay HUD with sprite animations
├── config.py            # Global runtime state and settings
├── controller.py        # Mouse, keyboard, and keep-alive jiggler engine
├── custom_models.json   # Custom model presets
├── llm_agent.py         # AI planner and task runner
├── main.py              # FastAPI server, WebSocket, and Telegram entry point
├── oneko.gif            # Base cat spritesheet
├── requirements.txt     # Python dependencies
├── .env.example         # Environment template
└── .gitignore           # Git ignore list (protects .env and secrets)
```

---

## 🔒 Security & Privacy

- All sensitive keys (`.env`) are gitignored by default.
- Automation actions can be aborted at any moment via the physical mouse fail-safe (moving to corner) or clicking **STOP** on the HUD / Web UI.

---

## 📄 License
MIT License.
