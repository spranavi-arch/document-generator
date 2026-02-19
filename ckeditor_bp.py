"""
Flask Blueprint for CKEditor 5: legal document editor and DOCX export.
Mount at /ckeditor (e.g. /ckeditor for the editor page, /ckeditor/api/export-docx for export).
Supports loading formatted content from the Streamlit formatter via set-content + load token.
"""
import secrets
import time
from flask import Blueprint, request, jsonify, send_from_directory, Response, current_app

ckeditor_bp = Blueprint("ckeditor", __name__, url_prefix="/ckeditor")

# In-memory store for content passed from Streamlit (token -> { "html", "created" })
# Entries expire after 10 minutes
_CONTENT_STORE = {}
_STORE_TTL_SEC = 600


def _expire_old():
    now = time.time()
    for token in list(_CONTENT_STORE):
        if now - _CONTENT_STORE[token]["created"] > _STORE_TTL_SEC:
            del _CONTENT_STORE[token]


@ckeditor_bp.route("/")
def editor_page():
    """Serve the CKEditor 5 legal document editor (static/editor.html)."""
    static_dir = current_app.static_folder or "static"
    return send_from_directory(static_dir, "editor.html")


@ckeditor_bp.route("/api/set-content", methods=["POST"])
def set_content():
    """Store HTML for later load. Called by Streamlit after Format with LLM. Returns a short-lived load_token."""
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


@ckeditor_bp.route("/api/load")
def load_content():
    """Return stored HTML for the given token (e.g. from ?load=TOKEN). Used by editor page to show formatted text."""
    token = (request.args.get("token") or request.args.get("load") or "").strip()
    if not token:
        return jsonify({"error": "Missing token"}), 400
    _expire_old()
    entry = _CONTENT_STORE.get(token)
    if not entry:
        return jsonify({"error": "Token not found or expired"}), 404
    return jsonify({"html": entry["html"]})


@ckeditor_bp.route("/api/export-docx", methods=["POST"])
def export_docx():
    """Accept JSON { \"html\": \"...\" } from CKEditor 5 and return a DOCX file."""
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
