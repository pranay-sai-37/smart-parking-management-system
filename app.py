
from flask import Flask, render_template
from database import get_connection
from flask import (
    Flask, render_template, request,
    redirect, url_for, flash, session
)
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps

app = Flask(__name__)
app.config["SECRET_KEY"] = "smart-parking-dev-key-2026"


def login_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped_view


def admin_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))

        if session.get("role") != "admin":
            flash("Admin access required.", "error")
            return redirect(url_for("customer"))

        return view(*args, **kwargs)
    return wrapped_view



@app.route("/customer/bookings/<int:booking_id>/cancel", methods=["POST"])
def cancel_customer_booking(booking_id):
    if "user_id" not in session or session.get("role") != "customer":
        return redirect(url_for("login"))

    conn = get_connection()
    if conn is None:
        flash("Database connection failed.", "error")
        return redirect(url_for("customer"))

    cursor = conn.cursor()
    try:
        cursor.execute("""
            UPDATE bookings
            SET status = 'cancelled'
            WHERE booking_id = %s
              AND user_id = %s
              AND status = 'booked'
        """, (booking_id, session["user_id"]))

        if cursor.rowcount == 1:
            conn.commit()
            flash("Booking cancelled successfully.", "success")
        else:
            conn.rollback()
            flash(
                "Booking not found or it can no longer be cancelled.",
                "error"
            )
    except Exception:
        conn.rollback()
        app.logger.exception("Booking cancellation failed")
        flash("Could not cancel booking.", "error")
    finally:
        cursor.close()
        conn.close()

    return redirect(url_for("customer"))



@app.route("/customer/vehicles", methods=["GET", "POST"])
def customer_vehicles():
    if "user_id" not in session or session.get("role") != "customer":
        return redirect(url_for("login"))

    if request.method == "POST":
        vehicle_number = request.form.get("vehicle_number", "").strip().upper()
        vehicle_type = request.form.get("vehicle_type", "").strip().lower()

        if not vehicle_number or vehicle_type not in ("car", "bike", "suv", "other"):
            flash("Please enter valid vehicle details.", "error")
            return redirect(url_for("customer_vehicles"))

        conn = get_connection()
        if conn is None:
            flash("Database connection failed.", "error")
            return redirect(url_for("customer_vehicles"))

        cursor = conn.cursor()
        try:
            cursor.execute("""
                INSERT INTO vehicles (user_id, vehicle_number, vehicle_type)
                VALUES (%s, %s, %s)
            """, (session["user_id"], vehicle_number, vehicle_type))
            conn.commit()
            flash("Vehicle added successfully!", "success")
            return redirect(url_for("customer"))

        except Exception as e:
            conn.rollback()
            if getattr(e, "errno", None) == 1062:
                flash("This vehicle number is already registered.", "error")
            else:
                app.logger.exception("Vehicle registration failed")
                flash("Could not add vehicle. Please try again.", "error")
        finally:
            cursor.close()
            conn.close()

    return render_template("vehicles.html")


@app.route("/customer/book", methods=["POST"])
def customer_book():
    if "user_id" not in session or session.get("role") != "customer":
        return redirect(url_for("login"))

    vehicle_id = request.form.get("vehicle_id", type=int)
    slot_id = request.form.get("slot_id", type=int)
    start_time = request.form.get("start_time", "").strip()
    end_time = request.form.get("end_time", "").strip()

    if not all([vehicle_id, slot_id, start_time, end_time]):
        flash("Please complete all booking fields.", "error")
        return redirect(url_for("customer"))

    from datetime import datetime

    try:
        start_dt = datetime.strptime(start_time, "%Y-%m-%dT%H:%M")
        end_dt = datetime.strptime(end_time, "%Y-%m-%dT%H:%M")
    except ValueError:
        flash("Invalid date or time.", "error")
        return redirect(url_for("customer"))

    if end_dt <= start_dt or start_dt < datetime.now():
        flash("Choose a future start time and a later end time.", "error")
        return redirect(url_for("customer"))

    conn = get_connection()
    if conn is None:
        flash("Database connection failed.", "error")
        return redirect(url_for("customer"))

    cursor = conn.cursor(dictionary=True)
    try:
        # Verify that this vehicle belongs to the logged-in customer.
        cursor.execute("""
            SELECT vehicle_id
            FROM vehicles
            WHERE vehicle_id = %s AND user_id = %s
        """, (vehicle_id, session["user_id"]))

        if not cursor.fetchone():
            flash("Please select your own registered vehicle.", "error")
            return redirect(url_for("customer"))

        # Check slot availability and prevent overlapping bookings.
        cursor.execute("""
            SELECT slot_id
            FROM parking_slots
            WHERE slot_id = %s AND status = 'available'
        """, (slot_id,))

        if not cursor.fetchone():
            flash("That slot is not currently available.", "error")
            return redirect(url_for("customer"))

        cursor.execute("""
            SELECT booking_id
            FROM bookings
            WHERE slot_id = %s
              AND status IN ('booked', 'active')
              AND start_time < %s
              AND end_time > %s
        """, (slot_id, end_dt, start_dt))

        if cursor.fetchone():
            flash("That slot is already booked for the selected time.", "error")
            return redirect(url_for("customer"))

        cursor.execute("""
            INSERT INTO bookings
                (user_id, vehicle_id, slot_id, start_time, end_time, status)
            VALUES (%s, %s, %s, %s, %s, 'booked')
        """, (
            session["user_id"], vehicle_id, slot_id,
            start_dt, end_dt
        ))

        conn.commit()
        flash("Parking booking created successfully!", "success")

    except Exception:
        conn.rollback()
        app.logger.exception("Customer booking failed")
        flash("Booking failed. Please try again.", "error")
    finally:
        cursor.close()
        conn.close()
        

    return redirect(url_for("customer"))


