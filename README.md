# Audio Transcriber — Step-by-Step Guide

This tool watches an audio folder on your Mac, transcribes recordings automatically, labels different speakers (`SPEAKER_00`, `SPEAKER_01`, etc.), and adds timestamps every 5 sentences so you can jump straight to quotes.

* **Local Transcription:** Your audio files and transcripts stay on your computer. They are not uploaded to OpenAI or a cloud transcription service. Setup, model downloads, token checks, and updates do use the internet.
* **No Admin Password & No Security Popups:** Installing with the one-line Terminal command below sets up a private copy of Python and all AI models inside `~/audio-transcriber` without needing an administrator password or triggering macOS "unverified developer" blocks.
* **Supported Audio/Video Files:** `.m4a`, `.mp3`, `.wav`, `.mp4`, `.mov`, `.flac`, `.ogg`, `.opus`, `.aac`, `.wma`, `.webm`

---

## Before You Start (30-Second Checklist)

1. **Computer:** An Apple Silicon Mac (M1, M2, M3, M4 or newer) running macOS 13 Ventura or newer.
2. **Free Space & Wi-Fi:** Have **at least 6 GB of free disk space** and an internet connection for setup. Once the models are downloaded, ordinary transcription does not need the internet.

---

## Part 1: One-Time Setup (About 10–15 Minutes)

Do these two steps once:

### Step 1: Get Your Free Hugging Face Token (For Speaker Labels)
The open-source AI model that separates speakers (`pyannote`) is free for research and journalism, and asks each user to accept its terms once:

1. Create a free account at **https://huggingface.co/join** (and confirm your email).
2. **Important — Accept BOTH model pages:** While logged into Hugging Face, open **both** links below, fill in the short form if asked, and click **Agree / Submit**:
   * **https://huggingface.co/pyannote/segmentation-3.0**
   * **https://huggingface.co/pyannote/speaker-diarization-3.1**
3. Create a read-only access token at **https://huggingface.co/settings/tokens**:
   * Click **Create new token**.
   * Select **Read** at the top for the token type (skip "Fine-grained"—plain **Read** is all you need and has zero extra checkboxes!).
   * Name it `audio-transcriber`, click **Create token**, and **copy** the token (it starts with `hf_`).

### Step 2: Run the One-Line Installer in Terminal
1. On your Mac, press **`Command + Space`**, type **`Terminal`**, and press **`Enter`**. This opens the Terminal app that comes pre-installed with your Mac.
2. Copy and paste this single line into the Terminal window and press **`Enter`**:

```bash
curl -fsSL https://raw.githubusercontent.com/KUTnathan/audio-transcriber/main/install.sh | bash
```

3. That command downloads Audio Transcriber into your Mac's Home folder (`~/audio-transcriber`), pops the folder open in **Finder** so you can see it (tip: drag `audio-transcriber` to your Finder sidebar or Dock for easy access!), and starts the setup automatically.
4. When Terminal asks for your Hugging Face token, **paste your `hf_...` token** (characters stay hidden for privacy) and press **`Enter`**.
   *(The app automatically stores your token in your Mac's encrypted **Keychain** so it isn't sitting in plain text in the folder!)*
5. Wait while it downloads the AI models. It may sit quietly for several minutes on large downloads—just let it finish until you see:
   `ALL SET! Audio Transcriber is completely ready.`

---

## What Is Inside Your `audio-transcriber` Folder

Once installed, everything works by normal double-clicking in Finder:

* **`0 Read Me First.txt`** — This guide.
* **`1 Setup.command`** — Re-checks setup or downloads models if you skipped pasting your token during install.
* **`2 Choose Folders.command`** — Pick (or change) which folder to watch for audio and where to save finished transcripts.
* **`3 Start Transcriber.command`** — Opens the watcher in Terminal. Leave that window open while working.
* **`Auto Start at Login (Optional).command`** — Turn automatic startup ON or OFF so the Terminal watcher opens whenever you log in.
* **`My Hugging Face Token.txt`** — Shows `SAVED_IN_KEYCHAIN` once your token is safely stored in macOS Keychain (or lets you paste a new `hf_...` token if you ever replace it).
* **`Audio to Transcribe/` & `Finished Transcripts/`** — Default input and output folders if you don't pick custom ones.
* **`More Tools/`** — Helpful extras (`Check Setup`, `Download Models`, `Repair Setup`, and `Make Support Zip`) if something ever acts up.

---

## Part 2: Choosing or Changing Your Folders Anytime

By default, you drop audio into **`Audio to Transcribe`** and transcripts appear in **`Finished Transcripts`**.

If you want to use your own folders (for example, watching a `2026 TAPE` folder and saving into a `TRANSCRIPTS` folder):

1. Double-click **`2 Choose Folders.command`**.
2. **First window:** Select the folder where you drop audio files, then click **Choose**.
3. **Second window:** Select the folder where finished transcripts should be saved, then click **Choose**.
   *(Tip: If you only want to change one of the two folders, just click **Cancel** on the window you want to keep as-is!)*

