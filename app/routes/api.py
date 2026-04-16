from flask import Blueprint, jsonify
bp = Blueprint("api", __name__)

@bp.get("/health")
def health():
    return jsonify({"status": "ok"})
