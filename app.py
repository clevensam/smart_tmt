import os
import re
import secrets
import time
import sqlite3
from datetime import datetime
from collections import defaultdict
from io import BytesIO
from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fpdf import FPDF
from starlette import status

from config import STUDENTS_DB_PATH, ENTRIES_DB_PATH, TEMPLATE_DIR

ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")
ADMIN_COOKIE = "admin_session"
_sessions = {}

REGNO_PATTERN = re.compile(r"^\d{14}$")
COURSE_PATTERN = re.compile(r"^[A-Za-z]{2,4}\s+\d{4}$")


def validate_regno(regno: str) -> str:
    regno = regno.strip()
    if not regno:
        raise HTTPException(status_code=422, detail="Registration number is required")
    if not REGNO_PATTERN.match(regno):
        raise HTTPException(status_code=422, detail="Registration number must be exactly 14 digits (e.g., 25100534760075)")
    return regno


def validate_course_code(code: str) -> str:
    code = code.strip().upper()
    if not code:
        raise HTTPException(status_code=422, detail="Course code is required")
    if not COURSE_PATTERN.match(code):
        raise HTTPException(status_code=422, detail="Invalid course code format. Expected format: 2-4 letters followed by 4 digits (e.g., CS 8115, ET 6312)")
    return code

app = FastAPI(title="MUST UE Timetable Downloader")

RATE_LIMIT = 30
RATE_WINDOW = 60
_rate_store = defaultdict(list)


@app.middleware("http")
async def rate_limit(request: Request, call_next):
    client = request.client.host if request.client else "unknown"
    now = time.time()
    window = _rate_store[client]
    window[:] = [t for t in window if t > now - RATE_WINDOW]
    if len(window) >= RATE_LIMIT:
        return HTMLResponse("Rate limit exceeded", status_code=429)
    window.append(now)
    return await call_next(request)



def latin1(text):
    return text.replace("\u2014", "-").replace("\u2013", "-").replace("\u2018", "'").replace("\u2019", "'").replace("\u201c", '"').replace("\u201d", '"').encode("latin-1", "replace").decode("latin-1")


def init_admin_db():
    conn = sqlite3.connect(ENTRIES_DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS admin_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            regno TEXT,
            student_name TEXT,
            request_type TEXT NOT NULL,
            description TEXT,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            resolved_at TIMESTAMP,
            admin_note TEXT
        )
    """)
    conn.commit()
    conn.close()


init_admin_db()


def migrate_students_db():
    conn = sqlite3.connect(STUDENTS_DB_PATH)
    try:
        conn.execute("ALTER TABLE students ADD COLUMN ue_number TEXT")
        conn.commit()
        print("Added ue_number column to students table")
    except sqlite3.OperationalError:
        pass
    conn.close()


migrate_students_db()


def get_student(regno: str):
    conn = sqlite3.connect(STUDENTS_DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT regno, page_number, name, programme, ue_number FROM students WHERE regno = ?",
        (regno,)
    ).fetchone()
    conn.close()
    return row


@app.get("/", response_class=HTMLResponse)
async def index():
    with open(f"{TEMPLATE_DIR}/index.html") as f:
        return HTMLResponse(f.read())


@app.get("/download")
async def download_get(regno: str = Query(...)):
    regno = validate_regno(regno)
    return await _download(regno)


@app.post("/download")
async def download_post(regno: str = Form(...)):
    regno = validate_regno(regno)
    return await _download(regno)


@app.get("/view")
async def view_get(regno: str = Query(...)):
    regno = validate_regno(regno)
    return await _view(regno)


def _get_entries(regno: str):
    conn = sqlite3.connect(ENTRIES_DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT exam_date, day, time_slot, course_code, course_name, venue
        FROM timetable_entries
        WHERE regno = ?
        ORDER BY exam_date, time_slot
    """, (regno,)).fetchall()
    conn.close()
    return rows


