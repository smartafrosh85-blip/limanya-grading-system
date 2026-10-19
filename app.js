const authView = document.querySelector("#auth-view");
const authContent = document.querySelector("#auth-content");
const appView = document.querySelector("#app-view");
const pageContent = document.querySelector("#page-content");
const modal = document.querySelector("#modal");
const toast = document.querySelector("#toast");
let currentUser = null;
let dashboard = null;
let toastTimer;
let activePage = "overview";

const roleLabels = {
  admin: "Headmaster · Administrator",
  teacher: "Teacher",
  student: "Student",
  parent: "Parent / Guardian",
};
const navItems = [
  { id: "overview", label: "Overview", icon: "⌂", roles: ["admin", "teacher", "student", "parent"] },
  { id: "students", label: "Students", icon: "♙", roles: ["admin", "teacher"] },
  { id: "grades", label: "Grades & results", icon: "▤", roles: ["admin", "teacher", "student", "parent"] },
  { id: "attendance", label: "Attendance", icon: "◷", roles: ["admin", "teacher", "student", "parent"] },
  { id: "announcements", label: "Announcements", icon: "◇", roles: ["admin", "teacher", "student", "parent"] },
  { id: "accounts", label: "User accounts", icon: "♧", roles: ["admin"] },
];

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[char]);
}
function formatDate(value, options = { year: "numeric", month: "short", day: "numeric" }) {
  if (!value) return "—";
  const date = new Date(value.length === 10 ? `${value}T12:00:00` : value);
  return Number.isNaN(date.getTime()) ? escapeHtml(value) : date.toLocaleDateString(undefined, options);
}
function initials(name) {
  return String(name || "?").split(/\s+/).slice(0, 2).map((part) => part[0] || "").join("").toUpperCase();
}
function showToast(message, isError = false) {
  toast.textContent = message;
  toast.classList.toggle("error", isError);
  toast.classList.add("visible");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove("visible"), 3300);
}
async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    credentials: "same-origin",
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    if (response.status === 401 && path !== "/api/login" && path !== "/api/setup") {
      currentUser = null;
      showAuth();
    }
    throw new Error(payload.error || "Something went wrong. Please try again.");
  }
  return payload;
}
function showAuth(setup = false) {
  appView.hidden = true;
  authView.hidden = false;
  if (setup) renderSetup(); else renderLogin();
}
function renderLogin() {
  authContent.innerHTML = `
    <p class="eyebrow auth-eyebrow">WELCOME TO YOUR SCHOOL PORTAL</p>
    <h1 class="auth-heading">Good to have you here.</h1>
    <p class="auth-subtitle">Sign in to continue to your Limanya workspace.</p>
    <form id="login-form">
      <div class="field"><label for="login-email">EMAIL ADDRESS</label><input id="login-email" name="email" type="email" autocomplete="username" placeholder="you@limanya.edu" required maxlength="254"></div>
      <div class="field"><label for="login-password">PASSWORD</label><input id="login-password" name="password" type="password" autocomplete="current-password" placeholder="Enter your password" required></div>
      <p class="auth-error" id="auth-error" role="alert"></p>
      <button class="button-primary auth-submit" type="submit">Sign in to your portal <span aria-hidden="true">→</span></button>
    </form>`;
  document.querySelector("#login-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = event.currentTarget.querySelector("button");
    const error = document.querySelector("#auth-error");
    button.disabled = true;
    error.textContent = "";
    try {
      const data = await api("/api/login", { method: "POST", body: JSON.stringify(Object.fromEntries(new FormData(event.currentTarget))) });
      currentUser = data.user;
      await enterApp();
    } catch (err) {
      error.textContent = err.message;
      button.disabled = false;
    }
  });
}
function renderSetup() {
  authContent.innerHTML = `
    <p class="eyebrow auth-eyebrow">WELCOME TO LIMANYA</p>
    <h1 class="auth-heading">Set up your school.</h1>
    <p class="auth-subtitle">Create the first account. This one-time account has headmaster administrator access.</p>
    <form id="setup-form">
      <div class="field"><label for="setup-name">HEADMASTER'S FULL NAME</label><input id="setup-name" name="name" autocomplete="name" placeholder="Your full name" required maxlength="160"></div>
      <div class="field"><label for="setup-email">EMAIL ADDRESS</label><input id="setup-email" name="email" type="email" autocomplete="email" placeholder="headmaster@limanya.edu" required maxlength="254"></div>
      <div class="field"><label for="setup-password">PASSWORD <span class="label-note">· at least 12 characters</span></label><input id="setup-password" name="password" type="password" autocomplete="new-password" minlength="12" required></div>
      <p class="auth-error" id="auth-error" role="alert"></p>
      <button class="button-primary auth-submit" type="submit">Create administrator account <span aria-hidden="true">→</span></button>
    </form>`;
  document.querySelector("#setup-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = event.currentTarget.querySelector("button");
    const error = document.querySelector("#auth-error");
    button.disabled = true;
    error.textContent = "";
    try {
      const data = await api("/api/setup", { method: "POST", body: JSON.stringify(Object.fromEntries(new FormData(event.currentTarget))) });
      currentUser = data.user;
      await enterApp();
    } catch (err) {
      error.textContent = err.message;
      button.disabled = false;
    }
  });
}
async function bootstrap() {
  try {
    const data = await api("/api/session");
    currentUser = data.user;
    if (currentUser) await enterApp();
    else showAuth(data.setup_required);
  } catch (err) {
    showAuth(false);
    showToast(err.message, true);
  }
}
async function enterApp() {
  authView.hidden = true;
  appView.hidden = false;
  document.querySelector("#year-now").textContent = new Date().getFullYear();
  const name = escapeHtml(currentUser.name);
  document.querySelector("#user-chip").innerHTML = `<span class="avatar">${escapeHtml(initials(currentUser.name))}</span><span class="user-chip-copy"><strong>${name}</strong><small>${escapeHtml(roleLabels[currentUser.role] || currentUser.role)}</small></span>`;
  renderNav();
  await loadDashboard();
}
function renderNav() {
  document.querySelector("#main-nav").innerHTML = navItems
    .filter((item) => item.roles.includes(currentUser.role))
    .map((item) => `<button class="nav-item${activePage === item.id ? " active" : ""}" data-page="${item.id}"><span class="nav-icon">${item.icon}</span>${item.label}</button>`)
    .join("");
  document.querySelectorAll(".nav-item").forEach((button) => button.addEventListener("click", () => {
    activePage = button.dataset.page;
    document.querySelector("#sidebar").classList.remove("open");
    renderNav();
    renderPage();
  }));
}
async function loadDashboard() {
  try {
    dashboard = await api("/api/dashboard");
    currentUser = dashboard.user;
    renderPage();
  } catch (err) {
    if (currentUser) showToast(err.message, true);
  }
}
function heading(title, subtitle, action = "") {
  return `<div class="welcome-row"><div><p class="welcome-kicker">${escapeHtml(roleLabels[currentUser.role])}</p><h1 class="page-title">${title}</h1><p class="page-subtitle">${subtitle}</p></div><div class="date-pill">${formatDate(new Date().toISOString())}</div>${action}</div>`;
}
function stats() {
  const counts = dashboard.counts;
  const list = currentUser.role === "admin"
    ? [
      ["Enrolled students", counts.students, "♙", "Across all year groups"],
      ["Teaching staff", counts.teachers, "♧", "Active staff accounts"],
      ["Grade records", counts.grades, "▤", "In your view"],
      ["Attendance records", counts.attendance_records, "◷", "In your view"],
    ]
    : currentUser.role === "teacher"
      ? [["My students", dashboard.students.length, "♙", "Students in the school directory"], ["Grade records", counts.grades, "▤", "Recently recorded results"], ["Attendance", counts.attendance_records, "◷", "Recently recorded attendance"], ["Notices", dashboard.announcements.length, "◇", "Shared with your school role"]]
      : [["My learner(s)", dashboard.students.length, "♙", "Linked student record"], ["Grade records", counts.grades, "▤", "Recent results"], ["Attendance", counts.attendance_records, "◷", "Recent records"], ["Notices", dashboard.announcements.length, "◇", "School updates"]];
  return `<div class="stat-grid">${list.map(([label, value, icon, note]) => `<article class="stat-card"><div class="stat-top"><span>${label}</span><span class="stat-icon">${icon}</span></div><div class="stat-value">${value ?? "—"}</div><div class="stat-note">${note}</div></article>`).join("")}</div>`;
}
function panelHeader(title, subtitle, button = "") {
  return `<div class="panel-header"><div><h2 class="panel-title">${title}</h2><p class="panel-caption">${subtitle}</p></div>${button}</div>`;
}
function gradeRows(grades, showStudent = true) {
  if (!grades.length) return `<tr><td class="table-empty" colspan="${showStudent ? 5 : 4}">No grade records yet.</td></tr>`;
  return grades.map((grade) => `<tr>${showStudent ? `<td><strong>${escapeHtml(grade.student_name)}</strong></td>` : ""}<td>${escapeHtml(grade.subject)}</td><td>${escapeHtml(grade.term)}</td><td><strong>${grade.score} / ${grade.max_score}</strong></td><td>${Math.round(grade.score / grade.max_score * 100)}%</td></tr>`).join("");
}
function attendanceRows(rows, showStudent = true) {
  if (!rows.length) return `<tr><td class="table-empty" colspan="${showStudent ? 4 : 3}">No attendance records yet.</td></tr>`;
  return rows.map((record) => `<tr>${showStudent ? `<td><strong>${escapeHtml(record.student_name)}</strong></td>` : ""}<td>${formatDate(record.attendance_date)}</td><td><span class="badge badge-${escapeHtml(record.status)}">${escapeHtml(record.status)}</span></td>${showStudent ? `<td>${escapeHtml(record.class_name)}</td>` : ""}</tr>`).join("");
}
function studentRows(students) {
  if (!students.length) return `<tr><td class="table-empty" colspan="4">No students registered yet. Add the first student to get started.</td></tr>`;
  return students.map((student) => `<tr><td><span class="student-name-cell">${studentPhoto(student)}<strong>${escapeHtml(student.full_name)}</strong></span></td><td>${escapeHtml(student.admission_no)}</td><td>${escapeHtml(student.class_name)}</td><td>${escapeHtml(student.guardian_name || "—")}</td></tr>`).join("");
}
function studentPhoto(student, className = "student-photo") {
  if (student.has_photo) {
    return `<img class="${className}" src="/api/students/${student.id}/photo" alt="${escapeHtml(student.full_name)} passport photo" loading="lazy">`;
  }
  return `<span class="${className} student-photo-placeholder" aria-label="No photo">${escapeHtml(initials(student.full_name))}</span>`;
}
function learnerProfile() {
  if (["admin", "teacher"].includes(currentUser.role) || !dashboard.students.length) return "";
  const learner = dashboard.students[0];
  return `<section class="panel learner-profile"><div class="learner-profile-photo">${studentPhoto(learner, "student-profile-photo")}</div><div><p class="panel-caption">YOUR STUDENT PROFILE</p><h2 class="panel-title">${escapeHtml(learner.full_name)}</h2><p class="learner-profile-class">${escapeHtml(learner.class_name)} · ${escapeHtml(learner.admission_no)}</p></div></section>`;
}
function announcementCards(items) {
  if (!items.length) return `<div class="table-empty">No announcements have been shared yet.</div>`;
  return items.map((item) => `<article class="announcement-item"><div class="announcement-top"><strong>${escapeHtml(item.title)}</strong><span class="announcement-date">${formatDate(item.created_at)}</span></div><p>${escapeHtml(item.body)}</p><div class="announcement-author">By ${escapeHtml(item.author)}</div></article>`).join("");
}
function actionButtons() {
  if (currentUser.role === "admin") return `<h2 class="section-heading">Quick actions</h2><div class="action-row"><button class="action-button" data-modal="student"><span class="action-icon">＋</span>Register student</button><button class="action-button" data-modal="account"><span class="action-icon">＋</span>Create user account</button><button class="action-button" data-modal="grade"><span class="action-icon">＋</span>Record a grade</button><button class="action-button" data-modal="attendance"><span class="action-icon">＋</span>Record attendance</button><button class="action-button" data-modal="announcement"><span class="action-icon">＋</span>Post announcement</button></div>`;
  if (currentUser.role === "teacher") return `<h2 class="section-heading">Teaching tools</h2><div class="action-row"><button class="action-button" data-modal="grade"><span class="action-icon">＋</span>Record a grade</button><button class="action-button" data-modal="attendance"><span class="action-icon">＋</span>Record attendance</button></div>`;
  return "";
}
function overviewPage() {
  const privileged = ["admin", "teacher"].includes(currentUser.role);
  const showStudent = privileged;
  const greeting = `Welcome back, ${escapeHtml(currentUser.name.split(/\s+/)[0])}.`;
  const grades = dashboard.grades.slice(0, 5);
  const attendance = dashboard.attendance.slice(0, 5);
  return `${heading(greeting, currentUser.role === "admin" ? "Here is your school's academic snapshot." : "Here is your learning and school community snapshot.")}${stats()}${learnerProfile()}<div class="dashboard-grid"><div><section class="panel">${panelHeader("Recent grades", "Latest recorded academic results", privileged ? `<button class="text-button" data-page-link="grades">View all</button>` : "")}<div class="table-wrap"><table><thead><tr>${showStudent ? "<th>STUDENT</th>" : ""}<th>SUBJECT</th><th>TERM</th><th>SCORE</th><th>RESULT</th></tr></thead><tbody>${gradeRows(grades, showStudent)}</tbody></table></div></section><section class="panel">${panelHeader("Recent attendance", "Daily class register", privileged ? `<button class="text-button" data-page-link="attendance">View all</button>` : "")}<div class="table-wrap"><table><thead><tr>${showStudent ? "<th>STUDENT</th>" : ""}<th>DATE</th><th>STATUS</th>${showStudent ? "<th>CLASS</th>" : ""}</tr></thead><tbody>${attendanceRows(attendance, showStudent)}</tbody></table></div></section></div><section class="panel">${panelHeader("School notices", "The latest from your community", currentUser.role === "admin" ? `<button class="text-button" data-modal="announcement">+ New</button>` : "")}<div class="announcement-list">${announcementCards(dashboard.announcements)}</div></section></div>${actionButtons()}`;
}
function studentsPage() {
  return `${heading("Student directory", "Registration records and family contacts.", `<button class="button-primary" data-modal="student">＋ Register student</button>`)}<section class="panel">${panelHeader("Enrolled students", `${dashboard.students.length} student record${dashboard.students.length === 1 ? "" : "s"}`)}<div class="table-wrap"><table><thead><tr><th>STUDENT</th><th>ADMISSION NO.</th><th>CLASS</th><th>GUARDIAN</th></tr></thead><tbody>${studentRows(dashboard.students)}</tbody></table></div></section>`;
}
function gradesPage() {
  const canManage = ["admin", "teacher"].includes(currentUser.role);
  return `${heading("Grades & results", "Review academic progress and assessment results.", canManage ? `<button class="button-primary" data-modal="grade">＋ Record a grade</button>` : "")}<section class="panel">${panelHeader("Assessment results", "Scores recorded in your authorized view")}<div class="table-wrap"><table><thead><tr>${canManage ? "<th>STUDENT</th>" : ""}<th>SUBJECT</th><th>TERM</th><th>SCORE</th><th>RESULT</th></tr></thead><tbody>${gradeRows(dashboard.grades, canManage)}</tbody></table></div></section>`;
}
function attendancePage() {
  const canManage = ["admin", "teacher"].includes(currentUser.role);
  return `${heading("Attendance", "A clear view of the school register.", canManage ? `<button class="button-primary" data-modal="attendance">＋ Record attendance</button>` : "")}<section class="panel">${panelHeader("Attendance register", "Recent daily attendance records")}<div class="table-wrap"><table><thead><tr>${canManage ? "<th>STUDENT</th>" : ""}<th>DATE</th><th>STATUS</th>${canManage ? "<th>CLASS</th>" : ""}</tr></thead><tbody>${attendanceRows(dashboard.attendance, canManage)}</tbody></table></div></section>`;
}
function announcementsPage() {
  return `${heading("Announcements", "Important updates for your school community.", currentUser.role === "admin" ? `<button class="button-primary" data-modal="announcement">＋ Post announcement</button>` : "")}<section class="panel">${panelHeader("Community updates", "Messages shared with your role")}<div class="announcement-list">${announcementCards(dashboard.announcements)}</div></section>`;
}
async function accountsPage() {
  const { users } = await api("/api/users");
  return `${heading("User accounts", "Manage staff, student and parent access.", `<button class="button-primary" data-modal="account">＋ Create account</button>`)}<section class="panel">${panelHeader("Portal access", `${users.length} account${users.length === 1 ? "" : "s"} · passwords are never displayed`)}<div class="table-wrap"><table><thead><tr><th>NAME</th><th>EMAIL</th><th>ROLE</th><th>LINKED STUDENT</th></tr></thead><tbody>${users.map((user) => `<tr><td><strong>${escapeHtml(user.name)}</strong></td><td>${escapeHtml(user.email)}</td><td><span class="badge badge-role">${escapeHtml(user.role)}</span></td><td>${escapeHtml(user.student_name || "—")}</td></tr>`).join("") || `<tr><td class="table-empty" colspan="4">No accounts found.</td></tr>`}</tbody></table></div></section>`;
}
async function renderPage() {
  if (!dashboard || !currentUser) return;
  const names = { overview: "Overview", students: "Students", grades: "Grades & results", attendance: "Attendance", announcements: "Announcements", accounts: "User accounts" };
  document.querySelector("#crumb-current").textContent = names[activePage] || "Overview";
  pageContent.innerHTML = `<div class="page-alert">Loading your authorized view…</div>`;
  try {
    const renderers = {
      overview: overviewPage, students: studentsPage, grades: gradesPage, attendance: attendancePage,
      announcements: announcementsPage, accounts: accountsPage,
    };
    pageContent.innerHTML = await (renderers[activePage] || overviewPage)();
    pageContent.querySelectorAll("[data-modal]").forEach((button) => button.addEventListener("click", () => openForm(button.dataset.modal)));
    pageContent.querySelectorAll("[data-page-link]").forEach((button) => button.addEventListener("click", () => {
      activePage = button.dataset.pageLink;
      renderNav();
      renderPage();
    }));
  } catch (err) {
    pageContent.innerHTML = `<div class="page-alert">${escapeHtml(err.message)}</div>`;
  }
}
function field(name, label, type = "text", options = {}) {
  const { required = true, placeholder = "", value = "", min = "", max = "", step = "", choices = null, extra = "" } = options;
  const requiredAttr = required ? "required" : "";
  if (choices) return `<div class="field ${extra}"><label for="f-${name}">${label.toUpperCase()}</label><select id="f-${name}" name="${name}" ${requiredAttr}>${choices.map(([val, text]) => `<option value="${escapeHtml(val)}">${escapeHtml(text)}</option>`).join("")}</select></div>`;
  if (type === "textarea") return `<div class="field ${extra}"><label for="f-${name}">${label.toUpperCase()}</label><textarea id="f-${name}" name="${name}" placeholder="${escapeHtml(placeholder)}" ${requiredAttr} maxlength="4000">${escapeHtml(value)}</textarea></div>`;
  return `<div class="field ${extra}"><label for="f-${name}">${label.toUpperCase()}</label><input id="f-${name}" name="${name}" type="${type}" placeholder="${escapeHtml(placeholder)}" value="${escapeHtml(value)}" ${requiredAttr} ${min ? `min="${min}"` : ""} ${max ? `max="${max}"` : ""} ${step ? `step="${step}"` : ""}></div>`;
}
async function openForm(kind) {
  let students = dashboard.students;
  if (kind === "account" || kind === "student" || kind === "grade" || kind === "attendance") {
    try { students = (await api("/api/students")).students; } catch (err) { showToast(err.message, true); return; }
  }
  const studentChoices = students.map((student) => [student.id, `${student.full_name} · ${student.admission_no}`]);
  const studentField = field("student_id", "Student", "select", { choices: studentChoices });
  const forms = {
    student: {
      title: "Register a student", endpoint: "/api/students",
      fields: field("admission_no", "Admission number", "text", { placeholder: "e.g. LIS-2026-014" }) + field("full_name", "Student's full name") + field("class_name", "Class / year group", "text", { placeholder: "e.g. Year 8" }) + field("date_of_birth", "Date of birth", "date", { required: false }) + field("guardian_name", "Parent / guardian name", "text", { required: false }) + field("guardian_phone", "Guardian phone", "tel", { required: false }) + `<div class="field span-two"><label for="f-photo">PASSPORT-STYLE PHOTO</label><input id="f-photo" name="photo" type="file" accept="image/jpeg,image/png"><small class="photo-help">Optional · JPEG or PNG · 2 MB maximum</small><img id="photo-preview" class="photo-preview" alt="Selected student photo preview" hidden></div>`,
    },
    account: {
      title: "Create a portal account", endpoint: "/api/users",
      fields: field("name", "Full name") + field("email", "Email address", "email") + field("password", "Temporary password (12+ characters)", "password", { min: 12 }) + field("role", "Portal role", "select", { choices: [["teacher", "Teacher"], ["student", "Student"], ["parent", "Parent / guardian"]] }) + `<div id="linked-student-field" class="span-two">${studentField}</div>`,
    },
    grade: {
      title: "Record a grade", endpoint: "/api/grades",
      fields: `<div class="span-two">${studentField}</div>` + field("subject", "Subject") + field("term", "Term", "text", { placeholder: "e.g. Term 1 · 2026" }) + field("score", "Score", "number", { min: "0", step: "0.01" }) + field("max_score", "Maximum score", "number", { min: "0.01", step: "0.01", value: "100" }),
    },
    attendance: {
      title: "Record attendance", endpoint: "/api/attendance",
      fields: `<div class="span-two">${studentField}</div>` + field("attendance_date", "Date", "date", { value: new Date().toISOString().slice(0, 10) }) + field("status", "Status", "select", { choices: [["present", "Present"], ["absent", "Absent"], ["late", "Late"]] }),
    },
    announcement: {
      title: "Post an announcement", endpoint: "/api/announcements",
      fields: field("title", "Title") + field("audience", "Visible to", "select", { choices: [["all", "Everyone"], ["staff", "Staff only"], ["families", "Students & parents"]] }) + field("body", "Message", "textarea", { extra: "span-two" }),
    },
  };
  const config = forms[kind];
  if (!config) return;
  modal.innerHTML = `<div class="modal-header"><h2>${config.title}</h2><button class="modal-close" type="button" aria-label="Close">×</button></div><form class="modal-form" id="record-form"><div class="form-grid">${config.fields}</div><p class="auth-error" id="form-error" role="alert"></p><div class="form-actions"><button type="button" class="button-secondary modal-cancel">Cancel</button><button class="button-primary" type="submit">Save record</button></div></form>`;
  modal.showModal();
  modal.querySelector(".modal-close").addEventListener("click", () => modal.close());
  modal.querySelector(".modal-cancel").addEventListener("click", () => modal.close());
  const roleSelect = modal.querySelector("#f-role");
  if (roleSelect) roleSelect.addEventListener("change", () => {
    modal.querySelector("#linked-student-field").hidden = !["student", "parent"].includes(roleSelect.value);
    const select = modal.querySelector("#f-student_id");
    select.required = ["student", "parent"].includes(roleSelect.value);
  });
  if (roleSelect) roleSelect.dispatchEvent(new Event("change"));
  const photoInput = modal.querySelector("#f-photo");
  if (photoInput) photoInput.addEventListener("change", () => {
    const photo = photoInput.files[0];
    const preview = modal.querySelector("#photo-preview");
    if (preview.src.startsWith("blob:")) URL.revokeObjectURL(preview.src);
    if (!photo) {
      preview.hidden = true;
      preview.removeAttribute("src");
      return;
    }
    if (!["image/jpeg", "image/png"].includes(photo.type) || photo.size > 2 * 1024 * 1024) {
      preview.hidden = true;
      preview.removeAttribute("src");
      return;
    }
    preview.src = URL.createObjectURL(photo);
    preview.hidden = false;
  });
  modal.querySelector("#record-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const button = form.querySelector('button[type="submit"]');
    const error = form.querySelector("#form-error");
    button.disabled = true;
    error.textContent = "";
    const body = Object.fromEntries(new FormData(form));
    const photo = photoInput?.files[0];
    delete body.photo;
    if (photo && (!["image/jpeg", "image/png"].includes(photo.type) || photo.size > 2 * 1024 * 1024)) {
      error.textContent = "Choose a JPEG or PNG photo that is 2 MB or smaller.";
      button.disabled = false;
      return;
    }
    if (photo) {
      try {
        body.photo_data = await new Promise((resolve, reject) => {
          const reader = new FileReader();
          reader.addEventListener("load", () => resolve(reader.result));
          reader.addEventListener("error", () => reject(new Error("Could not read the selected photo.")));
          reader.readAsDataURL(photo);
        });
      } catch (err) {
        error.textContent = err.message;
        button.disabled = false;
        return;
      }
    }
    if (body.student_id) body.student_id = Number(body.student_id);
    if (body.score !== undefined) body.score = Number(body.score);
    if (body.max_score !== undefined) body.max_score = Number(body.max_score);
    try {
      await api(config.endpoint, { method: "POST", body: JSON.stringify(body) });
      modal.close();
      showToast(kind === "student" ? (photo ? "Student registered and passport photo saved." : "Student registered.") : kind === "account" ? "User account created." : kind === "grade" ? "Grade recorded." : kind === "attendance" ? "Attendance saved." : "Announcement published.");
      await loadDashboard();
      if (kind === "account" && activePage === "accounts") await renderPage();
    } catch (err) {
      error.textContent = err.message;
      button.disabled = false;
    }
  });
}
document.querySelector("#logout-button").addEventListener("click", async () => {
  try { await api("/api/logout", { method: "POST", body: "{}" }); } catch (err) { showToast(err.message, true); }
  currentUser = null;
  dashboard = null;
  activePage = "overview";
  showAuth(false);
});
document.querySelector("#menu-toggle").addEventListener("click", () => document.querySelector("#sidebar").classList.toggle("open"));
document.querySelector("#modal").addEventListener("click", (event) => {
  if (event.target === modal) modal.close();
});
modal.addEventListener("close", () => {
  const preview = modal.querySelector("#photo-preview");
  if (preview?.src.startsWith("blob:")) URL.revokeObjectURL(preview.src);
});
bootstrap();
