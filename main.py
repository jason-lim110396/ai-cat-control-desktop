# -*- coding: utf-8 -*-
"""
main.py - FastAPI Web  + WebSocket  + Telegram 
 Stop / Killed 
"""
import io
import asyncio
import json
from contextlib import asynccontextmanager
from typing import Set

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from config import settings, state
from cat_overlay import cat_hud
from controller import controller
from llm_agent import agent_runner

#  WebSocket 
active_connections: Set[WebSocket] = set()


# =========================================================================
# 1. Telegram Bot 
# =========================================================================
def check_auth(update: Update) -> bool:
    """ Telegram """
    user_id = update.effective_user.id if update.effective_user else None
    if settings.ALLOWED_CHAT_ID and user_id != settings.ALLOWED_CHAT_ID:
        return False
    return True


async def tg_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not check_auth(update):
        await update.message.reply_text("⛔ Unauthorized access!")
        return
    welcome_text = (
        "🐱 *NekoDesk Agent Ready!*\n\n"
        "Commands:\n"
        "• `/status` - View system status & current screenshot\n"
        "• `/keepalive` - Toggle anti-sleep mouse patrol\n"
        "• `/model [qwen|gemini]` - Switch AI model\n"
        "• `/stop` - 🛑 *Emergency Stop current task*\n"
        "• Send text message - Start desktop automation task!"
    )
    await update.message.reply_text(welcome_text, parse_mode="Markdown")


async def tg_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not check_auth(update):
        return
    await update.message.reply_text("📸 Taking screenshot and generating status report...")
    try:
        loop = asyncio.get_running_loop()
        img_bytes = await loop.run_in_executor(None, controller.capture_screen_bytes, 1280, 75)
        
        info = (
            f"🐱 *NekoDesk Status Overview*\n"
            f"• Active Model: `{state.active_model}`\n"
            f"• Keep-Alive: `{'Enabled' if state.keep_alive_enabled else 'Disabled'}`\n"
            f"• Agent State: `{'Running Task' if state.is_agent_busy else 'Idle / Standby'}`\n"
            f"• Current Action: {state.current_action_desc}"
        )
        await update.message.reply_photo(photo=io.BytesIO(img_bytes), caption=info, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ Failed to capture screenshot: {e}")


async def tg_keepalive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not check_auth(update):
        return
    is_on = await controller.toggle_keep_alive()
    status_str = "🟢 Anti-sleep mouse patrol enabled" if is_on else "⚪ Anti-sleep disabled"
    await update.message.reply_text(f"(=^-ω-^=) {status_str}")


async def tg_model(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not check_auth(update):
        return
    args = context.args
    if not args or args[0].lower() not in ("qwen", "gemini"):
        await update.message.reply_text(
            f"Current model: `{state.active_model}`\nUsage: `/model qwen` or `/model gemini`",
            parse_mode="Markdown"
        )
        return

    new_model = args[0].lower()
    state.active_model = new_model
    state.add_log(f"🔄 Model switched via Telegram: {new_model}")
    await update.message.reply_text(f"✨ Model switched to: *{new_model}*", parse_mode="Markdown")


async def tg_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Emergency Stop"""
    if not check_auth(update):
        return
    msg = state.request_stop()
    cat_hud.update_status("idle", "Stopped via Telegram")
    await update.message.reply_text(f"🛑 {msg}")


async def tg_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text instructions for automation"""
    if not check_auth(update):
        return

    user_text = update.message.text
    if not user_text:
        return

    if state.is_agent_busy:
        await update.message.reply_text("⚠️ A task is already in progress! Send `/stop` to abort.")
        return

    await update.message.reply_text(f"(=｀ω´=) Task received: `{user_text}`\nExecuting on desktop...", parse_mode="Markdown")

    task = asyncio.create_task(agent_runner.run_task(user_text))
    state.current_task = task

    try:
        result = await task
        await update.message.reply_text(f"🎉 Task Report:\n{result}")
        loop = asyncio.get_running_loop()
        img_bytes = await loop.run_in_executor(None, controller.capture_screen_bytes, 1280, 75)
        await update.message.reply_photo(photo=io.BytesIO(img_bytes), caption="Current screen state")
    except asyncio.CancelledError:
        await update.message.reply_text("🛑 Task forcefully cancelled!")
    except Exception as e:
        await update.message.reply_text(f"❌ Task error: {e}")


# =========================================================================
# 2.  (WebSocket)
# =========================================================================
async def screen_stream_broadcaster():
    """ FPS  Web  JPEG """
    interval = 1.0 / max(1, settings.STREAM_FPS)
    loop = asyncio.get_running_loop()

    while True:
        try:
            if active_connections:
                b64_img = await loop.run_in_executor(
                    None, controller.capture_screen_base64, 1280, settings.JPEG_QUALITY
                )

                #  (0.0~1.0)，
                cursor_pos = None
                try:
                    cur_x, cur_y = pyautogui.position()
                    sw, sh = controller.get_screen_size()
                    cursor_pos = {"x": cur_x / float(sw), "y": cur_y / float(sh)}
                except Exception:
                    pass

                payload = json.dumps({
                    "image": b64_img,
                    "current_desc": state.current_action_desc,
                    "keep_alive": state.keep_alive_enabled,
                    "is_busy": state.is_agent_busy,
                    "active_model": state.active_model,
                    "custom_models": state.custom_models,
                    "logs": state.logs[-20:],
                    "last_click": state.last_click,
                    "cursor": cursor_pos,
                    "notification": state.last_notification,
                    "pending_question": state.pending_question,
                })

                disconnected = []
                for ws in list(active_connections):
                    try:
                        await ws.send_text(payload)
                    except Exception:
                        disconnected.append(ws)

                for ws in disconnected:
                    active_connections.discard(ws)

            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            break
        except Exception as e:
            state.add_log(f"⚠️ : {e}")
            await asyncio.sleep(1.0)


# =========================================================================
# 3. FastAPI 
# =========================================================================
tg_app = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global tg_app
    state.add_log("🌟 NekoDesk is starting services...")
    
    # 1. Start Desktop Cat Overlay HUD
    cat_hud.start()
    cat_hud.update_status("idle", "Ready & idling")

    # 2. Start Telegram Bot if configured
    if settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_BOT_TOKEN != "YOUR_TELEGRAM_BOT_TOKEN_HERE":
        try:
            tg_app = Application.builder().token(settings.TELEGRAM_BOT_TOKEN).build()
            tg_app.add_handler(CommandHandler("start", tg_start))
            tg_app.add_handler(CommandHandler("status", tg_status))
            tg_app.add_handler(CommandHandler("keepalive", tg_keepalive))
            tg_app.add_handler(CommandHandler("model", tg_model))
            tg_app.add_handler(CommandHandler("stop", tg_stop))
            tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, tg_message_handler))

            await tg_app.initialize()
            await tg_app.start()
            await tg_app.updater.start_polling()
            state.add_log("🤖 Telegram Bot connected successfully!")
        except Exception as e:
            state.add_log(f"⚠️ Telegram Bot startup error (check token or proxy): {e}")

    # 3. Start background screen streaming
    stream_task = asyncio.create_task(screen_stream_broadcaster())

    yield

    # Shutdown
    state.add_log("🛑 Stopping all services...")
    stream_task.cancel()
    if tg_app:
        await tg_app.updater.stop()
        await tg_app.stop()
        await tg_app.shutdown()
    cat_hud.stop()


