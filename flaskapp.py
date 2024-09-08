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

@app.route('/get_secret/secret')
def get_secret(secret_name):
    try:
        secret_value = config[secret_name]
        return jsonify({'secret': secret_value})
    except KeyError:
        return jsonify({'error': 'Secret not found'}), 404

# Route to render the HTML page
@app.route('/')
def index():
    parent_dir = "/shows"
    directories = get_directories(parent_dir)
    return render_template('index.html', directories=directories, version=version, os=os)

@app.route("/run", methods=["POST"])
def run():
    if request.method == 'POST':
        folder = str(request.form['folder'])
        include = request.form['include']
        quality = request.form['quality']
        delete = request.form['delete']
        telegram_token = get_secret("TELEGRAM_TOKEN")
        telegram_chatid = get_secret("TELEGRAM_CHATID")
        # Call the x265transcoder.py script and pass the variables
        subprocess.run(['python', 'x265transcoder.py', folder, include, quality, delete, telegram_token, telegram_chatid])
        return "Success"

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0')
