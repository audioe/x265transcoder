from flask import Flask, render_template, request, jsonify
import subprocess
import os
import yaml

app = Flask(__name__)

# Read the version number from the file
with open('version.txt', 'r') as f:
    version = f.read().strip()

# Load the configuration file
with open('/config/config.yaml', 'r') as f:
    config = yaml.safe_load(f)

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

@app.route('/get_secret/<string:secret_name>')
def get_secret(secret_name):
    try:
        secret_value = config['secrets'][secret_name]
        return secret_value
    except KeyError:
        return jsonify({'error': 'Secret not found'}), 404

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

# Route to render the HTML page
@app.route('/')
def index():
    transcoder_status = transcode_check('ffmpeg')
    return render_template('index.html', version=version, os=os, config=config, transcoder_status=transcoder_status)

@app.route('/load_directories', methods=['POST'])
def load_directories():
    parent_dir = request.form.get('parent_dir', '/shows')
    is_films = parent_dir == config['libraries']['films']

    if is_films:
        # If the selected parent directory is for films, render the final form directly
        subdirectories = sorted([{
            'name': entry,
            'path': os.path.join(parent_dir, entry)
        } for entry in os.listdir(parent_dir) if os.path.isdir(os.path.join(parent_dir, entry))], key=lambda x: x['name'].lower())
        html = render_template('index.html', subdirectories=subdirectories, version=version, os=os, current_dir=parent_dir, parent_dir=parent_dir, config=config, films='films')
    else:
        # If the selected parent directory is for shows, render the folder selection form
        directories = sorted([{'name': entry, 'path': os.path.join(parent_dir, entry)}
                            for entry in os.listdir(parent_dir)
                            if os.path.isdir(os.path.join(parent_dir, entry))], key=lambda x: x['name'].lower())
        html = render_template('index.html', directories=directories, version=version, os=os, config=config)
    return html

@app.route('/load_subdirectories', methods=['POST'])
def load_subdirectories():
    parent_dir = request.form.get('parent_dir')
    current_dir = request.form.get('folder')

    if current_dir:
        subdirectories = sorted([{'name': entry, 'path': os.path.join(current_dir, entry)}
                                 for entry in os.listdir(current_dir)
                                 if os.path.isdir(os.path.join(current_dir, entry))], key=lambda x: x['name'].lower())
        html = render_template('index.html', subdirectories=subdirectories, version=version, os=os, current_dir=current_dir, parent_dir=parent_dir, config=config, shows='shows')
    else:
        directories = sorted([{'name': entry, 'path': os.path.join(parent_dir, entry)}
                              for entry in os.listdir(parent_dir)
                              if os.path.isdir(os.path.join(parent_dir, entry))], key=lambda x: x['name'].lower())
        html = render_template('index.html', directories=directories, version=version, os=os, current_dir=parent_dir, parent_dir=parent_dir, config=config)

    return html

@app.route("/run", methods=["POST"])
def run():
    if request.method == 'POST':
        folder = str(request.form['folder'])
        include = request.form['include']
        quality = request.form['quality']
        # Get delete toggle value.  Use 'No' as the default value if 'delete' is not present
        delete = request.form.get('delete', 'No')
        # Get Telegram secrets
        telegram_token = get_secret("TELEGRAM_TOKEN")
        telegram_chatid = get_secret("TELEGRAM_CHATID")
        # Call the x265transcoder.py script and pass the variables
        subprocess.run(['python', 'x265transcoder.py', folder, include, quality, delete, str(telegram_token), str(telegram_chatid), version])
        return "Success"

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0')
