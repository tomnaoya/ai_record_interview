"""管理画面ルート"""
from datetime import datetime, timezone
from flask import (Blueprint, abort, flash, redirect,
                   render_template, request, url_for, send_file)
from flask_login import current_user, login_required, login_user, logout_user
import json, os

from app.models import Account, Applicant, Company, InterviewSession, Job, db

bp = Blueprint("admin", __name__)

# ── 認証 ─────────────────────────────────────────────────────────────────────

@bp.get("/login")
def login():
    return render_template("admin/login.html")

@bp.post("/login")
def login_post():
    email = request.form.get("email", "")
    password = request.form.get("password", "")
    user = Account.query.filter_by(email=email, is_active=True).first()
    if not user or not user.check_password(password):
        flash("メールアドレスまたはパスワードが違います", "error")
        return redirect(url_for("admin.login"))
    user.last_login_at = datetime.now(timezone.utc)
    db.session.commit()
    login_user(user)
    return redirect(url_for("admin.dashboard"))

@bp.get("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("admin.login"))

# ── ダッシュボード ────────────────────────────────────────────────────────────

@bp.get("/")
@bp.get("")
@login_required
def dashboard():
    recent = (InterviewSession.query
              .order_by(InterviewSession.created_at.desc()).limit(10).all())
    stats = {
        "companies": Company.query.filter_by(is_active=True).count(),
        "jobs":      Job.query.filter_by(is_active=True).count(),
        "applicants": Applicant.query.count(),
        "completed": InterviewSession.query.filter(
            InterviewSession.status.in_(["completed", "evaluated"])
        ).count(),
    }
    return render_template("admin/dashboard.html", recent=recent, stats=stats)

# ── 企業情報管理 ──────────────────────────────────────────────────────────────

@bp.get("/companies")
@login_required
def companies():
    return render_template("admin/companies.html",
                           companies=Company.query.order_by(Company.created_at.desc()).all())

@bp.get("/companies/new")
@login_required
def company_new():
    return render_template("admin/company_form.html", company=None)

@bp.post("/companies/new")
@login_required
def company_create():
    c = Company(name=request.form["name"],
                name_kana=request.form.get("name_kana") or None,
                industry=request.form.get("industry") or None,
                size=request.form.get("size") or None,
                phone=request.form.get("phone") or None)
    db.session.add(c); db.session.commit()
    flash("企業を登録しました", "success")
    return redirect(url_for("admin.companies"))

@bp.get("/companies/<int:cid>/edit")
@login_required
def company_edit(cid: int):
    return render_template("admin/company_form.html", company=Company.query.get_or_404(cid))

@bp.post("/companies/<int:cid>/edit")
@login_required
def company_update(cid: int):
    c = Company.query.get_or_404(cid)
    c.name=request.form["name"]; c.name_kana=request.form.get("name_kana") or None
    c.industry=request.form.get("industry") or None; c.size=request.form.get("size") or None
    c.phone=request.form.get("phone") or None
    db.session.commit()
    flash("企業情報を更新しました", "success")
    return redirect(url_for("admin.companies"))

@bp.post("/companies/<int:cid>/toggle")
@login_required
def company_toggle(cid: int):
    c = Company.query.get_or_404(cid)
    c.is_active = not c.is_active; db.session.commit()
    flash(f"企業を{'有効化' if c.is_active else '無効化'}しました", "success")
    return redirect(url_for("admin.companies"))

# ── 企業アカウント管理 ────────────────────────────────────────────────────────

@bp.get("/accounts")
@login_required
def accounts():
    return render_template("admin/accounts.html",
                           accounts=Account.query.order_by(Account.created_at.desc()).all())

@bp.get("/accounts/new")
@login_required
def account_new():
    return render_template("admin/account_form.html", account=None,
                           companies=Company.query.filter_by(is_active=True).all())

@bp.post("/accounts/new")
@login_required
def account_create():
    pw = request.form.get("password", "")
    if pw != request.form.get("password_confirm", ""):
        flash("パスワードが一致しません", "error"); return redirect(url_for("admin.account_new"))
    if len(pw) < 8:
        flash("パスワードは8文字以上で入力してください", "error"); return redirect(url_for("admin.account_new"))
    if Account.query.filter_by(email=request.form["email"]).first():
        flash("そのメールアドレスは既に使用されています", "error"); return redirect(url_for("admin.account_new"))
    a = Account(company_id=int(request.form["company_id"]), name=request.form["name"],
                email=request.form["email"], role=request.form.get("role", "company"))
    a.set_password(pw); db.session.add(a); db.session.commit()
    flash("アカウントを作成しました", "success")
    return redirect(url_for("admin.accounts"))

@bp.get("/accounts/<int:aid>/edit")
@login_required
def account_edit(aid: int):
    return render_template("admin/account_form.html", account=Account.query.get_or_404(aid),
                           companies=Company.query.filter_by(is_active=True).all())

@bp.post("/accounts/<int:aid>/edit")
@login_required
def account_update(aid: int):
    a = Account.query.get_or_404(aid)
    a.company_id=int(request.form["company_id"]); a.name=request.form["name"]
    a.email=request.form["email"]; a.role=request.form.get("role", "company")
    pw = request.form.get("password", "")
    if pw:
        if pw != request.form.get("password_confirm", ""):
            flash("パスワードが一致しません", "error"); return redirect(url_for("admin.account_edit", aid=aid))
        if len(pw) < 8:
            flash("パスワードは8文字以上", "error"); return redirect(url_for("admin.account_edit", aid=aid))
        a.set_password(pw)
    db.session.commit()
    flash("アカウントを更新しました", "success")
    return redirect(url_for("admin.accounts"))

@bp.post("/accounts/<int:aid>/toggle")
@login_required
def account_toggle(aid: int):
    a = Account.query.get_or_404(aid)
    if a.id == current_user.id:
        flash("自分自身のアカウントは変更できません", "error")
        return redirect(url_for("admin.accounts"))
    a.is_active = not a.is_active; db.session.commit()
    flash(f"アカウントを{'有効化' if a.is_active else '無効化'}しました", "success")
    return redirect(url_for("admin.accounts"))

# ── 求人管理 ──────────────────────────────────────────────────────────────────

@bp.get("/jobs")
@login_required
def jobs():
    return render_template("admin/jobs.html",
                           jobs=Job.query.order_by(Job.created_at.desc()).all())

@bp.get("/jobs/new")
@login_required
def job_new():
    return render_template("admin/job_form.html", job=None,
                           companies=Company.query.filter_by(is_active=True).all())

@bp.post("/jobs/new")
@login_required
def job_create():
    qs = [q.strip() for q in request.form.get("questions","").splitlines() if q.strip()]
    j = Job(company_id=int(request.form["company_id"]), title=request.form["title"],
            description=request.form.get("description") or None,
            requirements=request.form.get("requirements") or None,
            evaluation_criteria=request.form.get("evaluation_criteria") or None,
            questions=qs,
            max_duration_minutes=int(request.form.get("max_duration_minutes", 30)))
    db.session.add(j); db.session.commit()
    flash("求人を登録しました", "success")
    return redirect(url_for("admin.jobs"))

@bp.get("/jobs/<int:jid>/edit")
@login_required
def job_edit(jid: int):
    return render_template("admin/job_form.html", job=Job.query.get_or_404(jid),
                           companies=Company.query.filter_by(is_active=True).all())

@bp.post("/jobs/<int:jid>/edit")
@login_required
def job_update(jid: int):
    j = Job.query.get_or_404(jid)
    qs = [q.strip() for q in request.form.get("questions","").splitlines() if q.strip()]
    j.company_id=int(request.form["company_id"]); j.title=request.form["title"]
    j.description=request.form.get("description") or None
    j.requirements=request.form.get("requirements") or None
    j.evaluation_criteria=request.form.get("evaluation_criteria") or None
    j.questions=qs; j.max_duration_minutes=int(request.form.get("max_duration_minutes", 30))
    db.session.commit()
    flash("求人情報を更新しました", "success")
    return redirect(url_for("admin.jobs"))

@bp.post("/jobs/<int:jid>/toggle")
@login_required
def job_toggle(jid: int):
    j = Job.query.get_or_404(jid)
    j.is_active = not j.is_active; db.session.commit()
    flash(f"求人を{'有効化' if j.is_active else '無効化'}しました", "success")
    return redirect(url_for("admin.jobs"))

# ── 応募者管理 ────────────────────────────────────────────────────────────────

@bp.get("/applicants")
@login_required
def applicants():
    return render_template("admin/applicants.html",
                           applicants=Applicant.query.order_by(Applicant.created_at.desc()).all())

@bp.get("/applicants/new")
@login_required
def applicant_new():
    return render_template("admin/applicant_form.html",
                           jobs=Job.query.filter_by(is_active=True).all())

@bp.post("/applicants/new")
@login_required
def applicant_create():
    a = Applicant(job_id=int(request.form["job_id"]), name=request.form["name"],
                  email=request.form.get("email") or None,
                  phone=request.form.get("phone") or None)
    db.session.add(a); db.session.flush()
    db.session.add(InterviewSession(applicant_id=a.id))
    db.session.commit()
    flash("応募者を登録し面接リンクを発行しました", "success")
    return redirect(url_for("admin.applicant_detail", aid=a.id))

@bp.get("/applicants/<int:aid>")
@login_required
def applicant_detail(aid: int):
    return render_template("admin/applicant_detail.html",
                           applicant=Applicant.query.get_or_404(aid))

@bp.post("/applicants/<int:aid>/issue_link")
@login_required
def issue_interview_link(aid: int):
    a = Applicant.query.get_or_404(aid)
    db.session.add(InterviewSession(applicant_id=a.id)); db.session.commit()
    flash("新しい面接リンクを発行しました", "success")
    return redirect(url_for("admin.applicant_detail", aid=aid))

# ── 面接履歴 ──────────────────────────────────────────────────────────────────

@bp.get("/interview-history")
@login_required
def interview_history():
    return render_template("admin/interview_history.html",
                           sessions=InterviewSession.query.order_by(
                               InterviewSession.created_at.desc()).all())

@bp.get("/interview-history/<int:sid>")
@login_required
def interview_detail(sid: int):
    s = InterviewSession.query.get_or_404(sid)
    evaluation = None
    if s.ai_evaluation:
        try: evaluation = json.loads(s.ai_evaluation)
        except Exception: pass
    return render_template("admin/interview_detail.html", session=s, evaluation=evaluation)

@bp.get("/interview-history/<int:sid>/video")
@login_required
def serve_video(sid: int):
    s = InterviewSession.query.get_or_404(sid)
    if not s.video_path or not os.path.exists(s.video_path):
        abort(404)
    return send_file(s.video_path, mimetype="video/webm", as_attachment=False, conditional=True)

@bp.post("/interview-history/<int:sid>/evaluate")
@login_required
def re_evaluate(sid: int):
    import threading
    from flask import current_app
    s = InterviewSession.query.get_or_404(sid)
    if not s.video_path:
        flash("動画がないため評価できません", "error")
        return redirect(url_for("admin.interview_detail", sid=sid))
    app = current_app._get_current_object()
    def _run():
        with app.app_context():
            from app.services.ai_evaluation import run_evaluation_pipeline
            try: run_evaluation_pipeline(sid)
            except Exception as e: print(f"[re-evaluate] {e}")
    threading.Thread(target=_run, daemon=True).start()
    flash("AI評価を再実行しました。しばらくお待ちください。", "success")
    return redirect(url_for("admin.interview_detail", sid=sid))

# ── プライバシーポリシー ──────────────────────────────────────────────────────

@bp.get("/privacy")
@login_required
def privacy():
    return render_template("admin/privacy.html", now=datetime.now())
