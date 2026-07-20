---
inclusion: always
---

# x265 Transcoder — Project Overview

## What this project is

A Dockerised web application that batch-transcodes video libraries from H.264 (x264) to HEVC (x265) using Intel Quick Sync Video hardware acceleration. It targets home media server use cases (Jellyfin/Plex-style libraries split into Shows and Films).

## Key components

| File | Role |
|------|------|
| `flaskapp.py` | Flask web UI and job dispatcher (port 5000) |
| `x265transcoder.py` | Background transcode engine; spawned by Flask via `subprocess.Popen` |
| `modules/collector.py` | Standalone media inventory scanner; writes `/config/db.yaml` (not yet wired into the UI) |
| `templates/index.html` | Single Jinja2 template — renders all UI states |
| `ref/config.yaml` | Reference config template; live config must be at `/config/config.yaml` |
| `dockerfile` | Container definition; installs jellyfin-ffmpeg6 and Intel QSV drivers |

## Runtime paths (inside container)

- `/config/config.yaml` — library paths + Telegram secrets
- `/config/job.yaml` — shared-file IPC between Flask and the transcoder (progress, current file, counters)
- `/config/db.yaml` — media inventory written by `collector.py`
- `/logs/` — per-job log files

## Tech stack

Python 3.9 · Flask · Jinja2 · pymediainfo · ffmpeg-progress-yield · jellyfin-ffmpeg (hevc_qsv) · PyYAML · Docker · GitHub Actions

## Documentation index

Full documentation lives in `docs/`:

- `docs/architecture.md` — component diagram, data flow, IPC, technology decisions
- `docs/deployment.md` — Docker run/Compose, volume mounts, CI/CD, QSV prerequisites, troubleshooting
- `docs/processes.md` — step-by-step logical flows for every route and the transcode engine
- `docs/issue-log.md` — known bugs, limitations, and improvement backlog

When working on this project, consult the relevant `docs/` file for the area you are changing before making recommendations or edits.