# Customer registration
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        phone = request.form.get("phone", "").strip()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not full_name or not email or not phone:
            flash("Please fill in all fields.", "error")
            return redirect(url_for("register"))

        if len(password) < 8:
            flash("Password must be at least 8 characters.", "error")
            return redirect(url_for("register"))

        if password != confirm_password:
            flash("Passwords do not match.", "error")
            return redirect(url_for("register"))

        password_hash = generate_password_hash(password)
        conn = get_connection()

        if conn is None:
            flash("Database connection failed.", "error")
            return redirect(url_for("register"))

        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO users
                    (full_name, email, password_hash, phone, role)
                VALUES (%s, %s, %s, %s, 'customer')
                """,
                (full_name, email, password_hash, phone)
            )
            conn.commit()
            flash("Account created. Please log in.", "success")
            return redirect(url_for("login"))

        except Exception as e:
            conn.rollback()
            if getattr(e, "errno", None) == 1062:
                flash("This email is already registered.", "error")
            else:
                app.logger.exception("Registration failed")
                flash("Registration failed. Please try again.", "error")
        finally:
            cursor.close()
            conn.close()

    return render_template("register.html")


# Login for both admin and customer
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        selected_role = request.form.get("role", "")

        if selected_role not in ("admin", "customer"):
            flash("Please select a valid account type.", "error")
            return redirect(url_for("login"))

        conn = get_connection()
        if conn is None:
            flash("Database connection failed.", "error")
            return redirect(url_for("login"))

        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute(
                """
                SELECT user_id, full_name, email, password_hash, role
                FROM users
                WHERE email = %s
                """,
                (email,)
            )
            user = cursor.fetchone()
        finally:
            cursor.close()
            conn.close()

        if (
            user
            and user["role"] == selected_role
            and check_password_hash(user["password_hash"], password)
        ):
            session.clear()
            session["user_id"] = user["user_id"]
            session["user_name"] = user["full_name"]
            session["role"] = user["role"]

            if user["role"] == "admin":
                return redirect(url_for("dashboard"))
            return redirect(url_for("customer"))

        flash("Invalid email, password, or account type.", "error")

    return render_template("login.html")


# Customer dashboard
@app.route("/customer")
def customer():
    if "user_id" not in session or session.get("role") != "customer":
        return redirect(url_for("login"))

    conn = get_connection()
    if conn is None:
        flash("Database connection failed.", "error")
        return redirect(url_for("logout"))

    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT vehicle_id, vehicle_number, vehicle_type
            FROM vehicles
            WHERE user_id = %s
            ORDER BY vehicle_number
        """, (session["user_id"],))
        vehicles = cursor.fetchall()

        cursor.execute("""
            SELECT ps.slot_id, ps.slot_number, ps.slot_type, pl.lot_name
            FROM parking_slots ps
            JOIN parking_lots pl ON pl.lot_id = ps.lot_id
            WHERE ps.status = 'available'
            ORDER BY pl.lot_name, ps.slot_number
        """)
        slots = cursor.fetchall()

        cursor.execute("""
            SELECT b.booking_id, b.start_time, b.end_time, b.status,
                   v.vehicle_number, ps.slot_number
            FROM bookings b
            JOIN vehicles v ON v.vehicle_id = b.vehicle_id
            JOIN parking_slots ps ON ps.slot_id = b.slot_id
            WHERE b.user_id = %s
            ORDER BY b.created_at DESC
        """, (session["user_id"],))
        bookings = cursor.fetchall()
        
        cursor.execute("""
               SELECT
                p.payment_id,
               b.booking_id,
               pl.lot_name,
              ps.amount,
             p.payment_method,
             p.payment_status,
             p.payment_time
             FROM payments p
              JOIN parking_sessions ps
             ON p.session_id = ps.session_id
            JOIN bookings b
             ON ps.booking_id = b.booking_id
              JOIN parking_slots sl
                ON b.slot_id = sl.slot_id
                JOIN parking_lots pl
              ON sl.lot_id = pl.lot_id
              WHERE b.user_id = %s
            ORDER BY p.payment_time DESC
          """, (session["user_id"],))

        payment_history = cursor.fetchall()
        cursor.execute("""
           SELECT
            COALESCE(
            SUM(CASE
                WHEN p.payment_status = 'paid'
                THEN p.amount
                ELSE 0
            END), 0
           ) AS total_paid,

        COALESCE(
            SUM(CASE
                WHEN p.payment_status = 'paid'
                THEN 1
                ELSE 0
            END), 0
        ) AS paid_count,

        COALESCE(
            SUM(CASE
                WHEN p.payment_status = 'pending'
                THEN 1
                ELSE 0
            END), 0
            ) AS pending_count

          FROM payments p
         JOIN parking_sessions ps
         ON p.session_id = ps.session_id
         JOIN bookings b
         ON ps.booking_id = b.booking_id
          WHERE b.user_id = %s
          """, (session["user_id"],))

        payment_summary = cursor.fetchone()
        
        

        return render_template(
            "customer.html",
            vehicles=vehicles,
            slots=slots,
            bookings=bookings,
            payment_history=payment_history,
            payment_summary=payment_summary
        )
    finally:
        cursor.close()
        conn.close()





