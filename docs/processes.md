# Logical Processes

This document describes the step-by-step flow of every major operation in the application.

---

## 1. Application Startup

**File:** `flaskapp.py`

1. Flask initialises and reads `version.txt` to load the version string.
2. Checks for `/config/config.yaml`. If present, loads it into the `config` dict (libraries + secrets). If absent, redirects to `/setup`.
3. Checks for `/config/job.yaml`. If present, reads `job_directory`, `job_progress`, and `file_progress` into module-level variables (used for template rendering on first load).
4. Initialises APScheduler `BackgroundScheduler` with a cron job (`scheduled_scan_job`) running daily at 04:00.
5. Flask begins listening on `0.0.0.0:5000` with `use_reloader=False` (prevents duplicate scheduler instances).

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
      - Input: `file_path_old` (software decode — no hardware input decoder)
      - Pixel format: `p010le` (10-bit)
      - Metadata: `title` set to the original filename
      - Video streams: `0:v:0` mapped, encoded with `hevc_qsv`
      - x265 params: `repeat-headers=1:profile=main10:level=5.1`
      - Audio streams: `0:a` mapped, copied without re-encoding
      - Subtitle streams: `0:s?` mapped, copied
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

---

## 9. Scheduled Media Scan (modules/scanner.py)

**Trigger:** APScheduler cron job at 04:00 daily, or manual `POST /scan_now`

### 9a. Scan Type Selection (`run_scan()`)

1. Checks if `/config/media.db` exists.
2. If absent, or if the `media_files` table is empty → runs a **full scan**.
3. Otherwise → runs an **incremental scan**.

### 9b. Full Scan (`full_scan()`)

1. Opens SQLite connection to `/config/media.db` (creates schema if needed).
2. Deletes all existing records from `media_files` (clean slate).
3. For each configured library (`films`, `shows`):
   - Walks the directory tree via `os.walk`.
   - For each `.mkv` file:
     - Gets file size and mtime from `os.stat`.
     - Derives `title` and `season` from the relative path (see schema notes in architecture.md).
     - Reads video codec via `pymediainfo`, normalises to `x264` / `x265` / raw format string.
     - Inserts record into `media_files`.
   - Commits in batches of 100 for efficiency.
4. Updates `scan_state.last_full_scan` with the current UTC timestamp.

### 9c. Incremental Scan (`incremental_scan()`)

1. Builds an in-memory dict of all `.mkv` files currently on disk (filepath → metadata).
2. Loads all existing DB records (filepath → mtime).
3. **Deletions:** any filepath in DB but not on disk → DELETE from `media_files`.
4. **Additions/Updates:** any filepath on disk where mtime differs from DB (or is new) → reads codec via pymediainfo and INSERT OR REPLACE.
5. Updates `scan_state.last_incremental_scan`.

### 9d. Scheduled Job Wrapper (`scheduled_scan_job()`)

1. Calls `load_config()` to refresh library paths.
2. Calls `run_scan(libraries)`.
3. Logs success or failure.

---

## 10. Recommendations (GET /recommendations)

**File:** `flaskapp.py → recommendations()`

1. Calls `get_recommendations(limit=50)` from `modules/scanner.py`.
2. Calls `get_scan_status()` for live scan progress (in-memory, thread-safe).
3. Calls `get_scan_history(limit=10)` for recent scan records from the `scan_history` table.
4. The recommendations function queries `/config/media.db`:
   - **Films:** `SELECT title, filename, size_bytes, filepath FROM media_files WHERE category='films' AND codec='x264' ORDER BY size_bytes DESC LIMIT 50`
   - **Shows:** `SELECT title, season, COUNT(*) as episode_count, SUM(size_bytes) as total_bytes FROM media_files WHERE category='shows' AND codec='x264' GROUP BY title, season ORDER BY total_bytes DESC LIMIT 50`
   - **Stats:** aggregate counts and sizes for x264 vs x265, plus last scan timestamps.
5. Renders `templates/recommendations.html` with recommendations data, scan status, and scan history.
6. If a scan is currently running, the template includes `<meta http-equiv="refresh" content="3">` for auto-polling.

---

## 11. Manual Scan Trigger (POST /scan_now)

**File:** `flaskapp.py → scan_now()`

1. Checks if a scan is already running via `get_scan_status()`. If so, redirects back without starting a new one.
2. Calls `load_config()` to get current library paths.
3. Spawns `run_scan(libraries)` in a background `threading.Thread` (daemon=True) so the HTTP response returns immediately.
4. Redirects to `GET /recommendations` — the page will show the in-progress status panel and auto-refresh.

