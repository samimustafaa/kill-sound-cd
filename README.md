# CD Kill Announcer – sg0hann

A lightweight Creative Destruction announcer prototype for Windows. It reads **two on-screen signals** (kill counter + elimination notification), then plays Windows voice announcements or your own MP3/WAV/OGG sounds.

**No game-file modifications, injection, memory reads, network hooks or anti-cheat bypass.** Compatibility with a particular client/anti-cheat is **not guaranteed**: check game rules before using any third-party overlay or screen-reading application.

## Run from source (Windows)

1. Install **Python 3.12** and **Tesseract OCR for Windows** (English language data); then double-click `run.bat`.
2. Start Creative Destruction, enter a match, and leave the HUD visible.
3. In the app's **Detection** tab, choose **Select Kill counter number** and drag a tight rectangle around the number itself.
4. Select **Elimination notification** and drag a rectangle around the place where *your* kill message appears. You can optionally select the **death notice** region for resetting the streak on death.
5. In **Settings**, browse to `tesseract.exe` if it was not detected. Customize keywords to match the actual game text you see.
6. Click **Start Detection** and watch the recognized text / counter in the app. Use **Sounds → Test** to hear each announcement.
7. Optional: select your own recorded sound file per streak level.

The first stable detected kill-counter value becomes the **baseline**. For best results, start scanning when the counter is zero. The app requires a matching elimination message within 2.5 seconds of an increased counter. An optional death-text region resets the streak. Without death detection, streaks reset when the kill counter itself decreases (typically a new match).

## Build a Windows download

Push this project to a GitHub repository and run **Actions → Build Windows app → Run workflow**. Download its artifact, unzip, and run `CD-Kill-Announcer.exe` inside the folder. Version tags like `v0.1.0` additionally publish the ZIP as a GitHub release. This is a **portable folder app**, not an installer or auto-updater.

## Limitations

- **Prototype**: depends on HUD layout, OCR readability, language, game resolution, fullscreen capture support, and anticheat policy. Needs testing against real gameplay screenshots.
- Region selection is for a **primary-monitor** game; secondary-monitor and some exclusive-fullscreen modes may need windowed/borderless mode or more calibration work.
- The counter must show a plain visible number within its selected box. Crop out surrounding labels and other numbers.
- Incorrect OCR can miss or miscount a kill. The two-signal cross-check is designed to reduce false positives, not eliminate them.
- `client.exe` process detection can match unrelated processes sharing that name; disable this check only for troubleshooting.
- Sounds use the **highest unlocked threshold**, not a repeating cycle. E.g., kills 9 triggers the 8-kill line until 10.
- Voice uses offline Windows SAPI. Custom MP3/WAV/OGG files stay at their original paths; moving them requires reselecting.
- This does not modify or patch Creative Destruction.

## Configuration

Saved at `%LOCALAPPDATA%\CDKillAnnouncer\settings.json`.

## Tech

Python 3.12 · Tkinter · MSS · Pillow · Tesseract OCR · pygame · offline Windows TTS · PyInstaller


## Auto-updates

Release builds check GitHub Releases when the app opens. When a newer version is available, the app asks for permission, downloads `CD-Kill-Announcer-Windows.zip`, replaces the app files after closing, then reopens itself. Your calibrated settings and chosen custom sounds are stored outside the app directory.

**Install once:** Download the latest release ZIP, extract the `CD-Kill-Announcer` folder to a user-writable path (for example `%LOCALAPPDATA%\Programs\CD-Kill-Announcer`) and run `CD-Kill-Announcer.exe`. **Do not run directly from a ZIP or from Program Files without write permission.** Future updates are managed inside the app.

**Publish:** Change `VERSION` in `updater.py` to your next version (for example `0.1.1`), commit, and push a matching `v0.1.1` tag. GitHub Actions builds a new ZIP and publishes it as a GitHub Release. Releases must use increasing semantic versions.

This is a beta. Windows gameplay recognition and antivirus compatibility require real-device testing. The update script uses PowerShell to replace files only after this application exits.