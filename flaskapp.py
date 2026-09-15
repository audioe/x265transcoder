from flask import Flask, render_template, request, jsonify, redirect, url_for
import subprocess
import os
import shutil
import yaml
import logging
import threading
from apscheduler.schedulers.background import BackgroundScheduler
from modules.scanner import run_scan, get_recommendations, get_scan_status, get_scan_history
from modules.history import get_job_history, get_job_files, get_lifetime_stats
from modules.scheduler import (
    get_schedule_config,
    save_schedule_config,
    get_scheduled_queue,
    get_all_scheduled_jobs,
    add_to_queue,
    remove_from_queue,
    move_queue_item,
    clear_completed_jobs,
    get_next_queued_job,
    mark_job_running,
    is_in_schedule_window,
    get_queue_estimates,
    get_now_and_upcoming_display,
)

app = Flask(__name__)

# Configure logging for the scanner scheduler
logging.basicConfig(level=logging.INFO)
scheduler_logger = logging.getLogger("scanner_scheduler")

# Read the version number from the file
with open('version.txt', 'r') as f:
    version = f.read().strip()

# Load the /config/job.yaml file
job_directory = ''
job_progress = ''
file_progress = ''
if os.path.exists('/config/job.yaml'):
    with open('/config/job.yaml', 'r') as f:
        job_config = yaml.safe_load(f)
        job_directory = job_config.get('job_directory', '')
        job_progress = job_config.get('job_progress', '')
        file_progress = job_config.get('file_progress', '')

def load_config():
    global config
    # Load the configuration file
    if os.path.exists('/config/config.yaml'):
        with open('/config/config.yaml', 'r') as f:
            config = yaml.safe_load(f)
    else:
        return redirect(url_for('setup'))

# Function to get list of directories
def get_directories(parent_dir, directories=None):
    if directories is None:
        directories = []

    entries = os.listdir(parent_dir)
    entries.sort()  # Sort the entries alphabetically

    for entry in entries:
        entry_path = os.path.join(parent_dir, entry)
        if os.path.isdir(entry_path):
            subdirectories = get_directories(entry_path, [])
            subdirectories.sort(key=lambda d: d['name'])  # Sort subdirectories alphabetically

            directories.append({
                'name': entry,
                'path': entry_path,
                'subdirectories': subdirectories
            })

    directories.sort(key=lambda d: d['name'])  # Sort top-level directories alphabetically
    return directories

def get_directory_size(path):
    total_size_in_bytes = 0
    try:
        for dirpath, dirnames, filenames in os.walk(path):
            for filename in filenames:
                file_path = os.path.join(dirpath, filename)
                try:
                    total_size_in_bytes += os.path.getsize(file_path)
                except OSError as e:
                    print(f"Error accessing file {file_path}: {e}")
            for dirname in dirnames:
                dir_path = os.path.join(dirpath, dirname)
                print(f"Scanning directory: {dir_path}")
    except OSError as e:
        print(f"Error accessing directory {path}: {e}")
    
    total_size = round(total_size_in_bytes / (1024*1024*1024), 2)
    return total_size

# Function to get secrets
@app.route('/get_secret/<string:secret_name>')
def get_secret(secret_name):
    try:
        secret_value = config['secrets'][secret_name]
        return secret_value
    except KeyError:
        return jsonify({'error': 'Secret not found'}), 404

# Function to determine if a Transcode job is already running
def transcode_check(keyword):
    try:
        output = subprocess.check_output(["ps", "aux"], text=True)
        lines = output.splitlines()
        for line in lines:
            if keyword in line:
                return True
        return False
    except subprocess.CalledProcessError:
        return False
    
def update_progress_yaml(item, progress):
    """Updates the progress in a YAML file.
    Args:
        progress: The progress percentage.
    """
    filename = "/config/job.yaml"
    with open(filename, 'r') as f:
        data = yaml.safe_load(f)
    data[item] = f"{progress}"
    with open(filename, 'w') as f:
        yaml.dump(data, f, default_flow_style=False)

