import sqlite3
import re
import fitz
from collections import defaultdict

from config import PDF_PATH, STUDENTS_DB_PATH, ENTRIES_DB_PATH


def ensure_schema():
    conn = sqlite3.connect(STUDENTS_DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS students (
            regno TEXT PRIMARY KEY,
            page_number INTEGER NOT NULL,
            name TEXT,
            programme TEXT,
            ue_number TEXT
        )
    """)
    # Add ue_number column if missing (for existing DBs)
    try:
        conn.execute("ALTER TABLE students ADD COLUMN ue_number TEXT")
    except Exception:
        pass
    conn.commit()
    conn.close()

    conn = sqlite3.connect(ENTRIES_DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS timetable_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            regno TEXT NOT NULL,
            student_name TEXT,
            student_programme TEXT,
            course_code TEXT NOT NULL,
            course_name TEXT,
            exam_date TEXT,
            day TEXT,
            time_slot TEXT,
            venue TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_entry_course ON timetable_entries(course_code)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_entry_regno ON timetable_entries(regno)")
    conn.commit()
    conn.close()


def load_student_map():
    conn = sqlite3.connect(STUDENTS_DB_PATH)
    rows = conn.execute("SELECT regno, name, programme FROM students").fetchall()
    conn.close()
    name_map = {}
    for regno, name, programme in rows:
        key = name.lower().strip() if name else ""
        if key:
            name_map[key] = (regno, name, programme)
    return name_map


def parse_page_info(page):
    lines = page.get_text().split("\n")
    name_re = re.compile(r"^\d+\.\s+(.+)")
    ue_re = re.compile(r"UE No:\s*(\S+)")
    prog_re = re.compile(r"Programme:\s*(.+)")

    name = ue_number = programme = None
    for line in lines:
        line = line.strip()
        m = name_re.match(line)
        if m:
            name = m.group(1).strip()
            continue
        m = ue_re.search(line)
        if m:
            ue_number = m.group(1)
            continue
        m = prog_re.match(line)
        if m:
            programme = m.group(1).strip()

    return name, ue_number, programme


def parse_page_entries(page):
    dict_blocks = page.get_text("dict")["blocks"]

    spans = []
    for block in dict_blocks:
        if "lines" not in block:
            continue
        for line in block["lines"]:
            for span in line["spans"]:
                text = span["text"].strip()
                if not text:
                    continue
                x0, y0, x1, y1 = span["bbox"]
                spans.append({
                    "text": text,
                    "x_mid": (x0 + x1) / 2,
                    "y_mid": (y0 + y1) / 2,
                })

    def col(x):
        if x < 65: return "no"
        if x < 130: return "date"
        if x < 185: return "day"
        if x < 250: return "time"
        if x < 310: return "code"
        if x < 450: return "module"
        if x < 550: return "venue"
        return None

    rows = {}
    for s in spans:
        c = col(s["x_mid"])
        if c is None:
            continue
        y_key = round(s["y_mid"])
        rows.setdefault(y_key, {})[c] = s["text"]

    sorted_y = sorted(rows)
    header_y = None
    for y in sorted_y:
        if rows[y].get("code") == "Code":
            header_y = y
            break

    entries = []
    if header_y is not None:
        for y in sorted_y:
            if y <= header_y:
                continue
            row = rows[y]
            if "code" in row and "venue" in row:
                entries.append((
                    row["code"],
                    row.get("module", ""),
                    row.get("date", ""),
                    row.get("day", ""),
                    row.get("time", ""),
                    row["venue"],
                ))

    return entries


def scrape():
    ensure_schema()

    print("Loading existing student map...")
    student_map = load_student_map()
    print(f"  Found {len(student_map)} students in database")

    print(f"Opening PDF: {PDF_PATH}")
    doc = fitz.open(PDF_PATH)
    total = len(doc)
    print(f"Total pages: {total}")

    students_conn = sqlite3.connect(STUDENTS_DB_PATH)
    students_cursor = students_conn.cursor()

    entries_conn = sqlite3.connect(ENTRIES_DB_PATH)
    entries_cursor = entries_conn.cursor()
    entries_cursor.execute("DELETE FROM timetable_entries")
    entries_conn.commit()

    entries_batch = []
    ue_updated = 0
    ue_skipped = 0
    no_name_pages = 0
    matched_entries = 0

    for page_num in range(total):
        page = doc[page_num]
        name, ue_number, programme = parse_page_info(page)

        if not name:
            no_name_pages += 1
            if (page_num + 1) % 1000 == 0:
                print(f"  Progress: {page_num + 1}/{total} pages processed (no name)")
            continue

        key = name.lower().strip()
        student = student_map.get(key)

        if student is None:
            ue_skipped += 1
            if ue_skipped <= 5:
                print(f"  [SKIP] No match for '{name}' (page {page_num + 1})")
            if (page_num + 1) % 1000 == 0:
                print(f"  Progress: {page_num + 1}/{total} pages processed")
            continue

        regno, old_name, old_prog = student

        if ue_number:
            students_cursor.execute(
                "UPDATE students SET ue_number = ? WHERE regno = ?",
                (ue_number, regno)
            )
            ue_updated += 1

        entries = parse_page_entries(page)
        for entry in entries:
            code, cname, date, day, time, venue = entry
            entries_batch.append((regno, old_name, old_prog, code, cname, date, day, time, venue))
            matched_entries += 1

        if len(entries_batch) >= 100:
            entries_cursor.executemany(
                "INSERT INTO timetable_entries (regno, student_name, student_programme, course_code, course_name, exam_date, day, time_slot, venue) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                entries_batch
            )
            entries_conn.commit()
            entries_batch = []

        if (page_num + 1) % 1000 == 0:
            students_conn.commit()
            print(f"  Progress: {page_num + 1}/{total} pages processed (UE:{ue_updated} skip:{ue_skipped} entries:{matched_entries})")

    if entries_batch:
        entries_cursor.executemany(
            "INSERT INTO timetable_entries (regno, student_name, student_programme, course_code, course_name, exam_date, day, time_slot, venue) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            entries_batch
        )
        entries_conn.commit()

    students_conn.commit()

    doc.close()
    students_conn.close()
    entries_conn.close()

    print(f"\nDone. Processed {total} pages.")
    print(f"  UE numbers updated: {ue_updated}")
    print(f"  No name on page: {no_name_pages}")
    print(f"  Unmatched students (skipped): {ue_skipped}")
    print(f"  Timetable entries inserted: {matched_entries}")


if __name__ == "__main__":
    scrape()
