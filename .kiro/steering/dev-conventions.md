---
inclusion: always
---

# x265 Transcoder — Development Conventions

## Language and runtime

- Python 3.9 (matches the Docker base image `python:3.9`).
- No type annotations currently used; adding them is welcome but not required.
- Dependencies are managed in `requirements.txt`. Pin versions when adding new packages.
- `ffmpeg-progress-yield` is installed separately in the Dockerfile (`pip3 install ffmpeg-progress-yield`) — if it is needed as an explicit dependency, add it to `requirements.txt`.
- APScheduler is pinned at `3.10.4` in `requirements.txt`. Flask is started with `use_reloader=False` to prevent the scheduler from running twice.

## Project structure

- Keep Flask routes in `flaskapp.py`. Do not add business logic to route handlers; extract functions.
- Keep transcode logic in `x265transcoder.py`. It is designed to run as a standalone CLI process.
- Shared/reusable utilities belong in `modules/`.
- `modules/scanner.py` is the active media inventory scanner (SQLite-backed). `modules/collector.py` is legacy (YAML-backed) and should not be extended.
- HTML templates go in `templates/`. CSS goes in `static/styles.css`. There is a single template (`index.html`) that handles all transcoder UI states, plus `recommendations.html` for the library overview and `setup.html` for initial config.

## Configuration and secrets

- All configuration is in `/config/config.yaml` at runtime. The file at `ref/config.yaml` is the reference template only — never commit real credentials.
- Access secrets via `config['secrets'][key]` in `flaskapp.py`. The `/get_secret/<name>` HTTP endpoint exists but is unauthenticated; avoid extending it (see `docs/issue-log.md` ISS-002).

## Inter-process communication

- Flask and `x265transcoder.py` share state through `/config/job.yaml`. Both processes read and write this file. See `docs/architecture.md` for the full field schema.
- There is currently no file locking on `job.yaml` (ISS-001 in `docs/issue-log.md`). Be careful adding new read/write operations to this file.
- `modules/scanner.py` uses SQLite (`/config/media.db`) with WAL mode for the media inventory. Flask reads this DB on `GET /recommendations`; the scanner writes to it during scans. SQLite handles this concurrency safely.

## Known issues to be aware of

Before making changes, check `docs/issue-log.md` for relevant open issues. Key ones that affect day-to-day development:

- **ISS-006** — Flask runs with `debug=True` in the container; not suitable for multi-user or production environments.
- **ISS-005** — Directory size calculation is synchronous and blocks the request thread on large libraries.
- **ISS-009** — `store_job()` in `flaskapp.py` has a bug in its `FileNotFoundError` handler that causes `job_directory` to be dropped when `job.yaml` is absent.

## Docker and CI/CD

- The container image is `audioe/x265transcoder`. Tags: `latest` (main branch), `dev` (dev branch).
- GitHub Actions workflows in `.github/workflows/` build and push automatically on branch push.
- The `dev` workflow appends `_dev` to `version.txt` before building — do not commit that change back.
- Increment `version.txt` manually when releasing. No automated versioning tooling is configured.
- The container requires `/dev/dri` passed through for Intel QSV. See `docs/deployment.md` for full hardware and volume requirements.

## Logging

- `x265transcoder.py` writes structured logs to `/logs/transcode_<DD-MM-YY_HH-MM-SS>.log`.
- Log level is `DEBUG` — all `logging.debug()` calls are written to the file.
- `flaskapp.py` does not currently write to a log file; it uses `print()` for debug output that goes to stdout.
