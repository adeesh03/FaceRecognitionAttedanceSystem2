from flask import Flask, render_template, request
from database import get_connection

app = Flask(__name__)

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

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT COUNT(*) FROM students"
    )

    total_students = cursor.fetchone()[0]

    cursor.execute(
        "SELECT COUNT(*) FROM attendance"
    )

    total_attendance = cursor.fetchone()[0]

    cursor.close()
    conn.close()

    return render_template(
        "dashboard.html",
        total_students=total_students,
        total_attendance=total_attendance
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

if __name__ == "__main__":
    app.run(
        debug=True,
        host="0.0.0.0",
        port=5001
    )
