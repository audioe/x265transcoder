# Architecture

## Component Overview

```
┌─────────────────────────────────────────────────────────────────┐
│  Docker Container  (audioe/x265transcoder)                      │
│                                                                 │
│  ┌──────────────┐   HTTP    ┌──────────────────────────────┐   │
│  │   Browser    │ ◄───────► │  flaskapp.py  (Flask :5000)  │   │
│  └──────────────┘           └──────────────┬───────────────┘   │
│                                            │ subprocess.Popen   │
│                              ┌─────────────▼──────────────┐    │
│                              │  x265transcoder.py          │    │
│                              │  (background process)       │    │
│                              └──────┬──────────────┬───────┘    │
│                                     │              │             │
│                          jellyfin-  │    ┌─────────▼──────┐    │
│                          ffmpeg     │    │  /config/       │    │
│                          (hevc_qsv) │    │  job.yaml       │    │
│                                     │    └─────────────────┘    │
│                              ┌──────▼──────────────────────┐   │
│                              │  /logs/transcode_<ts>.log   │   │
│                              └─────────────────────────────┘   │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  modules/scanner.py  (scheduled media inventory)        │   │
│  │  APScheduler (nightly 04:00) + manual trigger           │   │
│  │  writes ──► /config/media.db  (SQLite)                  │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  modules/collector.py  (legacy scan utility — dormant)  │   │
│  │  writes ──► /config/db.yaml                             │   │
│  └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
         │                                         │
   ┌─────▼──────┐                         ┌────────▼───────┐
   │  /shows    │                         │  /films        │
   │  (volume)  │                         │  (volume)      │
   └────────────┘                         └────────────────┘

External:
  Telegram Bot API  ◄── notifications from x265transcoder.py
```

---

## Components

### flaskapp.py

The web front-end and job dispatcher. Responsibilities:

- Serves the single-page UI via Jinja2 templates (`templates/index.html`).
- Reads `/config/config.yaml` at startup for library paths and secrets.
- Reads `/config/job.yaml` at startup and on every `GET /` request to surface live progress.
- Provides four HTTP routes plus `/recommendations`, `/scan_now`, and `/scan_status` (see [Logical Processes](processes.md)).
- Runs APScheduler with a nightly job (04:00) that triggers `modules/scanner.py` to scan both libraries.
- Runs manual scans in a background thread to avoid blocking HTTP responses; exposes live scan progress via `GET /scan_status` (JSON).
- Detects whether a transcode job is already running by scanning the process list for `x265transcoder.py` via `ps aux`.
- Spawns `x265transcoder.py` as a detached subprocess via `subprocess.Popen`, passing all job parameters as positional CLI arguments.
- Writes initial `job_progress` and `file_progress` values to `/config/job.yaml` immediately after spawning the child process.

### x265transcoder.py

The transcode engine. Runs as a background process independent of the Flask app. Responsibilities:

- Walks the target directory for files matching the `include` pattern.
- Uses `pymediainfo` to identify video codec, frame count, and duration of each file.
- Skips files already encoded as HEVC (`x265` / `High Efficiency Video Coding`).
- Renames source files to `<name>_old` before transcoding to preserve originals.
- Invokes `jellyfin-ffmpeg` with Intel QSV hardware acceleration (`hevc_qsv`) via the `ffmpeg-progress-yield` library to stream per-frame progress.
- Writes real-time progress (file %, job %, current file name, counters) to `/config/job.yaml` so the Flask UI can poll it.
- Performs post-transcode validation: file size must be smaller; duration must match within ±50 ms; frame count must match within ±0.11%.
- Optionally deletes the `_old` source file after successful validation.
- Sends a Telegram notification on job completion (success or failure summary).
- Writes a structured log file to `/logs/transcode_<datetime>.log`.

### modules/scanner.py

The scheduled media inventory scanner. Replaces `collector.py` as the active library scanner. Responsibilities:

- Performs a full scan on first run (when `/config/media.db` is absent or empty), walking both libraries and recording every `.mkv` file's codec, size, mtime, title, and season.
- Performs incremental scans on subsequent runs — only processes files with changed mtime and removes records for deleted files.
- Stores data in SQLite (`/config/media.db`) with WAL mode for safe concurrent reads from Flask.
- Tracks live scan progress in thread-safe in-memory state (`get_scan_status()`), including current file, files processed, phase, and status message.
- Records scan history in the `scan_history` table (type, duration, files processed/added/removed, success/failure).
- Provides `get_recommendations()` which queries the DB for the largest x264 films (by individual file size) and largest x264 show seasons (by aggregate season size).
- Provides `get_scan_history()` for the most recent scan records.
- Triggered nightly at 04:00 via APScheduler, or manually via `POST /scan_now` (runs in background thread).

### modules/history.py

Transcode job history recorder. Responsibilities:

