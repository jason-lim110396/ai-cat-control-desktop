import tkinter as tk
import threading
import queue
import time
import os
from typing import Optional
from PIL import Image, ImageTk


class CatOverlay:
    """
    Desktop topmost floating Cat HUD running in a dedicated daemon thread.
    Features real animated pixel cat sprites rendered with high-DPI scaling:
      - "idle": Cute sitting, blinking, and yawning cat
      - "keepalive": Walking, strolling patrol cat
      - "working": Fast running/typing paws cat with sparks
      - "done": Happy jumping celebration cat
    """

    def __init__(self):
        self._cmd_queue: queue.Queue = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._running: bool = False

        self._mode: str = "idle"
        self._text: str = "Ready & idling"
        self._anim_tick: int = 0
        self._keepalive_active: bool = False

        self._drag_x: int = 0
        self._drag_y: int = 0
        self._cat_sprites: dict = {}

    def start(self):
        """Start overlay in a dedicated thread"""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_tk_loop, daemon=True)
        self._thread.start()

    def update_status(self, mode: str, text: str):
        """
        Thread-safe status & text update
        :param mode: "idle" | "keepalive" | "working" | "done"
        :param text: Descriptive text to display
        """
        self._cmd_queue.put(("STATUS", (mode, text)))

    def set_keepalive_state(self, is_enabled: bool):
        """Update Keep-Alive toggle button visual state"""
        self._cmd_queue.put(("KEEPALIVE_SYNC", is_enabled))

    def stop(self):
        """Close overlay"""
        self._running = False
        self._cmd_queue.put(("QUIT", ""))

    def _load_cat_sprites(self):
        """Load and extract real cat animated sprite frames"""
        base_dir = os.path.dirname(__file__)
        frames_dir = os.path.join(base_dir, "cat_frames")
        gif_path = os.path.join(base_dir, "oneko.gif")

        # If frames don't exist yet, generate them from oneko.gif
        if not os.path.exists(frames_dir) or not os.listdir(frames_dir):
            try:
                os.makedirs(frames_dir, exist_ok=True)
                im = Image.open(gif_path).convert("RGBA")
                
                def get_frame(col, row, scale=1.5):
                    x = col * 32
                    y = row * 32
                    cropped = im.crop((x, y, x + 32, y + 32))
                    w, h = int(32 * scale), int(32 * scale)
                    return cropped.resize((w, h), Image.NEAREST)

                anim_defs = {
                    "idle": [(3, 3), (3, 3), (3, 2), (7, 3)],
                    "sleeping": [(2, 0), (2, 1)],
                    "scratch": [(5, 0), (6, 0), (7, 0)],
                    "working": [(3, 0), (3, 1), (4, 2), (4, 3)],
                    "patrol": [(3, 0), (3, 1), (6, 3), (7, 2)],
                    "done": [(7, 3), (5, 0), (7, 3), (6, 0)]
                }
                for anim_name, coords in anim_defs.items():
                    for idx, (c, r) in enumerate(coords):
                        f = get_frame(c, r, scale=1.5)
                        f.save(os.path.join(frames_dir, f"{anim_name}_{idx}.png"))
            except Exception as e:
                print("Generate cat frames note:", e)

        # Load PhotoImages into memory
        self._cat_sprites = {}
        modes = ["idle", "sleeping", "scratch", "working", "patrol", "done"]
        for m in modes:
            self._cat_sprites[m] = []
            idx = 0
            while True:
                fpath = os.path.join(frames_dir, f"{m}_{idx}.png")
                if not os.path.exists(fpath):
                    break
                try:
                    img = Image.open(fpath)
                    photo = ImageTk.PhotoImage(img)
                    self._cat_sprites[m].append(photo)
                except Exception as ex:
                    print(f"Error loading {fpath}:", ex)
                idx += 1

    def _run_tk_loop(self):
        """Tkinter main event loop running in sub-thread"""
        try:
            import ctypes
            user32 = ctypes.windll.user32
            hdesk = user32.OpenInputDesktop(0, False, 0x01FF)
            if hdesk:
                user32.SetThreadDesktop(hdesk)
                user32.CloseDesktop(hdesk)
        except Exception as e:
            print("Desktop bind note:", e)

        self.root = tk.Tk()
        self.root.title("NekoDesk HUD")

        # Frameless, topmost, 94% opacity
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.94)

        # Sleek dimensions tailored for real cat animation (340x68)
        win_w, win_h = 340, 68
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        pos_x = screen_w - win_w - 24
        pos_y = screen_h - win_h - 60
        self.root.geometry(f"{win_w}x{win_h}+{pos_x}+{pos_y}")

        # Load sprites inside the Tk thread
        self._load_cat_sprites()

        # Main container frame
        self.frame = tk.Frame(
            self.root,
            bg="#181825",
            highlightbackground="#cba6f7",
            highlightthickness=2,
            padx=8,
            pady=4
        )
        self.frame.pack(fill=tk.BOTH, expand=True)

        # Left: Real Cat Animated Canvas/Label (48x48)
        self.cat_img_label = tk.Label(
            self.frame,
            bg="#181825",
            width=50,
            height=50
        )
        self.cat_img_label.pack(side=tk.LEFT, padx=(2, 8))

        # Right container: Info & Buttons
        self.info_box = tk.Frame(self.frame, bg="#181825")
        self.info_box.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Top row: Status Title + Buttons
        self.top_row = tk.Frame(self.info_box, bg="#181825")
        self.top_row.pack(fill=tk.X, pady=(2, 0))

        self.cat_label = tk.Label(
            self.top_row,
            text="IDLE",
            font=("Segoe UI", 9, "bold"),
            fg="#f5c2e7",
            bg="#181825",
            anchor="w"
        )
        self.cat_label.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.btn_keepalive = tk.Button(
            self.top_row,
            text="☕ Keep: OFF",
            font=("Segoe UI", 8, "bold"),
            bg="#313244",
            fg="#cdd6f4",
            activebackground="#45475a",
            activeforeground="#ffffff",
            relief="flat",
            padx=6,
            pady=1,
            command=self._on_hud_keepalive_clicked,
            cursor="hand2"
        )
        self.btn_keepalive.pack(side=tk.LEFT, padx=(2, 6))

        self.btn_stop = tk.Button(
            self.top_row,
            text="🛑 STOP",
            font=("Segoe UI", 8, "bold"),
            bg="#f38ba8",
            fg="#181825",
            activebackground="#ff6584",
            activeforeground="#ffffff",
            relief="flat",
            padx=7,
            pady=1,
            command=self._on_hud_stop_clicked,
            cursor="hand2"
        )
        self.btn_stop.pack(side=tk.RIGHT)

        # Bottom row: Action description
        self.desc_label = tk.Label(
            self.info_box,
            text="🐾 Ready (Hold to drag)",
            font=("Segoe UI", 8),
            fg="#a6adc8",
            bg="#181825",
            anchor="w",
            wraplength=250,
            justify="left"
        )
        self.desc_label.pack(fill=tk.X, pady=(2, 0))

        # Dragging events across widgets
        for widget in (self.frame, self.cat_img_label, self.info_box, self.cat_label, self.desc_label, self.top_row):
            widget.bind("<ButtonPress-1>", self._on_drag_start)
            widget.bind("<B1-Motion>", self._on_drag_motion)

        self.root.after(100, self._periodic_check)
        self.root.mainloop()

    def _on_hud_stop_clicked(self):
        """User clicked STOP directly on overlay"""
        try:
            from config import state
            state.request_stop()
            self.update_status("idle", "Stopped via desktop HUD")
        except Exception as e:
            print("HUD Stop Error:", e)

    def _on_hud_keepalive_clicked(self):
        """User toggled Keep-Alive directly from overlay"""
        try:
            from controller import controller
            import asyncio
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.run_coroutine_threadsafe(controller.toggle_keep_alive(), loop)
                else:
                    asyncio.run(controller.toggle_keep_alive())
            except Exception:
                from config import state
                state.keep_alive_enabled = not state.keep_alive_enabled
                self.set_keepalive_state(state.keep_alive_enabled)
        except Exception as e:
            print("HUD Keepalive Toggle Error:", e)

    def flash_click_ripple(self, real_x: int, real_y: int):
        """Flash a subtle radar circle on the exact screen coordinates clicked"""
        if not self._running:
            return
        self._cmd_queue.put(("RIPPLE", (real_x, real_y)))

    def show_toast(self, title: str, message: str, event_type: str = "completed"):
        """Display prominent toast banner with sound alert"""
        if not self._running:
            return
        self._cmd_queue.put(("TOAST", (title, message, event_type)))

    def _show_toast_window(self, title: str, message: str, event_type: str):
        try:
            import ctypes
            beep_type = 0x10 if event_type == "stopped" else 0x40
            ctypes.windll.user32.MessageBeep(beep_type)

            toast = tk.Toplevel(self.root)
            toast.overrideredirect(True)
            toast.attributes("-topmost", True)
            toast.attributes("-alpha", 0.95)

            tw, th = 380, 85
            sw = self.root.winfo_screenwidth()
            pos_x = (sw - tw) // 2
            pos_y = 50
            toast.geometry(f"{tw}x{th}+{pos_x}+{pos_y}")

            is_stop = (event_type == "stopped")
            bg_c = "#2d161d" if is_stop else "#182b23"
            bd_c = "#f38ba8" if is_stop else "#a6e3a1"
            fg_title = "#f38ba8" if is_stop else "#a6e3a1"

            frame = tk.Frame(toast, bg=bg_c, highlightbackground=bd_c, highlightthickness=2, padx=12, pady=8)
            frame.pack(fill=tk.BOTH, expand=True)

            lbl_title = tk.Label(frame, text=title, font=("Segoe UI", 10, "bold"), fg=fg_title, bg=bg_c, anchor="w")
            lbl_title.pack(fill=tk.X)

            lbl_msg = tk.Label(frame, text=message, font=("Segoe UI", 8), fg="#cdd6f4", bg=bg_c, anchor="w", wraplength=350, justify="left")
            lbl_msg.pack(fill=tk.X, pady=(4, 0))

            self.root.after(4000, toast.destroy)
        except Exception as e:
            print("Toast error:", e)

    def _show_screen_ripple_win(self, real_x: int, real_y: int):
        try:
            ripple = tk.Toplevel(self.root)
            ripple.overrideredirect(True)
            ripple.attributes("-topmost", True)
            ripple.attributes("-transparentcolor", "#000000")
            size = 46
            rx = real_x - (size // 2)
            ry = real_y - (size // 2)
            ripple.geometry(f"{size}x{size}+{rx}+{ry}")

            canvas = tk.Canvas(ripple, width=size, height=size, bg="#000000", highlightthickness=0)
            canvas.pack(fill=tk.BOTH, expand=True)
            canvas.create_oval(4, 4, size-4, size-4, outline="#f38ba8", width=3)
            canvas.create_oval(16, 16, size-16, size-16, fill="#f38ba8", outline="")

            self.root.after(350, ripple.destroy)
        except Exception:
            pass

    def _on_drag_start(self, event):
        self._drag_x = event.x
        self._drag_y = event.y

    def _on_drag_motion(self, event):
        x = self.root.winfo_x() + (event.x - self._drag_x)
        y = self.root.winfo_y() + (event.y - self._drag_y)
        self.root.geometry(f"+{x}+{y}")

    def ask_user_dialog(self, question: str, default: str = ""):
        if not self._running:
            return
        self._cmd_queue.put(("ASK_USER", (question, default)))

    def _show_ask_user_dialog(self, question: str, default: str):
        try:
            import ctypes
            ctypes.windll.user32.MessageBeep(0x40)

            dlg = tk.Toplevel(self.root)
            dlg.overrideredirect(False)
            dlg.title("NekoDesk Question")
            dlg.attributes("-topmost", True)
            dlg.configure(bg="#1e1e2e")

            dw, dh = 460, 190
            sw = self.root.winfo_screenwidth()
            sh = self.root.winfo_screenheight()
            pos_x = (sw - dw) // 2
            pos_y = (sh - dh) // 2
            dlg.geometry(f"{dw}x{dh}+{pos_x}+{pos_y}")

            frame = tk.Frame(dlg, bg="#1e1e2e", padx=18, pady=14)
            frame.pack(fill=tk.BOTH, expand=True)

            lbl_title = tk.Label(
                frame,
                text="❓ Cat needs your clarification:",
                font=("Segoe UI", 10, "bold"),
                fg="#f5c2e7",
                bg="#1e1e2e",
                anchor="w"
            )
            lbl_title.pack(fill=tk.X)

            lbl_q = tk.Label(
                frame,
                text=question,
                font=("Segoe UI", 9),
                fg="#cdd6f4",
                bg="#1e1e2e",
                anchor="w",
                wraplength=420,
                justify="left"
            )
            lbl_q.pack(fill=tk.X, pady=(5, 10))

            entry = tk.Entry(
                frame,
                font=("Segoe UI", 10),
                bg="#313244",
                fg="#ffffff",
                insertbackground="#ffffff",
                relief="flat",
                highlightthickness=1,
                highlightbackground="#cba6f7",
                highlightcolor="#a6e3a1"
            )
            entry.pack(fill=tk.X, ipady=3)
            if default:
                entry.insert(0, default)
            entry.focus_set()

            btn_frame = tk.Frame(frame, bg="#1e1e2e")
            btn_frame.pack(fill=tk.X, pady=(10, 0))

            def _on_confirm():
                ans = entry.get().strip()
                dlg.destroy()
                try:
                    from config import state
                    state.submit_user_answer(ans)
                except Exception as ex:
                    print("Submit answer error:", ex)

            btn_ok = tk.Button(
                btn_frame,
                text="Confirm & Continue",
                font=("Segoe UI", 9, "bold"),
                bg="#a6e3a1",
                fg="#11111b",
                activebackground="#89dceb",
                relief="flat",
                padx=12,
                pady=2,
                cursor="hand2",
                command=_on_confirm
            )
            btn_ok.pack(side=tk.RIGHT)
            entry.bind("<Return>", lambda e: _on_confirm())

        except Exception as e:
            print("Ask user dialog error:", e)

    def _periodic_check(self):
        """Update loop running every 160ms with real animated sprite frames"""
        if not self._running:
            self.root.destroy()
            return

        try:
            while True:
                cmd, data = self._cmd_queue.get_nowait()
                if cmd == "QUIT":
                    self.root.destroy()
                    return
                elif cmd == "RIPPLE":
                    rx, ry = data
                    self._show_screen_ripple_win(rx, ry)
                elif cmd == "TOAST":
                    t_title, t_msg, t_type = data
                    self._show_toast_window(t_title, t_msg, t_type)
                elif cmd == "ASK_USER":
                    q_text, def_ans = data
                    self._show_ask_user_dialog(q_text, def_ans)
                elif cmd == "STATUS":
                    self._mode, self._text = data
                elif cmd == "KEEPALIVE_SYNC":
                    self._keepalive_active = bool(data)
        except queue.Empty:
            pass

        # Sync Keep-Alive button text & style
        if self._keepalive_active:
            self.btn_keepalive.config(
                text="☕ Keep: ON",
                bg="#a6e3a1",
                fg="#11111b",
                activebackground="#89dceb"
            )
        else:
            self.btn_keepalive.config(
                text="☕ Keep: OFF",
                bg="#313244",
                fg="#cdd6f4",
                activebackground="#45475a"
            )

        self._anim_tick += 1
        mode_lower = str(self._mode).lower()

        # Select animation sequence & visual palette based on current status
        if mode_lower in ("working", "running"):
            cat_anim_name = "working"
            bg_color = "#251417"
            border_color = "#f38ba8" if (self._anim_tick % 2 == 0) else "#fab387"
            cat_fg = "#fab387"
            desc_fg = "#ffd8a8"
            cat_str = "RUNNING"
            desc_str = f"⚡ {self._text}"

        elif mode_lower in ("done", "completed"):
            cat_anim_name = "done"
            bg_color = "#112219"
            border_color = "#a6e3a1"
            cat_fg = "#a6e3a1"
            desc_fg = "#cdd6f4"
            cat_str = "DONE"
            desc_str = f"🎉 {self._text}"

        elif mode_lower in ("keepalive", "patrol"):
            cat_anim_name = "patrol"
            bg_color = "#0f2228"
            border_color = "#89dceb"
            cat_fg = "#89dceb"
            desc_fg = "#cdd6f4"
            cat_str = "PATROL"
            desc_str = f"☕ {self._text}"

        else:
            # Idle / Standby: alternate between cute sitting and sleeping
            cat_anim_name = "idle" if ((self._anim_tick // 16) % 2 == 0) else "sleeping"
            bg_color = "#181825"
            border_color = "#585b70"
            cat_fg = "#b4befe"
            desc_fg = "#9399b2"
            cat_str = "IDLE"
            desc_str = f"🌙 {self._text}"

        # Update cat image sprite frame
        frames = self._cat_sprites.get(cat_anim_name, [])
        if frames:
            frame_idx = self._anim_tick % len(frames)
            self.cat_img_label.config(image=frames[frame_idx], bg=bg_color)

        # Update theme colors
        self.frame.config(bg=bg_color, highlightbackground=border_color)
        self.info_box.config(bg=bg_color)
        self.top_row.config(bg=bg_color)
        self.cat_label.config(text=cat_str, bg=bg_color, fg=cat_fg)
        self.desc_label.config(text=desc_str, bg=bg_color, fg=desc_fg)

        self.root.attributes("-topmost", True)
        self.root.after(160, self._periodic_check)


# Global singleton instance
cat_hud = CatOverlay()


if __name__ == "__main__":
    print("Starting Cat Overlay HUD test...")
    cat_hud.start()
    time.sleep(2)
    cat_hud.update_status("keepalive", "Patrolling to keep PC awake...")
    time.sleep(3)
    cat_hud.update_status("working", "Clicking search bar in Chrome")
    time.sleep(4)
    cat_hud.update_status("idle", "Task completed, standby")
    time.sleep(2)
    cat_hud.stop()