from reportlab.platypus import (
    SimpleDocTemplate,
    Table,
    TableStyle,
    Paragraph,
    Spacer
)

from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet

from database import get_connection

pdf = SimpleDocTemplate(
    "attendance_report.pdf"
)

elements = []

styles = getSampleStyleSheet()

title = Paragraph(
    "Attendance Report",
    styles["Title"]
)

elements.append(title)
elements.append(Spacer(1, 20))

conn = get_connection()
cursor = conn.cursor()

cursor.execute(
    """
    SELECT *
    FROM attendance
    """
)

rows = cursor.fetchall()

data = [
    [
        "ID",
        "Student ID",
        "Date",
        "Time",
        "Status"
    ]
]

for row in rows:
    data.append([
        str(row[0]),
        str(row[1]).strip(),
        str(row[2]),
        str(row[3]),
        str(row[4])
    ])

table = Table(data)

table.setStyle(
    TableStyle([
        ('BACKGROUND',(0,0),(-1,0),colors.grey),
        ('TEXTCOLOR',(0,0),(-1,0),colors.whitesmoke),
        ('GRID',(0,0),(-1,-1),1,colors.black),
        ('FONTNAME',(0,0),(-1,0),'Helvetica-Bold')
    ])
)

elements.append(table)

pdf.build(elements)

cursor.close()
conn.close()

print("PDF Report Generated Successfully")