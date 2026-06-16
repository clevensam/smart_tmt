import sqlite3
import re
import fitz

from config import PDF_PATH, STUDENTS_DB_PATH, ENTRIES_DB_PATH


def create_students_db():
    conn = sqlite3.connect(STUDENTS_DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS students (
            regno TEXT PRIMARY KEY,
            page_number INTEGER NOT NULL,
            name TEXT,
            programme TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_page ON students(page_number)")
    conn.commit()
    return conn


def create_entries_db():
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
    return conn


def parse_page(page):
    dict_blocks = page.get_text("dict")["blocks"]

    name_re = re.compile(r"^\d+\.\s+(.+)")
    reg_re = re.compile(r"Reg:\s*(\d+)")
    prog_re = re.compile(r"Programme:\s*(.+)")

    regno = name = programme = None
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
                x_mid = (x0 + x1) / 2

                if regno is None:
                    m = name_re.match(text)
                    if m:
                        name = m.group(1).strip()
                    m = reg_re.search(text)
                    if m:
                        regno = m.group(1)
                    m = prog_re.match(text)
                    if m:
                        programme = m.group(1).strip()

                spans.append({
                    "text": text,
                    "x_mid": x_mid,
                    "y_mid": (y0 + y1) / 2,
                })

    if regno is None:
        return None, []

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

    return (regno, name, programme), entries


def scrape():
    print(f"Opening PDF: {PDF_PATH}")
    doc = fitz.open(PDF_PATH)
    total = len(doc)
    print(f"Total pages: {total}")

    students_conn = create_students_db()
    entries_conn = create_entries_db()
    students_cursor = students_conn.cursor()
    entries_cursor = entries_conn.cursor()

    students_cursor.execute("DELETE FROM students")
    entries_cursor.execute("DELETE FROM timetable_entries")

    students_batch = []
    entries_batch = []
    for page_num in range(total):
        page = doc[page_num]
        student, entries = parse_page(page)

        if student is not None:
            regno, name, programme = student
            students_batch.append((regno, page_num + 1, name, programme))
            for entry in entries:
                code, cname, date, day, time, venue = entry
                entries_batch.append((regno, name, programme, code, cname, date, day, time, venue))
        else:
            print(f"  [WARN] No regno found on page {page_num + 1}")

        if len(students_batch) >= 100:
            students_cursor.executemany(
                "INSERT OR REPLACE INTO students (regno, page_number, name, programme) VALUES (?, ?, ?, ?)",
                students_batch
            )
            entries_cursor.executemany(
                "INSERT INTO timetable_entries (regno, student_name, student_programme, course_code, course_name, exam_date, day, time_slot, venue) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                entries_batch
            )
            students_conn.commit()
            entries_conn.commit()
            students_batch = []
            entries_batch = []

        if (page_num + 1) % 1000 == 0:
            print(f"  Progress: {page_num + 1}/{total} pages processed")

    if students_batch:
        students_cursor.executemany(
            "INSERT OR REPLACE INTO students (regno, page_number, name, programme) VALUES (?, ?, ?, ?)",
            students_batch
        )
        entries_cursor.executemany(
            "INSERT INTO timetable_entries (regno, student_name, student_programme, course_code, course_name, exam_date, day, time_slot, venue) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            entries_batch
        )
        students_conn.commit()
        entries_conn.commit()

    doc.close()
    students_conn.close()
    entries_conn.close()
    print(f"Done. Scraped {total} pages.")


if __name__ == "__main__":
    scrape()