# Function to store the directory that will be transcoded to /config/job.yaml
def store_job(job_data):
    filename = "/config/job.yaml"
    try:
        with open(filename, 'r') as f:
            data = yaml.safe_load(f)
        data['job_directory'] = f"{job_data}"
        data['progress'] = "0"
        with open(filename, 'w') as f:
            yaml.dump(data, f, default_flow_style=False)
    except FileNotFoundError:
        # Create the YAML file if it doesn't exist
        data = {'job_directory': job_data}
        data = {'progress': "0"}
        with open(filename, 'w') as f:
            yaml.dump(data, f, default_flow_style=False)


#def store_job(job_data):
#    with open('job.config', 'a') as f:
#        # Append the job data to the file
#        f.write(job_data)


# Route to render the HTML page
@app.route('/')
def index():
    load_config()
    transcoder_status = transcode_check('x265transcoder.py')
    job_directory = ''
    job_progress = ''
    file_progress = ''
    current_file_number = ''
    current_file = ''
    total_files = ''
    eta = ''
    if transcoder_status == True:
        with open('/config/job.yaml', 'r') as f:
            job_config = yaml.safe_load(f)
            # Guard against empty/corrupt YAML (returns None during concurrent writes)
            if job_config is None:
                job_config = {}
            job_directory = job_config.get('job_directory', '')
            job_progress = job_config.get('job_progress', '')
            file_progress = job_config.get('file_progress', '')
            eta = job_config.get('eta', '')
            try:
                current_file_number = job_config.get('current_file_number', '')
            except:
                current_file_number = "Loading..."
                pass
            try:
                current_file = job_config.get('current_file', '')
            except:
                current_file = "Loading..."
                pass
            try:
                total_files = job_config.get('total_files', '')
            except:
                total_files = "Loading..."
                pass

    # Get library summary stats for the dashboard (when idle)
    dashboard_stats = get_recommendations(limit=0).get('stats', {}) if not transcoder_status else {}

    # Get last job directory for restart capability (when idle)
    last_job_directory = ''
    if not transcoder_status and os.path.exists('/config/job.yaml'):
        try:
            with open('/config/job.yaml', 'r') as f:
                last_job_config = yaml.safe_load(f)
                if last_job_config:
                    last_job_directory = last_job_config.get('job_directory', '')
        except:
            pass

    # Get free space of /films
    films_free_gb = 0
    if os.path.exists('/films'):
        try:
            total, used, free = shutil.disk_usage('/films')
            films_free_gb = free // (2**30)
        except:
            pass

    # Get queue display info for scheduled jobs
    display_info = get_now_and_upcoming_display(job_directory if transcoder_status else None)
    now_transcoding = display_info.get("now_transcoding")
    upcoming_items = display_info.get("upcoming_items", [])
    is_scheduled_job = display_info.get("is_scheduled", False)

    return render_template('index.html', version=version, os=os, config=config,
                           transcoder_status=transcoder_status, job_directory=job_directory,
                           job_progress=job_progress, file_progress=file_progress,
                           current_file_number=current_file_number, current_file=current_file,
                           total_files=total_files, eta=eta, dashboard_stats=dashboard_stats,
                           films_free_gb=films_free_gb,
                           last_job_directory=last_job_directory,
                           now_transcoding=now_transcoding,
                           upcoming_items=upcoming_items,
                           is_scheduled_job=is_scheduled_job,
                           active_page='home')

