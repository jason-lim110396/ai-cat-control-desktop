# -*- coding: utf-8 -*-
"""
config.py - NekoDesk Agent 全局配置与运行状态管理
"""
import os
import asyncio
from typing import Optional, List
from dotenv import load_dotenv

# 加载 .env 环境变量文件（如果存在）
load_dotenv()
import json

CUSTOM_MODELS_FILE = os.path.join(os.path.dirname(__file__), "custom_models.json")

def load_custom_models() -> dict:
    if os.path.exists(CUSTOM_MODELS_FILE):
        try:
            with open(CUSTOM_MODELS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    # 默认预设
    default_models = {
        "openrouter_r1": {
            "name": "OpenRouter: DeepSeek R1 (Free)",
            "base_url": "https://openrouter.ai/api/v1",
            "api_key": "",
            "model": "deepseek/deepseek-r1:free",
            "type": "openai"
        },
        "openrouter_gemini": {
            "name": "OpenRouter: Gemini 2.0 Flash (Free)",
            "base_url": "https://openrouter.ai/api/v1",
            "api_key": "",
            "model": "google/gemini-2.0-flash-exp:free",
            "type": "openai"
        },
        "groq_llama": {
            "name": "Groq: Llama 3.3 70B (Fast/Free)",
            "base_url": "https://api.groq.com/openai/v1",
            "api_key": "",
            "model": "llama-3.3-70b-versatile",
            "type": "openai"
        },
        "siliconflow": {
            "name": "SiliconFlow: Qwen 2.5 7B (Free)",
            "base_url": "https://api.siliconflow.cn/v1",
            "api_key": "",
            "model": "Qwen/Qwen2.5-7B-Instruct",
            "type": "openai"
        }
    }
    save_custom_models(default_models)
    return default_models

def save_custom_models(data: dict):
    try:
        with open(CUSTOM_MODELS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Error saving custom models: {e}")



class Config:
    # ================= 1. Gemini API Configuration =================
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")

    # ================= 2. LM Studio (Qwen 本地引擎) 配置 =================
    LM_STUDIO_BASE_URL: str = os.getenv("LM_STUDIO_BASE_URL", "http://localhost:1234/v1")
    LM_STUDIO_API_KEY: str = os.getenv("LM_STUDIO_API_KEY", "lm-studio")
    QWEN_MODEL_NAME: str = os.getenv("QWEN_MODEL_NAME", "qwen2.5-coder-7b-instruct")

    # ================= 3. Telegram Bot 配置 =================
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN_HERE")
    ALLOWED_CHAT_ID: int = int(os.getenv("ALLOWED_CHAT_ID", "123456789"))

    # ================= 4. Web 远程控制服务配置 =================
    WEB_HOST: str = os.getenv("WEB_HOST", "0.0.0.0")
    WEB_PORT: int = int(os.getenv("WEB_PORT", "8000"))
    STREAM_FPS: int = 2
    JPEG_QUALITY: int = 65

    # ================= 5. 自动化与防休眠参数 =================
    KEEP_ALIVE_INTERVAL: int = 30
    MAX_AGENT_STEPS: int = 15


class RuntimeState:
    """
    全局单例状态容器：供 Web、Telegram、Agent 与猫咪 HUD 实时共享状态
    """
    def __init__(self):
        # 当前激活的模型: "gemini" 或 "qwen"
        self.active_model: str = "qwen"
        self.custom_models: dict = load_custom_models()
        # 是否开启鼠标防休眠 (Keep-Online)
        self.keep_alive_enabled: bool = False
        # 当前是否有 AI 自动化任务正在执行
        self.is_agent_busy: bool = False
        # 紧急停止标志位
        self.stop_requested: bool = False
        # 当前正在执行的任务或动作描述
        self.current_action_desc: str = "Ready & idling"
        # 历史日志列表 (供 Web 前端展示)
        self.logs: List[str] = []
        # 当前运行任务的 asyncio.Task 引用
        self.current_task: Optional[asyncio.Task] = None
        # 猫咪最近一次点击的屏幕归一化位置 (用于前端雷达波纹与真实桌面光标显示)
        self.last_click: Optional[dict] = None
        # 最新通知事件 (供 Web 前端弹窗与声音播放，包含 id, type, title, message)
        self.last_notification: Optional[dict] = None
        # 待用户回答的问题交互状态 (用于当 LLM 不确定时向用户弹出输入框询问)
        self.pending_question: Optional[dict] = None
        self._user_answer_event: asyncio.Event = asyncio.Event()
        self.last_user_answer: str = ""

    def ask_user(self, question: str, default: str = "") -> str:
        """Register question for user interaction"""
        import time
        self.last_user_answer = ""
        self._user_answer_event.clear()
        self.pending_question = {
            "id": time.time(),
            "question": question,
            "default": default
        }
        self.add_log(f"❓ [Awaiting user input] {question}")

    def submit_user_answer(self, answer: str):
        """User submitted response"""
        self.last_user_answer = answer
        self.pending_question = None
        self._user_answer_event.set()
        self.add_log(f"💬 [User input confirmed] {answer}")

    async def wait_for_user_answer(self, timeout: float = 60.0) -> str:
        """Wait asynchronously for user response"""
        try:
            await asyncio.wait_for(self._user_answer_event.wait(), timeout=timeout)
            return self.last_user_answer
        except asyncio.TimeoutError:
            self.pending_question = None
            self.add_log("⌛ User input timed out, resuming execution")
            return ""

    def notify_user(self, event_type: str, title: str, message: str):
        """Record and dispatch notification"""
        import time
        self.last_notification = {
            "id": time.time(),
            "type": event_type,  # "completed" | "stopped" | "error"
            "title": title,
            "message": message
        }

    def add_log(self, message: str):
        """Append timestamped log"""
        import datetime
        import sys
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        log_entry = f"[{timestamp}] {message}"
        try:
            print(log_entry)
        except UnicodeEncodeError:
            print(log_entry.encode(sys.stdout.encoding or "utf-8", errors="replace").decode(sys.stdout.encoding or "utf-8"))
        self.logs.append(log_entry)
        if len(self.logs) > 100:
            self.logs.pop(0)

    def request_stop(self) -> str:
        """Unified emergency stop method"""
        self.stop_requested = True
        killed = False
        if self.current_task and not self.current_task.done():
            self.current_task.cancel()
            killed = True
        self.is_agent_busy = False
        self.current_action_desc = "Command aborted by user 🛑"
        self.add_log("🛑 Emergency stop received: current AI task and actions aborted!")
        self.notify_user("stopped", "🛑 Automation Stopped", "User manually stopped the task.")
        return "Stop signal sent" + (" and active task forcefully cancelled!" if killed else ".")


# 导出全局配置与状态实例
settings = Config()
state = RuntimeState()