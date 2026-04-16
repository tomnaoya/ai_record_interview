from flask import Blueprint, jsonify
bp = Blueprint("api", __name__)

@bp.get("/health")
def health():
    return jsonify({"status": "ok"})

@bp.get("/tts-rules")
def tts_rules():
    """面接ページがTTS読み方辞書を取得するAPI"""
    from app.models import TTSRule
    rules = TTSRule.query.order_by(TTSRule.word.desc()).all()
    return jsonify([{"word": r.word, "reading": r.reading} for r in rules])