@app.route("/customer/pay/<int:booking_id>", methods=["GET", "POST"])
def customer_pay(booking_id):
    from decimal import Decimal
    from math import ceil
    from flask import request, redirect, url_for, flash, render_template, session

    if "user_id" not in session or session.get("role") != "customer":
        return redirect(url_for("login"))

    conn = get_connection()
    if conn is None:
        flash("Database connection failed.", "error")
        return redirect(url_for("customer"))

    cursor = conn.cursor(dictionary=True)

    try:
        # Find this customer's completed booking.
        cursor.execute("""
            SELECT booking_id, user_id, status, start_time, end_time
            FROM bookings
            WHERE booking_id = %s AND user_id = %s
            FOR UPDATE
        """, (booking_id, session["user_id"]))

        booking = cursor.fetchone()

        if booking is None:
            conn.rollback()
            flash("Booking not found.", "error")
            return redirect(url_for("customer"))

        if booking["status"] != "completed":
            conn.rollback()
            flash("Only completed bookings can be paid.", "error")
            return redirect(url_for("customer"))

        start_time = booking["start_time"]
        end_time = booking["end_time"]

        if not start_time or not end_time or end_time <= start_time:
            conn.rollback()
            flash("Invalid booking duration.", "error")
            return redirect(url_for("customer"))

        # Charge ₹5 for every started hour; minimum one hour.
        duration_seconds = (end_time - start_time).total_seconds()
        hours = max(1, ceil(duration_seconds / 3600))
        amount = (Decimal(hours) * Decimal("5.00")).quantize(
            Decimal("0.01")
        )

        # Check whether a session already exists.
        cursor.execute("""
            SELECT session_id, amount
            FROM parking_sessions
            WHERE booking_id = %s
        """, (booking_id,))

        parking_session = cursor.fetchone()

        if parking_session is None:
            cursor.execute("""
                INSERT INTO parking_sessions
                    (booking_id, entry_time, exit_time, amount)
                VALUES (%s, %s, %s, %s)
            """, (booking_id, start_time, end_time, amount))

            session_id = cursor.lastrowid
            session_amount = amount
        else:
            session_id = parking_session["session_id"]
            session_amount = Decimal(str(parking_session["amount"]))

        # Do not create duplicate payments.
        cursor.execute("""
            SELECT payment_id, payment_status
            FROM payments
            WHERE session_id = %s
        """, (session_id,))

        existing_payment = cursor.fetchone()

        if existing_payment:
            conn.commit()
            flash(
                "Payment record already exists. Status: "
                + existing_payment["payment_status"],
                "info"
            )
            return redirect(url_for("customer"))

        if request.method == "POST":
            method = request.form.get("payment_method", "")

            if method not in ("cash", "upi", "card"):
                conn.rollback()
                flash("Please select a valid payment method.", "error")
                return redirect(
                    url_for("customer_pay", booking_id=booking_id)
                )

            # Demo only: this does not transfer real money.
            cursor.execute("""
                INSERT INTO payments
                    (session_id, amount, payment_method, payment_status)
                VALUES (%s, %s, %s, 'pending')
            """, (session_id, session_amount, method))

            conn.commit()
            flash(
                "Parking session and pending payment created successfully.",
                "success"
            )
            return redirect(url_for("customer"))

        # Display the calculated amount on the payment page.
        conn.commit()
        booking["amount"] = session_amount

        return render_template(
            "customer_pay.html",
            booking=booking,
            hours=hours,
            hourly_rate=Decimal("5.00")
        )

    except Exception:
        conn.rollback()
        app.logger.exception("Customer payment error")
        flash("Unable to process the payment request.", "error")
        return redirect(url_for("customer"))

    finally:
        cursor.close()
        conn.close()




