"""
Scheduler module for x265transcoder.

Manages scheduled transcoding jobs, configuration time windows,
queue reordering, and transcode duration/capacity estimation.
Persists data to SQLite at /config/media.db.
"""

import os
import sqlite3
import threading
import logging
from datetime import datetime, time, timedelta, timezone

DB_PATH = "/config/media.db"
logger = logging.getLogger(__name__)

_schema_initialised = False
_schema_lock = threading.Lock()


def _get_connection():
    """Get a SQLite connection with a busy timeout for concurrent access."""
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_scheduler_schema():
    """Ensure the scheduler database schema exists. Thread-safe."""
    global _schema_initialised
    if _schema_initialised:
        return
    with _schema_lock:
        if _schema_initialised:
            return
        os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
        conn = sqlite3.connect(DB_PATH, timeout=30)
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS schedule_config (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                enabled INTEGER DEFAULT 0,
                start_time TEXT DEFAULT '22:00',
                end_time TEXT DEFAULT '06:00',
                quality INTEGER DEFAULT 23,
                delete_originals TEXT DEFAULT 'Yes',
                stop_after_current INTEGER DEFAULT 1
            );

            INSERT OR IGNORE INTO schedule_config (id, enabled, start_time, end_time, quality, delete_originals, stop_after_current)
            VALUES (1, 0, '22:00', '06:00', 23, 'Yes', 1);

            CREATE TABLE IF NOT EXISTS scheduled_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                directory TEXT NOT NULL,
                item_name TEXT NOT NULL,
                category TEXT NOT NULL,
                season TEXT,
                quality INTEGER DEFAULT 23,
                delete_originals TEXT DEFAULT 'Yes',
                status TEXT DEFAULT 'queued',
                total_files INTEGER DEFAULT 0,
                estimated_size_bytes INTEGER DEFAULT 0,
                estimated_duration_seconds REAL DEFAULT 0,
                position INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                started_at TEXT,
                completed_at TEXT,
                error_message TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_sched_jobs_status ON scheduled_jobs(status);
            CREATE INDEX IF NOT EXISTS idx_sched_jobs_position ON scheduled_jobs(position);
        """)
        conn.close()
        _schema_initialised = True


# --- Configuration Functions ---

def get_schedule_config():
    """Return the current scheduler configuration as a dict."""
    init_scheduler_schema()
    conn = _get_connection()
    row = conn.execute("SELECT * FROM schedule_config WHERE id = 1").fetchone()
    conn.close()
    if row:
        return {
            "enabled": bool(row["enabled"]),
            "start_time": row["start_time"] or "22:00",
            "end_time": row["end_time"] or "06:00",
            "quality": row["quality"] or 23,
            "delete_originals": row["delete_originals"] or "Yes",
            "stop_after_current": bool(row["stop_after_current"]),
        }
    return {
        "enabled": False,
        "start_time": "22:00",
        "end_time": "06:00",
        "quality": 23,
        "delete_originals": "Yes",
        "stop_after_current": True,
    }


def save_schedule_config(enabled, start_time, end_time, quality=23, delete_originals="Yes", stop_after_current=True):
    """Save updated scheduler configuration."""
    init_scheduler_schema()
    conn = _get_connection()
    conn.execute("""
        UPDATE schedule_config
        SET enabled = ?, start_time = ?, end_time = ?, quality = ?, delete_originals = ?, stop_after_current = ?
        WHERE id = 1
    """, (1 if enabled else 0, str(start_time), str(end_time), int(quality), str(delete_originals), 1 if stop_after_current else 0))
    conn.commit()
    conn.close()


# --- Queue Management Functions ---

def get_scheduled_queue():
    """Return all queued jobs ordered by position ascending, then id ascending."""
    init_scheduler_schema()
    conn = _get_connection()
    rows = conn.execute("""
        SELECT * FROM scheduled_jobs
        WHERE status IN ('queued', 'running', 'paused')
        ORDER BY position ASC, id ASC
    """, ()).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_scheduled_jobs(limit=50):
    """Return all jobs (queued, running, completed, failed) with limit."""
    init_scheduler_schema()
    conn = _get_connection()
    rows = conn.execute("""
        SELECT * FROM scheduled_jobs
        ORDER BY id DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def add_to_queue(directory, item_name, category, season=None, quality=None, delete_originals=None, total_files=0, estimated_size_bytes=0):
    """Add a new item to the end of the scheduled queue."""
    init_scheduler_schema()
    cfg = get_schedule_config()
    if quality is None:
        quality = cfg.get("quality", 23)
    if delete_originals is None:
        delete_originals = cfg.get("delete_originals", "Yes")

    conn = _get_connection()
    pos_row = conn.execute("SELECT MAX(position) as max_pos FROM scheduled_jobs WHERE status IN ('queued', 'running', 'paused')").fetchone()
    next_pos = (pos_row["max_pos"] or 0) + 1 if pos_row and pos_row["max_pos"] is not None else 1

    now = datetime.now(timezone.utc).isoformat()
    cursor = conn.execute("""
        INSERT INTO scheduled_jobs
            (directory, item_name, category, season, quality, delete_originals, status,
             total_files, estimated_size_bytes, position, created_at)
        VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?)
    """, (directory, item_name, category, season, int(quality), delete_originals,
          int(total_files), int(estimated_size_bytes), next_pos, now))
    conn.commit()
    job_id = cursor.lastrowid
    conn.close()
    logger.info(f"Added job #{job_id} ({item_name}) to scheduler queue at position {next_pos}")
    return job_id


