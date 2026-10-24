"""CD Kill Announcer - screen-only kill detection for Creative Destruction."""
import ctypes
import json
import os
import queue
import re
import sys
import threading
import time
import subprocess
import webbrowser
import tempfile
import urllib.request
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import mss
import psutil
import pytesseract
from PIL import Image, ImageEnhance, ImageFilter, ImageOps
from updater import VERSION, get_latest, install_update

try:
    import pygame
except ImportError:
    pygame = None

if sys.platform == "win32":
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass

APP_NAME = "CD Kill Announcer – sg0hann"
APP_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "CDKillAnnouncer"
SETTINGS_PATH = APP_DIR / "settings.json"
DEFAULT_EVENTS = {
    "1": "First Kill!", "2": "Double Kill!", "3": "Triple Kill!", "4": "Multi Kill!",
    "5": "Amazing Kill!", "6": "Ultra Kill!", "7": "Monster Kill!", "8": "Killing Spree!",
    "10": "Unstoppable!", "15": "Dominating!", "20": "Godlike!",
}
DEFAULTS = {
    "counter_region": None, "message_region": None, "death_region": None,
    "killer_region": None, "victim_region": None, "player_name": "",
    "tesseract": "", "keywords": "eliminated,killed,defeated,elimination",
    "death_keywords": "eliminated by,you died,you were eliminated",
    "volume": 0.9, "poll_interval": 0.40, "requires_game": True, "detection_mode": "counter",
    "phrases": DEFAULT_EVENTS.copy(), "sounds": {}, "active": False, "auto_update": True,
}

def asset_path(name):
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return root / name

def find_tesseract(preferred):
    paths = [preferred, str(asset_path("tesseract/tesseract.exe")),
             r"C:\Program Files\Tesseract-OCR\tesseract.exe",
             r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"]
    for p in paths:
        if p and Path(p).is_file():
            return p
    from shutil import which
    return which("tesseract")

def load_settings():
    try:
        stored = json.loads(SETTINGS_PATH.read_text(encoding="utf8"))
    except (OSError, ValueError):
        stored = {}
    data = DEFAULTS.copy()
    data.update({k: v for k, v in stored.items() if k in data})
    data["phrases"] = {**DEFAULT_EVENTS, **data.get("phrases", {})}
    return data

def save_settings(data):
    APP_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(data, indent=2), encoding="utf8")

def running_game():
    for proc in psutil.process_iter(["name"]):
        try:
            if (proc.info["name"] or "").lower() == "client.exe":
                return True
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            pass
    return False

def rect_string(r):
    return "Not calibrated" if not r else f'{r["left"]}, {r["top"]}  •  {r["width"]}×{r["height"]}'

def get_ocr_image(sct, rect, counter=False):
    raw = sct.grab(rect)
    im = Image.frombytes("RGB", raw.size, raw.rgb)
    im = ImageOps.grayscale(im)
    im = ImageEnhance.Contrast(im).enhance(2.3)
    im = ImageOps.autocontrast(im)
    if counter:
        im = im.resize((im.width * 4, im.height * 4), Image.Resampling.LANCZOS)
        im = im.filter(ImageFilter.SHARPEN)
        im = im.point(lambda x: 255 if x > 125 else 0)
    else:
        im = im.resize((im.width * 2, im.height * 2), Image.Resampling.LANCZOS)
    return im

def read_counter(sct, region):
    image = get_ocr_image(sct, region, counter=True)
    config = "--psm 7 -c tessedit_char_whitelist=0123456789"
    text = pytesseract.image_to_string(image, config=config).strip()
    numbers = re.findall(r"\d+", text)
    if not numbers:
        # Different HUDs can render white digits on dark or vice versa.
        flipped = ImageOps.invert(image)
        numbers = re.findall(r"\d+", pytesseract.image_to_string(flipped, config=config))
    if not numbers:
        return None
    val = int(numbers[0])
    return val if 0 <= val <= 99 else None

def read_message(sct, region):
    image = get_ocr_image(sct, region)
    return "\n".join(" ".join(line.split()) for line in pytesseract.image_to_string(image, config="--psm 6").lower().splitlines() if line.strip())

class AudioEngine:
    def __init__(self, events):
        self.events = events
        self.q = queue.Queue()
        self.ready = threading.Event()
        self.worker = threading.Thread(target=self._loop, daemon=True)
        self.worker.start()

    def say(self, phrase, sound_path, volume):
        self.q.put((phrase, sound_path, volume))

    def _loop(self):
        import pyttsx3
        voice_engine = None
        try:
            if pygame:
                pygame.mixer.init()
        except Exception as exc:
            self.events.put(("log", f"Audio mixer unavailable: {exc}"))
        try:
            voice_engine = pyttsx3.init("sapi5") if sys.platform == "win32" else pyttsx3.init()
            voice_engine.setProperty("rate", 175)
        except Exception as exc:
            self.events.put(("log", f"Voice unavailable: {exc}"))
        self.ready.set()
        while True:
            job = self.q.get()
            if job is None:
                break
            phrase, sound, volume = job
            try:
                if sound and Path(sound).is_file() and pygame and pygame.mixer.get_init():
                    pygame.mixer.music.load(sound)
                    pygame.mixer.music.set_volume(float(volume))
                    pygame.mixer.music.play()
                    while pygame.mixer.music.get_busy():
                        time.sleep(0.08)
                elif voice_engine:
                    voice_engine.setProperty("volume", float(volume))
                    voice_engine.say(phrase)
                    voice_engine.runAndWait()
                else:
                    import winsound
                    winsound.MessageBeep()
            except Exception as exc:
                self.events.put(("log", f"Sound error: {exc}"))

