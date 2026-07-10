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


def load_ue_map():
    conn = sqlite3.connect(STUDENTS_DB_PATH)
    rows = conn.execute(
        "SELECT regno, name, programme, ue_number FROM students WHERE ue_number IS NOT NULL AND ue_number != ''"
    ).fetchall()
    conn.close()
    ue_map = {}
    for regno, name, programme, ue_number in rows:
        ue_map[ue_number] = (regno, name, programme)
    return ue_map


def load_name_map():
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

    print("Loading UE number → regno map...")
    ue_map = load_ue_map()
    print(f"  Found {len(ue_map)} students with UE numbers")

    print("Loading name → regno fallback map...")
    name_map = load_name_map()
    print(f"  Found {len(name_map)} students with names")

    print(f"Opening PDF: {PDF_PATH}")
    doc = fitz.open(PDF_PATH)
    total = len(doc)
    print(f"Total pages: {total}")

    entries_conn = sqlite3.connect(ENTRIES_DB_PATH)
    entries_cursor = entries_conn.cursor()
    entries_cursor.execute("DELETE FROM timetable_entries")
    entries_conn.commit()

    entries_batch = []
    ue_matched = 0
    name_matched = 0
    no_name_pages = 0
    no_ue_pages = 0
    unmatched_ue = 0
    fallback_skipped = 0
    matched_entries = 0

    for page_num in range(total):
        page = doc[page_num]
        name, ue_number, programme = parse_page_info(page)

        if not name:
            no_name_pages += 1
            if (page_num + 1) % 1000 == 0:
                print(f"  Progress: {page_num + 1}/{total} pages processed (no name)")
            continue

        regno = student_name = student_prog = None

        # Primary: look up by UE number
        if ue_number and ue_number in ue_map:
            regno, student_name, student_prog = ue_map[ue_number]
            ue_matched += 1
        elif ue_number:
            # UE number exists in PDF but not in DB
            unmatched_ue += 1
            if unmatched_ue <= 5:
                print(f"  [UNMATCHED UE] '{ue_number}' for '{name}' (page {page_num + 1})")
        else:
            no_ue_pages += 1

        # Fallback: look up by name if UE didn't work
        if regno is None and name:
            key = name.lower().strip()
            student = name_map.get(key)
            if student:
                regno, student_name, student_prog = student
                name_matched += 1
                if name_matched <= 5:
                    print(f"  [NAME FALLBACK] '{name}' matched by name (page {page_num + 1})")
            else:
                fallback_skipped += 1

        if regno is None:
            if (page_num + 1) % 1000 == 0:
                print(f"  Progress: {page_num + 1}/{total} pages processed (skipped)")
            continue

        entries = parse_page_entries(page)
        for entry in entries:
            code, cname, date, day, time, venue = entry
            entries_batch.append((regno, student_name, student_prog, code, cname, date, day, time, venue))
            matched_entries += 1

        if len(entries_batch) >= 100:
            entries_cursor.executemany(
                "INSERT INTO timetable_entries (regno, student_name, student_programme, course_code, course_name, exam_date, day, time_slot, venue) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                entries_batch
            )
            entries_conn.commit()
            entries_batch = []

        if (page_num + 1) % 1000 == 0:
            print(f"  Progress: {page_num + 1}/{total} pages (UE:{ue_matched} name:{name_matched} unmatched_ue:{unmatched_ue} no_ue:{no_ue_pages} skipped:{fallback_skipped} entries:{matched_entries})")

    if entries_batch:
        entries_cursor.executemany(
            "INSERT INTO timetable_entries (regno, student_name, student_programme, course_code, course_name, exam_date, day, time_slot, venue) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            entries_batch
        )
        entries_conn.commit()

    doc.close()
    entries_conn.close()

    print(f"\nDone. Processed {total} pages.")
    print(f"  Matched by UE number: {ue_matched}")
    print(f"  Matched by name (fallback): {name_matched}")
    print(f"  No name on page: {no_name_pages}")
    print(f"  No UE on page: {no_ue_pages}")
    print(f"  UE in PDF but not in DB: {unmatched_ue}")
    print(f"  Unmatched (skipped): {fallback_skipped}")
    print(f"  Timetable entries inserted: {matched_entries}")


if __name__ == "__main__":
    scrape()
