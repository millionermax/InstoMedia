# Instoper — Automated Instagram Reel Publishing Platform

**Instoper** is an enterprise-grade Android automation tool engineered for stealthy, high-reliability automated publishing of Instagram Reels directly through the native Instagram mobile client on real Android devices.

Equipped with a modern web-based Studio dashboard, real-time Android screen mirroring, anti-bot human biometric simulation (Cubic Bézier trajectories, Gaussian click jitter, dwell times), and 11-step state verification with self-healing.

---

## Key Features

- **Intuitive Web Studio**: Full-featured dark-mode dashboard (Flask + Tailwind CSS). No complex CLI needed.
- **Instagram Audio Deep-Linking**: Attach any trending audio track via direct Instagram audio URL without Meta Graph API restrictions.
- **Drag & Drop Video Uploader**: In-browser video dropzone with HTML5 playback preview, or pick the latest video directly from phone gallery.
- **Real-Time Live Device Mirror**: Interactive low-latency MJPEG video stream (~10–12 FPS) with bidirectional click injection directly from browser to device.
- **Anti-Bot Human Biometrics Engine**: 
  - Cubic Bézier curve swipe trajectories with sinusoidal ease-in/ease-out acceleration.
  - Gaussian off-center click coordinate distribution.
  - Finger dwell times (65ms–125ms) and 10x cadence human typing.
- **11-Step Interactive Pipeline**: Visual step progress indicators (Active / Done / Error) with real-time SSE log streaming.
- **Auto Link Extraction**: Automatically navigates to the newly posted Reel, opens the share sheet, and captures the published `/reel/` URL.

---

## 11-Step Workflow Pipeline

1. **Open Audio Link**: Launches Instagram directly to the target audio track via Android View Intent.
2. **Use Audio**: Taps "Use audio" to attach the audio and launch the Reel camera.
3. **Open Gallery**: Taps the Gallery picker from the camera view.
4. **Select Video**: Pushes the uploaded video to device storage (`/sdcard/DCIM/Camera/`) and selects it.
5. **Preview**: Taps "Next" on the video preview editor.
6. **Caption**: Writes caption & hashtags with natural typing cadence, then cleanly dismisses the soft keyboard via hardware keyevent.
7. **More Options**: Scrolls smoothly to locate and open "More options".
8. **Privacy Toggles**: Enables "Hide like count on this reel" and "Hide share count on this reel".
9. **Share**: Taps "Share" to submit the Reel.
10. **Wait for Upload**: Exits the audio sub-activity to Home feed and watches the upload progress bar.
11. **Copy Reel Link**: Identifies the published Reel, opens the share drawer, copies the link, and captures the URL from the clipboard.

---

## Quick Start

### Prerequisites
- Python 3.10+
- Android device with USB debugging enabled, connected via ADB:
  ```bash
  adb devices
  ```
- Target package: Official Instagram App (`com.instagram.android`) installed and logged in.

### Installation
```bash
git clone https://github.com/ilovekushgola/Instoper.git
cd Instoper
pip install -r requirements.txt
```

### Run the Web Studio GUI
Double-click `start_gui.bat` or run:
```bash
python app.py
```
Open your browser at `http://127.0.0.1:5000`.

### CLI Execution (Optional)
```bash
python adb.py --audio "https://www.instagram.com/reels/audio/<id>/" --video "path/to/video.mp4" --caption "My awesome caption #viral"
```

---

## Tech Stack
- **Backend**: Python 3, Flask, Werkzeug
- **Android Bridge**: ADB, `uiautomator2`
- **Anti-Bot Engine**: Pure-Python (Cubic Bézier, Gaussian distribution, MotionEvent emulation)
- **Frontend**: HTML5, Tailwind CSS, FontAwesome, Server-Sent Events (SSE)
- **Screen Streaming**: Multi-part JPEG (MJPEG) over HTTP

---

## License
MIT License. Created by [ilovekushgola](https://github.com/ilovekushgola).
