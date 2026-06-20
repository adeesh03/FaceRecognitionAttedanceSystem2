import face_recognition
import pickle
import os

known_encodings = []
known_names = []

dataset_path = "dataset"

for student_name in os.listdir(dataset_path):

    student_folder = os.path.join(
        dataset_path,
        student_name
    )

    if not os.path.isdir(student_folder):
        continue

    for image_name in os.listdir(student_folder):

        image_path = os.path.join(
            student_folder,
            image_name
        )

        image = face_recognition.load_image_file(
            image_path
        )

        encodings = face_recognition.face_encodings(
            image
        )

        if len(encodings) > 0:

            known_encodings.append(
                encodings[0]
            )

            known_names.append(
                student_name
            )

data = {
    "encodings": known_encodings,
    "names": known_names
}

os.makedirs(
    "encodings",
    exist_ok=True
)

with open(
    "encodings/encodings.pkl",
    "wb"
) as file:

    pickle.dump(
        data,
        file
    )

print(
    f"Training Complete: {len(known_names)} faces encoded"
)