# Logout
@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))



@app.route("/payments", methods=["GET", "POST"])
@admin_required
def manage_payments():
    from flask import request, redirect, url_for, flash
    from decimal import Decimal, InvalidOperation

    connection = get_connection()
    if connection is None:
        return "Database connection failed.", 500

    cursor = None
    try:
        cursor = connection.cursor(dictionary=True)

        if request.method == "POST":
            booking_id = request.form.get("booking_id", type=int)
            amount_text = request.form.get("amount", "").strip()
            method = request.form.get("payment_method", "")
            payment_status = request.form.get("payment_status", "")

            if method not in ("cash", "upi", "card"):
                flash("Choose a valid payment method.")
                return redirect(url_for("manage_payments"))

            if payment_status not in ("pending", "paid"):
                flash("Choose a valid payment status.")
                return redirect(url_for("manage_payments"))

            try:
                amount = Decimal(amount_text)
                if not amount.is_finite() or amount < 0 or amount > Decimal("99999999.99"):
                    raise InvalidOperation
                amount = amount.quantize(Decimal("0.01"))
            except (InvalidOperation, ValueError):
                flash("Enter a valid non-negative amount.")
                return redirect(url_for("manage_payments"))

            cursor.execute(
                """SELECT booking_id, start_time, end_time
                   FROM bookings
                   WHERE booking_id = %s AND status = 'completed'
                   FOR UPDATE""",
                (booking_id,)
            )
            booking = cursor.fetchone()

            if booking is None:
                flash("Only completed bookings can be charged.")
                return redirect(url_for("manage_payments"))

            cursor.execute(
                "SELECT session_id FROM parking_sessions WHERE booking_id = %s",
                (booking_id,)
            )
            if cursor.fetchone():
                flash("A parking session already exists for this booking.")
                return redirect(url_for("manage_payments"))

            cursor.execute(
                """INSERT INTO parking_sessions
                   (booking_id, entry_time, exit_time, amount)
                   VALUES (%s, %s, %s, %s)""",
                (booking_id, booking["start_time"],
                 booking["end_time"], amount)
            )
            session_id = cursor.lastrowid

            cursor.execute(
                """INSERT INTO payments
                   (session_id, amount, payment_method, payment_status)
                   VALUES (%s, %s, %s, %s)""",
                (session_id, amount, method, payment_status)
            )

            connection.commit()
            flash("Parking session and payment record created.")
            return redirect(url_for("manage_payments"))

        cursor.execute("""
            SELECT b.booking_id, u.full_name, v.vehicle_number
            FROM bookings b
            JOIN users u ON b.user_id = u.user_id
            JOIN vehicles v ON b.vehicle_id = v.vehicle_id
            LEFT JOIN parking_sessions s ON s.booking_id = b.booking_id
            WHERE b.status = 'completed' AND s.session_id IS NULL
            ORDER BY b.booking_id DESC
        """)
        completed_bookings = cursor.fetchall()

        cursor.execute("""
            SELECT p.payment_id, p.amount, p.payment_method,
                   p.payment_status, p.payment_time,
                   s.session_id, s.booking_id
            FROM payments p
            JOIN parking_sessions s ON p.session_id = s.session_id
            ORDER BY p.payment_id DESC
        """)
        payments = cursor.fetchall()

        cursor.execute("""
            SELECT COALESCE(SUM(amount), 0) AS total_revenue
            FROM payments
            WHERE payment_status = 'paid'
        """)
        total_revenue = cursor.fetchone()["total_revenue"]

        return render_template(
            "payments.html",
            completed_bookings=completed_bookings,
            payments=payments,
            total_revenue=total_revenue
        )

    except Exception as e:
        connection.rollback()
        return f"Payments error: {e}", 500
    finally:
        if cursor is not None:
            cursor.close()
        connection.close()
        


