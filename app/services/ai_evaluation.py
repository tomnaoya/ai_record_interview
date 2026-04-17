"""
AI評価パイプライン
  1. imageio-ffmpeg の同梱バイナリで音声抽出（システムffmpeg不要）
  2. 音声が25MB超なら時間で均等分割
  3. Whisper API で文字起こし → 結合
  4. Claude で評価・採点
"""

import json
import os
import subprocess
import tempfile

import anthropic

_client = anthropic.Anthropic()

WHISPER_MAX_BYTES = 24 * 1024 * 1024  # 24MB


# ─────────────────────────────────────────────────────────────────────────────
# ffmpeg バイナリパス（imageio-ffmpeg が同梱）
# ─────────────────────────────────────────────────────────────────────────────

def _ffmpeg_bin() -> str:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"  # システムffmpegにフォールバック


# ─────────────────────────────────────────────────────────────────────────────
# 1. 音声抽出・分割・文字起こし
# ─────────────────────────────────────────────────────────────────────────────

def _get_duration(ffmpeg: str, video_path: str) -> float:
    """動画の長さ（秒）を取得"""
    result = subprocess.run(
        [ffmpeg, "-i", video_path],
        capture_output=True, text=True
    )
    # stderr に "Duration: HH:MM:SS.ss" が出る
    for line in result.stderr.splitlines():
        if "Duration:" in line:
            parts = line.strip().split("Duration:")[1].split(",")[0].strip()
            h, m, s = parts.split(":")
            return float(h) * 3600 + float(m) * 60 + float(s)
    return 0.0


def _extract_audio_segment(ffmpeg: str, video_path: str,
                            start: float, duration: float, out_path: str):
    """動画の指定区間から音声をmp3で抽出"""
    subprocess.run(
        [
            ffmpeg, "-y",
            "-ss", str(start),
            "-t",  str(duration),
            "-i",  video_path,
            "-vn",
            "-ar", "16000",
            "-ac", "1",
            "-b:a", "32k",   # 低ビットレートで容量を抑える
            out_path,
        ],
        check=True,
        capture_output=True,
    )


def _transcribe_file(oai_client, file_path: str) -> str:
    with open(file_path, "rb") as f:
        return oai_client.audio.transcriptions.create(
            model="whisper-1",
            file=f,
            language="ja",
            response_format="text",
        )


def transcribe_video(video_path: str) -> str:
    import openai
    oai    = openai.OpenAI()
    ffmpeg = _ffmpeg_bin()

    # ── まず音声全体をmp3に抽出 ───────────────────────────────────────────
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
        audio_path = tmp.name

    try:
        subprocess.run(
            [
                ffmpeg, "-y", "-i", video_path,
                "-vn", "-ar", "16000", "-ac", "1", "-b:a", "32k",
                audio_path,
            ],
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as e:
        # ffmpeg失敗時は動画ファイルをそのまま送信（フォールバック）
        print(f"[transcribe] ffmpeg failed, sending raw file: {e}")
        os.unlink(audio_path)
        return _transcribe_file(oai, video_path)

    try:
        audio_size = os.path.getsize(audio_path)

        if audio_size <= WHISPER_MAX_BYTES:
            # 25MB以内 → そのまま送信
            print(f"[transcribe] audio {audio_size/1024/1024:.1f}MB → single request")
            return _transcribe_file(oai, audio_path)

        # 25MB超 → 時間で均等分割
        total_sec = _get_duration(ffmpeg, video_path)
        # 1チャンクあたりの秒数を計算（余裕を持って0.8倍）
        chunk_sec = total_sec * (WHISPER_MAX_BYTES / audio_size) * 0.8
        chunk_sec = max(60.0, chunk_sec)  # 最低60秒

        n_chunks  = int(total_sec / chunk_sec) + 1
        print(f"[transcribe] audio {audio_size/1024/1024:.1f}MB, "
              f"total={total_sec:.0f}s → {n_chunks} chunks of {chunk_sec:.0f}s")

        transcripts = []
        tmp_chunks  = []

        try:
            for i in range(n_chunks):
                start    = i * chunk_sec
                if start >= total_sec:
                    break
                duration = min(chunk_sec, total_sec - start)

                chunk_tmp = tempfile.NamedTemporaryFile(
                    suffix=".mp3", delete=False, prefix=f"chunk_{i}_"
                )
                chunk_tmp.close()
                tmp_chunks.append(chunk_tmp.name)

                _extract_audio_segment(
                    ffmpeg, video_path, start, duration, chunk_tmp.name
                )

                chunk_size = os.path.getsize(chunk_tmp.name)
                print(f"[transcribe] chunk {i+1}/{n_chunks}: "
                      f"start={start:.0f}s dur={duration:.0f}s "
                      f"size={chunk_size/1024/1024:.1f}MB")

                try:
                    text = _transcribe_file(oai, chunk_tmp.name)
                    if text and text.strip():
                        transcripts.append(text.strip())
                except Exception as e:
                    print(f"[transcribe] chunk {i+1} whisper error: {e}")

        finally:
            for p in tmp_chunks:
                try: os.unlink(p)
                except Exception: pass

        return "\n".join(transcripts)

    finally:
        try: os.unlink(audio_path)
        except Exception: pass


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


def evaluate_interview(transcript: str, job_title: str,
                        evaluation_criteria: str, questions: list) -> dict:
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

        job    = session.job
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