**Good to know:** You can run **`2 Choose Folders.command`** whenever you want—even while the transcriber is already running! An open transcriber window will detect the change within a few seconds and switch to your new folders automatically.

---

## Part 3: Everyday Use — Starting, Stopping & Restarting

### Starting the Watcher
1. Double-click **`3 Start Transcriber.command`** (unless you turned on Auto Start at Login, in which case it opens for you automatically!).
2. A **Terminal** window opens and says:
   `Ready and watching for audio files!`
3. **Leave that Terminal window open.** Terminal is the app's visible status window. You can minimize it to the Dock (`Command + M`). If the Mac sleeps or you close its lid, transcription pauses and continues after the Mac wakes. Do not close the Terminal window while you want the app watching.
4. Drop an audio file into your watch folder. You'll see `[1/4]` through `[4/4]` progress steps in Terminal. When done, you'll hear a completion chime and find `YourFileName_transcript.txt` in your transcript folder!
   **Important:** AI transcripts and speaker labels can be wrong. Listen to the recording and verify every quote, name, number, and speaker before publication.

### How to Stop and Restart (`Control + C`, `Up-Arrow`, `Enter`)
Need to pause transcription or restart the watcher?
1. **To Stop:** Click inside the Terminal window and hold **`Control`** and press **`C`** (`Control + C`).
   * The watcher stops cleanly and stays right in that same window.
2. **To Restart:** Press the **`Up-Arrow`** key on your keyboard, then press **`Enter`** (or simply type `restart` and press `Enter`).
   * Waiting files remain in the queue. If a file was actively transcribing when you stopped, that file starts again from the beginning.

If you change folders while files are already waiting, those waiting files finish using the folders that were selected when they entered the queue. Newly added files use the new folders.

---

## Part 4: Opening Automatically When You Log In (Optional)

If you want Audio Transcriber to pop open automatically in a Terminal window every time you log into or restart your Mac:

1. Double-click **`Auto Start at Login (Optional).command`**.
2. Press **`Enter`** to turn Auto-Start **ON**.
   *(No admin password needed!)*
3. Want to turn it off later? Just double-click **`Auto Start at Login (Optional).command`** again and press **`Enter`** to turn it **OFF**.

*(Note: If you ever move the `audio-transcriber` folder to a different place on your Mac after turning Auto-Start on, run `Auto Start at Login (Optional).command` once in the new location so macOS updates the shortcut.)*

---

## Part 5: Troubleshooting Common Questions

* **"It says Hugging Face check failed (403 — terms not accepted yet)"**
  Make sure you opened **both** `https://huggingface.co/pyannote/segmentation-3.0` **and** `https://huggingface.co/pyannote/speaker-diarization-3.1` while logged into the **exact same** Hugging Face account that created your token, and clicked **Agree** on both. Then run `More Tools/Check Setup.command`.

* **"It says Hugging Face check failed (401 — token rejected)"**
  Open `My Hugging Face Token.txt` in TextEdit, replace `SAVED_IN_KEYCHAIN` (or `YOUR_TOKEN_HERE`) with your fresh `hf_...` token (no extra spaces or quotes), save, and run `More Tools/Check Setup.command`—it will automatically update your Keychain.

* **"An audio file is in my watch folder, and nothing happened"**
  1. Check that the Terminal window from `3 Start Transcriber.command` is open and says `Ready and watching`.
  2. If the audio file was already in that folder *before* you opened `3 Start Transcriber.command`, move it temporarily to another folder, then move it back into the watch folder.
  3. Check if a `YourFile_transcript.txt` **already exists** in your transcript folder—by default, the app skips files that already have a transcript so it doesn't accidentally overwrite your work. Delete or move the old transcript first if you want to redo it.

* **"A file created `YourFile_FAILED.txt` instead of a transcript"**
  Open `YourFile_FAILED.txt`. It explains what happened and what to try next.

* **"Transcription feels much slower than usual"**
  On Apple Silicon Macs, a 30-minute recording typically takes 8–25 minutes depending on your Mac. If it suddenly takes 3x+ longer than the audio length, your Mac is likely low on free memory from heavy browser tabs or apps—closing extra tabs or restarting your Mac restores normal speed.

* **"Something in the Python setup broke"**
  Open **`More Tools`** and double-click **`Repair Setup.command`**, type `REPAIR`, and press `Enter`. It rebuilds the Python environment without touching your audio, transcripts, token, or downloaded AI models.

* **"I still need help from Nathan"**
  Open **`More Tools`** and double-click **`Make Support Zip.command`**. It creates `Audio Transcriber Support Info.zip` in the main folder. The zip excludes audio and transcripts and redacts the Hugging Face token, folder paths, and source-derived filenames. Send that zip to Nathan in Slack or email.

---

*(Optional power-user settings like subtitle `.srt` output—`output_format = srt` or `both`—and model size can be adjusted in `Settings.ini` if you ever want them; most people never need to touch `Settings.ini`.)*