@app.route('/setup', methods=['GET', 'POST'])
def setup():
    if request.method == 'POST':
        use_telegram = 'use_telegram' in request.form
        telegram_chat_id = request.form.get('telegram_chat_id', '')
        telegram_token = request.form.get('telegram_token', '')
        shows_directory = request.form.get('shows_directory', '')
        films_directory = request.form.get('films_directory', '')

        config = {
            'secrets': {
                'TELEGRAM_TOKEN': telegram_token if use_telegram else '',
                'TELEGRAM_CHATID': telegram_chat_id if use_telegram else ''
            },
            'libraries': {
                'shows': shows_directory,
                'films': films_directory
            },
            'encoder': request.form.get('encoder', 'auto')
        }

        with open('/config/config.yaml', 'w') as f:
            yaml.dump(config, f)

        return redirect(url_for('index'))

    # Load existing config if it exists
    config = {}
    if os.path.exists('/config/config.yaml'):
        with open('/config/config.yaml', 'r') as f:
            config = yaml.safe_load(f)

    # Set default values, using existing config if available
    default_shows = config.get('libraries', {}).get('shows', '/shows')
    default_films = config.get('libraries', {}).get('films', '/films')
    use_telegram = bool(config.get('secrets', {}).get('TELEGRAM_TOKEN'))
    telegram_chat_id = config.get('secrets', {}).get('TELEGRAM_CHATID', '')
    telegram_token = config.get('secrets', {}).get('TELEGRAM_TOKEN', '')
    current_encoder = config.get('encoder', 'auto')

    return render_template('setup.html', 
                           default_shows=default_shows, 
                           default_films=default_films,
                           use_telegram=use_telegram,
                           telegram_chat_id=telegram_chat_id,
                           telegram_token=telegram_token,
                           current_encoder=current_encoder,
                           version=version)

# Route to handle loading directories
@app.route('/load_directories', methods=['POST'])
def load_directories():
    load_config()
    parent_dir = request.form.get('parent_dir', '/shows')
    is_films = parent_dir == config['libraries']['films']

    if is_films:
        # If the selected parent directory is for films, render the final form directly
        subdirectories = sorted([{
            'name': entry,
            'path': os.path.join(parent_dir, entry),
            'size': str(get_directory_size(os.path.join(parent_dir, entry))) + " GB"
        } for entry in os.listdir(parent_dir) if os.path.isdir(os.path.join(parent_dir, entry))], key=lambda x: x['name'].lower())
        html = render_template('index.html', subdirectories=subdirectories, version=version, os=os, current_dir=parent_dir, parent_dir=parent_dir, config=config, films='films', active_page='home')
    else:
        # If the selected parent directory is for shows, render the folder selection form
        directories = sorted([{
            'name': entry,
            'path': os.path.join(parent_dir, entry),
            'size': str(get_directory_size(os.path.join(parent_dir, entry))) + " GB"
        } for entry in os.listdir(parent_dir) if os.path.isdir(os.path.join(parent_dir, entry))], key=lambda x: x['name'].lower())
        html = render_template('index.html', directories=directories, version=version, os=os, config=config, active_page='home')
    return html

# Route to handle loading subdirectories (for TV shows)
@app.route('/load_subdirectories', methods=['POST'])
def load_subdirectories():
    load_config()
    parent_dir = request.form.get('parent_dir')
    current_dir = request.form.get('folder')

    if current_dir:
        subdirectories = sorted([{'name': entry, 'path': os.path.join(current_dir, entry), 'size': str(get_directory_size(os.path.join(current_dir, entry))) + " GB"}
                                 for entry in os.listdir(current_dir)
                                 if os.path.isdir(os.path.join(current_dir, entry))], key=lambda x: x['name'].lower())
        html = render_template('index.html', subdirectories=subdirectories, version=version, os=os, current_dir=current_dir, parent_dir=parent_dir, config=config, shows='shows', active_page='home')
    else:
        directories = sorted([{'name': entry, 'path': os.path.join(parent_dir, entry), 'size': str(get_directory_size(os.path.join(parent_dir, entry))) + " GB"}
                              for entry in os.listdir(parent_dir)
                              if os.path.isdir(os.path.join(parent_dir, entry))], key=lambda x: x['name'].lower())
        html = render_template('index.html', directories=directories, version=version, os=os, current_dir=parent_dir, parent_dir=parent_dir, config=config, active_page='home')

    return html

