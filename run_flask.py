"""
Run the Flask app (DOCX viewer + CKEditor 5 at /ckeditor).
Activate your venv first, then: python run_flask.py
"""
import sys
from pathlib import Path

# Ensure project root is on path
_root = Path(__file__).resolve().parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from app import app

if __name__ == "__main__":
    app.run(debug=True, port=5000, use_reloader=False)
