"""
Simple web UI: upload a .docx file and view its paragraph formatting;
CKEditor 5 legal document editor at /ckeditor with DOCX export.
Run: python app.py  then open http://127.0.0.1:5000
"""
import io
import sys
from pathlib import Path

from flask import Flask, request, jsonify, send_from_directory
from werkzeug.utils import secure_filename

from format import extract_formatting_from_file

# Allow importing formatting package from project root (for CKEditor blueprint)
_root = Path(__file__).resolve().parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from ckeditor_bp import ckeditor_bp

app = Flask(__name__, static_folder="static")
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16 MB max upload
app.register_blueprint(ckeditor_bp)
ALLOWED_EXTENSIONS = {"docx"}


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/api/upload", methods=["POST"])
def upload():
    if "file" not in request.files:
        return jsonify({"error": "No file part"}), 400
    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "No file selected"}), 400
    if not allowed_file(file.filename):
        return jsonify({"error": "Only .docx files are allowed"}), 400
    try:
        stream = io.BytesIO(file.read())
        data = extract_formatting_from_file(stream)
        return jsonify({"ok": True, "data": data, "filename": secure_filename(file.filename)})
    except Exception as e:
        return jsonify({"error": str(e)}), 422


if __name__ == "__main__":
    import sys
    # When run via "streamlit run app.py", Streamlit runs this in a worker thread;
    # Flask's debug server uses signal handlers that only work in the main thread.
    if "streamlit" in sys.modules:
        # Flask app is loaded but not started; run Flask separately with: python app.py
        pass
    else:
        app.run(debug=True, port=5000, use_reloader=False)
