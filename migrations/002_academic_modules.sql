USE attendance_system;

CREATE TABLE IF NOT EXISTS lecturers (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  staff_id VARCHAR(40) NOT NULL UNIQUE,
  name VARCHAR(120) NOT NULL,
  email VARCHAR(255) NOT NULL UNIQUE,
  password_hash VARCHAR(255) NOT NULL,
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS courses (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  course_code VARCHAR(40) NOT NULL UNIQUE,
  course_name VARCHAR(160) NOT NULL,
  department VARCHAR(100) NOT NULL,
  semester VARCHAR(40) NOT NULL,
  lecturer_id BIGINT UNSIGNED NULL,
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_courses_lecturer FOREIGN KEY (lecturer_id) REFERENCES lecturers(id)
    ON UPDATE CASCADE ON DELETE SET NULL
) ENGINE=InnoDB;

ALTER TABLE attendance_session
  ADD COLUMN course_id BIGINT UNSIGNED NULL AFTER session_name,
  ADD COLUMN created_by_lecturer_id BIGINT UNSIGNED NULL AFTER course_id,
  ADD INDEX ix_attendance_session_course (course_id),
  ADD CONSTRAINT fk_session_course FOREIGN KEY (course_id) REFERENCES courses(id)
    ON UPDATE CASCADE ON DELETE RESTRICT,
  ADD CONSTRAINT fk_session_lecturer FOREIGN KEY (created_by_lecturer_id) REFERENCES lecturers(id)
    ON UPDATE CASCADE ON DELETE SET NULL;

CREATE TABLE IF NOT EXISTS course_enrollments (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  course_id BIGINT UNSIGNED NOT NULL,
  student_id VARCHAR(40) NOT NULL,
  enrolled_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_enrollment_course_student (course_id, student_id),
  CONSTRAINT fk_enrollment_course FOREIGN KEY (course_id) REFERENCES courses(id)
    ON UPDATE CASCADE ON DELETE CASCADE,
  CONSTRAINT fk_enrollment_student FOREIGN KEY (student_id) REFERENCES students(student_id)
    ON UPDATE CASCADE ON DELETE RESTRICT
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS audit_logs (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  actor_type VARCHAR(30) NOT NULL,
  actor_id VARCHAR(80) NULL,
  action VARCHAR(80) NOT NULL,
  entity_type VARCHAR(50) NOT NULL,
  entity_id VARCHAR(80) NULL,
  details TEXT NULL,
  ip_address VARCHAR(45) NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY ix_audit_created (created_at),
  KEY ix_audit_action (action)
) ENGINE=InnoDB;
