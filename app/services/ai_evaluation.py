"""
AI評価パイプライン
  1. Whisper で動画から音声を文字起こし
  2. Claude で評価・採点・サマリーを生成
"""

import json
import os
import subprocess
import tempfile

import anthropic

# Anthropic クライアント（ANTHROPIC_API_KEY 環境変数を自動参照）
_client = anthropic.Anthropic()


# ─────────────────────────────────────────────────────────────────────────────
# 1. 音声文字起こし
# ─────────────────────────────────────────────────────────────────────────────

def transcribe_video(video_path: str) -> str:
    """
    動画ファイルから音声を抽出し、Whisper API で文字起こしする。
    ffmpeg が必要（Render の環境では apt で追加）。
    """
    # 音声を一時ファイルに抽出（16kHz mono mp3）
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
        audio_path = tmp.name

    try:
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", video_path,
                "-vn",                    # 映像を除外
                "-ar", "16000",           # サンプリングレート
                "-ac", "1",               # モノラル
                "-b:a", "64k",
                audio_path,
            ],
            check=True,
            capture_output=True,
        )

        # Whisper API 呼び出し
        import openai
        oai = openai.OpenAI()  # OPENAI_API_KEY を参照
        with open(audio_path, "rb") as f:
            result = oai.audio.transcriptions.create(
                model="whisper-1",
                file=f,
                language="ja",
                response_format="text",
            )
        return result

    finally:
        if os.path.exists(audio_path):
            os.unlink(audio_path)


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
    questions: list[str],
) -> dict:
    """
    Claude に面接内容を評価させる。
    返り値は dict: score, recommendation, summary, evaluation, strengths, concerns
    """
    prompt = f"""
【求人職種】{job_title}

【評価基準】
{evaluation_criteria or "コミュニケーション能力・論理的思考・意欲を重視"}

【面接で問うた質問】
{chr(10).join(f"- {q}" for q in questions)}

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
    # コードブロックが含まれる場合に除去
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw)


# ─────────────────────────────────────────────────────────────────────────────
# 3. 評価パイプライン（まとめて実行）
# ─────────────────────────────────────────────────────────────────────────────

def run_evaluation_pipeline(session_id: int):
    """
    Flask アプリコンテキスト内で呼ぶこと。
    InterviewSession を取得 → 文字起こし → 評価 → DB保存
    """
    from app.models import InterviewSession, db

    session = db.session.get(InterviewSession, session_id)
    if not session or not session.video_path:
        return

    try:
        session.status = "evaluating"
        db.session.commit()

        # 文字起こし
        transcript = transcribe_video(session.video_path)
        session.transcript = transcript

        # Claude 評価
        job = session.job
        result = evaluate_interview(
            transcript=transcript,
            job_title=job.title,
            evaluation_criteria=job.evaluation_criteria or "",
            questions=job.questions or [],
        )

        session.score = result.get("score")
        session.recommendation = result.get("recommendation")
        session.ai_summary = result.get("summary")
        session.ai_evaluation = json.dumps(result, ensure_ascii=False)
        session.status = "evaluated"

    except Exception as e:
        session.status = "error"
        session.ai_summary = f"評価エラー: {e}"
        raise

    finally:
        db.session.commit()
