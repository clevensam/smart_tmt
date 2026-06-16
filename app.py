import os
import time
import sqlite3
from collections import defaultdict
from io import BytesIO
from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fpdf import FPDF

from config import STUDENTS_DB_PATH, ENTRIES_DB_PATH, TEMPLATE_DIR

app = FastAPI(title="MUST Timetable Downloader")

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


def get_student(regno: str):
    conn = sqlite3.connect(STUDENTS_DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT regno, page_number, name, programme FROM students WHERE regno = ?",
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
    return await _download(regno)


@app.post("/download")
async def download_post(regno: str = Form(...)):
    return await _download(regno)


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
    pdf.cell(0, 7, "CONTINUOUS ASSESSMENT (TEST 02) - SEMESTER II 2025/2026", align="C")
    pdf.ln(12)

    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 7, latin1(f"Student: {student['name']}"))
    pdf.ln(7)
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 6, latin1(f"Reg No: {student['regno']}"))
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


async def _download(regno: str):
    regno = regno.strip()
    student = get_student(regno)
    if not student:
        raise HTTPException(status_code=404, detail=f"Registration number {regno} not found")

    entries = _get_entries(regno)
    if not entries:
        raise HTTPException(status_code=404, detail=f"No timetable entries found for {regno}")

    buf = _generate_timetable_pdf(student, entries)
    filename = f"{regno}.pdf"
    return StreamingResponse(
        buf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


@app.get("/api/lookup/{regno}")
async def api_lookup(regno: str):
    student = get_student(regno.strip())
    if not student:
        raise HTTPException(status_code=404, detail="Not found")
    return {
        "regno": student["regno"],
        "name": student["name"],
        "programme": student["programme"],
        "page_number": student["page_number"]
    }


@app.get("/api/venue-share")
async def venue_share(course: str = Query(...)):
    conn = sqlite3.connect(ENTRIES_DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT student_name as name, regno, student_programme as programme,
               venue, exam_date, time_slot, course_name
        FROM timetable_entries
        WHERE course_code = ?
        ORDER BY venue, student_name
    """, (course.strip(),)).fetchall()
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
        "course_code": course.strip(),
        "course_name": rows[0]["course_name"],
        "venues": list(venues_map.values())
    }


@app.get("/api/venue-share/download")
async def venue_share_download(course: str = Query(...), venue: str = Query(default=None)):
    conn = sqlite3.connect(ENTRIES_DB_PATH)
    conn.row_factory = sqlite3.Row

    if venue:
        rows = conn.execute("""
            SELECT student_name as name, regno, student_programme as programme,
                   venue, exam_date, time_slot, course_name
            FROM timetable_entries
            WHERE course_code = ? AND venue = ?
            ORDER BY student_name
        """, (course.strip(), venue.strip())).fetchall()
    else:
        rows = conn.execute("""
            SELECT student_name as name, regno, student_programme as programme,
                   venue, exam_date, time_slot, course_name
            FROM timetable_entries
            WHERE course_code = ?
            ORDER BY venue, student_name
        """, (course.strip(),)).fetchall()
    conn.close()

    if not rows:
        raise HTTPException(
            status_code=404,
            detail=f"No entries found for course code '{course}'"
        )

    pdf = FPDF()
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "MUST Timetable", align="C")
    pdf.ln(6)
    pdf.set_font("Helvetica", "", 12)
    pdf.cell(0, 8, "Venue Sharing Report", align="C")
    pdf.ln(12)

    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 7, latin1(f"Course: {course.strip()} - {rows[0]['course_name']}"))
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

        filename = f"{course.strip().replace(' ', '_')}_{venue.strip().replace(' ', '_')}.pdf"
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

        filename = f"{course.strip().replace(' ', '_')}_venue_sharing.pdf"

    pdf.ln(8)
    pdf.set_text_color(128, 128, 128)
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(0, 6, "Developed by Mopao and MSS", align="C")

    buf = BytesIO()
    pdf.output(buf)
    buf.seek(0)

    return StreamingResponse(
        buf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )
