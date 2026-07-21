# Issue Log

Issues are categorised as **Bug**, **Limitation**, or **Improvement**. Items without a resolution are open.

---

## Open Issues

### ISS-001 — Race condition on /config/job.yaml writes
**Type:** Bug  
**Severity:** Medium  
**Files:** `flaskapp.py`, `x265transcoder.py`

Both the Flask process and the transcoder process read and write `/config/job.yaml` without any file locking. A write from one process can partially overwrite or corrupt a concurrent write from the other, particularly at job submission time when Flask resets progress fields while the transcoder is also initialising.

**Impact:** Corrupted YAML could cause the progress UI to crash on parse, or cause the transcoder to lose its job metadata.

**Mitigation applied (2026-07-20):** The `index()` route now guards against `yaml.safe_load()` returning `None` (which happens when the file is read mid-write). The page gracefully shows "Loading..." and auto-refreshes. The underlying race condition (no file locking) remains unresolved.

**Suggested fix:** Use `fcntl.flock()` (Linux) or a lock file alongside the YAML, or replace the shared-file IPC with a lightweight SQLite database or Redis instance.

---

### ISS-002 — `GET /get_secret/<name>` is unauthenticated
**Type:** Bug / Security  
**Severity:** High (if network-exposed)  
**Files:** `flaskapp.py`

The `/get_secret/<name>` route returns config secrets (including the Telegram bot token) in plain text with no authentication or authorisation check.

**Impact:** Any client that can reach port 5000 can retrieve all configured secrets.

**Suggested fix:** Remove the public endpoint and read secrets directly from the `config` dict where needed (already done in `run()`). The route appears to be a development artefact.

---

### ISS-003 — Single active job enforcement uses `ps aux`
**Type:** Limitation  
**Severity:** Low  
**Files:** `flaskapp.py → transcode_check()`

Job detection is implemented by scanning `ps aux` output for the string `x265transcoder.py`. This is fragile: it can match unrelated processes, and `ps` is not available on all container base images.

**Impact:** Multiple jobs could be submitted concurrently, or a false positive could block job submission.

**Suggested fix:** Write a PID file when the transcoder starts and check/clean it on startup; or use a proper process manager.

---

### ISS-004 — `modules/collector.py` is not integrated into the UI
**Type:** Improvement  
**Severity:** Low  
**Files:** `modules/collector.py`, `flaskapp.py`  
**Status:** Resolved (superseded)

The `collector.py` module scans the media library and writes a codec inventory to `/config/db.yaml`, but there are no Flask routes that call it or expose the data.

**Resolution (2026-07-20):** The new `modules/scanner.py` fully supersedes `collector.py`. It performs scheduled (nightly at 04:00) and manual scans, stores results in SQLite (`/config/media.db`), and exposes library stats and recommendations via `GET /recommendations`. `collector.py` is retained for backward compatibility only and should not be extended.

---

### ISS-005 — Directory size calculation blocks the request thread
**Type:** Limitation  
**Severity:** Medium  
**Files:** `flaskapp.py → get_directory_size()`

`get_directory_size()` performs a full recursive `os.walk` on every listed directory synchronously in the Flask request handler. For large libraries this can take tens of seconds, blocking the entire Flask dev server (single-threaded by default).

**Impact:** The UI becomes unresponsive while directory sizes are being calculated.

**Suggested fix:** Run size calculations in a background thread or process and return cached values. Alternatively, use `du -sb` via subprocess which can be faster for large trees. Consider running Flask under Gunicorn with multiple workers for the production container.

---

### ISS-006 — Flask development server used in production
**Type:** Limitation  
**Severity:** Medium  
**Files:** `flaskapp.py`, `dockerfile`

The container CMD runs Flask with `app.run(debug=True, host='0.0.0.0')`. The Flask development server is single-threaded, not designed for concurrent requests, and has `debug=True` which enables the interactive debugger and auto-reloader — both inappropriate for production.

**Suggested fix:** Switch to Gunicorn: `gunicorn -w 2 -b 0.0.0.0:5000 flaskapp:app`. Disable debug mode or gate it behind an environment variable.

---

### ISS-007 — No validation on POST form inputs
**Type:** Bug / Security  
**Severity:** Medium  
**Files:** `flaskapp.py → run()`

The `folder`, `include`, `quality`, and `delete` values from the transcode submission form are passed directly as CLI arguments to `subprocess.Popen` without sanitisation. A user could potentially pass path traversal sequences or shell metacharacters via the `folder` or `include` fields.

**Impact:** Limited by the fact that `subprocess.Popen` with a list argument does not invoke a shell, so shell injection is mitigated. However, path traversal via `folder` could cause the transcoder to operate on unintended directories.

**Suggested fix:** Validate that `folder` is a subdirectory of the configured library paths; validate `quality` is an integer in the 18–25 range; restrict `include` to known file extensions.

---

### ISS-013 — `-x265-params` flag is ignored by `hevc_qsv` encoder
**Type:** Bug  
**Severity:** Low  
**Files:** `x265transcoder.py`  
**Status:** Resolved

The FFmpeg command passes `-x265-params "repeat-headers=1:profile=main10:level=5.1"` which is a parameter for the **software** x265 encoder only. Since the project uses `hevc_qsv` (Intel Quick Sync hardware encoder), this flag is silently ignored by FFmpeg. The intended profile/level constraints are not being applied.

**Resolution (2026-07-21):** Removed the dead `-x265-params` flag and its unused `params` variable. Replaced with QSV-native options: `-profile:v main10`, `-preset medium`, `-look_ahead 1`, `-look_ahead_depth 40`, `-adaptive_i 1`, `-adaptive_b 1`. These were already partially applied in a prior session; this session cleaned up the remaining dead code.

---

