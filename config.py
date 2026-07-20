import os
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

STUDENTS_DB_PATH = os.path.join(BASE_DIR, "students.db")
ENTRIES_DB_PATH = os.path.join(BASE_DIR, "entries.db")
DONATIONS_DB_PATH = os.path.join(BASE_DIR, "donations.db")
TEMPLATE_DIR = os.path.join(BASE_DIR, "templates")
PDF_PATH = os.path.join(BASE_DIR, "V6_MUST_PERSONALIZED_TIMETABLE_SEMESTER_II_EXAMINATIONS_2025-2026_UE_NUMBERS.pdf")

CLICKPESA_CLIENT_ID = os.environ.get("CLICKPESA_CLIENT_ID", "")
CLICKPESA_API_KEY = os.environ.get("CLICKPESA_API_KEY", "")
