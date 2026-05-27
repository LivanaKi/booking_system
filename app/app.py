import os
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash
from werkzeug.security import check_password_hash, generate_password_hash
from .db import init_db, get_connection
from .ml_recommender import recommend_slots, forecast_load, forecast_service_demand, classify_all_clients, allocate_clients_to_specialists, optimize_schedule, WORKING_SLOTS
from .notifications import notify_user
from dotenv import load_dotenv

STATUS_LABELS = {
    "new": "Очікує підтвердження",
    "confirmed": "Підтверджено",
    "cancelled": "Скасовано",
    "completed": "Виконано",
    "no_show": "Не з'явився",
}

load_dotenv()

def create_app(test_config=None):
    app = Flask(__name__)
    app.secret_key = os.getenv("SECRET_KEY", "dev-secret-key")
    if test_config:
        app.config.update(test_config)
    init_db()

    @app.context_processor
    def inject_globals():
        return {"status_labels": STATUS_LABELS}

    def login_required(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if "user_id" not in session:
                return redirect(url_for("login"))
            return view(*args, **kwargs)
        return wrapped

    def role_required(*roles):
        def decorator(view):
            @wraps(view)
            def wrapped(*args, **kwargs):
                if session.get("role") not in roles:
                    flash("Недостатньо прав доступу для цієї дії")
                    return redirect(url_for("dashboard"))
                return view(*args, **kwargs)
            return wrapped
        return decorator

    def get_bookings(where="", params=()):
        conn = get_connection()
        rows = conn.execute(f"""
            SELECT b.*, u.name AS client_name, u.email AS client_email,
                   s.name AS service_name, s.duration_minutes, s.price,
                   su.name AS specialist_name, sp.specialization
            FROM bookings b
            JOIN users u ON u.id = b.client_id
            JOIN services s ON s.id = b.service_id
            JOIN specialists sp ON sp.id = b.specialist_id
            JOIN users su ON su.id = sp.user_id
            {where}
            ORDER BY b.booking_date DESC, b.booking_time DESC
        """, params).fetchall()
        conn.close()
        return rows

    def get_notifications(user_id):
        conn = get_connection()
        rows = conn.execute("""
            SELECT * FROM notifications WHERE user_id=?
            ORDER BY created_at DESC LIMIT 10
        """, (user_id,)).fetchall()
        conn.close()
        return rows

    def get_specialist_service_map(conn):
        rows = conn.execute("SELECT specialist_id, service_id FROM specialist_services").fetchall()
        data = {}
        for row in rows:
            data.setdefault(row["specialist_id"], set()).add(row["service_id"])
        return data

    def get_specialist_rows(conn):
        return conn.execute("""
            SELECT specialists.id, users.name, users.email, specialists.specialization, specialists.work_start, specialists.work_end
            FROM specialists JOIN users ON users.id = specialists.user_id
            ORDER BY users.name
        """).fetchall()

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            login_value = request.form.get("login", "").strip()
            password = request.form.get("password", "")
            conn = get_connection()
            user = conn.execute("SELECT * FROM users WHERE login=? OR email=?", (login_value, login_value)).fetchone()
            conn.close()
            if user and user["status"] != "active":
                flash("Ваш акаунт заблоковано адміністратором")
            elif user and check_password_hash(user["password_hash"], password):
                session["user_id"] = user["id"]
                session["name"] = user["name"]
                session["role"] = user["role"]
                return redirect(url_for("dashboard"))
            else:
                flash("Невірний логін/email або пароль")
        return render_template("login.html")

    @app.route("/register", methods=["GET", "POST"])
    def register():
        if "user_id" in session:
            return redirect(url_for("dashboard"))
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            email = request.form.get("email", "").strip().lower()
            login_value = request.form.get("login", "").strip().lower()
            password = request.form.get("password", "")
            password2 = request.form.get("password2", "")

            if not name or not email or not login_value or not password:
                flash("Заповніть усі поля реєстрації")
                return render_template("register.html")
            if len(password) < 6:
                flash("Пароль має містити щонайменше 6 символів")
                return render_template("register.html")
            if password != password2:
                flash("Паролі не збігаються")
                return render_template("register.html")

            conn = get_connection()
            exists = conn.execute("SELECT id FROM users WHERE login=? OR email=?", (login_value, email)).fetchone()
            if exists:
                conn.close()
                flash("Користувач із таким логіном або email уже існує")
                return render_template("register.html")

            cur = conn.execute(
                "INSERT INTO users(name, email, login, password_hash, role, status) VALUES (?, ?, ?, ?, 'client', 'active')",
                (name, email, login_value, generate_password_hash(password))
            )
            user_id = cur.lastrowid
            conn.commit()
            conn.close()
            notify_user(user_id, "Реєстрація успішна", "Ваш клієнтський акаунт створено. Тепер можна створювати записи на послуги.")
            flash("Реєстрацію завершено. Увійдіть у систему.")
            return redirect(url_for("login"))
        return render_template("register.html")

    @app.route("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.route("/")
    @login_required
    def dashboard():
        role = session.get("role")
        notifications = get_notifications(session["user_id"])
        conn = get_connection()
        all_bookings = conn.execute("SELECT * FROM bookings").fetchall()

        if role == "client":
            bookings = get_bookings("WHERE b.client_id=?", (session["user_id"],))
            conn.close()
            return render_template("dashboard_client.html", bookings=bookings, notifications=notifications)

        if role == "specialist":
            specialist = conn.execute("SELECT * FROM specialists WHERE user_id=?", (session["user_id"],)).fetchone()
            bookings = get_bookings("WHERE b.specialist_id=?", (specialist["id"],)) if specialist else []
            load = forecast_load(all_bookings, specialist["id"]) if specialist else []
            conn.close()
            return render_template("dashboard_specialist.html", bookings=bookings, notifications=notifications, load=load)

        bookings = get_bookings()
        stats = {
            "total": len(bookings),
            "new": sum(1 for b in bookings if b["status"] == "new"),
            "confirmed": sum(1 for b in bookings if b["status"] == "confirmed"),
            "cancelled": sum(1 for b in bookings if b["status"] == "cancelled"),
        }
        load_by_specialist = conn.execute("""
            SELECT su.name AS specialist_name, COUNT(b.id) AS total
            FROM specialists sp
            JOIN users su ON su.id=sp.user_id
            LEFT JOIN bookings b ON b.specialist_id=sp.id AND b.status IN ('new','confirmed')
            GROUP BY sp.id, su.name
            ORDER BY total DESC
        """).fetchall()
        services = conn.execute("SELECT * FROM services WHERE is_active=1 ORDER BY name").fetchall()
        clients = conn.execute("SELECT id, name FROM users WHERE role='client' ORDER BY name").fetchall()
        specialists = get_specialist_rows(conn)
        service_map = get_specialist_service_map(conn)
        demand_forecast = forecast_service_demand(bookings, services)
        client_classes = classify_all_clients(bookings, clients)
        schedule_plan = optimize_schedule(bookings, specialists)
        allocation = {}
        target_date = request.args.get("target_date", "2026-01-20")
        for service in services:
            allocation[service["id"]] = allocate_clients_to_specialists(bookings, specialists, service_map, service["id"], target_date)
        conn.close()
        return render_template(
            "dashboard_admin.html",
            bookings=bookings, notifications=notifications, stats=stats,
            load_by_specialist=load_by_specialist, demand_forecast=demand_forecast,
            client_classes=client_classes, schedule_plan=schedule_plan,
            allocation=allocation, services=services, target_date=target_date
        )

    @app.route("/booking/new", methods=["GET", "POST"])
    @login_required
    @role_required("client", "admin")
    def new_booking():
        conn = get_connection()
        services = conn.execute("SELECT * FROM services WHERE is_active=1 ORDER BY name").fetchall()
        specialists = conn.execute("""
            SELECT specialists.id, users.name, specialists.specialization
            FROM specialists JOIN users ON users.id = specialists.user_id
            ORDER BY users.name
        """).fetchall()
        all_bookings = conn.execute("SELECT * FROM bookings").fetchall()

        selected_date = request.values.get("booking_date", "")
        selected_specialist_id = request.values.get("specialist_id", "")
        recommendations = []
        allocation_candidates = []
        explanation = None
        selected_service_id = request.values.get("service_id", "")
        if selected_date and selected_specialist_id:
            recommendations, explanation = recommend_slots(all_bookings, selected_date, int(selected_specialist_id))
        if selected_date and selected_service_id:
            service_map = get_specialist_service_map(conn)
            allocation_candidates = allocate_clients_to_specialists(all_bookings, specialists, service_map, int(selected_service_id), selected_date)

        if request.method == "POST":
            service_id = request.form.get("service_id")
            specialist_id = int(request.form.get("specialist_id"))
            booking_date = request.form.get("booking_date")
            booking_time = request.form.get("booking_time")
            client_id = int(request.form.get("client_id") or session["user_id"])

            if not service_id or not booking_date or booking_time not in WORKING_SLOTS:
                flash("Заповніть усі поля коректно")
                conn.close()
                return redirect(url_for("new_booking", booking_date=booking_date, specialist_id=specialist_id))

            link = conn.execute("""
                SELECT 1 FROM specialist_services WHERE specialist_id=? AND service_id=?
            """, (specialist_id, service_id)).fetchone()
            if not link:
                flash("Обраний спеціаліст не виконує цю послугу")
                conn.close()
                return redirect(url_for("new_booking", booking_date=booking_date, specialist_id=specialist_id))

            busy = conn.execute("""
                SELECT id FROM bookings
                WHERE specialist_id=? AND booking_date=? AND booking_time=? AND status!='cancelled'
            """, (specialist_id, booking_date, booking_time)).fetchone()
            if busy:
                flash("Обраний слот уже зайнятий. Оберіть інший час із рекомендацій.")
                conn.close()
                return redirect(url_for("new_booking", booking_date=booking_date, specialist_id=specialist_id))

            conn.execute("""
                INSERT INTO bookings(client_id, service_id, specialist_id, booking_date, booking_time, status)
                VALUES (?, ?, ?, ?, ?, 'new')
            """, (client_id, service_id, specialist_id, booking_date, booking_time))
            booking_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
            sp_user = conn.execute("SELECT user_id FROM specialists WHERE id=?", (specialist_id,)).fetchone()
            conn.commit()
            conn.close()

            subject = "Створено новий запис на послугу"
            message = f"Ваш запис створено та очікує підтвердження. Дата: {booking_date}, час: {booking_time}."
            notify_user(client_id, subject, message, booking_id)
            if sp_user:
                notify_user(sp_user["user_id"], "Новий запис у вашому графіку", f"Новий запис на {booking_date} о {booking_time}. Статус: очікує підтвердження.", booking_id)

            flash("Запис створено. Email-сповіщення сформовано.")
            return redirect(url_for("dashboard"))

        clients = conn.execute("SELECT id, name FROM users WHERE role='client' ORDER BY name").fetchall()
        conn.close()
        return render_template(
            "booking_form.html",
            services=services,
            specialists=specialists,
            clients=clients,
            slots=WORKING_SLOTS,
            recommendations=recommendations,
            explanation=explanation,
            selected_date=selected_date,
            selected_specialist_id=selected_specialist_id,
            selected_service_id=selected_service_id,
            allocation_candidates=allocation_candidates,
        )

    @app.route("/booking/<int:booking_id>/confirm", methods=["POST"])
    @login_required
    @role_required("admin")
    def confirm_booking(booking_id):
        conn = get_connection()
        booking = conn.execute("SELECT * FROM bookings WHERE id=?", (booking_id,)).fetchone()
        if booking:
            conn.execute("UPDATE bookings SET status='confirmed' WHERE id=?", (booking_id,))
            sp_user = conn.execute("SELECT user_id FROM specialists WHERE id=?", (booking["specialist_id"],)).fetchone()
            conn.commit()
            client_id = booking["client_id"]
            booking_date = booking["booking_date"]
            booking_time = booking["booking_time"]
            specialist_user_id = sp_user["user_id"] if sp_user else None
            conn.close()
            notify_user(client_id, "Запис підтверджено", f"Ваш запис підтверджено. Дата: {booking_date}, час: {booking_time}.", booking_id)
            if specialist_user_id:
                notify_user(specialist_user_id, "Запис підтверджено адміністратором", f"Підтверджений запис у графіку: {booking_date} о {booking_time}.", booking_id)
        else:
            conn.close()
        return redirect(url_for("dashboard"))

    @app.route("/booking/<int:booking_id>/complete", methods=["POST"])
    @login_required
    @role_required("specialist", "admin")
    def complete_booking(booking_id):
        conn = get_connection()
        booking = conn.execute("SELECT * FROM bookings WHERE id=?", (booking_id,)).fetchone()
        allowed = False
        if booking and session.get("role") == "admin":
            allowed = True
        elif booking:
            specialist = conn.execute("SELECT id FROM specialists WHERE user_id=?", (session["user_id"],)).fetchone()
            allowed = specialist and specialist["id"] == booking["specialist_id"]
        if allowed:
            conn.execute("UPDATE bookings SET status='completed' WHERE id=?", (booking_id,))
            conn.commit()
            client_id = booking["client_id"]
            conn.close()
            notify_user(client_id, "Послугу виконано", "Дякуємо за візит. Статус вашого запису змінено на 'Виконано'.", booking_id)
        else:
            flash("Недостатньо прав для завершення запису")
            conn.close()
        return redirect(url_for("dashboard"))

    @app.route("/booking/<int:booking_id>/noshow", methods=["POST"])
    @login_required
    @role_required("specialist", "admin")
    def no_show_booking(booking_id):
        conn = get_connection()
        booking = conn.execute("SELECT * FROM bookings WHERE id=?", (booking_id,)).fetchone()
        allowed = False
        if booking and session.get("role") == "admin":
            allowed = True
        elif booking:
            specialist = conn.execute("SELECT id FROM specialists WHERE user_id=?", (session["user_id"],)).fetchone()
            allowed = specialist and specialist["id"] == booking["specialist_id"]
        if allowed:
            conn.execute("UPDATE bookings SET status='no_show' WHERE id=?", (booking_id,))
            conn.commit()
            client_id = booking["client_id"]
            conn.close()
            notify_user(client_id, "Статус запису змінено", "Ваш запис позначено як 'Не з'явився'.", booking_id)
        else:
            flash("Недостатньо прав")
            conn.close()
        return redirect(url_for("dashboard"))

    @app.route("/booking/<int:booking_id>/cancel", methods=["POST"])
    @login_required
    def cancel_booking(booking_id):
        conn = get_connection()
        booking = conn.execute("SELECT * FROM bookings WHERE id=?", (booking_id,)).fetchone()
        can_cancel = booking and (session.get("role") == "admin" or booking["client_id"] == session["user_id"])
        if can_cancel:
            conn.execute("UPDATE bookings SET status='cancelled' WHERE id=?", (booking_id,))
            sp_user = conn.execute("SELECT user_id FROM specialists WHERE id=?", (booking["specialist_id"],)).fetchone()
            conn.commit()
            client_id = booking["client_id"]
            specialist_user_id = sp_user["user_id"] if sp_user else None
            conn.close()
            notify_user(client_id, "Запис скасовано", f"Ваш запис на {booking['booking_date']} о {booking['booking_time']} скасовано.", booking_id)
            if specialist_user_id:
                notify_user(specialist_user_id, "Запис скасовано", f"Запис на {booking['booking_date']} о {booking['booking_time']} скасовано.", booking_id)
        else:
            flash("Недостатньо прав для скасування запису")
            conn.close()
        return redirect(url_for("dashboard"))

    @app.route("/catalog")
    @login_required
    @role_required("admin")
    def catalog():
        conn = get_connection()
        services = conn.execute("SELECT * FROM services ORDER BY name").fetchall()
        specialists = conn.execute("""
            SELECT specialists.id, users.name, users.email, specialists.specialization, specialists.work_start, specialists.work_end
            FROM specialists JOIN users ON users.id = specialists.user_id
            ORDER BY users.name
        """).fetchall()
        links = conn.execute("""
            SELECT sp.id AS specialist_id, GROUP_CONCAT(s.name, ', ') AS services
            FROM specialists sp
            LEFT JOIN specialist_services ss ON ss.specialist_id=sp.id
            LEFT JOIN services s ON s.id=ss.service_id
            GROUP BY sp.id
        """).fetchall()
        conn.close()
        service_map = {row["specialist_id"]: row["services"] for row in links}
        return render_template("catalog.html", services=services, specialists=specialists, service_map=service_map)


    @app.route("/specialists/add", methods=["POST"])
    @login_required
    @role_required("admin")
    def add_specialist():
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip()
        login_value = request.form.get("login", "").strip()
        password = request.form.get("password", "spec123")
        specialization = request.form.get("specialization", "").strip()
        work_start = request.form.get("work_start", "09:00")
        work_end = request.form.get("work_end", "17:00")
        service_ids = request.form.getlist("service_ids")
        if not name or not email or not login_value or not specialization:
            flash("Заповніть усі поля спеціаліста")
            return redirect(url_for("catalog"))
        conn = get_connection()
        try:
            cur = conn.execute(
                "INSERT INTO users(name, email, login, password_hash, role) VALUES (?, ?, ?, ?, 'specialist')",
                (name, email, login_value, generate_password_hash(password))
            )
            user_id = cur.lastrowid
            cur = conn.execute(
                "INSERT INTO specialists(user_id, specialization, work_start, work_end) VALUES (?, ?, ?, ?)",
                (user_id, specialization, work_start, work_end)
            )
            specialist_id = cur.lastrowid
            for sid in service_ids:
                conn.execute("INSERT OR IGNORE INTO specialist_services(specialist_id, service_id) VALUES (?, ?)", (specialist_id, sid))
            conn.commit()
            flash("Спеціаліста додано")
        except Exception as exc:
            conn.rollback()
            flash(f"Не вдалося додати спеціаліста: {exc}")
        finally:
            conn.close()
        return redirect(url_for("catalog"))

    @app.route("/specialists/<int:specialist_id>/update", methods=["POST"])
    @login_required
    @role_required("admin")
    def update_specialist(specialist_id):
        specialization = request.form.get("specialization", "").strip()
        work_start = request.form.get("work_start", "09:00")
        work_end = request.form.get("work_end", "17:00")
        service_ids = request.form.getlist("service_ids")
        conn = get_connection()
        conn.execute("UPDATE specialists SET specialization=?, work_start=?, work_end=? WHERE id=?", (specialization, work_start, work_end, specialist_id))
        conn.execute("DELETE FROM specialist_services WHERE specialist_id=?", (specialist_id,))
        for sid in service_ids:
            conn.execute("INSERT OR IGNORE INTO specialist_services(specialist_id, service_id) VALUES (?, ?)", (specialist_id, sid))
        conn.commit()
        conn.close()
        flash("Дані спеціаліста оновлено")
        return redirect(url_for("catalog"))

    @app.route("/specialists/<int:specialist_id>/delete", methods=["POST"])
    @login_required
    @role_required("admin")
    def delete_specialist(specialist_id):
        conn = get_connection()
        active = conn.execute("SELECT COUNT(*) AS c FROM bookings WHERE specialist_id=? AND status IN ('new','confirmed')", (specialist_id,)).fetchone()["c"]
        if active:
            flash("Не можна видалити спеціаліста з активними записами")
            conn.close()
            return redirect(url_for("catalog"))
        user = conn.execute("SELECT user_id FROM specialists WHERE id=?", (specialist_id,)).fetchone()
        conn.execute("DELETE FROM specialist_services WHERE specialist_id=?", (specialist_id,))
        conn.execute("DELETE FROM specialists WHERE id=?", (specialist_id,))
        if user:
            conn.execute("DELETE FROM users WHERE id=?", (user["user_id"],))
        conn.commit()
        conn.close()
        flash("Спеціаліста видалено")
        return redirect(url_for("catalog"))

    @app.route("/users")
    @login_required
    @role_required("admin")
    def users_admin():
        conn = get_connection()
        users = conn.execute("""
            SELECT u.*, sp.id AS specialist_id, sp.specialization
            FROM users u
            LEFT JOIN specialists sp ON sp.user_id = u.id
            ORDER BY u.role, u.name
        """).fetchall()
        conn.close()
        return render_template("users.html", users=users)

    @app.route("/users/add", methods=["POST"])
    @login_required
    @role_required("admin")
    def add_user():
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        login_value = request.form.get("login", "").strip().lower()
        password = request.form.get("password", "user123")
        role = request.form.get("role", "client")
        if role not in ["client", "admin"]:
            role = "client"
        if not name or not email or not login_value or not password:
            flash("Заповніть усі поля користувача")
            return redirect(url_for("users_admin"))
        conn = get_connection()
        try:
            conn.execute(
                "INSERT INTO users(name, email, login, password_hash, role, status) VALUES (?, ?, ?, ?, ?, 'active')",
                (name, email, login_value, generate_password_hash(password), role)
            )
            conn.commit()
            flash("Користувача створено")
        except Exception as exc:
            conn.rollback()
            flash(f"Не вдалося створити користувача: {exc}")
        finally:
            conn.close()
        return redirect(url_for("users_admin"))

    @app.route("/users/<int:user_id>/update", methods=["POST"])
    @login_required
    @role_required("admin")
    def update_user(user_id):
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        status = request.form.get("status", "active")
        role = request.form.get("role", "client")
        if user_id == session.get("user_id") and status != "active":
            flash("Не можна заблокувати власний акаунт")
            return redirect(url_for("users_admin"))
        if role not in ["client", "admin", "specialist"]:
            role = "client"
        if status not in ["active", "blocked"]:
            status = "active"
        conn = get_connection()
        conn.execute("UPDATE users SET name=?, email=?, role=?, status=? WHERE id=?", (name, email, role, status, user_id))
        new_password = request.form.get("password", "")
        if new_password:
            conn.execute("UPDATE users SET password_hash=? WHERE id=?", (generate_password_hash(new_password), user_id))
        conn.commit()
        conn.close()
        flash("Дані користувача оновлено")
        return redirect(url_for("users_admin"))

    @app.route("/analytics")
    @login_required
    @role_required("admin")
    def analytics():
        conn = get_connection()
        bookings = get_bookings()
        services = conn.execute("SELECT * FROM services WHERE is_active=1 ORDER BY name").fetchall()
        clients = conn.execute("SELECT id, name FROM users WHERE role='client' ORDER BY name").fetchall()
        specialists = get_specialist_rows(conn)
        service_map = get_specialist_service_map(conn)
        target_date = request.args.get("target_date", "2026-01-20")
        demand_forecast = forecast_service_demand(bookings, services)
        client_classes = classify_all_clients(bookings, clients)
        schedule_plan = optimize_schedule(bookings, specialists)
        allocation = {service["id"]: allocate_clients_to_specialists(bookings, specialists, service_map, service["id"], target_date) for service in services}
        conn.close()
        return render_template("analytics.html", demand_forecast=demand_forecast, client_classes=client_classes, schedule_plan=schedule_plan, allocation=allocation, services=services, target_date=target_date)

    @app.route("/notifications")
    @login_required
    def notifications():
        rows = get_notifications(session["user_id"])
        return render_template("notifications.html", notifications=rows)

    return app
