# Limanya International School — Academic Portal

A responsive school grading and records portal built with HTML, CSS, vanilla JavaScript, and a Python standard-library backend. SQLite stores school records and accounts locally. Role checks are enforced by the server, not just hidden in the interface.

## Run locally

Requires Python 3.10 or newer; there are no third-party packages to install.

```powershell
python server.py
```

Open <http://127.0.0.1:8000>. On the first visit, create the headmaster account. That one-time setup is available only while the database has no accounts. Choose a unique email and a password of at least 12 characters.

The SQLite database is created as `limanya.db` next to `server.py`. To use a different database file or port, set `LIMANYA_DB_PATH` or `LIMANYA_PORT` before starting the server. When serving behind HTTPS, set `LIMANYA_COOKIE_SECURE=1` so browsers send the session cookie only over TLS.

## Included

- Headmaster administration: register students; create teacher, student, and parent accounts; see the user directory; record or review grades and attendance; publish announcements.
- Student registration accepts an optional JPEG or PNG passport-style photo (maximum 2 MB); photos are stored outside the public web directory and only served to school staff or that student's linked student/parent accounts.
- Teacher workspace: review the school student directory and record grades and attendance. Teachers cannot create accounts, register students, or publish announcements.
- Student and parent workspaces: view only the student record linked to their account, that learner's grades and attendance, and relevant school announcements.
- One-time first-admin setup, salted PBKDF2 password hashes, random server-side sessions with opaque HttpOnly cookies, server-enforced role checks, and basic browser security headers.

To link a student or parent login to a learner, first register the learner, then create the account and select that learner in the account form. A student and a parent can each be linked to the same student record.

## Important deployment note

This is a functional starting point for local development and evaluation, not a production-ready student information system. Before using real student or family information, deploy behind HTTPS, restrict network access, establish encrypted backups and retention policies, configure operational monitoring and account recovery, and obtain an independent security/privacy review. The built-in server is intended for local use; use a hardened production web server and deployment configuration for a public service.