app = FastAPI(title="NekoDesk Agent", lifespan=lifespan)


@app.get("/", response_class=HTMLResponse)
async def get_index():
    with open("templates/index.html", "r", encoding="utf-8") as f:
        return f.read()


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    active_connections.add(websocket)
    state.add_log("🌐 New Web control client connected")

    try:
        while True:
            raw_data = await websocket.receive_text()
            data = json.loads(raw_data)
            msg_type = data.get("type")

            # 1. Emergency STOP / KILL
            if msg_type == "stop":
                res = state.request_stop()
                cat_hud.update_status("idle", "Stopped via Web interface")

            # 2. Click screen
            elif msg_type == "click":
                rx = float(data.get("ratio_x", 0.5))
                ry = float(data.get("ratio_y", 0.5))
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, controller.click_at_ratio, rx, ry)

            # 3. Toggle Keep-Alive
            elif msg_type == "toggle_keepalive":
                await controller.toggle_keep_alive()

            # 4. Change Model
            elif msg_type == "change_model":
                new_m = data.get("model", "qwen")
                state.active_model = new_m
                state.add_log(f"🔄 Model switched to: {new_m}")

            # 4.1 Save/Update Custom Model
            elif msg_type == "save_custom_model":
                from config import save_custom_models
                model_id = data.get("id")
                model_info = data.get("info", {})
                if model_id and model_info:
                    state.custom_models[model_id] = model_info
                    save_custom_models(state.custom_models)
                    state.add_log(f"💾 Custom model saved: {model_info.get('name', model_id)}")

            # 4.2 Delete Custom Model
            elif msg_type == "delete_custom_model":
                from config import save_custom_models
                model_id = data.get("id")
                if model_id in state.custom_models:
                    del state.custom_models[model_id]
                    save_custom_models(state.custom_models)
                    state.add_log(f"🗑️ Custom model deleted: {model_id}")

            # 5. User submitted question response
            elif msg_type == "submit_answer":
                ans = data.get("answer", "")
                state.submit_user_answer(ans)

            # 6. Execute task
            elif msg_type == "task":
                inst = data.get("instruction", "").strip()
                if inst:
                    if state.is_agent_busy:
                        state.add_log("⚠️ Task already running. Please Stop or wait until it finishes!")
                    else:
                        task = asyncio.create_task(agent_runner.run_task(inst))
                        state.current_task = task

    except WebSocketDisconnect:
        active_connections.discard(websocket)
        state.add_log("🌐 Web control client disconnected")
    except Exception as e:
        active_connections.discard(websocket)


if __name__ == "__main__":
    uvicorn.run(
        app,
        host=settings.WEB_HOST,
        port=settings.WEB_PORT,
    )
