"""
AI評価パイプライン
  1. Whisper で動画を文字起こし（25MB超はチャンク分割）
  2. Claude で評価・採点・サマリーを生成
"""

import json
import os
import tempfile

import anthropic

_client = anthropic.Anthropic()

WHISPER_MAX_BYTES = 24 * 1024 * 1024  # 24MB（25MB制限に余裕を持たせる）


# ─────────────────────────────────────────────────────────────────────────────
# 1. 文字起こし
# ─────────────────────────────────────────────────────────────────────────────

def _transcribe_file(oai_client, file_path: str) -> str:
    """単一ファイルをWhisperに送って文字起こし"""
    with open(file_path, "rb") as f:
        result = oai_client.audio.transcriptions.create(
            model="whisper-1",
            file=f,
            language="ja",
            response_format="text",
        )
    return result


def _split_file(file_path: str, chunk_size: int) -> list[str]:
    """
    ファイルをバイト単位で分割して一時ファイルリストを返す。
    webm/mp4 はコンテナ単位の分割ができないため、バイト分割して
    各チャンクを独立したファイルとして送る（Whisperは不完全なwebmも処理可能）。
    """
    tmp_files = []
    with open(file_path, "rb") as f:
        idx = 0
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            tmp = tempfile.NamedTemporaryFile(
                suffix=".webm", delete=False, prefix=f"whisper_chunk_{idx}_"
            )
            tmp.write(chunk)
            tmp.close()
            tmp_files.append(tmp.name)
            idx += 1
    return tmp_files


def transcribe_video(video_path: str) -> str:
    """
    動画ファイルを文字起こしする。
    25MB以下: そのままWhisperに送信
    25MB超  : チャンク分割して送信し結果を結合
    """
    import openai
    oai = openai.OpenAI()

    file_size = os.path.getsize(video_path)

    if file_size <= WHISPER_MAX_BYTES:
        # そのまま送信
        return _transcribe_file(oai, video_path)

    # チャンク分割
    print(f"[transcribe] File size {file_size/1024/1024:.1f}MB > 24MB, splitting...")
    tmp_files = _split_file(video_path, WHISPER_MAX_BYTES)
    transcripts = []
    try:
        for i, tmp_path in enumerate(tmp_files):
            print(f"[transcribe] Processing chunk {i+1}/{len(tmp_files)}...")
            try:
                text = _transcribe_file(oai, tmp_path)
                if text:
                    transcripts.append(text.strip())
            except Exception as e:
                print(f"[transcribe] Chunk {i+1} failed: {e}")
    finally:
        for p in tmp_files:
            try:
                os.unlink(p)
            except Exception:
                pass

    return "\n".join(transcripts)


# ─────────────────────────────────────────────────────────────────────────────
# 2. Claude による評価
# ─────────────────────────────────────────────────────────────────────────────

EVALUATION_SYSTEM = """
あなたは採用担当の面接評価AIです。
AI面接官と応募者のやりとりの文字起こしを読み、以下のJSON形式のみで回答してください。
JSON以外のテキストは一切含めないでください。

{
  "score": <0〜100の整数>,
  "recommendation": <"pass" | "review" | "fail">,
  "summary": "<200字以内の総評>",
  "evaluation": "<詳細評価。各質問への回答の質・論理性・熱意・コミュニケーション能力を具体的に>",
  "strengths": ["<強み1>", "<強み2>"],
  "concerns": ["<懸念点1>", "<懸念点2>"]
}

採点基準:
- 80〜100: pass  （即戦力・非常に良好）
- 60〜79:  review（要検討・上長判断）
- 0〜59:   fail   （基準未達）
"""


def evaluate_interview(
    transcript: str,
    job_title: str,
    evaluation_criteria: str,
    questions: list,
) -> dict:
    prompt = f"""
【求人職種】{job_title}

【評価基準】
{evaluation_criteria or "コミュニケーション能力・論理的思考・意欲を重視"}

【面接で問うた質問】
{chr(10).join(f"- {q.get('question_ja', q) if isinstance(q, dict) else q}" for q in questions)}

【面接文字起こし】
{transcript}

上記を踏まえて評価してください。
"""

    message = _client.messages.create(
        model="claude-opus-4-5",
        max_tokens=1024,
        system=EVALUATION_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = message.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw)


# ─────────────────────────────────────────────────────────────────────────────
# 3. 評価パイプライン
# ─────────────────────────────────────────────────────────────────────────────

def run_evaluation_pipeline(session_id: int):
    from app.models import InterviewSession, db

    session = db.session.get(InterviewSession, session_id)
    if not session or not session.video_path:
        print(f"[evaluation] session {session_id}: no video, skipping")
        return

    if not os.path.exists(session.video_path):
        session.status     = "error"
        session.ai_summary = f"動画ファイルが見つかりません: {session.video_path}"
        db.session.commit()
        return

    try:
        session.status = "evaluating"
        db.session.commit()

        transcript = transcribe_video(session.video_path)
        session.transcript = transcript

        job = session.job
        result = evaluate_interview(
            transcript=transcript,
            job_title=job.title,
            evaluation_criteria=job.evaluation_criteria or "",
            questions=job.questions or [],
        )

        session.score          = result.get("score")
        session.recommendation = result.get("recommendation")
        session.ai_summary     = result.get("summary")
        session.ai_evaluation  = json.dumps(result, ensure_ascii=False)
        session.status         = "evaluated"

    except Exception as e:
        session.status     = "error"
        session.ai_summary = f"評価エラー: {e}"
        raise

    finally:
        db.session.commit()
