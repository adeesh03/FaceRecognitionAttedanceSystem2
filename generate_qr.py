import qrcode
import os

os.makedirs(
    "static/qr",
    exist_ok=True
)

img = qrcode.make(
    "http://192.168.5.101:5001/student_attendance"
)

img.save(
    "static/qr/current_qr.png"
)