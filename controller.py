# -*- coding: utf-8 -*-
"""
controller.py - 、 Keep-Online 
"""
import io
import math
import time
import base64
import platform
import asyncio
import subprocess
from typing import Tuple, Dict, Any

import mss
import pyautogui
import pyperclip
from PIL import Image

from config import settings, state
from cat_overlay import cat_hud

# ： PyAutoGUI ！ (0,0)  FailSafeException
pyautogui.FAILSAFE = True
#  PyAutoGUI 
pyautogui.PAUSE = 0.15


class ComputerController:
    def __init__(self):
        self.os_name = platform.system()  # 'Windows', 'Darwin' (macOS), 'Linux'
        self._keep_alive_task: asyncio.Task | None = None

    # =====================================================================
    # 1. 
    # =====================================================================
    def get_screen_size(self) -> Tuple[int, int]:
        """ ( PyAutoGUI )"""
        width, height = pyautogui.size()
        return width, height

    def capture_screen_bytes(self, max_width: int = 1280, quality: int = 75) -> bytes:
        """
         mss ， JPEG 。
         Windows  (SetThreadDesktop)， BitBlt Access Denied 。
        """
        # Windows  (InputDesktop)
        if self.os_name == "Windows":
            try:
                import ctypes
                user32 = ctypes.windll.user32
                hdesk = user32.OpenInputDesktop(0, False, 0x01FF)
                if hdesk:
                    user32.SetThreadDesktop(hdesk)
                    user32.CloseDesktop(hdesk)
            except Exception:
                pass

        try:
            with mss.mss() as sct:
                monitor = sct.monitors[1]
                sct_img = sct.grab(monitor)
                img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")

                if img.width > max_width:
                    ratio = max_width / float(img.width)
                    new_height = int(float(img.height) * ratio)
                    img = img.resize((max_width, new_height), Image.Resampling.LANCZOS)

                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=quality)
                return buf.getvalue()
        except Exception as e:
            from PIL import ImageDraw
            img = Image.new("RGB", (960, 540), color=(30, 30, 46))
            draw = ImageDraw.Draw(img)
            draw.text((360, 240), "(=^･ω･^=) NekoDesk Desktop Stream", fill=(245, 194, 231))
            draw.text((330, 270), f"Screen captured paused: {e}", fill=(166, 173, 200))
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=70)
            return buf.getvalue()

    def capture_screen_base64(self, max_width: int = 1280, quality: int = 75) -> str:
        """ Base64 （ WebSocket  LLM ）"""
        jpeg_bytes = self.capture_screen_bytes(max_width=max_width, quality=quality)
        return base64.b64encode(jpeg_bytes).decode("utf-8")

    # =====================================================================
    # 2. 
    # =====================================================================
    def normalize_to_screen_coords(self, norm_x: float, norm_y: float) -> Tuple[int, int]:
        """
         0~1000 。
        """
        screen_w, screen_h = self.get_screen_size()
        real_x = int((norm_x / 1000.0) * screen_w)
        real_y = int((norm_y / 1000.0) * screen_h)
        #  FailSafe ( (0,0) )
        real_x = max(5, min(screen_w - 5, real_x))
        real_y = max(5, min(screen_h - 5, real_y))
        return real_x, real_y

    def click_at_ratio(self, ratio_x: float, ratio_y: float, button: str = "left"):
        """ Web ： (0.0 ~ 1.0) """
        if state.stop_requested:
            return
        screen_w, screen_h = self.get_screen_size()
        real_x = max(5, min(screen_w - 5, int(ratio_x * screen_w)))
        real_y = max(5, min(screen_h - 5, int(ratio_y * screen_h)))
        pyautogui.click(real_x, real_y, button=button)
        state.add_log(f"🖱️ Web : ({real_x}, {real_y})")

    def type_text_unicode(self, text: str, press_enter: bool = False):
        """、Emoji """
        if state.stop_requested:
            return
        pyperclip.copy(text)
        time.sleep(0.1)
        if self.os_name == "Darwin":
            pyautogui.hotkey("command", "v")
        else:
            pyautogui.hotkey("ctrl", "v")
        time.sleep(0.2)
        if press_enter and not state.stop_requested:
            pyautogui.press("enter")

    def execute_action(self, action_data: Dict[str, Any]) -> str:
        """
         LLM Agent  JSON 。
         stop 。
        """
        if state.stop_requested:
            raise asyncio.CancelledError("")

        # Windows 
        if self.os_name == "Windows":
            try:
                import ctypes
                user32 = ctypes.windll.user32
                hdesk = user32.OpenInputDesktop(0, False, 0x01FF)
                if hdesk:
                    user32.SetThreadDesktop(hdesk)
                # ， (0,0) ， FailSafe
                pt = ctypes.wintypes.POINT() if hasattr(ctypes, "wintypes") else None
                class POINT(ctypes.Structure):
                    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
                p = POINT()
                user32.GetCursorPos(ctypes.byref(p))
                if p.x <= 5 and p.y <= 5:
                    user32.SetCursorPos(400, 400)
            except Exception:
                pass

        action_type = action_data.get("action", "").lower()
        thought = action_data.get("thought", f" {action_type}")
        param = action_data.get("param")

        log_detail = f"{thought} (: {param})" if param is not None and str(param) not in thought else thought
        state.current_action_desc = log_detail
        cat_hud.update_status("working", log_detail)
        state.add_log(f"🐾  [{action_type}]: {log_detail}")

        try:
            #  "param" 
            param = action_data.get("param")

            if action_type in ("click", "double_click", "right_click"):
                x_val = action_data.get("x")
                y_val = action_data.get("y")
                norm_x = float(x_val) if x_val is not None else 500.0
                norm_y = float(y_val) if y_val is not None else 500.0
                real_x, real_y = self.normalize_to_screen_coords(norm_x, norm_y)

                if state.stop_requested:
                    raise asyncio.CancelledError("")

                pyautogui.moveTo(real_x, real_y, duration=0.3, tween=pyautogui.easeOutQuad)

                if state.stop_requested:
                    raise asyncio.CancelledError("")

                if action_type == "click":
                    cat_hud.flash_click_ripple(real_x, real_y)
                    state.last_click = {"x": norm_x / 1000.0, "y": norm_y / 1000.0, "type": "click"}
                    pyautogui.click(real_x, real_y)
                elif action_type == "double_click":
                    cat_hud.flash_click_ripple(real_x, real_y)
                    state.last_click = {"x": norm_x / 1000.0, "y": norm_y / 1000.0, "type": "double_click"}
                    pyautogui.doubleClick(real_x, real_y)
                elif action_type == "right_click":
                    cat_hud.flash_click_ripple(real_x, real_y)
                    state.last_click = {"x": norm_x / 1000.0, "y": norm_y / 1000.0, "type": "right_click"}
                    pyautogui.rightClick(real_x, real_y)
                return f" ({real_x}, {real_y})  {action_type}"

            elif action_type == "type":
                # 
                x_val = action_data.get("x")
                y_val = action_data.get("y")
                if x_val is not None and y_val is not None:
                    try:
                        rx, ry = self.normalize_to_screen_coords(float(x_val), float(y_val))
                        pyautogui.moveTo(rx, ry, duration=0.3)
                        pyautogui.click(rx, ry)
                        time.sleep(0.2)
                    except (ValueError, TypeError):
                        pass

                if state.stop_requested:
                    raise asyncio.CancelledError("")

                text = str(param if param is not None else action_data.get("text", ""))
                press_enter = bool(action_data.get("press_enter", False))
                self.type_text_unicode(text, press_enter=press_enter)
                return f": '{text}'"

            elif action_type == "hotkey":
                key_param = param if param is not None else action_data.get("keys", ["win"])
                if isinstance(key_param, str):
                    keys = [k.strip() for k in key_param.split("+")]
                elif isinstance(key_param, list):
                    keys = key_param
                else:
                    keys = ["win"]

                if keys and not state.stop_requested:
                    pyautogui.hotkey(*keys)
                return f": {'+'.join(keys)}"

            elif action_type == "press":
                key = str(param if param is not None else action_data.get("key", "enter")).lower()
                if not state.stop_requested:
                    pyautogui.press(key)
                return f": {key}"

            elif action_type == "click_text":
                # ： Enter 
                if not state.stop_requested:
                    pyautogui.press("enter")
                return f": {param}"

            elif action_type == "scroll":
                amount = int(param if param is not None else action_data.get("amount", -500))
                pyautogui.scroll(amount)
                return f": {amount}"

            elif action_type == "open_url":
                import webbrowser
                url = str(param if param is not None else action_data.get("url", ""))
                if url:
                    webbrowser.open(url)
                    return f": {url}"
                return " URL "

            elif action_type == "focus_window":
                win_name = str(param if param is not None else action_data.get("title", "")).lower()
                try:
                    import ctypes
                    user32 = ctypes.windll.user32
                    user32.SetThreadDesktop(user32.OpenInputDesktop(0, False, 0x01FF))
                    target_hwnd = None
                    def _find_win(hwnd, extra):
                        nonlocal target_hwnd
                        buf = ctypes.create_unicode_buffer(512)
                        user32.GetWindowTextW(hwnd, buf, 512)
                        t = buf.value.lower()
                        if win_name in t and user32.IsWindowVisible(hwnd):
                            target_hwnd = hwnd
                            return False
                        return True
                    WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)
                    user32.EnumWindows(WNDPROC(_find_win), 0)
                    if target_hwnd:
                        user32.ShowWindow(target_hwnd, 9)
                        user32.SetForegroundWindow(target_hwnd)
                        return f" '{win_name}' ！"
                except Exception as ex:
                    pass
                return f": {win_name}"

            elif action_type == "run_cmd":
                return f" [{action_type}]： open_url ！"

            elif action_type == "wait":
                sec = float(param if param is not None else action_data.get("seconds", 2.0))
                total_sleep = min(sec, 10.0)
                step_sleep = 0.1
                elapsed = 0.0
                while elapsed < total_sleep:
                    if state.stop_requested:
                        raise asyncio.CancelledError("")
                    time.sleep(step_sleep)
                    elapsed += step_sleep
                return f" {sec} "

            elif action_type == "done":
                msg = str(param if param is not None else action_data.get("message", "！"))
                return f"DONE: {msg}"

            else:
                return f": {action_type}"

        except pyautogui.FailSafeException:
            state.request_stop()
            err_msg = "⚠️ Physical FailSafe triggered: mouse moved to corner!"
            state.add_log(err_msg)
            cat_hud.update_status("idle", "Emergency stop triggered")
            raise RuntimeError(err_msg)
        except asyncio.CancelledError:
            state.add_log("⏹️ Action execution cancelled (Killed)")
            cat_hud.update_status("idle", "Command cancelled")
            raise
        except Exception as e:
            err_msg = f"❌ Execution error in action {action_type}: {str(e)}"
            state.add_log(err_msg)
            return err_msg

    # =====================================================================
    # 3. Keep-Online Mouse Anti-Sleep Patrol Loop
    # =====================================================================
    async def toggle_keep_alive(self, enable: bool | None = None) -> bool:
        """Toggle or set Keep-Online Anti-Sleep mode"""
        if enable is None:
            state.keep_alive_enabled = not state.keep_alive_enabled
        else:
            state.keep_alive_enabled = enable

        cat_hud.set_keepalive_state(state.keep_alive_enabled)

        if state.keep_alive_enabled:
            state.add_log("🟢 Keep-Online mouse anti-sleep mode activated")
            if not state.is_agent_busy:
                cat_hud.update_status("keepalive", "Patrolling to keep PC awake...")
            if self._keep_alive_task is None or self._keep_alive_task.done():
                self._keep_alive_task = asyncio.create_task(self._keep_alive_loop())
        else:
            state.add_log("⚪ Keep-Online anti-sleep mode deactivated")
            if self._keep_alive_task and not self._keep_alive_task.done():
                self._keep_alive_task.cancel()
            if not state.is_agent_busy:
                cat_hud.update_status("idle", "Ready & idling")

        return state.keep_alive_enabled

    async def _keep_alive_loop(self):
        """
        Keep-Online ： MoveMyCursor.com  Jiggler！
        -  (Smooth Natural Jiggler Path)
        - ，
        -  Windows SetThreadExecutionState (ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED)
          // (Away)
        - 100% ，
        """
        import math
        try:
            # 1.  WakeLock ()
            if self.os_name == "Windows":
                try:
                    import ctypes
                    # ES_CONTINUOUS = 0x80000000, ES_SYSTEM_REQUIRED = 0x00000001, ES_DISPLAY_REQUIRED = 0x00000002
                    ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001 | 0x00000002)
                except Exception:
                    pass

            radius = 55       #  ()
            steps = 32        
            angle_step = (2 * math.pi) / steps

            while state.keep_alive_enabled:
                if not state.is_agent_busy:
                    try:
                        if self.os_name == "Windows":
                            import ctypes
                            user32 = ctypes.windll.user32
                            
                            # ，！
                            try:
                                hdesk = user32.OpenInputDesktop(0, False, 0x01FF)
                                if hdesk:
                                    user32.SetThreadDesktop(hdesk)
                                    user32.CloseDesktop(hdesk)
                            except Exception:
                                pass

                            sw = user32.GetSystemMetrics(0)
                            sh = user32.GetSystemMetrics(1)
                            
                            class POINT(ctypes.Structure):
                                _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
                            pt = POINT()
                            user32.GetCursorPos(ctypes.byref(pt))
                            cx, cy = pt.x, pt.y

                            # ，
                            if cx < 80 or cy < 80 or cx > sw - 80 or cy > sh - 80:
                                cx, cy = sw // 2, sh // 2
                                user32.SetCursorPos(cx, cy)

                            # ：
                            # 1.  ()
                            # 2.  (Click)  (Right Click)
                            user_interrupted = False
                            for i in range(steps):
                                if not state.keep_alive_enabled or state.is_agent_busy or state.stop_requested:
                                    break

                                #  (0x01)  (0x02) 
                                if (user32.GetAsyncKeyState(0x01) & 0x8000) or (user32.GetAsyncKeyState(0x02) & 0x8000):
                                    user_interrupted = True
                                    break

                                # ： 15 ，！
                                user32.GetCursorPos(ctypes.byref(pt))
                                if i > 0 and (abs(pt.x - last_set_x) > 15 or abs(pt.y - last_set_y) > 15):
                                    user_interrupted = True
                                    break

                                theta = i * angle_step
                                tx = int(cx + radius * math.cos(theta))
                                ty = int(cy + radius * math.sin(theta))
                                user32.SetCursorPos(tx, ty)
                                last_set_x, last_set_y = tx, ty
                                await asyncio.sleep(0.025)

                            if user_interrupted:
                                # User active with mouse/click: stop circling, politely step aside for 10s
                                cat_hud.update_status(
                                    "keepalive",
                                    "User active: Cat politely stepping aside..."
                                )
                                await asyncio.sleep(10.0)
                                continue

                            # Return to center
                            if state.keep_alive_enabled and not state.is_agent_busy:
                                user32.SetCursorPos(cx, cy)
                                user32.mouse_event(0x0001, 1, 0, 0, 0)
                                user32.mouse_event(0x0001, -1, 0, 0, 0)

                        else:
                            orig_x, orig_y = pyautogui.position()
                            for i in range(steps):
                                if not state.keep_alive_enabled or state.is_agent_busy or state.stop_requested:
                                    break
                                theta = i * angle_step
                                tx = int(orig_x + radius * math.cos(theta))
                                ty = int(orig_y + radius * math.sin(theta))
                                pyautogui.moveTo(tx, ty, duration=0.01)
                                await asyncio.sleep(0.02)
                            pyautogui.moveTo(orig_x, orig_y, duration=0.02)

                        cat_hud.update_status(
                            "keepalive",
                            "Jiggler patrolling in circle (Next in 10s)"
                        )
                    except Exception as e:
                        state.add_log(f"Keep-Alive notice: {e}")

                # ： 10  (10 Buff)
                await asyncio.sleep(10.0)
        except asyncio.CancelledError:
            pass
        finally:
            # 
            if self.os_name == "Windows":
                try:
                    import ctypes
                    # ES_CONTINUOUS = 0x80000000 ()
                    ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
                except Exception:
                    pass


controller = ComputerController()