def _generate_timetable_pdf(student, entries):
    pdf = FPDF()
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 15)
    pdf.cell(0, 10, "MBEYA UNIVERSITY OF SCIENCE AND TECHNOLOGY", align="C")
    pdf.ln(7)
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 7, "SEMESTER II EXAMINATIONS - 2025/2026", align="C")
    pdf.ln(12)

    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 7, latin1(f"Student: {student['name']}"))
    pdf.ln(7)
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 6, latin1(f"Reg No: {student['regno']}"))
    pdf.ln(6)
    if student["ue_number"]:
        pdf.cell(0, 6, latin1(f"UE No: {student['ue_number']}"))
        pdf.ln(6)
    pdf.cell(0, 6, latin1(f"Programme: {student['programme']}"))
    pdf.ln(10)

    col_w = [28, 24, 30, 58, 40]
    headers = ["Date", "Day", "Time", "Course", "Venue"]
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_fill_color(24, 81, 47)
    pdf.set_text_color(255, 255, 255)
    for i, h in enumerate(headers):
        pdf.cell(col_w[i], 7, h, border=1, fill=True, align="C")
    pdf.ln()

    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Helvetica", "", 9)
    for entry in entries:
        pdf.cell(col_w[0], 6, latin1(entry["exam_date"]), border=1, align="C")
        pdf.cell(col_w[1], 6, latin1(entry["day"]), border=1, align="C")
        pdf.cell(col_w[2], 6, latin1(entry["time_slot"]), border=1, align="C")
        pdf.cell(col_w[3], 6, latin1(f"{entry['course_code']} {entry['course_name']}"), border=1)
        pdf.cell(col_w[4], 6, latin1(entry["venue"]), border=1, align="C")
        pdf.ln()

    pdf.ln(8)
    pdf.set_text_color(128, 128, 128)
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 6, f"Total entries: {len(entries)}", align="C")
    pdf.ln(7)
    pdf.cell(0, 6, "Developed by Mopao and MSS", align="C")

    buf = BytesIO()
    pdf.output(buf)
    buf.seek(0)
    return buf


async def _serve_pdf(regno: str, inline: bool = False):
    student = get_student(regno)
    if not student:
        raise HTTPException(status_code=404, detail=f"Registration number {regno} not found")

    entries = _get_entries(regno)
    if not entries:
        raise HTTPException(status_code=404, detail=f"No timetable entries found for {regno}")

    buf = _generate_timetable_pdf(student, entries)
    disposition = "inline" if inline else "attachment"
    filename = f"{regno}.pdf"
    return StreamingResponse(
        buf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'{disposition}; filename="{filename}"'}
    )


async def _download(regno: str):
    return await _serve_pdf(regno, inline=False)


async def _view(regno: str):
    return await _serve_pdf(regno, inline=True)


@app.get("/api/lookup/{regno}")
async def api_lookup(regno: str):
    regno = validate_regno(regno)
    student = get_student(regno)
    if not student:
        raise HTTPException(status_code=404, detail=f"Registration number {regno} not found")
    return {
        "regno": student["regno"],
        "ue_number": student["ue_number"],
        "name": student["name"],
        "programme": student["programme"],
        "page_number": student["page_number"]
    }


