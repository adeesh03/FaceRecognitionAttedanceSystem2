-- Run once against an existing attendance_system database.
-- Historical rows remain with a NULL session_id because their original
-- attendance session cannot be reconstructed safely.
USE attendance_system;

ALTER TABLE attendance
  -- Match the existing attendance_session.id type (signed INT).
  ADD COLUMN session_id INT NULL AFTER student_id,
  ADD KEY ix_attendance_session_id (session_id),
  ADD CONSTRAINT fk_attendance_session
    FOREIGN KEY (session_id) REFERENCES attendance_session (id)
    ON UPDATE CASCADE ON DELETE RESTRICT,
  ADD UNIQUE KEY uq_attendance_student_session (student_id, session_id);
