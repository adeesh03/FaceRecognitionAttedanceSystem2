"""Flask application for the face-recognition attendance system."""

from __future__ import annotations

import base64
import os
import pickle
import secrets
from contextlib import contextmanager
from datetime import datetime, timedelta
from functools import wraps
from io import BytesIO
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
    session,
    url_for,
)
from mysql.connector import Error, IntegrityError
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.security import check_password_hash

from database import get_connection

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
ENCODINGS_FILE = BASE_DIR / "encodings" / "encodings.pkl"
QR_FILE = BASE_DIR / "static" / "qr" / "current_qr.png"


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32),
        MAX_CONTENT_LENGTH=5 * 1024 * 1024,
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


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("admin"):
            flash("Please sign in to continue.", "warning")
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


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

    @app.before_request
    def protect_post_requests():
        if request.method == "POST":
            _validate_csrf()

    @app.get("/")
    def home():
        return redirect(url_for("dashboard" if session.get("admin") else "login"))

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if session.get("admin"):
            return redirect(url_for("dashboard"))
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            configured_username = os.environ.get("ADMIN_USERNAME")
            password_hash = os.environ.get("ADMIN_PASSWORD_HASH")
            plain_password = os.environ.get("ADMIN_PASSWORD")
            if not configured_username or not (password_hash or plain_password):
                app.logger.error("ADMIN_USERNAME and an admin password are not configured")
                abort(503, description="Administrator credentials are not configured.")
            password_valid = (
                check_password_hash(password_hash, password)
                if password_hash
                else secrets.compare_digest(password, plain_password)
            )
            if secrets.compare_digest(username, configured_username) and password_valid:
                session.clear()
                session["admin"] = True
                session.permanent = True
                _csrf_token()
                flash("Welcome back.", "success")
                return redirect(url_for("dashboard"))
            flash("The username or password is incorrect.", "danger")
        return render_template("login.html")

    @app.post("/logout")
    @admin_required
    def logout():
        session.clear()
        flash("You have been signed out.", "info")
        return redirect(url_for("login"))

    @app.get("/dashboard")
    @admin_required
    def dashboard():
        with database_cursor() as (_, cursor):
            cursor.execute("SELECT COUNT(*) FROM students")
            total_students = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM attendance")
            total_attendance = cursor.fetchone()[0]
            cursor.execute(
                "SELECT COUNT(DISTINCT student_id) FROM attendance WHERE attendance_date=CURDATE()"
            )
            present_today = cursor.fetchone()[0]
            cursor.execute(
                """
                SELECT id, pin, expires_at FROM attendance_session
                WHERE is_active=TRUE AND expires_at > NOW()
                ORDER BY id DESC LIMIT 1
                """
            )
            active_session = cursor.fetchone()
        return render_template(
            "dashboard.html",
            total_students=total_students,
            total_attendance=total_attendance,
            present_today=present_today,
            active_session=active_session,
            qr_exists=QR_FILE.exists(),
        )

    @app.route("/register", methods=["GET", "POST"])
    @admin_required
    def register():
        if request.method == "POST":
            student_id = request.form.get("student_id", "").strip().upper()
            name = request.form.get("name", "").strip()
            department = request.form.get("department", "").strip()
            email = request.form.get("email", "").strip().lower()
            if not all((student_id, name, department, email)):
                flash("All fields are required.", "danger")
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
                    connection.commit()
            except IntegrityError:
                flash("That student ID or email is already registered.", "danger")
                return render_template("register.html"), 409
            flash(f"Student {student_id} was registered successfully.", "success")
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

    @app.get("/attendance")
    @admin_required
    def attendance():
        with database_cursor() as (_, cursor):
            cursor.execute(
                """
                SELECT a.id, a.student_id, a.attendance_date, a.attendance_time,
                       a.status, a.session_id, s.session_name
                FROM attendance a
                LEFT JOIN attendance_session s ON s.id=a.session_id
                ORDER BY a.attendance_date DESC, a.attendance_time DESC
                """
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
    @admin_required
    def attendance_percentage():
        with database_cursor() as (_, cursor):
            cursor.execute(
                """
                SELECT COUNT(*) FROM attendance_session
                WHERE is_active=FALSE OR expires_at <= NOW()
                """
            )
            total_sessions = cursor.fetchone()[0]
            cursor.execute(
                """
                SELECT s.student_id, s.name,
                       COUNT(DISTINCT completed.id) AS attended_sessions
                FROM students s
                LEFT JOIN attendance a ON a.student_id=s.student_id
                LEFT JOIN attendance_session completed
                  ON completed.id=a.session_id
                 AND (completed.is_active=FALSE OR completed.expires_at <= NOW())
                GROUP BY s.student_id, s.name ORDER BY s.student_id
                """
            )
            rows = cursor.fetchall()
        report = [
            (
                student_id,
                name,
                attended,
                total_sessions,
                round(attended / total_sessions * 100, 1) if total_sessions else 0,
            )
            for student_id, name, attended in rows
        ]
        return render_template("attendance_percentage.html", data=report)

    @app.get("/analytics")
    @admin_required
    def analytics():
        with database_cursor() as (_, cursor):
            cursor.execute(
                """
                SELECT s.student_id, COUNT(a.id)
                FROM students s LEFT JOIN attendance a ON a.student_id=s.student_id
                GROUP BY s.student_id ORDER BY s.student_id
                """
            )
            chart_data = cursor.fetchall()
        return render_template(
            "analytics.html",
            labels=[row[0] for row in chart_data],
            values=[row[1] for row in chart_data],
        )

    @app.post("/generate_pin")
    @admin_required
    def generate_pin():
        pin = f"{secrets.randbelow(1_000_000):06d}"
        expiry = datetime.now() + timedelta(minutes=5)
        with database_cursor() as (connection, cursor):
            cursor.execute("UPDATE attendance_session SET is_active=FALSE")
            cursor.execute(
                """
                INSERT INTO attendance_session (pin, is_active, expires_at, session_name)
                VALUES (%s, TRUE, %s, %s)
                """,
                (
                    pin,
                    expiry,
                    f"{app.config['INSTITUTION_NAME']} · {datetime.now():%d %b %Y, %H:%M}",
                ),
            )
            session_id = cursor.lastrowid
            connection.commit()
        base_url = _public_base_url()
        attendance_url = f"{base_url}{url_for('student_attendance', session_id=session_id)}"
        QR_FILE.parent.mkdir(parents=True, exist_ok=True)
        qrcode.make(attendance_url).save(QR_FILE)
        flash("A new five-minute attendance session is active.", "success")
        return redirect(url_for("dashboard"))

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
                    SELECT 1 FROM attendance_session
                    WHERE id=%s AND pin=%s AND is_active=TRUE AND expires_at > NOW()
                    """,
                    (session_id, pin),
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
        encodings = face_recognition.face_encodings(image)
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
