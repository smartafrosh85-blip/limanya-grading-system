import base64
import binascii
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import date
from http import HTTPStatus
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse


ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
DB_PATH = Path(os.environ.get("LIMANYA_DB_PATH", ROOT / "limanya.db"))
HOST = os.environ.get("LIMANYA_HOST", "127.0.0.1")
PORT = int(os.environ.get("LIMANYA_PORT", "8000"))
SESSION_TTL = 60 * 60 * 12
PBKDF2_ROUNDS = 310_000
MAX_BODY = 3 * 1024 * 1024
MAX_PHOTO_BYTES = 2 * 1024 * 1024
DB_LOCK = threading.RLock()
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
ROLES = {"admin", "teacher", "student", "parent"}


@contextmanager
def connect_db():
    connection = sqlite3.connect(DB_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def initialize_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with DB_LOCK, connect_db() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS students (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                admission_no TEXT NOT NULL UNIQUE,
                full_name TEXT NOT NULL,
                class_name TEXT NOT NULL,
                date_of_birth TEXT NOT NULL DEFAULT '',
                guardian_name TEXT NOT NULL DEFAULT '',
                guardian_phone TEXT NOT NULL DEFAULT '',
                photo_path TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('admin', 'teacher', 'student', 'parent')),
                student_id INTEGER REFERENCES students(id) ON DELETE SET NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                expires_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS grades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
                subject TEXT NOT NULL,
                score REAL NOT NULL,
                max_score REAL NOT NULL,
                term TEXT NOT NULL,
                entered_by INTEGER NOT NULL REFERENCES users(id),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS attendance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
                attendance_date TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('present', 'absent', 'late')),
                entered_by INTEGER NOT NULL REFERENCES users(id),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(student_id, attendance_date)
            );
            CREATE TABLE IF NOT EXISTS announcements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                audience TEXT NOT NULL CHECK(audience IN ('all', 'staff', 'families')),
                author_id INTEGER NOT NULL REFERENCES users(id),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_grades_student ON grades(student_id);
            CREATE INDEX IF NOT EXISTS idx_attendance_student ON attendance(student_id);
            CREATE INDEX IF NOT EXISTS idx_sessions_expiry ON sessions(expires_at);
            """
        )
        student_columns = {row["name"] for row in db.execute("PRAGMA table_info(students)")}
        if "photo_path" not in student_columns:
            db.execute("ALTER TABLE students ADD COLUMN photo_path TEXT")


def password_digest(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ROUNDS)
    return f"{salt.hex()}:{derived.hex()}"


def password_matches(password, stored):
    salt_hex, digest_hex = stored.split(":", 1)
    candidate = password_digest(password, bytes.fromhex(salt_hex))
    return hmac.compare_digest(candidate.split(":", 1)[1], digest_hex)


def clean_text(value, field, limit=160, required=True):
    if not isinstance(value, str):
        raise ValueError(f"{field} must be text.")
    value = value.strip()
    if required and not value:
        raise ValueError(f"{field} is required.")
    if len(value) > limit:
        raise ValueError(f"{field} must be {limit} characters or fewer.")
    return value


class AppHandler(BaseHTTPRequestHandler):
    server_version = "LimanyaSchool/1.0"

    def log_message(self, fmt, *args):
        print(f"{self.log_date_time_string()} {self.address_string()} {fmt % args}")

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data: blob:; connect-src 'self'; base-uri 'self'; "
            "form-action 'self'; frame-ancestors 'none'",
        )
        super().end_headers()

    def _json(self, status, payload, extra_headers=None):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        if extra_headers:
            for key, value in extra_headers.items():
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("Invalid request body.") from exc
        if length < 0 or length > MAX_BODY:
            raise ValueError("Request body is too large.")
        try:
            value = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError("Request must contain valid JSON.") from exc
        if not isinstance(value, dict):
            raise ValueError("Request body must be a JSON object.")
        return value

    def _cookie_token(self):
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
            return cookie["limanya_session"].value
        except (KeyError, CookieError):
            return None

    def _current_user(self):
        token = self._cookie_token()
        if not token:
            return None
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        now = int(time.time())
        with DB_LOCK, connect_db() as db:
            row = db.execute(
                """SELECT u.id, u.name, u.email, u.role, u.student_id
                   FROM sessions s JOIN users u ON u.id = s.user_id
                   WHERE s.token_hash = ? AND s.expires_at > ?""",
                (digest, now),
            ).fetchone()
            db.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
        return dict(row) if row else None

    def _require(self, roles=None):
        user = self._current_user()
        if not user:
            self._json(HTTPStatus.UNAUTHORIZED, {"error": "Please sign in to continue."})
            return None
        if roles and user["role"] not in roles:
            self._json(HTTPStatus.FORBIDDEN, {"error": "You do not have permission to do that."})
            return None
        return user

    def _handle_error(self, error, status=HTTPStatus.BAD_REQUEST):
        self._json(status, {"error": str(error)})

    def do_GET(self):
        path = unquote(urlparse(self.path).path)
        if path.startswith("/api/"):
            try:
                self._api_get(path)
            except sqlite3.Error:
                self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "A database operation failed."})
            return
        self._serve_static(path)

    def do_POST(self):
        path = unquote(urlparse(self.path).path)
        if not path.startswith("/api/"):
            self._json(HTTPStatus.NOT_FOUND, {"error": "Not found."})
            return
        try:
            data = self._read_json()
            self._api_post(path, data)
        except ValueError as exc:
            self._handle_error(exc)
        except sqlite3.IntegrityError as exc:
            message = "That value already exists or conflicts with a linked record."
            self._handle_error(message, HTTPStatus.CONFLICT)
        except sqlite3.Error:
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "A database operation failed."})
        except OSError:
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "The student photo could not be saved."})

    def _serve_static(self, path):
        requested = "index.html" if path == "/" else path.lstrip("/")
        candidate = (STATIC / requested).resolve()
        try:
            candidate.relative_to(STATIC.resolve())
        except ValueError:
            self._json(HTTPStatus.NOT_FOUND, {"error": "Not found."})
            return
        if not candidate.is_file():
            self._json(HTTPStatus.NOT_FOUND, {"error": "Not found."})
            return
        content_type = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".svg": "image/svg+xml",
        }.get(candidate.suffix.lower(), "application/octet-stream")
        body = candidate.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _api_get(self, path):
        photo_match = re.fullmatch(r"/api/students/(\d+)/photo", path)
        if photo_match:
            self._serve_student_photo(int(photo_match.group(1)))
            return
        if path == "/api/session":
            user = self._current_user()
            with DB_LOCK, connect_db() as db:
                setup_required = db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
            self._json(HTTPStatus.OK, {"user": user, "setup_required": setup_required})
            return
        if path == "/api/dashboard":
            user = self._require()
            if not user:
                return
            self._dashboard(user)
            return
        if path == "/api/students":
            user = self._require({"admin", "teacher"})
            if not user:
                return
            with DB_LOCK, connect_db() as db:
                students = db.execute(
                    """SELECT id, admission_no, full_name, class_name, date_of_birth, guardian_name,
                              guardian_phone, photo_path IS NOT NULL AS has_photo
                       FROM students ORDER BY full_name"""
                ).fetchall()
            self._json(HTTPStatus.OK, {"students": [dict(row) for row in students]})
            return
        if path == "/api/users":
            user = self._require({"admin"})
            if not user:
                return
            with DB_LOCK, connect_db() as db:
                users = db.execute(
                    """SELECT u.id, u.name, u.email, u.role, u.student_id, s.full_name AS student_name
                       FROM users u LEFT JOIN students s ON s.id = u.student_id ORDER BY u.name"""
                ).fetchall()
            self._json(HTTPStatus.OK, {"users": [dict(row) for row in users]})
            return
        self._json(HTTPStatus.NOT_FOUND, {"error": "Not found."})

    def _dashboard(self, user):
        role = user["role"]
        with DB_LOCK, connect_db() as db:
            if role in {"admin", "teacher"}:
                students = db.execute(
                    """SELECT id, admission_no, full_name, class_name, date_of_birth, guardian_name,
                              guardian_phone, photo_path IS NOT NULL AS has_photo
                       FROM students ORDER BY full_name"""
                ).fetchall()
                student_ids = [row["id"] for row in students]
            elif role == "student" and user["student_id"]:
                students = db.execute(
                    """SELECT id, admission_no, full_name, class_name,
                              photo_path IS NOT NULL AS has_photo
                       FROM students WHERE id = ?""",
                    (user["student_id"],),
                ).fetchall()
                student_ids = [row["id"] for row in students]
            elif role == "parent" and user["student_id"]:
                students = db.execute(
                    """SELECT id, admission_no, full_name, class_name,
                              photo_path IS NOT NULL AS has_photo
                       FROM students WHERE id = ?""",
                    (user["student_id"],),
                ).fetchall()
                student_ids = [row["id"] for row in students]
            else:
                students = []
                student_ids = []

            grades = []
            attendance = []
            if student_ids:
                placeholders = ",".join("?" for _ in student_ids)
                grades = db.execute(
                    f"""SELECT g.id, g.student_id, s.full_name AS student_name, s.class_name,
                               g.subject, g.score, g.max_score, g.term, g.created_at
                        FROM grades g JOIN students s ON s.id = g.student_id
                        WHERE g.student_id IN ({placeholders}) ORDER BY g.created_at DESC, g.id DESC LIMIT 100""",
                    student_ids,
                ).fetchall()
                attendance = db.execute(
                    f"""SELECT a.id, a.student_id, s.full_name AS student_name, s.class_name,
                               a.attendance_date, a.status
                        FROM attendance a JOIN students s ON s.id = a.student_id
                        WHERE a.student_id IN ({placeholders}) ORDER BY a.attendance_date DESC, a.id DESC LIMIT 100""",
                    student_ids,
                ).fetchall()

            if role == "admin":
                audiences = ["all", "staff", "families"]
            elif role == "teacher":
                audiences = ["all", "staff"]
            else:
                audiences = ["all", "families"]
            marks = ",".join("?" for _ in audiences)
            announcements = db.execute(
                f"""SELECT a.id, a.title, a.body, a.audience, a.created_at, u.name AS author
                    FROM announcements a JOIN users u ON u.id = a.author_id
                    WHERE a.audience IN ({marks}) ORDER BY a.created_at DESC, a.id DESC LIMIT 20""",
                audiences,
            ).fetchall()
            counts = {
                "students": db.execute("SELECT COUNT(*) FROM students").fetchone()[0] if role == "admin" else len(students),
                "teachers": db.execute("SELECT COUNT(*) FROM users WHERE role = 'teacher'").fetchone()[0] if role == "admin" else None,
                "grades": len(grades),
                "attendance_records": len(attendance),
            }
        self._json(
            HTTPStatus.OK,
            {
                "user": user,
                "students": [dict(row) for row in students],
                "grades": [dict(row) for row in grades],
                "attendance": [dict(row) for row in attendance],
                "announcements": [dict(row) for row in announcements],
                "counts": counts,
            },
        )

    def _api_post(self, path, data):
        if path == "/api/setup":
            name = clean_text(data.get("name"), "Full name")
            email = self._email(data.get("email"))
            password = self._password(data.get("password"))
            with DB_LOCK, connect_db() as db:
                db.execute("BEGIN IMMEDIATE")
                if db.execute("SELECT COUNT(*) FROM users").fetchone()[0] != 0:
                    self._json(HTTPStatus.CONFLICT, {"error": "Initial setup has already been completed."})
                    return
                cursor = db.execute(
                    "INSERT INTO users (name, email, password_hash, role) VALUES (?, ?, ?, 'admin')",
                    (name, email, password_digest(password)),
                )
                user = db.execute(
                    "SELECT id, name, email, role, student_id FROM users WHERE id = ?", (cursor.lastrowid,)
                ).fetchone()
                token = self._new_session(db, cursor.lastrowid)
            self._json(HTTPStatus.CREATED, {"user": dict(user)}, self._session_cookie(token))
            return

        if path == "/api/login":
            email = self._email(data.get("email"))
            password = data.get("password")
            if not isinstance(password, str) or not password:
                raise ValueError("Password is required.")
            with DB_LOCK, connect_db() as db:
                row = db.execute(
                    "SELECT id, name, email, password_hash, role, student_id FROM users WHERE email = ? COLLATE NOCASE",
                    (email,),
                ).fetchone()
                if not row or not password_matches(password, row["password_hash"]):
                    self._json(HTTPStatus.UNAUTHORIZED, {"error": "Email or password is incorrect."})
                    return
                token = self._new_session(db, row["id"])
            user = {key: row[key] for key in ("id", "name", "email", "role", "student_id")}
            self._json(HTTPStatus.OK, {"user": user}, self._session_cookie(token))
            return

        if path == "/api/logout":
            token = self._cookie_token()
            if token:
                digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
                with DB_LOCK, connect_db() as db:
                    db.execute("DELETE FROM sessions WHERE token_hash = ?", (digest,))
            self._json(
                HTTPStatus.OK,
                {"ok": True},
                {"Set-Cookie": "limanya_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"},
            )
            return

        if path == "/api/students":
            user = self._require({"admin"})
            if not user:
                return
            admission_no = clean_text(data.get("admission_no"), "Admission number", 40)
            full_name = clean_text(data.get("full_name"), "Student name")
            class_name = clean_text(data.get("class_name"), "Class", 80)
            date_of_birth = clean_text(data.get("date_of_birth", ""), "Date of birth", 10, False)
            guardian_name = clean_text(data.get("guardian_name", ""), "Guardian name", 160, False)
            guardian_phone = clean_text(data.get("guardian_phone", ""), "Guardian phone", 40, False)
            photo_bytes, photo_suffix = self._decode_photo(data.get("photo_data"))
            photo_name = f"{secrets.token_hex(16)}{photo_suffix}" if photo_bytes else None
            photo_path = self._photo_directory() / photo_name if photo_name else None
            try:
                with DB_LOCK, connect_db() as db:
                    cursor = db.execute(
                        """INSERT INTO students
                           (admission_no, full_name, class_name, date_of_birth, guardian_name, guardian_phone, photo_path)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (
                            admission_no, full_name, class_name, date_of_birth,
                            guardian_name, guardian_phone, photo_name,
                        ),
                    )
                    if photo_path:
                        self._photo_directory().mkdir(parents=True, exist_ok=True)
                        with photo_path.open("xb") as photo_file:
                            photo_file.write(photo_bytes)
                    student = db.execute(
                        """SELECT id, admission_no, full_name, class_name, date_of_birth,
                                  guardian_name, guardian_phone, photo_path IS NOT NULL AS has_photo
                           FROM students WHERE id = ?""",
                        (cursor.lastrowid,),
                    ).fetchone()
            except (sqlite3.Error, OSError):
                if photo_path:
                    photo_path.unlink(missing_ok=True)
                raise
            self._json(HTTPStatus.CREATED, {"student": dict(student)})
            return

        if path == "/api/users":
            user = self._require({"admin"})
            if not user:
                return
            name = clean_text(data.get("name"), "Full name")
            email = self._email(data.get("email"))
            password = self._password(data.get("password"))
            role = data.get("role")
            if role not in ROLES - {"admin"}:
                raise ValueError("Choose a teacher, student, or parent role.")
            student_id = data.get("student_id")
            if role in {"student", "parent"}:
                if not isinstance(student_id, int) or isinstance(student_id, bool):
                    raise ValueError("Select a student record for this account.")
            else:
                student_id = None
            with DB_LOCK, connect_db() as db:
                if student_id is not None and not db.execute(
                    "SELECT 1 FROM students WHERE id = ?", (student_id,)
                ).fetchone():
                    raise ValueError("The selected student record does not exist.")
                cursor = db.execute(
                    "INSERT INTO users (name, email, password_hash, role, student_id) VALUES (?, ?, ?, ?, ?)",
                    (name, email, password_digest(password), role, student_id),
                )
                new_user = db.execute(
                    "SELECT id, name, email, role, student_id FROM users WHERE id = ?", (cursor.lastrowid,)
                ).fetchone()
            self._json(HTTPStatus.CREATED, {"user": dict(new_user)})
            return

        if path == "/api/grades":
            user = self._require({"admin", "teacher"})
            if not user:
                return
            student_id = self._positive_int(data.get("student_id"), "Student")
            subject = clean_text(data.get("subject"), "Subject", 100)
            term = clean_text(data.get("term"), "Term", 80)
            score = self._number(data.get("score"), "Score")
            max_score = self._number(data.get("max_score"), "Maximum score")
            if max_score <= 0 or score < 0 or score > max_score:
                raise ValueError("Score must be between zero and the maximum score.")
            with DB_LOCK, connect_db() as db:
                self._student_exists(db, student_id)
                cursor = db.execute(
                    """INSERT INTO grades (student_id, subject, score, max_score, term, entered_by)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (student_id, subject, score, max_score, term, user["id"]),
                )
                grade = db.execute(
                    """SELECT g.id, s.full_name AS student_name, g.subject, g.score,
                              g.max_score, g.term, g.created_at
                       FROM grades g JOIN students s ON s.id = g.student_id WHERE g.id = ?""",
                    (cursor.lastrowid,),
                ).fetchone()
            self._json(HTTPStatus.CREATED, {"grade": dict(grade)})
            return

        if path == "/api/attendance":
            user = self._require({"admin", "teacher"})
            if not user:
                return
            student_id = self._positive_int(data.get("student_id"), "Student")
            attendance_date = clean_text(data.get("attendance_date"), "Date", 10)
            status = data.get("status")
            if status not in {"present", "absent", "late"}:
                raise ValueError("Attendance must be present, absent, or late.")
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", attendance_date):
                raise ValueError("Enter a valid date.")
            try:
                date.fromisoformat(attendance_date)
            except ValueError as exc:
                raise ValueError("Enter a valid date.") from exc
            with DB_LOCK, connect_db() as db:
                self._student_exists(db, student_id)
                db.execute(
                    """INSERT INTO attendance (student_id, attendance_date, status, entered_by)
                       VALUES (?, ?, ?, ?)
                       ON CONFLICT(student_id, attendance_date)
                       DO UPDATE SET status = excluded.status, entered_by = excluded.entered_by""",
                    (student_id, attendance_date, status, user["id"]),
                )
                record = db.execute(
                    """SELECT a.id, s.full_name AS student_name, s.class_name,
                              a.attendance_date, a.status
                       FROM attendance a JOIN students s ON s.id = a.student_id
                       WHERE a.student_id = ? AND a.attendance_date = ?""",
                    (student_id, attendance_date),
                ).fetchone()
            self._json(HTTPStatus.CREATED, {"attendance": dict(record)})
            return

        if path == "/api/announcements":
            user = self._require({"admin"})
            if not user:
                return
            title = clean_text(data.get("title"), "Title", 160)
            body = clean_text(data.get("body"), "Message", 4000)
            audience = data.get("audience")
            if audience not in {"all", "staff", "families"}:
                raise ValueError("Choose a valid announcement audience.")
            with DB_LOCK, connect_db() as db:
                cursor = db.execute(
                    "INSERT INTO announcements (title, body, audience, author_id) VALUES (?, ?, ?, ?)",
                    (title, body, audience, user["id"]),
                )
                announcement = db.execute(
                    """SELECT a.id, a.title, a.body, a.audience, a.created_at, u.name AS author
                       FROM announcements a JOIN users u ON u.id = a.author_id WHERE a.id = ?""",
                    (cursor.lastrowid,),
                ).fetchone()
            self._json(HTTPStatus.CREATED, {"announcement": dict(announcement)})
            return

        self._json(HTTPStatus.NOT_FOUND, {"error": "Not found."})

    def _new_session(self, db, user_id):
        token = secrets.token_urlsafe(32)
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        expires = int(time.time()) + SESSION_TTL
        db.execute("INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)", (digest, user_id, expires))
        return token

    @staticmethod
    def _session_cookie(token):
        secure = "; Secure" if os.environ.get("LIMANYA_COOKIE_SECURE", "").lower() in {"1", "true", "yes"} else ""
        return {
            "Set-Cookie": f"limanya_session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={SESSION_TTL}{secure}"
        }

    @staticmethod
    def _email(value):
        email = clean_text(value, "Email", 254).lower()
        if not EMAIL_RE.fullmatch(email):
            raise ValueError("Enter a valid email address.")
        return email

    @staticmethod
    def _password(value):
        if not isinstance(value, str) or len(value) < 12 or len(value) > 256:
            raise ValueError("Password must be between 12 and 256 characters.")
        return value

    @staticmethod
    def _positive_int(value, field):
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise ValueError(f"{field} is invalid.")
        return value

    @staticmethod
    def _number(value, field):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"{field} must be a number.")
        return float(value)

    @staticmethod
    def _student_exists(db, student_id):
        if not db.execute("SELECT 1 FROM students WHERE id = ?", (student_id,)).fetchone():
            raise ValueError("The selected student record does not exist.")

    @staticmethod
    def _photo_directory():
        return DB_PATH.parent / "student_photos"

    @staticmethod
    def _decode_photo(value):
        if value in (None, ""):
            return None, None
        if not isinstance(value, str):
            raise ValueError("Student photo must be a JPEG or PNG image.")
        header, separator, encoded = value.partition(",")
        formats = {
            "data:image/jpeg;base64": (".jpg", b"\xff\xd8\xff", b"\xff\xd9"),
            "data:image/png;base64": (".png", b"\x89PNG\r\n\x1a\n", b"IEND\xaeB`\x82"),
        }
        if not separator or header not in formats:
            raise ValueError("Student photo must be a JPEG or PNG image.")
        if len(encoded) > 4 * ((MAX_PHOTO_BYTES + 2) // 3):
            raise ValueError("Student photo must be 2 MB or smaller.")
        try:
            photo_bytes = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("Student photo is not valid base64 image data.") from exc
        suffix, signature, ending = formats[header]
        if len(photo_bytes) > MAX_PHOTO_BYTES:
            raise ValueError("Student photo must be 2 MB or smaller.")
        if not photo_bytes.startswith(signature) or not photo_bytes.endswith(ending):
            raise ValueError("Student photo does not match its JPEG or PNG format.")
        return photo_bytes, suffix

    def _serve_student_photo(self, student_id):
        user = self._require()
        if not user:
            return
        with DB_LOCK, connect_db() as db:
            student = db.execute(
                "SELECT photo_path FROM students WHERE id = ?", (student_id,)
            ).fetchone()
        if (
            not student
            or not student["photo_path"]
            or (user["role"] in {"student", "parent"} and user["student_id"] != student_id)
        ):
            self._json(HTTPStatus.NOT_FOUND, {"error": "Student photo not found."})
            return
        photo_name = student["photo_path"]
        if not re.fullmatch(r"[a-f0-9]{32}\.(?:jpg|png)", photo_name):
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Student photo storage is invalid."})
            return
        photo_path = self._photo_directory() / photo_name
        try:
            photo_bytes = photo_path.read_bytes()
        except FileNotFoundError:
            self._json(HTTPStatus.NOT_FOUND, {"error": "Student photo not found."})
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "image/jpeg" if photo_name.endswith(".jpg") else "image/png")
        self.send_header("Content-Length", str(len(photo_bytes)))
        self.send_header("Cache-Control", "private, no-store")
        self.end_headers()
        self.wfile.write(photo_bytes)


if __name__ == "__main__":
    initialize_db()
    print(f"Limanya International School is available at http://{HOST}:{PORT}")
    ThreadingHTTPServer((HOST, PORT), AppHandler).serve_forever()
