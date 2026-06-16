import sqlite3
import re
import fitz

from config import PDF_PATH, ENTRIES_DB_PATH

HEADER = ["No.", "Exam Date", "Day", "Time Slot", "Code", "Module Name", "Venue"]


def create_db():
    conn = sqlite3.connect(ENTRIES_DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
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


def parse_entries_from_text(text, regno, name, programme):
    lines = [l.strip() for l in text.split("\n")]

    header_start = -1
    for i in range(len(lines) - 6):
        if lines[i:i+7] == HEADER:
            header_start = i
            break

    if header_start == -1:
        return []

    row_start = header_start + 7
    entries = []
    i = row_start
    while i + 6 < len(lines):
        row_num = lines[i]
        if not row_num.isdigit():
            break

        date = lines[i + 1]
        day = lines[i + 2]
        time_slot = lines[i + 3]
        code = lines[i + 4]
        module = lines[i + 5]
        venue = lines[i + 6]

        if not date or not code or not venue:
            break

        entries.append((
            regno, name, programme,
            code, module, date, day, time_slot, venue,
        ))
        i += 7

    return entries


def scrape():
    print(f"Opening PDF: {PDF_PATH}")
    doc = fitz.open(PDF_PATH)
    total = len(doc)
    print(f"Total pages: {total}")

    conn = create_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM timetable_entries")

    name_re = re.compile(r"^\d+\.\s+(.+)")
    reg_re = re.compile(r"Reg:\s*(\d+)")
    prog_re = re.compile(r"Programme:\s*(.+)")

    batch = []
    for page_num in range(total):
        page = doc[page_num]
        text = page.get_text()
        lines = text.split("\n")

        regno = name = programme = None
        for line in lines:
            line = line.strip()
            m = name_re.match(line)
            if m:
                name = m.group(1).strip()
                continue
            m = reg_re.search(line)
            if m:
                regno = m.group(1)
                continue
            m = prog_re.match(line)
            if m:
                programme = m.group(1).strip()

        if regno is None:
            print(f"  [WARN] No regno found on page {page_num + 1}")
            continue

        for entry in parse_entries_from_text(text, regno, name, programme):
            batch.append(entry)

        if len(batch) >= 100:
            cursor.executemany(
                "INSERT INTO timetable_entries (regno, student_name, student_programme, course_code, course_name, exam_date, day, time_slot, venue) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                batch
            )
            conn.commit()
            batch = []

        if (page_num + 1) % 1000 == 0:
            print(f"  Progress: {page_num + 1}/{total} pages processed")

    if batch:
        cursor.executemany(
            "INSERT INTO timetable_entries (regno, student_name, student_programme, course_code, course_name, exam_date, day, time_slot, venue) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            batch
        )
        conn.commit()

    doc.close()
    conn.close()
    print(f"Done. Scraped {total} pages.")


if __name__ == "__main__":
    scrape()