class ScreenSelector(tk.Toplevel):
    def __init__(self, parent, label, done):
        super().__init__(parent)
        self.done = done
        self.title("Select " + label)
        self.attributes("-fullscreen", True)
        self.attributes("-topmost", True)
        self.attributes("-alpha", 0.36)
        self.configure(bg="#111927")
        self.canvas = tk.Canvas(self, bg="#111927", cursor="cross", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.create_text(self.winfo_screenwidth() // 2, 55,
                                text=f"Drag over {label}  •  ESC to cancel", fill="white",
                                font=("Segoe UI", 22, "bold"))
        self.start = None
        self.outline = None
        self.canvas.bind("<ButtonPress-1>", self.begin)
        self.canvas.bind("<B1-Motion>", self.move)
        self.canvas.bind("<ButtonRelease-1>", self.finish)
        self.bind("<Escape>", lambda e: self.destroy())
        self.focus_force()

    def begin(self, e):
        self.start = (e.x, e.y)
        if self.outline:
            self.canvas.delete(self.outline)
        self.outline = self.canvas.create_rectangle(e.x, e.y, e.x, e.y, outline="#47ecb2", width=4, fill="")

    def move(self, e):
        if self.start:
            self.canvas.coords(self.outline, *self.start, e.x, e.y)

    def finish(self, e):
        if not self.start:
            return
        x1, y1 = self.start
        x2, y2 = e.x, e.y
        left, top = min(x1, x2), min(y1, y2)
        width, height = abs(x2 - x1), abs(y2 - y1)
        self.destroy()
        if width >= 16 and height >= 12:
            self.done({"left": left, "top": top, "width": width, "height": height})

class KillDetector:
    """Kill-feed-only scanner: separately calibrated attacker/victim name columns."""
    def __init__(self, settings, emit):
        self.settings = settings.copy()
        self.emit = emit
        self.stop_event = threading.Event()
        self.thread = None
        self.streak = 0
        self.seen = {}
        self.last_game_check = 0.0
        self.game_running = False

    def start(self):
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()

    @staticmethod
    def name_present(line, username):
        # Normalize spacing and punctuation while avoiding partial-name matches.
        norm = lambda x: re.sub(r"[^a-z0-9]+", "", x.casefold())
        tokens = re.findall(r"[a-z0-9_]+", line.casefold())
        target = norm(username)
        return bool(target) and (any(norm(t) == target for t in tokens) or
                                  (len(target) >= 4 and target in norm(line)))

    def _loop(self):
        cfg = self.settings
        tess = find_tesseract(cfg["tesseract"])
        if not tess:
            self.emit(("error", "Tesseract OCR unavailable. Open Settings and test/install OCR."))
            return
        pytesseract.pytesseract.tesseract_cmd = tess
        bundled_data = asset_path("tesseract/tessdata")
        if bundled_data.is_dir() and Path(tess).resolve() == asset_path("tesseract/tesseract.exe").resolve():
            os.environ["TESSDATA_PREFIX"] = str(bundled_data)
        username = cfg["player_name"].strip()
        self.emit(("log", "Kill-feed scanner started for " + username + ". Watching attacker and victim zones."))
        with mss.mss() as sct:
            while not self.stop_event.is_set():
                now = time.monotonic()
                if cfg["requires_game"]:
                    if now - self.last_game_check >= 2:
                        self.game_running = running_game()
                        self.last_game_check = now
                    if not self.game_running:
                        self.stop_event.wait(0.4)
                        continue
                try:
                    attacker_text = read_message(sct, cfg["killer_region"])
                    victim_text = read_message(sct, cfg["victim_region"])
                    self.emit(("ocr", "ATTACKER: " + (attacker_text[:65] or "—") +
                                     " | VICTIM: " + (victim_text[:65] or "—")))
                    # Evaluate individual visible feed rows; only announce new rows.
                    events = []
                    for side, content in (("kill", attacker_text), ("death", victim_text)):
                        for line in content.splitlines():
                            line = " ".join(line.split())
                            if line and self.name_present(line, username):
                                events.append((side, line))
                    # Avoid ambiguous detections when the name is in both calibrated zones.
                    active_sides = {side for side, _ in events}
                    if len(active_sides) == 1:
                        for side, line in events:
                            key = (side, line)
                            if key not in self.seen:
                                self.seen[key] = now
                                if side == "death":
                                    self.streak = 0
                                    self.emit(("streak", 0))
                                    self.emit(("log", "Death feed matched: " + line[:100]))
                                else:
                                    self.streak += 1
                                    self.emit(("kill", (self.streak, 1)))
                                    self.emit(("log", "Kill feed matched: " + line[:100]))
                    else:
                        if events:
                            self.emit(("log", "Ambiguous feed: name on both sides; ignored."))
                    # Refresh visible rows, and re-arm only after a row disappears.
                    active_keys = set(events) if len(active_sides) == 1 else set()
                    for key in active_keys:
                        if key in self.seen:
                            self.seen[key] = now
                    for key, when in list(self.seen.items()):
                        if key not in active_keys and now - when >= 3:
                            del self.seen[key]
                except Exception as exc:
                    self.emit(("log", f"Kill-feed scan error: {exc}"))
                self.stop_event.wait(max(0.2, float(cfg["poll_interval"])))
        self.emit(("log", "Kill-feed scanner stopped."))


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("760x760")
        self.minsize(640, 650)
        self.configure(bg="#101722")
        self.settings = load_settings()
        self.messages = queue.Queue()
        self.audio = AudioEngine(self.messages)
        self.detector = None
        self.state_str = tk.StringVar(value="IDLE")
        self.counter_str = tk.StringVar(value="—")
        self.streak_str = tk.StringVar(value="0")
        self.ocr_str = tk.StringVar(value="—")
        self.keywords_str = tk.StringVar(value=self.settings["keywords"])
        self.death_str = tk.StringVar(value=self.settings["death_keywords"])
        self.requires_game_var = tk.BooleanVar(value=self.settings["requires_game"])
        self.counter_first_var = tk.BooleanVar(value=False)
        self.player_name_var = tk.StringVar(value=self.settings.get("player_name", ""))
        self.volume_var = tk.DoubleVar(value=self.settings["volume"])
        self.tess_var = tk.StringVar(value=self.settings["tesseract"])
        self.ocr_status_var = tk.StringVar(value="Checking OCR engine...")
        self.auto_update_var = tk.BooleanVar(value=self.settings.get("auto_update", True))
        self.update_status_var = tk.StringVar(value=f"VERSION {VERSION}  ·  UPDATE CHECK PENDING")
        self.update_busy = False
        self.pending_update = None
        self._build()
        self.after(300, self.refresh_ocr_status)
        self.after(2500, self.check_updates_silently)
        self.after(120, self.poll)
        self.protocol("WM_DELETE_WINDOW", self.close)

    def _build(self):
        self.palette = {"bg": "#090e19", "surface": "#10192a", "raised": "#18243a",
                        "edge": "#2a3950", "text": "#f2f5ff", "muted": "#8a9cb5",
                        "accent": "#6ce9db", "purple": "#af97ff", "warning": "#f8c47a"}
        c = self.palette
        self.configure(bg=c["bg"])
        self.geometry("1210x825")
        self.minsize(970, 690)
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TFrame", background=c["bg"])
        style.configure("Panel.TFrame", background=c["surface"])
        style.configure("TLabel", background=c["bg"], foreground=c["text"], font=("Segoe UI", 10))
        style.configure("Panel.TLabel", background=c["surface"], foreground=c["text"], font=("Segoe UI", 10))
        style.configure("Muted.TLabel", background=c["bg"], foreground=c["muted"], font=("Segoe UI", 10))
        style.configure("TEntry", fieldbackground=c["raised"], foreground=c["text"], insertcolor=c["text"], bordercolor=c["edge"], lightcolor=c["accent"], padding=8)
        style.configure("TButton", background=c["raised"], foreground=c["text"], borderwidth=0, padding=(15, 10), font=("Segoe UI", 10, "bold"))
        style.map("TButton", background=[("active", "#293c58"), ("pressed", "#355071")], foreground=[("disabled", c["muted"])])
        style.configure("Accent.TButton", background=c["accent"], foreground="#0c1b27", padding=(22, 12), font=("Segoe UI", 11, "bold"))
        style.map("Accent.TButton", background=[("active", "#a8fff2"), ("pressed", "#4dc5ba")])
        style.configure("TCheckbutton", background=c["surface"], foreground=c["text"], font=("Segoe UI", 10))
        style.map("TCheckbutton", background=[("active", c["surface"])])
        style.configure("TScale", background=c["surface"], troughcolor=c["raised"])
        style.configure("TNotebook", background=c["bg"], borderwidth=0, tabmargins=0)
        style.configure("TNotebook.Tab", background=c["surface"], foreground=c["muted"], padding=(26, 13), borderwidth=0, font=("Segoe UI", 10, "bold"))
        style.map("TNotebook.Tab", background=[("selected", c["raised"]), ("active", "#203149")], foreground=[("selected", c["accent"]), ("active", c["text"])])
        # Distinct app shell: left navigation, large content workspace, action dock.
        self.configure(bg="#080d16")
        root = tk.Frame(self, bg="#080d16")
        root.pack(fill="both", expand=True)
        sidebar = tk.Frame(root, bg="#101a2b", width=240)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        brand = tk.Frame(sidebar, bg="#101a2b")
        brand.pack(fill="x", padx=22, pady=(32, 25))
        tk.Label(brand, text="◉  SG0HANN", bg="#101a2b", fg=c["accent"], font=("Segoe UI", 13, "bold")).pack(anchor="w")
        tk.Label(brand, text="KILL ANNOUNCER", bg="#101a2b", fg="#ffffff", font=("Segoe UI", 18, "bold")).pack(anchor="w", pady=(9, 3))
        tk.Label(brand, text="CREATIVE DESTRUCTION", bg="#101a2b", fg=c["muted"], font=("Segoe UI", 9)).pack(anchor="w")
        tk.Frame(sidebar, bg="#28364b", height=1).pack(fill="x", padx=18, pady=(0, 25))
        tk.Label(sidebar, text="WORKSPACE", bg="#101a2b", fg="#7289a8", font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=23, pady=(0, 11))
        workspace = tk.Frame(root, bg=c["bg"])
        workspace.pack(side="left", fill="both", expand=True, padx=28, pady=22)
        top = tk.Frame(workspace, bg=c["bg"])
        top.pack(fill="x", pady=(0, 18))
        tk.Label(top, text="COMMAND CENTER", bg=c["bg"], fg=c["text"], font=("Segoe UI", 23, "bold")).pack(anchor="w")
        tk.Label(top, text="Fine-tune your highlights. Hear every elimination.", bg=c["bg"], fg=c["muted"], font=("Segoe UI", 10)).pack(anchor="w", pady=(3, 0))
        version = tk.Frame(top, bg=c["bg"])
        version.place(relx=1.0, x=0, y=2, anchor="ne")
        tk.Label(version, text=f"VERSION {VERSION}", bg=c["raised"], fg=c["accent"], font=("Segoe UI", 9, "bold"), padx=13, pady=8).pack(anchor="e")
        stats = tk.Frame(workspace, bg=c["bg"])
        stats.pack(fill="x", pady=(8, 20))
        for i, (label, value, fg, icon) in enumerate([
            ("DETECTOR", self.state_str, c["accent"], "◉"),
            ("KILLS", self.counter_str, c["text"], "⌖"),
            ("CURRENT STREAK", self.streak_str, c["purple"], "⚡")]):
            panel = tk.Frame(stats, bg=c["surface"], highlightbackground=c["edge"], highlightthickness=1)
            panel.grid(row=0, column=i, sticky="nsew", padx=(0, 12) if i < 2 else 0)
            stats.columnconfigure(i, weight=1, uniform="stats")
            tk.Label(panel, text=f"{icon}   {label}", bg=c["surface"], fg=c["muted"], font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=18, pady=(14, 5))
            tk.Label(panel, textvariable=value, bg=c["surface"], fg=fg, font=("Segoe UI", 24, "bold")).pack(anchor="w", padx=18, pady=(0, 15))
        style.configure("Invisible.TNotebook", background=c["bg"], borderwidth=0, tabmargins=0)
        style.layout("Invisible.TNotebook.Tab", [])
        nav = ttk.Notebook(workspace, style="Invisible.TNotebook")
        nav.pack(fill="both", expand=True)
        tab = tk.Frame(nav, bg=c["bg"])
        sound_tab = tk.Frame(nav, bg=c["bg"])
        settings_tab = tk.Frame(nav, bg=c["bg"])
        nav.add(tab, text="HUD detection")
        nav.add(sound_tab, text="Announcer sounds")
        nav.add(settings_tab, text="Settings")
        self._nav_buttons = []
        def select_page(index):
            nav.select(index)
            for i, btn in enumerate(self._nav_buttons):
                btn.configure(bg="#253c55" if i == index else "#101a2b", fg=c["accent"] if i == index else "#bac9dd")
        for i, name in enumerate(["◉   LIVE DETECTION", "♫   ANNOUNCER LIBRARY", "⚙   SETTINGS"]):
            btn = tk.Button(sidebar, text=name, anchor="w", relief="flat", bd=0, cursor="hand2", bg="#101a2b", fg="#bac9dd", activebackground="#253c55", activeforeground=c["accent"], padx=20, pady=15, font=("Segoe UI", 10, "bold"), command=lambda idx=i: select_page(idx))
            btn.pack(fill="x", padx=12, pady=4)
            self._nav_buttons.append(btn)
        tk.Frame(sidebar, bg="#28364b", height=1).pack(fill="x", padx=18, pady=(30, 20))
        tk.Label(sidebar, text="APP UPDATES", bg="#101a2b", fg="#7289a8", font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=23)
        tk.Label(sidebar, textvariable=self.update_status_var, bg="#101a2b", fg=c["accent"], font=("Segoe UI", 9), wraplength=190, justify="left").pack(anchor="w", padx=23, pady=(10, 14))
        tk.Button(sidebar, text="↻   CHECK FOR UPDATES", command=self.check_updates, bg="#294357", fg="#bafbf2", activebackground="#345b70", activeforeground="white", relief="flat", bd=0, padx=14, pady=12, cursor="hand2", font=("Segoe UI", 9, "bold")).pack(fill="x", padx=16)
        tk.Label(sidebar, text="Made by sg0hann", bg="#101a2b", fg="#7289a8", font=("Segoe UI", 9)).pack(side="bottom", anchor="w", padx=22, pady=24)
        self._detection_tab(tab)
        self._sounds_tab(sound_tab)
        self._settings_tab(settings_tab)
        select_page(0)
        bottom = tk.Frame(workspace, bg=c["bg"])
        bottom.pack(fill="x", pady=(14, 0))
        self.start_btn = ttk.Button(bottom, text="▶   START DETECTION", style="Accent.TButton", command=self.toggle)
        self.start_btn.pack(side="left", fill="x", expand=True, padx=(0, 12))
        ttk.Button(bottom, text="SAVE SETTINGS", command=self.save).pack(side="right")
    def _heading(self, parent, label, description):
        c = self.palette
        tk.Label(parent, text=label, bg=c["bg"], fg=c["text"], font=("Segoe UI", 16, "bold")).pack(anchor="w", pady=(6, 3))
        tk.Label(parent, text=description, bg=c["bg"], fg=c["muted"], font=("Segoe UI", 10), wraplength=700, justify="left").pack(anchor="w", pady=(0, 17))

    def _detection_tab(self, tab):
        c = self.palette
        self._heading(tab, "KILL FEED DETECTION", "No kill counter needed. Select two areas inside your kill feed: the ATTACKER names and the VICTIM names.")
        name_row = tk.Frame(tab, bg=c["surface"], padx=18, pady=14)
        name_row.pack(fill="x", pady=(0, 12))
        tk.Label(name_row, text="YOUR EXACT IN-GAME NAME", bg=c["surface"], fg=c["text"], font=("Segoe UI", 10, "bold")).pack(anchor="w")
        ttk.Entry(name_row, textvariable=self.player_name_var, width=38).pack(fill="x", pady=(8, 0))
        tk.Label(tab, text="Example:  [ YOU ]  weapon  [ ENEMY ] → kill   •   [ ENEMY ]  weapon  [ YOU ] → death",
                 bg=c["bg"], fg=c["accent"], font=("Segoe UI", 10)).pack(anchor="w", pady=(0, 12))
        self.region_labels = {}
        for index, (key, label, help_text) in enumerate([
            ("killer_region", "LEFT — ATTACKER NAMES", "Select the left-side name column in the kill feed only."),
            ("victim_region", "RIGHT — VICTIM NAMES", "Select the right-side name column in the same feed only.")]):
            panel = tk.Frame(tab, bg=c["surface"], padx=16, pady=13, highlightbackground=c["edge"], highlightthickness=1)
            panel.pack(fill="x", pady=(0, 10))
            tk.Label(panel, text=label, bg=c["surface"], fg=c["text"], font=("Segoe UI", 10, "bold")).pack(anchor="w")
            tk.Label(panel, text=help_text, bg=c["surface"], fg=c["muted"]).pack(anchor="w", pady=(3, 7))
            value = tk.StringVar(value=rect_string(self.settings.get(key)))
            self.region_labels[key] = value
            tk.Label(panel, textvariable=value, bg=c["surface"], fg=c["accent"]).pack(side="left")
            ttk.Button(panel, text="SELECT AREA", command=lambda k=key, n=label: self.select_region(k, n)).pack(side="right")
        tk.Label(tab, text="LIVE OCR — verify your name is read in the correct column", bg=c["bg"], fg=c["muted"], font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(12, 6))
        tk.Label(tab, textvariable=self.ocr_str, bg=c["surface"], fg=c["accent"], anchor="w", padx=14, pady=12, wraplength=790).pack(fill="x")
        tk.Label(tab, text="ACTIVITY LOG", bg=c["bg"], fg=c["muted"], font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(16, 7))
        self.logs = tk.Text(tab, height=6, state="disabled", bg=c["surface"], fg="#c6d6eb", relief="flat", insertbackground=c["text"], font=("Consolas", 10), padx=12, pady=10)
        self.logs.pack(fill="both", expand=True)

    def _sounds_tab(self, tab):
        c = self.palette
        self._heading(tab, "YOUR ANNOUNCER COLLECTION", "Customize every announcement. Use a voice line or select your own MP3, WAV, or OGG file.")
        container = tk.Frame(tab, bg=c["bg"])
        container.pack(fill="both", expand=True)
        wrapper = tk.Canvas(container, bg=c["bg"], highlightthickness=0)
        scrollbar = ttk.Scrollbar(container, orient="vertical", command=wrapper.yview)
        inner = tk.Frame(wrapper, bg=c["bg"])
        window_id = wrapper.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda e: wrapper.configure(scrollregion=wrapper.bbox("all")))
        wrapper.bind("<Configure>", lambda e: wrapper.itemconfigure(window_id, width=e.width))
        wrapper.configure(yscrollcommand=scrollbar.set)
        wrapper.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        def wheel(event):
            wrapper.yview_scroll(-int(event.delta / 120), "units")
        wrapper.bind("<MouseWheel>", wheel)
        inner.bind("<MouseWheel>", wheel)
        self.phrase_entries = {}
        self.sound_labels = {}
        for number in DEFAULT_EVENTS:
            row = tk.Frame(inner, bg=c["surface"], highlightthickness=1, highlightbackground=c["edge"])
            row.pack(fill="x", pady=(0, 8), padx=(0, 8))
            badge = tk.Frame(row, bg=c["raised"], width=60)
            badge.pack(side="left", fill="y", padx=(0, 13))
            tk.Label(badge, text=number, bg=c["raised"], fg=c["accent"], font=("Segoe UI", 19, "bold"), padx=14, pady=12).pack()
            copy = tk.Frame(row, bg=c["surface"])
            copy.pack(side="left", fill="x", expand=True, pady=9)
            phrase = tk.StringVar(value=self.settings["phrases"].get(number, DEFAULT_EVENTS[number]))
            self.phrase_entries[number] = phrase
            ttk.Entry(copy, textvariable=phrase, width=25).pack(anchor="w", fill="x", padx=(0, 10))
            lab = tk.StringVar(value=Path(self.settings["sounds"].get(number, "")).name or "Windows voice · No audio file")
            self.sound_labels[number] = lab
            tk.Label(copy, textvariable=lab, bg=c["surface"], fg=c["muted"], font=("Segoe UI", 9), anchor="w").pack(anchor="w", pady=(5, 0))
            controls = tk.Frame(row, bg=c["surface"])
            controls.pack(side="right", padx=12)
            ttk.Button(controls, text="FILE", command=lambda k=number: self.choose_audio(k)).pack(side="left", padx=3)
            ttk.Button(controls, text="▶", command=lambda k=number: self.test_sound(k)).pack(side="left", padx=3)
            ttk.Button(controls, text="×", command=lambda k=number: self.clear_audio(k)).pack(side="left", padx=3)

    def _settings_tab(self, tab):
        c = self.palette
        self._heading(tab, "PREFERENCES & UPDATES", "Configure screen recognition and your announcer experience.")
        panel = tk.Frame(tab, bg=c["surface"], highlightthickness=1, highlightbackground=c["edge"])
        panel.pack(fill="x", pady=(0, 12))
        body = tk.Frame(panel, bg=c["surface"])
        body.pack(fill="x", padx=20, pady=17)
        tk.Label(body, text="SCREEN TEXT RECOGNITION", bg=c["surface"], fg=c["text"], font=("Segoe UI", 12, "bold")).pack(anchor="w")
        tk.Label(body, text="Tesseract reads the on-screen kill count and elimination banner. It doesn't modify game files.", bg=c["surface"], fg=c["muted"], font=("Segoe UI", 9), wraplength=760, justify="left").pack(anchor="w", pady=(5, 12))
        ocr_card = tk.Frame(body, bg=c["raised"], padx=12, pady=11)
        ocr_card.pack(fill="x", pady=(0, 13))
        self.ocr_indicator = tk.Label(ocr_card, textvariable=self.ocr_status_var, bg=c["raised"], fg=c["warning"], font=("Segoe UI", 10, "bold"), anchor="w")
        self.ocr_indicator.pack(fill="x")
        tk.Label(body, text="TESSERACT EXECUTABLE (OPTIONAL)", bg=c["surface"], fg=c["muted"], font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(0, 6))
        line = tk.Frame(body, bg=c["surface"])
        line.pack(fill="x", pady=(0, 7))
        ttk.Entry(line, textvariable=self.tess_var).pack(side="left", fill="x", expand=True)
        ttk.Button(line, text="BROWSE", command=self.choose_tesseract).pack(side="left", padx=(8, 0))
        ttk.Button(line, text="AUTO DETECT", command=self.auto_detect_tesseract).pack(side="left", padx=(8, 0))
        help_line = tk.Frame(body, bg=c["surface"])
        help_line.pack(fill="x", pady=(0, 16))
        tk.Label(help_line, text="Leave blank to use the built-in OCR engine, when available.", bg=c["surface"], fg=c["muted"], font=("Segoe UI", 9)).pack(side="left")
        ttk.Button(help_line, text="TEST OCR", command=self.test_tesseract).pack(side="right")
        ttk.Button(help_line, text="⬇ INSTALL TESSERACT OCR", command=self.install_tesseract).pack(side="right", padx=8)
        ttk.Separator(body, orient="horizontal").pack(fill="x", pady=(1, 16))
        tk.Label(body, text="DETECTION WORDS", bg=c["surface"], fg=c["text"], font=("Segoe UI", 12, "bold")).pack(anchor="w", pady=(0, 8))
        for title, desc, variable in [
            ("ELIMINATION KEYWORDS", "Words displayed when YOU eliminate another player (comma-separated).", self.keywords_str),
            ("DEATH KEYWORDS", "Words displayed when you die; used to reset your streak (comma-separated).", self.death_str),
        ]:
            tk.Label(body, text=title, bg=c["surface"], fg=c["muted"], font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(2, 4))
            ttk.Entry(body, textvariable=variable).pack(fill="x")
            tk.Label(body, text=desc, bg=c["surface"], fg=c["muted"], font=("Segoe UI", 9), wraplength=740, justify="left").pack(anchor="w", pady=(4, 13))
        ttk.Checkbutton(body, text="Only scan when Creative Destruction (client.exe) is running", variable=self.requires_game_var).pack(anchor="w", pady=(0, 8))
        tk.Label(body, text="APPLICATION UPDATES", bg=c["surface"], fg=c["text"], font=("Segoe UI", 12, "bold")).pack(anchor="w", pady=(14, 6))
        ttk.Checkbutton(body, text="Automatically check for and install updates when the detector is idle", variable=self.auto_update_var, command=self.save).pack(anchor="w", pady=(0, 5))
        tk.Label(body, text="Checks on startup. If a match is running, the upgrade waits until you stop detection. Your sounds and settings stay intact.", bg=c["surface"], fg=c["muted"], font=("Segoe UI", 9), wraplength=730, justify="left").pack(anchor="w", pady=(0, 10))
        ttk.Button(body, text="↻  CHECK FOR UPDATES NOW", command=self.check_updates).pack(anchor="w", pady=(0, 12))
        tk.Label(body, text="ANNOUNCER VOLUME", bg=c["surface"], fg=c["muted"], font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(4, 8))
        ttk.Scale(body, variable=self.volume_var, from_=0, to=1, orient="horizontal").pack(fill="x")
        tk.Label(tab, text="TIP  ·  If you change your screen resolution or HUD scale, recalibrate both regions on the Detection tab.", bg=c["bg"], fg=c["muted"], font=("Segoe UI", 9), wraplength=770, justify="left").pack(anchor="w", pady=8)

    def refresh_ocr_status(self):
        path = find_tesseract(self.tess_var.get().strip())
        if path:
            self.ocr_status_var.set("● OCR READY  •  " + ("Bundled engine" if "_MEIPASS" in dir(sys) and Path(path).is_relative_to(Path(sys._MEIPASS)) else Path(path).name))
            self.ocr_indicator.configure(fg=self.palette["accent"])
        else:
            self.ocr_status_var.set("● OCR NOT FOUND  •  Install Tesseract or select tesseract.exe")
            self.ocr_indicator.configure(fg=self.palette["warning"])

    def auto_detect_tesseract(self):
        self.tess_var.set("")
        self.refresh_ocr_status()
        if find_tesseract(""):
            messagebox.showinfo("OCR available", "Tesseract was detected automatically. You don't need to set a path.")
        else:
            messagebox.showwarning("OCR missing", "Tesseract could not be located. Use INSTALL GUIDE for setup instructions.")

    def test_tesseract(self):
        path = find_tesseract(self.tess_var.get().strip())
        if not path:
            self.refresh_ocr_status()
            messagebox.showerror("OCR missing", "No Tesseract executable was found. Install OCR or browse to tesseract.exe.")
            return
        try:
            result = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=5, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if result.returncode != 0:
                raise RuntimeError((result.stderr or result.stdout or "Unknown error")[:300])
            pytesseract.pytesseract.tesseract_cmd = path
            from PIL import ImageDraw
            sample = Image.new("RGB", (450, 100), "white")
            ImageDraw.Draw(sample).text((25, 25), "KILLS 12", fill="black", stroke_width=0)
            detected = pytesseract.image_to_string(sample, config="--psm 6")
            if "12" not in detected:
                raise RuntimeError("The OCR engine started, but did not recognize the sample. Check English language data.")
            self.refresh_ocr_status()
            messagebox.showinfo("OCR test passed", "Tesseract is ready!\n\nEngine: " + path + "\nSample reading: " + detected.strip())
        except Exception as exc:
            self.ocr_status_var.set("● OCR ERROR  •  Test failed")
            self.ocr_indicator.configure(fg=self.palette["warning"])
            messagebox.showerror("OCR test failed", str(exc))

    def install_tesseract(self):
        if sys.platform != "win32":
            messagebox.showerror("Windows only", "The OCR installer is for Windows.")
            return
        if find_tesseract(self.tess_var.get().strip()):
            if not messagebox.askyesno("OCR already available", "Tesseract is already available. Download the external installer anyway?"):
                self.refresh_ocr_status()
                return
        elif not messagebox.askyesno("Install Tesseract OCR", "Download the Windows OCR installer from UB Mannheim?\n\nAfter downloading, Windows will open the installer so you can confirm installation. Leave the installation directory at its default location."):
            return
        self.ocr_status_var.set("● FETCHING OCR INSTALLER  •  Connecting...")
        def download():
            try:
                class InstallerLinks(HTMLParser):
                    def __init__(self):
                        super().__init__()
                        self.links = []
                    def handle_starttag(self, tag, attrs):
                        if tag == "a":
                            href = dict(attrs).get("href", "")
                            if re.fullmatch(r"tesseract-ocr-w64-setup-(?:v)?5[^/]*\.exe", href, re.I):
                                self.links.append(href)
                base = "https://digi.bib.uni-mannheim.de/tesseract/"
                request = urllib.request.Request(base, headers={"User-Agent": "CDKillAnnouncer-OCR-Setup/1.0"})
                with urllib.request.urlopen(request, timeout=20) as response:
                    if urlparse(response.geturl()).hostname != "digi.bib.uni-mannheim.de":
                        raise RuntimeError("Unexpected download source")
                    html = response.read(1_000_000).decode("utf-8", "replace")
                parser = InstallerLinks()
                parser.feed(html)
                if not parser.links:
                    raise RuntimeError("No supported Windows Tesseract installer was found")
                def version_key(name):
                    nums = [int(x) for x in re.findall(r"\d+", name.split("setup-")[-1])]
                    return tuple((nums + [0] * 6)[:6])
                name = max(parser.links, key=version_key)
                url = urljoin(base, name)
                path = Path(tempfile.mkdtemp(prefix="cd-kill-ocr-")) / "Tesseract-OCR-Setup.exe"
                req = urllib.request.Request(url, headers={"User-Agent": "CDKillAnnouncer-OCR-Setup/1.0"})
                with urllib.request.urlopen(req, timeout=45) as response, path.open("wb") as stream:
                    if urlparse(response.geturl()).hostname != "digi.bib.uni-mannheim.de":
                        raise RuntimeError("Unexpected installer redirect")
                    total = 0
                    while chunk := response.read(1024 * 1024):
                        total += len(chunk)
                        if total > 200 * 1024 * 1024:
                            raise RuntimeError("Download exceeds the allowed size")
                        stream.write(chunk)
                with path.open("rb") as stream:
                    valid_exe = stream.read(2) == b"MZ"
                if total < 1_000_000 or not valid_exe:
                    raise RuntimeError("Downloaded installer is invalid")
                self.after(0, lambda: self._finish_tesseract_download(str(path), name))
            except Exception as exc:
                detail = str(exc)
                self.after(0, lambda: (self.refresh_ocr_status(), messagebox.showerror("OCR download failed", detail)))
        threading.Thread(target=download, daemon=True).start()

    def _finish_tesseract_download(self, path, filename):
        self.ocr_status_var.set("● OCR DOWNLOADED  •  Installer ready")
        if messagebox.askyesno("Install OCR", f"Downloaded {filename} from UB Mannheim.\n\nLaunch the installer now? Windows may request administrator approval.\n\nChoose the default install folder and include English language data."):
            try:
                os.startfile(path)
                self.after(2000, self.refresh_ocr_status)
            except OSError as exc:
                messagebox.showerror("Installer could not launch", str(exc))

    def open_tesseract_guide(self):
        webbrowser.open("https://github.com/UB-Mannheim/tesseract/wiki")

    def select_region(self, key, label):
        if self.detector:
            messagebox.showinfo("Stop first", "Stop detection before changing regions.")
            return
        self.withdraw()
        self.after(300, lambda: self._open_selector(key, label))

    def _open_selector(self, key, label):
        self.deiconify()
        self.update()
        self.withdraw()
        selector = ScreenSelector(self, label, lambda region: self.set_region(key, region))
        selector.bind("<Destroy>", lambda e: self.after(150, self.deiconify) if e.widget == selector else None, add="+")

    def set_region(self, key, region):
        self.settings[key] = region
        self.region_labels[key].set(rect_string(region))
        self.save()

    def choose_audio(self, key):
        file = filedialog.askopenfilename(filetypes=[("Audio", "*.wav *.mp3 *.ogg"), ("All files", "*.*")])
        if file:
            self.settings["sounds"][key] = file
            self.sound_labels[key].set(Path(file).name)
            self.save()

    def clear_audio(self, key):
        self.settings["sounds"].pop(key, None)
        self.sound_labels[key].set("Windows voice · No audio file")
        self.save()

    def test_sound(self, key):
        self.save()
        self.audio.say(self.settings["phrases"][key], self.settings["sounds"].get(key, ""), self.settings["volume"])

    def choose_tesseract(self):
        file = filedialog.askopenfilename(filetypes=[("Executable", "*.exe")])
        if file:
            self.tess_var.set(file)
            self.refresh_ocr_status()

    def save(self):
        self.settings["tesseract"] = self.tess_var.get().strip()
        self.settings["keywords"] = self.keywords_str.get().strip()
        self.settings["death_keywords"] = self.death_str.get().strip()
        self.settings["requires_game"] = self.requires_game_var.get()
        self.settings["detection_mode"] = "feed"
        self.settings["player_name"] = self.player_name_var.get().strip()
        self.settings["auto_update"] = self.auto_update_var.get()
        self.settings["volume"] = round(self.volume_var.get(), 2)
        self.settings["phrases"] = {k: v.get() for k, v in self.phrase_entries.items()}
        save_settings(self.settings)

    def toggle(self):
        if self.detector:
            self.detector.stop()
            self.detector = None
            self.state_str.set("IDLE")
            self.start_btn.configure(text="▶  START DETECTION")
            if self.pending_update and self.auto_update_var.get():
                update = self.pending_update
                self.pending_update = None
                self.after(250, lambda: self._start_install(*update))
            return
        self.save()
        if not self.settings.get("player_name") or not self.settings.get("killer_region") or not self.settings.get("victim_region"):
            messagebox.showwarning("Calibration needed", "Enter your exact player name and select both attacker and victim name areas in the kill feed.")
            return
        if not find_tesseract(self.settings["tesseract"]):
            messagebox.showerror("OCR missing", "Install Tesseract OCR first, or select its executable in Settings.")
            return
        self.detector = KillDetector(self.settings, self.messages.put)
        self.detector.start()
        self.state_str.set("RUNNING")
        self.start_btn.configure(text="■  STOP DETECTION")

    def log(self, content):
        self.logs.configure(state="normal")
        self.logs.insert("end", time.strftime("%H:%M:%S") + "  " + content + "\n")
        self.logs.see("end")
        self.logs.configure(state="disabled")

    def poll(self):
        try:
            while True:
                kind, value = self.messages.get_nowait()
                if kind == "counter":
                    self.counter_str.set("—" if value is None else str(value))
                elif kind == "ocr":
                    self.ocr_str.set(value)
                elif kind == "streak":
                    self.streak_str.set(str(value))
                elif kind == "kill":
                    streak, gained = value
                    self.streak_str.set(str(streak))
                    eligible = [int(k) for k in self.settings["phrases"] if int(k) <= streak]
                    if eligible:
                        tier = str(max(eligible))
                        self.audio.say(self.settings["phrases"][tier], self.settings["sounds"].get(tier, ""), self.settings["volume"])
                        self.log(f"Confirmed +{gained} kill(s) → {self.settings['phrases'][tier]} (streak {streak})")
                elif kind == "error":
                    self.log(value)
                    self.toggle() if self.detector else None
                    messagebox.showerror("Detector error", value)
                elif kind == "log":
                    self.log(value)
        except queue.Empty:
            pass
        self.after(120, self.poll)

    def check_updates_silently(self):
        self._check_updates(silent=True)

    def check_updates(self):
        self._check_updates(silent=False)

    def _check_updates(self, silent=False):
        if self.update_busy:
            if not silent:
                messagebox.showinfo("Updates", "An update check or installation is already running.")
            return
        self.update_busy = True
        self.update_status_var.set("CHECKING GITHUB RELEASES…")
        def worker():
            try:
                latest = get_latest()
                self.after(0, lambda: self._update_result(latest, silent))
            except Exception as error:
                msg = str(error)
                self.after(0, lambda: self._update_check_error(msg, silent))
        threading.Thread(target=worker, daemon=True).start()

    def _update_check_error(self, message, silent):
        self.update_busy = False
        self.update_status_var.set("UPDATE CHECK FAILED  ·  TRY AGAIN")
        if not silent:
            messagebox.showerror("Update check", message)

    def _update_result(self, latest, silent):
        self.update_busy = False
        if latest is None:
            self.update_status_var.set(f"VERSION {VERSION}  ·  UP TO DATE")
            if not silent:
                messagebox.showinfo("Updates", f"You're up to date (v{VERSION}).")
            return
        version, asset_url = latest
        self.update_status_var.set(f"NEW VERSION AVAILABLE  ·  {version}")
        if not getattr(sys, 'frozen', False):
            if not silent:
                messagebox.showinfo("Update available", f"{version} is available. Use the Windows release build to enable automatic installations.")
            return
        if silent:
            if not self.auto_update_var.get():
                return
            if self.detector:
                self.pending_update = (version, asset_url)
                self.update_status_var.set(f"{version} READY  ·  INSTALL AFTER MATCH")
                return
            self.pending_update = (version, asset_url)
            self.update_status_var.set(f"{version} AVAILABLE  ·  CLICK UPDATE")
            return
        if messagebox.askyesno("Update available", f"CD Kill Announcer {version} is available.\n\nDownload, install, and restart automatically?\nYour settings and sounds will be preserved."):
            self._start_install(version, asset_url)

    def _start_install(self, version, asset_url):
        if self.update_busy:
            return
        self.update_busy = True
        if self.detector:
            self.detector.stop()
            self.detector = None
        self.save()
        self.update_status_var.set(f"INSTALLING {version}  ·  DO NOT CLOSE")
        def worker():
            try:
                install_update(asset_url, lambda message: self.messages.put(("log", message)))
                self.after(0, self.close)
            except Exception as error:
                message = str(error)
                def failed():
                    self.update_busy = False
                    self.update_status_var.set("UPDATE FAILED  ·  RETRY")
                    messagebox.showerror("Update failed", message)
                self.after(0, failed)
        threading.Thread(target=worker, daemon=True).start()

    def close(self):
        if self.detector:
            self.detector.stop()
        self.save()
        self.destroy()

if __name__ == "__main__":
    App().mainloop()