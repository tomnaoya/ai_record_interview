"""
AI評価パイプライン
  - imageio-ffmpeg の同梱バイナリで音声抽出（システムffmpeg不要）
  - 25MB超は時間分割してWhisperに送信
  - Claude で評価・採点
"""

import json
import os
import subprocess
import tempfile

import anthropic

_client = anthropic.Anthropic()

WHISPER_MAX_BYTES = 24 * 1024 * 1024  # 24MB


def _ffmpeg_bin() -> str:
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        print(f"[ffmpeg] using imageio-ffmpeg: {exe}")
        return exe
    except Exception as e:
        print(f"[ffmpeg] imageio-ffmpeg unavailable ({e}), using system ffmpeg")
        return "ffmpeg"


def _get_duration(ffmpeg: str, path: str) -> float:
    result = subprocess.run([ffmpeg, "-i", path], capture_output=True, text=True)
    for line in result.stderr.splitlines():
        if "Duration:" in line:
            t = line.strip().split("Duration:")[1].split(",")[0].strip()
            h, m, s = t.split(":")
            return float(h) * 3600 + float(m) * 60 + float(s)
    return 0.0


def _extract_audio_segment(ffmpeg: str, video_path: str,
                            start: float, duration: float, out_path: str):
    subprocess.run(
        [ffmpeg, "-y", "-ss", str(start), "-t", str(duration),
         "-i", video_path, "-vn", "-ar", "16000", "-ac", "1", "-b:a", "32k", out_path],
        check=True, capture_output=True,
    )


def _transcribe_file(oai_client, file_path: str) -> str:
    with open(file_path, "rb") as f:
        return oai_client.audio.transcriptions.create(
            model="whisper-1", file=f, language="ja", response_format="text"
        )


def transcribe_video(video_path: str) -> str:
    import openai
    oai    = openai.OpenAI()
    ffmpeg = _ffmpeg_bin()

    # 音声全体をmp3に抽出
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
        audio_path = tmp.name

    try:
        subprocess.run(
            [ffmpeg, "-y", "-i", video_path,
             "-vn", "-ar", "16000", "-ac", "1", "-b:a", "32k", audio_path],
            check=True, capture_output=True,
        )
    except subprocess.CalledProcessError as e:
        print(f"[transcribe] ffmpeg audio extraction failed: {e.stderr.decode()[:300]}")
        os.unlink(audio_path)
        # フォールバック: 動画をそのまま送信（25MB以下の場合のみ）
        file_size = os.path.getsize(video_path)
        if file_size <= WHISPER_MAX_BYTES:
            print(f"[transcribe] fallback: sending raw file ({file_size/1024/1024:.1f}MB)")
            return _transcribe_file(oai, video_path)
        else:
            raise RuntimeError(f"ffmpeg失敗かつファイルサイズが{file_size/1024/1024:.1f}MBで直送不可")

    try:
        audio_size = os.path.getsize(audio_path)
        print(f"[transcribe] audio {audio_size/1024/1024:.1f}MB extracted")

        if audio_size <= WHISPER_MAX_BYTES:
            print("[transcribe] → single request")
            return _transcribe_file(oai, audio_path)

        # 25MB超 → 時間分割
        total_sec = _get_duration(ffmpeg, video_path)
        chunk_sec = max(60.0, total_sec * (WHISPER_MAX_BYTES / audio_size) * 0.8)
        n_chunks  = int(total_sec / chunk_sec) + 1
        print(f"[transcribe] → split into {n_chunks} chunks of {chunk_sec:.0f}s")

        transcripts = []
        tmp_chunks  = []
        try:
            for i in range(n_chunks):
                start = i * chunk_sec
                if start >= total_sec:
                    break
                dur = min(chunk_sec, total_sec - start)
                c   = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False, prefix=f"chunk_{i}_")
                c.close()
                tmp_chunks.append(c.name)
                _extract_audio_segment(ffmpeg, video_path, start, dur, c.name)
                csize = os.path.getsize(c.name)
                print(f"[transcribe] chunk {i+1}/{n_chunks}: start={start:.0f}s dur={dur:.0f}s size={csize/1024/1024:.1f}MB")
                try:
                    text = _transcribe_file(oai, c.name)
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
- 80〜100: pass
- 60〜79:  review
- 0〜59:   fail
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
    msg = _client.messages.create(
        model="claude-opus-4-5", max_tokens=1024,
        system=EVALUATION_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = msg.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"): raw = raw[4:]
    return json.loads(raw)


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

    print(f"[evaluation] session {session_id}: video={session.video_path} size={os.path.getsize(session.video_path)/1024/1024:.1f}MB")

    try:
        session.status = "evaluating"
        db.session.commit()

        transcript = transcribe_video(session.video_path)
        print(f"[evaluation] transcript length: {len(transcript)} chars")
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
        print(f"[evaluation] done: score={session.score}, recommendation={session.recommendation}")

    except Exception as e:
        session.status     = "error"
        session.ai_summary = f"評価エラー: {e}"
        print(f"[evaluation] ERROR: {e}")
        raise

    finally:
        db.session.commit()
