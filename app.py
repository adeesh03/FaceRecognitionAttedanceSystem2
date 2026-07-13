from flask import Flask, render_template, request, redirect, session
from database import get_connection
import random
import subprocess
from flask import (
    Flask,
    render_template,
    request,
    redirect,
    session
)
from datetime import datetime, timedelta
import os
import qrcode
app = Flask(__name__)
app.secret_key = "attendance_secret_key"

# =========================
# HOME PAGE
# =========================

@app.route("/")
def home():
    return render_template("register.html")


# =========================
# REGISTER STUDENT
# =========================

@app.route("/register", methods=["POST"])
def register():

    student_id = request.form["student_id"]
    name = request.form["name"]
    department = request.form["department"]
    email = request.form["email"]

    conn = get_connection()
    cursor = conn.cursor()

    sql = """
    INSERT INTO students
    (
        student_id,
        name,
        department,
        email
    )
    VALUES
    (%s,%s,%s,%s)
    """

    values = (
        student_id,
        name,
        department,
        email
    )

    cursor.execute(sql, values)

    conn.commit()

    cursor.close()
    conn.close()

    return f"""
    <h2>Student Registered Successfully</h2>

    <p><b>Student ID:</b> {student_id}</p>
    <p><b>Name:</b> {name}</p>
    <p><b>Department:</b> {department}</p>
    <p><b>Email:</b> {email}</p>

    <a href="/">Go Back</a>
    """


# =========================
# VIEW STUDENTS
# =========================

@app.route("/students")
def students():

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT * FROM students"
    )

    students_data = cursor.fetchall()

    cursor.close()
    conn.close()

    return render_template(
        "students.html",
        students=students_data
    )


# =========================
# VIEW ATTENDANCE
# =========================

@app.route("/attendance")
def attendance():

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT * FROM attendance"
    )

    attendance_data = cursor.fetchall()

    cursor.close()
    conn.close()

    return render_template(
        "attendance.html",
        attendance=attendance_data
    )


# =========================
# DASHBOARD
# =========================

@app.route("/dashboard")
def dashboard():

    if "admin" not in session:
        return redirect("/login")

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM students")
    total_students = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM attendance")
    total_attendance = cursor.fetchone()[0]

    cursor.execute("""
        SELECT COUNT(DISTINCT student_id)
        FROM attendance
        WHERE attendance_date = CURDATE()
    """)
    present_today = cursor.fetchone()[0]

    cursor.close()
    conn.close()

    return render_template(
        "dashboard.html",
        total_students=total_students,
        total_attendance=total_attendance,
        present_today=present_today
    )
@app.route("/search_attendance", methods=["GET", "POST"])
def search_attendance():

    attendance_data = []

    if request.method == "POST":

        student_id = request.form["student_id"].strip()

        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT *
            FROM attendance
            WHERE TRIM(student_id)=TRIM(%s)
            """,
            (student_id,)
        )

        attendance_data = cursor.fetchall()

        print("Search Result:", attendance_data)

        cursor.close()
        conn.close()

    return render_template(
        "search_attendance.html",
        attendance=attendance_data
    )

@app.route("/attendance_percentage")
def attendance_percentage():

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            student_id,
            COUNT(*) as present_days
        FROM attendance
        GROUP BY student_id
    """)

    data = cursor.fetchall()

    percentage_data = []

    TOTAL_WORKING_DAYS = 30

    for row in data:

        student_id = row[0]
        present_days = row[1]

        percentage = (
            present_days /
            TOTAL_WORKING_DAYS
        ) * 100

        percentage_data.append(
            (
                student_id,
                present_days,
                TOTAL_WORKING_DAYS,
                round(percentage, 2)
            )
        )

    cursor.close()
    conn.close()

    return render_template(
        "attendance_percentage.html",
        data=percentage_data
    )