- Stores per-file transcode results (original/new size, codec, quality, duration, status, failure reason) in the `transcode_files` table.
- Stores per-job summaries (directory, timestamps, file counts, total space saved, quality, delete setting) in the `transcode_jobs` table.
- Called by `x265transcoder.py` at job start (`start_job`), after each file (`record_file`), and at job end (`complete_job`).
- Provides query functions for Flask: `get_job_history()`, `get_job_files()`, `get_lifetime_stats()`.
- Uses the shared SQLite database at `/config/media.db` (same as `scanner.py`).

### modules/collector.py

A legacy standalone scan utility (superseded by `scanner.py` but retained for backward compatibility). Responsibilities:

- Walks a directory tree and inventories all `.mkv` files.
- Detects codec via `pymediainfo` and categorises files as `x264` or `x265`.
- Writes a structured inventory to `/config/db.yaml` grouped by `category → codec → directory → filename: size_GB`.
- Removes the alternate-codec entry for a directory when one side is found (prevents stale records after a successful transcode).

### templates/index.html + static/

Single Jinja2 template that renders all UI states:

| State | Condition | Behaviour |
|-------|-----------|-----------|
| Idle | No running job | Shows library-type selector form |
| Directory list | Shows library selected | Lists top-level show folders with sizes |
| Subdirectory list | Show selected | Lists seasons with transcode options |
| Film list | Films selected | Lists film directories directly with transcode options |
| In progress | Job running | Auto-refreshes every 5 s; shows file and job progress bars |

JavaScript on the page handles loading-spinner display during the initial directory load POST. The progress-display branch uses `<meta http-equiv="refresh" content="5">` for polling rather than a JS fetch loop.

---

## Data Flow

### Job submission

```
User selects folder + options
        │
        ▼
POST /run  ──► flaskapp.py
        │  reads Telegram secrets from config
        │  calls store_job() → writes job_directory to /config/job.yaml
        │  subprocess.Popen(['python', 'x265transcoder.py', ...args])
        │  update_progress_yaml("job_progress", 0)
        │  update_progress_yaml("file_progress", 0)
        ▼
redirect 302 → GET /
```

### Transcode loop (x265transcoder.py)

```
for each file in file_list:
    mediainfo → check codec
    if HEVC → skip
    if H.264:
        rename file → file_old
        ffmpeg (hevc_qsv) with FfmpegProgress
            └─ on each progress event:
                   write file_progress  → /config/job.yaml
                   write job_progress   → /config/job.yaml
        post-transcode checks (size / duration / frames)
        if delete=Yes and checks pass → delete file_old
        update job_progress to (i+1)/total * 100
send Telegram notification
```

### Progress polling

```
Browser  GET /  (every 5 s via meta-refresh)
        │
        ▼
flaskapp.py reads /config/job.yaml
        └─ passes job_progress, file_progress, current_file,
           current_file_number, total_files to template
        ▼
index.html renders progress bars
```

---

## Inter-Process Communication

The Flask process and the transcoder process share state exclusively through `/config/job.yaml`. This file is read and written by both processes without any locking mechanism.

Key fields written by `x265transcoder.py`:

| Field | Type | Description |
|-------|------|-------------|
| `job_directory` | string | Target directory path |
| `total_files` | int | Total files to process |
| `current_file` | string | Filename currently being transcoded |
| `current_file_number` | int | 1-based index of current file |
| `file_progress` | int 0–100 | Progress of the current file |
| `job_progress` | int 0–100 | Overall job progress |

---

## Technology Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Web framework | Flask | Lightweight; minimal overhead for a single-user internal tool |
| Template engine | Jinja2 (bundled with Flask) | No separate build step required |
| Hardware encoder | `hevc_qsv` via jellyfin-ffmpeg | Jellyfin's FFmpeg build bundles QSV support; avoids manual FFmpeg compilation |
| Progress tracking | `ffmpeg-progress-yield` | Parses FFmpeg stderr to yield per-frame % without custom regex |
| Media analysis | `pymediainfo` (Python binding for libmediainfo) | More reliable than parsing `ffmpeg -i` output; works on all common containers |
| Config format | YAML | Human-readable; supports the nested structure needed for libraries + secrets |
| Media inventory DB | SQLite (`/config/media.db`) | Handles concurrent reads safely; efficient queries for recommendations; no external service needed |
| Scheduled jobs | APScheduler (BackgroundScheduler) | Pure-Python, integrates directly with Flask; no external cron or task queue required |
| IPC | Shared YAML file | Simple; avoids introducing a message queue or database dependency |
| Notifications | Telegram Bot API | Low friction; no server-side listener required |
| Containerisation | Docker | Encapsulates the Jellyfin FFmpeg dependency and Intel driver stack |
| CI/CD | GitHub Actions | Automatic Docker Hub pushes on `main` and `dev` branch commits |
