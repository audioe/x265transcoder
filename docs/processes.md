# Logical Processes

This document describes the step-by-step flow of every major operation in the application.

---

## 1. Application Startup

**File:** `flaskapp.py`

1. Flask initialises and reads `version.txt` to load the version string.
2. Checks for `/config/config.yaml`. If present, loads it into the `config` dict (libraries + secrets). If absent, sets `config_present = "False"` — note: this does not prevent startup but all library-dependent routes will fail.
3. Checks for `/config/job.yaml`. If present, reads `job_directory`, `job_progress`, and `file_progress` into module-level variables (used for template rendering on first load).
4. Flask begins listening on `0.0.0.0:5000`.

---

## 2. Home Page Load (GET /)

**File:** `flaskapp.py → index()`

1. Calls `transcode_check('x265transcoder.py')` which runs `ps aux` and scans output for the keyword.
2. **If a job is running:**
   - Reads `/config/job.yaml` to get `job_directory`, `job_progress`, `file_progress`, `current_file`, `current_file_number`, `total_files`.
   - Renders `index.html` in the "in progress" state, which injects a `<meta http-equiv="refresh" content="5">` tag to auto-reload every 5 seconds.
3. **If no job is running:**
   - Renders `index.html` with the library-type selector form.

---

## 3. Load Directories (POST /load_directories)

**File:** `flaskapp.py → load_directories()`

Triggered when the user submits the library-type radio form.

1. Reads `parent_dir` from the POST body (the value is the configured library path).
2. Checks whether the selected path matches `config['libraries']['films']`.
3. **Films path selected:**
   - Lists all immediate subdirectories of the films root.
   - Calculates each directory's size (recursive walk via `get_directory_size()`).
   - Sorts results alphabetically (case-insensitive).
   - Renders `index.html` with `subdirectories` and `films='films'` — jumps straight to the transcode options form.
4. **Shows path selected:**
   - Lists all immediate subdirectories of the shows root with sizes.
   - Renders `index.html` with `directories` — presents the show-selector dropdown.

`get_directory_size()` walks the entire subtree via `os.walk`, summing `os.path.getsize()` for every file, and returns the total in GB (rounded to 2 decimal places).

---

## 4. Load Subdirectories (POST /load_subdirectories)

**File:** `flaskapp.py → load_subdirectories()`

Triggered when the user selects a show from the directory list.

1. Reads `parent_dir` (shows root) and `folder` (selected show directory) from POST body.
2. **If `folder` is provided:**
   - Lists all subdirectories of the selected show (i.e. seasons) with sizes.
   - Renders `index.html` with `subdirectories`, `current_dir`, and `shows='shows'` — presents the season + transcode options form.
3. **If `folder` is empty:**
   - Falls back to re-listing the parent directory (defensive path, same as step 3 shows flow).

---

## 5. Job Submission (POST /run)

**File:** `flaskapp.py → run()`

1. Reads form fields: `folder`, `include`, `quality`, `delete` (defaults to `'No'` if the checkbox is absent).
2. Fetches Telegram credentials by calling `get_secret("TELEGRAM_TOKEN")` and `get_secret("TELEGRAM_CHATID")` — reads directly from the in-memory `config` dict.
3. Calls `store_job(folder)` which writes `job_directory` to `/config/job.yaml` (creates the file if absent).
4. Calls `subprocess.Popen` to launch `x265transcoder.py` as a detached background process, passing: `folder`, `include`, `quality`, `delete`, `telegram_token`, `telegram_chatid`, `version` as positional CLI arguments.
5. Immediately calls `update_progress_yaml("job_progress", 0)` and `update_progress_yaml("file_progress", 0)` to reset progress indicators.
6. Returns an HTTP 302 redirect to `GET /`, which will detect the running process and render the progress UI.

---

## 6. Transcode Engine

**File:** `x265transcoder.py`

### 6a. Initialisation

