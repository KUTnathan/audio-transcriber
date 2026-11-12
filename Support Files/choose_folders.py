#!/usr/bin/env python3
"""Cross-platform folder chooser and atomic Settings.ini updater for Audio Transcriber."""

from __future__ import annotations

import argparse
import configparser
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

SUPPORT_DIR = Path(__file__).resolve().parent
APP_DIR = SUPPORT_DIR.parent
CONFIG_PATH = APP_DIR / "Settings.ini"
LOCK_PATH = SUPPORT_DIR / "logs" / "transcriber.lock"


def resolve_app_path(raw_value: str) -> Path:
    expanded = os.path.expandvars(raw_value.strip())
    path = Path(expanded).expanduser()
    if path.is_absolute():
        return path
    return APP_DIR / path


def load_current_folders(config_path: Path) -> tuple[Path, Path]:
    if not config_path.exists():
        raise FileNotFoundError(f"Missing Settings.ini at {config_path}")
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(config_path, encoding="utf-8")
    watch_raw = parser.get("Paths", "watch_folder", fallback="Audio to Transcribe")
    transcript_raw = parser.get("Paths", "transcript_folder", fallback="Finished Transcripts")
    return resolve_app_path(watch_raw), resolve_app_path(transcript_raw)


def is_watcher_running() -> bool:
    if not LOCK_PATH.exists():
        return False
    try:
        handle = open(LOCK_PATH, "a+", encoding="utf-8")
    except OSError:
        return False
    with handle:
        if sys.platform == "win32":
            import msvcrt

            try:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                return False
            except OSError:
                return True
        else:
            import fcntl

            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                return False
            except OSError:
                return True


def _dialog_start_dir(initial_dir: Path) -> Path:
    """Open Finder at the user's Home folder (showing Desktop/Documents/etc.) when still on defaults,
    or at the parent of a custom folder so neighbor folders are immediately visible.
    """
    try:
        if initial_dir.resolve().is_relative_to(APP_DIR.resolve()):
            return Path.home()
        if initial_dir.exists() and initial_dir.parent.exists():
            return initial_dir.parent
    except Exception:
        pass
    return Path.home() if Path.home().exists() else APP_DIR


def _parse_terminal_folder_input(raw: str) -> str:
    """Parse a folder path typed or dragged from Finder into Terminal (handling \\ spaces and quotes)."""
    text = raw.strip()
    if not text:
        return ""
    try:
        parts = shlex.split(text)
        if len(parts) == 1:
            return parts[0].strip()
    except ValueError:
        pass
    return text.strip("'\"")


def _prompt_terminal_folder_fallback(step_label: str, current_dir: Path) -> str:
    if not sys.stdin or not sys.stdin.isatty():
        return ""
    print(f"  Tip: You can also drag your {step_label} folder from Finder right into")
    print("       this Terminal window and press Enter (or just press Enter to keep current):")
    try:
        raw = input("  Folder path (or Enter to keep current): ")
    except EOFError:
        return ""
    return _parse_terminal_folder_input(raw)


def _choose_folder_macos(prompt: str, initial_dir: Path, step_label: str = "desired") -> str:
    start_dir = _dialog_start_dir(initial_dir)
    start_str = str(start_dir)
    applescript = """
on run argv
    set promptText to item 1 of argv
    set startPath to item 2 of argv
    set hasDefault to false
    try
        set defaultAlias to (POSIX file startPath) as alias
        set hasDefault to true
    end try

    -- Try 1: Top-level activate (needs zero TCC Automation permission)
    try
        activate
        if hasDefault then
            set chosenFolder to choose folder with prompt promptText default location defaultAlias
        else
            set chosenFolder to choose folder with prompt promptText
        end if
        return "OK:" & (POSIX path of chosenFolder)
    on error number -128
        return "CANCEL"
    on error
        -- Try 2: Terminal (the app already hosting this window)
        try
            tell application "Terminal"
                activate
                if hasDefault then
                    set chosenFolder to choose folder with prompt promptText default location defaultAlias
                else
                    set chosenFolder to choose folder with prompt promptText
                end if
            end tell
            return "OK:" & (POSIX path of chosenFolder)
        on error number -128
            return "CANCEL"
        on error
            -- Try 3: SystemUIServer fallback
            try
                tell application "SystemUIServer"
                    activate
                    set chosenFolder to choose folder with prompt promptText
                end tell
                return "OK:" & (POSIX path of chosenFolder)
            on error number -128
                return "CANCEL"
            on error errMsg number errNum
                return "ERR:" & errNum & ":" & errMsg
            end try
        end try
    end try
end run
"""
    cmd = ["/usr/bin/osascript"]
    for line in applescript.strip().splitlines():
        cmd.extend(["-e", line])
    cmd.extend(["--", prompt, start_str])

    result = subprocess.run(
        cmd,
        text=True,
        capture_output=True,
        check=False,
    )
    out = result.stdout.strip()
    if result.returncode == 0 and out.startswith("OK:"):
        chosen = out[3:].strip()
        if chosen and chosen != "/":
            chosen = chosen.rstrip("/")
        return chosen

    # Bring Terminal back to the front so the user can either press Enter or drag a folder in!
    subprocess.run(
        ["/usr/bin/osascript", "-e", 'tell application "Terminal" to activate'],
        capture_output=True,
        check=False,
    )
    if out != "CANCEL":
        print("  (Finder popup window could not open automatically on this Mac.)")
    return _prompt_terminal_folder_fallback(step_label, initial_dir)


