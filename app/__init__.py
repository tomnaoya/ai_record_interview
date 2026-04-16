import os
from flask import Flask, redirect, url_for
from flask_login import LoginManager
from .models import db, Account


def create_app():
    app = Flask(__name__, template_folder="templates", static_folder="static")

    # DATABASE_URL を psycopg3 形式に変換
    db_url = os.environ.get("DATABASE_URL", "sqlite:///morgenrot.db")
    db_url = db_url.replace("postgres://", "postgresql://")
    if "postgresql://" in db_url and "postgresql+psycopg://" not in db_url:
        db_url = db_url.replace("postgresql://", "postgresql+psycopg://")

    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "change-me-in-production")
    app.config["SQLALCHEMY_DATABASE_URI"] = db_url
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    app.config["VIDEO_UPLOAD_DIR"] = os.environ.get(
        "VIDEO_UPLOAD_DIR", os.path.join(os.path.dirname(__file__), "..", "videos")
    )
    os.makedirs(app.config["VIDEO_UPLOAD_DIR"], exist_ok=True)
    app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024 * 1024  # 2GB

    db.init_app(app)

    login_manager = LoginManager()
    login_manager.login_view = "admin.login"
    login_manager.init_app(app)

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(Account, int(user_id))

    # ルートURLを /admin にリダイレクト
    @app.route("/")
    def index():
        return redirect(url_for("admin.dashboard"))

    from .routes.admin import bp as admin_bp
    from .routes.interview import bp as interview_bp
    from .routes.api import bp as api_bp

    app.register_blueprint(admin_bp, url_prefix="/admin")
    app.register_blueprint(interview_bp, url_prefix="/interview")
    app.register_blueprint(api_bp, url_prefix="/api")

    with app.app_context():
        db.create_all()
        _seed_initial_data()

    return app


def _seed_initial_data():
    """初回起動時のみデフォルトデータを投入する"""
    from .models import Account, Company, Job, db

    # ── 管理者アカウント ─────────────────────────────────────────────────────
    admin_email    = os.environ.get("ADMIN_EMAIL", "admin@morgenrot.jp")
    admin_password = os.environ.get("ADMIN_PASSWORD", "changeme")

    # ── 企業 ────────────────────────────────────────────────────────────────
    company = Company.query.filter_by(name="医療法人社団モルゲンロート").first()
    if not company:
        company = Company(
            name="医療法人社団モルゲンロート",
            name_kana="イリョウホウジンシャダンモルゲンロート",
            industry="医療・福祉",
            size="51〜100名",
            is_active=True,
        )
        db.session.add(company)
        db.session.flush()
        print("[seed] Company created")

    # ── 管理者アカウント ─────────────────────────────────────────────────────
    if not Account.query.filter_by(email=admin_email).first():
        admin = Account(
            company_id=company.id,
            name="システム管理者",
            email=admin_email,
            role="admin",
        )
        admin.set_password(admin_password)
        db.session.add(admin)
        print(f"[seed] Admin created: {admin_email}")

    # ── 求人情報（初回のみ） ─────────────────────────────────────────────────
    if not Job.query.first():
        questions = [
            {"id": 1,  "question_ja": "簡潔に自己紹介をお願いします。",                                                    "question_en": "Please briefly introduce yourself.",                                                                                      "time_limit": 120},
            {"id": 2,  "question_ja": "採用HPや採用動画を見たうえで率直な当法人の印象や感想を教えてください。",                "question_en": "What is your honest impression of our organization after viewing our recruitment site and videos?",                        "time_limit": 180},
            {"id": 3,  "question_ja": "ご自身の長所と短所を教えてください。",                                               "question_en": "Please tell us your strengths and weaknesses.",                                                                           "time_limit": 180},
            {"id": 4,  "question_ja": "当法人を志望した理由を教えてください。",                                             "question_en": "Why did you apply to our organization?",                                                                                  "time_limit": 180},
            {"id": 5,  "question_ja": "転職活動で大事にしている転職活動の軸、ゴールを教えてください。",                      "question_en": "What are the key criteria and goals of your job search?",                                                                  "time_limit": 180},
            {"id": 6,  "question_ja": "現在他社選考の状況や進捗があれば教えてください。",                                   "question_en": "Please share your current status with other companies' selection processes.",                                             "time_limit": 120},
            {"id": 7,  "question_ja": "複数の内定が出た際に何を基準として就業先を決めますか？",                              "question_en": "If you receive multiple job offers, what criteria will you use to decide?",                                                "time_limit": 150},
            {"id": 8,  "question_ja": "ご家族の方、友人の方、職場の方にどのような人といわれることが多いですか。",             "question_en": "How do your family, friends, and colleagues typically describe you?",                                                       "time_limit": 150},
            {"id": 9,  "question_ja": "今までのお仕事の経験での失敗談を教えてください。",                                   "question_en": "Please share a failure experience from your work history.",                                                                "time_limit": 180},
            {"id": 10, "question_ja": "仲間と議論して意見が相違しているとき、貴方はどう考えて動きますか。",                  "question_en": "When you disagree with colleagues during discussions, how do you handle it?",                                              "time_limit": 180},
            {"id": 11, "question_ja": "今までを振り返って運がいい方と思いますか悪い方と思いますか。",                        "question_en": "Looking back on your life, do you consider yourself lucky or unlucky?",                                                   "time_limit": 120},
            {"id": 12, "question_ja": "当法人に入職したときに叶えたいことやチャレンジしたいことを教えてください。",           "question_en": "What do you hope to achieve or challenge yourself with when joining our organization?",                                   "time_limit": 180},
        ]
        job = Job(
            company_id=company.id,
            title="テスト",
            description="医療法人社団モルゲンロートの採用面接",
            evaluation_criteria="医療・福祉現場で求められる協調性・思いやり・責任感を重点的に評価してください。",
            questions=questions,
            max_duration_minutes=35,
            is_active=True,
        )
        db.session.add(job)
        print("[seed] Job created with 12 questions")

    db.session.commit()