1. Validates CLI arguments (requires at least `folder` and `include`).
2. Assigns all positional arguments to named variables.
3. Initialises counters: `OldFolderSizeBytes`, `NewFolderSizeBytes`, `Successful`, `SuccessfulCount`, `Failed`, `FailedCount`, `SkippedCount`.
4. Configures the Python logger to write to `/logs/transcode_<DD-MM-YY_HH-MM-SS>.log`.

### 6b. File Discovery

1. Calls `get_files(mediafolder, include)` which walks the directory tree via `os.walk`.
2. Filters files where the filename ends with the `include` pattern.
3. Returns a flat list of absolute file paths.

### 6c. Per-File Processing Loop

For each file in the list:

1. Updates `/config/job.yaml` with `current_file_number` (1-based).
2. Reads codec via `pymediainfo`: parses media info and returns `track.format` for the Video track.
3. **HEVC file (`"High Efficiency Video Coding"` or `"HEVC"`):** increments `SkippedCount`, adds original file size to `NewFolderSizeBytes`, continues to next file.
4. **H.264 file (`"Advanced Video Codec"` or `"AVC"`):**

   a. Records original file size (bytes) and accumulates into `OldFolderSizeBytes`.

   b. Gets frame count and duration (milliseconds) via `pymediainfo`.

   c. Renames source file: `shutil.move(file_path, file_path + "_old")`.

   d. Determines output filename: if `"264"` appears in the filename it is replaced with `"265"`; otherwise the original path is used as output.

   e. Builds the `ffmpeg` command array targeting `/usr/lib/jellyfin-ffmpeg/ffmpeg` with:
      - Input decoder: `h264_qsv` (hardware H.264 decode)
      - Input: `file_path_old`
      - Pixel format: `p010le` (10-bit)
      - Metadata: `title` set to the original filename
      - Video streams: `0:0` mapped, encoded with `hevc_qsv`
      - x265 params: `repeat-headers=1:profile=main10:level=5.1`
      - Audio streams: `0:a` mapped, copied without re-encoding
      - Rate control: `CQP` with `global_quality` set to the user-supplied value
      - Preset: `fast`
      - Stats period: 15 seconds

   f. Wraps the command in `FfmpegProgress` and iterates progress events:
      - Writes `file_progress` percentage to `/config/job.yaml`.
      - Calculates and writes `job_progress` (weighted by position in total file list).

### 6d. Post-Transcode Validation

After FFmpeg completes for each file:

1. **Size check:** new file size must be less than original. If not, marks job as failed.
2. **Duration check:** new file duration must be within ±50 ms of original. If not, marks job as failed.
3. **Frame count check:** new frame count must be within ±0.11% of original. If outside tolerance, logs a warning and marks job as failed.

If all checks pass and `delete == "Yes"`, calls `os.remove(file_path + "_old")`.

### 6e. Job Summary

After all files are processed:

1. Calculates total old/new folder sizes and percentage difference.
2. Sends a Telegram message with success/failure counts and space saving stats.
3. If any files failed, includes the failure list in the notification.

---

## 7. Media Inventory Scan (collector.py)

This module is not currently called from any Flask route — it is a standalone utility.

1. Call `scan_directory(directory)` with a root path.
2. Walks all subdirectories via `os.walk`.
3. For each `.mkv` file:
   - Determines codec via `pymediainfo`.
   - Gets file size in GB.
   - Maps codec to `"x264"` or `"x265"`.
4. Calls `store_db_items(category, codec, dir, filename, filesize)`:
   - Loads `/config/db.yaml` (creates empty dict if absent).
   - Writes entry at `data[category][codec][directory][filename] = filesize`.
   - If the same directory exists under the alternate codec key, removes it (keeps inventory consistent after transcoding).
   - Writes the updated dict back to `/config/db.yaml`.

---

## 8. Secret Retrieval (GET /get_secret/\<name\>)

**File:** `flaskapp.py → get_secret()`

A utility HTTP endpoint for fetching config secrets by key name. Returns the plain-text value from `config['secrets'][secret_name]`, or a 404 JSON error if the key is not found.

Note: this endpoint is unauthenticated and should not be exposed on a public network.
