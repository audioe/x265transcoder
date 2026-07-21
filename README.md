# x265 Transcoder

A web-based tool for batch transcoding video libraries from x264 (H.264) to x265 (HEVC) using Intel Quick Sync Video (QSV) hardware acceleration via Jellyfin's FFmpeg build.

Current version: **1.3.6**

---

## Overview

x265 Transcoder provides a simple browser UI to select a media directory (Films or TV Shows), configure transcode options, and kick off a background encoding job. Progress is tracked in real time and a Telegram notification is sent on completion.

Key features:

- Hardware-accelerated HEVC encoding via Intel QSV (`hevc_qsv`)
- Supports separate Films and TV Shows library paths
- Per-file and overall job progress displayed in the UI
- Post-transcode validation (file size, duration, frame count)
- Optional deletion of source files after successful transcode
- Telegram notifications on job completion or failure
- Structured log files written to `/logs`

---

## Quick Start

### Prerequisites

- Docker host with an Intel GPU that supports QSV (6th-gen Core or later recommended)
- Intel media drivers available on the host (`intel-media-va-driver-non-free` / `onevpl-tools`)
- A `/config/config.yaml` file (see [Configuration](#configuration))
- Media libraries accessible as Docker volume mounts

### Run with Docker

```bash
docker run -d \
  --name x265transcoder \
  --device /dev/dri:/dev/dri \
  -p 5000:5000 \
  -v /path/to/config:/config \
  -v /path/to/logs:/logs \
  -v /path/to/shows:/shows \
  -v /path/to/films:/films \
  audioe/x265transcoder:latest
```

Then open `http://<host>:5000` in a browser.

### Run with Docker Compose (example)

```yaml
services:
  x265transcoder:
    image: audioe/x265transcoder:latest
    devices:
      - /dev/dri:/dev/dri
    ports:
      - "5000:5000"
    volumes:
      - /path/to/config:/config
      - /path/to/logs:/logs
      - /path/to/shows:/shows
      - /path/to/films:/films
    restart: unless-stopped
```

---

## Configuration

Copy `ref/config.yaml` to your `/config` mount and populate it:

```yaml
secrets:
  TELEGRAM_TOKEN: <your-bot-token>
  TELEGRAM_CHATID: <your-chat-id>
libraries:
  shows: /shows          # path as seen inside the container
  films: /films
```

| Key | Description |
|-----|-------------|
| `secrets.TELEGRAM_TOKEN` | Bot token from @BotFather. Used for job completion notifications. |
| `secrets.TELEGRAM_CHATID` | Target chat/channel ID for notifications. |
| `libraries.shows` | Container-internal path to the TV shows root directory. |
| `libraries.films` | Container-internal path to the films root directory. |

If `/config/config.yaml` is absent the app will start but all library and notification features will fail.

---

## Transcode Options (UI)

| Option | Description | Default |
|--------|-------------|---------|
| Library type | Films or Shows — determines the directory browsing flow | — |
| Folder | The specific show season or film directory to process | — |
| Include | File extension filter (e.g. `.mkv`) | — |
| Quality | CQP value 18–25. Lower = better quality / larger file. | 23 |
| Delete after transcoding | Remove the original `_old` file once validation passes | Enabled |

---

## Runtime Paths (inside container)

| Path | Purpose |
|------|---------|
| `/config/config.yaml` | Application configuration |
| `/config/job.yaml` | Live job state (progress, current file, totals) |
| `/config/db.yaml` | Media inventory written by `collector.py` |
| `/logs/transcode_<datetime>.log` | Per-job log file |
| `/app` | Application working directory |

---

## Documentation

| Document | Description |
|----------|-------------|
| [Architecture](docs/architecture.md) | Component overview, data flow, technology decisions |
| [Deployment](docs/deployment.md) | Full deployment guide including CI/CD and QSV setup |
| [Logical Processes](docs/processes.md) | Step-by-step flow for every major operation |
| [Issue Log](docs/issue-log.md) | Known issues, limitations, and improvement backlog |

---

## Docker Hub

- `audioe/x265transcoder:latest` — built from `main` branch
- `audioe/x265transcoder:dev` — built from `dev` branch (version string has `_dev` suffix)