def remove_from_queue(job_id):
    """Remove a job from the queue (or delete record if not running)."""
    init_scheduler_schema()
    conn = _get_connection()
    conn.execute("DELETE FROM scheduled_jobs WHERE id = ?", (int(job_id),))
    conn.commit()
    conn.close()
    logger.info(f"Removed job #{job_id} from scheduler queue")


def move_queue_item(job_id, direction):
    """
    Move a queued job up or down in order.
    direction: 'up' or 'down'
    """
    init_scheduler_schema()
    queue = get_scheduled_queue()
    ids = [j["id"] for j in queue]
    if job_id not in ids:
        return

    idx = ids.index(job_id)
    if direction == "up" and idx > 0:
        ids[idx], ids[idx - 1] = ids[idx - 1], ids[idx]
    elif direction == "down" and idx < len(ids) - 1:
        ids[idx], ids[idx + 1] = ids[idx + 1], ids[idx]
    else:
        return

    conn = _get_connection()
    for new_pos, jid in enumerate(ids, start=1):
        conn.execute("UPDATE scheduled_jobs SET position = ? WHERE id = ?", (new_pos, jid))
    conn.commit()
    conn.close()


def clear_completed_jobs():
    """Remove completed or failed jobs from the scheduled_jobs table."""
    init_scheduler_schema()
    conn = _get_connection()
    conn.execute("DELETE FROM scheduled_jobs WHERE status IN ('completed', 'failed')")
    conn.commit()
    conn.close()


def get_next_queued_job():
    """Return the next job ready to run (status == 'queued' or 'paused')."""
    init_scheduler_schema()
    conn = _get_connection()
    row = conn.execute("""
        SELECT * FROM scheduled_jobs
        WHERE status IN ('queued', 'paused')
        ORDER BY position ASC, id ASC
        LIMIT 1
    """).fetchone()
    conn.close()
    return dict(row) if row else None


def mark_job_running(job_id):
    """Mark a scheduled job as running."""
    init_scheduler_schema()
    now = datetime.now(timezone.utc).isoformat()
    conn = _get_connection()
    conn.execute("""
        UPDATE scheduled_jobs
        SET status = 'running', started_at = ?
        WHERE id = ?
    """, (now, int(job_id)))
    conn.commit()
    conn.close()


def mark_job_completed(job_id):
    """Mark a scheduled job as completed."""
    init_scheduler_schema()
    now = datetime.now(timezone.utc).isoformat()
    conn = _get_connection()
    conn.execute("""
        UPDATE scheduled_jobs
        SET status = 'completed', completed_at = ?
        WHERE id = ?
    """, (now, int(job_id)))
    conn.commit()
    conn.close()


def mark_job_queued(job_id):
    """
    Reset a scheduled job back to 'queued' status so it can resume in the next window.
    """
    init_scheduler_schema()
    conn = _get_connection()
    conn.execute("""
        UPDATE scheduled_jobs
        SET status = 'queued'
        WHERE id = ?
    """, (int(job_id),))
    conn.commit()
    conn.close()


def mark_job_failed(job_id, error_message=None):
    """Mark a scheduled job as failed."""
    init_scheduler_schema()
    now = datetime.now(timezone.utc).isoformat()
    conn = _get_connection()
    conn.execute("""
        UPDATE scheduled_jobs
        SET status = 'failed', completed_at = ?, error_message = ?
        WHERE id = ?
    """, (now, str(error_message) if error_message else None, int(job_id)))
    conn.commit()
    conn.close()


# --- Window & Timing Calculations ---

def _parse_time_str(time_str):
    """Parse 'HH:MM' string into a datetime.time object."""
    try:
        parts = time_str.strip().split(":")
        return time(int(parts[0]), int(parts[1]))
    except Exception:
        return time(22, 0)


