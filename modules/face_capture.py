import cv2
import os

student_id = input("Enter Student ID: ")

folder = f"dataset/{student_id}"

os.makedirs(folder, exist_ok=True)

camera = cv2.VideoCapture(0)

count = 0

while True:

    ret, frame = camera.read()

    if not ret:
        break

    cv2.imshow("Face Capture", frame)

    count += 1

    cv2.imwrite(
        f"{folder}/{count}.jpg",
        frame
    )

    if count >= 50:
        break

    cv2.waitKey(100)

camera.release()
cv2.destroyAllWindows()

print("50 Images Captured Successfully")