# Route to handle running the Transcoder
@app.route("/run", methods=["POST"])
def run():
    load_config()
    if request.method == 'POST':
        folder = str(request.form['folder'])
        include = request.form['include']
        quality = request.form['quality']
        # Get delete toggle value.  Use 'No' as the default value if 'delete' is not present
        delete = request.form.get('delete', 'No')
        # Get Telegram secrets
        telegram_token = get_secret("TELEGRAM_TOKEN")
        telegram_chatid = get_secret("TELEGRAM_CHATID")
        # Store the job data in job.config
        store_job(folder)
        # Call the x265transcoder.py script and pass the variables
        subprocess.Popen(['python', 'x265transcoder.py', folder, include, quality, delete, str(telegram_token), str(telegram_chatid), version])
        update_progress_yaml("job_progress", 0)
        update_progress_yaml("file_progress", 0)
        return redirect(url_for('index'))


# Route to run a transcode job directly from the recommendations page
@app.route("/run_from_recommendations", methods=["POST"])
def run_from_recommendations():
    load_config()
    # Check if a job is already running
    if transcode_check('x265transcoder.py'):
        return redirect(url_for('recommendations'))

    folder = str(request.form['folder'])
    include = request.form.get('include', '.mkv')
    quality = request.form.get('quality', '23')
    delete = request.form.get('delete', 'Yes')

    # Get Telegram secrets
    telegram_token = get_secret("TELEGRAM_TOKEN")
    telegram_chatid = get_secret("TELEGRAM_CHATID")

    # Store the job data
    store_job(folder)

    # Launch the transcoder
    subprocess.Popen(['python', 'x265transcoder.py', folder, include, quality, delete,
                      str(telegram_token), str(telegram_chatid), version])
    update_progress_yaml("job_progress", 0)
    update_progress_yaml("file_progress", 0)

    return redirect(url_for('index'))


# Function to clean up partially completed transcode files in a directory
def cleanup_interrupted_transcodes(directory):
    """
    Restore _old files and remove partial x265 outputs.
    
    When a transcode is interrupted:
    - Original file was renamed: movie.mkv -> movie.mkv_old
    - Partial output may exist: movie.mkv (or movie_x265.mkv if name contained "264")
    
    This function:
    1. Finds all *_old files
    2. Determines what the output filename would have been
    3. Deletes the partial output (if it exists)
    4. Renames the _old file back to its original name
    
    Returns a count of files restored.
    """
    restored = 0
    for dirpath, _, filenames in os.walk(directory):
        for filename in filenames:
            if not filename.endswith("_old"):
                continue

            old_filepath = os.path.join(dirpath, filename)
            # Original filename is the _old file without the _old suffix
            original_filename = filename[:-4]  # strip "_old"
            original_filepath = os.path.join(dirpath, original_filename)

            # Determine what the output file would have been called
            if "264" in original_filename:
                output_filename = original_filename.replace('264', '265')
            else:
                output_filename = original_filename
            output_filepath = os.path.join(dirpath, output_filename)

            # Delete the partial output file if it exists
            if os.path.exists(output_filepath) and output_filepath != original_filepath:
                try:
                    os.remove(output_filepath)
                except OSError:
                    pass

            # Also delete the original path if it exists and is different from _old
            # (this handles the case where output_filename == original_filename)
            if output_filename == original_filename and os.path.exists(original_filepath):
                try:
                    os.remove(original_filepath)
                except OSError:
                    pass

            # Rename _old file back to original name
            try:
                shutil.move(old_filepath, original_filepath)
                restored += 1
            except OSError:
                pass

    return restored