@app.route("/payments/approve/<int:payment_id>", methods=["POST"])
@admin_required
def approve_payment(payment_id):
    from flask import redirect, url_for, flash

    conn = get_connection()

    if conn is None:
        flash("Database connection failed.", "error")
        return redirect(url_for("manage_payments"))

    cursor = conn.cursor(dictionary=True)

    try:
        cursor.execute("""
            SELECT payment_id, payment_status
            FROM payments
            WHERE payment_id = %s
            FOR UPDATE
        """, (payment_id,))

        payment = cursor.fetchone()

        if payment is None:
            conn.rollback()
            flash("Payment record not found.", "error")

        elif payment["payment_status"] != "pending":
            conn.rollback()
            flash("Only pending payments can be approved.", "error")

        else:
            cursor.execute("""
                UPDATE payments
                SET payment_status = 'paid'
                WHERE payment_id = %s
                  AND payment_status = 'pending'
            """, (payment_id,))

            conn.commit()
            flash("Payment approved successfully.", "success")

    except Exception:
        conn.rollback()
        app.logger.exception("Payment approval failed")
        flash("Could not approve payment.", "error")

    finally:
        cursor.close()
        conn.close()

    return redirect(url_for("manage_payments"))
        



@app.route("/reports")
@admin_required
def reports():
    connection = get_connection()
    if connection is None:
        return "Database connection failed.", 500

    cursor = None
    try:
        cursor = connection.cursor(dictionary=True)

        # 1. Overall parking statistics
        cursor.execute("""
            SELECT
                COUNT(*) AS total_slots,
                COALESCE(SUM(status = 'available'), 0) AS available_slots,
                COALESCE(SUM(status = 'occupied'), 0) AS occupied_slots,
                COALESCE(SUM(status = 'maintenance'), 0) AS maintenance_slots
            FROM parking_slots
        """)
        slot_stats = cursor.fetchone()

        # 2. Booking status summary
        cursor.execute("""
            SELECT status, COUNT(*) AS total
            FROM bookings
            GROUP BY status
            ORDER BY status
        """)
        booking_stats = cursor.fetchall()

        # 3. Parking lot-wise slot occupancy
        cursor.execute("""
            SELECT
                pl.lot_name,
                COUNT(ps.slot_id) AS total_slots,
                COALESCE(SUM(ps.status = 'available'), 0) AS available_slots,
                COALESCE(SUM(ps.status = 'occupied'), 0) AS occupied_slots,
                COALESCE(SUM(ps.status = 'maintenance'), 0) AS maintenance_slots
            FROM parking_lots pl
            LEFT JOIN parking_slots ps ON pl.lot_id = ps.lot_id
            GROUP BY pl.lot_id, pl.lot_name
            ORDER BY pl.lot_name
        """)
        lot_stats = cursor.fetchall()

        # 4. Revenue by payment method
        cursor.execute("""
            SELECT
                payment_method,
                COUNT(*) AS transaction_count,
                COALESCE(SUM(amount), 0) AS total_amount
            FROM payments
            WHERE payment_status = 'paid'
            GROUP BY payment_method
            ORDER BY total_amount DESC
        """)
        payment_stats = cursor.fetchall()

        # 5. Customer booking history
        cursor.execute("""
            SELECT
                u.full_name,
                COUNT(b.booking_id) AS total_bookings,
                COALESCE(SUM(
                    CASE WHEN b.status = 'completed' THEN 1 ELSE 0 END
                ), 0) AS completed_bookings
            FROM users u
            LEFT JOIN bookings b ON u.user_id = b.user_id
            WHERE u.role = 'customer'
            GROUP BY u.user_id, u.full_name
            ORDER BY total_bookings DESC, u.full_name
        """)
        customer_stats = cursor.fetchall()

        # 6. Total paid revenue
        cursor.execute("""
            SELECT COALESCE(SUM(amount), 0) AS total_revenue
            FROM payments
            WHERE payment_status = 'paid'
        """)
        total_revenue = cursor.fetchone()["total_revenue"]

        return render_template(
            "reports.html",
            slot_stats=slot_stats,
            booking_stats=booking_stats,
            lot_stats=lot_stats,
            payment_stats=payment_stats,
            customer_stats=customer_stats,
            total_revenue=total_revenue
        )

    except Exception as e:
        return f"Reports error: {e}", 500
    finally:
        if cursor is not None:
            cursor.close()
        connection.close()



