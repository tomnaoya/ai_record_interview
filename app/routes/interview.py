import os
import threading
from datetime import datetime, timezone
from flask import Blueprint, abort, current_app, jsonify, render_template, request
from app.models import InterviewSession, db

bp = Blueprint("interview", __name__)


def _extract_question(q):
    """質問が辞書形式でも文字列形式でも日本語テキストを返す"""
    if isinstance(q, dict):
        return q.get("question_ja") or q.get("question") or ""
    return str(q)


@bp.get("/<token>")
def index(token):
    s = InterviewSession.query.filter_by(token=token).first_or_404()
    if s.status not in ("waiting", "in_progress"):
        return render_template("interview/expired.html", session=s)
    return render_template("interview/index.html", session=s, job=s.job, applicant=s.applicant)


@bp.post("/<token>/start")
def start(token):
    s = InterviewSession.query.filter_by(token=token).first_or_404()
    if s.status != "waiting":
        return jsonify({"error": "already started"}), 400
    s.status = "in_progress"
    s.started_at = datetime.now(timezone.utc)
    db.session.commit()
    questions = s.job.questions or []
    first_q = _extract_question(questions[0]) if questions else "自己紹介をお願いします。"
    return jsonify({"ok": True, "first_question": first_q, "total": len(questions)})


@bp.post("/<token>/next_question")
def next_question(token):
    s = InterviewSession.query.filter_by(token=token).first_or_404()
    data = request.get_json()
    idx = int(data.get("current_index", 0)) + 1
    questions = s.job.questions or []
    if idx >= len(questions):
        return jsonify({"done": True})
    return jsonify({"done": False, "question": _extract_question(questions[idx]), "index": idx})


@bp.post("/<token>/upload")
def upload_video(token):
    s = InterviewSession.query.filter_by(token=token).first_or_404()
    if "video" not in request.files:
        abort(400, "No video field")
    video_file = request.files["video"]
    upload_dir = current_app.config["VIDEO_UPLOAD_DIR"]
    filename = f"session_{s.id}_{int(datetime.now().timestamp())}.webm"
    save_path = os.path.join(upload_dir, filename)
    video_file.save(save_path)
    s.video_path = save_path
    db.session.commit()
    return jsonify({"ok": True, "path": filename})


@bp.post("/<token>/complete")
def complete(token):
    s = InterviewSession.query.filter_by(token=token).first_or_404()
    if s.status == "completed":
        return jsonify({"ok": True})
    s.status = "completed"
    s.completed_at = datetime.now(timezone.utc)
    db.session.commit()
    app = current_app._get_current_object()
    sid = s.id
    def _run():
        with app.app_context():
            from app.services.ai_evaluation import run_evaluation_pipeline
            try:
                run_evaluation_pipeline(sid)
            except Exception as e:
                print(f"[evaluation error] session={sid}: {e}")
    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"ok": True, "message": "評価を開始しました"})
