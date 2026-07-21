"""
Media library scanner.

Scans Films and Shows libraries, recording file metadata (codec, size, mtime)
into a SQLite database at /config/media.db.

- First run (or missing DB): performs a full scan of all .mkv files.
- Subsequent runs: performs an incremental scan — only processes files whose
  mtime has changed and removes records for files that no longer exist on disk.
"""

import os
import sqlite3
import logging
from datetime import datetime, timezone
from pymediainfo import MediaInfo

DB_PATH = "/config/media.db"

logger = logging.getLogger(__name__)


def _get_connection():
    """Get a SQLite connection, creating the schema if needed."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")  # safer for concurrent reads
    _ensure_schema(conn)
    return conn


def _ensure_schema(conn):
    """Create tables if they don't already exist."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS media_files (
            id INTEGER PRIMARY KEY,
            category TEXT NOT NULL,
            title TEXT NOT NULL,
            season TEXT,
            filepath TEXT NOT NULL UNIQUE,
            filename TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            codec TEXT NOT NULL,
            mtime REAL NOT NULL,
            scanned_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS scan_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            last_full_scan TEXT,
            last_incremental_scan TEXT
        );

        INSERT OR IGNORE INTO scan_state (id) VALUES (1);

        CREATE INDEX IF NOT EXISTS idx_media_codec ON media_files(codec);
        CREATE INDEX IF NOT EXISTS idx_media_category ON media_files(category);
        CREATE INDEX IF NOT EXISTS idx_media_filepath ON media_files(filepath);
    """)
    conn.commit()


def _get_video_codec(file_path):
    """Return the codec of the first video track, or None."""
    try:
        media_info = MediaInfo.parse(file_path)
        for track in media_info.tracks:
            if track.track_type == "Video":
                return track.format
    except Exception as e:
        logger.warning(f"Failed to parse media info for {file_path}: {e}")
    return None


def _normalise_codec(raw_codec):
    """Map raw codec string to 'x264' or 'x265'."""
    if raw_codec in ("Advanced Video Codec", "AVC"):
        return "x264"
    elif raw_codec in ("High Efficiency Video Coding", "HEVC"):
        return "x265"
    else:
        # Unknown codec — store the raw value for visibility
        return raw_codec or "unknown"


def _parse_path(filepath, library_root, category):
    """
    Extract title and season from a filepath relative to its library root.

    Films:  /films/Inception/Inception.mkv       -> title='Inception', season=None
    Shows:  /shows/Breaking Bad/S1/ep1.mkv       -> title='Breaking Bad', season='S1'
    """
    rel_path = os.path.relpath(filepath, library_root)
    parts = rel_path.split(os.sep)

    title = parts[0] if parts else os.path.basename(filepath)

    if category == "shows" and len(parts) > 2:
        season = parts[1]
    else:
        season = None

    return title, season


def _walk_library(library_root, category):
    """
    Yield (filepath, filename, size_bytes, mtime, title, season)
    for every .mkv file under library_root.
    """
    for dirpath, _, filenames in os.walk(library_root):
        for filename in filenames:
            if not filename.lower().endswith(".mkv"):
                continue
            filepath = os.path.join(dirpath, filename)
            try:
                stat = os.stat(filepath)
                size_bytes = stat.st_size
                mtime = stat.st_mtime
            except OSError as e:
                logger.warning(f"Cannot stat {filepath}: {e}")
                continue

            title, season = _parse_path(filepath, library_root, category)
            yield filepath, filename, size_bytes, mtime, title, season


def full_scan(libraries):
    """
    Perform a full scan of all configured libraries.

    Args:
        libraries: dict with keys 'films' and 'shows' mapping to directory paths.
    """
    logger.info("Starting full media scan...")
    conn = _get_connection()
    now = datetime.now(timezone.utc).isoformat()

    # Clear existing data for a clean full scan
    conn.execute("DELETE FROM media_files")
    conn.commit()

    count = 0
    for category, library_root in libraries.items():
        if not library_root or not os.path.isdir(library_root):
            logger.warning(f"Library path not accessible: {category}={library_root}")
            continue

        for filepath, filename, size_bytes, mtime, title, season in _walk_library(library_root, category):
            raw_codec = _get_video_codec(filepath)
            codec = _normalise_codec(raw_codec)

            conn.execute("""
                INSERT OR REPLACE INTO media_files
                    (category, title, season, filepath, filename, size_bytes, codec, mtime, scanned_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (category, title, season, filepath, filename, size_bytes, codec, mtime, now))
            count += 1

            # Commit in batches to avoid holding large transactions
            if count % 100 == 0:
                conn.commit()

    conn.commit()

    # Update scan state
    conn.execute("UPDATE scan_state SET last_full_scan = ? WHERE id = 1", (now,))
    conn.commit()
    conn.close()

    logger.info(f"Full scan complete. {count} files indexed.")
    return count


