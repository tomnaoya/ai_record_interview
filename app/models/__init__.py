from datetime import datetime, timezone
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
import secrets

db = SQLAlchemy()


def utcnow():
    return datetime.now(timezone.utc)


class Company(db.Model):
    __tablename__ = "companies"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    name_kana = db.Column(db.String(200))
    industry = db.Column(db.String(100))
    size = db.Column(db.String(50))
    phone = db.Column(db.String(20))
    logo_path = db.Column(db.String(500))
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)

    accounts = db.relationship("Account", back_populates="company")
    jobs = db.relationship("Job", back_populates="company")


class Account(UserMixin, db.Model):
    __tablename__ = "accounts"

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("companies.id"), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(200), nullable=False, unique=True)
    password_hash = db.Column(db.String(256))
    role = db.Column(db.String(20), default="company")  # admin / company
    is_active = db.Column(db.Boolean, default=True)
    last_login_at = db.Column(db.DateTime(timezone=True))
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)

    company = db.relationship("Company", back_populates="accounts")

    def set_password(self, password: str):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)


class Job(db.Model):
    """求人情報"""
    __tablename__ = "jobs"

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("companies.id"), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    requirements = db.Column(db.Text)          # 応募要件
    evaluation_criteria = db.Column(db.Text)   # AIへの評価基準指示
    # 面接設定
    questions = db.Column(db.JSON, default=list)      # 事前設定質問リスト
    max_duration_minutes = db.Column(db.Integer, default=30)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)

    company = db.relationship("Company", back_populates="jobs")
    applicants = db.relationship("Applicant", back_populates="job")


class Applicant(db.Model):
    """応募者"""
    __tablename__ = "applicants"

    id = db.Column(db.Integer, primary_key=True)
    job_id = db.Column(db.Integer, db.ForeignKey("jobs.id"), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(200))
    phone = db.Column(db.String(20))
    resume_path = db.Column(db.String(500))
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)

    job = db.relationship("Job", back_populates="applicants")
    interviews = db.relationship("InterviewSession", back_populates="applicant")


class InterviewSession(db.Model):
    """面接セッション（1応募者に複数回あり得る）"""
    __tablename__ = "interview_sessions"

    id = db.Column(db.Integer, primary_key=True)
    applicant_id = db.Column(db.Integer, db.ForeignKey("applicants.id"), nullable=False)
    # 面接リンク用トークン（公開URL）
    token = db.Column(db.String(64), unique=True, nullable=False,
                      default=lambda: secrets.token_urlsafe(32))

    status = db.Column(
        db.String(20), default="waiting"
    )  # waiting / in_progress / completed / evaluating / evaluated / error

    started_at = db.Column(db.DateTime(timezone=True))
    completed_at = db.Column(db.DateTime(timezone=True))
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)

    # 録画動画
    video_path = db.Column(db.String(500))      # サーバ上のファイルパス
    video_duration_sec = db.Column(db.Integer)

    # 文字起こし・評価結果
    transcript = db.Column(db.Text)            # Whisper文字起こし結果
    ai_summary = db.Column(db.Text)            # Claude要約
    ai_evaluation = db.Column(db.Text)         # Claude評価文（詳細）
    score = db.Column(db.Integer)              # 0-100
    recommendation = db.Column(db.String(20))  # pass / review / fail

    applicant = db.relationship("Applicant", back_populates="interviews")

    @property
    def job(self):
        return self.applicant.job


class TTSRule(db.Model):
    """TTS読み方辞書（管理画面で設定）"""
    __tablename__ = "tts_rules"

    id         = db.Column(db.Integer, primary_key=True)
    word       = db.Column(db.String(100), nullable=False, unique=True)  # 表記
    reading    = db.Column(db.String(200), nullable=False)               # 読み替え
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)
