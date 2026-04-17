import os
import glob
import threading
from datetime import datetime, timezone
from flask import Blueprint, abort, current_app, jsonify, render_template, request
from app.models import InterviewSession, db

bp = Blueprint("interview", __name__)


def _extract_question(q):
    if isinstance(q, dict):
        return q.get("question_ja") or q.get("question") or ""
    return str(q)


def _chunks_dir(upload_dir: str, session_id: int) -> str:
    """チャンク保存ディレクトリ"""
    d = os.path.join(upload_dir, f"chunks_{session_id}")
    os.makedirs(d, exist_ok=True)
    return d


def _merge_chunks(session_id: int, upload_dir: str) -> str | None:
    """チャンクを結合して最終動画ファイルを作成"""
    chunk_dir = _chunks_dir(upload_dir, session_id)
    pattern   = os.path.join(chunk_dir, "chunk_*.webm")
    chunks    = sorted(glob.glob(pattern),
                       key=lambda p: int(os.path.basename(p).replace("chunk_", "").replace(".webm", "")))
    if not chunks:
        return None

    out_path = os.path.join(upload_dir, f"session_{session_id}.webm")
    with open(out_path, "wb") as out:
        for chunk_path in chunks:
            with open(chunk_path, "rb") as f:
                out.write(f.read())

    # チャンクファイルを削除
    for c in chunks:
        try:
            os.remove(c)
        except Exception:
            pass
    try:
        os.rmdir(chunk_dir)
    except Exception:
        pass

    return out_path


# ── 面接ページ ────────────────────────────────────────────────────────────────

@bp.get("/<token>")
def index(token):
    s = InterviewSession.query.filter_by(token=token).first_or_404()
    if s.status not in ("waiting", "in_progress"):
        return render_template("interview/expired.html", session=s)
    return render_template("interview/index.html", session=s, job=s.job, applicant=s.applicant)


# ── 開始 ─────────────────────────────────────────────────────────────────────

@bp.post("/<token>/start")
def start(token):
    s = InterviewSession.query.filter_by(token=token).first_or_404()
    questions = s.job.questions or []
    first_q   = _extract_question(questions[0]) if questions else "自己紹介をお願いします。"

    if s.status == "in_progress":
        return jsonify({"ok": True, "first_question": first_q,
                        "total": len(questions), "resumed": True})
    if s.status != "waiting":
        return jsonify({"error": "この面接は既に完了しています"}), 400

    s.status     = "in_progress"
    s.started_at = datetime.now(timezone.utc)
    db.session.commit()
    return jsonify({"ok": True, "first_question": first_q, "total": len(questions)})


# ── 次の質問 ─────────────────────────────────────────────────────────────────

@bp.post("/<token>/next_question")
def next_question(token):
    s    = InterviewSession.query.filter_by(token=token).first_or_404()
    data = request.get_json()
    idx  = int(data.get("current_index", 0)) + 1
    questions = s.job.questions or []
    if idx >= len(questions):
        return jsonify({"done": True})
    return jsonify({"done": False, "question": _extract_question(questions[idx]), "index": idx})


# ── チャンク受信（リアルタイム） ──────────────────────────────────────────────

@bp.post("/<token>/chunk")
def upload_chunk(token):
    """
    5秒ごとのチャンクを受信して保存。
    クエリパラメータ: seq=0,1,2,... （シーケンス番号）
    """
    s = InterviewSession.query.filter_by(token=token).first_or_404()
    if "video" not in request.files:
        abort(400, "No video field")

    seq        = request.args.get("seq", "0")
    upload_dir = current_app.config["VIDEO_UPLOAD_DIR"]
    chunk_dir  = _chunks_dir(upload_dir, s.id)
    chunk_path = os.path.join(chunk_dir, f"chunk_{seq.zfill(6)}.webm")

    request.files["video"].save(chunk_path)
    return jsonify({"ok": True, "seq": seq})


# ── 完全アップロード（従来方式・フォールバック用） ────────────────────────────

@bp.post("/<token>/upload")
def upload_video(token):
    s = InterviewSession.query.filter_by(token=token).first_or_404()
    if "video" not in request.files:
        abort(400, "No video field")

    upload_dir = current_app.config["VIDEO_UPLOAD_DIR"]
    ext        = "webm"  # モバイルmp4の場合も受け入れ
    filename   = f"session_{s.id}_{int(datetime.now().timestamp())}.{ext}"
    save_path  = os.path.join(upload_dir, filename)
    request.files["video"].save(save_path)
    s.video_path = save_path
    db.session.commit()
    return jsonify({"ok": True, "path": filename})


# ── 完了 ─────────────────────────────────────────────────────────────────────

@bp.post("/<token>/complete")
def complete(token):
    s = InterviewSession.query.filter_by(token=token).first_or_404()
    if s.status in ("completed", "evaluated", "evaluating"):
        return jsonify({"ok": True})

    s.status       = "completed"
    s.completed_at = datetime.now(timezone.utc)

    # チャンクが存在すれば結合して video_path に設定
    upload_dir = current_app.config["VIDEO_UPLOAD_DIR"]
    merged = _merge_chunks(s.id, upload_dir)
    if merged and not s.video_path:
        s.video_path = merged
    elif merged:
        # チャンクと通常アップロードの両方がある場合はチャンクを優先
        s.video_path = merged

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


# ── 中断（ページ離脱時） ──────────────────────────────────────────────────────

@bp.post("/<token>/interrupt")
def interrupt(token):
    """
    ブラウザ離脱時に呼ばれる。
    チャンクが保存済みであれば結合して評価を開始する。
    """
    s = InterviewSession.query.filter_by(token=token).first_or_404()
    if s.status in ("completed", "evaluated", "evaluating"):
        return jsonify({"ok": True})

    upload_dir = current_app.config["VIDEO_UPLOAD_DIR"]
    merged = _merge_chunks(s.id, upload_dir)
    if merged:
        s.video_path = merged

    s.status       = "completed"
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
                print(f"[interrupt evaluation error] session={sid}: {e}")

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"ok": True})
