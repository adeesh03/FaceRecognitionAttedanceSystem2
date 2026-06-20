import sys
import os

sys.path.append(
    os.path.dirname(
        os.path.dirname(
            os.path.abspath(__file__)
        )
    )
)

from database import get_connection
from datetime import datetime

def mark_attendance(student_id):

    conn = get_connection()
    cursor = conn.cursor()

    today = datetime.now().date()

    check_sql = """
    SELECT *
    FROM attendance
    WHERE student_id=%s
    AND attendance_date=%s
    """

    cursor.execute(check_sql, (student_id, today))

    if cursor.fetchone():
        print("Attendance already marked")
        cursor.close()
        conn.close()
        return

    insert_sql = """
    INSERT INTO attendance
    (
        student_id,
        attendance_date,
        attendance_time,
        status
    )
    VALUES (%s, %s, %s, %s)
    """

    cursor.execute(
        insert_sql,
        (
            student_id,
            today,
            datetime.now().time(),
            "Present"
        )
    )

    conn.commit()

    print("Attendance Marked Successfully")

    cursor.close()
    conn.close()