# Route to restart a previously interrupted job (with cleanup)
@app.route("/restart_job", methods=["POST"])
def restart_job():
    load_config()
    # Check if a job is already running
    if transcode_check('x265transcoder.py'):
        return redirect(url_for('index'))

    folder = str(request.form['folder'])
    include = request.form.get('include', '.mkv')
    quality = request.form.get('quality', '23')
    delete = request.form.get('delete', 'Yes')

    # Clean up any partially transcoded files first
    restored_count = cleanup_interrupted_transcodes(folder)

    # Get Telegram secrets
    telegram_token = get_secret("TELEGRAM_TOKEN")
    telegram_chatid = get_secret("TELEGRAM_CHATID")

    # Store the job data
    store_job(folder)

    # Launch the transcoder
    subprocess.Popen(['python', 'x265transcoder.py', folder, include, quality, delete,
                      str(telegram_token), str(telegram_chatid), version])
    update_progress_yaml("job_progress", 0)
    update_progress_yaml("file_progress", 0)

    return redirect(url_for('index'))

    
# --- Scheduled Scanner ---

def scheduled_scan_job():
    """Run the media scanner as a scheduled job."""
    scheduler_logger.info("Scheduled scan starting...")
    try:
        load_config()
        libraries = config.get('libraries', {})
        run_scan(libraries)
        scheduler_logger.info("Scheduled scan complete.")
    except Exception as e:
        scheduler_logger.error(f"Scheduled scan failed: {e}")


# Initialise the background scheduler (runs daily at 04:00)
scheduler = BackgroundScheduler(daemon=True)
scheduler.add_job(scheduled_scan_job, 'cron', hour=4, minute=0, id='nightly_scan')


# --- Scheduled Transcoder Worker ---

def check_and_run_scheduled_jobs():
    """Background check: if inside schedule window and idle, start next queued job."""
    try:
        # 1. Do not start if a transcode job is already running
        if transcode_check('x265transcoder.py'):
            return

        # 2. Check if inside scheduled time window and scheduler is enabled
        if not is_in_schedule_window():
            return

        # 3. Get next queued job
        next_job = get_next_queued_job()
        if not next_job:
            return

        job_id = next_job['id']
        folder = next_job['directory']
        quality = str(next_job.get('quality', 23))
        delete = next_job.get('delete_originals', 'Yes')
        include = '.mkv'

        load_config()
        telegram_token = get_secret("TELEGRAM_TOKEN")
        telegram_chatid = get_secret("TELEGRAM_CHATID")

        # Mark job running in SQLite database
        mark_job_running(job_id)

        # Store job data in /config/job.yaml
        store_job(folder)
        update_progress_yaml("job_progress", 0)
        update_progress_yaml("file_progress", 0)
        update_progress_yaml("scheduled_job_id", str(job_id))
        update_progress_yaml("scheduled_item_name", next_job['item_name'])

        # Launch x265transcoder with scheduled_job_id as the 8th argument
        subprocess.Popen([
            'python', 'x265transcoder.py',
            folder, include, quality, delete,
            str(telegram_token), str(telegram_chatid), version,
            str(job_id)
        ])
        scheduler_logger.info(f"Started scheduled transcode job #{job_id}: {next_job['item_name']} ({folder})")

    except Exception as e:
        scheduler_logger.error(f"Error checking/starting scheduled job: {e}")


# Run scheduler check every 15 seconds
scheduler.add_job(check_and_run_scheduled_jobs, 'interval', seconds=15, id='transcode_scheduler')
scheduler.start()


# --- Recommendations & Scan Routes ---

@app.route('/recommendations')
def recommendations():
    load_config()
    data = get_recommendations(limit=50)
    scan_status = get_scan_status()
    history = get_scan_history(limit=10)
    transcoder_running = transcode_check('x265transcoder.py')
    return render_template('recommendations.html', version=version, config=config,
                           data=data, scan_status=scan_status, scan_history=history,
                           transcoder_running=transcoder_running, active_page='recommendations')


