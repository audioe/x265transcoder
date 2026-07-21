# Deployment Guide

## Requirements

### Host Hardware

- Intel CPU with integrated or discrete GPU supporting Intel Quick Sync Video (QSV).
- Recommended minimum: 6th-generation Intel Core (Skylake) for H.264 decode + HEVC encode.
- For 10-bit HEVC encoding (`p010le` pixel format): 7th-generation (Kaby Lake) or later.

### Host Software

- Docker Engine 20.10 or later.
- Intel media drivers installed on the host:
  - Debian/Ubuntu: `intel-media-va-driver-non-free`, `onevpl-tools`, `vainfo`
  - These are also installed inside the container image, but the host `/dev/dri` device must be accessible.

Verify QSV availability on the host before deploying:

```bash
vainfo
# Should list VAEntrypointEncSlice for H264 and HEVCMain/HEVCMain10
```

---

## Volume Mounts

The container expects the following mounts:

| Host path | Container path | Required | Description |
|-----------|---------------|----------|-------------|
| `/path/to/config` | `/config` | Yes | Config and runtime state files |
| `/path/to/logs` | `/logs` | Yes | Transcode log output |
| `/path/to/shows` | `/shows` (or as configured) | Yes | TV shows library root |
| `/path/to/films` | `/films` (or as configured) | Yes | Films library root |

The container-internal paths for `shows` and `films` must match what is configured in `/config/config.yaml` under `libraries.shows` and `libraries.films`.

---

## Configuration File

Create `/path/to/config/config.yaml` on the host before first run:

```yaml
secrets:
  TELEGRAM_TOKEN: "123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ"
  TELEGRAM_CHATID: "-100123456789"
libraries:
  shows: /shows
  films: /films
```

A reference template is available at `ref/config.yaml` in the repository.

---

## Docker Run

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

## Docker Compose

```yaml
services:
  x265transcoder:
    image: audioe/x265transcoder:latest
    container_name: x265transcoder
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

## Building Locally

```bash
git clone https://github.com/audioe/x265transcoder.git
cd x265transcoder
docker build -t x265transcoder:local .
```

The Dockerfile:

1. Starts from `python:3.14-bookworm` (Bookworm base ensures compatibility with Jellyfin apt repo and Intel media drivers).
2. Installs system dependencies including `libmediainfo0v5`.
3. Installs Python dependencies from `requirements.txt` plus `ffmpeg-progress-yield`.
4. Adds the Jellyfin apt repository and installs `jellyfin-ffmpeg6`.
5. Installs Intel QSV runtime packages (`onevpl-tools`, `vainfo`, `intel-media-va-driver-non-free`).
6. Copies application source into `/app`.
7. Exposes port `5000` and starts the Flask app via `CMD ["python", "flaskapp.py"]`.

Note: The Dockerfile also installs `nano` for in-container debugging convenience.

---

## CI/CD Pipelines

Two GitHub Actions workflows handle automated Docker Hub publishing.

### main-docker-image.yml — Production

Trigger: push to `main` branch

Steps:
1. Checkout code.
2. Build image tagged `audioe/x265transcoder:latest`.
3. Log in to Docker Hub using `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN` repository secrets.
4. Push `audioe/x265transcoder:latest`.

### dev-docker-image.yml — Development

Trigger: push to `dev` branch

Steps:
1. Checkout code.
2. Append `_dev` to the content of `version.txt` (e.g. `1.3.6` → `1.3.6_dev`).
3. Build image tagged `audioe/x265transcoder:dev`.
4. Log in to Docker Hub.
5. Push `audioe/x265transcoder:dev`.

### Required Repository Secrets

| Secret | Description |
|--------|-------------|
| `DOCKERHUB_USERNAME` | Docker Hub account username |
| `DOCKERHUB_TOKEN` | Docker Hub access token (not the account password) |

These must be set under **Settings → Secrets and variables → Actions** in the GitHub repository.

---

## Upgrading

Pull the latest image and recreate the container. No database migrations are required — the YAML files in `/config` are forward-compatible.

```bash
docker pull audioe/x265transcoder:latest
docker stop x265transcoder
docker rm x265transcoder
# re-run docker run command above
```

Or with Compose:

```bash
docker compose pull
docker compose up -d
```

---

## Troubleshooting

### UI shows blank / config error

- Confirm `/config/config.yaml` exists and is valid YAML.
- Check container logs: `docker logs x265transcoder`.

### No QSV encoding / FFmpeg errors

- Verify `/dev/dri` is passed through to the container.
- Run `vainfo` inside the container: `docker exec -it x265transcoder vainfo`.
- Ensure the host Intel media driver version is compatible with the `jellyfin-ffmpeg6` build inside the container.

### Progress stuck at 0%

- The UI refreshes every 5 seconds. Allow a few refresh cycles after job submission.
- Check `/config/job.yaml` is writable by the container process.
- Review the log in `/logs/` for FFmpeg errors.

### Telegram notifications not sent

- Verify `TELEGRAM_TOKEN` and `TELEGRAM_CHATID` in `/config/config.yaml`.
- Confirm the bot has been added to the target chat and has permission to send messages.
- Check the log file for `requests` errors.
