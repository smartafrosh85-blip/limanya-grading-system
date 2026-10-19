import base64
import http.cookiejar
import json
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

import server as app


class SchoolPortalTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        app.DB_PATH = Path(self.temp_dir.name) / "test.db"
        app.initialize_db()
        self.httpd = app.ThreadingHTTPServer(("127.0.0.1", 0), app.AppHandler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.httpd.server_port}"
        self.admin = self.new_client()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=3)
        self.temp_dir.cleanup()

    @staticmethod
    def new_client():
        jar = http.cookiejar.CookieJar()
        return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    def request(self, path, method="GET", data=None, client=None):
        body = json.dumps(data).encode("utf-8") if data is not None else None
        request = urllib.request.Request(
            self.base_url + path,
            data=body,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            response = (client or self.admin).open(request, timeout=5)
        except urllib.error.HTTPError as error:
            response = error
        try:
            return response.status, json.loads(response.read()), response.headers
        finally:
            response.close()

    def post(self, path, data, client=None):
        return self.request(path, "POST", data, client)

    def test_existing_student_table_gets_photo_column(self):
        with tempfile.TemporaryDirectory() as migration_dir:
            database_path = Path(migration_dir) / "existing.db"
            db = sqlite3.connect(database_path)
            try:
                db.execute(
                    """CREATE TABLE students (
                       id INTEGER PRIMARY KEY AUTOINCREMENT,
                       admission_no TEXT NOT NULL UNIQUE,
                       full_name TEXT NOT NULL,
                       class_name TEXT NOT NULL,
                       date_of_birth TEXT NOT NULL DEFAULT '',
                       guardian_name TEXT NOT NULL DEFAULT '',
                       guardian_phone TEXT NOT NULL DEFAULT '',
                       created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )"""
                )
                db.commit()
            finally:
                db.close()
            original_path = app.DB_PATH
            try:
                app.DB_PATH = database_path
                app.initialize_db()
                with app.connect_db() as db:
                    columns = {row["name"] for row in db.execute("PRAGMA table_info(students)")}
                self.assertIn("photo_path", columns)
            finally:
                app.DB_PATH = original_path

    def get_bytes(self, path, client=None):
        request = urllib.request.Request(self.base_url + path)
        try:
            response = (client or self.admin).open(request, timeout=5)
        except urllib.error.HTTPError as error:
            response = error
        try:
            return response.status, response.read(), response.headers
        finally:
            response.close()

    def test_role_boundaries_and_school_workflow(self):
        tiny_png = (
            "data:image/png;base64,"
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/lE8AAAAASUVORK5CYII="
        )
        status, setup, headers = self.post(
            "/api/setup",
            {"name": "Headmaster Lim", "email": "headmaster@limanya.edu", "password": "SchoolPassword!2026"},
        )
        self.assertEqual(status, 201)
        self.assertEqual(setup["user"]["role"], "admin")
        self.assertIn("HttpOnly", headers["Set-Cookie"])
        self.assertIn("SameSite=Strict", headers["Set-Cookie"])

        status, _, _ = self.post(
            "/api/setup",
            {"name": "Second Admin", "email": "second@limanya.edu", "password": "SchoolPassword!2026"},
        )
        self.assertEqual(status, 409)

        first = self.post(
            "/api/students",
            {
                "admission_no": "LIS-001",
                "full_name": "Amina Example",
                "class_name": "Year 8",
                "guardian_name": "Family Example",
                "guardian_phone": "+000 555 0101",
                "photo_data": tiny_png,
            },
        )
        second = self.post(
            "/api/students",
            {
                "admission_no": "LIS-002",
                "full_name": "Tariq Example",
                "class_name": "Year 9",
                "photo_data": tiny_png,
            },
        )
        self.assertEqual(first[0], 201)
        self.assertEqual(second[0], 201)
        first_id = first[1]["student"]["id"]
        second_id = second[1]["student"]["id"]
        self.assertTrue(first[1]["student"]["has_photo"])
        self.assertNotIn("photo_path", first[1]["student"])
        photo_status, photo_bytes, photo_headers = self.get_bytes(f"/api/students/{first_id}/photo")
        self.assertEqual(photo_status, 200)
        self.assertEqual(photo_bytes, base64.b64decode(tiny_png.split(",", 1)[1]))
        self.assertEqual(photo_headers["Content-Type"], "image/png")
        self.assertIn("no-store", photo_headers["Cache-Control"])

        status, _, _ = self.post(
            "/api/students",
            {
                "admission_no": "LIS-INVALID",
                "full_name": "Invalid photo",
                "class_name": "Year 1",
                "photo_data": "data:image/svg+xml;base64,PHN2Zy8+",
            },
        )
        self.assertEqual(status, 400)

        for name, email, role, student_id in [
            ("Teacher One", "teacher@limanya.edu", "teacher", None),
            ("Amina Example", "amina@limanya.edu", "student", first_id),
            ("Family Example", "parent@limanya.edu", "parent", first_id),
        ]:
            status, _, _ = self.post(
                "/api/users",
                {
                    "name": name,
                    "email": email,
                    "password": "SchoolPassword!2026",
                    "role": role,
                    "student_id": student_id,
                },
            )
            self.assertEqual(status, 201)

        teacher = self.new_client()
        status, _, _ = self.post(
            "/api/login",
            {"email": "teacher@limanya.edu", "password": "SchoolPassword!2026"},
            teacher,
        )
        self.assertEqual(status, 200)
        status, _, _ = self.request("/api/users", client=teacher)
        self.assertEqual(status, 403)
        status, _, _ = self.post(
            "/api/students",
            {"admission_no": "LIS-003", "full_name": "Forbidden", "class_name": "Year 1"},
            teacher,
        )
        self.assertEqual(status, 403)
        status, teacher_photo, _ = self.get_bytes(f"/api/students/{first_id}/photo", teacher)
        self.assertEqual(status, 200)
        self.assertEqual(teacher_photo, photo_bytes)

        status, _, _ = self.post(
            "/api/grades",
            {"student_id": first_id, "subject": "Mathematics", "score": 86, "max_score": 100, "term": "Term 1"},
            teacher,
        )
        self.assertEqual(status, 201)
        status, _, _ = self.post(
            "/api/grades",
            {"student_id": first_id, "subject": "Science", "score": 90, "max_score": 100, "term": "Term 1"},
        )
        self.assertEqual(status, 201)
        status, _, _ = self.post(
            "/api/grades",
            {"student_id": second_id, "subject": "History", "score": 75, "max_score": 100, "term": "Term 1"},
        )
        self.assertEqual(status, 201)
        status, _, _ = self.post(
            "/api/grades",
            {"student_id": first_id, "subject": "Art", "score": 101, "max_score": 100, "term": "Term 1"},
        )
        self.assertEqual(status, 400)

        status, _, _ = self.post(
            "/api/attendance",
            {"student_id": first_id, "attendance_date": "2026-10-19", "status": "present"},
            teacher,
        )
        self.assertEqual(status, 201)
        status, _, _ = self.post(
            "/api/announcements",
            {"title": "Family gathering", "body": "Families are welcome.", "audience": "families"},
        )
        self.assertEqual(status, 201)
        status, _, _ = self.post(
            "/api/announcements",
            {"title": "School update", "body": "A message for everyone.", "audience": "all"},
        )
        self.assertEqual(status, 201)

        student = self.new_client()
        self.post("/api/login", {"email": "amina@limanya.edu", "password": "SchoolPassword!2026"}, student)
        status, student_data, _ = self.request("/api/dashboard", client=student)
        self.assertEqual(status, 200)
        self.assertEqual([row["id"] for row in student_data["students"]], [first_id])
        self.assertTrue(student_data["students"][0]["has_photo"])
        self.assertNotIn("guardian_phone", student_data["students"][0])
        self.assertEqual({grade["student_name"] for grade in student_data["grades"]}, {"Amina Example"})
        status, student_photo, _ = self.get_bytes(f"/api/students/{first_id}/photo", student)
        self.assertEqual(status, 200)
        self.assertEqual(student_photo, photo_bytes)
        status, _, _ = self.get_bytes(f"/api/students/{second_id}/photo", student)
        self.assertEqual(status, 404)
        status, _, _ = self.request("/api/students", client=student)
        self.assertEqual(status, 403)
        status, _, _ = self.post(
            "/api/grades",
            {"student_id": first_id, "subject": "Art", "score": 95, "max_score": 100, "term": "Term 1"},
            student,
        )
        self.assertEqual(status, 403)

        parent = self.new_client()
        self.post("/api/login", {"email": "parent@limanya.edu", "password": "SchoolPassword!2026"}, parent)
        status, parent_data, _ = self.request("/api/dashboard", client=parent)
        self.assertEqual(status, 200)
        self.assertEqual([row["id"] for row in parent_data["students"]], [first_id])
        status, parent_photo, _ = self.get_bytes(f"/api/students/{first_id}/photo", parent)
        self.assertEqual(status, 200)
        self.assertEqual(parent_photo, photo_bytes)
        self.assertEqual({grade["student_name"] for grade in parent_data["grades"]}, {"Amina Example"})
        self.assertIn("Family gathering", {item["title"] for item in parent_data["announcements"]})

        status, admin_data, _ = self.request("/api/dashboard")
        self.assertEqual(status, 200)
        self.assertEqual(len(admin_data["students"]), 2)
        self.assertEqual(
            {item["title"] for item in admin_data["announcements"]},
            {"Family gathering", "School update"},
        )

        bad_login = self.new_client()
        status, _, _ = self.post(
            "/api/login", {"email": "teacher@limanya.edu", "password": "wrong"}, bad_login
        )
        self.assertEqual(status, 401)


if __name__ == "__main__":
    unittest.main()
