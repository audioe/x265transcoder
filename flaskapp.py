from flask import Flask, render_template, request
import subprocess
import os

app = Flask(__name__)

# Function to get list of directories
def get_directories(parent_dir):
    directories = []
    for entry in os.listdir(parent_dir):
        entry_path = os.path.join(parent_dir, entry)
        if os.path.isdir(entry_path):
            directories.append(entry)
    return directories

# Route to render the HTML page
@app.route('/')
def index():
    parent_dir = "/shows"
    directories = get_directories(parent_dir)
    return render_template('index.html', directories=directories)

@app.route("/run", methods=["POST"])
def run():
    if request.method == 'POST':
        folder = request.form['folder']
        include = request.form['include']
        quality = request.form['quality']
        delete = request.form['delete']
        # Call the x265transcoder.py script and pass the variables
        subprocess.run(['python', 'x265transcoder.py', folder, include, quality, delete])
        return "Success"

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0')
