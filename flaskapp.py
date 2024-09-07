from flask import Flask, render_template, request
import subprocess

app = Flask(__name__)

# Route to render the HTML page
@app.route('/')
def index():
    return render_template('index.html')

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