@app.route("/bookings/update/<int:booking_id>", methods=["POST"])
@admin_required
def update_booking_status(booking_id):
    from flask import request, redirect, url_for, flash

    new_status = request.form.get("status")
    allowed_statuses = {"active", "completed", "cancelled"}

    if new_status not in allowed_statuses:
        flash("Invalid booking status.")
        return redirect(url_for("manage_bookings"))

    connection = get_connection()
    if connection is None:
        return "Database connection failed.", 500

    cursor = None
    try:
        cursor = connection.cursor(dictionary=True)

        cursor.execute(
            """SELECT booking_id, slot_id, status
               FROM bookings
               WHERE booking_id = %s
               FOR UPDATE""",
            (booking_id,)
        )
        booking = cursor.fetchone()

        if booking is None:
            flash("Booking not found.")
            return redirect(url_for("manage_bookings"))

        if booking["status"] in ("completed", "cancelled"):
            flash("This booking is already finished or cancelled.")
            return redirect(url_for("manage_bookings"))

        slot_id = booking["slot_id"]

        if new_status == "active":
            cursor.execute(
                """SELECT booking_id FROM bookings
                   WHERE slot_id = %s
                   AND status = 'active'
                   AND booking_id != %s
                   LIMIT 1""",
                (slot_id, booking_id)
            )

            if cursor.fetchone():
                connection.rollback()
                flash("Another active booking is using this slot.")
                return redirect(url_for("manage_bookings"))

            cursor.execute(
                """UPDATE bookings
                   SET status = 'active'
                   WHERE booking_id = %s""",
                (booking_id,)
            )
            cursor.execute(
                """UPDATE parking_slots
                   SET status = 'occupied'
                   WHERE slot_id = %s""",
                (slot_id,)
            )

        else:
            cursor.execute(
                """UPDATE bookings
                   SET status = %s
                   WHERE booking_id = %s""",
                (new_status, booking_id)
            )

            cursor.execute(
                """SELECT COUNT(*) AS active_count
                   FROM bookings
                   WHERE slot_id = %s
                   AND status = 'active'
                   AND booking_id != %s""",
                (slot_id, booking_id)
            )
            active_count = cursor.fetchone()["active_count"]

            if active_count == 0:
                cursor.execute(
                    """UPDATE parking_slots
                       SET status = 'available'
                       WHERE slot_id = %s
                       AND status = 'occupied'""",
                    (slot_id,)
                )

        connection.commit()
        flash("Booking status updated successfully!")

    except Exception as e:
        connection.rollback()
        return f"Status update error: {e}", 500

    finally:
        if cursor is not None:
            cursor.close()
        connection.close()

    return redirect(url_for("manage_bookings"))



