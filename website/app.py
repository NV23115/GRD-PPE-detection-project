from flask import Flask, render_template_string, send_from_directory
import os

app = Flask(__name__)

# Get the folder where this script lives
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Serve the CSS file
@app.route('/style.css')
def style():
    return send_from_directory(BASE_DIR, 'style.css')

# Home page route
@app.route('/')
def home():
    # Use absolute path to open index.html
    index_path = os.path.join(BASE_DIR, 'index.html')
    with open(index_path, 'r') as f:
        html_content = f.read()
    return render_template_string(html_content)

if __name__ == '__main__':
    app.run(debug=True)