def _choose_folder_windows(prompt: str, initial_dir: Path) -> str:
    start_str = str(initial_dir) if initial_dir.exists() else str(APP_DIR)
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        selected = filedialog.askdirectory(title=prompt, initialdir=start_str, mustexist=False)
        root.destroy()
        return selected.strip()
    except Exception:
        pass

    ps_script = (
        "Add-Type -AssemblyName System.Windows.Forms; "
        "$dlg = New-Object System.Windows.Forms.FolderBrowserDialog; "
        "$dlg.Description = $env:AT_PROMPT; "
        "$dlg.SelectedPath = $env:AT_START; "
        "$dlg.ShowNewFolderButton = $true; "
        "if ($dlg.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { "
        "[Console]::Out.WriteLine($dlg.SelectedPath) }"
    )
    env = os.environ.copy()
    env["AT_PROMPT"] = prompt
    env["AT_START"] = start_str
    result = subprocess.run(
        ["powershell", "-NoProfile", "-STA", "-Command", ps_script],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )
    if result.returncode == 0:
        return result.stdout.strip()
    return ""


def choose_folder_dialog(prompt: str, initial_dir: Path, step_label: str = "desired") -> str:
    if sys.platform == "darwin":
        return _choose_folder_macos(prompt, initial_dir, step_label=step_label)
    if sys.platform == "win32":
        return _choose_folder_windows(prompt, initial_dir)
    try:
        raw = input(f"{prompt} [Press Enter to keep '{initial_dir}']: ").strip()
        return _parse_terminal_folder_input(raw)
    except EOFError:
        return ""