@app.route("/analytics")
def analytics():

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT student_id,
        COUNT(*) as total
        FROM attendance
        GROUP BY student_id
    """)

    chart_data = cursor.fetchall()

    cursor.close()
    conn.close()

    labels = []
    values = []

    for row in chart_data:

        labels.append(row[0])
        values.append(row[1])

    return render_template(
        "analytics.html",
        labels=labels,
        values=values
    )

@app.route("/login", methods=["GET", "POST"])
def login():

    if request.method == "POST":

        username = request.form["username"]
        password = request.form["password"]

        if username == "admin" and password == "admin123":

            session["admin"] = True

            return redirect("/dashboard")

        return "Invalid Username or Password"

    return render_template("login.html")
@app.route("/logout")
def logout():

    session.pop("admin", None)

    return redirect("/login")
@app.route("/generate_pin")
def generate_pin():

    pin = str(random.randint(1000, 9999))

    expiry_time = datetime.now() + timedelta(minutes=5)

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE attendance_session
        SET is_active = FALSE
    """)

    session_name = (
        "Attendance Session "
        + datetime.now().strftime("%d-%m-%Y")
    )

    cursor.execute(
        """
        INSERT INTO attendance_session
        (
            pin,
            is_active,
            expires_at,
            session_name
        )
        VALUES
        (
            %s,
            TRUE,
            %s,
            %s
        )
        """,
        (
            pin,
            expiry_time,
            session_name
        )
    )

    conn.commit()

    session_id = cursor.lastrowid

    cursor.close()
    conn.close()

    os.makedirs(
        "static/qr",
        exist_ok=True
    )

    qr_url = (
        f"http://192.168.5.101:5001/student_attendance/{session_id}"
    )

    img = qrcode.make(qr_url)

    img.save(
        "static/qr/current_qr.png"
    )

    return f"""
    <h2>Attendance Session Created</h2>

    <h1>PIN : {pin}</h1>

    <h3>Session ID : {session_id}</h3>

    <p>Valid Until : {expiry_time}</p>

    <img
    src='/static/qr/current_qr.png'
    width='250'>

    <br><br>

    <a href='/dashboard'>
    Dashboard
    </a>
    """
@app.route(
    "/student_attendance/<int:session_id>",
    methods=["GET", "POST"]
)
def student_attendance(session_id):

    if request.method == "POST":

        student_id = request.form["student_id"]
        pin = request.form["pin"]

        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT *
            FROM attendance_session
            WHERE id = %s
              AND pin = %s
              AND is_active = TRUE
              AND expires_at > NOW()
            """,
            (
                session_id,
                pin
            )
        )

        session_data = cursor.fetchone()

        cursor.close()
        conn.close()

        if session_data:

            return redirect(
                "/student_camera"
            )

        else:

            return """
            <h2>
            Invalid or Expired PIN
            </h2>

            <a href="/student_attendance">
            Try Again
            </a>
            """

    return render_template(
        "student_attendance.html"
    )
@app.route("/student_camera")
def student_camera():

    return render_template(
        "student_camera.html"
    )
@app.route("/verify_face", methods=["POST"])
def verify_face():

    import base64
    import json
    import pickle
    import face_recognition
    import numpy as np

    data = request.get_json()

    image_data = data["image"]

    image_data = image_data.split(",")[1]

    image_bytes = base64.b64decode(
        image_data
    )

    image_path = (
        "uploads/temp.jpg"
    )

    with open(
        image_path,
        "wb"
    ) as f:

        f.write(image_bytes)

    image = face_recognition.load_image_file(
        image_path
    )

    encodings = (
        face_recognition
        .face_encodings(image)
    )

    if len(encodings) == 0:

        return "No Face Detected"

    face_encoding = encodings[0]

    with open(
        "encodings/encodings.pkl",
        "rb"
    ) as file:

        data = pickle.load(file)

    known_encodings = data["encodings"]
    known_names = data["names"]

    matches = (
        face_recognition
        .compare_faces(
            known_encodings,
            face_encoding
        )
    )

    if True in matches:

        index = matches.index(True)

        recognized_name = (
            known_names[index]
        )

        return (
            f"Face Verified : "
            f"{recognized_name}"
        )

    return "Unknown Face"
if __name__ == "__main__":
    app.run(
        debug=True,
        host="0.0.0.0",
        port=5001
    )