@app.route('/scan_now', methods=['POST'])
def scan_now():
    """Trigger an immediate media library scan in a background thread."""
    scan_status = get_scan_status()
    if scan_status.get("running"):
        # Already running — return JSON if AJAX, otherwise redirect
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json or 'application/json' in request.headers.get('Accept', ''):
            return jsonify({'status': 'already_running', 'scan_status': scan_status})
        return redirect(url_for('recommendations'))

    load_config()
    libraries = config.get('libraries', {})

    def _run_in_background():
        try:
            run_scan(libraries)
        except Exception as e:
            scheduler_logger.error(f"Manual scan failed: {e}")

    thread = threading.Thread(target=_run_in_background, daemon=True)
    thread.start()

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json or 'application/json' in request.headers.get('Accept', ''):
        return jsonify({'status': 'started'})

    return redirect(url_for('recommendations'))


@app.route('/scan_status')
def scan_status_endpoint():
    """JSON endpoint for polling scan progress."""
    return jsonify(get_scan_status())


@app.route('/job_status')
def job_status_endpoint():
    """JSON endpoint for polling transcode job progress."""
    transcoder_status = transcode_check('x265transcoder.py')
    result = {'running': transcoder_status}
    job_directory = ''
    if transcoder_status:
        try:
            with open('/config/job.yaml', 'r') as f:
                job_config = yaml.safe_load(f)
            if job_config is None:
                job_config = {}
            job_directory = job_config.get('job_directory', '')
            result['job_directory'] = job_directory
            result['job_progress'] = job_config.get('job_progress', '0')
            result['file_progress'] = job_config.get('file_progress', '0')
            result['current_file'] = job_config.get('current_file', '')
            result['current_file_number'] = job_config.get('current_file_number', '')
            result['total_files'] = job_config.get('total_files', '')
            result['eta'] = job_config.get('eta', '')
            result['scheduled_job_id'] = job_config.get('scheduled_job_id', '')
            result['scheduled_item_name'] = job_config.get('scheduled_item_name', '')
        except Exception:
            pass

    display_info = get_now_and_upcoming_display(job_directory if transcoder_status else None)
    result['now_transcoding'] = display_info.get("now_transcoding", "")
    result['upcoming_items'] = display_info.get("upcoming_items", [])
    result['is_scheduled'] = display_info.get("is_scheduled", False)
    return jsonify(result)


# --- Transcode History Routes ---

@app.route('/history')
def history():
    jobs = get_job_history(limit=20)
    stats = get_lifetime_stats()
    return render_template('history.html', version=version, jobs=jobs, stats=stats, active_page='history')


@app.route('/history/<int:job_id>')
def history_detail(job_id):
    jobs = get_job_history(limit=20)
    # Find the specific job summary
    job = None
    for j in jobs:
        if j["id"] == job_id:
            job = j
            break
    files = get_job_files(job_id)
    stats = get_lifetime_stats()
    return render_template('history.html', version=version, jobs=jobs, stats=stats,
                           selected_job=job, selected_files=files, active_page='history')


# --- Logs Routes ---

@app.route('/logs')
def logs():
    load_config()
    log_dir = "/logs"
    log_files = []
    if os.path.exists(log_dir):
        for f in os.listdir(log_dir):
            if f.endswith('.log'):
                path = os.path.join(log_dir, f)
                log_files.append({
                    'name': f,
                    'path': path,
                    'mtime': os.path.getmtime(path),
                    'size': os.path.getsize(path)
                })
        log_files.sort(key=lambda x: x['mtime'], reverse=True)
    return render_template('logs.html', version=version, log_files=log_files, active_page='logs')