@app.route("/bookings", methods=["GET", "POST"])
@admin_required
def manage_bookings():
    from flask import request, redirect, url_for, flash
    from datetime import datetime

    connection = get_connection()
    if connection is None:
        return "Database connection failed.", 500

    cursor = None
    try:
        cursor = connection.cursor(dictionary=True)

        if request.method == "POST":
            user_id = request.form.get("user_id", type=int)
            vehicle_id = request.form.get("vehicle_id", type=int)
            slot_id = request.form.get("slot_id", type=int)
            start_time = request.form.get("start_time", "")
            end_time = request.form.get("end_time", "")

            if not all([user_id, vehicle_id, slot_id, start_time, end_time]):
                flash("Please fill in all booking fields.")
                return redirect(url_for("manage_bookings"))

            try:
                start_dt = datetime.fromisoformat(start_time)
                end_dt = datetime.fromisoformat(end_time)
            except ValueError:
                flash("Invalid date or time.")
                return redirect(url_for("manage_bookings"))

            if end_dt <= start_dt:
                flash("End time must be later than start time.")
                return redirect(url_for("manage_bookings"))

            # Confirm the selected vehicle belongs to this customer.
            cursor.execute(
                """SELECT vehicle_id FROM vehicles
                   WHERE vehicle_id = %s AND user_id = %s""",
                (vehicle_id, user_id)
            )
            if cursor.fetchone() is None:
                flash("Please select a vehicle belonging to this customer.")
                return redirect(url_for("manage_bookings"))

            # Check that the slot exists and is available.
            cursor.execute(
                """SELECT slot_id FROM parking_slots
                   WHERE slot_id = %s AND status = 'available'""",
                (slot_id,)
            )
            if cursor.fetchone() is None:
                flash("This slot is unavailable. Choose another slot.")
                return redirect(url_for("manage_bookings"))

            # Prevent overlapping non-cancelled bookings for this slot.
            cursor.execute(
                """SELECT booking_id FROM bookings
                   WHERE slot_id = %s
                   AND status IN ('booked', 'active')
                   AND start_time < %s AND end_time > %s
                   LIMIT 1""",
                (slot_id, end_dt, start_dt)
            )
            if cursor.fetchone():
                flash("This slot is already booked during that time.")
                return redirect(url_for("manage_bookings"))

            cursor.execute(
                """INSERT INTO bookings
                   (user_id, vehicle_id, slot_id, start_time, end_time)
                   VALUES (%s, %s, %s, %s, %s)""",
                (user_id, vehicle_id, slot_id, start_dt, end_dt)
            )
            connection.commit()
            flash("Booking created successfully!")
            return redirect(url_for("manage_bookings"))

        cursor.execute("""
            SELECT user_id, full_name, email
            FROM users
            WHERE role = 'customer'
            ORDER BY full_name
        """)
        customers = cursor.fetchall()

        cursor.execute("""
            SELECT vehicle_id, user_id, vehicle_number, vehicle_type
            FROM vehicles
            ORDER BY vehicle_number
        """)
        vehicles = cursor.fetchall()

        cursor.execute("""
            SELECT slot_id, slot_number, slot_type
            FROM parking_slots
            WHERE status = 'available'
            ORDER BY slot_number
        """)
        slots = cursor.fetchall()

        cursor.execute("""
            SELECT b.booking_id, u.full_name, v.vehicle_number,
                   ps.slot_number, pl.lot_name,
                   b.start_time, b.end_time, b.status
            FROM bookings b
            JOIN users u ON b.user_id = u.user_id
            JOIN vehicles v ON b.vehicle_id = v.vehicle_id
            JOIN parking_slots ps ON b.slot_id = ps.slot_id
            JOIN parking_lots pl ON ps.lot_id = pl.lot_id
            ORDER BY b.booking_id DESC
        """)
        bookings = cursor.fetchall()

        return render_template(
            "bookings.html",
            customers=customers,
            vehicles=vehicles,
            slots=slots,
            bookings=bookings
        )

    except Exception as e:
        connection.rollback()
        return f"Bookings error: {e}", 500
    finally:
        if cursor is not None:
            cursor.close()
        connection.close()


@app.route("/lots", methods=["GET", "POST"])
@admin_required
def parking_lots():
    from flask import request, redirect, url_for, flash

    connection = get_connection()
    if connection is None:
        return "Database connection failed.", 500

    cursor = None
    try:
        cursor = connection.cursor(dictionary=True)

        if request.method == "POST":
            lot_name = request.form.get("lot_name", "").strip()
            location = request.form.get("location", "").strip()
            opening_time = request.form.get("opening_time", "")
            closing_time = request.form.get("closing_time", "")

            if not all([lot_name, location, opening_time, closing_time]):
                flash("Please fill in all fields.")
                return redirect(url_for("parking_lots"))

            if closing_time <= opening_time:
                flash("Closing time must be later than opening time.")
                return redirect(url_for("parking_lots"))

            cursor.execute(
                """INSERT INTO parking_lots
                   (lot_name, location, opening_time, closing_time)
                   VALUES (%s, %s, %s, %s)""",
                (lot_name, location, opening_time, closing_time)
            )
            connection.commit()
            flash("Parking lot added successfully!")
            return redirect(url_for("parking_lots"))

        cursor.execute("""
            SELECT pl.lot_id, pl.lot_name, pl.location,
                   pl.opening_time, pl.closing_time,
                   COUNT(ps.slot_id) AS total_slots
            FROM parking_lots pl
            LEFT JOIN parking_slots ps ON pl.lot_id = ps.lot_id
            GROUP BY pl.lot_id, pl.lot_name, pl.location,
                     pl.opening_time, pl.closing_time
            ORDER BY pl.lot_id DESC
        """)
        lots = cursor.fetchall()

        return render_template("lots.html", lots=lots)

    except Exception as e:
        connection.rollback()
        return f"Parking lots error: {e}", 500
    finally:
        if cursor is not None:
            cursor.close()
        connection.close()


