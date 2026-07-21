---
inclusion: always
---

# x265 Transcoder — Project Overview

## What this project is

A Dockerised web application that batch-transcodes video libraries from H.264 (x264) to HEVC (x265) using Intel Quick Sync Video hardware acceleration. It targets home media server use cases (Jellyfin/Plex-style libraries split into Shows and Films).

## Key components

| File | Role |
|------|------|
| `flaskapp.py` | Flask web UI, job dispatcher, and scheduler host (port 5000) |
| `x265transcoder.py` | Background transcode engine; spawned by Flask via `subprocess.Popen` |
| `modules/scanner.py` | Scheduled media inventory scanner; writes `/config/media.db` (SQLite). Runs nightly at 04:00 via APScheduler |
| `modules/history.py` | Transcode job history; records per-file results and per-job summaries to `/config/media.db` |
| `modules/encoder.py` | Hardware encoder detection (QSV/VAAPI/NVENC/software) and FFmpeg command builder |
| `modules/collector.py` | Legacy media inventory scanner; writes `/config/db.yaml` (dormant — superseded by scanner.py) |
| `templates/index.html` | Main Jinja2 template — renders all transcoder UI states |
| `templates/recommendations.html` | Recommendations page — library stats, top x264 films/shows sorted by size |
| `templates/history.html` | Transcode history page — lifetime stats, job list, per-job file detail drill-down |
| `templates/setup.html` | Initial configuration setup form |
| `ref/config.yaml` | Reference config template; live config must be at `/config/config.yaml` |
| `dockerfile` | Container definition; installs jellyfin-ffmpeg6, Intel/AMD/NVIDIA GPU drivers |

## Runtime paths (inside container)

- `/config/config.yaml` — library paths + Telegram secrets
- `/config/job.yaml` — shared-file IPC between Flask and the transcoder (progress, current file, counters)
- `/config/media.db` — SQLite media inventory database written by `modules/scanner.py`
- `/config/db.yaml` — legacy media inventory written by `collector.py` (dormant)
- `/logs/` — per-job log files

## Tech stack

Python 3.14 · Flask · Jinja2 · pymediainfo · ffmpeg-progress-yield · jellyfin-ffmpeg (hevc_qsv / hevc_vaapi / hevc_nvenc / libx265) · PyYAML · APScheduler · SQLite · Docker · GitHub Actions

## Documentation index

Full documentation lives in `docs/`:

- `docs/architecture.md` — component diagram, data flow, IPC, technology decisions
- `docs/deployment.md` — Docker run/Compose, volume mounts, CI/CD, QSV prerequisites, troubleshooting
- `docs/processes.md` — step-by-step logical flows for every route and the transcode engine
- `docs/issue-log.md` — known bugs, limitations, and improvement backlog

When working on this project, consult the relevant `docs/` file for the area you are changing before making recommendations or edits.