def validate_watch_folder(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    if not os.access(folder, os.R_OK):
        raise PermissionError(f"Cannot read audio input folder: {folder}")


def validate_transcript_folder(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.NamedTemporaryFile(prefix=".write_check_", dir=folder, delete=True) as tmp:
            tmp.write(b"ok")
            tmp.flush()
    except OSError as exc:
        raise PermissionError(f"Cannot save files into transcript folder '{folder}': {exc}") from exc


def format_for_config(folder: Path) -> str:
    """Keep default internal folders portable/relative; store external folders as absolute paths."""
    try:
        rel = folder.resolve().relative_to(APP_DIR.resolve())
        if str(rel) in ("Audio to Transcribe", "Finished Transcripts"):
            return str(rel)
    except ValueError:
        pass
    return str(folder.resolve())


def update_settings_ini_atomic(config_path: Path, updates: dict[str, str]) -> None:
    if not updates:
        return

    lines = config_path.read_text(encoding="utf-8").splitlines()
    seen: set[str] = set()
    new_lines: list[str] = []
    in_paths_section = False
    inserted_missing = False

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            if in_paths_section and not inserted_missing:
                for key, value in updates.items():
                    if key not in seen:
                        new_lines.append(f"{key} = {value}")
                        seen.add(key)
                inserted_missing = True
            in_paths_section = stripped.lower() == "[paths]"
            new_lines.append(line)
            continue

        replaced = False
        if in_paths_section and not stripped.startswith(("#", ";")):
            for key, value in updates.items():
                if stripped.startswith(f"{key} =") or stripped.startswith(f"{key}="):
                    new_lines.append(f"{key} = {value}")
                    seen.add(key)
                    replaced = True
                    break
        if not replaced:
            new_lines.append(line)

    if not inserted_missing:
        if not any(line.strip().lower() == "[paths]" for line in new_lines):
            new_lines.insert(0, "[Paths]")
        for key, value in updates.items():
            if key not in seen:
                new_lines.append(f"{key} = {value}")

    candidate_text = "\n".join(new_lines).rstrip() + "\n"

    # Verify the updated file parses cleanly before replacing Settings.ini.
    test_parser = configparser.ConfigParser(interpolation=None)
    test_parser.read_string(candidate_text)
    for key, expected in updates.items():
        actual = test_parser.get("Paths", key, fallback=None)
        if actual != expected:
            raise RuntimeError(f"Settings validation failed for {key}")

    fd, temp_name = tempfile.mkstemp(prefix=".Settings.ini.", dir=str(config_path.parent))
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(candidate_text)
        os.replace(temp_path, config_path)
    finally:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Choose Watch and Transcript folders for Audio Transcriber.")
    parser.add_argument("--watch", default=None, help="Set watch folder directly (skips GUI dialog)")
    parser.add_argument("--transcript", default=None, help="Set transcript folder directly (skips GUI dialog)")
    parser.add_argument("--config", default=str(CONFIG_PATH), help="Path to Settings.ini")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config_path = Path(args.config).resolve()

    try:
        current_watch, current_transcript = load_current_folders(config_path)
    except Exception as exc:
        print(f"ERROR: {exc}")
        return 1

    interactive = args.watch is None and args.transcript is None
    if interactive:
        print("=== Choose Audio & Transcript Folders ===")
        print()
        print("Current folders:")
        print(f"  1. Audio input (watch) folder : {current_watch}")
        print(f"  2. Transcript output folder   : {current_transcript}")
        print()
        print("For each step below, a Finder window will pop up so you can pick a folder.")
        print("Tip: Prefer drag-and-drop? Close/Cancel the popup, drag any folder from Finder")
        print("     into this Terminal window, and press Enter! (Or press Enter to keep as-is.)")
        print()

        print("Step 1 of 2: Choose the AUDIO INPUT folder (where you drop audio files)...")
        chosen_watch = choose_folder_dialog(
            "Step 1 of 2: Choose the folder where you will drop audio files",
            current_watch,
            step_label="AUDIO INPUT (watch)",
        )
        if chosen_watch:
            print(f"  -> Selected: {chosen_watch}")
        else:
            print(f"  -> Kept current: {current_watch}")
        print()

        print("Step 2 of 2: Choose the TRANSCRIPT OUTPUT folder (where transcripts are saved)...")
        chosen_transcript = choose_folder_dialog(
            "Step 2 of 2: Choose the folder where finished transcripts should be saved",
            current_transcript,
            step_label="TRANSCRIPT OUTPUT",
        )
        if chosen_transcript:
            print(f"  -> Selected: {chosen_transcript}")
        else:
            print(f"  -> Kept current: {current_transcript}")
        print()
    else:
        chosen_watch = args.watch or ""
        chosen_transcript = args.transcript or ""

    updates: dict[str, str] = {}
    final_watch = resolve_app_path(chosen_watch) if chosen_watch else current_watch
    final_transcript = resolve_app_path(chosen_transcript) if chosen_transcript else current_transcript

    if final_watch.resolve() == final_transcript.resolve():
        print("Note: Audio input and transcript output are set to the same folder.")
        print("      Transcripts are .txt/.srt files so they will not re-trigger audio watching,")
        print("      though separate folders are usually tidier.")
        print()

    try:
        if chosen_watch:
            validate_watch_folder(final_watch)
            updates["watch_folder"] = format_for_config(final_watch)
        if chosen_transcript:
            validate_transcript_folder(final_transcript)
            updates["transcript_folder"] = format_for_config(final_transcript)

        if updates:
            update_settings_ini_atomic(config_path, updates)
            print("Saved folder settings:")
        else:
            print("No changes made — kept your existing folder settings:")

        print(f"  Audio input (watch) folder : {final_watch}")
        print(f"  Transcript output folder   : {final_transcript}")
        print()

        if is_watcher_running():
            print("Audio Transcriber is currently open — it will switch to your updated folders")
            print("automatically within a few seconds (after any active file finishes).")
        else:
            print("Next: Double-click '3 Start Transcriber.command' when you're ready to start.")
        return 0
    except Exception as exc:
        print(f"ERROR: Could not save folder settings: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
