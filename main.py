"""CD Kill Announcer - screen-only kill detection for Creative Destruction."""
import ctypes
import json
import os
import queue
import re
import sys
import threading
import time
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
    "tesseract": "", "keywords": "eliminated,killed,defeated,elimination",
    "death_keywords": "eliminated by,you died,you were eliminated",
    "volume": 0.9, "poll_interval": 0.40, "requires_game": True,
    "phrases": DEFAULT_EVENTS.copy(), "sounds": {}, "active": False,
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
    return " ".join(pytesseract.image_to_string(image, config="--psm 6").lower().split())

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
    def __init__(self, settings, emit):
        self.settings = settings.copy()
        self.emit = emit
        self.stop_event = threading.Event()
        self.thread = None
        self.base = None
        self.last_candidate = None
        self.candidate_hits = 0
        self.streak = 0
        self.last_message_time = 0.0
        self.last_death_time = 0.0
        self.last_announced_at = 0.0

    def start(self):
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()

    def _loop(self):
        cfg = self.settings
        tess = find_tesseract(cfg["tesseract"])
        if not tess:
            self.emit(("error", "Tesseract OCR not found. Install it or select tesseract.exe in Settings."))
            return
        pytesseract.pytesseract.tesseract_cmd = tess
        bundled_data = asset_path("tesseract/tessdata")
        if bundled_data.is_dir() and Path(tess).resolve() == asset_path("tesseract/tesseract.exe").resolve():
            os.environ["TESSDATA_PREFIX"] = str(bundled_data)
        keywords = [k.strip().lower() for k in cfg["keywords"].split(",") if k.strip()]
        death_words = [k.strip().lower() for k in cfg["death_keywords"].split(",") if k.strip()]
        last_game_check = 0.0
        game_is_running = False
        self.emit(("log", "Scanning. Start a match and keep the HUD visible."))
        with mss.mss() as sct:
            while not self.stop_event.is_set():
                now = time.monotonic()
                if cfg["requires_game"]:
                    if now - last_game_check > 2.0:
                        game_is_running = running_game()
                        last_game_check = now
                    if not game_is_running:
                        self.stop_event.wait(0.5)
                        continue
                try:
                    # Counter is the primary signal; on-screen message confirms it.
                    message = read_message(sct, cfg["message_region"])
                    self.emit(("ocr", message[:110] or "(empty)"))
                    if any(k in message for k in keywords):
                        self.last_message_time = time.monotonic()
                    if cfg.get("death_region"):
                        death_text = read_message(sct, cfg["death_region"])
                        if any(k in death_text for k in death_words) and now - self.last_death_time > 5:
                            self.streak = 0
                            self.last_death_time = now
                            self.emit(("log", "Death message detected — streak reset."))
                            self.emit(("streak", self.streak))
                    current = read_counter(sct, cfg["counter_region"])
                    self.emit(("counter", current))
                    if current is not None:
                        if self.last_candidate == current:
                            self.candidate_hits += 1
                        else:
                            self.last_candidate, self.candidate_hits = current, 1
                        if self.candidate_hits >= 2:
                            if self.base is None:
                                self.base = current
                                self.emit(("log", f"Counter baseline established: {current}"))
                            elif current < self.base:
                                self.base = current
                                self.streak = 0
                                self.emit(("streak", self.streak))
                                self.emit(("log", "Kill counter reset — streak reset."))
                            elif current > self.base:
                                count = min(10, current - self.base)
                                if now - self.last_message_time <= 2.5 and now - self.last_announced_at > 0.65:
                                    self.base = current
                                    self.streak += count
                                    self.last_announced_at = time.monotonic()
                                    self.emit(("kill", (self.streak, count)))
                                elif now - self.last_message_time > 2.5:
                                    # Wait briefly for the message: don't confirm a kill on counter OCR alone.
                                    self.emit(("pending", current))
                except Exception as exc:
                    self.emit(("log", f"Scan error: {exc}"))
                self.stop_event.wait(max(0.15, float(cfg["poll_interval"])))
        self.emit(("log", "Scanner stopped."))

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
        self.volume_var = tk.DoubleVar(value=self.settings["volume"])
        self.tess_var = tk.StringVar(value=self.settings["tesseract"])
        self._build()
        self.after(2500, self.check_updates_silently)
        self.after(120, self.poll)
        self.protocol("WM_DELETE_WINDOW", self.close)

    def _build(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TNotebook", background="#101722", borderwidth=0)
        style.configure("TNotebook.Tab", padding=(17, 10), font=("Segoe UI", 10))
        style.configure("TFrame", background="#101722")
        style.configure("TLabel", background="#101722", foreground="#e7edf6", font=("Segoe UI", 10))
        style.configure("TButton", padding=7, font=("Segoe UI", 10))
        header = tk.Frame(self, bg="#101722", padx=25, pady=18)
        header.pack(fill="x")
        tk.Label(header, text="CD KILL ANNOUNCER", font=("Segoe UI", 21, "bold"), bg="#101722", fg="#e7edf6").pack(anchor="w")
        tk.Label(header, text=f"sg0hann  /  v{VERSION}  /  Screen-only detection  /  No game file changes", bg="#101722", fg="#9eb0c5", font=("Segoe UI", 10)).pack(anchor="w")
        status = tk.Frame(self, bg="#172335", padx=20, pady=16)
        status.pack(fill="x", padx=25, pady=(0, 14))
        for label, var in [("STATUS", self.state_str), ("KILL COUNTER", self.counter_str), ("STREAK", self.streak_str)]:
            cell = tk.Frame(status, bg="#172335")
            cell.pack(side="left", expand=True, fill="x")
            tk.Label(cell, text=label, fg="#91a6c0", bg="#172335", font=("Segoe UI", 9)).pack()
            tk.Label(cell, textvariable=var, fg="#48eeb6", bg="#172335", font=("Segoe UI", 19, "bold")).pack()
        nav = ttk.Notebook(self)
        nav.pack(fill="both", expand=True, padx=25, pady=(0, 14))
        tab = ttk.Frame(nav, padding=16)
        sound_tab = ttk.Frame(nav, padding=16)
        settings_tab = ttk.Frame(nav, padding=16)
        nav.add(tab, text="Detection")
        nav.add(sound_tab, text="Sounds")
        nav.add(settings_tab, text="Settings")
        self._detection_tab(tab)
        self._sounds_tab(sound_tab)
        self._settings_tab(settings_tab)
        bottom = tk.Frame(self, bg="#101722")
        bottom.pack(fill="x", padx=25, pady=(0, 20))
        self.start_btn = ttk.Button(bottom, text="▶  START DETECTION", command=self.toggle)
        self.start_btn.pack(side="left", fill="x", expand=True, padx=(0, 8))
        ttk.Button(bottom, text="Save settings", command=self.save).pack(side="left", padx=(0, 8))
        ttk.Button(bottom, text="Check updates", command=self.check_updates).pack(side="left")

    def _detection_tab(self, tab):
        ttk.Label(tab, text="1. Calibrate the two HUD areas", font=("Segoe UI", 12, "bold")).pack(anchor="w", pady=(0, 8))
        ttk.Label(tab, text="Run Creative Destruction first. Select small tight boxes around the number and your kill message.", wraplength=640).pack(anchor="w", pady=(0, 10))
        self.region_labels = {}
        for key, name in [("counter_region", "Kill counter number"), ("message_region", "Elimination notification"), ("death_region", "Death notification (optional)")]:
            line = ttk.Frame(tab)
            line.pack(fill="x", pady=5)
            ttk.Button(line, text=f"Select {name}", width=30,
                       command=lambda k=key, n=name: self.select_region(k, n)).pack(side="left")
            value = tk.StringVar(value=rect_string(self.settings[key]))
            ttk.Label(line, textvariable=value).pack(side="left", padx=12)
            self.region_labels[key] = value
        ttk.Label(tab, text="2. Verify the OCR in a match", font=("Segoe UI", 12, "bold")).pack(anchor="w", pady=(22, 7))
        ttk.Label(tab, text="Recognized elimination text:").pack(anchor="w")
        ttk.Label(tab, textvariable=self.ocr_str, wraplength=630, foreground="#55daba").pack(anchor="w", pady=(3, 12))
        ttk.Label(tab, text="A kill requires an increased counter AND a nearby elimination message. Death reset needs its optional region.", wraplength=620).pack(anchor="w")
        ttk.Label(tab, text="Activity", font=("Segoe UI", 12, "bold")).pack(anchor="w", pady=(22, 6))
        self.logs = tk.Text(tab, height=7, state="disabled", bg="#172335", fg="#d3e4f0", relief="flat", font=("Consolas", 9))
        self.logs.pack(fill="both", expand=True)

    def _sounds_tab(self, tab):
        ttk.Label(tab, text="Announcer voice lines", font=("Segoe UI", 12, "bold")).pack(anchor="w", pady=(0, 8))
        ttk.Label(tab, text="Leave the file empty for Windows text-to-speech; add WAV/MP3/OGG for custom audio.", wraplength=620).pack(anchor="w", pady=(0, 13))
        wrapper = tk.Canvas(tab, highlightthickness=0, bg="#101722")
        scrollbar = ttk.Scrollbar(tab, orient="vertical", command=wrapper.yview)
        inner = ttk.Frame(wrapper)
        inner.bind("<Configure>", lambda e: wrapper.configure(scrollregion=wrapper.bbox("all")))
        wrapper.create_window((0, 0), window=inner, anchor="nw")
        wrapper.configure(yscrollcommand=scrollbar.set)
        wrapper.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.phrase_entries = {}
        self.sound_labels = {}
        for number in DEFAULT_EVENTS:
            r = ttk.Frame(inner)
            r.pack(fill="x", pady=4)
            ttk.Label(r, text=f"{number} kills", width=10).pack(side="left")
            phrase = tk.StringVar(value=self.settings["phrases"].get(number, DEFAULT_EVENTS[number]))
            self.phrase_entries[number] = phrase
            ttk.Entry(r, textvariable=phrase, width=23).pack(side="left", padx=5)
            ttk.Button(r, text="Choose audio", command=lambda k=number: self.choose_audio(k)).pack(side="left", padx=3)
            ttk.Button(r, text="Test", command=lambda k=number: self.test_sound(k)).pack(side="left")
            ttk.Button(r, text="Clear", command=lambda k=number: self.clear_audio(k)).pack(side="left", padx=3)
            lab = tk.StringVar(value=Path(self.settings["sounds"].get(number, "")).name or "TTS")
            self.sound_labels[number] = lab
            ttk.Label(inner, textvariable=lab, foreground="#95abc2").pack(anchor="w", padx=86)

    def _settings_tab(self, tab):
        ttk.Label(tab, text="Tesseract OCR executable", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        line = ttk.Frame(tab)
        line.pack(fill="x", pady=(5, 17))
        ttk.Entry(line, textvariable=self.tess_var).pack(side="left", fill="x", expand=True)
        ttk.Button(line, text="Browse", command=self.choose_tesseract).pack(side="left", padx=6)
        ttk.Label(tab, text="Elimination message keywords (comma-separated)").pack(anchor="w")
        ttk.Entry(tab, textvariable=self.keywords_str).pack(fill="x", pady=(5, 15))
        ttk.Label(tab, text="Death message keywords (comma-separated)").pack(anchor="w")
        ttk.Entry(tab, textvariable=self.death_str).pack(fill="x", pady=(5, 15))
        ttk.Checkbutton(tab, text="Only scan while client.exe is running", variable=self.requires_game_var).pack(anchor="w")
        ttk.Label(tab, text="Announcer volume").pack(anchor="w", pady=(17, 5))
        ttk.Scale(tab, variable=self.volume_var, from_=0, to=1, orient="horizontal").pack(fill="x")
        ttk.Label(tab, text="Calibration uses the primary monitor. Keep the game at the same resolution and HUD scale after selecting regions.", wraplength=600).pack(anchor="w", pady=(25, 0))

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
        self.sound_labels[key].set("TTS")
        self.save()

    def test_sound(self, key):
        self.save()
        self.audio.say(self.settings["phrases"][key], self.settings["sounds"].get(key, ""), self.settings["volume"])

    def choose_tesseract(self):
        file = filedialog.askopenfilename(filetypes=[("Executable", "*.exe")])
        if file:
            self.tess_var.set(file)

    def save(self):
        self.settings["tesseract"] = self.tess_var.get().strip()
        self.settings["keywords"] = self.keywords_str.get().strip()
        self.settings["death_keywords"] = self.death_str.get().strip()
        self.settings["requires_game"] = self.requires_game_var.get()
        self.settings["volume"] = round(self.volume_var.get(), 2)
        self.settings["phrases"] = {k: v.get() for k, v in self.phrase_entries.items()}
        save_settings(self.settings)

    def toggle(self):
        if self.detector:
            self.detector.stop()
            self.detector = None
            self.state_str.set("IDLE")
            self.start_btn.configure(text="▶  START DETECTION")
            return
        self.save()
        if not self.settings["counter_region"] or not self.settings["message_region"]:
            messagebox.showwarning("Calibration needed", "Select both kill counter and elimination message regions.")
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
        def worker():
            try:
                latest = get_latest()
                self.after(0, lambda: self._update_result(latest, silent))
            except Exception as error:
                if not silent:
                    msg = str(error)
                    self.after(0, lambda: messagebox.showerror("Update check", msg))
        threading.Thread(target=worker, daemon=True).start()

    def _update_result(self, latest, silent):
        if latest is None:
            if not silent:
                messagebox.showinfo("Updates", "You already have the latest version.")
            return
        version, asset_url = latest
        if not getattr(sys, 'frozen', False):
            if not silent:
                messagebox.showinfo("Update available", f"{version} is available. Install a release build to enable automatic updates.")
            return
        if not messagebox.askyesno("Update available", f"CD Kill Announcer {version} is available.\n\nDownload, install and restart automatically?\nYour settings and sounds will be preserved."):
            return
        if self.detector:
            self.detector.stop()
            self.detector = None
        self.save()
        def worker():
            try:
                install_update(asset_url, lambda text: self.messages.put(("log", text)))
                self.after(0, self.close)
            except Exception as error:
                message = str(error)
                self.after(0, lambda: messagebox.showerror("Update failed", message))
        threading.Thread(target=worker, daemon=True).start()

    def close(self):
        if self.detector:
            self.detector.stop()
        self.save()
        self.destroy()

if __name__ == "__main__":
    App().mainloop()