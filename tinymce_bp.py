"""
Flask Blueprint for TinyMCE: legal document editor and DOCX export.
Mount at /tinymce (e.g. /tinymce for the editor page, /tinymce/api/export-docx for export).
Same API contract as CKEditor: set-content, load, export-docx.
"""
import secrets
import time
from flask import Blueprint, request, jsonify, send_from_directory, Response, current_app

tinymce_bp = Blueprint("tinymce", __name__, url_prefix="/tinymce")

# In-memory store for content passed from Streamlit or other callers (token -> { "html", "created" })
# Entries expire after 10 minutes
_CONTENT_STORE = {}
_STORE_TTL_SEC = 600


def _expire_old():
    now = time.time()
    for token in list(_CONTENT_STORE):
        if now - _CONTENT_STORE[token]["created"] > _STORE_TTL_SEC:
            del _CONTENT_STORE[token]


@tinymce_bp.route("/")
def editor_page():
    """Serve the TinyMCE legal document editor (static/tinymce_editor.html)."""
    static_dir = current_app.static_folder or "static"
    return send_from_directory(static_dir, "tinymce_editor.html")


@tinymce_bp.route("/api/set-content", methods=["POST"])
def set_content():
    """Store HTML for later load. Returns a short-lived load_token."""
    try:
        data = request.get_json(force=True, silent=True) or {}
        html = data.get("html") or ""
    except Exception:
        return jsonify({"error": "Invalid JSON body"}), 400
    if not isinstance(html, str):
        return jsonify({"error": "Missing or invalid 'html' field"}), 400
    _expire_old()
    token = secrets.token_urlsafe(12)
    _CONTENT_STORE[token] = {"html": html, "created": time.time()}
    return jsonify({"load_token": token})


@tinymce_bp.route("/api/load")
def load_content():
    """Return stored HTML for the given token (e.g. from ?load=TOKEN)."""
    token = (request.args.get("token") or request.args.get("load") or "").strip()
    if not token:
        return jsonify({"error": "Missing token"}), 400
    _expire_old()
    entry = _CONTENT_STORE.get(token)
    if not entry:
        return jsonify({"error": "Token not found or expired"}), 404
    return jsonify({"html": entry["html"]})


@tinymce_bp.route("/api/export-docx", methods=["POST"])
def export_docx():
    """Accept JSON { \"html\": \"...\" } from TinyMCE and return a DOCX file."""
    try:
        data = request.get_json(force=True, silent=True) or {}
        html = data.get("html") or ""
    except Exception:
        return jsonify({"error": "Invalid JSON body"}), 400
    if not isinstance(html, str):
        return jsonify({"error": "Missing or invalid 'html' field"}), 400
    try:
        from formatting.utils.html_to_docx import html_to_docx_bytes
        docx_bytes = html_to_docx_bytes(html)
    except ImportError as e:
        return jsonify({"error": "DOCX export unavailable: " + str(e)}), 503
    except Exception as e:
        return jsonify({"error": str(e)}), 422
    return Response(
        docx_bytes,
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": "attachment; filename=document.docx"},
    )
