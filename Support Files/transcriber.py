#!/usr/bin/env python3
"""Portable Newsroom Audio Transcriber (macOS & Windows).

Watches a chosen audio input folder, transcribes new audio locally with WhisperX
and pyannote speaker diarization, and writes readable speaker-labeled transcripts.
"""

from __future__ import annotations

import argparse
import configparser
import contextlib
import dataclasses
import datetime as dt
import gc
import json
import logging
import os
import plistlib
import queue
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.request
import warnings
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any

SUPPORT_DIR = Path(__file__).resolve().parent
APP_DIR = SUPPORT_DIR.parent
CONFIG_PATH = APP_DIR / "Settings.ini"
VERSION_PATH = APP_DIR / "VERSION.txt"
LOG_DIR = SUPPORT_DIR / "logs"
LOG_FILE = LOG_DIR / "transcriber.log"
HEARTBEAT_FILE = LOG_DIR / "heartbeat.json"
LOCK_FILE = LOG_DIR / "transcriber.lock"
PENDING_QUEUE_FILE = LOG_DIR / "pending_queue.json"
MANIFEST_FILE = LOG_DIR / "completed_manifest.json"
MODELS_ROOT = SUPPORT_DIR / "models"

# Configure ALL model/data caches inside Support Files BEFORE any third-party
# ML/audio imports so nothing leaks into ~/.cache on macOS or Windows.
_CACHE_ENV = {
    "HF_HOME": MODELS_ROOT / "huggingface",
    "HF_HUB_CACHE": MODELS_ROOT / "huggingface" / "hub",
    "HUGGINGFACE_HUB_CACHE": MODELS_ROOT / "huggingface" / "hub",
    "TORCH_HOME": MODELS_ROOT / "torch",
    "PYANNOTE_CACHE": MODELS_ROOT / "pyannote",
    "NLTK_DATA": MODELS_ROOT / "nltk_data",
    "XDG_CACHE_HOME": SUPPORT_DIR / ".xdg-cache",
    "XDG_CONFIG_HOME": SUPPORT_DIR / ".xdg-config",
    "XDG_DATA_HOME": SUPPORT_DIR / ".xdg-data",
    "MPLCONFIGDIR": SUPPORT_DIR / ".matplotlib",
}
for _env_key, _env_dir in _CACHE_ENV.items():
    os.environ[_env_key] = str(_env_dir)
    _env_dir.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

VALID_EXTENSIONS = {
    ".wav",
    ".mp3",
    ".m4a",
    ".mp4",
    ".mov",
    ".flac",
    ".ogg",
    ".opus",
    ".aac",
    ".wma",
    ".webm",
}
PYANNOTE_GATED_MODELS = [
    "pyannote/segmentation-3.0",
    "pyannote/speaker-diarization-3.1",
]
MIN_FREE_GB = 4
RECOMMENDED_FREE_GB = 8
TOKEN_PATTERN = re.compile(r"^hf_[A-Za-z0-9_\-]{20,}$")
TOKEN_REDACT_PATTERN = re.compile(r"hf_[A-Za-z0-9_\-]{12,}")
SOURCE_OFFSET_PATTERN = re.compile(r"\(part \d+ of \d+, from (\d+)h(\d{2})m\)", re.IGNORECASE)

KEYCHAIN_SERVICE_DEFAULT = "com.audio-transcriber.hf-token"
KEYCHAIN_MARKER = "SAVED_IN_KEYCHAIN"
PLACEHOLDER_TOKENS = {"", "YOUR_TOKEN_HERE", "hf_your_actual_token_here", KEYCHAIN_MARKER}
KEYCHAIN_NOTICE_TEXT = (
    "# Your Hugging Face token is safely stored in your computer's private Keychain\n"
    "# (not kept in plain text inside this folder).\n"
    "# To replace or update your token anytime, replace SAVED_IN_KEYCHAIN below\n"
    "# with a new hf_... token and save this file.\n"
    f"{KEYCHAIN_MARKER}\n"
)
STARTUP_SETTLE_SECONDS = 1.0

MACOS_AUTOSTART_LABEL = "com.audio-transcriber.autostart"
MACOS_LEGACY_LABELS = ["com.reporter-transcriber.autostart"]

LOG = logging.getLogger("audio_transcriber")

# Keep reporter-facing Terminal output calm and readable.
warnings.filterwarnings("ignore", category=SyntaxWarning)
warnings.filterwarnings("ignore", message=".*torchaudio.*deprecated.*", category=UserWarning)
warnings.filterwarnings("ignore", message=".*degrees of freedom is <= 0.*", category=UserWarning)
warnings.filterwarnings("ignore", message=".*resource_tracker: There appear to be.*", category=UserWarning)
warnings.filterwarnings("ignore", message=".*Model was trained with pyannote.*", category=UserWarning)
warnings.filterwarnings("ignore", message=".*Model was trained with torch.*", category=UserWarning)


@dataclasses.dataclass(frozen=True)
class JobSettings:
    """Immutable snapshot of settings used for a single transcription job."""

    watch_folder: Path
    transcript_folder: Path
    model_cache_dir: Path
    notification_sound: Path | None
    hf_token: str
    token_file: Path | None
    model_size: str
    language: str
    device: str
    compute_type: str
    batch_size: int
    sentences_per_timecode: int
    output_format: str
    skip_existing: bool
    recursive_watch: bool
    process_existing: bool
    max_existing_on_start: int


def app_version() -> str:
    if VERSION_PATH.exists():
        return VERSION_PATH.read_text(encoding="utf-8").strip() or "unknown"
    return "unknown"


def setup_logging(console: bool = True) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter("[%(asctime)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    handlers: list[logging.Handler] = [logging.FileHandler(LOG_FILE, encoding="utf-8")]
    if console:
        handlers.append(logging.StreamHandler(sys.stdout))
    for handler in handlers:
        handler.setFormatter(formatter)

    # Keep third-party ML/font libraries quiet unless an actual error happens.
    logging.basicConfig(level=logging.ERROR, handlers=handlers, force=True)
    for noisy_name in (
        "matplotlib",
        "speechbrain",
        "pytorch_lightning",
        "lightning",
        "lightning_fabric",
        "urllib3",
        "fsspec",
    ):
        logging.getLogger(noisy_name).setLevel(logging.ERROR)

    LOG.handlers.clear()
    for handler in handlers:
        LOG.addHandler(handler)
    LOG.setLevel(logging.INFO)
    LOG.propagate = False


def load_config() -> configparser.ConfigParser:
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f"Missing Settings.ini at {CONFIG_PATH}")
    # interpolation=None allows folder names containing '%' and Windows %VARS%.
    config = configparser.ConfigParser(interpolation=None)
    config.read(CONFIG_PATH, encoding="utf-8")
    return config


def resolve_path(value: str) -> Path:
    expanded = os.path.expandvars(value.strip())
    path = Path(expanded).expanduser()
    if path.is_absolute():
        return path
    return APP_DIR / path


def resolve_support_path(value: str) -> Path:
    expanded = os.path.expandvars(value.strip())
    path = Path(expanded).expanduser()
    if path.is_absolute():
        return path
    return SUPPORT_DIR / path


# ---------------------------------------------------------------------------
# Single-Instance Process Lock
# ---------------------------------------------------------------------------


class SingleInstanceLock:
    """Cross-platform non-blocking file lock preventing duplicate watcher windows."""

    def __init__(self, path: Path = LOCK_FILE) -> None:
        self.path = path
        self._handle: Any = None

    def acquire(self) -> tuple[bool, str]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, "a+", encoding="utf-8")
        try:
            if sys.platform == "win32":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.seek(0)
            existing = handle.read().strip()
            handle.close()
            return False, existing

        handle.seek(0)
        handle.truncate()
        info = f"pid={os.getpid()} started={dt.datetime.now().isoformat(timespec='seconds')}\n"
        handle.write(info)
        handle.flush()
        self._handle = handle
        return True, info.strip()

    def release(self) -> None:
        if self._handle is None:
            return
        try:
            if sys.platform == "win32":
                import msvcrt

                self._handle.seek(0)
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        try:
            self._handle.close()
        except OSError:
            pass
        self._handle = None


def is_another_instance_running() -> tuple[bool, str]:
    probe = SingleInstanceLock(LOCK_FILE)
    acquired, detail = probe.acquire()
    if acquired:
        probe.release()
        return False, ""
    return True, detail


# ---------------------------------------------------------------------------
# Heartbeat & System Health Helpers
# ---------------------------------------------------------------------------


