"""
Transcode job history.

Records per-file transcode results and per-job summaries into
the shared SQLite database at /config/media.db.

Used by x265transcoder.py to write records, and by flaskapp.py to query them.
"""

import os
import sqlite3
import threading
from datetime import datetime, timezone

DB_PATH = "/config/media.db"

_schema_initialised = False
_schema_lock = threading.Lock()


def _get_connection():
    """Get a SQLite connection with busy timeout."""
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _init_schema():
    """Ensure transcode history tables exist. Called once, thread-safe."""
    global _schema_initialised
    if _schema_initialised:
        return
    with _schema_lock:
        if _schema_initialised:
            return
        conn = sqlite3.connect(DB_PATH, timeout=30)
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS transcode_jobs (
                id INTEGER PRIMARY KEY,
                directory TEXT NOT NULL,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                total_files INTEGER DEFAULT 0,
                successful_count INTEGER DEFAULT 0,
                failed_count INTEGER DEFAULT 0,
                skipped_count INTEGER DEFAULT 0,
                total_original_bytes INTEGER DEFAULT 0,
                total_new_bytes INTEGER DEFAULT 0,
                total_space_saved_bytes INTEGER DEFAULT 0,
                quality INTEGER,
                delete_originals TEXT,
                status TEXT DEFAULT 'running'
            );

            CREATE TABLE IF NOT EXISTS transcode_files (
                id INTEGER PRIMARY KEY,
                job_id INTEGER NOT NULL,
                filepath TEXT NOT NULL,
                filename TEXT NOT NULL,
                category TEXT,
                title TEXT,
                season TEXT,
                original_size_bytes INTEGER,
                new_size_bytes INTEGER,
                space_saved_bytes INTEGER,
                original_codec TEXT,
                quality INTEGER,
                duration_seconds REAL,
                status TEXT NOT NULL,
                failure_reason TEXT,
                transcoded_at TEXT NOT NULL,
                FOREIGN KEY (job_id) REFERENCES transcode_jobs(id)
            );

            CREATE INDEX IF NOT EXISTS idx_transcode_files_job ON transcode_files(job_id);
            CREATE INDEX IF NOT EXISTS idx_transcode_jobs_status ON transcode_jobs(status);
        """)
        conn.close()
        _schema_initialised = True


# --- Write functions (called from x265transcoder.py) ---

def start_job(directory, quality, delete_originals):
    """Create a new job record and return its ID."""
    _init_schema()
    conn = _get_connection()
    now = datetime.now(timezone.utc).isoformat()
    cursor = conn.execute("""
        INSERT INTO transcode_jobs (directory, started_at, quality, delete_originals, status)
        VALUES (?, ?, ?, ?, 'running')
    """, (directory, now, int(quality), delete_originals))
    conn.commit()
    job_id = cursor.lastrowid
    conn.close()
    return job_id


def record_file(job_id, filepath, filename, status, category=None, title=None,
                season=None, original_size_bytes=None, new_size_bytes=None,
                original_codec=None, quality=None, duration_seconds=None,
                failure_reason=None):
    """Record a per-file transcode result."""
    _init_schema()
    conn = _get_connection()
    now = datetime.now(timezone.utc).isoformat()

    space_saved = None
    if original_size_bytes is not None and new_size_bytes is not None:
        space_saved = original_size_bytes - new_size_bytes

    conn.execute("""
        INSERT INTO transcode_files
            (job_id, filepath, filename, category, title, season,
             original_size_bytes, new_size_bytes, space_saved_bytes,
             original_codec, quality, duration_seconds, status,
             failure_reason, transcoded_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (job_id, filepath, filename, category, title, season,
          original_size_bytes, new_size_bytes, space_saved,
          original_codec, quality, duration_seconds, status,
          failure_reason, now))
    conn.commit()
    conn.close()


def complete_job(job_id, total_files, successful_count, failed_count, skipped_count,
                 total_original_bytes, total_new_bytes):
    """Mark a job as complete with summary stats."""
    _init_schema()
    conn = _get_connection()
    now = datetime.now(timezone.utc).isoformat()
    space_saved = total_original_bytes - total_new_bytes

    status = "success" if failed_count == 0 else "completed_with_failures"

    conn.execute("""
        UPDATE transcode_jobs
        SET completed_at = ?, total_files = ?, successful_count = ?,
            failed_count = ?, skipped_count = ?, total_original_bytes = ?,
            total_new_bytes = ?, total_space_saved_bytes = ?, status = ?
        WHERE id = ?
    """, (now, total_files, successful_count, failed_count, skipped_count,
          total_original_bytes, total_new_bytes, space_saved, status, job_id))
    conn.commit()
    conn.close()


# --- Query functions (called from flaskapp.py) ---

def get_job_history(limit=20):
    """Return the most recent transcode jobs."""
    _init_schema()
    if not os.path.exists(DB_PATH):
        return []
    conn = _get_connection()
    rows = conn.execute("""
        SELECT id, directory, started_at, completed_at, total_files,
               successful_count, failed_count, skipped_count,
               total_original_bytes, total_new_bytes, total_space_saved_bytes,
               quality, delete_originals, status
        FROM transcode_jobs
        ORDER BY id DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_job_files(job_id):
    """Return all file records for a specific job."""
    _init_schema()
    if not os.path.exists(DB_PATH):
        return []
    conn = _get_connection()
    rows = conn.execute("""
        SELECT filepath, filename, category, title, season,
               original_size_bytes, new_size_bytes, space_saved_bytes,
               original_codec, quality, duration_seconds, status,
               failure_reason, transcoded_at
        FROM transcode_files
        WHERE job_id = ?
        ORDER BY id ASC
    """, (job_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_lifetime_stats():
    """Return all-time transcode statistics."""
    _init_schema()
    if not os.path.exists(DB_PATH):
        return {}
    conn = _get_connection()
    row = conn.execute("""
        SELECT
            COUNT(*) as total_jobs,
            COALESCE(SUM(successful_count), 0) as total_files_transcoded,
            COALESCE(SUM(failed_count), 0) as total_files_failed,
            COALESCE(SUM(skipped_count), 0) as total_files_skipped,
            COALESCE(SUM(total_original_bytes), 0) as total_original_bytes,
            COALESCE(SUM(total_new_bytes), 0) as total_new_bytes,
            COALESCE(SUM(total_space_saved_bytes), 0) as total_space_saved_bytes
        FROM transcode_jobs
        WHERE status != 'running'
    """).fetchone()
    conn.close()

    if not row or row["total_jobs"] == 0:
        return {}

    total_original = row["total_original_bytes"]
    total_new = row["total_new_bytes"]
    avg_compression = round((1 - total_new / total_original) * 100, 1) if total_original > 0 else 0

    return {
        "total_jobs": row["total_jobs"],
        "total_files_transcoded": row["total_files_transcoded"],
        "total_files_failed": row["total_files_failed"],
        "total_files_skipped": row["total_files_skipped"],
        "total_original_gb": round(total_original / (1024**3), 2),
        "total_new_gb": round(total_new / (1024**3), 2),
        "total_space_saved_gb": round(row["total_space_saved_bytes"] / (1024**3), 2),
        "avg_compression_pct": avg_compression,
    }
