"""
面接画面ルート
  GET  /interview/<token>         → 面接UI
  POST /interview/<token>/start   → セッション開始
  POST /interview/<token>/upload  → 動画アップロード
  POST /interview/<token>/complete → 面接完了 → 評価ジョブ起動
"""

import os
import threading
from datetime import datetime, timezone
from flask import Blueprint, abort, current_app, jsonify, render_template, request

from app.models import InterviewSession, db

bp = Blueprint("interview", __name__)


@bp.get("/<token>")
def index(token: str):
    session = InterviewSession.query.filter_by(token=token).first_or_404()
    if session.status not in ("waiting", "in_progress"):
        return render_template("interview/expired.html", session=session)

    job = session.job
    return render_template(
        "interview/index.html",
        session=session,
        job=job,
        applicant=session.applicant,
    )


@bp.post("/<token>/start")
def start(token: str):
    session = InterviewSession.query.filter_by(token=token).first_or_404()
    if session.status != "waiting":
        return jsonify({"error": "already started"}), 400

    session.status = "in_progress"
    session.started_at = datetime.now(timezone.utc)
    db.session.commit()

    # 最初の質問を返す
    questions = session.job.questions or []
    first_q = questions[0] if questions else "自己紹介をお願いします。"
    return jsonify({"ok": True, "first_question": first_q, "total": len(questions)})


@bp.post("/<token>/next_question")
def next_question(token: str):
    """現在の質問インデックスを受け取り次の質問を返す"""
    session = InterviewSession.query.filter_by(token=token).first_or_404()
    data = request.get_json()
    idx = int(data.get("current_index", 0)) + 1
    questions = session.job.questions or []

    if idx >= len(questions):
        return jsonify({"done": True})
    return jsonify({"done": False, "question": questions[idx], "index": idx})


@bp.post("/<token>/upload")
def upload_video(token: str):
    """
    フロントエンドが録画完了後に動画 Blob を POST する。
    Content-Type: multipart/form-data  field name: "video"
    """
    session = InterviewSession.query.filter_by(token=token).first_or_404()

    if "video" not in request.files:
        abort(400, "No video field")

    video_file = request.files["video"]
    upload_dir = current_app.config["VIDEO_UPLOAD_DIR"]
    filename = f"session_{session.id}_{int(datetime.now().timestamp())}.webm"
    save_path = os.path.join(upload_dir, filename)

    video_file.save(save_path)
    session.video_path = save_path
    db.session.commit()

    return jsonify({"ok": True, "path": filename})


@bp.post("/<token>/complete")
def complete(token: str):
    """面接終了 → 非同期で評価パイプライン起動"""
    session = InterviewSession.query.filter_by(token=token).first_or_404()

    if session.status == "completed":
        return jsonify({"ok": True})

    session.status = "completed"
    session.completed_at = datetime.now(timezone.utc)
    db.session.commit()

    # 別スレッドで評価を実行（Renderの場合はCelery/RQ推奨だが、シンプルにスレッドで）
    app = current_app._get_current_object()
    session_id = session.id

    def _run():
        with app.app_context():
            from app.services.ai_evaluation import run_evaluation_pipeline
            try:
                run_evaluation_pipeline(session_id)
            except Exception as e:
                print(f"[evaluation error] session={session_id}: {e}")

    threading.Thread(target=_run, daemon=True).start()

    return jsonify({"ok": True, "message": "評価を開始しました"})
