# -*- coding: utf-8 -*-
"""
controller.py - 屏幕截取、键鼠自动化操控与 Keep-Online 防休眠核心引擎
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

# 安全第一：开启 PyAutoGUI 紧急中断！把物理鼠标猛甩到屏幕最左上角 (0,0) 即可触发 FailSafeException
pyautogui.FAILSAFE = True
# 每个 PyAutoGUI 动作之间的短暂缓冲间隔
pyautogui.PAUSE = 0.15


class ComputerController:
    def __init__(self):
        self.os_name = platform.system()  # 'Windows', 'Darwin' (macOS), 'Linux'
        self._keep_alive_task: asyncio.Task | None = None

    # =====================================================================
    # 1. 屏幕尺寸与极速截图方法
    # =====================================================================
    def get_screen_size(self) -> Tuple[int, int]:
        """获取逻辑屏幕宽高 (用于 PyAutoGUI 定位)"""
        width, height = pyautogui.size()
        return width, height

    def capture_screen_bytes(self, max_width: int = 1280, quality: int = 75) -> bytes:
        """
        使用 mss 极速截取主屏幕，并压缩为 JPEG 字节流。
        自动挂载 Windows 活跃桌面 (SetThreadDesktop)，彻底解决后台线程 BitBlt Access Denied 问题。
        """
        # Windows 系统下自动关联活跃桌面 (InputDesktop)
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
        """获取 Base64 格式的屏幕截图（用于 WebSocket 推送与 LLM 分析）"""
        jpeg_bytes = self.capture_screen_bytes(max_width=max_width, quality=quality)
        return base64.b64encode(jpeg_bytes).decode("utf-8")

    # =====================================================================
    # 2. 坐标转换与基础键鼠动作执行
    # =====================================================================
    def normalize_to_screen_coords(self, norm_x: float, norm_y: float) -> Tuple[int, int]:
        """
        将 0~1000 归一化坐标转换为当前屏幕的实际逻辑坐标。
        """
        screen_w, screen_h = self.get_screen_size()
        real_x = int((norm_x / 1000.0) * screen_w)
        real_y = int((norm_y / 1000.0) * screen_h)
        # 防止越界触发 FailSafe (避开 (0,0) 极点)
        real_x = max(5, min(screen_w - 5, real_x))
        real_y = max(5, min(screen_h - 5, real_y))
        return real_x, real_y

    def click_at_ratio(self, ratio_x: float, ratio_y: float, button: str = "left"):
        """供 Web 远程控制端调用：根据网页点击位置百分比 (0.0 ~ 1.0) 点击真实屏幕"""
        if state.stop_requested:
            return
        screen_w, screen_h = self.get_screen_size()
        real_x = max(5, min(screen_w - 5, int(ratio_x * screen_w)))
        real_y = max(5, min(screen_h - 5, int(ratio_y * screen_h)))
        pyautogui.click(real_x, real_y, button=button)
        state.add_log(f"🖱️ Web 远程点击坐标: ({real_x}, {real_y})")

    def type_text_unicode(self, text: str, press_enter: bool = False):
        """支持中文、Emoji 及特殊符号的文本输入方法"""
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
        执行由 LLM Agent 解析出的标准 JSON 动作指令。
        具备即时 stop 检测能力。
        """
        if state.stop_requested:
            raise asyncio.CancelledError("执行已被用户终止")

        # Windows 系统下自动关联物理活跃输入桌面
        if self.os_name == "Windows":
            try:
                import ctypes
                user32 = ctypes.windll.user32
                hdesk = user32.OpenInputDesktop(0, False, 0x01FF)
                if hdesk:
                    user32.SetThreadDesktop(hdesk)
                # 检查鼠标位置，如果处于 (0,0) 盲点，立即平移到安全位置避免触发 FailSafe
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
        thought = action_data.get("thought", f"执行 {action_type}")
        param = action_data.get("param")

        log_detail = f"{thought} (参数: {param})" if param is not None and str(param) not in thought else thought
        state.current_action_desc = log_detail
        cat_hud.update_status("working", log_detail)
        state.add_log(f"🐾 猫咪执行 [{action_type}]: {log_detail}")

        try:
            # 兼容 "param" 统一参数字段解析
            param = action_data.get("param")

            if action_type in ("click", "double_click", "right_click"):
                x_val = action_data.get("x")
                y_val = action_data.get("y")
                norm_x = float(x_val) if x_val is not None else 500.0
                norm_y = float(y_val) if y_val is not None else 500.0
                real_x, real_y = self.normalize_to_screen_coords(norm_x, norm_y)

                if state.stop_requested:
                    raise asyncio.CancelledError("执行已被用户终止")

                pyautogui.moveTo(real_x, real_y, duration=0.3, tween=pyautogui.easeOutQuad)

                if state.stop_requested:
                    raise asyncio.CancelledError("执行已被用户终止")

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
                return f"已在坐标 ({real_x}, {real_y}) 执行 {action_type}"

            elif action_type == "type":
                # 安全解析可选坐标
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
                    raise asyncio.CancelledError("执行已被用户终止")

                text = str(param if param is not None else action_data.get("text", ""))
                press_enter = bool(action_data.get("press_enter", False))
                self.type_text_unicode(text, press_enter=press_enter)
                return f"已输入文本: '{text}'"

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
                return f"已按下快捷键: {'+'.join(keys)}"

            elif action_type == "press":
                key = str(param if param is not None else action_data.get("key", "enter")).lower()
                if not state.stop_requested:
                    pyautogui.press(key)
                return f"已单按按键: {key}"

            elif action_type == "click_text":
                # 点击文本逻辑：按 Enter 激活
                if not state.stop_requested:
                    pyautogui.press("enter")
                return f"已激活匹配项: {param}"

            elif action_type == "scroll":
                amount = int(param if param is not None else action_data.get("amount", -500))
                pyautogui.scroll(amount)
                return f"已滚动页面: {amount}"

            elif action_type == "open_url":
                import webbrowser
                url = str(param if param is not None else action_data.get("url", ""))
                if url:
                    webbrowser.open(url)
                    return f"已通过系统默认浏览器快速打开网址: {url}"
                return "未指定 URL 网址"

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
                        return f"已将窗口 '{win_name}' 置顶激活并聚焦！"
                except Exception as ex:
                    pass
                return f"未能匹配窗口: {win_name}"

            elif action_type == "run_cmd":
                return f"已拦截 [{action_type}]：请改用标准键鼠或 open_url 动作！"

            elif action_type == "wait":
                sec = float(param if param is not None else action_data.get("seconds", 2.0))
                total_sleep = min(sec, 10.0)
                step_sleep = 0.1
                elapsed = 0.0
                while elapsed < total_sleep:
                    if state.stop_requested:
                        raise asyncio.CancelledError("等待被用户终止")
                    time.sleep(step_sleep)
                    elapsed += step_sleep
                return f"已等待 {sec} 秒"

            elif action_type == "done":
                msg = str(param if param is not None else action_data.get("message", "任务圆满完成喵！"))
                return f"DONE: {msg}"

            else:
                return f"未知动作类型: {action_type}"

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
        Keep-Online 巡逻循环：对标 MoveMyCursor.com 专业在线防休眠 Jiggler！
        - 持续平滑画圆游走 (Smooth Natural Jiggler Path)
        - 自动获取鼠标当前落点，围绕当前落点进行自然平滑画圆与巡逻
        - 结合 Windows SetThreadExecutionState (ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED)
          彻底锁定系统与显示器不进入休眠/变黄/变离开 (Away)
        - 100% 真实光标物理移动，支持秒级紧急停止
        """
        import math
        try:
            # 1. 开启系统级防锁屏 WakeLock (阻止睡眠与显示器关闭)
            if self.os_name == "Windows":
                try:
                    import ctypes
                    # ES_CONTINUOUS = 0x80000000, ES_SYSTEM_REQUIRED = 0x00000001, ES_DISPLAY_REQUIRED = 0x00000002
                    ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001 | 0x00000002)
                except Exception:
                    pass

            radius = 55       # 画圆半径 (像素)
            steps = 32        # 平滑步数
            angle_step = (2 * math.pi) / steps

            while state.keep_alive_enabled:
                if not state.is_agent_busy:
                    try:
                        if self.os_name == "Windows":
                            import ctypes
                            user32 = ctypes.windll.user32
                            
                            # 必须切换挂载活跃用户桌面，否则后台协程无法控制主屏真实物理鼠标！
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

                            # 如果当前光标在极端死角，平移至屏幕舒适区
                            if cx < 80 or cy < 80 or cx > sw - 80 or cy > sh - 80:
                                cx, cy = sw // 2, sh // 2
                                user32.SetCursorPos(cx, cy)

                            # 检测用户主动操作：
                            # 1. 用户移动了鼠标 (外力拖动)
                            # 2. 用户按下了左键 (Click) 或右键 (Right Click)
                            user_interrupted = False
                            for i in range(steps):
                                if not state.keep_alive_enabled or state.is_agent_busy or state.stop_requested:
                                    break

                                # 检测物理左键 (0x01) 或右键 (0x02) 是否被用户按下
                                if (user32.GetAsyncKeyState(0x01) & 0x8000) or (user32.GetAsyncKeyState(0x02) & 0x8000):
                                    user_interrupted = True
                                    break

                                # 检查实际光标位置：如果跟上次设置的位置偏差超过 15 像素，说明用户正在操控鼠标！
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

                # 按照主人要求：每隔 10 秒平滑画圆巡逻一次 (10秒 Buff)
                await asyncio.sleep(10.0)
        except asyncio.CancelledError:
            pass
        finally:
            # 退出时恢复系统默认电源策略
            if self.os_name == "Windows":
                try:
                    import ctypes
                    # ES_CONTINUOUS = 0x80000000 (恢复默认状态)
                    ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
                except Exception:
                    pass


controller = ComputerController()