@app.route('/api/logs/latest')
def api_logs_latest():
    log_dir = "/logs"
    if not os.path.exists(log_dir):
        return jsonify({'error': 'Log directory not found', 'content': ''})
    
    log_files = [os.path.join(log_dir, f) for f in os.listdir(log_dir) if f.endswith('.log')]
    if not log_files:
        return jsonify({'error': 'No log files found', 'content': ''})
        
    latest_log = max(log_files, key=lambda f: os.path.getmtime(os.path.join(log_dir, f)))
    
    try:
        with open(os.path.join(log_dir, latest_log), 'r', encoding='utf-8') as f:
            lines = f.readlines()
            # Return last 100 lines for live view
            return jsonify({'content': ''.join(lines[-100:])})
    except Exception as e:
        return jsonify({'error': str(e), 'content': ''})

@app.route('/api/logs/view/<log_file>')
def api_logs_view(log_file):
    log_dir = "/logs"
    log_path = os.path.join(log_dir, log_file)
    
    if not os.path.exists(log_path):
        return jsonify({'error': 'Log file not found', 'content': ''})
        
    try:
        with open(log_path, 'r', encoding='utf-8') as f:
            return jsonify({'content': f.read()})
    except Exception as e:
        return jsonify({'error': str(e), 'content': ''})


# --- Scheduler Routes ---

@app.route('/scheduler')
def scheduler_view():
    load_config()
    estimates = get_queue_estimates()
    all_jobs = get_all_scheduled_jobs(limit=20)
    recommendations_data = get_recommendations(limit=100)
    transcoder_running = transcode_check('x265transcoder.py')

    queued_directories = {item['directory'] for item in estimates['queue']}
    sched_cfg = get_schedule_config()

    return render_template(
        'scheduler.html',
        version=version,
        config=config,
        schedule_config=sched_cfg,
        estimates=estimates,
        window_info=estimates['window_info'],
        queue=estimates['queue'],
        all_jobs=all_jobs,
        recommendations=recommendations_data,
        queued_directories=queued_directories,
        transcoder_running=transcoder_running,
        active_page='scheduler'
    )


@app.route('/scheduler/config', methods=['POST'])
def scheduler_save_config_route():
    enabled = request.form.get('enabled') == 'on' or request.form.get('enabled') == '1'
    start_time = request.form.get('start_time', '22:00')
    end_time = request.form.get('end_time', '06:00')
    quality = int(request.form.get('quality', 23))
    delete_originals = request.form.get('delete', 'Yes')
    save_schedule_config(
        enabled=enabled,
        start_time=start_time,
        end_time=end_time,
        quality=quality,
        delete_originals=delete_originals
    )
    return redirect(url_for('scheduler_view'))


@app.route('/scheduler/add', methods=['POST'])
def scheduler_add_job_route():
    directory = request.form.get('directory', '').strip()
    item_name = request.form.get('item_name', '').strip() or os.path.basename(directory.rstrip('/\\'))
    category = request.form.get('category', 'films')
    season = request.form.get('season') or None
    quality = request.form.get('quality')
    delete = request.form.get('delete')
    total_files = int(request.form.get('total_files', 0) or 0)
    estimated_size_bytes = int(request.form.get('estimated_size_bytes', 0) or 0)

    if directory:
        add_to_queue(
            directory=directory,
            item_name=item_name,
            category=category,
            season=season,
            quality=int(quality) if quality else None,
            delete_originals=delete if delete else None,
            total_files=total_files,
            estimated_size_bytes=estimated_size_bytes
        )
    return redirect(url_for('scheduler_view'))


@app.route('/scheduler/remove/<int:job_id>', methods=['POST'])
def scheduler_remove_job_route(job_id):
    remove_from_queue(job_id)
    return redirect(url_for('scheduler_view'))


@app.route('/scheduler/reorder/<int:job_id>/<string:direction>', methods=['POST'])
def scheduler_reorder_job_route(job_id, direction):
    move_queue_item(job_id, direction)
    return redirect(url_for('scheduler_view'))


@app.route('/scheduler/clear_completed', methods=['POST'])
def scheduler_clear_completed_route():
    clear_completed_jobs()
    return redirect(url_for('scheduler_view'))


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', use_reloader=False)
