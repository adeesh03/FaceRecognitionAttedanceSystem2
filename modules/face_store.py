"""Validated biometric photo storage and atomic face-model updates."""

from __future__ import annotations

import pickle
import uuid
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png"}


class FacePhotoError(ValueError):
    pass


def prepare_photos(files, maximum: int = 20) -> list[bytes]:
    import face_recognition

    selected = [file for file in files if file and file.filename]
    if not selected:
        raise FacePhotoError("Select at least one face photo.")
    if len(selected) > maximum:
        raise FacePhotoError(f"Upload no more than {maximum} photos at once.")

    prepared = []
    for uploaded in selected:
        suffix = Path(uploaded.filename).suffix.lower()
        if suffix not in ALLOWED_EXTENSIONS:
            raise FacePhotoError(f"{uploaded.filename}: use JPG or PNG format.")
        try:
            source = Image.open(uploaded.stream)
            image = ImageOps.exif_transpose(source).convert("RGB")
            image.thumbnail((1600, 1600))
            output = BytesIO()
            image.save(output, "JPEG", quality=92, optimize=True)
            data = output.getvalue()
            locations = face_recognition.face_locations(
                face_recognition.load_image_file(BytesIO(data)),
                number_of_times_to_upsample=1,
                model="hog",
            )
        except (OSError, ValueError) as error:
            raise FacePhotoError(f"{uploaded.filename}: the image could not be read.") from error
        if len(locations) != 1:
            reason = "no face was detected" if not locations else "more than one face was detected"
            raise FacePhotoError(f"{uploaded.filename}: {reason}.")
        prepared.append(data)
    return prepared


def store_photos(dataset_dir: Path, student_id: str, photos: list[bytes]) -> int:
    folder = dataset_dir / student_id
    folder.mkdir(parents=True, exist_ok=True)
    for data in photos:
        (folder / f"{uuid.uuid4().hex}.jpg").write_bytes(data)
    return len(photos)


def rebuild_student_model(dataset_dir: Path, encodings_file: Path, student_id: str) -> int:
    import face_recognition

    folder = dataset_dir / student_id
    new_encodings = []
    for image_path in sorted(folder.iterdir()):
        if image_path.suffix.lower() not in ALLOWED_EXTENSIONS:
            continue
        image = face_recognition.load_image_file(image_path)
        locations = face_recognition.face_locations(image, number_of_times_to_upsample=1)
        if len(locations) == 1:
            new_encodings.append(
                face_recognition.face_encodings(image, known_face_locations=locations)[0]
            )
    if not new_encodings:
        raise FacePhotoError("No usable face photos were found for this student.")

    data = {"encodings": [], "names": []}
    if encodings_file.exists():
        with encodings_file.open("rb") as source:
            existing = pickle.load(source)
        for encoding, name in zip(existing.get("encodings", []), existing.get("names", [])):
            if str(name).strip().upper() != student_id:
                data["encodings"].append(encoding)
                data["names"].append(name)
    data["encodings"].extend(new_encodings)
    data["names"].extend([student_id] * len(new_encodings))

    encodings_file.parent.mkdir(parents=True, exist_ok=True)
    temporary = encodings_file.with_suffix(".tmp")
    with temporary.open("wb") as destination:
        pickle.dump(data, destination)
    temporary.replace(encodings_file)
    return len(new_encodings)
