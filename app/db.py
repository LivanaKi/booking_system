import sqlite3
from pathlib import Path
from werkzeug.security import generate_password_hash

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "booking_system.db"


def get_connection():
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        email TEXT NOT NULL,
        login TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL CHECK(role IN ('client', 'admin', 'specialist')),
        status TEXT DEFAULT 'active',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS services (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        category TEXT NOT NULL,
        duration_minutes INTEGER NOT NULL,
        price REAL NOT NULL,
        is_active INTEGER DEFAULT 1
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS specialists (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        specialization TEXT NOT NULL,
        work_start TEXT DEFAULT '09:00',
        work_end TEXT DEFAULT '17:00',
        FOREIGN KEY(user_id) REFERENCES users(id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS specialist_services (
        specialist_id INTEGER NOT NULL,
        service_id INTEGER NOT NULL,
        PRIMARY KEY (specialist_id, service_id),
        FOREIGN KEY(specialist_id) REFERENCES specialists(id),
        FOREIGN KEY(service_id) REFERENCES services(id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS bookings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        client_id INTEGER NOT NULL,
        service_id INTEGER NOT NULL,
        specialist_id INTEGER NOT NULL,
        booking_date TEXT NOT NULL,
        booking_time TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('new', 'confirmed', 'cancelled', 'completed', 'no_show')),
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(client_id) REFERENCES users(id),
        FOREIGN KEY(service_id) REFERENCES services(id),
        FOREIGN KEY(specialist_id) REFERENCES specialists(id),
        UNIQUE(specialist_id, booking_date, booking_time)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS notifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        booking_id INTEGER,
        subject TEXT NOT NULL,
        message TEXT NOT NULL,
        channel TEXT DEFAULT 'email',
        email_to TEXT,
        is_sent INTEGER DEFAULT 0,
        error TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        sent_at TEXT,
        is_read INTEGER DEFAULT 0,
        FOREIGN KEY(user_id) REFERENCES users(id),
        FOREIGN KEY(booking_id) REFERENCES bookings(id)
    )
    """)

    migrate_db(conn)
    conn.commit()
    seed_data(conn)
    conn.close()


def migrate_db(conn):
    cur = conn.cursor()
    columns = [row[1] for row in cur.execute("PRAGMA table_info(users)").fetchall()]
    if "status" not in columns:
        cur.execute("ALTER TABLE users ADD COLUMN status TEXT DEFAULT 'active'")
    if "created_at" not in columns:
        cur.execute("ALTER TABLE users ADD COLUMN created_at TEXT DEFAULT CURRENT_TIMESTAMP")


def seed_data(conn):
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) AS c FROM users")
    if cur.fetchone()["c"] > 0:
        return

    users = [
        ("Адміністратор", "admin@example.com", "admin", generate_password_hash("admin123"), "admin"),
        ("Наталія Клієнт", "client@example.com", "client", generate_password_hash("client123"), "client"),
        ("Спеціаліст Олена", "olena@example.com", "olena", generate_password_hash("spec123"), "specialist"),
        ("Спеціаліст Ірина", "iryna@example.com", "iryna", generate_password_hash("spec123"), "specialist"),
    ]
    cur.executemany("INSERT INTO users(name, email, login, password_hash, role) VALUES (?, ?, ?, ?, ?)", users)

    services = [
        ("Манікюр", "Краса", 60, 600, 1),
        ("Стрижка", "Перукарські послуги", 45, 450, 1),
        ("Консультація", "Консультації", 30, 300, 1),
        ("Фарбування", "Перукарські послуги", 120, 1500, 1),
    ]
    cur.executemany("INSERT INTO services(name, category, duration_minutes, price, is_active) VALUES (?, ?, ?, ?, ?)", services)

    for login, specialization in [("olena", "Манікюр, консультації"), ("iryna", "Стрижка, фарбування")]:
        cur.execute("SELECT id FROM users WHERE login=?", (login,))
        user_id = cur.fetchone()["id"]
        cur.execute("INSERT INTO specialists(user_id, specialization) VALUES (?, ?)", (user_id, specialization))

    cur.execute("SELECT id FROM specialists ORDER BY id")
    sp_ids = [row["id"] for row in cur.fetchall()]
    # Олена: Манікюр + консультація; Ірина: Стрижка + фарбування + консультація
    links = [(sp_ids[0], 1), (sp_ids[0], 3), (sp_ids[1], 2), (sp_ids[1], 4), (sp_ids[1], 3)]
    cur.executemany("INSERT INTO specialist_services(specialist_id, service_id) VALUES (?, ?)", links)

    cur.execute("SELECT id FROM users WHERE login='client'")
    client_id = cur.fetchone()["id"]
    history = [
        (client_id, 1, sp_ids[0], "2026-01-12", "10:00", "confirmed"),
        (client_id, 1, sp_ids[0], "2026-01-12", "11:00", "completed"),
        (client_id, 3, sp_ids[0], "2026-01-13", "14:00", "confirmed"),
        (client_id, 1, sp_ids[0], "2026-01-14", "10:00", "cancelled"),
        (client_id, 4, sp_ids[1], "2026-01-15", "15:00", "confirmed"),
        (client_id, 2, sp_ids[1], "2026-01-16", "09:00", "completed"),
        (client_id, 2, sp_ids[1], "2026-01-17", "16:00", "no_show"),
    ]
    cur.executemany("""
        INSERT OR IGNORE INTO bookings(client_id, service_id, specialist_id, booking_date, booking_time, status)
        VALUES (?, ?, ?, ?, ?, ?)
    """, history)
    conn.commit()
