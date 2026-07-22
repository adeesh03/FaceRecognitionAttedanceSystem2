"""Flask application for the face-recognition attendance system."""

from __future__ import annotations

import base64
import csv
import json
import os
import pickle
import re
import secrets
from contextlib import contextmanager
from datetime import datetime, timedelta
from functools import wraps
from io import BytesIO, StringIO
from pathlib import Path

import qrcode
from dotenv import load_dotenv
from flask import (
    Flask,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    Response,
    session,
    url_for,
)
from mysql.connector import Error, IntegrityError
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.security import check_password_hash, generate_password_hash

from database import get_connection
from modules.face_store import FacePhotoError, prepare_photos, rebuild_student_model, store_photos

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
ENCODINGS_FILE = BASE_DIR / "encodings" / "encodings.pkl"
DATASET_DIR = BASE_DIR / "dataset"
QR_DIR = BASE_DIR / "static" / "qr"


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32),
        MAX_CONTENT_LENGTH=25 * 1024 * 1024,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "0") == "1",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
        FACE_MATCH_TOLERANCE=float(os.environ.get("FACE_MATCH_TOLERANCE", "0.5")),
        INSTITUTION_NAME=os.environ.get("INSTITUTION_NAME", "JAIN University"),
        APPLICATION_NAME=os.environ.get("APPLICATION_NAME", "Smart Attendance Portal"),
    )
    if test_config:
        app.config.update(test_config)

    register_routes(app)
    register_error_handlers(app)
    return app


@contextmanager
def database_cursor(dictionary: bool = False):
    connection = get_connection()
    cursor = connection.cursor(dictionary=dictionary)
    try:
        yield connection, cursor
    finally:
        cursor.close()
        connection.close()


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if session.get("role") not in {"admin", "lecturer"}:
            flash("Please sign in to continue.", "warning")
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("role"):
            flash("Please sign in to continue.", "warning")
            return redirect(url_for("login", next=request.path))
        if session.get("role") != "admin":
            abort(403, description="Administrator access is required.")
        return view(*args, **kwargs)
    return wrapped


def staff_required(view):
    return login_required(view)


def _course_scope(alias: str = "c") -> tuple[str, tuple]:
    if session.get("role") == "lecturer":
        return f" AND {alias}.lecturer_id=%s", (session.get("user_id"),)
    return "", ()


def _audit(cursor, action: str, entity_type: str, entity_id=None, details=None) -> None:
    cursor.execute(
        """INSERT INTO audit_logs
           (actor_type, actor_id, action, entity_type, entity_id, details, ip_address)
           VALUES (%s,%s,%s,%s,%s,%s,%s)""",
        (session.get("role", "public"), str(session.get("user_id") or session.get("display_name") or "anonymous"),
         action, entity_type, str(entity_id) if entity_id is not None else None,
         json.dumps(details, ensure_ascii=False) if isinstance(details, dict) else details,
         request.headers.get("X-Forwarded-For", request.remote_addr)),
    )


def _csrf_token() -> str:
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return session["csrf_token"]


def _validate_csrf() -> None:
    supplied = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token", "")
    expected = session.get("csrf_token", "")
    if not expected or not secrets.compare_digest(supplied, expected):
        abort(400, description="Invalid or missing security token. Refresh the page and try again.")


def _public_base_url() -> str:
    """Return the explicitly configured externally reachable application URL."""
    configured = os.environ.get("PUBLIC_BASE_URL")
    if not configured:
        abort(503, description="PUBLIC_BASE_URL is not configured.")
    if current_app.config["SESSION_COOKIE_SECURE"] and not configured.startswith("https://"):
        abort(503, description="PUBLIC_BASE_URL must use HTTPS when secure cookies are enabled.")
    return configured.rstrip("/")