@app.route("/lots/delete/<int:lot_id>", methods=["POST"])
@admin_required
def delete_lot(lot_id):
    from flask import redirect, url_for, flash

    connection = get_connection()
    if connection is None:
        return "Database connection failed.", 500

    cursor = None
    try:
        cursor = connection.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM parking_slots WHERE lot_id = %s",
            (lot_id,)
        )
        slot_count = cursor.fetchone()[0]

        if slot_count > 0:
            flash("Cannot delete this lot because it has parking slots.")
        else:
            cursor.execute(
                "DELETE FROM parking_lots WHERE lot_id = %s",
                (lot_id,)
            )
            connection.commit()
            flash("Parking lot deleted successfully.")

        return redirect(url_for("parking_lots"))

    except Exception as e:
        connection.rollback()
        return f"Delete lot error: {e}", 500
    finally:
        if cursor is not None:
            cursor.close()
        connection.close()


@app.route("/")
@admin_required
def dashboard():
    connection = get_connection()

    if connection is None:
        return "Database connection failed. Check database.py.", 500

    try:
        cursor = connection.cursor(dictionary=True)

        # Parking slot statistics
        cursor.execute("""
            SELECT
                COUNT(*) AS total_slots,
                COALESCE(SUM(status = 'available'), 0) AS available_slots,
                COALESCE(SUM(status = 'occupied'), 0) AS occupied_slots,
                COALESCE(SUM(status = 'maintenance'), 0) AS maintenance_slots
            FROM parking_slots
        """)
        slots = cursor.fetchone()

        # Booking statistics
        cursor.execute("""
            SELECT
                COUNT(*) AS total_bookings,
                COALESCE(SUM(status = 'active'), 0) AS active_bookings,
                COALESCE(SUM(status = 'completed'), 0) AS completed_bookings
            FROM bookings
        """)
        bookings = cursor.fetchone()

        # Revenue from successful payments only
        cursor.execute("""
            SELECT COALESCE(SUM(amount), 0) AS total_revenue
            FROM payments
            WHERE payment_status = 'paid'
        """)
        revenue = cursor.fetchone()["total_revenue"]

        # Latest bookings
        cursor.execute("""
            SELECT
                b.booking_id,
                u.full_name,
                v.vehicle_number,
                ps.slot_number,
                b.start_time,
                b.status
            FROM bookings b
            JOIN users u ON b.user_id = u.user_id
            JOIN vehicles v ON b.vehicle_id = v.vehicle_id
            JOIN parking_slots ps ON b.slot_id = ps.slot_id
            ORDER BY b.booking_id DESC
            LIMIT 5
        """)
        recent_bookings = cursor.fetchall()

        return render_template(
            "dashboard.html",
            slots=slots,
            bookings=bookings,
            revenue=revenue,
            recent_bookings=recent_bookings
        )

    except Exception as e:
        return f"Dashboard error: {e}", 500

    finally:
        cursor.close()
        connection.close()
        

@app.route("/slots", methods=["GET", "POST"])
@admin_required
def parking_slots():
    from flask import request, redirect, url_for, flash

    connection = get_connection()
    if connection is None:
        return "Database connection failed.", 500

    cursor = None
    try:
        cursor = connection.cursor(dictionary=True)

        if request.method == "POST":
            lot_id = request.form.get("lot_id", type=int)
            slot_number = request.form.get("slot_number", "").strip()
            slot_type = request.form.get("slot_type", "")

            if not lot_id or not slot_number or slot_type not in (
                "car", "bike", "suv", "other"
            ):
                flash("Please enter valid slot details.")
                return redirect(url_for("parking_slots"))

            try:
                cursor.execute(
               """INSERT INTO parking_slots
               (lot_id, slot_number, slot_type)
               VALUES (%s, %s, %s)""",
               (lot_id, slot_number, slot_type)
               )
                connection.commit()
                flash("Parking slot added successfully!")

            except Exception as e:
                  connection.rollback()

                  if getattr(e, "errno", None) == 1062:
                       flash("This slot number already exists in this parking lot.")
                  else:
                      raise

            return redirect(url_for("parking_slots"))

        cursor.execute(
            "SELECT lot_id, lot_name FROM parking_lots ORDER BY lot_name"
        )
        lots = cursor.fetchall()
        

        cursor.execute("""
            SELECT ps.slot_id, ps.slot_number, ps.slot_type, ps.status,
                   pl.lot_name
            FROM parking_slots ps
            JOIN parking_lots pl ON ps.lot_id = pl.lot_id
            ORDER BY ps.slot_id DESC
        """)
        slots = cursor.fetchall()

        return render_template("slots.html", lots=lots, slots=slots)

    except Exception as e:
        connection.rollback()
        return f"Parking slots error: {e}", 500
    finally:
        if cursor is not None:
            cursor.close()
        connection.close()


if __name__ == "__main__":
    app.run(debug=True)
