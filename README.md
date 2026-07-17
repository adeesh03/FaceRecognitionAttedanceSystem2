# JAIN University — Smart Attendance Portal

A professional Flask and MySQL attendance application branded for JAIN University, with time-limited QR sessions, PIN
validation, browser camera capture, and face-to-student identity matching.

## Requirements

- Python 3.11 or 3.12
- MySQL 8+
- CMake/compiler dependencies required by `dlib`
- HTTPS for camera access from mobile browsers

## Initial setup

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
mysql -u root -p < schema.sql
```

Replace every placeholder in `.env`. Generate a Flask secret with:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

Generate an administrator password hash with:

```bash
python -c "from werkzeug.security import generate_password_hash; print(generate_password_hash(input('Password: ')))"
```

Set `PUBLIC_BASE_URL` to the externally reachable HTTPS domain or tunnel and
set `COOKIE_SECURE=1`. HTTP LAN URLs can be used for desktop development, but
mobile browsers normally deny camera access outside a secure HTTPS context.

## Upgrade an existing database

Back up the database, then apply the session-attendance migration once:

```bash
mysqldump -u root -p attendance_system > attendance_system.backup.sql
mysql -u root -p attendance_system < migrations/001_session_based_attendance.sql
```

Historical attendance records retain a `NULL` session because their original
session cannot be reconstructed safely. New records require a session and the
database enforces one record per student/session.

## Run

```bash
python app.py
```

For deployment, run the application behind a production WSGI server and an
HTTPS reverse proxy. Do not expose Flask's development server publicly.

## Attendance calculation

Attendance percentage is calculated as:

```text
completed sessions attended / total completed sessions * 100
```

A session is completed after it expires or when it is replaced by a newer
session. The currently active session is excluded until completion.