---

## 12. Transcode from Recommendations (POST /run_from_recommendations)

**File:** `flaskapp.py → run_from_recommendations()`

1. Checks if a transcode job is already running via `transcode_check()`. If so, redirects back to `/recommendations`.
2. Reads the `folder` from the POST body (set by the hidden input in each recommendation row's form).
3. Uses default transcode settings: include `.mkv`, quality `23`, delete `Yes`.
4. Fetches Telegram credentials from config.
5. Calls `store_job(folder)` and spawns `x265transcoder.py` as a background process.
6. Redirects to `GET /` where the progress UI is displayed.

---

## 13. Scan Status Polling (GET /scan_status)

**File:** `flaskapp.py → scan_status_endpoint()`

Returns a JSON object with the current scan state:

```json
{
    "running": true,
    "scan_type": "full",
    "started_at": "2026-07-20T04:00:00+00:00",
    "current_file": "movie.mkv",
    "files_processed": 42,
    "files_total": 200,
    "phase": "scanning",
    "message": "Scanning films library..."
}
```

Used by the recommendations page meta-refresh (or optionally by JS fetch for finer-grained polling).

---

## 14. Transcode History Recording (modules/history.py)

**Trigger:** Called by `x265transcoder.py` during job execution.

### 14a. Job Start

1. `start_job(directory, quality, delete_originals)` is called at the beginning of `x265transcoder.py`.
2. Inserts a row into `transcode_jobs` with status `'running'`.
3. Returns the `job_id` used to associate per-file records.

### 14b. Per-File Recording

After each file in the transcode loop, `record_file()` is called with one of three statuses:

- **`"success"`** — file transcoded and validated. Records original/new sizes, quality, duration.
- **`"failed"`** — file transcoded but failed validation (size, duration, or frame count mismatch). Records sizes, duration, and failure reason.
- **`"skipped"`** — file is already x265. Records original size only.

### 14c. Job Completion

1. `complete_job()` is called after the transcode loop finishes.
2. Updates the `transcode_jobs` row with: completed timestamp, file counts, total sizes, space saved, and final status (`"success"` or `"completed_with_failures"`).

---

## 15. History UI (GET /history, GET /history/<job_id>)

**File:** `flaskapp.py → history()`, `history_detail()`

### GET /history

1. Calls `get_job_history(limit=20)` for recent jobs.
2. Calls `get_lifetime_stats()` for all-time aggregates (total jobs, files transcoded, space saved, average compression %).
3. Renders `templates/history.html` with job list and stats panel.

### GET /history/<job_id>

1. Calls `get_job_history()` to get the job list (for context).
2. Calls `get_job_files(job_id)` for per-file detail of the selected job.
3. Renders `templates/history.html` with the detail panel expanded, showing per-file results (filename, status, original/new size, space saved, duration).

---

## 16. Restart Job with Cleanup (POST /restart_job)

**File:** `flaskapp.py → restart_job()`, `cleanup_interrupted_transcodes()`

Handles the case where a transcode job was interrupted (container killed, crash, etc.) and partially processed files remain on disk.

### 16a. Cleanup Phase (`cleanup_interrupted_transcodes(directory)`)

1. Walks the target directory tree looking for files ending in `_old`.
2. For each `*_old` file found:
   - Determines the original filename by stripping the `_old` suffix.
   - Determines the expected output filename (if original contained "264", the output would have "265" substituted; otherwise output = original name).
   - Deletes the partial/incomplete output file if it exists on disk.
   - Renames the `_old` file back to its original name.
3. Returns the count of files restored.

### 16b. Job Launch

1. Checks if a transcode job is already running. If so, redirects back.
2. Reads form fields: `folder`, `quality`, `delete`.
3. Calls `cleanup_interrupted_transcodes(folder)` to restore any interrupted files.
4. Fetches Telegram credentials and calls `store_job()`.
5. Spawns `x265transcoder.py` and resets progress counters.
6. Redirects to `GET /` to display progress.

### File State Diagram

```
Interrupted state:
  movie.mkv_old     (original x264 — intact)
  movie.mkv         (partial x265 — incomplete)

After cleanup:
  movie.mkv         (original x264 — restored from _old)

Transcoder runs:
  Detects x264 codec → processes normally
```