def is_in_schedule_window(now_dt=None):
    """
    Check whether the specified datetime (or now) falls inside the scheduled window.
    Returns True if schedule is enabled and current time is in window.
    """
    cfg = get_schedule_config()
    if not cfg["enabled"]:
        return False

    if now_dt is None:
        now_dt = datetime.now()

    current_t = now_dt.time()
    start_t = _parse_time_str(cfg["start_time"])
    end_t = _parse_time_str(cfg["end_time"])

    if start_t <= end_t:
        # Same-day window (e.g. 02:00 to 06:00)
        return start_t <= current_t < end_t
    else:
        # Overnight window (e.g. 22:00 to 06:00)
        return current_t >= start_t or current_t < end_t


def get_window_info(now_dt=None):
    """
    Calculate full window metrics:
    - is_active: bool
    - start_time: 'HH:MM'
    - end_time: 'HH:MM'
    - total_window_seconds: float
    - remaining_seconds_in_window: float
    - seconds_until_next_window: float
    - status_text: str
    """
    cfg = get_schedule_config()
    if now_dt is None:
        now_dt = datetime.now()

    start_t = _parse_time_str(cfg["start_time"])
    end_t = _parse_time_str(cfg["end_time"])

    today = now_dt.date()
    dt_start = datetime.combine(today, start_t)
    if start_t <= end_t:
        dt_end = datetime.combine(today, end_t)
    else:
        dt_end = datetime.combine(today + timedelta(days=1), end_t)

    total_window_seconds = max(0, (dt_end - dt_start).total_seconds())
    is_active = is_in_schedule_window(now_dt)

    remaining_seconds = 0
    seconds_until_next = 0

    if is_active:
        if start_t <= end_t:
            current_window_end = datetime.combine(today, end_t)
        else:
            if now_dt.time() >= start_t:
                current_window_end = datetime.combine(today + timedelta(days=1), end_t)
            else:
                current_window_end = datetime.combine(today, end_t)

        remaining_seconds = max(0, (current_window_end - now_dt).total_seconds())
        rem_hrs = int(remaining_seconds // 3600)
        rem_mins = int((remaining_seconds % 3600) // 60)
        status_text = f"Window Active (ends in {rem_hrs}h {rem_mins}m)"
    else:
        if now_dt.time() < start_t:
            next_window_start = datetime.combine(today, start_t)
        else:
            next_window_start = datetime.combine(today + timedelta(days=1), start_t)

        seconds_until_next = max(0, (next_window_start - now_dt).total_seconds())
        wait_hrs = int(seconds_until_next // 3600)
        wait_mins = int((seconds_until_next % 3600) // 60)
        if cfg["enabled"]:
            status_text = f"Next window starts in {wait_hrs}h {wait_mins}m ({cfg['start_time']} - {cfg['end_time']})"
        else:
            status_text = "Scheduler Disabled"

    return {
        "enabled": cfg["enabled"],
        "is_active": is_active,
        "start_time": cfg["start_time"],
        "end_time": cfg["end_time"],
        "quality": cfg.get("quality", 23),
        "delete_originals": cfg.get("delete_originals", "Yes"),
        "total_window_seconds": total_window_seconds,
        "remaining_seconds_in_window": remaining_seconds,
        "seconds_until_next_window": seconds_until_next,
        "status_text": status_text,
    }


# --- Transcoding Speed & Capacity Estimation ---

def estimate_transcode_speed_bps():
    """
    Estimate transcode throughput in bytes per second.
    Queries successful jobs from transcode_files in SQLite.
    Falls back to ~1.25 MB/s (~4.5 GB/hour) if no records exist.
    """
    init_scheduler_schema()
    try:
        conn = _get_connection()
        row = conn.execute("""
            SELECT SUM(original_size_bytes) as total_bytes, SUM(duration_seconds) as total_secs
            FROM transcode_files
            WHERE status = 'success' AND duration_seconds > 0 AND original_size_bytes > 0
        """).fetchone()
        conn.close()

        if row and row["total_secs"] and row["total_secs"] > 60 and row["total_bytes"]:
            bps = float(row["total_bytes"]) / float(row["total_secs"])
            return max(200 * 1024, min(bps, 25 * 1024 * 1024))
    except Exception as e:
        logger.warning(f"Could not calculate historical transcode speed: {e}")

    # Fallback estimate: 1.25 MB/s (approx 4.5 GB per hour)
    return 1.25 * 1024 * 1024


def get_queue_estimates():
    """
    Compute capacity estimates for the upcoming or active window and determine
    which items in the queue are estimated to fit.
    """
    win_info = get_window_info()
    speed_bps = estimate_transcode_speed_bps()
    speed_gb_hr = round((speed_bps * 3600) / (1024**3), 2)

    available_seconds = (
        win_info["remaining_seconds_in_window"]
        if win_info["is_active"]
        else win_info["total_window_seconds"]
    )
    if available_seconds <= 0:
        available_seconds = win_info["total_window_seconds"]

    capacity_bytes = available_seconds * speed_bps
    capacity_gb = round(capacity_bytes / (1024**3), 2)

    queue = get_scheduled_queue()
    accumulated_duration_sec = 0.0
    items_fitting_count = 0
    annotated_queue = []

    for item in queue:
        item_dict = dict(item)
        size_bytes = item_dict.get("estimated_size_bytes") or 0
        if size_bytes <= 0 and item_dict.get("directory") and os.path.exists(item_dict["directory"]):
            try:
                for root, _, files in os.walk(item_dict["directory"]):
                    for f in files:
                        fp = os.path.join(root, f)
                        if not os.path.islink(fp):
                            size_bytes += os.path.getsize(fp)
                item_dict["estimated_size_bytes"] = size_bytes
            except Exception:
                size_bytes = 4 * (1024**3)
        elif size_bytes <= 0:
            size_bytes = 4 * (1024**3)

        est_duration = size_bytes / speed_bps
        item_dict["estimated_duration_seconds"] = est_duration

        hrs = int(est_duration // 3600)
        mins = int((est_duration % 3600) // 60)
        item_dict["estimated_duration_formatted"] = f"{hrs}h {mins}m" if hrs > 0 else f"{mins}m"
        item_dict["size_gb"] = round(size_bytes / (1024**3), 2)

        accumulated_duration_sec += est_duration
        if accumulated_duration_sec <= available_seconds:
            item_dict["will_fit"] = True
            items_fitting_count += 1
        else:
            item_dict["will_fit"] = False

        annotated_queue.append(item_dict)

    total_count = len(annotated_queue)
    win_hrs = int(available_seconds // 3600)
    win_mins = int((available_seconds % 3600) // 60)
    win_dur_str = f"{win_hrs}h {win_mins}m"

    if total_count == 0:
        summary_text = f"Queue is empty. Capacity in upcoming window is approx {capacity_gb} GB ({win_dur_str})."
    elif items_fitting_count == total_count:
        summary_text = f"All {total_count} items in queue are estimated to fit within the upcoming window ({win_dur_str})."
    elif items_fitting_count > 0:
        overflow = total_count - items_fitting_count
        summary_text = f"Estimated to fit {items_fitting_count} of {total_count} items in the upcoming window ({win_dur_str}). {overflow} item{'s' if overflow > 1 else ''} will carry over to the next window."
    else:
        summary_text = f"The first item in the queue exceeds the available window capacity ({win_dur_str}). It will begin in this window and complete in the next."

    return {
        "speed_bps": speed_bps,
        "speed_gb_hr": speed_gb_hr,
        "available_seconds": available_seconds,
        "capacity_gb": capacity_gb,
        "items_fitting_count": items_fitting_count,
        "total_items_count": total_count,
        "summary_text": summary_text,
        "queue": annotated_queue,
        "window_info": win_info,
    }


# --- Display Helpers for Transcode Tab & API ---

def get_now_and_upcoming_display(job_directory=None):
    """
    Return current transcoding title and upcoming items for display in the Transcode tab.
    """
    queue = get_scheduled_queue()
    now_transcoding = None
    upcoming_items = []

    running_job = None
    for item in queue:
        if item["status"] == "running":
            running_job = item
            break

    if running_job:
        now_transcoding = running_job["item_name"]
        for item in queue:
            if item["id"] != running_job["id"]:
                size_gb = round((item["estimated_size_bytes"] or 0) / (1024**3), 2)
                upcoming_items.append({
                    "id": item["id"],
                    "name": item["item_name"],
                    "category": item["category"],
                    "size": f"{size_gb} GB" if size_gb > 0 else "",
                    "directory": item["directory"],
                })
    elif queue:
        if job_directory:
            now_transcoding = os.path.basename(job_directory.rstrip("/\\"))
        upcoming_items = []
        for item in queue:
            size_gb = round((item["estimated_size_bytes"] or 0) / (1024**3), 2)
            upcoming_items.append({
                "id": item["id"],
                "name": item["item_name"],
                "category": item["category"],
                "size": f"{size_gb} GB" if size_gb > 0 else "",
                "directory": item["directory"],
            })
    elif job_directory:
        now_transcoding = os.path.basename(job_directory.rstrip("/\\"))

    return {
        "now_transcoding": now_transcoding,
        "upcoming_items": upcoming_items,
        "is_scheduled": bool(running_job),
    }
