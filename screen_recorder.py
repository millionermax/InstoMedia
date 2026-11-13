"""
screen_recorder.py - Per-Task Device Screen Recording Engine

Manages task-scoped Android device screen recordings via ADB screenrecord:
- Handles 180-second Android process limits via seamless sequential segmentation.
- Flushes MP4 moov atom on stop using SIGINT (kill -2) on device.
- Pulls recordings to local task-scoped storage (uploads/artifacts/recordings/task_{id}/).
- Concatenates multi-segment clips via ffmpeg concat demuxer with clip fallback.
- Enforces lifecycle decision:
    * 100% Successful task (completed + cleanup ok) -> deletes video file, sets 'deleted_success'.
    * Unsuccessful task (failed/interrupted/cancelled/cleanup failed) -> retains video, sets 'saved'.
    * Logs are permanently preserved in all outcomes.
- 30-day auto-expiry retention cleaner.
"""

from datetime import datetime, timezone, timedelta
import json
import logging
import os
import shutil
import subprocess
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("instoper.screen_recorder")

DEFAULT_TIME_LIMIT = 180
DEFAULT_BIT_RATE = 4_000_000  # 4 Mbps
REMOTE_TMP_DIR = "/sdcard"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def probe_video_duration(video_path: str) -> Optional[float]:
    """Probes video duration in seconds using ffprobe if available."""
    if not video_path or not os.path.isfile(video_path):
        return None
    ffprobe_bin = shutil.which("ffprobe")
    if not ffprobe_bin:
        return None
    try:
        res = subprocess.run(
            [
                ffprobe_bin,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                video_path,
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if res.returncode == 0 and res.stdout.strip():
            val = float(res.stdout.strip())
            return round(val, 2)
    except Exception as e:
        logger.debug(f"ffprobe duration check failed for {video_path}: {e}")
    return None


def concatenate_video_segments(segment_paths: List[str], output_path: str) -> bool:
    """Concatenates multiple MP4 segments using ffmpeg concat demuxer without re-encoding."""
    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin or not segment_paths:
        return False
    if len(segment_paths) == 1:
        shutil.copyfile(segment_paths[0], output_path)
        return True

    list_file = output_path + ".concat_list.txt"
    try:
        with open(list_file, "w", encoding="utf-8") as f:
            for p in segment_paths:
                clean_p = os.path.abspath(p).replace("\\", "/")
                f.write(f"file '{clean_p}'\n")

        res = subprocess.run(
            [
                ffmpeg_bin,
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                list_file,
                "-c",
                "copy",
                output_path,
            ],
            capture_output=True,
            text=True,
            timeout=45,
        )
        return res.returncode == 0 and os.path.isfile(output_path) and os.path.getsize(output_path) > 0
    except Exception as exc:
        logger.warning(f"ffmpeg concat failed: {exc}")
        return False
    finally:
        if os.path.exists(list_file):
            try:
                os.remove(list_file)
            except OSError:
                pass


class TaskScreenRecorder:
    """Manages device screen recording for a single execution task."""

    def __init__(
        self,
        task_id: int,
        device_serial: str,
        storage_base_dir: str,
        log_callback: Optional[Callable[[str, str], None]] = None,
        time_limit: int = DEFAULT_TIME_LIMIT,
        bit_rate: int = DEFAULT_BIT_RATE,
        testing_mode: bool = False,
    ):
        self.task_id = int(task_id)
        self.device_serial = str(device_serial or "").strip()
        self.storage_dir = os.path.join(storage_base_dir, f"task_{self.task_id}")
        self.log_callback = log_callback
        self.time_limit = max(10, min(int(time_limit), 180))
        self.bit_rate = int(bit_rate)
        self.testing_mode = testing_mode

        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._monitor_thread: Optional[threading.Thread] = None

        self._current_proc: Optional[subprocess.Popen] = None
        self._current_seg_index = 0
        self._current_remote_path: Optional[str] = None
        self._current_seg_start_time: Optional[float] = None

        self._segments: List[Dict[str, Any]] = []
        self._started_at: Optional[str] = None
        self._completed_at: Optional[str] = None
        self._error: Optional[str] = None
        self._is_running = False

    def _log(self, level: str, msg: str) -> None:
        if self.log_callback:
            try:
                self.log_callback(level, msg)
            except Exception:
                pass
        else:
            getattr(logger, level, logger.info)(f"[Task #{self.task_id} Recorder] {msg}")

    def start(self) -> bool:
        """Starts screen recording segment #0 and spawns rollover monitor."""
        with self._lock:
            if self._is_running:
                return True
            self._started_at = _utcnow()

            if not self.device_serial and not self.testing_mode:
                self._error = "No device serial provided for screen recording."
                self._log("warning", f"Could not start screen recording: {self._error}")
                return False

            try:
                os.makedirs(self.storage_dir, exist_ok=True)
            except Exception as e:
                self._error = f"Failed to create local recording directory: {e}"
                self._log("error", self._error)
                return False

            if self.testing_mode:
                self._is_running = True
                self._log("info", f"Screen recording started (simulated) for Task #{self.task_id}.")
                return True

            # Ensure no lingering screenrecord process exists on device before starting
            if self.device_serial:
                try:
                    res = subprocess.run(
                        ["adb", "-s", self.device_serial, "shell", "pidof", "screenrecord"],
                        capture_output=True,
                        text=True,
                        timeout=5,
                    )
                    if res.returncode == 0 and res.stdout.strip():
                        for p in res.stdout.strip().split():
                            subprocess.run(["adb", "-s", self.device_serial, "shell", "kill", "-2", p], capture_output=True, timeout=5)
                        time.sleep(0.5)
                except Exception:
                    pass

            success = self._start_segment(0)
            if not success:
                return False

            self._is_running = True
            self._stop_event.clear()
            self._monitor_thread = threading.Thread(
                target=self._rollover_monitor_worker,
                name=f"rec-monitor-task-{self.task_id}",
                daemon=True,
            )
            self._monitor_thread.start()
            self._log("info", f"Screen recording active on '{self.device_serial}' (task #{self.task_id}).")
            return True

    def _start_segment(self, seg_idx: int) -> bool:
        """Launches a single adb shell screenrecord segment."""
        remote_filename = f"instoper_task_{self.task_id}_seg{seg_idx}.mp4"
        remote_path = f"{REMOTE_TMP_DIR}/{remote_filename}"
        cmd = [
            "adb",
            "-s",
            self.device_serial,
            "shell",
            "screenrecord",
            "--time-limit",
            str(self.time_limit),
            "--bit-rate",
            str(self.bit_rate),
            remote_path,
        ]
        try:
            # Clean remote target if lingering
            subprocess.run(
                ["adb", "-s", self.device_serial, "shell", "rm", "-f", remote_path],
                capture_output=True,
                timeout=5,
            )
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            self._current_proc = proc
            self._current_seg_index = seg_idx
            self._current_remote_path = remote_path
            self._current_seg_start_time = time.time()
            return True
        except Exception as exc:
            self._error = f"Failed to spawn screenrecord segment #{seg_idx}: {exc}"
            self._log("warning", self._error)
            return False

    def _stop_active_segment_on_device(self) -> None:
        """Gracefully stops the active segment so Android screenrecord flushes moov atom."""
        if self.testing_mode:
            return

        # 1. Send SIGINT (2) to screenrecord on device
        if self.device_serial:
            try:
                res = subprocess.run(
                    ["adb", "-s", self.device_serial, "shell", "pidof", "screenrecord"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if res.returncode == 0 and res.stdout.strip():
                    pids = res.stdout.strip().split()
                    for p in pids:
                        subprocess.run(
                            ["adb", "-s", self.device_serial, "shell", "kill", "-2", p],
                            capture_output=True,
                            timeout=5,
                        )
            except Exception as e:
                logger.debug(f"Device kill -2 error: {e}")

        # 2. Wait for Popen process to finalize
        proc = self._current_proc
        if proc:
            try:
                proc.wait(timeout=4)
            except subprocess.TimeoutExpired:
                try:
                    proc.terminate()
                    proc.wait(timeout=3)
                except Exception:
                    pass
            finally:
                self._current_proc = None

        if self._current_remote_path:
            elapsed = time.time() - (self._current_seg_start_time or time.time())
            self._segments.append(
                {
                    "index": self._current_seg_index,
                    "remote_path": self._current_remote_path,
                    "local_filename": f"seg_{self._current_seg_index}.mp4",
                    "duration_seconds": round(max(0.1, elapsed), 2),
                }
            )
            self._current_remote_path = None

    def _rollover_monitor_worker(self) -> None:
        """Monitors segment duration and starts the next segment if approaching 180s."""
        # Rollover 3 seconds before hard 180s cut
        rollover_threshold = max(5, self.time_limit - 3)

        while not self._stop_event.is_set():
            time.sleep(1)
            if self._stop_event.is_set():
                break

            with self._lock:
                if not self._is_running or not self._current_proc:
                    break

                elapsed = time.time() - (self._current_seg_start_time or time.time())
                proc_finished = self._current_proc.poll() is not None

                if proc_finished:
                    self._error = f"screenrecord segment #{self._current_seg_index} exited prematurely (code {self._current_proc.returncode})."
                    self._log("warning", self._error)
                    break

                if elapsed >= rollover_threshold:
                    self._log(
                        "info",
                        f"Screenrecord segment #{self._current_seg_index} reached {int(elapsed)}s. Rolling over to segment #{self._current_seg_index + 1}...",
                    )
                    self._stop_active_segment_on_device()
                    next_idx = self._current_seg_index + 1
                    ok = self._start_segment(next_idx)
                    if not ok:
                        self._log("warning", f"Failed to rollover to screen recording segment #{next_idx}.")
                        break

    def stop(self, terminal_task_state: str, cleanup_ok: bool) -> Dict[str, Any]:
        """Stops recording, pulls clips from device, joins if multi-segment, and enforces lifecycle deletion/retention."""
        with self._lock:
            self._stop_event.set()
            self._is_running = False
            self._completed_at = _utcnow()

            # Stop active device process
            self._stop_active_segment_on_device()

        # Wait for monitor thread to exit
        if self._monitor_thread and self._monitor_thread.is_alive():
            self._monitor_thread.join(timeout=3)

        # Simulation mode handling for unit tests
        if self.testing_mode:
            return self._handle_simulation_stop(terminal_task_state, cleanup_ok)

        if not self._segments and not self._error:
            self._error = "No screen recording segments were captured."

        pulled_segments: List[Dict[str, Any]] = []
        total_duration = 0.0

        # Pull all recorded segments from device
        for seg in self._segments:
            remote = seg["remote_path"]
            local_name = seg["local_filename"]
            local_dest = os.path.join(self.storage_dir, local_name)
            try:
                pull_res = subprocess.run(
                    ["adb", "-s", self.device_serial, "pull", remote, local_dest],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                # Remove remote segment from device
                subprocess.run(
                    ["adb", "-s", self.device_serial, "shell", "rm", "-f", remote],
                    capture_output=True,
                    timeout=5,
                )
                if os.path.isfile(local_dest) and os.path.getsize(local_dest) > 0:
                    dur = probe_video_duration(local_dest) or seg.get("duration_seconds") or 0.0
                    sz = os.path.getsize(local_dest)
                    pulled_segments.append(
                        {
                            "index": seg["index"],
                            "filename": local_name,
                            "path": local_dest,
                            "size_bytes": sz,
                            "duration_seconds": dur,
                        }
                    )
                    total_duration += dur
                else:
                    self._log("warning", f"Pulled segment #{seg['index']} was empty or missing.")
            except Exception as e:
                self._log("warning", f"Failed to pull segment #{seg['index']} from device: {e}")

        final_video_path: Optional[str] = None
        final_status = "unavailable"

        if not pulled_segments:
            final_status = "capture_failed" if self._error else "unavailable"
            final_error = self._error or "Device did not produce any valid video segments."
            return {
                "status": final_status,
                "recording_path": None,
                "duration_seconds": None,
                "segments": [],
                "error": final_error,
                "started_at": self._started_at,
                "completed_at": self._completed_at,
            }

        # Determine primary video path
        if len(pulled_segments) == 1:
            primary_path = os.path.join(self.storage_dir, f"task_{self.task_id}_recording.mp4")
            try:
                if os.path.exists(primary_path):
                    os.remove(primary_path)
                os.rename(pulled_segments[0]["path"], primary_path)
                pulled_segments[0]["path"] = primary_path
                pulled_segments[0]["filename"] = os.path.basename(primary_path)
                final_video_path = primary_path
            except Exception:
                final_video_path = pulled_segments[0]["path"]
        else:
            joined_path = os.path.join(self.storage_dir, f"task_{self.task_id}_joined.mp4")
            seg_paths = [s["path"] for s in pulled_segments]
            join_ok = concatenate_video_segments(seg_paths, joined_path)
            if join_ok and os.path.isfile(joined_path):
                joined_dur = probe_video_duration(joined_path) or total_duration
                final_video_path = joined_path
                total_duration = joined_dur
            else:
                self._log("warning", "Segment concatenation failed; retaining original individual segment clips.")
                final_video_path = pulled_segments[0]["path"]

        # Enforce Lifecycle Policy
        is_100_percent_success = (terminal_task_state == "completed") and (cleanup_ok is True)

        if is_100_percent_success:
            deletion_error: Optional[str] = None
            try:
                if os.path.exists(self.storage_dir):
                    shutil.rmtree(self.storage_dir)
                # Rigorous filesystem verification
                if os.path.exists(self.storage_dir):
                    deletion_error = f"Recording directory '{self.storage_dir}' still exists on filesystem after deletion attempt."
            except Exception as del_err:
                deletion_error = f"Failed to delete recording directory '{self.storage_dir}': {del_err}"

            if deletion_error:
                final_status = "delete_failed"
                self._log("error", f"Screen recording deletion failed for successful Task #{self.task_id}: {deletion_error}")
                normalized_path = os.path.abspath(final_video_path).replace("\\", "/") if final_video_path and os.path.exists(final_video_path) else None
                return {
                    "status": final_status,
                    "recording_path": normalized_path,
                    "duration_seconds": round(total_duration, 2) if total_duration > 0 else None,
                    "segments": pulled_segments if normalized_path else [],
                    "error": deletion_error,
                    "started_at": self._started_at,
                    "completed_at": self._completed_at,
                }
            else:
                final_status = "deleted_success"
                self._log("info", f"Screen recording deleted after 100% verified success for Task #{self.task_id}.")
                return {
                    "status": final_status,
                    "recording_path": None,
                    "duration_seconds": round(total_duration, 2) if total_duration > 0 else None,
                    "segments": [],
                    "error": None,
                    "started_at": self._started_at,
                    "completed_at": self._completed_at,
                }
        else:
            # Retain video and logs for failed / interrupted / cancelled tasks
            final_status = "saved"
            self._log(
                "info",
                f"Retained task screen recording ({round(total_duration, 1)}s) due to task outcome: {terminal_task_state}.",
            )
            # Store path relative to workspace or absolute
            normalized_path = os.path.abspath(final_video_path).replace("\\", "/") if final_video_path else None
            return {
                "status": final_status,
                "recording_path": normalized_path,
                "duration_seconds": round(total_duration, 2) if total_duration > 0 else None,
                "segments": pulled_segments,
                "error": None,
                "started_at": self._started_at,
                "completed_at": self._completed_at,
            }

    def _handle_simulation_stop(self, terminal_task_state: str, cleanup_ok: bool) -> Dict[str, Any]:
        """Handles stop logic when in unit-testing / simulation mode."""
        is_100_percent_success = (terminal_task_state == "completed") and (cleanup_ok is True)
        dummy_video = os.path.join(self.storage_dir, f"task_{self.task_id}_simulated.mp4")

        if is_100_percent_success:
            deletion_error = getattr(self, "_simulate_delete_error", None)
            if deletion_error:
                os.makedirs(self.storage_dir, exist_ok=True)
                with open(dummy_video, "wb") as f:
                    f.write(b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom")
                return {
                    "status": "delete_failed",
                    "recording_path": os.path.abspath(dummy_video).replace("\\", "/"),
                    "duration_seconds": 12.5,
                    "segments": [],
                    "error": deletion_error,
                    "started_at": self._started_at,
                    "completed_at": self._completed_at,
                }
            try:
                if os.path.exists(self.storage_dir):
                    shutil.rmtree(self.storage_dir)
                if os.path.exists(self.storage_dir):
                    raise OSError(f"Directory {self.storage_dir} could not be removed.")
            except Exception as e:
                return {
                    "status": "delete_failed",
                    "recording_path": None,
                    "duration_seconds": 12.5,
                    "segments": [],
                    "error": str(e),
                    "started_at": self._started_at,
                    "completed_at": self._completed_at,
                }
            return {
                "status": "deleted_success",
                "recording_path": None,
                "duration_seconds": 12.5,
                "segments": [],
                "error": None,
                "started_at": self._started_at,
                "completed_at": self._completed_at,
            }
        else:
            os.makedirs(self.storage_dir, exist_ok=True)
            with open(dummy_video, "wb") as f:
                f.write(b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom")
            return {
                "status": "saved",
                "recording_path": os.path.abspath(dummy_video).replace("\\", "/"),
                "duration_seconds": 12.5,
                "segments": [
                    {
                        "index": 0,
                        "filename": os.path.basename(dummy_video),
                        "path": os.path.abspath(dummy_video).replace("\\", "/"),
                        "size_bytes": os.path.getsize(dummy_video),
                        "duration_seconds": 12.5,
                    }
                ],
                "error": None,
                "started_at": self._started_at,
                "completed_at": self._completed_at,
            }


def cleanup_expired_recordings(
    older_than_days: int = 30,
    db_module: Any = None,
    db_path: Optional[str] = None,
) -> int:
    """Finds recordings older than older_than_days, deletes their local files, and marks them 'expired'."""
    if db_module is None:
        import db as db_module

    expired_tasks = db_module.list_expired_recordings(older_than_days=older_than_days, db_path=db_path)
    count = 0
    for t in expired_tasks:
        tid = t["id"]
        rec_path = t.get("recording_path")
        if rec_path and os.path.exists(rec_path):
            try:
                os.remove(rec_path)
                parent_dir = os.path.dirname(rec_path)
                if os.path.isdir(parent_dir) and not os.listdir(parent_dir):
                    os.rmdir(parent_dir)
            except OSError as e:
                logger.warning(f"Could not remove expired recording file {rec_path}: {e}")

        # Also purge any segment folder
        segments = t.get("recording_segments") or []
        for s in segments:
            spath = s.get("path")
            if spath and os.path.exists(spath):
                try:
                    os.remove(spath)
                except OSError:
                    pass

        db_module.mark_recording_expired(tid, db_path=db_path)
        count += 1

    if count > 0:
        logger.info(f"Cleaned up {count} expired task screen recordings (> {older_than_days} days).")
    return count