### ISS-008 — FFmpeg binary path is hardcoded
**Type:** Limitation  
**Files:** `modules/encoder.py`

The FFmpeg binary path `/usr/lib/jellyfin-ffmpeg/ffmpeg` is defined as `FFMPEG_PATH` in `modules/encoder.py`. It is now centralized in one place (previously hardcoded in `x265transcoder.py`), but still not configurable at runtime.

**Suggested fix:** Make the FFmpeg path configurable via `config.yaml` or an environment variable with the current path as the default.

---

### ISS-009 — `store_job()` in `flaskapp.py` has a bug in its FileNotFoundError handler
**Type:** Bug  
**Severity:** Low  
**Files:** `flaskapp.py → store_job()`

In the `except FileNotFoundError` branch, the code assigns `data` twice in succession, with the second assignment (`data = {'progress': "0"}`) overwriting the first (`data = {'job_directory': job_data}`). As a result, if `job.yaml` does not exist, the created file will only contain `progress: "0"` and the `job_directory` will be lost.

```python
# Bug — second assignment clobbers first
data = {'job_directory': job_data}
data = {'progress': "0"}
```

**Suggested fix:**
```python
data = {'job_directory': job_data, 'progress': "0"}
```

---

### ISS-010 — Progress meta-refresh is not used for films jobs
**Type:** Bug  
**Severity:** Low  
**Files:** `templates/index.html`  
**Status:** Resolved

The auto-refresh `<meta>` tag is rendered when `transcoder_status == True`, but the "in progress" display branches on whether `"films"` appears in `job` (the `job_directory` value). The films branch does not display progress bars — it only shows the directory name. The progress bars are only shown in the shows branch, and only when `current_file` does not contain `"Loading"`. This may be intentional, but is undocumented.

**Resolution (2026-07-20):** Rewrote `index.html` progress display. Both films and shows now show the same progress UI (current file, file/job progress bars). The `<meta refresh>` tag was also moved from `<body>` to `<head>` where it belongs.

---

## Resolved Issues

### ISS-014 — Job history stuck in "Running" if Telegram notification fails
**Type:** Bug  
**Severity:** Medium  
**Files:** `x265transcoder.py`  
**Resolved:** 2026-07-21

The `complete_job()` call was positioned **after** the `send_telegram_message()` calls at the end of `convert_job()`. If Telegram's API failed with an unhandled exception (timeout, DNS failure, network error), `complete_job()` was never reached, leaving the job record permanently in "running" status in the database.

Additionally, the second `send_telegram_message` call had a broken indentation — it was outside the `if/else` block, causing the success message to always be sent regardless of whether failures occurred.

**Fix applied:**
- Moved `complete_job()` to execute **before** any Telegram notification attempts.
- Fixed `send_telegram_message` indentation so success/failure messages are mutually exclusive.
- Added missing `Successful.append(filetitle)` so the success list is actually populated.

---

### ISS-012 — QSV hardware decoder silently drops frames on certain streams
**Type:** Bug  
**Severity:** High  
**Files:** `x265transcoder.py`  
**Resolved:** 2026-07-21

The FFmpeg command used `-c:v h264_qsv` to force QSV hardware decoding of the input stream. On certain files (e.g. IMAX variable aspect ratio, unusual NAL units, high-profile features), the QSV decoder silently dropped frames without raising errors, producing a truncated output that FFmpeg still reported as 100% complete. Post-transcode validation passed because the file was smaller and `pymediainfo` could still read it.

**Root cause:** QSV hardware decoders have limited compatibility with complex H.264 streams compared to software decoders.

**Fix applied:**
- Removed `-c:v h264_qsv` input decoder flag. FFmpeg now uses software decoding (auto-selects correct decoder) for the input.
- QSV is still used for the output encoder (`hevc_qsv`) where the performance benefit matters.
- Also added `-map 0:s? -c:s copy` to preserve subtitle streams, and improved logging (full FFmpeg command now logged).

---

### ISS-011 — SQLite "database is locked" error on concurrent access
**Type:** Bug  
**Severity:** High  
**Files:** `modules/scanner.py`  
**Resolved:** 2026-07-20

When the background scan thread held a write lock on `/config/media.db`, any concurrent request to `GET /recommendations` would also attempt to run `executescript` for schema initialisation, causing an immediate `sqlite3.OperationalError: database is locked`.

**Root cause:** Schema initialisation via `executescript` (which requires an exclusive lock) was called on every `_get_connection()`, and connections had no busy timeout.

**Fix applied:**
- Schema initialisation (`_init_schema()`) is now a one-shot operation protected by a threading lock — runs once per process lifetime.
- All `sqlite3.connect()` calls now pass `timeout=30` so readers wait for the write lock to release instead of failing immediately.

---

## Improvement Backlog

| ID | Description | Priority |
|----|-------------|----------|
| IMP-001 | Add Gunicorn to the container (ISS-006) | High |
| IMP-002 | Fix `store_job` FileNotFoundError handler (ISS-009) | High |
| IMP-003 | Remove or secure `/get_secret` endpoint (ISS-002) | High |
| IMP-004 | Add file locking to job.yaml writes (ISS-001) | Medium |
| IMP-005 | ~~Wire `collector.py` into the UI as a library overview page~~ — resolved by `modules/scanner.py` + `/recommendations` | ~~Medium~~ Done |
| IMP-006 | Async directory size calculation (ISS-005) | Medium |
| IMP-007 | Make FFmpeg path configurable (ISS-008) | Low |
| IMP-008 | Add input validation on transcode form (ISS-007) | Medium |
| IMP-009 | Replace `ps aux` job detection with PID file (ISS-003) | Low |
| IMP-010 | ~~Fix dead `-x265-params` and optimise QSV encode settings (ISS-013)~~ | ~~Medium~~ Done |
