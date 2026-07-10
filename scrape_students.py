import sqlite3
import re
import fitz

from config import PDF_PATH, STUDENTS_DB_PATH


def create_db():
    conn = sqlite3.connect(STUDENTS_DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS students (
            regno TEXT PRIMARY KEY,
            page_number INTEGER NOT NULL,
            name TEXT,
            programme TEXT,
            ue_number TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_page ON students(page_number)")
    conn.commit()
    return conn


def scrape():
    print(f"Opening PDF: {PDF_PATH}")
    doc = fitz.open(PDF_PATH)
    total = len(doc)
    print(f"Total pages: {total}")

    conn = create_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM students")

    name_re = re.compile(r"^\d+\.\s+(.+)")
    reg_re = re.compile(r"Reg:\s*(\d+)")
    prog_re = re.compile(r"Programme:\s*(.+)")
    ue_re = re.compile(r"UE No:\s*(\S+)")

    batch = []
    for page_num in range(total):
        page = doc[page_num]
        lines = page.get_text().split("\n")

        regno = name = programme = ue_number = None
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
                continue
            m = ue_re.search(line)
            if m:
                ue_number = m.group(1)

        if regno:
            batch.append((regno, page_num + 1, name, programme, ue_number))
        else:
            print(f"  [WARN] No regno found on page {page_num + 1}")

        if len(batch) >= 100:
            cursor.executemany(
                "INSERT OR REPLACE INTO students (regno, page_number, name, programme, ue_number) VALUES (?, ?, ?, ?, ?)",
                batch
            )
            conn.commit()
            batch = []

        if (page_num + 1) % 1000 == 0:
            print(f"  Progress: {page_num + 1}/{total} pages processed")

    if batch:
        cursor.executemany(
            "INSERT OR REPLACE INTO students (regno, page_number, name, programme, ue_number) VALUES (?, ?, ?, ?, ?)",
            batch
        )
        conn.commit()

    doc.close()
    conn.close()
    print(f"Done. Scraped {total} pages.")


if __name__ == "__main__":
    scrape()