def register_routes(app: Flask) -> None:
    app.jinja_env.globals["csrf_token"] = _csrf_token
    app.jinja_env.globals["current_year"] = datetime.now().year
    app.jinja_env.globals["now"] = datetime.now()

    @app.before_request
    def protect_post_requests():
        if request.method == "POST":
            _validate_csrf()

    @app.get("/")
    def home():
        return redirect(url_for("dashboard" if session.get("role") else "login"))

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if session.get("role"):
            return redirect(url_for("dashboard"))
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            configured_username = os.environ.get("ADMIN_USERNAME")
            password_hash = os.environ.get("ADMIN_PASSWORD_HASH")
            plain_password = os.environ.get("ADMIN_PASSWORD")
            password_valid = False
            if configured_username and (password_hash or plain_password):
                password_valid = check_password_hash(password_hash, password) if password_hash else secrets.compare_digest(password, plain_password)
            if configured_username and secrets.compare_digest(username, configured_username) and password_valid:
                session.clear()
                session.update(role="admin", display_name="Administrator")
                session.permanent = True
                _csrf_token()
                flash("Welcome back.", "success")
                return redirect(url_for("dashboard"))
            with database_cursor(dictionary=True) as (_, cursor):
                cursor.execute("SELECT id, staff_id, name, password_hash FROM lecturers WHERE is_active=TRUE AND (staff_id=%s OR email=%s)", (username.upper(), username.lower()))
                lecturer = cursor.fetchone()
            if lecturer and check_password_hash(lecturer["password_hash"], password):
                session.clear()
                session.update(role="lecturer", user_id=lecturer["id"], display_name=lecturer["name"], staff_id=lecturer["staff_id"])
                session.permanent = True
                _csrf_token()
                flash(f"Welcome, {lecturer['name']}.", "success")
                return redirect(url_for("dashboard"))
            flash("The username or password is incorrect.", "danger")
        return render_template("login.html")

    @app.post("/logout")
    @login_required
    def logout():
        session.clear()
        flash("You have been signed out.", "info")
        return redirect(url_for("login"))

    @app.get("/dashboard")
    @login_required
    def dashboard():
        scope, params = _course_scope()
        with database_cursor() as (_, cursor):
            cursor.execute("SELECT COUNT(*) FROM students")
            total_students = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM attendance a LEFT JOIN attendance_session ats ON ats.id=a.session_id LEFT JOIN courses c ON c.id=ats.course_id WHERE 1=1" + scope, params)
            total_attendance = cursor.fetchone()[0]
            cursor.execute(
                "SELECT COUNT(DISTINCT a.student_id) FROM attendance a LEFT JOIN attendance_session ats ON ats.id=a.session_id LEFT JOIN courses c ON c.id=ats.course_id WHERE a.attendance_date=CURDATE()" + scope, params
            )
            present_today = cursor.fetchone()[0]
            cursor.execute(
                """SELECT ats.id, ats.pin, ats.expires_at, c.course_code, c.course_name
                   FROM attendance_session ats JOIN courses c ON c.id=ats.course_id
                   WHERE ats.is_active=TRUE AND ats.expires_at>NOW()""" + scope + " ORDER BY ats.id DESC", params
            )
            active_sessions = cursor.fetchall()
            cursor.execute("SELECT c.id,c.course_code,c.course_name FROM courses c WHERE c.is_active=TRUE" + scope + " ORDER BY c.course_code", params)
            courses = cursor.fetchall()
        return render_template(
            "dashboard.html",
            total_students=total_students,
            total_attendance=total_attendance,
            present_today=present_today,
            active_sessions=active_sessions, courses=courses,
        )

    @app.route("/register", methods=["GET", "POST"])
    @admin_required
    def register():
        if request.method == "POST":
            student_id = request.form.get("student_id", "").strip().upper()
            name = request.form.get("name", "").strip()
            department = request.form.get("department", "").strip()
            email = request.form.get("email", "").strip().lower()
            photos = request.files.getlist("face_photos")
            if not all((student_id, name, department, email)):
                flash("All fields are required.", "danger")
                return render_template("register.html"), 400
            if not re.fullmatch(r"[A-Z0-9_-]{2,40}", student_id):
                flash("Student ID may contain only letters, numbers, hyphens, and underscores.", "danger")
                return render_template("register.html"), 400
            try:
                prepared_photos = prepare_photos(photos)
            except FacePhotoError as error:
                flash(str(error), "danger")
                return render_template("register.html"), 400
            try:
                with database_cursor() as (connection, cursor):
                    cursor.execute(
                        """
                        INSERT INTO students (student_id, name, department, email)
                        VALUES (%s, %s, %s, %s)
                        """,
                        (student_id, name, department, email),
                    )
                    _audit(cursor, "student.created", "student", student_id, {"name": name})
                    connection.commit()
            except IntegrityError:
                flash("That student ID or email is already registered.", "danger")
                return render_template("register.html"), 409
            try:
                store_photos(DATASET_DIR, student_id, prepared_photos)
                encoded = rebuild_student_model(DATASET_DIR, ENCODINGS_FILE, student_id)
            except (FacePhotoError, OSError, pickle.UnpicklingError) as error:
                app.logger.exception("Face enrolment failed for %s", student_id)
                flash(f"Student was registered, but face enrolment failed: {error}", "warning")
                return redirect(url_for("student_faces", student_id=student_id))
            flash(f"Student {student_id} was registered with {encoded} face encodings.", "success")
            return redirect(url_for("students"))
        return render_template("register.html")

    @app.get("/students")
    @admin_required
    def students():
        with database_cursor() as (_, cursor):
            cursor.execute(
                "SELECT id, student_id, name, department, email FROM students ORDER BY student_id"
            )
            rows = cursor.fetchall()
        return render_template("students.html", students=rows)

    @app.route("/students/<student_id>/faces", methods=["GET", "POST"])
    @admin_required
    def student_faces(student_id):
        student_id = student_id.strip().upper()
        with database_cursor(dictionary=True) as (_, cursor):
            cursor.execute("SELECT student_id,name,department FROM students WHERE student_id=%s", (student_id,))
            student = cursor.fetchone()
        if not student:
            abort(404, description="Student was not found.")
        folder = DATASET_DIR / student_id
        photo_count = sum(1 for path in folder.glob("*") if path.suffix.lower() in {".jpg", ".jpeg", ".png"}) if folder.exists() else 0
        if request.method == "POST":
            try:
                prepared = prepare_photos(request.files.getlist("face_photos"))
                added = store_photos(DATASET_DIR, student_id, prepared)
                encoded = rebuild_student_model(DATASET_DIR, ENCODINGS_FILE, student_id)
                with database_cursor() as (connection, cursor):
                    _audit(cursor, "face_photos.added", "student", student_id, {"added": added, "total_encodings": encoded})
                    connection.commit()
                flash(f"Added {added} photos. The model now has {encoded} encodings for {student_id}.", "success")
                return redirect(url_for("student_faces", student_id=student_id))
            except (FacePhotoError, OSError, pickle.UnpicklingError) as error:
                flash(str(error), "danger")
        return render_template("student_faces.html", student=student, photo_count=photo_count)

    @app.get("/attendance")
    @staff_required
    def attendance():
        scope, params = _course_scope()
        with database_cursor() as (_, cursor):
            cursor.execute(
                """
                SELECT a.id, a.student_id, a.attendance_date, a.attendance_time,
                       a.status, a.session_id, ats.session_name, c.course_code
                FROM attendance a
                LEFT JOIN attendance_session ats ON ats.id=a.session_id
                LEFT JOIN courses c ON c.id=ats.course_id WHERE 1=1
                """ + scope + " ORDER BY a.attendance_date DESC, a.attendance_time DESC", params
            )
            rows = cursor.fetchall()
        return render_template("attendance.html", attendance=rows)

    @app.route("/search_attendance", methods=["GET", "POST"])
    @admin_required
    def search_attendance():
        rows = []
        searched_id = ""
        if request.method == "POST":
            searched_id = request.form.get("student_id", "").strip().upper()
            with database_cursor() as (_, cursor):
                cursor.execute(
                    """
                    SELECT a.id, a.student_id, a.attendance_date, a.attendance_time,
                           a.status, a.session_id, s.session_name
                    FROM attendance a
                    LEFT JOIN attendance_session s ON s.id=a.session_id
                    WHERE a.student_id=%s
                    ORDER BY a.attendance_date DESC, a.attendance_time DESC
                    """,
                    (searched_id,),
                )
                rows = cursor.fetchall()
        return render_template(
            "search_attendance.html", attendance=rows, searched_id=searched_id
        )

    @app.get("/attendance_percentage")
    @staff_required
    def attendance_percentage():
        selected = request.args.get("course_id", type=int)
        scope, params = _course_scope()
        course_filter = " AND c.id=%s" if selected else ""
        values = params + ((selected,) if selected else ())
        with database_cursor(dictionary=True) as (_, cursor):
            cursor.execute(
                """SELECT c.course_code,c.course_name,s.student_id,s.name,
                    COUNT(DISTINCT CASE WHEN ats.id IS NOT NULL AND (ats.is_active=FALSE OR ats.expires_at<=NOW()) THEN ats.id END) total_sessions,
                    COUNT(DISTINCT CASE WHEN (ats.is_active=FALSE OR ats.expires_at<=NOW()) AND a.id IS NOT NULL THEN ats.id END) attended_sessions
                    FROM course_enrollments e JOIN courses c ON c.id=e.course_id
                    JOIN students s ON s.student_id=e.student_id
                    LEFT JOIN attendance_session ats ON ats.course_id=c.id
                    LEFT JOIN attendance a ON a.session_id=ats.id AND a.student_id=s.student_id
                    WHERE 1=1""" + scope + course_filter +
                " GROUP BY c.id,c.course_code,c.course_name,s.student_id,s.name ORDER BY c.course_code,s.student_id", values
            )
            rows = cursor.fetchall()
            cursor.execute("SELECT c.id,c.course_code,c.course_name FROM courses c WHERE c.is_active=TRUE" + scope + " ORDER BY c.course_code", params)
            courses = cursor.fetchall()
        for row in rows:
            row["percentage"] = round(row["attended_sessions"] / row["total_sessions"] * 100, 1) if row["total_sessions"] else 0
        return render_template("attendance_percentage.html", data=rows, courses=courses, selected=selected)

    @app.get("/analytics")
    @staff_required
    def analytics():
        scope, params = _course_scope()
        with database_cursor() as (_, cursor):
            cursor.execute(
                """
                SELECT st.student_id, COUNT(a.id) FROM students st
                LEFT JOIN attendance a ON a.student_id=st.student_id
                LEFT JOIN attendance_session ats ON ats.id=a.session_id
                LEFT JOIN courses c ON c.id=ats.course_id WHERE 1=1
                """ + scope + " GROUP BY st.student_id ORDER BY st.student_id", params
            )
            chart_data = cursor.fetchall()
        return render_template(
            "analytics.html",
            labels=[row[0] for row in chart_data],
            values=[row[1] for row in chart_data],
        )

    def _report_rows():
        scope, params = _course_scope()
        clauses=[]; values=list(params)
        if request.args.get("course_id",type=int): clauses.append("c.id=%s"); values.append(request.args.get("course_id",type=int))
        if request.args.get("student_id","").strip(): clauses.append("a.student_id=%s"); values.append(request.args["student_id"].strip().upper())
        if request.args.get("start",""): clauses.append("a.attendance_date>=%s"); values.append(request.args["start"])
        if request.args.get("end",""): clauses.append("a.attendance_date<=%s"); values.append(request.args["end"])
        extra=(" AND "+" AND ".join(clauses)) if clauses else ""
        with database_cursor(dictionary=True) as (_,cursor):
            cursor.execute("""SELECT a.student_id,st.name,c.course_code,c.course_name,ats.session_name,
                a.attendance_date,a.attendance_time,a.status FROM attendance a
                JOIN students st ON st.student_id=a.student_id JOIN attendance_session ats ON ats.id=a.session_id
                JOIN courses c ON c.id=ats.course_id WHERE 1=1"""+scope+extra+" ORDER BY a.attendance_date DESC,a.attendance_time DESC",tuple(values)); rows=cursor.fetchall()
            cursor.execute("SELECT c.id,c.course_code,c.course_name FROM courses c WHERE 1=1"+scope+" ORDER BY c.course_code",params); course_rows=cursor.fetchall()
        return rows,course_rows

    @app.get("/reports")
    @staff_required
    def reports():
        rows,course_rows=_report_rows()
        return render_template("reports.html",attendance=rows,courses=course_rows)

    @app.get("/reports/export.csv")
    @staff_required
    def report_export():
        rows,_=_report_rows(); output=StringIO(); writer=csv.writer(output); writer.writerow(["Student ID","Student","Course","Subject","Session","Date","Time","Status"])
        for r in rows: writer.writerow([r["student_id"],r["name"],r["course_code"],r["course_name"],r["session_name"],r["attendance_date"],r["attendance_time"],r["status"]])
        with database_cursor() as (connection,cursor): _audit(cursor,"report.exported","attendance_report",details={"rows":len(rows)}); connection.commit()
        return Response(output.getvalue(),mimetype="text/csv",headers={"Content-Disposition":"attachment; filename=attendance-report.csv"})

    @app.get("/audit-logs")
    @admin_required
    def audit_logs():
        action=request.args.get("action","").strip(); values=(); where=""
        if action: where=" WHERE action LIKE %s"; values=(f"%{action}%",)
        with database_cursor(dictionary=True) as (_,cursor): cursor.execute("SELECT * FROM audit_logs"+where+" ORDER BY id DESC LIMIT 500",values); rows=cursor.fetchall()
        return render_template("audit_logs.html",logs=rows,action=action)

    @app.post("/generate_pin")
    @staff_required
    def generate_pin():
        course_id = request.form.get("course_id", type=int)
        scope, params = _course_scope()
        with database_cursor(dictionary=True) as (_, cursor):
            cursor.execute("SELECT c.id,c.course_code,c.course_name FROM courses c WHERE c.id=%s AND c.is_active=TRUE" + scope, (course_id,) + params)
            course = cursor.fetchone()
        if not course:
            abort(403, description="Select a course assigned to your account.")
        pin = f"{secrets.randbelow(1_000_000):06d}"
        expiry = datetime.now() + timedelta(minutes=5)
        with database_cursor() as (connection, cursor):
            cursor.execute("UPDATE attendance_session SET is_active=FALSE WHERE course_id=%s", (course_id,))
            cursor.execute(
                """
                INSERT INTO attendance_session (pin, is_active, expires_at, session_name, course_id, created_by_lecturer_id)
                VALUES (%s, TRUE, %s, %s, %s, %s)
                """,
                (
                    pin,
                    expiry,
                    f"{course['course_code']} · {datetime.now():%d %b %Y, %H:%M}", course_id,
                    session.get("user_id") if session.get("role") == "lecturer" else None,
                ),
            )
            session_id = cursor.lastrowid
            _audit(cursor, "session.created", "attendance_session", session_id, {"course": course["course_code"]})
            connection.commit()
        base_url = _public_base_url()
        attendance_url = f"{base_url}{url_for('student_attendance', session_id=session_id)}"
        QR_DIR.mkdir(parents=True, exist_ok=True)
        qrcode.make(attendance_url).save(QR_DIR / f"session_{session_id}.png")
        flash("A new five-minute attendance session is active.", "success")
        return redirect(url_for("dashboard"))

    @app.get("/lecturers")
    @admin_required
    def lecturers():
        with database_cursor(dictionary=True) as (_, cursor):
            cursor.execute("SELECT l.*,COUNT(c.id) course_count FROM lecturers l LEFT JOIN courses c ON c.lecturer_id=l.id GROUP BY l.id ORDER BY l.name")
            rows = cursor.fetchall()
        return render_template("lecturers.html", lecturers=rows)

    @app.route("/lecturers/new", methods=["GET", "POST"])
    @admin_required
    def lecturer_new():
        if request.method == "POST":
            staff_id=request.form.get("staff_id","").strip().upper(); name=request.form.get("name","").strip(); email=request.form.get("email","").strip().lower(); password=request.form.get("password","")
            if not all((staff_id,name,email,password)) or len(password)<8:
                flash("Complete every field and use a password of at least 8 characters.", "danger")
            else:
                try:
                    with database_cursor() as (connection,cursor):
                        cursor.execute("INSERT INTO lecturers(staff_id,name,email,password_hash) VALUES(%s,%s,%s,%s)",(staff_id,name,email,generate_password_hash(password)))
                        _audit(cursor,"lecturer.created","lecturer",cursor.lastrowid,{"staff_id":staff_id}); connection.commit()
                    flash("Lecturer account created.","success"); return redirect(url_for("lecturers"))
                except IntegrityError: flash("That staff ID or email already exists.","danger")
        return render_template("lecturer_form.html")

    @app.post("/lecturers/<int:lecturer_id>/toggle")
    @admin_required
    def lecturer_toggle(lecturer_id):
        with database_cursor() as (connection,cursor):
            cursor.execute("UPDATE lecturers SET is_active=NOT is_active WHERE id=%s",(lecturer_id,)); _audit(cursor,"lecturer.toggled","lecturer",lecturer_id); connection.commit()
        flash("Lecturer status updated.","success"); return redirect(url_for("lecturers"))

    @app.get("/courses")
    @staff_required
    def courses():
        scope,params=_course_scope()
        with database_cursor(dictionary=True) as (_,cursor):
            cursor.execute("SELECT c.*,l.name lecturer_name,COUNT(DISTINCT e.id) enrolled,COUNT(DISTINCT ats.id) sessions FROM courses c LEFT JOIN lecturers l ON l.id=c.lecturer_id LEFT JOIN course_enrollments e ON e.course_id=c.id LEFT JOIN attendance_session ats ON ats.course_id=c.id WHERE 1=1"+scope+" GROUP BY c.id ORDER BY c.course_code",params); rows=cursor.fetchall()
        return render_template("courses.html",courses=rows)

    @app.route("/courses/new", methods=["GET","POST"])
    @admin_required
    def course_new():
        with database_cursor(dictionary=True) as (_,cursor): cursor.execute("SELECT id,staff_id,name FROM lecturers WHERE is_active=TRUE ORDER BY name"); lecturer_rows=cursor.fetchall()
        if request.method=="POST":
            code=request.form.get("course_code","").strip().upper(); name=request.form.get("course_name","").strip(); department=request.form.get("department","").strip(); semester=request.form.get("semester","").strip(); lecturer_id=request.form.get("lecturer_id",type=int)
            if not all((code,name,department,semester)):
                flash("All course fields are required.","danger")
            else:
                try:
                    with database_cursor() as (connection,cursor):
                        cursor.execute("INSERT INTO courses(course_code,course_name,department,semester,lecturer_id) VALUES(%s,%s,%s,%s,%s)",(code,name,department,semester,lecturer_id)); _audit(cursor,"course.created","course",cursor.lastrowid,{"code":code}); connection.commit()
                    flash("Course created.","success"); return redirect(url_for("courses"))
                except IntegrityError: flash("That course code already exists.","danger")
        return render_template("course_form.html",lecturers=lecturer_rows)

    @app.get("/courses/<int:course_id>")
    @staff_required
    def course_detail(course_id):
        scope,params=_course_scope()
        with database_cursor(dictionary=True) as (_,cursor):
            cursor.execute("SELECT c.*,l.name lecturer_name FROM courses c LEFT JOIN lecturers l ON l.id=c.lecturer_id WHERE c.id=%s"+scope,(course_id,)+params); course=cursor.fetchone()
            if not course: abort(404)
            cursor.execute("SELECT e.student_id,s.name,s.department FROM course_enrollments e JOIN students s ON s.student_id=e.student_id WHERE e.course_id=%s ORDER BY s.student_id",(course_id,)); enrolled=cursor.fetchall()
            cursor.execute("SELECT id,session_name,is_active,expires_at,created_at FROM attendance_session WHERE course_id=%s ORDER BY id DESC",(course_id,)); sessions=cursor.fetchall()
            cursor.execute("SELECT student_id,name FROM students WHERE student_id NOT IN (SELECT student_id FROM course_enrollments WHERE course_id=%s) ORDER BY student_id",(course_id,)); available=cursor.fetchall()
        return render_template("course_detail.html",course=course,enrolled=enrolled,sessions=sessions,available=available)

    @app.post("/courses/<int:course_id>/enroll")
    @admin_required
    def course_enroll(course_id):
        student_id=request.form.get("student_id","").strip().upper()
        try:
            with database_cursor() as (connection,cursor):
                cursor.execute("INSERT INTO course_enrollments(course_id,student_id) VALUES(%s,%s)",(course_id,student_id)); _audit(cursor,"student.enrolled","course",course_id,{"student_id":student_id}); connection.commit()
            flash("Student enrolled.","success")
        except IntegrityError: flash("Student is already enrolled or does not exist.","danger")
        return redirect(url_for("course_detail",course_id=course_id))

    @app.post("/courses/<int:course_id>/unenroll/<student_id>")
    @admin_required
    def course_unenroll(course_id,student_id):
        with database_cursor() as (connection,cursor): cursor.execute("DELETE FROM course_enrollments WHERE course_id=%s AND student_id=%s",(course_id,student_id)); _audit(cursor,"student.unenrolled","course",course_id,{"student_id":student_id}); connection.commit()
        flash("Student removed from course.","success"); return redirect(url_for("course_detail",course_id=course_id))

    @app.route("/student_attendance/<int:session_id>", methods=["GET", "POST"])
    def student_attendance(session_id: int):
        with database_cursor() as (_, cursor):
            cursor.execute(
                """
                SELECT id, expires_at FROM attendance_session
                WHERE id=%s AND is_active=TRUE AND expires_at > NOW()
                """,
                (session_id,),
            )
            active = cursor.fetchone()
        if not active:
            return render_template("student_attendance.html", expired=True), 410
        if request.method == "POST":
            student_id = request.form.get("student_id", "").strip().upper()
            pin = request.form.get("pin", "").strip()
            with database_cursor() as (_, cursor):
                cursor.execute("SELECT 1 FROM students WHERE student_id=%s", (student_id,))
                student_exists = cursor.fetchone() is not None
                cursor.execute(
                    """
                    SELECT 1 FROM attendance_session ats
                    JOIN course_enrollments e ON e.course_id=ats.course_id AND e.student_id=%s
                    WHERE ats.id=%s AND ats.pin=%s AND ats.is_active=TRUE AND ats.expires_at > NOW()
                    """,
                    (student_id, session_id, pin),
                )
                valid_pin = cursor.fetchone() is not None
            if student_exists and valid_pin:
                session["attendance_student_id"] = student_id
                session["attendance_session_id"] = session_id
                return redirect(url_for("student_camera"))
            flash("The student ID or attendance PIN is invalid.", "danger")
        return render_template("student_attendance.html", expired=False)

    @app.get("/student_camera")
    def student_camera():
        if not session.get("attendance_student_id") or not session.get("attendance_session_id"):
            abort(403, description="Validate an attendance PIN before using the camera.")
        return render_template(
            "student_camera.html", student_id=session["attendance_student_id"]
        )

    @app.post("/verify_face")
    def verify_face():
        import face_recognition

        student_id = session.get("attendance_student_id")
        session_id = session.get("attendance_session_id")
        if not student_id or not session_id:
            return jsonify(ok=False, message="Validate the attendance PIN first."), 403
        image_data = (request.get_json(silent=True) or {}).get("image", "")
        if not image_data.startswith("data:image/") or "," not in image_data:
            return jsonify(ok=False, message="The submitted image is invalid."), 400
        try:
            image_bytes = base64.b64decode(image_data.split(",", 1)[1], validate=True)
            image = face_recognition.load_image_file(BytesIO(image_bytes))
        except (ValueError, TypeError, OSError):
            return jsonify(ok=False, message="The submitted image could not be read."), 400
        face_locations = face_recognition.face_locations(
            image, number_of_times_to_upsample=2, model="hog"
        )
        encodings = face_recognition.face_encodings(
            image, known_face_locations=face_locations
        )
        if len(encodings) != 1:
            message = "No face was detected." if not encodings else "Only one face may be visible."
            return jsonify(ok=False, message=message), 400
        try:
            with ENCODINGS_FILE.open("rb") as file:
                trained = pickle.load(file)
        except (OSError, pickle.UnpicklingError, EOFError):
            return jsonify(ok=False, message="The recognition model is unavailable."), 503
        known_encodings = trained.get("encodings", [])
        known_names = trained.get("names", [])
        if not known_encodings or len(known_encodings) != len(known_names):
            return jsonify(ok=False, message="No trained faces are available."), 503
        distances = face_recognition.face_distance(known_encodings, encodings[0])
        index = int(distances.argmin())
        recognized_id = str(known_names[index]).strip().upper()
        if (
            distances[index] > app.config["FACE_MATCH_TOLERANCE"]
            or not secrets.compare_digest(recognized_id, student_id)
        ):
            return jsonify(ok=False, message="Face does not match the submitted student ID."), 403
        with database_cursor() as (connection, cursor):
            cursor.execute(
                """
                SELECT 1 FROM attendance_session
                WHERE id=%s AND is_active=TRUE AND expires_at > NOW()
                """,
                (session_id,),
            )
            if not cursor.fetchone():
                return jsonify(ok=False, message="The attendance session has expired."), 403
            cursor.execute(
                """
                INSERT INTO attendance
                    (student_id, session_id, attendance_date, attendance_time, status)
                VALUES (%s, %s, CURDATE(), CURTIME(), 'Present')
                ON DUPLICATE KEY UPDATE id=id
                """,
                (student_id, session_id),
            )
            already_marked = cursor.rowcount == 0
            _audit(cursor, "attendance.duplicate" if already_marked else "attendance.recorded", "attendance_session", session_id, {"student_id": student_id})
            connection.commit()
        session.pop("attendance_student_id", None)
        session.pop("attendance_session_id", None)
        message = (
            "Attendance was already recorded for this session."
            if already_marked
            else "Attendance recorded successfully."
        )
        return jsonify(ok=True, message=message, student_id=student_id)


def register_error_handlers(app: Flask) -> None:
    @app.errorhandler(RequestEntityTooLarge)
    def image_too_large(_error):
        if request.path == "/verify_face":
            return jsonify(ok=False, message="The image must be smaller than 5 MB."), 413
        return render_template("error.html", code=413, message="The submitted data is too large."), 413

    @app.errorhandler(400)
    @app.errorhandler(403)
    @app.errorhandler(404)
    @app.errorhandler(410)
    def handled_error(error):
        return render_template(
            "error.html", code=error.code, message=getattr(error, "description", "Request failed.")
        ), error.code

    @app.errorhandler(Error)
    def database_error(error):
        app.logger.exception("Database operation failed: %s", error)
        return render_template(
            "error.html", code=503, message="The database is temporarily unavailable."
        ), 503

    @app.errorhandler(500)
    def server_error(error):
        app.logger.exception("Unhandled application error: %s", error)
        return render_template(
            "error.html", code=500, message="An unexpected error occurred."
        ), 500


app = create_app()

if __name__ == "__main__":
    app.run(
        debug=os.environ.get("FLASK_DEBUG", "0") == "1",
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "5001")),
    )