def incremental_scan(libraries):
    """
    Perform an incremental scan — only process new/changed files and remove deleted ones.

    Args:
        libraries: dict with keys 'films' and 'shows' mapping to directory paths.
    """
    logger.info("Starting incremental media scan...")
    conn = _get_connection()
    now = datetime.now(timezone.utc).isoformat()

    # Build a set of all current filepaths on disk, and their mtimes
    disk_files = {}  # filepath -> (filename, size_bytes, mtime, title, season, category)
    for category, library_root in libraries.items():
        if not library_root or not os.path.isdir(library_root):
            logger.warning(f"Library path not accessible: {category}={library_root}")
            continue
        for filepath, filename, size_bytes, mtime, title, season in _walk_library(library_root, category):
            disk_files[filepath] = (filename, size_bytes, mtime, title, season, category)

    # Get all currently indexed filepaths and their mtimes from the DB
    db_rows = conn.execute("SELECT filepath, mtime FROM media_files").fetchall()
    db_files = {row["filepath"]: row["mtime"] for row in db_rows}

    # 1. Remove records for files that no longer exist
    deleted = set(db_files.keys()) - set(disk_files.keys())
    if deleted:
        conn.executemany(
            "DELETE FROM media_files WHERE filepath = ?",
            [(fp,) for fp in deleted]
        )
        logger.info(f"Removed {len(deleted)} deleted file(s) from database.")

    # 2. Add or update files that are new or have changed mtime
    updated_count = 0
    for filepath, (filename, size_bytes, mtime, title, season, category) in disk_files.items():
        existing_mtime = db_files.get(filepath)
        if existing_mtime is not None and abs(existing_mtime - mtime) < 0.001:
            # File unchanged — skip
            continue

        # File is new or modified — scan codec
        raw_codec = _get_video_codec(filepath)
        codec = _normalise_codec(raw_codec)

        conn.execute("""
            INSERT OR REPLACE INTO media_files
                (category, title, season, filepath, filename, size_bytes, codec, mtime, scanned_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (category, title, season, filepath, filename, size_bytes, codec, mtime, now))
        updated_count += 1

        if updated_count % 100 == 0:
            conn.commit()

    conn.commit()

    # Update scan state
    conn.execute("UPDATE scan_state SET last_incremental_scan = ? WHERE id = 1", (now,))
    conn.commit()
    conn.close()

    logger.info(f"Incremental scan complete. {updated_count} file(s) added/updated, {len(deleted)} removed.")
    return updated_count, len(deleted)


def run_scan(libraries):
    """
    Run the appropriate scan type: full if no DB exists or is empty, incremental otherwise.

    Args:
        libraries: dict with keys 'films' and 'shows' mapping to directory paths.
    """
    if not os.path.exists(DB_PATH):
        return full_scan(libraries)

    conn = _get_connection()
    row_count = conn.execute("SELECT COUNT(*) FROM media_files").fetchone()[0]
    conn.close()

    if row_count == 0:
        return full_scan(libraries)
    else:
        return incremental_scan(libraries)


def get_recommendations(limit=50):
    """
    Return recommendations for transcoding — largest x264 files and show seasons.

    Returns:
        dict with keys:
            'films': list of dicts (title, filename, size_bytes, filepath)
            'shows': list of dicts (title, season, episode_count, total_bytes)
            'stats': dict with summary statistics
    """
    if not os.path.exists(DB_PATH):
        return {"films": [], "shows": [], "stats": {}}

    conn = _get_connection()

    # Top x264 films by individual file size
    films = conn.execute("""
        SELECT title, filename, size_bytes, filepath
        FROM media_files
        WHERE category = 'films' AND codec = 'x264'
        ORDER BY size_bytes DESC
        LIMIT ?
    """, (limit,)).fetchall()

    # Top x264 show seasons by total season size
    shows = conn.execute("""
        SELECT title, season, COUNT(*) as episode_count, SUM(size_bytes) as total_bytes
        FROM media_files
        WHERE category = 'shows' AND codec = 'x264'
        GROUP BY title, season
        ORDER BY total_bytes DESC
        LIMIT ?
    """, (limit,)).fetchall()

    # Summary statistics
    stats = {}
    row = conn.execute("""
        SELECT
            COUNT(*) as total_files,
            SUM(size_bytes) as total_size,
            SUM(CASE WHEN codec = 'x264' THEN 1 ELSE 0 END) as x264_count,
            SUM(CASE WHEN codec = 'x265' THEN 1 ELSE 0 END) as x265_count,
            SUM(CASE WHEN codec = 'x264' THEN size_bytes ELSE 0 END) as x264_size,
            SUM(CASE WHEN codec = 'x265' THEN size_bytes ELSE 0 END) as x265_size
        FROM media_files
    """).fetchone()

    if row and row["total_files"]:
        stats = {
            "total_files": row["total_files"],
            "total_size_gb": round(row["total_size"] / (1024**3), 2) if row["total_size"] else 0,
            "x264_count": row["x264_count"] or 0,
            "x265_count": row["x265_count"] or 0,
            "x264_size_gb": round(row["x264_size"] / (1024**3), 2) if row["x264_size"] else 0,
            "x265_size_gb": round(row["x265_size"] / (1024**3), 2) if row["x265_size"] else 0,
        }

    # Get last scan times
    scan_state = conn.execute("SELECT last_full_scan, last_incremental_scan FROM scan_state WHERE id = 1").fetchone()
    if scan_state:
        stats["last_full_scan"] = scan_state["last_full_scan"]
        stats["last_incremental_scan"] = scan_state["last_incremental_scan"]

    conn.close()

    return {
        "films": [dict(r) for r in films],
        "shows": [dict(r) for r in shows],
        "stats": stats,
    }