@app.get("/api/venue-share")
async def venue_share(course: str = Query(...)):
    course = validate_course_code(course)
    conn = sqlite3.connect(ENTRIES_DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT student_name as name, regno, student_programme as programme,
               venue, exam_date, time_slot, course_name
        FROM timetable_entries
        WHERE course_code = ?
        ORDER BY venue, student_name
    """, (course,)).fetchall()
    conn.close()

    if not rows:
        raise HTTPException(
            status_code=404,
            detail=f"No entries found for course code '{course}'"
        )

    venues_map = {}
    for row in rows:
        v = row["venue"]
        if v not in venues_map:
            venues_map[v] = {
                "venue": v,
                "exam_date": row["exam_date"],
                "time_slot": row["time_slot"],
                "students": []
            }
        venues_map[v]["students"].append({
            "name": row["name"],
            "regno": row["regno"],
            "programme": row["programme"]
        })

    return {
        "course_code": course,
        "course_name": rows[0]["course_name"],
        "venues": list(venues_map.values())
    }


async def _venue_pdf(course: str, venue: str | None = None, inline: bool = False):
    conn = sqlite3.connect(ENTRIES_DB_PATH)
    conn.row_factory = sqlite3.Row

    if venue:
        rows = conn.execute("""
            SELECT student_name as name, regno, student_programme as programme,
                   venue, exam_date, time_slot, course_name
            FROM timetable_entries
            WHERE course_code = ? AND venue = ?
            ORDER BY student_name
        """, (course, venue.strip())).fetchall()
    else:
        rows = conn.execute("""
            SELECT student_name as name, regno, student_programme as programme,
                   venue, exam_date, time_slot, course_name
            FROM timetable_entries
            WHERE course_code = ?
            ORDER BY venue, student_name
        """, (course,)).fetchall()
    conn.close()

    if not rows:
        raise HTTPException(
            status_code=404,
            detail=f"No entries found for course code '{course}'"
        )

    pdf = FPDF()
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "MUST UE Timetable", align="C")
    pdf.ln(6)
    pdf.set_font("Helvetica", "", 12)
    pdf.cell(0, 8, "Venue Sharing Report", align="C")
    pdf.ln(12)

    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 7, latin1(f"Course: {course} - {rows[0]['course_name']}"))
    pdf.ln(7)

    if venue:
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(0, 7, latin1(f"Venue: {venue.strip()} ({len(rows)} students)"))
        pdf.ln(10)

        pdf.set_text_color(0, 0, 0)
        pdf.set_font("Helvetica", "", 9)
        for i, s in enumerate(rows, 1):
            pdf.cell(0, 5, latin1(f"  {i}. {s['name']}  -  {s['programme']}"))
            pdf.ln(7)

        pdf.ln(3)
        pdf.set_font("Helvetica", "B", 10)
        pdf.set_text_color(24, 81, 47)
        pdf.cell(0, 7, f"Total: {len(rows)} students")

        filename = f"{course.replace(' ', '_')}_{venue.strip().replace(' ', '_')}.pdf"
    else:
        venues_map = {}
        for row in rows:
            v = row["venue"]
            if v not in venues_map:
                venues_map[v] = {
                    "venue": v,
                    "exam_date": row["exam_date"],
                    "time_slot": row["time_slot"],
                    "students": []
                }
            venues_map[v]["students"].append(row)

        total_students = 0
        pdf.set_font("Helvetica", "B", 10)
        for v_name, v_data in venues_map.items():
            pdf.set_fill_color(24, 81, 47)
            pdf.set_text_color(255, 255, 255)
            pdf.cell(0, 7, latin1(f"  Venue: {v_name} ({len(v_data['students'])} students)"), fill=True)
            pdf.ln(7)

            pdf.set_text_color(24, 81, 47)
            pdf.set_font("Helvetica", "", 9)
            pdf.cell(0, 5, latin1(f"  {v_data['exam_date']}  |  {v_data['time_slot']}"))
            pdf.ln(6)

            pdf.set_text_color(0, 0, 0)
            pdf.set_font("Helvetica", "", 9)
            for i, s in enumerate(v_data["students"], 1):
                pdf.cell(0, 5, latin1(f"  {i}. {s['name']}  -  {s['programme']}"))
                pdf.ln(7)

            total_students += len(v_data["students"])
            pdf.ln(5)

        pdf.set_font("Helvetica", "B", 10)
        pdf.set_text_color(24, 81, 47)
        pdf.cell(0, 7, f"Total students: {total_students}")

        filename = f"{course.replace(' ', '_')}_venue_sharing.pdf"

    pdf.ln(8)
    pdf.set_text_color(128, 128, 128)
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(0, 6, "Developed by Mopao and MSS", align="C")

    buf = BytesIO()
    pdf.output(buf)
    buf.seek(0)

    disposition = "inline" if inline else "attachment"
    return StreamingResponse(
        buf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'{disposition}; filename="{filename}"'}
    )


@app.get("/api/venue-share/download")
async def venue_share_download(course: str = Query(...), venue: str = Query(default=None)):
    course = validate_course_code(course)
    return await _venue_pdf(course, venue, inline=False)


@app.get("/api/venue-share/view")
async def venue_share_view(course: str = Query(...), venue: str = Query(default=None)):
    course = validate_course_code(course)
    return await _venue_pdf(course, venue, inline=True)


# ─── Admin Auth ─────────────────────────────────────────────────────────────────


def _get_session(request: Request) -> str | None:
    token = request.cookies.get(ADMIN_COOKIE)
    return _sessions.get(token)


def _login_redirect():
    resp = RedirectResponse(url="/admin/login", status_code=status.HTTP_303_SEE_OTHER)
    resp.delete_cookie(ADMIN_COOKIE)
    return resp


@app.get("/admin/login", response_class=HTMLResponse)
async def admin_login_page(request: Request):
    if _get_session(request):
        return RedirectResponse(url="/admin", status_code=status.HTTP_303_SEE_OTHER)
    return HTMLResponse("""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Admin Login - MUST UE Timetable</title>
        <style>
            * { margin: 0; padding: 0; box-sizing: border-box; }
            body {
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                background: linear-gradient(135deg, #18512f 0%, #2a8f52 100%);
                min-height: 100vh; display: flex; align-items: center; justify-content: center;
            }
            .login-box {
                background: #fff; border-radius: 12px; padding: 40px; width: 380px;
                box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            }
            .login-box h1 { color: #18512f; text-align: center; margin-bottom: 8px; font-size: 20px; }
            .login-box p { text-align: center; color: #666; margin-bottom: 28px; font-size: 14px; }
            .form-group { margin-bottom: 18px; }
            .form-group label { display: block; margin-bottom: 5px; color: #333; font-weight: 600; font-size: 14px; }
            .form-group input {
                width: 100%; padding: 11px 14px; border: 2px solid #ddd; border-radius: 8px;
                font-size: 14px; transition: border-color .2s;
            }
            .form-group input:focus { border-color: #18512f; outline: none; }
            button {
                width: 100%; padding: 12px; background: #18512f; color: #fff; border: none;
                border-radius: 8px; font-size: 15px; font-weight: 600; cursor: pointer; transition: background .2s;
            }
            button:hover { background: #2a8f52; }
            .error { color: #d32f2f; text-align: center; margin-bottom: 16px; font-size: 14px; }
            .footer { text-align: center; margin-top: 20px; font-size: 12px; color: #999; }
        </style>
    </head>
    <body>
        <div class="login-box">
            <h1>MUST UE Timetable</h1>
            <p>Admin Dashboard Login</p>
            """ + (f'<div class="error">{_err}</div>' if (_err := request.query_params.get("error", "")) else "") + """
            <form method="POST" action="/admin/login">
                <div class="form-group">
                    <label for="username">Username</label>
                    <input type="text" id="username" name="username" required autocomplete="username" autofocus>
                </div>
                <div class="form-group">
                    <label for="password">Password</label>
                    <input type="password" id="password" name="password" required autocomplete="current-password">
                </div>
                <button type="submit">Sign In</button>
            </form>
            <div class="footer">Developed by Mopao and MSS</div>
        </div>
    </body>
    </html>
    """)


@app.post("/admin/login")
async def admin_login(request: Request, username: str = Form(...), password: str = Form(...)):
    if username != ADMIN_USERNAME or password != ADMIN_PASSWORD:
        return RedirectResponse(url="/admin/login?error=Invalid+credentials", status_code=status.HTTP_303_SEE_OTHER)
    token = secrets.token_hex(32)
    _sessions[token] = username
    resp = RedirectResponse(url="/admin", status_code=status.HTTP_303_SEE_OTHER)
    resp.set_cookie(
        key=ADMIN_COOKIE, value=token, httponly=True, samesite="lax",
        max_age=86400,
    )
    return resp


@app.get("/admin/logout")
async def admin_logout(request: Request):
    token = request.cookies.get(ADMIN_COOKIE)
    _sessions.pop(token, None)
    return _login_redirect()


# ─── Admin Routes ──────────────────────────────────────────────────────────────


@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    if not _get_session(request):
        return _login_redirect()
    with open(f"{TEMPLATE_DIR}/admin.html") as f:
        return HTMLResponse(f.read())


def _require_admin(request: Request):
    if not _get_session(request):
        raise HTTPException(status_code=401, detail="Unauthorized")


@app.get("/api/admin/stats")
async def admin_stats(request: Request):
    _require_admin(request)
    s_conn = sqlite3.connect(STUDENTS_DB_PATH)
    s_conn.row_factory = sqlite3.Row
    e_conn = sqlite3.connect(ENTRIES_DB_PATH)
    e_conn.row_factory = sqlite3.Row

    total_students = s_conn.execute("SELECT COUNT(*) FROM students").fetchone()[0]
    with_ue = s_conn.execute("SELECT COUNT(*) FROM students WHERE ue_number IS NOT NULL").fetchone()[0]
    without_ue = total_students - with_ue

    total_entries = e_conn.execute("SELECT COUNT(*) FROM timetable_entries").fetchone()[0]
    total_courses = e_conn.execute("SELECT COUNT(DISTINCT course_code) FROM timetable_entries").fetchone()[0]
    total_venues = e_conn.execute("SELECT COUNT(DISTINCT venue) FROM timetable_entries").fetchone()[0]

    dates = e_conn.execute("SELECT DISTINCT exam_date FROM timetable_entries WHERE exam_date != '' ORDER BY exam_date").fetchall()
    date_range = f"{dates[0][0]} - {dates[-1][0]}" if dates else "N/A"

    # Students by year
    year_map = {"First Year": 0, "Second Year": 0, "Third Year": 0, "Fourth Year": 0, "Other": 0}
    for r in s_conn.execute("SELECT programme FROM students").fetchall():
        prog = r[0] or ""
        if "First Year" in prog or "(First Year)" in prog:
            year_map["First Year"] += 1
        elif "Second Year" in prog or "(Second Year)" in prog:
            year_map["Second Year"] += 1
        elif "Third Year" in prog or "(Third Year)" in prog:
            year_map["Third Year"] += 1
        elif "Fourth Year" in prog or "(Fourth Year)" in prog:
            year_map["Fourth Year"] += 1
        else:
            year_map["Other"] += 1

    students_by_year = [{"year": k, "count": v} for k, v in year_map.items()]

    # Entries per exam date
    date_entries = e_conn.execute("""
        SELECT exam_date, COUNT(*) as cnt FROM timetable_entries
        WHERE exam_date != '' GROUP BY exam_date ORDER BY cnt DESC LIMIT 10
    """).fetchall()
    entries_by_date = [{"date": r["exam_date"], "count": r["cnt"]} for r in date_entries]

    # Top venues
    top_venues = e_conn.execute("""
        SELECT venue, COUNT(*) as cnt FROM timetable_entries
        WHERE venue != '' GROUP BY venue ORDER BY cnt DESC LIMIT 10
    """).fetchall()
    top_venues_list = [{"venue": r["venue"], "count": r["cnt"]} for r in top_venues]

    # Top courses
    top_courses = e_conn.execute("""
        SELECT course_code, course_name, COUNT(*) as cnt FROM timetable_entries
        GROUP BY course_code ORDER BY cnt DESC LIMIT 10
    """).fetchall()
    top_courses_list = [{"code": r["course_code"], "name": r["course_name"], "count": r["cnt"]} for r in top_courses]

    # Students without UE
    no_ue = s_conn.execute("""
        SELECT regno, name, programme FROM students WHERE ue_number IS NULL LIMIT 50
    """).fetchall()
    no_ue_list = [{"regno": r["regno"], "name": r["name"], "programme": r["programme"]} for r in no_ue]

    s_conn.close()
    e_conn.close()

    return {
        "total_students": total_students,
        "with_ue": with_ue,
        "without_ue": without_ue,
        "total_entries": total_entries,
        "total_courses": total_courses,
        "total_venues": total_venues,
        "date_range": date_range,
        "students_by_year": students_by_year,
        "entries_by_date": entries_by_date,
        "top_venues": top_venues_list,
        "top_courses": top_courses_list,
        "students_without_ue": no_ue_list,
    }


@app.get("/api/admin/requests")
async def admin_get_requests(request: Request, status: str = Query(default=None)):
    _require_admin(request)
    conn = sqlite3.connect(ENTRIES_DB_PATH)
    conn.row_factory = sqlite3.Row
    if status:
        rows = conn.execute("""
            SELECT * FROM admin_requests WHERE status = ? ORDER BY created_at DESC
        """, (status,)).fetchall()
    else:
        rows = conn.execute("""
            SELECT * FROM admin_requests ORDER BY created_at DESC
        """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.post("/api/admin/requests")
async def admin_create_request(
    request: Request,
    regno: str = Form(default=""),
    student_name: str = Form(default=""),
    request_type: str = Form(...),
    description: str = Form(default=""),
):
    _require_admin(request)
    conn = sqlite3.connect(ENTRIES_DB_PATH)
    conn.execute("""
        INSERT INTO admin_requests (regno, student_name, request_type, description)
        VALUES (?, ?, ?, ?)
    """, (regno.strip(), student_name.strip(), request_type.strip(), description.strip()))
    conn.commit()
    req_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    return {"id": req_id, "status": "created"}


@app.patch("/api/admin/requests/{req_id}/resolve")
async def admin_resolve_request(request: Request, req_id: int, admin_note: str = Form(default="")):
    _require_admin(request)
    conn = sqlite3.connect(ENTRIES_DB_PATH)
    conn.execute("""
        UPDATE admin_requests
        SET status = 'resolved', resolved_at = ?, admin_note = ?
        WHERE id = ? AND status = 'pending'
    """, (datetime.now().isoformat(), admin_note.strip(), req_id))
    conn.commit()
    affected = conn.total_changes
    conn.close()
    if affected == 0:
        raise HTTPException(status_code=404, detail="Request not found or already resolved")
    return {"status": "resolved"}
