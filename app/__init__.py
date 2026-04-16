import os
from flask import Flask
from flask_login import LoginManager
from .models import db, Account


def create_app():
    app = Flask(__name__, template_folder="templates", static_folder="static")

    # DATABASE_URL を psycopg3 形式に変換
    db_url = os.environ.get("DATABASE_URL", "sqlite:///morgenrot.db")
    db_url = db_url.replace("postgres://", "postgresql://")
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

    from .routes.admin import bp as admin_bp
    from .routes.interview import bp as interview_bp
    from .routes.api import bp as api_bp

    app.register_blueprint(admin_bp, url_prefix="/admin")
    app.register_blueprint(interview_bp, url_prefix="/interview")
    app.register_blueprint(api_bp, url_prefix="/api")

    with app.app_context():
        db.create_all()
        _seed_admin()

    return app


def _seed_admin():
    from .models import Account, db

    admin_email = os.environ.get("ADMIN_EMAIL", "admin@morgenrot.jp")
    admin_password = os.environ.get("ADMIN_PASSWORD", "changeme")

    if not Account.query.filter_by(email=admin_email).first():
        admin = Account(
            company_id=1,
            name="システム管理者",
            email=admin_email,
            role="admin",
        )
        admin.set_password(admin_password)
        from .models import Company
        if not Company.query.first():
            company = Company(name="管理法人", is_active=True)
            db.session.add(company)
            db.session.flush()
            admin.company_id = company.id
        db.session.add(admin)
        db.session.commit()
        print(f"[seed] Admin created: {admin_email}")