class ActivityTracker:
    """Thread-safe status tracker writing atomic heartbeat.json updates."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._status = "starting"
        self._detail = "Audio Transcriber is starting"
        self._current: dict[str, Any] | None = None
        self._latest_completed = self._load_previous_completed()

    @staticmethod
    def _load_previous_completed() -> dict[str, Any] | None:
        try:
            data = json.loads(HEARTBEAT_FILE.read_text(encoding="utf-8"))
            prev = data.get("latest_completed")
            return prev if isinstance(prev, dict) else None
        except Exception:
            return None

    def update(self, status: str, detail: str, current: Any = "unchanged") -> None:
        with self._lock:
            self._status = status
            self._detail = detail
            if current != "unchanged":
                self._current = current
        self.write()

    def set_stage(self, stage: str, processing_started: bool = False) -> None:
        with self._lock:
            if isinstance(self._current, dict):
                self._current["stage"] = stage
                if processing_started and not self._current.get("started_at"):
                    self._current["started_at"] = dt.datetime.now().astimezone().isoformat(timespec="seconds")
                self._detail = f"{stage}: {self._current.get('name', '')}"
        self.write()

    def record_completed(
        self,
        audio_path: Path,
        transcript_path: Path,
        elapsed_min: float,
        audio_min: float,
        ratio: float,
    ) -> None:
        with self._lock:
            self._latest_completed = {
                "name": audio_path.name,
                "transcript_name": transcript_path.name,
                "elapsed_minutes": round(elapsed_min, 1),
                "audio_minutes": round(audio_min, 1),
                "realtime_ratio": round(ratio, 2),
                "completed_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            }
            self._current = None
        self.write()

    def write(self) -> None:
        with self._lock:
            payload = {
                "timestamp": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
                "pid": os.getpid(),
                "status": self._status,
                "detail": self._detail,
                "current": self._current,
                "latest_completed": self._latest_completed,
            }
        HEARTBEAT_FILE.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=".heartbeat.", dir=str(HEARTBEAT_FILE.parent))
        temp_path = Path(temp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle)
                handle.write("\n")
            os.replace(temp_path, HEARTBEAT_FILE)
        except OSError:
            pass
        finally:
            if temp_path.exists():
                temp_path.unlink(missing_ok=True)


def check_system_health() -> None:
    """Log a friendly heads-up if the computer is under severe memory pressure."""
    problems: list[str] = []
    if sys.platform == "darwin":
        try:
            lvl_out = subprocess.run(
                ["/usr/sbin/sysctl", "-n", "kern.memorystatus_vm_pressure_level"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            ).stdout.strip()
            if lvl_out and int(lvl_out) >= 4:
                problems.append("macOS reports critical memory pressure")
        except Exception:
            pass
        try:
            swap_out = subprocess.run(
                ["/usr/sbin/sysctl", "-n", "vm.swapusage"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            ).stdout
            match = re.search(r"used\s*=\s*([\d.]+)M", swap_out)
            if match and float(match.group(1)) > 6144 and problems:
                problems.append(f"{float(match.group(1)) / 1024:.1f} GB of disk swap in use")
        except Exception:
            pass
    elif sys.platform == "win32":
        try:
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)) and stat.dwMemoryLoad >= 92:
                problems.append(f"Windows memory usage is very high ({stat.dwMemoryLoad}%)")
        except Exception:
            pass

    if problems:
        LOG.warning("!" * 64)
        LOG.warning("HEADS UP: Your computer is very low on free memory right now:")
        for item in problems:
            LOG.warning("  - %s", item)
        LOG.warning("Transcription may run slower than normal. Closing heavy browser tabs")
        LOG.warning("or restarting your computer usually restores normal speed.")
        LOG.warning("!" * 64)


@contextlib.contextmanager
def prevent_idle_sleep():
    """Prevent macOS from idle-sleeping ONLY while a file is actively transcribing."""
    proc: subprocess.Popen[bytes] | None = None
    if sys.platform == "darwin":
        caffeinate = Path("/usr/bin/caffeinate")
        if caffeinate.exists():
            try:
                proc = subprocess.Popen(
                    [str(caffeinate), "-i", "-w", str(os.getpid())],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except Exception:
                proc = None
    try:
        yield
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except Exception:
                proc.kill()


# ---------------------------------------------------------------------------
# FFmpeg Setup (macOS & Windows)
# ---------------------------------------------------------------------------


def _ffmpeg_runs(ffmpeg_path: str | None) -> bool:
    if not ffmpeg_path:
        return False
    try:
        subprocess.run(
            [ffmpeg_path, "-version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
            timeout=15,
        )
        return True
    except Exception:
        return False


def _make_macos_binary_runnable(binary: Path) -> None:
    if sys.platform != "darwin":
        return
    try:
        binary.chmod(binary.stat().st_mode | 0o111)
    except Exception:
        pass
    xattr = Path("/usr/bin/xattr")
    if xattr.exists():
        subprocess.run(
            [str(xattr), "-d", "com.apple.quarantine", str(binary)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )


def ensure_ffmpeg_on_path() -> bool:
    """Expose a working `ffmpeg` (`ffmpeg.exe` on Windows) on PATH and verify execution."""
    if sys.platform == "darwin":
        extra_paths = [Path("/opt/homebrew/bin"), Path("/usr/local/bin")]
        os.environ["PATH"] = os.pathsep.join(str(p) for p in extra_paths) + os.pathsep + os.environ.get("PATH", "")

    system_ffmpeg = shutil.which("ffmpeg")
    if _ffmpeg_runs(system_ffmpeg):
        LOG.info("Audio decoder ready (ffmpeg: %s)", system_ffmpeg)
        return True

    try:
        import imageio_ffmpeg

        bundled = Path(imageio_ffmpeg.get_ffmpeg_exe())
    except Exception:
        LOG.error("ffmpeg was not found and bundled imageio-ffmpeg is missing. Run 1 Setup first.")
        return False

    if not bundled.exists():
        LOG.error("Bundled ffmpeg binary is missing: %s. Run 1 Setup first.", bundled)
        return False

    _make_macos_binary_runnable(bundled)
    bin_dir = SUPPORT_DIR / ".ffmpeg-bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    exe_name = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
    ffmpeg_target = bin_dir / exe_name

    # Reuse existing link/copy if it already points to or matches bundled binary and runs cleanly.
    if not _ffmpeg_runs(str(ffmpeg_target)):
        try:
            if ffmpeg_target.is_symlink() or ffmpeg_target.exists():
                ffmpeg_target.unlink()
            if sys.platform == "win32":
                shutil.copy2(bundled, ffmpeg_target)
            else:
                try:
                    ffmpeg_target.symlink_to(bundled)
                except Exception:
                    shutil.copy2(bundled, ffmpeg_target)
                    _make_macos_binary_runnable(ffmpeg_target)
        except Exception as exc:
            LOG.error("Could not prepare bundled ffmpeg: %s", exc)
            return False

    # If macOS blocks an unsigned copy, apply ad-hoc signature once as fallback.
    if sys.platform == "darwin" and not _ffmpeg_runs(str(ffmpeg_target)):
        codesign = Path("/usr/bin/codesign")
        if codesign.exists():
            subprocess.run(
                [str(codesign), "--force", "--sign", "-", str(bundled)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )

    os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")
    os.environ["FFMPEG_BINARY"] = str(ffmpeg_target)

    resolved = shutil.which("ffmpeg")
    if _ffmpeg_runs(resolved):
        LOG.info("Audio decoder ready (bundled ffmpeg)")
        return True

    LOG.error(
        "Bundled ffmpeg is present but could not run. On macOS, check System Settings -> "
        "Privacy & Security -> Open Anyway, or run 1 Setup again."
    )
    return False


# ---------------------------------------------------------------------------
# Lazy ML / Watchdog Imports & PyTorch Helpers
# ---------------------------------------------------------------------------


def import_torch():
    import torch

    return torch


def import_whisperx():
    import whisperx

    return whisperx


def import_watchdog():
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer
    from watchdog.observers.polling import PollingObserver

    return FileSystemEventHandler, Observer, PollingObserver


def create_started_observer(event_handler: Any, watch_folder: Path, recursive: bool) -> tuple[Any, Any]:
    """Start native OS file-system observer (FSEvents on macOS, ReadDirectoryChangesW on Windows),
    falling back automatically to a 1-second PollingObserver if native watching fails (e.g. network drive).
    """
    _, Observer, PollingObserver = import_watchdog()
    try:
        observer = Observer()
        watch_handle = observer.schedule(event_handler, str(watch_folder), recursive=recursive)
        observer.start()
        return observer, watch_handle
    except Exception as native_exc:
        LOG.warning("Native folder watcher unavailable (%s); using 1-second polling fallback.", native_exc)
        observer = PollingObserver(timeout=1)
        watch_handle = observer.schedule(event_handler, str(watch_folder), recursive=recursive)
        observer.start()
        return observer, watch_handle


def import_diarization_pipeline():
    from whisperx.diarize import DiarizationPipeline

    return DiarizationPipeline


@contextlib.contextmanager
def trusted_torch_checkpoints():
    """Allow trusted pyannote checkpoints under PyTorch 2.6+ scoped strictly to model load."""
    torch = import_torch()
    original_torch_load = torch.load

    def torch_load_trusted(*args, **kwargs):
        kwargs["weights_only"] = False
        return original_torch_load(*args, **kwargs)

    torch.load = torch_load_trusted
    try:
        yield
    finally:
        torch.load = original_torch_load


def choose_device(config: configparser.ConfigParser) -> str:
    requested = config.get("Whisper", "device", fallback="cpu").strip().lower()
    if requested in ("", "cpu"):
        return "cpu"
    if requested == "mps":
        LOG.warning("MPS was requested; WhisperX/faster-whisper requires CPU on Macs. Using CPU.")
        return "cpu"
    torch = import_torch()
    if requested in ("auto", "cuda") or requested.startswith("cuda"):
        if torch.cuda.is_available():
            return "cuda" if requested == "auto" else requested
        if requested != "auto":
            LOG.warning("CUDA was requested but is not available. Falling back to CPU.")
        return "cpu"
    return "cpu"


def cleanup_torch(device: str) -> None:
    gc.collect()
    torch = sys.modules.get("torch")
    if torch is not None and device.startswith("cuda") and torch.cuda.is_available():
        torch.cuda.empty_cache()


# ---------------------------------------------------------------------------
# Token & Settings Validation (With Automatic macOS Keychain / Local Storage)
# ---------------------------------------------------------------------------


def _secure_store_enabled() -> bool:
    return os.environ.get("AT_DISABLE_KEYCHAIN", "").strip() != "1"


def _keychain_service() -> str:
    return os.environ.get("AT_KEYCHAIN_SERVICE", KEYCHAIN_SERVICE_DEFAULT).strip() or KEYCHAIN_SERVICE_DEFAULT


def _keychain_account() -> str:
    return os.environ.get("USER") or os.environ.get("LOGNAME") or "audio-transcriber"


def _windows_token_store_path() -> Path:
    override = os.environ.get("AT_WINDOWS_TOKEN_STORE", "").strip()
    if override:
        return Path(override)
    local_app_data = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(local_app_data) / "AudioTranscriber" / "hf_token.txt"


def _macos_keychain_unlocked() -> bool:
    """Return True if the user's personal login Keychain is currently unlocked (never triggers a popup)."""
    security_bin = Path("/usr/bin/security")
    if not security_bin.exists():
        return False
    try:
        res = subprocess.run(
            [str(security_bin), "show-keychain-info"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        return res.returncode == 0
    except Exception:
        return False


def _save_token_to_secure_store(token: str) -> bool:
    """Save valid hf_... token into macOS Keychain (or %LOCALAPPDATA% on Windows) and verify readback."""
    if not _secure_store_enabled() or not TOKEN_PATTERN.match(token):
        return False
    if sys.platform == "darwin":
        if not _macos_keychain_unlocked():
            return False
        try:
            res = subprocess.run(
                [
                    "/usr/bin/security",
                    "add-generic-password",
                    "-U",
                    "-a",
                    _keychain_account(),
                    "-s",
                    _keychain_service(),
                    "-w",
                    token,
                ],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if res.returncode != 0:
                return False
            return _load_token_from_secure_store() == token
        except Exception:
            return False
    if sys.platform == "win32":
        try:
            store_path = _windows_token_store_path()
            atomic_write_text(store_path, token + "\n", mode=0o600)
            return _load_token_from_secure_store() == token
        except Exception:
            return False
    return False


def _load_token_from_secure_store() -> str:
    if not _secure_store_enabled():
        return ""
    if sys.platform == "darwin":
        if not _macos_keychain_unlocked():
            return ""
        try:
            res = subprocess.run(
                [
                    "/usr/bin/security",
                    "find-generic-password",
                    "-a",
                    _keychain_account(),
                    "-s",
                    _keychain_service(),
                    "-w",
                ],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if res.returncode == 0:
                candidate = res.stdout.strip()
                return candidate if TOKEN_PATTERN.match(candidate) else ""
        except Exception:
            return ""
    elif sys.platform == "win32":
        try:
            store_path = _windows_token_store_path()
            candidate = read_token_from_file(store_path)
            return candidate if TOKEN_PATTERN.match(candidate) else ""
        except Exception:
            return ""
    return ""


def _write_keychain_marker_file(token_file: Path) -> None:
    try:
        atomic_write_text(token_file, KEYCHAIN_NOTICE_TEXT, mode=0o600)
    except OSError:
        pass


def read_token_from_file(path: Path) -> str:
    if not path.exists():
        return ""
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            line = line.split("=", 1)[1].strip().strip("'\"")
        return line
    return ""


def read_hugging_face_token(config: configparser.ConfigParser) -> tuple[str, Path | None]:
    token_file_value = config.get("Diarization", "token_file", fallback="My Hugging Face Token.txt").strip()
    token_file = resolve_path(token_file_value) if token_file_value else None
    raw_file_token = read_token_from_file(token_file) if token_file else ""

    # 1. If the user pasted a token directly into My Hugging Face Token.txt, honor it first
    #    (and automatically move valid hf_... tokens into Keychain so plain text doesn't linger!).
    if raw_file_token not in PLACEHOLDER_TOKENS:
        if token_file is not None and TOKEN_PATTERN.match(raw_file_token):
            if _save_token_to_secure_store(raw_file_token):
                _write_keychain_marker_file(token_file)
                store_label = "macOS Keychain" if sys.platform == "darwin" else "private local storage"
                LOG.info(
                    "Moved Hugging Face token from '%s' into %s (no plain-text token left in folder).",
                    token_file.name,
                    store_label,
                )
        return raw_file_token, token_file

    # 2. Check optional Settings.ini override if explicitly set.
    ini_token = config.get("Diarization", "huggingface_token", fallback="").strip()
    if ini_token and ini_token not in PLACEHOLDER_TOKENS:
        return ini_token, token_file

    # 3. Load from macOS Keychain / Windows %LOCALAPPDATA% secure store.
    stored_token = _load_token_from_secure_store()
    if stored_token:
        return stored_token, token_file

    # Return raw_file_token (e.g. SAVED_IN_KEYCHAIN or YOUR_TOKEN_HERE) so validate_token gives tailored guidance.
    return raw_file_token, token_file


def save_token_from_stdin() -> int:
    """Save a pasted token from 1 Setup directly into Keychain (or token file fallback)."""
    pasted = sys.stdin.read().strip()
    if not pasted:
        return 0
    try:
        config = load_config()
        token_file_value = config.get("Diarization", "token_file", fallback="My Hugging Face Token.txt").strip()
    except Exception:
        token_file_value = "My Hugging Face Token.txt"
    token_file = resolve_path(token_file_value) if token_file_value else (APP_DIR / "My Hugging Face Token.txt")

    if TOKEN_PATTERN.match(pasted) and _save_token_to_secure_store(pasted):
        _write_keychain_marker_file(token_file)
        store_label = "macOS Keychain" if sys.platform == "darwin" else "private local storage"
        print(f"Saved token securely to {store_label} (and marked '{token_file.name}' as {KEYCHAIN_MARKER}).")
        return 0

    atomic_write_text(token_file, pasted + "\n", mode=0o600)
    print(f"Saved token to '{token_file.name}'.")
    return 0


def validate_token(token: str, token_file: Path | None) -> None:
    location = token_file.name if token_file else "My Hugging Face Token.txt"
    if token == KEYCHAIN_MARKER:
        raise RuntimeError(
            f"'{location}' says {KEYCHAIN_MARKER}, which means this folder's token was saved on another computer "
            f"(none was found in this computer's Keychain yet). Open '{location}', replace {KEYCHAIN_MARKER} "
            "with your hf_... token, save, and try again."
        )
    if not token or token in PLACEHOLDER_TOKENS:
        raise RuntimeError(
            f"No Hugging Face token is set yet. Open '{location}', paste your hf_... token, save, and try again."
        )
    if not TOKEN_PATTERN.match(token):
        raise RuntimeError(
            f"The token in '{location}' does not look like a Hugging Face token (it should start with hf_). "
            f"Check that only the token is pasted inside '{location}'."
        )


def build_job_settings(config: configparser.ConfigParser, validate_device: bool = False) -> JobSettings:
    watch_folder = resolve_path(config.get("Paths", "watch_folder", fallback="Audio to Transcribe"))
    transcript_folder = resolve_path(config.get("Paths", "transcript_folder", fallback="Finished Transcripts"))
    model_cache_dir = resolve_support_path(config.get("Paths", "model_cache_dir", fallback="models"))
    notif_raw = config.get("Paths", "notification_sound", fallback="").strip()
    notification_sound = resolve_support_path(notif_raw) if notif_raw else None

    watch_folder.mkdir(parents=True, exist_ok=True)
    transcript_folder.mkdir(parents=True, exist_ok=True)
    model_cache_dir.mkdir(parents=True, exist_ok=True)

    hf_token, token_file = read_hugging_face_token(config)
    validate_token(hf_token, token_file)

    output_format = config.get("Transcription", "output_format", fallback="txt").strip().lower()
    if output_format not in ("txt", "srt", "both"):
        LOG.warning("Unknown output_format '%s' in Settings.ini; defaulting to 'txt'.", output_format)
        output_format = "txt"

    device = choose_device(config) if validate_device else config.get("Whisper", "device", fallback="cpu").strip().lower() or "cpu"
    if device == "mps":
        device = "cpu"

    return JobSettings(
        watch_folder=watch_folder,
        transcript_folder=transcript_folder,
        model_cache_dir=model_cache_dir,
        notification_sound=notification_sound,
        hf_token=hf_token,
        token_file=token_file,
        model_size=config.get("Whisper", "model_size", fallback="medium").strip() or "medium",
        language=config.get("Whisper", "language", fallback="en").strip().lower() or "en",
        device=device,
        compute_type=config.get("Whisper", "compute_type", fallback="int8").strip() or "int8",
        batch_size=max(1, config.getint("Whisper", "batch_size", fallback=8)),
        sentences_per_timecode=max(0, config.getint("Transcription", "sentences_per_timecode", fallback=5)),
        output_format=output_format,
        skip_existing=config.getboolean("Transcription", "skip_if_transcript_exists", fallback=True),
        recursive_watch=config.getboolean("Transcription", "recursive_watch", fallback=False),
        process_existing=config.getboolean("Transcription", "process_existing_on_start", fallback=False),
        max_existing_on_start=max(1, config.getint("Transcription", "max_existing_files_on_start", fallback=25)),
    )


# ---------------------------------------------------------------------------
# Transcript & SRT Formatting (Matches Nathan's WhisperX Output Style)
# ---------------------------------------------------------------------------


def seconds_to_hms(seconds: float) -> str:
    seconds_int = max(0, int(seconds or 0))
    hours = seconds_int // 3600
    minutes = (seconds_int % 3600) // 60
    secs = seconds_int % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def extract_source_offset_seconds(filename: str) -> int:
    """Support meeting downloader filenames like `(part 2 of 4, from 2h00m)`."""
    match = SOURCE_OFFSET_PATTERN.search(filename)
    if not match:
        return 0
    return int(match.group(1)) * 3600 + int(match.group(2)) * 60


def _extract_sentences_from_segments(segments: list[dict[str, Any]]) -> list[tuple[str, float, str]]:
    """Convert WhisperX segments into `(speaker, start_sec, sentence_text)` tuples.

    Resolves speakers per sentence using word-level speaker labels when available
    (so short back-and-forth replies inside a single Whisper segment get the right
    speaker without mid-sentence word jitter), and falls back cleanly to segment
    text/timestamps when word alignment was unavailable.
    """
    sentences: list[tuple[str, float, str]] = []

    for segment in segments:
        seg_speaker = segment.get("speaker") or "Unknown speaker"
        seg_start = float(segment.get("start") or 0.0)
        words = segment.get("words") or []

        # Collect usable word tokens; fall back to segment["text"] if words are absent.
        valid_words = [w for w in words if isinstance(w, dict) and (w.get("word") or "").strip()]
        if not valid_words:
            raw_text = (segment.get("text") or "").strip()
            if not raw_text:
                continue
            # Split unaligned segment text into sentences while keeping segment timestamp.
            parts = re.findall(r"[^.?!]+[.?!]*", raw_text)
            for part in parts:
                cleaned = " ".join(part.split())
                if cleaned:
                    sentences.append((seg_speaker, seg_start, cleaned))
            continue

        buf_tokens: list[str] = []
        buf_speakers: list[str] = []
        buf_start: float | None = None
        last_known_time = seg_start

        def flush_sentence() -> None:
            nonlocal buf_tokens, buf_speakers, buf_start
            if not buf_tokens:
                return
            text = " ".join(buf_tokens).strip()
            if text:
                speaker = Counter(buf_speakers).most_common(1)[0][0] if buf_speakers else seg_speaker
                start_val = buf_start if buf_start is not None else last_known_time
                sentences.append((speaker, start_val, text))
            buf_tokens = []
            buf_speakers = []
            buf_start = None

        for word_info in valid_words:
            token = (word_info.get("word") or "").strip()
            if not token:
                continue
            w_start = word_info.get("start")
            if isinstance(w_start, (int, float)):
                last_known_time = float(w_start)
                if buf_start is None:
                    buf_start = float(w_start)
            elif buf_start is None:
                buf_start = last_known_time

            w_speaker = word_info.get("speaker")
            if w_speaker:
                buf_speakers.append(str(w_speaker))

            buf_tokens.append(token)
            if token.endswith((".", "?", "!")):
                flush_sentence()

        flush_sentence()

    return sentences


def format_transcript(
    segments: list[dict[str, Any]],
    sentences_per_timecode: int = 5,
    source_offset_seconds: int = 0,
) -> str:
    """Format readable transcript matching Nathan's newsroom WhisperX layout:

    SPEAKER_00 [00:00:01]: Sentence 1. Sentence 2. Sentence 3. Sentence 4. Sentence 5.
    [00:01:04] Sentence 6. Sentence 7.
    SPEAKER_01 [00:01:28]: Next speaker's reply.
    """
    sentences = _extract_sentences_from_segments(segments)
    if not sentences:
        return ""

    out_parts: list[str] = []
    current_speaker: str | None = None
    sentence_counter = 0

    for speaker, start_sec, text in sentences:
        adjusted_start = start_sec + source_offset_seconds
        timecode = seconds_to_hms(adjusted_start)

        if speaker != current_speaker:
            if out_parts:
                out_parts.append("\n")
            out_parts.append(f"{speaker} [{timecode}]: {text} ")
            current_speaker = speaker
            sentence_counter = 1 if text.endswith((".", "?", "!")) else 0
        else:
            if sentences_per_timecode > 0 and sentence_counter >= sentences_per_timecode:
                out_parts.append(f"\n[{timecode}] {text} ")
                sentence_counter = 1 if text.endswith((".", "?", "!")) else 0
            else:
                out_parts.append(f"{text} ")
                if text.endswith((".", "?", "!")):
                    sentence_counter += 1

    lines = [line.rstrip() for line in "".join(out_parts).splitlines()]
    return "\n".join(lines).strip() + "\n"


def seconds_to_srt_timestamp(seconds: float) -> str:
    total_ms = max(0, int(round((seconds or 0) * 1000)))
    hours = total_ms // 3_600_000
    minutes = (total_ms % 3_600_000) // 60_000
    secs = (total_ms % 60_000) // 1000
    millis = total_ms % 1000
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _segment_text(segment: dict[str, Any]) -> str:
    words = segment.get("words") or []
    if words:
        joined = " ".join((w.get("word") or "").strip() for w in words if (w.get("word") or "").strip()).strip()
        if joined:
            return joined
    return (segment.get("text") or "").strip()


def format_srt(segments: list[dict[str, Any]]) -> str:
    """Build standard zero-based .srt subtitle cues (kept clip-relative for video editors)."""
    blocks: list[str] = []
    index = 1
    for segment in segments:
        text = _segment_text(segment)
        if not text:
            continue
        speaker = segment.get("speaker")
        if speaker:
            text = f"{speaker}: {text}"
        start = seconds_to_srt_timestamp(float(segment.get("start") or 0.0))
        end = seconds_to_srt_timestamp(float(segment.get("end") or 0.0))
        blocks.append(f"{index}\n{start} --> {end}\n{text}\n")
        index += 1
    return ("\n".join(blocks).strip() + "\n") if blocks else ""


# ---------------------------------------------------------------------------
# File Stability, Manifest & Pending Queue Persistence
# ---------------------------------------------------------------------------


def atomic_write_text(path: Path, text: str, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".incomplete", dir=str(path.parent))
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        try:
            os.chmod(temp_path, mode)
        except OSError:
            pass
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)


def is_file_stable(
    path: Path,
    stop_event: threading.Event | None = None,
    check_interval: float = 2.0,
    stable_seconds: float = 6.0,
    max_wait_seconds: float = 1800.0,
) -> bool:
    """Wait until `path` is non-empty, stops changing size, and can be opened for reading."""
    LOG.info("Waiting for '%s' to finish copying...", path.name)
    last_size = -1
    stable_for = 0.0
    waited = 0.0

    while stable_for < stable_seconds:
        if stop_event is not None and stop_event.is_set():
            return False
        if waited >= max_wait_seconds:
            LOG.warning("Timed out waiting for '%s' to finish copying.", path.name)
            return False
        try:
            stat = path.stat()
            current_size = stat.st_size
        except FileNotFoundError:
            LOG.warning("File was moved or deleted before transcription started: %s", path.name)
            return False
        except OSError as exc:
            LOG.warning("Waiting for file access on '%s': %s", path.name, exc)
            current_size = -1

        if current_size > 0 and current_size == last_size:
            stable_for += check_interval
        else:
            stable_for = 0.0
            last_size = current_size

        if stable_for >= stable_seconds:
            try:
                with open(path, "rb") as probe:
                    probe.read(16)
                return True
            except OSError:
                stable_for = 0.0

        time.sleep(check_interval)
        waited += check_interval

    return False


class ManifestStore:
    """Tracks which source audio file produced each transcript to avoid stem collisions."""

    def __init__(self, path: Path = MANIFEST_FILE) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._entries: dict[str, dict[str, Any]] = self._load()

    def _load(self) -> dict[str, dict[str, Any]]:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def get(self, output_path: Path) -> dict[str, Any] | None:
        with self._lock:
            return self._entries.get(str(output_path.resolve()))

    def record(self, output_paths: list[Path], audio_path: Path) -> None:
        try:
            stat = audio_path.stat()
            meta = {
                "source_path": str(audio_path.resolve()),
                "source_name": audio_path.name,
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
                "saved_at": dt.datetime.now().isoformat(timespec="seconds"),
            }
        except OSError:
            return
        with self._lock:
            for out in output_paths:
                self._entries[str(out.resolve())] = meta
            try:
                atomic_write_text(self._path, json.dumps(self._entries, indent=2) + "\n")
            except OSError:
                pass


class PendingQueueStore:
    """Persists queued/in-flight files so Ctrl+C -> restart resumes unfinished work."""

    def __init__(self, path: Path = PENDING_QUEUE_FILE) -> None:
        self._path = path
        self._lock = threading.Lock()

    def load(self) -> list[Path]:
        with self._lock:
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                if isinstance(raw, list):
                    return [Path(item) for item in raw if isinstance(item, str)]
            except Exception:
                pass
            return []

    def save(self, paths: list[Path]) -> None:
        with self._lock:
            try:
                if not paths:
                    self._path.unlink(missing_ok=True)
                else:
                    payload = [str(p) for p in paths]
                    atomic_write_text(self._path, json.dumps(payload, indent=2) + "\n")
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Core Transcription Engine
# ---------------------------------------------------------------------------


def friendly_error(exc: Exception) -> str:
    text = str(exc)
    lower = text.lower()
    if "gated" in lower or "401" in lower or "403" in lower or "unauthorized" in lower:
        return (
            "Hugging Face denied access to a speaker-label model. Make sure your token in "
            "'My Hugging Face Token.txt' is valid AND that you clicked Agree on BOTH "
            "https://huggingface.co/pyannote/segmentation-3.0 and "
            "https://huggingface.co/pyannote/speaker-diarization-3.1 while logged into that account."
        )
    if "no space" in lower or "disk" in lower:
        return "Your computer appears to be out of disk space. Free up space and try again."
    if "ffmpeg" in lower:
        return "Could not decode this audio file (it may be corrupted or an unsupported format)."
    if "network" in lower or "timed out" in lower or "temporary failure" in lower or "connection" in lower:
        return "A network download failed while checking models. Check Wi-Fi and try again."
    return f"Technical error: {text}"


class TranscriberEngine:
    def __init__(self, activity: ActivityTracker, manifest: ManifestStore) -> None:
        self.activity = activity
        self.manifest = manifest

    @staticmethod
    def output_dir_for(audio_path: Path, settings: JobSettings) -> Path:
        try:
            relative = audio_path.resolve().relative_to(settings.watch_folder.resolve())
        except ValueError:
            relative = Path(audio_path.name)
        return settings.transcript_folder / relative.parent

    def stem_for_audio(self, audio_path: Path, settings: JobSettings) -> str:
        """Use clean `Name_transcript.txt`, adding `(ext)` only if a different audio file shares the stem."""
        out_dir = self.output_dir_for(audio_path, settings)
        base_stem = audio_path.stem
        audio_resolved = str(audio_path.resolve())
        ext_tag = audio_path.suffix.lstrip(".").lower() or "audio"

        for ext in ("txt", "srt"):
            candidate = out_dir / f"{base_stem}_transcript.{ext}"
            if not candidate.exists():
                continue
            recorded = self.manifest.get(candidate)
            if recorded and recorded.get("source_path") and recorded["source_path"] != audio_resolved:
                return f"{base_stem} ({ext_tag})"
            # Also check if another audio sibling with the same stem and different suffix sits right beside it.
            if recorded is None:
                for sibling in audio_path.parent.glob(f"{base_stem}.*"):
                    if sibling.resolve() != audio_path.resolve() and sibling.suffix.lower() in VALID_EXTENSIONS:
                        return f"{base_stem} ({ext_tag})"
        return base_stem

    def planned_outputs(self, audio_path: Path, settings: JobSettings) -> list[tuple[str, Path]]:
        out_dir = self.output_dir_for(audio_path, settings)
        stem = self.stem_for_audio(audio_path, settings)
        outputs: list[tuple[str, Path]] = []
        if settings.output_format in ("txt", "both"):
            outputs.append(("txt", out_dir / f"{stem}_transcript.txt"))
        if settings.output_format in ("srt", "both"):
            outputs.append(("srt", out_dir / f"{stem}_transcript.srt"))
        return outputs

    def failure_path_for(self, audio_path: Path, settings: JobSettings) -> Path:
        out_dir = self.output_dir_for(audio_path, settings)
        stem = self.stem_for_audio(audio_path, settings)
        return out_dir / f"{stem}_FAILED.txt"

    def should_accept_file(self, path: Path, settings: JobSettings, log_skip: bool = True) -> bool:
        if path.name.startswith((".", "_", "~$")):
            return False
        if path.suffix.lower() not in VALID_EXTENSIONS:
            return False
        outputs = self.planned_outputs(path, settings)
        if settings.skip_existing and outputs and all(out.exists() for _, out in outputs):
            # If the audio file was replaced with a newer/different recording, re-transcribe it!
            recorded = self.manifest.get(outputs[0][1])
            try:
                stat = path.stat()
                if recorded and (
                    recorded.get("size") != stat.st_size or recorded.get("mtime_ns") != stat.st_mtime_ns
                ):
                    LOG.info("Detected updated audio recording for '%s'; re-transcribing.", path.name)
                    return True
            except OSError:
                pass
            if log_skip:
                LOG.info(
                    "Skipping '%s' because transcript already exists. (Delete the transcript if you want to redo it.)",
                    path.name,
                )
            return False
        return True

    def transcribe_file(
        self,
        audio_path: Path,
        settings: JobSettings,
        stop_event: threading.Event,
    ) -> bool:
        if not self.should_accept_file(audio_path, settings):
            return False

        self.activity.update(
            "processing",
            f"Waiting for {audio_path.name} to finish copying",
            {
                "name": audio_path.name,
                "path": str(audio_path),
                "stage": "Waiting for file to stabilize",
                "detected_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
                "started_at": None,
            },
        )
        if not is_file_stable(audio_path, stop_event=stop_event):
            return False
        if stop_event.is_set():
            return False

        outputs = self.planned_outputs(audio_path, settings)
        check_system_health()
        t0 = time.time()

        model = None
        model_a = None
        diarize_model = None

        with prevent_idle_sleep():
            try:
                whisperx = import_whisperx()
                audio = whisperx.load_audio(str(audio_path))
                audio_minutes = len(audio) / 16000.0 / 60.0

                LOG.info("-" * 64)
                LOG.info(
                    "Starting '%s' (%.1f min of audio). Terminal may sit quietly during long steps.",
                    audio_path.name,
                    audio_minutes,
                )

                if stop_event.is_set():
                    return False

                # Step 1/4: Speech recognition
                self.activity.set_stage("[1/4] Transcribing speech", processing_started=True)
                LOG.info("[1/4] Transcribing speech (model: %s, language: %s)...", settings.model_size, settings.language)
                transcribe_language = None if settings.language == "auto" else settings.language
                with trusted_torch_checkpoints():
                    model = whisperx.load_model(
                        settings.model_size,
                        settings.device,
                        compute_type=settings.compute_type,
                        download_root=str(settings.model_cache_dir),
                        language=transcribe_language,
                    )
                result = model.transcribe(audio, batch_size=settings.batch_size, language=transcribe_language)
                detected_language = result.get("language") or transcribe_language or "en"
                del model
                model = None
                cleanup_torch(settings.device)

                if stop_event.is_set():
                    return False

                # Step 2/4: Word-timestamp alignment (graceful fallback if unavailable for language)
                self.activity.set_stage("[2/4] Aligning word timestamps")
                align_language = detected_language if settings.language == "auto" else settings.language
                LOG.info("[2/4] Aligning word timestamps (language: %s)...", align_language)
                try:
                    with trusted_torch_checkpoints():
                        model_a, metadata = whisperx.load_align_model(
                            language_code=align_language,
                            device=settings.device,
                            model_dir=str(settings.model_cache_dir),
                        )
                    result = whisperx.align(
                        result["segments"],
                        model_a,
                        metadata,
                        audio,
                        settings.device,
                        return_char_alignments=False,
                    )
                except Exception as align_exc:
                    LOG.warning(
                        "Word-level alignment unavailable for '%s' (%s); continuing with phrase timestamps.",
                        align_language,
                        align_exc,
                    )
                    result = {"segments": result["segments"]}
                finally:
                    if model_a is not None:
                        del model_a
                        model_a = None
                    cleanup_torch(settings.device)

                if stop_event.is_set():
                    return False

                # Step 3/4: Speaker diarization
                self.activity.set_stage("[3/4] Identifying speakers")
                LOG.info("[3/4] Identifying speakers...")
                with trusted_torch_checkpoints():
                    DiarizationPipeline = import_diarization_pipeline()
                    diarize_model = DiarizationPipeline(use_auth_token=settings.hf_token, device=settings.device)
                    diarize_segments = diarize_model(audio)
                result = whisperx.assign_word_speakers(diarize_segments, result)
                del diarize_model
                diarize_model = None
                cleanup_torch(settings.device)

                if stop_event.is_set():
                    return False

                # Step 4/4: Format & write output atomically
                self.activity.set_stage("[4/4] Saving transcript")
                source_offset = extract_source_offset_seconds(audio_path.name)
                if source_offset:
                    LOG.info(
                        "Applying source meeting timecode offset of %dh%02dm from filename.",
                        source_offset // 3600,
                        (source_offset % 3600) // 60,
                    )

                written_paths: list[Path] = []
                for fmt, output_path in outputs:
                    if fmt == "srt":
                        body = format_srt(result["segments"])
                    else:
                        body = format_transcript(
                            result["segments"],
                            sentences_per_timecode=settings.sentences_per_timecode,
                            source_offset_seconds=source_offset,
                        )
                    atomic_write_text(output_path, body)
                    written_paths.append(output_path)
                    LOG.info("[4/4] Saved transcript: %s", output_path)

                self.manifest.record(written_paths, audio_path)
                failure_path = self.failure_path_for(audio_path, settings)
                if failure_path.exists():
                    failure_path.unlink(missing_ok=True)

                elapsed_min = (time.time() - t0) / 60.0
                ratio = elapsed_min / audio_minutes if audio_minutes > 0 else 0.0
                LOG.info(
                    "Finished '%s' in %.1f min for %.1f min of audio (%.1fx realtime).",
                    audio_path.name,
                    elapsed_min,
                    audio_minutes,
                    ratio,
                )
                if ratio > 3.0 and audio_minutes >= 2.0:
                    LOG.warning(
                        "Note: That run was slower than usual (>3x realtime). Closing heavy apps/tabs "
                        "or restarting your computer can help if memory is tight."
                    )
                LOG.info("-" * 64)

                if written_paths:
                    self.activity.record_completed(audio_path, written_paths[0], elapsed_min, audio_minutes, ratio)
                play_notification(settings.notification_sound)
                return True
            except Exception as exc:
                if stop_event.is_set():
                    return False
                message = friendly_error(exc)
                LOG.error("Could not finish '%s': %s", audio_path.name, message)
                LOG.debug(traceback.format_exc())
                self._write_failure_file(audio_path, settings, message)
                return False
            finally:
                for obj in (model, model_a, diarize_model):
                    if obj is not None:
                        del obj
                cleanup_torch(settings.device)

    def _write_failure_file(self, audio_path: Path, settings: JobSettings, message: str) -> None:
        failure_path = self.failure_path_for(audio_path, settings)
        check_cmd = "More Tools/Check Setup.command" if sys.platform == "darwin" else "Check Setup.bat"
        support_cmd = "More Tools/Make Support Zip.command" if sys.platform == "darwin" else "Make Support Zip.bat"
        retry_hint = (
            "click the audio file in Finder, press Command+Delete then Command+Z (Undo)"
            if sys.platform == "darwin"
            else "move the audio file out of your watch folder and drop it back in"
        )
        body = (
            "Audio Transcriber could not finish this file.\n\n"
            f"Audio file:\n{audio_path.name}\n\n"
            f"What happened:\n{message}\n\n"
            "What to try:\n"
            "1. Look at the open Terminal window for the latest message.\n"
            f"2. Run '{check_cmd}' to verify your token and setup.\n"
            f"3. Once fixed, {retry_hint} to retry.\n"
            f"4. Still stuck? Run '{support_cmd}' and send the zip file for help.\n\n"
            f"Time:\n{time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        )
        try:
            atomic_write_text(failure_path, body)
            LOG.info("Saved error note: %s", failure_path)
        except Exception as note_exc:
            LOG.error("Could not save error note: %s", note_exc)


def play_notification(sound_path: Path | None) -> None:
    if sound_path is None or not sound_path.exists():
        return
    try:
        if sys.platform == "darwin":
            afplay = Path("/usr/bin/afplay")
            if afplay.exists():
                subprocess.run(
                    [str(afplay), str(sound_path)],
                    check=False,
                    timeout=10,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
        elif sys.platform == "win32":
            import winsound

            winsound.PlaySound(str(sound_path), winsound.SND_FILENAME | winsound.SND_NODEFAULT)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Folder Watcher, Queue Coordinator & Live Settings Hot-Reload
# ---------------------------------------------------------------------------


def _normalize_watch_path(raw_path: Path) -> Path:
    """Resolve symlinks (e.g. /var -> /private/var on macOS) even when raw_path was just deleted."""
    try:
        if raw_path.exists():
            return raw_path.resolve()
        return raw_path.parent.resolve() / raw_path.name
    except OSError:
        return raw_path


class WorkCoordinator:
    """Thread-safe queue manager deduplicating events and persisting pending files."""

    def __init__(self, engine: TranscriberEngine, pending_store: PendingQueueStore, initial_settings: JobSettings) -> None:
        self.engine = engine
        self.pending_store = pending_store
        self._lock = threading.Lock()
        self._settings = initial_settings
        self._queue: queue.Queue[tuple[Path, JobSettings]] = queue.Queue()
        self._queued_paths: list[Path] = []
        self._in_flight: Path | None = None
        # Track `(size, mtime_ns)` of recently failed/skipped attempts so modified events don't infinite-loop.
        self._attempted_signatures: dict[Path, tuple[int, int]] = {}
        self._last_attempt_mono: dict[Path, float] = {}
        # Track pre-existing files at startup when process_existing=False so untouched old archive files
        # stay ignored, while Finder Delete + Undo (Cmd+Delete -> Cmd+Z) triggers immediately!
        self._startup_signatures: dict[Path, tuple[int, int]] = {}
        self._settle_until_mono: float = 0.0

    def get_settings(self) -> JobSettings:
        with self._lock:
            return self._settings

    def update_settings(self, new_settings: JobSettings) -> None:
        with self._lock:
            self._settings = new_settings

    def snapshot_existing_files(self, settings: JobSettings, settle_seconds: float = STARTUP_SETTLE_SECONDS) -> None:
        """Record pre-existing files in watch_folder before observer starts when process_existing=False."""
        snap: dict[Path, tuple[int, int]] = {}
        if not settings.process_existing and settings.watch_folder.exists():
            pattern = "**/*" if settings.recursive_watch else "*"
            for candidate in settings.watch_folder.glob(pattern):
                try:
                    if candidate.is_file() and candidate.suffix.lower() in VALID_EXTENSIONS:
                        st = candidate.stat()
                        snap[candidate.resolve()] = (st.st_size, st.st_mtime_ns)
                except OSError:
                    continue
        with self._lock:
            self._startup_signatures = snap
            self._settle_until_mono = time.monotonic() + settle_seconds

    def mark_observer_started(self, settle_seconds: float = STARTUP_SETTLE_SECONDS) -> None:
        with self._lock:
            self._settle_until_mono = time.monotonic() + settle_seconds

    def note_removed(self, raw_path: Path) -> None:
        """Clear startup/failure ignore state when a file leaves the folder (e.g. Finder Cmd+Delete)."""
        path = _normalize_watch_path(raw_path)
        with self._lock:
            self._startup_signatures.pop(path, None)
            self._attempted_signatures.pop(path, None)
            self._last_attempt_mono.pop(path, None)

    def enqueue(self, raw_path: Path, reason: str = "detected") -> bool:
        path = _normalize_watch_path(raw_path)
        now_mono = time.monotonic()

        with self._lock:
            settings = self._settings
            if path == self._in_flight or path in self._queued_paths:
                return False

            try:
                stat = path.stat()
                sig = (stat.st_size, stat.st_mtime_ns)
            except OSError:
                return False

            startup_sig = self._startup_signatures.get(path)
            if startup_sig is not None:
                if reason in ("resume", "startup"):
                    self._startup_signatures.pop(path, None)
                elif reason in ("created", "moved"):
                    # Ignore buffered FSEvents replay from right before observer.start(),
                    # then treat any subsequent created/moved event (including instant 0ms Delete+Undo) as live!
                    if now_mono < self._settle_until_mono and sig == startup_sig:
                        return False
                    self._startup_signatures.pop(path, None)
                    self._attempted_signatures.pop(path, None)
                elif reason == "modified":
                    # Ignore Spotlight/Finder metadata touches on unchanged pre-existing archive files.
                    if sig == startup_sig:
                        return False
                    self._startup_signatures.pop(path, None)
                    self._attempted_signatures.pop(path, None)
            elif reason in ("created", "moved"):
                # Allow immediate retry on re-drop / Delete+Undo, while debouncing duplicate FSEvents within 1.5s.
                if now_mono - self._last_attempt_mono.get(path, 0.0) >= 1.5:
                    self._attempted_signatures.pop(path, None)
                elif self._attempted_signatures.get(path) == sig:
                    return False

            # Avoid infinitely retrying an unchanged failed/skipped file on modified events.
            if reason == "modified" and self._attempted_signatures.get(path) == sig:
                return False

            if not self.engine.should_accept_file(path, settings, log_skip=(reason != "modified")):
                self._attempted_signatures[path] = sig
                self._last_attempt_mono[path] = now_mono
                return False

            self._queued_paths.append(path)
            self._queue.put((path, settings))
            self._persist_locked()

        LOG.info("Queued audio file: %s", path.name)
        return True

    def pop_next(self, timeout: float = 1.0) -> tuple[Path, JobSettings] | None:
        try:
            path, settings = self._queue.get(timeout=timeout)
        except queue.Empty:
            return None
        with self._lock:
            self._in_flight = path
        return path, settings

    def finish_job(self, path: Path, succeeded: bool) -> None:
        with self._lock:
            if path in self._queued_paths:
                self._queued_paths.remove(path)
            if self._in_flight == path:
                self._in_flight = None
            try:
                stat = path.stat()
                self._attempted_signatures[path] = (stat.st_size, stat.st_mtime_ns)
                self._last_attempt_mono[path] = time.monotonic()
            except OSError:
                self._attempted_signatures.pop(path, None)
                self._last_attempt_mono.pop(path, None)
            self._persist_locked()
        self._queue.task_done()

    def leave_for_resume(self) -> None:
        """Keep interrupted in-flight + remaining queued files saved in pending_queue.json on Ctrl+C."""
        with self._lock:
            self._persist_locked()

    def _persist_locked(self) -> None:
        ordered: list[Path] = []
        if self._in_flight is not None:
            ordered.append(self._in_flight)
        for p in self._queued_paths:
            if p not in ordered:
                ordered.append(p)
        self.pending_store.save(ordered)


def make_event_handler(coordinator: WorkCoordinator):
    FileSystemEventHandler, _, _ = import_watchdog()

    class AudioEventHandler(FileSystemEventHandler):
        def on_created(self, event) -> None:
            if not event.is_directory:
                coordinator.enqueue(Path(event.src_path), reason="created")

        def on_deleted(self, event) -> None:
            if not event.is_directory:
                coordinator.note_removed(Path(event.src_path))

        def on_moved(self, event) -> None:
            if not event.is_directory:
                coordinator.note_removed(Path(event.src_path))
                coordinator.enqueue(Path(event.dest_path), reason="moved")

        def on_modified(self, event) -> None:
            if not event.is_directory:
                coordinator.enqueue(Path(event.src_path), reason="modified")

    return AudioEventHandler()


def worker_loop(
    engine: TranscriberEngine,
    coordinator: WorkCoordinator,
    activity: ActivityTracker,
    stop_event: threading.Event,
) -> None:
    while not stop_event.is_set():
        item = coordinator.pop_next(timeout=1.0)
        if item is None:
            continue
        path, job_settings = item
        try:
            ok = engine.transcribe_file(path, job_settings, stop_event)
            if stop_event.is_set():
                coordinator.leave_for_resume()
                break
            coordinator.finish_job(path, succeeded=ok)
            cur = coordinator.get_settings()
            activity.update("watching", f"Watching {cur.watch_folder}", None)
        except BaseException as unexpected:
            if stop_event.is_set() or isinstance(unexpected, KeyboardInterrupt):
                coordinator.leave_for_resume()
                break
            LOG.error("Unexpected error while handling '%s': %s", path.name, unexpected)
            LOG.debug(traceback.format_exc())
            coordinator.finish_job(path, succeeded=False)
            cur = coordinator.get_settings()
            activity.update("watching", f"Watching {cur.watch_folder}", None)


# ---------------------------------------------------------------------------
# Hugging Face Gated-File Access Check & Model Download
# ---------------------------------------------------------------------------


def check_hf_gated_file_access(token: str, model_id: str) -> tuple[bool, str]:
    """Authenticate against a real gated file (`/resolve/main/config.yaml`), NOT public repo metadata."""
    url = f"https://huggingface.co/{model_id}/resolve/main/config.yaml"
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": "audio-transcriber-preflight",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            if 200 <= response.status < 400:
                response.read(64)
                return True, "ok"
            return False, f"unexpected HTTP status {response.status}"
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            return (
                False,
                "token rejected (401). Open 'My Hugging Face Token.txt' and check that your full hf_... token is pasted.",
            )
        if exc.code == 403:
            return (
                False,
                f"terms not accepted yet (403). Open https://huggingface.co/{model_id} in your browser, "
                "log into the SAME Hugging Face account, click Agree/Accept, then try again.",
            )
        if exc.code == 404:
            return False, f"model '{model_id}' not found (404)."
        return False, f"HTTP error {exc.code}"
    except Exception as exc:
        return False, f"network check could not reach Hugging Face ({exc})"


def ensure_nltk_data(download: bool = False) -> bool:
    nltk_dir = MODELS_ROOT / "nltk_data"
    nltk_dir.mkdir(parents=True, exist_ok=True)
    os.environ["NLTK_DATA"] = str(nltk_dir)
    try:
        import nltk
        from nltk.data import find

        try:
            find("tokenizers/punkt_tab/english")
            LOG.info("PASS: Sentence tokenizer data is ready.")
            return True
        except LookupError:
            if not download:
                LOG.info("NOTE: Sentence tokenizer data will be downloaded with the AI models.")
                return True
            LOG.info("[1/4] Downloading NLTK sentence tokenizer data...")
            return bool(nltk.download("punkt_tab", download_dir=str(nltk_dir), quiet=True))
    except Exception as exc:
        LOG.error("FAIL: Could not verify/download NLTK data: %s", exc)
        return False


def free_space_gb(path: Path) -> float:
    usage = shutil.disk_usage(path)
    return usage.free / (1024**3)


def download_required_models(settings: JobSettings) -> bool:
    """Download/verify AI models SEQUENTIALLY so peak memory stays gentle on 8 GB Macs."""
    LOG.info("Downloading and verifying AI models (this can take several minutes the first time)...")
    if not ensure_nltk_data(download=True):
        return False

    whisperx = import_whisperx()
    transcribe_language = None if settings.language == "auto" else settings.language
    align_language = "en" if settings.language == "auto" else settings.language

    try:
        LOG.info("[2/4] Downloading/checking Whisper '%s' speech model...", settings.model_size)
        with trusted_torch_checkpoints():
            model = whisperx.load_model(
                settings.model_size,
                settings.device,
                compute_type=settings.compute_type,
                download_root=str(settings.model_cache_dir),
                language=transcribe_language,
            )
        del model
        cleanup_torch(settings.device)

        LOG.info("[3/4] Downloading/checking word-alignment model (%s)...", align_language)
        try:
            with trusted_torch_checkpoints():
                model_a, _ = whisperx.load_align_model(
                    language_code=align_language,
                    device=settings.device,
                    model_dir=str(settings.model_cache_dir),
                )
            del model_a
            cleanup_torch(settings.device)
        except Exception as align_exc:
            LOG.warning("Alignment model for '%s' skipped (%s).", align_language, align_exc)

        LOG.info("[4/4] Downloading/checking speaker-identification models...")
        with trusted_torch_checkpoints():
            DiarizationPipeline = import_diarization_pipeline()
            diarize_model = DiarizationPipeline(use_auth_token=settings.hf_token, device=settings.device)
        del diarize_model
        cleanup_torch(settings.device)

        LOG.info("PASS: All AI models are downloaded and ready inside Support Files/models.")
        return True
    except Exception as exc:
        LOG.error("FAIL: Could not download/load AI models: %s", friendly_error(exc))
        LOG.debug(traceback.format_exc())
        cleanup_torch(settings.device)
        return False


def run_preflight(download_models: bool = False) -> int:
    setup_logging()
    LOG.info("Audio Transcriber version: %s", app_version())
    LOG.info("Checking setup...")
    ok = True

    if not ensure_ffmpeg_on_path():
        ok = False

    try:
        config = load_config()
        settings = build_job_settings(config, validate_device=True)
        LOG.info("PASS: Settings.ini and Hugging Face token format look good.")
        LOG.info("      Audio input folder : %s", settings.watch_folder)
        LOG.info("      Transcript folder  : %s", settings.transcript_folder)
    except Exception as exc:
        LOG.error("FAIL: %s", exc)
        return 1

    # Verify write access to transcript folder.
    try:
        with tempfile.NamedTemporaryFile(prefix=".check_", dir=settings.transcript_folder, delete=True) as tmp:
            tmp.write(b"ok")
    except OSError as exc:
        ok = False
        LOG.error("FAIL: Cannot write to transcript folder '%s': %s", settings.transcript_folder, exc)

    free_gb = free_space_gb(APP_DIR)
    if free_gb >= RECOMMENDED_FREE_GB:
        LOG.info("PASS: %.1f GB free disk space available.", free_gb)
    elif free_gb >= MIN_FREE_GB:
        LOG.warning(
            "NOTE: %.1f GB free disk space. That is enough to run, though 8+ GB is comfortable for first-time downloads.",
            free_gb,
        )
    else:
        ok = False
        LOG.error("FAIL: Only %.1f GB free disk space. Please free up at least %s GB.", free_gb, MIN_FREE_GB)

    try:
        import_torch()
        import_whisperx()
        import_watchdog()
        LOG.info("PASS: Python transcription packages import cleanly.")
        if not ensure_nltk_data(download=False):
            ok = False
    except Exception as exc:
        ok = False
        LOG.error("FAIL: Python packages are missing or damaged: %s", exc)
        LOG.error("      Run '1 Setup' (or 'More Tools/Repair Setup').")

    for model_id in PYANNOTE_GATED_MODELS:
        access_ok, detail = check_hf_gated_file_access(settings.hf_token, model_id)
        if access_ok:
            LOG.info("PASS: Hugging Face access verified for %s.", model_id)
        else:
            ok = False
            LOG.error("FAIL: Hugging Face check failed for %s — %s", model_id, detail)

    if download_models and ok:
        ok = download_required_models(settings)

    if ok:
        LOG.info("Setup check passed! Ready to run '3 Start Transcriber'.")
        return 0

    LOG.error("Setup check did not pass yet. Fix the FAIL line(s) above and run Check Setup again.")
    return 1


# ---------------------------------------------------------------------------
# Auto-Start Management (macOS LaunchAgent via plistlib + Windows Startup)
# ---------------------------------------------------------------------------


def _macos_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{MACOS_AUTOSTART_LABEL}.plist"


def _windows_startup_cmd_path() -> Path:
    appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "Audio Transcriber.cmd"


def manage_autostart(action: str) -> int:
    """Safely inspect, enable, or disable login auto-start without admin rights."""
    if sys.platform == "darwin":
        plist_path = _macos_plist_path()
        start_cmd = APP_DIR / "3 Start Transcriber.command"
        uid = str(os.getuid())
        domain = f"gui/{uid}"

        if action == "status":
            if plist_path.exists():
                try:
                    data = plistlib.loads(plist_path.read_bytes())
                    args = data.get("ProgramArguments") or []
                    target = args[-1] if args else ""
                    print(f"ON ({target})")
                except Exception:
                    print("ON")
            else:
                print("OFF")
            return 0

        # Always clean up any legacy labels first.
        for legacy_label in MACOS_LEGACY_LABELS:
            legacy_plist = Path.home() / "Library" / "LaunchAgents" / f"{legacy_label}.plist"
            subprocess.run(["/bin/launchctl", "bootout", domain, str(legacy_plist)], capture_output=True, check=False)
            subprocess.run(["/bin/launchctl", "disable", f"{domain}/{legacy_label}"], capture_output=True, check=False)
            legacy_plist.unlink(missing_ok=True)

        if action == "off":
            subprocess.run(["/bin/launchctl", "bootout", domain, str(plist_path)], capture_output=True, check=False)
            subprocess.run(
                ["/bin/launchctl", "disable", f"{domain}/{MACOS_AUTOSTART_LABEL}"],
                capture_output=True,
                check=False,
            )
            plist_path.unlink(missing_ok=True)
            print("Auto-Start at Login is now OFF.")
            print("Audio Transcriber will not open automatically when you log in.")
            print("(If a transcriber Terminal window is currently open, press Control+C in it to stop.)")
            return 0

        if action == "on":
            if not start_cmd.exists():
                print(f"ERROR: Could not find '{start_cmd.name}' in {APP_DIR}")
                return 1
            start_cmd.chmod(start_cmd.stat().st_mode | 0o111)
            plist_path.parent.mkdir(parents=True, exist_ok=True)

            # Clear any previous `launchctl disable` override BEFORE writing the plist!
            subprocess.run(
                ["/bin/launchctl", "enable", f"{domain}/{MACOS_AUTOSTART_LABEL}"],
                capture_output=True,
                check=False,
            )
            subprocess.run(["/bin/launchctl", "bootout", domain, str(plist_path)], capture_output=True, check=False)

            plist_payload = {
                "Label": MACOS_AUTOSTART_LABEL,
                "ProgramArguments": ["/usr/bin/open", "-a", "Terminal", str(start_cmd)],
                "WorkingDirectory": str(APP_DIR),
                "RunAtLoad": True,
            }
            fd, temp_name = tempfile.mkstemp(prefix=".autostart.", suffix=".plist", dir=str(plist_path.parent))
            temp_path = Path(temp_name)
            try:
                with os.fdopen(fd, "wb") as handle:
                    plistlib.dump(plist_payload, handle, sort_keys=False)
                temp_path.chmod(0o644)
                os.replace(temp_path, plist_path)
            finally:
                if temp_path.exists():
                    temp_path.unlink(missing_ok=True)

            # Validate XML syntax using macOS plutil when present.
            plutil = Path("/usr/bin/plutil")
            if plutil.exists():
                subprocess.run([str(plutil), "-lint", str(plist_path)], check=True, stdout=subprocess.DEVNULL)

            print("Auto-Start at Login is now ON.")
            print("Every time you log into this Mac, '3 Start Transcriber.command' will open")
            print("automatically in a visible Terminal window (so you can see status or stop/restart).")
            print("Note: If you ever move the audio-transcriber folder to a new place, run this toggle again.")
            return 0

    elif sys.platform == "win32":
        startup_cmd = _windows_startup_cmd_path()
        target_bat = APP_DIR / "3 Start Transcriber.bat"
        if not target_bat.exists():
            target_bat = SUPPORT_DIR / "windows" / "3 Start Transcriber.bat"

        if action == "status":
            print("ON" if startup_cmd.exists() else "OFF")
            return 0
        if action == "off":
            startup_cmd.unlink(missing_ok=True)
            print("Auto-Start at Login is now OFF.")
            return 0
        if action == "on":
            startup_cmd.parent.mkdir(parents=True, exist_ok=True)
            content = f'@echo off\r\nstart "Audio Transcriber" cmd /k ""{target_bat}""\r\n'
            startup_cmd.write_text(content, encoding="utf-8")
            print("Auto-Start at Login is now ON.")
            print("Audio Transcriber will open in a visible window automatically when you sign into Windows.")
            return 0

    print(f"Auto-start management is not supported on platform '{sys.platform}'.")
    return 1


# ---------------------------------------------------------------------------
# Privacy-Redacted Support Diagnostic Zip
# ---------------------------------------------------------------------------


def _redact_sensitive_text(text: str) -> str:
    """Strip hf_ tokens, home-folder usernames, and quoted audio/transcript filenames."""
    redacted = TOKEN_REDACT_PATTERN.sub("hf_[REDACTED_TOKEN]", text)
    home_str = str(Path.home())
    if home_str:
        redacted = redacted.replace(home_str, "~")
    # Redact quoted audio filenames in logs (`'Confidential Source.m4a'` -> `'<audio-file>'`)
    redacted = re.sub(
        r"'[^'\n]+\.(?:wav|mp3|m4a|mp4|mov|flac|ogg|opus|aac|wma|webm)'",
        "'<redacted-audio-file>'",
        redacted,
        flags=re.IGNORECASE,
    )
    redacted = re.sub(r"(_transcript\.(?:txt|srt)|_FAILED\.txt)", r" <redacted-output>\1", redacted)
    return redacted


def make_support_zip() -> int:
    out_zip = APP_DIR / "Audio Transcriber Support Info.zip"
    print("=== Make Support Zip ===")
    print()
    print("Creating a troubleshooting zip file...")
    print("Privacy protection: Your Hugging Face token, audio files, transcripts, and")
    print("audio filenames in logs are automatically excluded or redacted.")
    print()

    system_lines = [
        "Audio Transcriber Support Info",
        f"Created: {dt.datetime.now().isoformat(timespec='seconds')}",
        f"Version: {app_version()}",
        f"Platform: {sys.platform} ({os.uname().machine if hasattr(os, 'uname') else os.environ.get('PROCESSOR_ARCHITECTURE', 'unknown')})",
        f"Python: {sys.version}",
        f"Free disk space: {free_space_gb(APP_DIR):.1f} GB",
    ]
    if sys.platform == "darwin":
        sw = subprocess.run(["/usr/bin/sw_vers"], capture_output=True, text=True, check=False).stdout.strip()
        if sw:
            system_lines.extend(["", "macOS:", sw])

    settings_redacted = ""
    if CONFIG_PATH.exists():
        raw_cfg = CONFIG_PATH.read_text(encoding="utf-8", errors="replace")
        cfg_lines = []
        for line in raw_cfg.splitlines():
            if line.strip().lower().startswith("huggingface_token"):
                cfg_lines.append("huggingface_token = [REDACTED]")
            else:
                cfg_lines.append(_redact_sensitive_text(line))
        settings_redacted = "\n".join(cfg_lines) + "\n"

    log_tail = ""
    if LOG_FILE.exists():
        lines = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()[-400:]
        log_tail = "\n".join(_redact_sensitive_text(line) for line in lines) + "\n"

    out_zip.unlink(missing_ok=True)
    with zipfile.ZipFile(out_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("support-info/system.txt", "\n".join(system_lines) + "\n")
        if settings_redacted:
            zf.writestr("support-info/Settings-redacted.ini", settings_redacted)
        if log_tail:
            zf.writestr("support-info/transcriber-last-400-lines-redacted.log", log_tail)

    print(f"Created: {out_zip}")
    print("You can safely send that zip file in Slack or email for troubleshooting.")
    return 0


# ---------------------------------------------------------------------------
# Main Watcher Service Loop
# ---------------------------------------------------------------------------


def run_service() -> int:
    setup_logging()
    lock = SingleInstanceLock(LOCK_FILE)
    acquired, existing_info = lock.acquire()
    if not acquired:
        LOG.warning("=" * 64)
        LOG.warning("Audio Transcriber is ALREADY running in another Terminal window!")
        if existing_info:
            LOG.warning("  Active session: %s", existing_info)
        LOG.warning("Switch to that open Terminal window to check status or stop it.")
        LOG.warning("=" * 64)
        return 2

    interrupted = False
    observer = None
    stop_event = threading.Event()

    try:
        LOG.info("Audio Transcriber v%s starting...", app_version())
        if not ensure_ffmpeg_on_path():
            return 1

        try:
            config = load_config()
            settings = build_job_settings(config, validate_device=True)
        except Exception as exc:
            LOG.error("%s", exc)
            LOG.error("Run '1 Setup' (or 'More Tools/Check Setup') after fixing the issue above.")
            return 1

        last_config_mtime = CONFIG_PATH.stat().st_mtime_ns if CONFIG_PATH.exists() else 0
        activity = ActivityTracker()
        manifest = ManifestStore()
        pending_store = PendingQueueStore()
        engine = TranscriberEngine(activity=activity, manifest=manifest)
        coordinator = WorkCoordinator(engine=engine, pending_store=pending_store, initial_settings=settings)
        coordinator.snapshot_existing_files(settings)

        event_handler = make_event_handler(coordinator)
        observer, watch_handle = create_started_observer(
            event_handler,
            settings.watch_folder,
            settings.recursive_watch,
        )
        coordinator.mark_observer_started()

        worker = threading.Thread(
            target=worker_loop,
            args=(engine, coordinator, activity, stop_event),
            name="transcriber-worker",
            daemon=True,
        )
        worker.start()

        LOG.info("Ready and watching for audio files!")
        LOG.info("  Audio input (watch) folder : %s", settings.watch_folder)
        LOG.info("  Transcript output folder   : %s", settings.transcript_folder)
        LOG.info("  Stop anytime               : Press Control+C")
        activity.update("watching", f"Watching {settings.watch_folder}", None)
        check_system_health()

        # 1. Resume any files that were queued/interrupted before a Ctrl+C restart.
        resumed = [p for p in pending_store.load() if p.exists() and p.is_file()]
        if resumed:
            LOG.info("Resuming %d unfinished audio file(s) from previous session...", len(resumed))
            for item in resumed:
                coordinator.enqueue(item, reason="resume")
        else:
            pending_store.save([])

        # 2. Optionally scan pre-existing files if enabled in Settings.ini.
        if settings.process_existing:
            pattern = "**/*" if settings.recursive_watch else "*"
            candidates = [
                p
                for p in sorted(settings.watch_folder.glob(pattern))
                if p.is_file() and engine.should_accept_file(p, settings, log_skip=False)
            ]
            if len(candidates) > settings.max_existing_on_start:
                LOG.warning(
                    "Found %d existing audio files in watch folder (safety limit is %d); "
                    "not auto-queueing old archive files. Drop new files in now, or raise max_existing_files_on_start.",
                    len(candidates),
                    settings.max_existing_on_start,
                )
            else:
                for candidate in candidates:
                    coordinator.enqueue(candidate, reason="startup")

        def handle_stop_signal(signum, frame) -> None:
            nonlocal interrupted
            interrupted = True
            LOG.info("Stop requested (Control+C) — shutting down cleanly...")
            stop_event.set()
            coordinator.leave_for_resume()
            if observer is not None:
                observer.stop()

        signal.signal(signal.SIGINT, handle_stop_signal)
        signal.signal(signal.SIGTERM, handle_stop_signal)

        while not stop_event.is_set():
            time.sleep(1.5)

            if not observer.is_alive():
                activity.update("error", "Folder watcher thread stopped unexpectedly", None)
                LOG.error("Folder watcher stopped unexpectedly.")
                return 1
            if not worker.is_alive():
                activity.update("error", "Transcription worker thread stopped unexpectedly", None)
                LOG.error("Transcription worker stopped unexpectedly.")
                return 1

            # Check if Settings.ini was modified (e.g. via `2 Choose Folders.command`) and hot-reload cleanly!
            try:
                current_mtime = CONFIG_PATH.stat().st_mtime_ns
            except OSError:
                current_mtime = last_config_mtime

            if current_mtime != last_config_mtime:
                last_config_mtime = current_mtime
                try:
                    new_cfg = load_config()
                    new_settings = build_job_settings(new_cfg, validate_device=False)
                    new_settings = dataclasses.replace(new_settings, device=settings.device)
                    watch_changed = (
                        new_settings.watch_folder.resolve() != settings.watch_folder.resolve()
                        or new_settings.recursive_watch != settings.recursive_watch
                    )
                    transcript_changed = new_settings.transcript_folder.resolve() != settings.transcript_folder.resolve()

                    coordinator.update_settings(new_settings)
                    if watch_changed:
                        coordinator.snapshot_existing_files(new_settings)
                        try:
                            observer.unschedule(watch_handle)
                            watch_handle = observer.schedule(
                                event_handler,
                                str(new_settings.watch_folder),
                                recursive=new_settings.recursive_watch,
                            )
                        except Exception:
                            try:
                                observer.stop()
                                observer.join(timeout=3)
                            except Exception:
                                pass
                            observer, watch_handle = create_started_observer(
                                event_handler,
                                new_settings.watch_folder,
                                new_settings.recursive_watch,
                            )
                        coordinator.mark_observer_started()
                    if watch_changed or transcript_changed:
                        LOG.info("Updated folder settings detected and loaded automatically!")
                        LOG.info("  Audio input (watch) folder : %s", new_settings.watch_folder)
                        LOG.info("  Transcript output folder   : %s", new_settings.transcript_folder)
                        activity.update("watching", f"Watching {new_settings.watch_folder}")
                    settings = new_settings
                except Exception as reload_exc:
                    LOG.warning("Settings.ini changed, but kept previous settings due to error: %s", reload_exc)

            activity.write()

        return 130 if interrupted else 0
    finally:
        stop_event.set()
        if observer is not None:
            try:
                observer.stop()
                observer.join(timeout=5)
            except Exception:
                pass
        # Clean up any leftover `.incomplete` partial output files if interrupted mid-write.
        worker_ref = locals().get("worker")
        if isinstance(worker_ref, threading.Thread) and worker_ref.is_alive():
            worker_ref.join(timeout=3)
        lock.release()
        LOG.info("Audio Transcriber stopped.")


# ---------------------------------------------------------------------------
# CLI Entrypoint
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Portable Newsroom Audio Transcriber")
    parser.add_argument("--check", action="store_true", help="Run setup & Hugging Face access check")
    parser.add_argument("--download-models", action="store_true", help="Verify and pre-download AI models")
    parser.add_argument(
        "--manage-autostart",
        choices=("status", "on", "off"),
        default=None,
        help="Check or toggle login auto-start",
    )
    parser.add_argument("--make-support-zip", action="store_true", help="Build a redacted support diagnostic zip")
    parser.add_argument("--is-running", action="store_true", help="Exit 0 if another instance is running, else 1")
    parser.add_argument(
        "--save-token-stdin",
        action="store_true",
        help="Save a Hugging Face token read from stdin into Keychain / secure storage",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.save_token_stdin:
        return save_token_from_stdin()
    if args.is_running:
        running, detail = is_another_instance_running()
        if running:
            print(detail or "running")
            return 0
        return 1
    if args.manage_autostart is not None:
        return manage_autostart(args.manage_autostart)
    if args.make_support_zip:
        return make_support_zip()
    if args.check or args.download_models:
        return run_preflight(download_models=args.download_models)
    return run_service()


if __name__ == "__main__":
    raise SystemExit(main())
