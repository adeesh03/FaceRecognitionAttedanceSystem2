import cv2
import face_recognition
import pickle
from modules.mark_attendance import mark_attendance

# Load trained encodings
with open("encodings/encodings.pkl", "rb") as f:
    data = pickle.load(f)

# Open camera
video = cv2.VideoCapture(0, cv2.CAP_AVFOUNDATION)

# Track already marked faces in current session
marked_faces = set()

while True:

    ret, frame = video.read()

    if not ret:
        print("Failed to access camera")
        break

    # Convert BGR to RGB
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

    # Detect faces
    face_locations = face_recognition.face_locations(rgb)

    # Generate encodings
    face_encodings = face_recognition.face_encodings(
        rgb,
        face_locations
    )

    for face_encoding, face_location in zip(
        face_encodings,
        face_locations
    ):

        matches = face_recognition.compare_faces(
            data["encodings"],
            face_encoding
        )

        name = "Unknown"

        if True in matches:

            match_index = matches.index(True)

            name = data["names"][match_index]

            # Mark attendance only once
            if name not in marked_faces:
                mark_attendance(name)
                marked_faces.add(name)

        top, right, bottom, left = face_location

        # Draw rectangle
        cv2.rectangle(
            frame,
            (left, top),
            (right, bottom),
            (0, 255, 0),
            2
        )

        # Display name
        cv2.putText(
            frame,
            name,
            (left, top - 10),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (0, 255, 0),
            2
        )

    cv2.imshow(
        "Face Recognition Attendance System",
        frame
    )

    # Press ESC to exit
    if cv2.waitKey(1) == 27:
        break

video.release()
cv2.destroyAllWindows()