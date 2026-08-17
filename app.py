import os
import io
import base64
import datetime
import ssl as _ssl
from functools import wraps

import pymysql
from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, send_file, send_from_directory, abort
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.lib import colors
from reportlab.lib.colors import HexColor

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'static', 'uploads')
GENERATED_FOLDER = os.path.join(BASE_DIR, 'generated_files')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}

try:
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    os.makedirs(GENERATED_FOLDER, exist_ok=True)
except OSError:
    pass  

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'smartqueue-secret-key-change-in-production')
app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024

HOSPITAL_NAME = 'Capsule-Care Smart Clinic'

DB_HOST = os.environ.get('DB_HOST', 'localhost')
DB_USER = os.environ.get('DB_USER', 'root')
DB_PASSWORD = os.environ.get('DB_PASSWORD', 'parthiban','Root')
DB_NAME = os.environ.get('DB_NAME', 'smart_queue_db')
DB_PORT = int(os.environ.get('DB_PORT', '3306'))
DB_SSL = os.environ.get('DB_SSL', '').lower() in ('1', 'true', 'yes')


def _ssl_ctx():
    ctx = _ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = _ssl.CERT_NONE
    return ctx


def _connect(database=None):
    kwargs = dict(host=DB_HOST, user=DB_USER, password=DB_PASSWORD, port=DB_PORT,
                  charset='utf8mb4', cursorclass=pymysql.cursors.DictCursor,
                  autocommit=True, connect_timeout=15)
    if DB_SSL:
        kwargs['ssl'] = _ssl_ctx()
    if database:
        kwargs['database'] = database
    return pymysql.connect(**kwargs)


TIME_SLOTS = [
    '09:00 AM', '09:30 AM', '10:00 AM', '10:30 AM', '11:00 AM', '11:30 AM',
    '12:00 PM', '12:30 PM', '01:00 PM', '01:30 PM', '02:00 PM', '02:30 PM',
    '03:00 PM', '03:30 PM', '04:00 PM', '04:30 PM', '05:00 PM', '05:30 PM',
    '06:00 PM', '06:30 PM', '07:00 PM', '07:30 PM',
]

TABLE_DDL = [
    """CREATE TABLE IF NOT EXISTS patients (
        id INT AUTO_INCREMENT PRIMARY KEY,
        name VARCHAR(100) NOT NULL,
        age INT NOT NULL,
        email VARCHAR(120) NOT NULL UNIQUE,
        mobile VARCHAR(15) NOT NULL,
        password VARCHAR(255) NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    ) ENGINE=InnoDB""",
    """CREATE TABLE IF NOT EXISTS doctors (
        id INT AUTO_INCREMENT PRIMARY KEY,
        doctor_name VARCHAR(100) NOT NULL,
        username VARCHAR(100) NOT NULL UNIQUE,
        password VARCHAR(255) NOT NULL,
        email VARCHAR(120) DEFAULT NULL,
        mobile VARCHAR(15) DEFAULT NULL,
        specialization VARCHAR(100) DEFAULT NULL,
        fees DECIMAL(10,2) DEFAULT NULL,
        gpay_qr LONGTEXT DEFAULT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    ) ENGINE=InnoDB""",
    """CREATE TABLE IF NOT EXISTS appointments (
        id INT AUTO_INCREMENT PRIMARY KEY,
        patient_id INT DEFAULT NULL,
        patient_name VARCHAR(100) NOT NULL,
        patient_age INT NOT NULL,
        appointment_date DATE NOT NULL,
        appointment_time VARCHAR(20) NOT NULL,
        doctor_id INT NOT NULL,
        doctor_name VARCHAR(100) NOT NULL,
        payment_mode VARCHAR(10) NOT NULL DEFAULT 'cash',
        payment_status VARCHAR(10) NOT NULL DEFAULT 'cash',
        paid_at DATETIME DEFAULT NULL,
        amount DECIMAL(10,2) NOT NULL DEFAULT 0.00,
        token_number INT NOT NULL,
        status VARCHAR(20) NOT NULL DEFAULT 'waiting',
        postponed_date DATE DEFAULT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_doctor_date (doctor_id, appointment_date),
        INDEX idx_patient (patient_id),
        CONSTRAINT fk_appt_patient FOREIGN KEY (patient_id) REFERENCES patients(id) ON DELETE SET NULL,
        CONSTRAINT fk_appt_doctor FOREIGN KEY (doctor_id) REFERENCES doctors(id) ON DELETE CASCADE
    ) ENGINE=InnoDB""",
    """CREATE TABLE IF NOT EXISTS reminders (
        id INT AUTO_INCREMENT PRIMARY KEY,
        patient_id INT NOT NULL,
        appointment_id INT DEFAULT NULL,
        doctor_name VARCHAR(100) DEFAULT NULL,
        medicine_name VARCHAR(255) NOT NULL,
        dosage VARCHAR(100) NOT NULL,
        frequency VARCHAR(50) NOT NULL,
        food_timing VARCHAR(20) NOT NULL DEFAULT 'after food',
        start_date DATE NOT NULL,
        duration_days INT NOT NULL DEFAULT 1,
        is_active TINYINT(1) NOT NULL DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_reminder_patient (patient_id),
        CONSTRAINT fk_rem_patient FOREIGN KEY (patient_id) REFERENCES patients(id) ON DELETE CASCADE,
        CONSTRAINT fk_rem_appt FOREIGN KEY (appointment_id) REFERENCES appointments(id) ON DELETE CASCADE
    ) ENGINE=InnoDB""",
    """CREATE TABLE IF NOT EXISTS queue_sessions (
        id INT AUTO_INCREMENT PRIMARY KEY,
        doctor_id INT NOT NULL,
        queue_date DATE NOT NULL,
        started_at DATETIME DEFAULT NULL,
        ended_at DATETIME DEFAULT NULL,
        UNIQUE KEY uq_doctor_date (doctor_id, queue_date)
    ) ENGINE=InnoDB""",
]


def init_database():
    try:
        conn = _connect(database=DB_NAME)
    except pymysql.err.OperationalError:
        conn = _connect()
        cur = conn.cursor()
        cur.execute("CREATE DATABASE IF NOT EXISTS `%s` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                    % DB_NAME.replace('`', ''))
        cur.close()
        conn.close()
        conn = _connect(database=DB_NAME)
    cur = conn.cursor()
    for ddl in TABLE_DDL:
        try:
            cur.execute(ddl)
        except pymysql.err.OperationalError:
            pass
    try:
        cur.execute("ALTER TABLE doctors MODIFY gpay_qr LONGTEXT")
    except pymysql.err.OperationalError:
        pass
    for alt in (
        "ALTER TABLE appointments ADD COLUMN payment_status VARCHAR(10) NOT NULL DEFAULT 'cash'",
        "ALTER TABLE appointments ADD COLUMN paid_at DATETIME DEFAULT NULL",
        "ALTER TABLE reminders ADD COLUMN is_active TINYINT(1) NOT NULL DEFAULT 1",
        "ALTER TABLE doctors ADD COLUMN mobile VARCHAR(15) DEFAULT NULL",
        "ALTER TABLE doctors ADD COLUMN email VARCHAR(120) DEFAULT NULL",
        "ALTER TABLE doctors ADD COLUMN specialization VARCHAR(100) DEFAULT NULL",
        "ALTER TABLE doctors ADD COLUMN fees DECIMAL(10,2) DEFAULT NULL",
        "ALTER TABLE appointments ADD COLUMN postponed_date DATE DEFAULT NULL",
    ):
        try:
            cur.execute(alt)
        except pymysql.err.OperationalError:
            pass
    cur.close()
    conn.close()


_schema_ready = False


def get_db():
    global _schema_ready
    if not _schema_ready:
        init_database()
        _schema_ready = True
    return _connect(database=DB_NAME)


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def get_queue_session(conn, doctor_id, date):
    cur = conn.cursor()
    cur.execute("SELECT * FROM queue_sessions WHERE doctor_id=%s AND queue_date=%s", (doctor_id, date))
    sess = cur.fetchone()
    if not sess:
        try:
            cur.execute("INSERT INTO queue_sessions(doctor_id, queue_date) VALUES(%s,%s)", (doctor_id, date))
        except pymysql.err.IntegrityError:
            pass
        cur.execute("SELECT * FROM queue_sessions WHERE doctor_id=%s AND queue_date=%s", (doctor_id, date))
        sess = cur.fetchone()
    cur.close()
    return sess


def renumber_tokens(conn, doctor_id, date):
    cur = conn.cursor()
    cur.execute("SELECT id, token_number, status FROM appointments "
                "WHERE doctor_id=%s AND appointment_date=%s ORDER BY token_number", (doctor_id, date))
    rows = cur.fetchall()
    done_numbers = set()
    ids = []
    for r in rows:
        if r['status'] in ('done', 'absent', 'cancelled', 'postponed'):
            done_numbers.add(r['token_number'])
        else:
            ids.append(r['id'])
    if ids:
        marks = ','.join(['%s'] * len(ids))
        cur.execute(
            "SELECT id FROM appointments WHERE id IN (%s) "
            "ORDER BY CASE WHEN payment_status IN ('paid','cash') THEN 0 ELSE 1 END, "
            "COALESCE(paid_at, created_at), created_at, id" % marks, ids)
        seq = [r['id'] for r in cur.fetchall()]
        n = 1
        for aid in seq:
            while n in done_numbers:
                n += 1
            cur.execute("UPDATE appointments SET token_number=%s WHERE id=%s", (n, aid))
            n += 1
    cur.close()


PAY_LABELS = {'paid': ('Paid', 'paid'), 'pending': ('Not Paid', 'pending'), 'cash': ('Cash', 'cash')}


@app.context_processor
def inject_globals():
    return dict(HOSPITAL_NAME=HOSPITAL_NAME)


@app.template_filter('fmtdate')
def fmtdate(value):
    try:
        if isinstance(value, datetime.date):
            return value.strftime('%d %b %Y')
        return datetime.datetime.strptime(str(value)[:10], '%Y-%m-%d').strftime('%d %b %Y')
    except Exception:
        return str(value)


@app.route('/static/<path:filename>')
def static_files(filename):
    return send_from_directory('static', filename)


@app.route('/health')
def health():
    return 'OK'


@app.route('/setup')
def setup_db():
    try:
        init_database()
        conn = _connect(database=DB_NAME)
        cur = conn.cursor()
        cur.execute("SELECT VERSION() AS v")
        version = cur.fetchone()['v']
        cur.close()
        conn.close()
        return ('<html><head><meta charset="utf-8"></head><body style="font-family:sans-serif;padding:40px">'
                '<h2 style="color:#0d9488">Database setup complete!</h2>'
                '<p>Connected to MySQL <strong>%s</strong>, database <strong>%s</strong>. '
                'All tables are ready.</p>'
                '<p><a href="/">Go to home page</a></p></body></html>' % (version, DB_NAME))
    except Exception as exc:
        return ('<html><head><meta charset="utf-8"></head><body style="font-family:sans-serif;padding:40px">'
                '<h2 style="color:#e11d48">Database setup failed</h2>'
                '<p>Error: <code>%s</code></p>'
                '<p>Check your Vercel Environment Variables (DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME) '
                'and make sure the MySQL host allows connections.</p></body></html>' % exc), 500


def patient_login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if 'patient_id' not in session:
            flash('Please login as a patient first.', 'warning')
            return redirect(url_for('patient_login'))
        return f(*args, **kwargs)
    return wrapper


def doctor_login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if 'doctor_id' not in session:
            flash('Please login as a doctor first.', 'warning')
            return redirect(url_for('doctor_login'))
        return f(*args, **kwargs)
    return wrapper


# ---------------------------------------------------------------- PDF helpers (in-memory)

def build_token_pdf(appt):
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    w, h = A4
    margin = 24 * mm
    card_top = h - 26 * mm
    card_bottom = card_top - 180 * mm

    c.setStrokeColor(HexColor('#0d9488'))
    c.setLineWidth(2)
    c.roundRect(margin, card_bottom, w - 2 * margin, card_top - card_bottom, 8 * mm, stroke=1, fill=0)

    c.setFillColor(HexColor('#0f766e'))
    c.setFont('Helvetica-Bold', 20)
    c.drawCentredString(w / 2, card_top - 14 * mm, HOSPITAL_NAME)
    c.setFont('Helvetica', 10)
    c.setFillColor(HexColor('#64748b'))
    c.drawCentredString(w / 2, card_top - 22 * mm, 'Smart Queue  -  Appointment Token')

    c.setStrokeColor(HexColor('#ccfbf1'))
    c.setLineWidth(1)
    c.line(margin + 10 * mm, card_top - 28 * mm, w - margin - 10 * mm, card_top - 28 * mm)

    center_y = card_top - 56 * mm
    c.setFillColor(HexColor('#0d9488'))
    c.circle(w / 2, center_y, 15 * mm, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont('Helvetica-Bold', 24)
    c.drawCentredString(w / 2, center_y - 3.5 * mm, str(appt['token_number']))
    c.setFont('Helvetica', 7)
    c.setFillColor(HexColor('#99f6e4'))
    c.drawCentredString(w / 2, center_y - 12 * mm, 'TOKEN NO')

    def row(yy, lk, lv, rk, rv):
        lx = margin + 16 * mm
        rx = w / 2 + 4 * mm
        c.setFont('Helvetica', 9)
        c.setFillColor(HexColor('#64748b'))
        c.drawString(lx, yy, lk)
        c.drawString(rx, yy, rk)
        c.setFont('Helvetica-Bold', 11)
        c.setFillColor(HexColor('#134e4a'))
        c.drawString(lx + 32 * mm, yy, str(lv))
        c.drawString(rx + 32 * mm, yy, str(rv))

    y = card_top - 84 * mm
    row(y, 'Patient', appt['patient_name'], 'Age', appt['patient_age']); y -= 11 * mm
    row(y, 'Doctor', 'Dr. ' + appt['doctor_name'], 'Date', appt['appointment_date'].strftime('%d %b %Y')); y -= 11 * mm
    row(y, 'Time', appt['appointment_time'], 'Payment', appt['payment_mode'].upper()); y -= 11 * mm
    row(y, 'Amount', 'Rs %.2f' % float(appt['amount']), 'Status',
        'Done' if appt['status'] == 'done' else 'Waiting')

    c.setFont('Helvetica', 9)
    c.setFillColor(HexColor('#64748b'))
    c.drawCentredString(w / 2, card_bottom + 16 * mm, 'Please arrive 10 minutes early and wait for your turn.')
    c.setFont('Helvetica', 8)
    c.drawCentredString(w / 2, card_bottom + 8 * mm, 'Thank you for choosing %s' % HOSPITAL_NAME)
    c.save()
    buf.seek(0)
    return buf


def build_report_pdf(doctor_name, rows, report_date, done_count, total_count):
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    w, h = A4
    margin = 22 * mm

    c.setFillColor(HexColor('#0f766e'))
    c.rect(0, h - 32 * mm, w, 32 * mm, stroke=0, fill=1)
    c.setFillColor(colors.white)
    c.setFont('Helvetica-Bold', 17)
    c.drawCentredString(w / 2, h - 13 * mm, HOSPITAL_NAME)
    c.setFont('Helvetica', 11)
    c.drawCentredString(w / 2, h - 21 * mm, 'Doctor Daily Token Report')
    c.setFont('Helvetica', 9)
    c.drawCentredString(w / 2, h - 28 * mm, 'Generated: %s' % datetime.datetime.now().strftime('%d %b %Y  %I:%M %p'))

    y = h - 46 * mm
    c.setFillColor(HexColor('#134e4a'))
    c.setFont('Helvetica-Bold', 12)
    c.drawString(margin, y, 'Doctor: Dr. %s' % doctor_name)
    c.setFont('Helvetica', 11)
    c.setFillColor(HexColor('#475569'))
    c.drawRightString(w - margin, y, 'Report Date: %s' % report_date.strftime('%d %b %Y'))

    y -= 14 * mm
    c.setFillColor(HexColor('#f0fdfa'))
    c.rect(margin, y - 16 * mm, w - 2 * margin, 16 * mm, stroke=0, fill=1)
    c.setFillColor(HexColor('#134e4a'))
    c.setFont('Helvetica-Bold', 11)
    c.drawString(margin + 8 * mm, y - 11 * mm, 'Tokens Done Today: %d' % done_count)
    c.drawString(w / 2 + 8 * mm, y - 11 * mm, 'Total Booked Today: %d' % total_count)

    cols = [('S.No', 9 * mm), ('Token', 22 * mm), ('Patient', 48 * mm),
            ('Time', 30 * mm), ('Payment', 30 * mm), ('Amount', 24 * mm)]

    def draw_header(yy):
        c.setFillColor(HexColor('#0d9488'))
        c.rect(margin, yy - 8 * mm, w - 2 * margin, 8 * mm, stroke=0, fill=1)
        c.setFillColor(colors.white)
        c.setFont('Helvetica-Bold', 9)
        x = margin + 6 * mm
        for label, cw in cols:
            c.drawString(x, yy - 5.5 * mm, label)
            x += cw

    y -= 30 * mm
    draw_header(y)
    y -= 8 * mm
    c.setFont('Helvetica', 9)

    for i, r in enumerate(rows, start=1):
        if y < 30 * mm:
            c.showPage()
            y = h - 20 * mm
            draw_header(y)
            y -= 8 * mm
            c.setFont('Helvetica', 9)
        if i % 2 == 0:
            c.setFillColor(HexColor('#f8fdfc'))
            c.rect(margin, y - 7 * mm, w - 2 * margin, 7 * mm, stroke=0, fill=1)
        c.setFillColor(HexColor('#1e293b'))
        x = margin + 6 * mm
        c.drawString(x, y - 5 * mm, str(i)); x += 9 * mm
        c.drawString(x, y - 5 * mm, str(r['token_number'])); x += 22 * mm
        c.drawString(x, y - 5 * mm, r['patient_name'][:24]); x += 48 * mm
        c.drawString(x, y - 5 * mm, str(r['appointment_time'])); x += 30 * mm
        c.drawString(x, y - 5 * mm, r['payment_mode'].upper()); x += 30 * mm
        c.drawString(x, y - 5 * mm, 'Rs %.2f' % float(r['amount']))
        y -= 7 * mm

    y -= 14 * mm
    c.setFillColor(HexColor('#134e4a'))
    c.setFont('Helvetica-Bold', 11)
    c.drawString(margin, y, 'Total Tokens Completed Today: %d' % done_count)
    c.setFont('Helvetica', 8)
    c.setFillColor(HexColor('#64748b'))
    c.drawCentredString(w / 2, 18 * mm, '%s  -  Smart Queue & Medicine Reminder System' % HOSPITAL_NAME)
    c.save()
    buf.seek(0)
    return buf


# ---------------------------------------------------------------- Home routes

@app.route('/')
def index():
    return render_template('index.html')


TEAM_MEMBERS = [
    ('parthiban', 'parthiban.zy033@gmail.com'),
    ('antony emmanuel james', 'antonyemmanuel148@gmail.com'),
    ('senthamizhselvi', 'ssenthamizhselvi04@gmail.com'),
     ('sathiya', 'sathiyadharshini2819@gmail.com'),
    ('jafren begum', 'jafren6305@gmail.com'),
    ('prasanth', 'prasanthnatrajanprasa83@gmail.com'),
]


@app.route('/team')
def team():
    return render_template('team.html', team=TEAM_MEMBERS)


# ---------------------------------------------------------------- Patient auth

@app.route('/patient/register', methods=['GET', 'POST'])
def patient_register():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        age = request.form.get('age', '').strip()
        email = request.form.get('email', '').strip().lower()
        mobile = request.form.get('mobile', '').strip()
        password = request.form.get('password', '')
        confirm = request.form.get('confirm', '')

        if not all([name, age, email, mobile, password, confirm]):
            flash('Please fill in all the fields.', 'error')
        elif not age.isdigit() or not (1 <= int(age) <= 120):
            flash('Please enter a valid age.', 'error')
        elif password != confirm:
            flash('Passwords do not match.', 'error')
        else:
            conn = get_db()
            cur = conn.cursor()
            cur.execute("SELECT id FROM patients WHERE email=%s", (email,))
            if cur.fetchone():
                flash('An account with this email already exists. Please login.', 'warning')
            else:
                cur.execute(
                    "INSERT INTO patients(name, age, email, mobile, password) VALUES(%s,%s,%s,%s,%s)",
                    (name, int(age), email, mobile, generate_password_hash(password))
                )
                flash('Account created! Please login to continue.', 'success')
                cur.close()
                conn.close()
                return redirect(url_for('patient_login'))
            cur.close()
            conn.close()
    return render_template('patient_register.html')


@app.route('/patient/login', methods=['GET', 'POST'])
def patient_login():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM patients WHERE email=%s", (email,))
        patient = cur.fetchone()
        cur.close()
        conn.close()
        if patient and check_password_hash(patient['password'], password):
            session.clear()
            session['patient_id'] = patient['id']
            session['patient_name'] = patient['name']
            flash('Welcome back, %s!' % patient['name'], 'success')
            return redirect(url_for('patient_dashboard'))
        flash('Invalid email or password.', 'error')
    return render_template('patient_login.html')


@app.route('/patient/logout')
def patient_logout():
    session.clear()
    flash('You have been logged out.', 'info')
    return redirect(url_for('index'))


# ---------------------------------------------------------------- Patient app

@app.route('/patient/dashboard')
@patient_login_required
def patient_dashboard():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM patients WHERE id=%s", (session['patient_id'],))
    patient = cur.fetchone()
    cur.execute("SELECT * FROM appointments WHERE patient_id=%s ORDER BY id DESC LIMIT 10",
                (session['patient_id'],))
    appointments = cur.fetchall()
    postponed = [a for a in appointments if a['status'] == 'postponed' and a.get('postponed_date')]
    doctor_emails = {}
    if postponed:
        pids = list({a['doctor_id'] for a in postponed})
        marks = ','.join(['%s'] * len(pids))
        cur.execute("SELECT id, email FROM doctors WHERE id IN (%s)" % marks, pids)
        for row in cur.fetchall():
            doctor_emails[row['id']] = row['email']
    cur.execute("SELECT COUNT(*) AS c FROM appointments WHERE patient_id=%s AND status='waiting'",
                (session['patient_id'],))
    active = cur.fetchone()['c']
    cur.execute("SELECT COUNT(*) AS c FROM appointments WHERE patient_id=%s AND status='done'",
                (session['patient_id'],))
    completed = cur.fetchone()['c']
    cur.execute("SELECT COUNT(*) AS c FROM reminders WHERE patient_id=%s", (session['patient_id'],))
    reminder_count = cur.fetchone()['c']

    today = datetime.date.today()
    cur.execute("SELECT medicine_name, dosage, frequency, food_timing, start_date, duration_days, is_active "
                "FROM reminders WHERE patient_id=%s", (session['patient_id'],))
    rem_rows = cur.fetchall()
    active_reminders = []
    for r in rem_rows:
        end = r['start_date'] + datetime.timedelta(days=r['duration_days'] - 1)
        if r['is_active'] and r['start_date'] <= today <= end:
            active_reminders.append(r)
    cur.close()
    conn.close()
    latest_id = appointments[0]['id'] if appointments else None
    return render_template('patient_dashboard.html', patient=patient, appointments=appointments,
                           active=active, completed=completed, reminder_count=reminder_count,
                           active_reminders=active_reminders, postponed=postponed,
                           doctor_emails=doctor_emails, latest_id=latest_id,
                           today_str=datetime.date.today().isoformat())


@app.route('/patient/profile', methods=['GET', 'POST'])
@patient_login_required
def patient_profile():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM patients WHERE id=%s", (session['patient_id'],))
    patient = cur.fetchone()
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        age = request.form.get('age', '').strip()
        email = request.form.get('email', '').strip().lower()
        mobile = request.form.get('mobile', '').strip()
        password = request.form.get('password', '')
        confirm = request.form.get('confirm', '')
        if not all([name, age, email, mobile]):
            flash('Name, age, email and mobile are required.', 'error')
        elif not age.isdigit() or not (1 <= int(age) <= 120):
            flash('Please enter a valid age.', 'error')
        elif password and password != confirm:
            flash('New passwords do not match.', 'error')
        else:
            cur.execute("SELECT id FROM patients WHERE email=%s AND id!=%s",
                        (email, session['patient_id']))
            if cur.fetchone():
                flash('That email is already used by another account.', 'error')
            else:
                if password:
                    cur.execute("UPDATE patients SET name=%s, age=%s, email=%s, mobile=%s, password=%s "
                                "WHERE id=%s",
                                (name, int(age), email, mobile,
                                 generate_password_hash(password), session['patient_id']))
                    flash('Profile updated (password changed)!', 'success')
                else:
                    cur.execute("UPDATE patients SET name=%s, age=%s, email=%s, mobile=%s WHERE id=%s",
                                (name, int(age), email, mobile, session['patient_id']))
                    flash('Profile updated!', 'success')
                cur.close()
                conn.close()
                return redirect(url_for('patient_profile'))
    cur.close()
    conn.close()
    return render_template('patient_profile.html', patient=patient)


@app.route('/patient/forgot-password', methods=['GET', 'POST'])
def patient_forgot_password():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        mobile = request.form.get('mobile', '').strip()
        password = request.form.get('password', '')
        confirm = request.form.get('confirm', '')
        if not all([email, mobile, password, confirm]):
            flash('Please fill in all the fields.', 'error')
        elif password != confirm:
            flash('Passwords do not match.', 'error')
        else:
            conn = get_db()
            cur = conn.cursor()
            cur.execute("SELECT id FROM patients WHERE email=%s AND mobile=%s", (email, mobile))
            pat = cur.fetchone()
            if not pat:
                flash('No account found with that email & mobile. Please register.', 'error')
            else:
                cur.execute("UPDATE patients SET password=%s WHERE id=%s",
                            (generate_password_hash(password), pat['id']))
                flash('Password reset successful! Please login.', 'success')
                cur.close()
                conn.close()
                return redirect(url_for('patient_login'))
            cur.close()
            conn.close()
    return render_template('patient_forgot.html')


@app.route('/book', methods=['GET', 'POST'])
@patient_login_required
def book():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM patients WHERE id=%s", (session['patient_id'],))
    patient = cur.fetchone()
    cur.execute("SELECT id, doctor_name, fees, gpay_qr FROM doctors ORDER BY doctor_name")
    doctors = cur.fetchall()
    doctor_options = [{'id': d['id'], 'doctor_name': d['doctor_name'],
                       'fees': float(d['fees']) if d['fees'] else 0,
                       'qr_uri': d['gpay_qr']} for d in doctors]

    if request.method == 'POST':
        name = request.form.get('patient_name', '').strip()
        age = request.form.get('patient_age', '').strip()
        appt_date = request.form.get('appointment_date', '').strip()
        appt_time = request.form.get('appointment_time', '').strip()
        doctor_id = request.form.get('doctor_id', '').strip()
        payment_mode = request.form.get('payment_mode', 'cash')
        amount = request.form.get('amount', '0').strip()

        if not all([name, age, appt_date, appt_time, doctor_id]):
            flash('Please fill in all required fields.', 'error')
        elif not age.isdigit() or not (1 <= int(age) <= 120):
            flash('Please enter a valid age.', 'error')
        elif not doctor_id.isdigit():
            flash('Please choose a doctor.', 'error')
        else:
            try:
                book_date = datetime.datetime.strptime(appt_date, '%Y-%m-%d').date()
            except ValueError:
                flash('Invalid date.', 'error')
                book_date = None
            if book_date is not None and book_date < datetime.date.today():
                flash('You cannot book an appointment for a past date.', 'error')
                book_date = None
            if book_date is not None:
                cur.execute("SELECT doctor_name, fees FROM doctors WHERE id=%s", (int(doctor_id),))
                doc = cur.fetchone()
                if not doc:
                    flash('Selected doctor not found.', 'error')
                else:
                    cur.execute(
                        "SELECT id FROM appointments WHERE patient_id=%s AND doctor_id=%s "
                        "AND appointment_date=%s AND appointment_time=%s AND status!='done'",
                        (session['patient_id'], int(doctor_id), appt_date, appt_time))
                    if cur.fetchone():
                        flash('Conflict: You already have an appointment with this doctor at this time slot.',
                              'error')
                    else:
                        cur.execute(
                            "SELECT COALESCE(MAX(token_number),0) AS max_token FROM appointments "
                            "WHERE doctor_id=%s AND appointment_date=%s", (int(doctor_id), appt_date))
                        max_token = cur.fetchone()['max_token']
                        token = int(max_token) + 1
                        try:
                            fees_val = float(doc['fees']) if doc.get('fees') else None
                            amount_val = fees_val if fees_val is not None else (float(amount) if amount else 0.0)
                        except ValueError:
                            amount_val = 0.0
                        pay_status = 'cash' if payment_mode == 'cash' else 'pending'
                        paid_at = datetime.datetime.now() if payment_mode == 'cash' else None
                        cur.execute(
                            "INSERT INTO appointments(patient_id, patient_name, patient_age, appointment_date, "
                            "appointment_time, doctor_id, doctor_name, payment_mode, payment_status, paid_at, "
                            "amount, token_number, status) "
                            "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'waiting')",
                            (session['patient_id'], name, int(age), appt_date, appt_time,
                             int(doctor_id), doc['doctor_name'], payment_mode, pay_status, paid_at,
                             amount_val, token))
                        appointment_id = cur.lastrowid
                        cur.close()
                        conn.close()
                        if pay_status == 'pending':
                            flash('Appointment booked! Token #%d. Please scan & pay using the doctor\'s QR, '
                                  'then click "I Have Paid" to activate priority.' % token, 'warning')
                        else:
                            flash('Appointment booked! Your token number is %d.' % token, 'success')
                        return redirect(url_for('view_token', appointment_id=appointment_id))

    today = datetime.date.today()
    cur.close()
    conn.close()
    return render_template('book_appointment.html', patient=patient, doctors=doctors,
                           doctor_options=doctor_options, time_slots=TIME_SLOTS, today=today.isoformat())


@app.route('/view-token/<int:appointment_id>')
@patient_login_required
def view_token(appointment_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM appointments WHERE id=%s AND patient_id=%s",
                (appointment_id, session['patient_id']))
    appt = cur.fetchone()
    doctor_email = None
    if appt:
        cur.execute("SELECT email FROM doctors WHERE id=%s", (appt['doctor_id'],))
        doc_row = cur.fetchone()
        if doc_row:
            doctor_email = doc_row['email']
    cur.close()
    conn.close()
    if not appt:
        abort(404)
    return render_template('view_token.html', appt=appt, today_str=datetime.date.today().isoformat(),
                           doctor_email=doctor_email)


@app.route('/download-token/<int:appointment_id>')
@patient_login_required
def download_token(appointment_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM appointments WHERE id=%s AND patient_id=%s",
                (appointment_id, session['patient_id']))
    appt = cur.fetchone()
    cur.close()
    conn.close()
    if not appt:
        abort(404)
    filename = secure_filename(
        'token_%s_%s_%s.pdf' % (appt['token_number'], appt['patient_name'], appt['appointment_date']))
    return send_file(build_token_pdf(appt), as_attachment=True, download_name=filename,
                     mimetype='application/pdf')


@app.route('/confirm-payment/<int:appointment_id>', methods=['POST'])
@patient_login_required
def confirm_payment(appointment_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT doctor_id, appointment_date, payment_status, status FROM appointments "
                "WHERE id=%s AND patient_id=%s", (appointment_id, session['patient_id']))
    appt = cur.fetchone()
    if not appt:
        cur.close()
        conn.close()
        abort(404)
    if appt['status'] != 'waiting':
        flash('This appointment is not active anymore, so payment cannot be confirmed.', 'error')
    elif appt['payment_status'] == 'pending':
        cur.execute("UPDATE appointments SET payment_status='paid', paid_at=NOW() WHERE id=%s",
                    (appointment_id,))
        renumber_tokens(conn, appt['doctor_id'], appt['appointment_date'])
        flash('Payment confirmed! You got priority - your token number is updated.', 'success')
    else:
        flash('This appointment is already marked as paid.', 'info')
    cur.close()
    conn.close()
    return redirect(url_for('view_token', appointment_id=appointment_id))


@app.route('/queue', methods=['GET', 'POST'])
@patient_login_required
def queue_status():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id, doctor_id, doctor_name, appointment_date, token_number, status FROM appointments "
                "WHERE patient_id=%s ORDER BY id DESC", (session['patient_id'],))
    mine = cur.fetchall()

    if not mine:
        cur.close()
        conn.close()
        return render_template('queue_status.html', mine=[], selected=None, queue=[], queue_started=False)

    selected_id = request.form.get('appointment_id') if request.method == 'POST' else None
    selected = None
    if selected_id:
        for a in mine:
            if str(a['id']) == str(selected_id):
                selected = a
                break
    if selected is None:
        for a in mine:
            if a['status'] == 'waiting':
                selected = a
                break
    if selected is None:
        selected = mine[0]

    cur.execute("SELECT id, patient_name, token_number, appointment_time, status, payment_status "
                "FROM appointments WHERE doctor_id=%s AND appointment_date=%s "
                "ORDER BY CASE WHEN payment_status IN ('paid','cash') THEN 0 ELSE 1 END, "
                "COALESCE(paid_at, created_at), created_at, id",
                (selected['doctor_id'], str(selected['appointment_date'])))
    rows = cur.fetchall()
    sess = get_queue_session(conn, selected['doctor_id'], selected['appointment_date'])
    queue_started = sess['started_at'] is not None
    cur.close()
    conn.close()

    attending_token = None
    if queue_started:
        for r in rows:
            if r['status'] not in ('done', 'absent', 'cancelled', 'postponed'):
                attending_token = r['token_number']
                break

    queue = []
    for r in rows:
        is_attending = (queue_started and r['status'] not in ('done', 'absent', 'cancelled', 'postponed')
                        and r['token_number'] == attending_token)
        is_you = (r['token_number'] == selected['token_number'])
        if r['status'] == 'absent':
            label, cls = 'Unable to Attend', 'absent'
        elif r['status'] == 'cancelled':
            label, cls = 'Cancelled', 'cancelled'
        elif r['status'] == 'postponed':
            label, cls = 'Postponed', 'postponed'
        elif r['status'] == 'done':
            label, cls = 'Completed', 'done'
        elif not queue_started:
            label, cls = 'Waiting', 'waiting'
        elif is_you and is_attending:
            label, cls = 'You - Attending', 'you'
        elif is_you:
            label, cls = 'You - Waiting', 'you'
        elif is_attending:
            label, cls = 'Attending', 'attending'
        else:
            label, cls = 'Waiting', 'waiting'
        queue.append({'token': r['token_number'], 'name': r['patient_name'],
                      'time': r['appointment_time'], 'label': label, 'cls': cls,
                      'pay': r['payment_status']})

    return render_template('queue_status.html', mine=mine, selected=selected, queue=queue,
                           queue_started=queue_started, attending=attending_token)


@app.route('/reminder', methods=['GET', 'POST'])
@patient_login_required
def reminder():
    conn = get_db()
    cur = conn.cursor()

    if request.method == 'POST':
        appointment_id = request.form.get('appointment_id', '')
        medicine = request.form.get('medicine_name', '').strip()
        dosage = request.form.get('dosage', '').strip()
        frequency = request.form.get('frequency', 'Twice a day')
        food_timing = request.form.get('food_timing', 'after food')
        start_date = request.form.get('start_date', '')
        duration = request.form.get('duration_days', '')

        if not all([medicine, dosage, start_date, duration]) or not appointment_id.isdigit():
            flash('Please fill in all the reminder fields.', 'error')
        else:
            cur.execute("SELECT id, doctor_name FROM appointments WHERE id=%s AND patient_id=%s AND status='done'",
                        (int(appointment_id), session['patient_id']))
            appt = cur.fetchone()
            if not appt:
                flash('Reminder can be set only after your doctor marks the token as done.', 'error')
            else:
                try:
                    days = int(duration)
                    if days < 1:
                        days = 1
                except ValueError:
                    days = 1
                cur.execute(
                    "INSERT INTO reminders(patient_id, appointment_id, doctor_name, medicine_name, dosage, "
                    "frequency, food_timing, start_date, duration_days) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (session['patient_id'], int(appointment_id), appt['doctor_name'], medicine, dosage,
                     frequency, food_timing, start_date, days))
                flash('Medicine reminder saved successfully!', 'success')

    cur.execute("SELECT id, doctor_name, appointment_date, token_number FROM appointments "
                "WHERE patient_id=%s AND status='done' ORDER BY id DESC", (session['patient_id'],))
    done_appointments = cur.fetchall()

    cur.execute("SELECT * FROM reminders WHERE patient_id=%s ORDER BY id DESC", (session['patient_id'],))
    reminders = cur.fetchall()

    today = datetime.date.today()
    schedule = []
    active_reminders = []
    for r in reminders:
        end = r['start_date'] + datetime.timedelta(days=r['duration_days'] - 1)
        active = bool(r['is_active']) and r['start_date'] <= today <= end
        if active:
            active_reminders.append(r)
        pct = 0
        if r['duration_days'] > 0 and end >= r['start_date']:
            elapsed = max(0, min((today - r['start_date']).days, r['duration_days'] - 1))
            pct = round(elapsed / (r['duration_days'] - 1) * 100)
            if r['duration_days'] == 1:
                pct = 100 if active else 100
        schedule.append({**r, 'active': active, 'end_date': end, 'progress': pct})

    cur.close()
    conn.close()
    return render_template('medicine_reminder.html', done_appointments=done_appointments,
                           reminders=schedule, active_reminders=active_reminders, today=today)


@app.route('/reminder/toggle/<int:reminder_id>', methods=['POST'])
@patient_login_required
def reminder_toggle(reminder_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id, is_active FROM reminders WHERE id=%s AND patient_id=%s",
                (reminder_id, session['patient_id']))
    rem = cur.fetchone()
    if not rem:
        cur.close()
        conn.close()
        abort(404)
    if rem['is_active']:
        cur.execute("UPDATE reminders SET is_active=0 WHERE id=%s", (reminder_id,))
        flash('Medicine reminder turned OFF.', 'info')
    else:
        cur.execute("UPDATE reminders SET is_active=1 WHERE id=%s", (reminder_id,))
        flash('Medicine reminder turned ON.', 'success')
    cur.close()
    conn.close()
    return redirect(url_for('reminder'))


# ---------------------------------------------------------------- Doctor auth

@app.route('/doctor/register', methods=['GET', 'POST'])
def doctor_register():
    if request.method == 'POST':
        doctor_name = request.form.get('doctor_name', '').strip()
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip().lower() or None
        mobile = request.form.get('mobile', '').strip() or None
        specialization = request.form.get('specialization', '').strip() or None
        fees = request.form.get('fees', '').strip()
        password = request.form.get('password', '')
        confirm = request.form.get('confirm', '')

        if not all([doctor_name, username, password, confirm]):
            flash('Please fill in all the fields.', 'error')
        elif password != confirm:
            flash('Passwords do not match.', 'error')
        else:
            try:
                fees_val = float(fees) if fees else None
            except ValueError:
                fees_val = None
            conn = get_db()
            cur = conn.cursor()
            cur.execute("SELECT id FROM doctors WHERE username=%s", (username,))
            if cur.fetchone():
                flash('This username is already taken.', 'warning')
            else:
                gpay_qr = None
                file = request.files.get('gpay_qr')
                if file and file.filename:
                    if not allowed_file(file.filename):
                        flash('Please upload a valid QR image (png, jpg, jpeg, gif, webp).', 'error')
                        cur.close()
                        conn.close()
                        return render_template('doctor_register.html')
                    ext = file.filename.rsplit('.', 1)[1].lower()
                    mime = 'image/jpeg' if ext == 'jpg' else 'image/%s' % ext
                    raw = file.read()
                    gpay_qr = 'data:%s;base64,%s' % (mime, base64.b64encode(raw).decode('ascii'))
                cur.execute(
                    "INSERT INTO doctors(doctor_name, username, password, email, mobile, specialization, fees, gpay_qr) "
                    "VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
                    (doctor_name, username, generate_password_hash(password), email,
                     mobile, specialization, fees_val, gpay_qr))
                flash('Doctor account created! Please login.', 'success')
                cur.close()
                conn.close()
                return redirect(url_for('doctor_login'))
            cur.close()
            conn.close()
    return render_template('doctor_register.html')


@app.route('/doctor/login', methods=['GET', 'POST'])
def doctor_login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM doctors WHERE username=%s", (username,))
        doctor = cur.fetchone()
        cur.close()
        conn.close()
        if doctor and check_password_hash(doctor['password'], password):
            session.clear()
            session['doctor_id'] = doctor['id']
            session['doctor_name'] = doctor['doctor_name']
            flash('Welcome back, Dr. %s!' % doctor['doctor_name'], 'success')
            return redirect(url_for('doctor_dashboard'))
        flash('Invalid username or password.', 'error')
    return render_template('doctor_login.html')


@app.route('/doctor/forgot-password', methods=['GET', 'POST'])
def doctor_forgot_password():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        mobile = request.form.get('mobile', '').strip()
        password = request.form.get('password', '')
        confirm = request.form.get('confirm', '')
        if not all([username, mobile, password, confirm]):
            flash('Please fill in all the fields.', 'error')
        elif password != confirm:
            flash('Passwords do not match.', 'error')
        else:
            conn = get_db()
            cur = conn.cursor()
            cur.execute("SELECT id FROM doctors WHERE username=%s AND mobile=%s", (username, mobile))
            doc = cur.fetchone()
            if not doc:
                flash('No doctor found with that username & mobile.', 'error')
            else:
                cur.execute("UPDATE doctors SET password=%s WHERE id=%s",
                            (generate_password_hash(password), doc['id']))
                flash('Password reset successful! Please login.', 'success')
                cur.close()
                conn.close()
                return redirect(url_for('doctor_login'))
            cur.close()
            conn.close()
    return render_template('doctor_forgot.html')


@app.route('/doctor/profile', methods=['GET', 'POST'])
@doctor_login_required
def doctor_profile():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM doctors WHERE id=%s", (session['doctor_id'],))
    doctor = cur.fetchone()
    if request.method == 'POST':
        doctor_name = request.form.get('doctor_name', '').strip()
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip().lower() or None
        mobile = request.form.get('mobile', '').strip() or None
        specialization = request.form.get('specialization', '').strip() or None
        fees = request.form.get('fees', '').strip()
        password = request.form.get('password', '')
        confirm = request.form.get('confirm', '')
        if not all([doctor_name, username]):
            flash('Doctor name and username are required.', 'error')
        elif password and password != confirm:
            flash('New passwords do not match.', 'error')
        else:
            try:
                fees_val = float(fees) if fees else None
            except ValueError:
                fees_val = None
            cur.execute("SELECT id FROM doctors WHERE username=%s AND id!=%s",
                        (username, session['doctor_id']))
            if cur.fetchone():
                flash('That username is already taken.', 'error')
            else:
                gpay_qr = doctor['gpay_qr']
                file = request.files.get('gpay_qr')
                if file and file.filename:
                    if not allowed_file(file.filename):
                        flash('Please upload a valid QR image (png, jpg, jpeg, gif, webp).', 'error')
                        cur.close()
                        conn.close()
                        return render_template('doctor_profile.html', doctor=doctor)
                    ext = file.filename.rsplit('.', 1)[1].lower()
                    mime = 'image/jpeg' if ext == 'jpg' else 'image/%s' % ext
                    raw = file.read()
                    gpay_qr = 'data:%s;base64,%s' % (mime, base64.b64encode(raw).decode('ascii'))
                if password:
                    cur.execute(
                        "UPDATE doctors SET doctor_name=%s, username=%s, email=%s, mobile=%s, "
                        "specialization=%s, fees=%s, gpay_qr=%s, password=%s WHERE id=%s",
                        (doctor_name, username, email, mobile, specialization, fees_val, gpay_qr,
                         generate_password_hash(password), session['doctor_id']))
                    flash('Profile updated (password changed)!', 'success')
                else:
                    cur.execute(
                        "UPDATE doctors SET doctor_name=%s, username=%s, email=%s, mobile=%s, "
                        "specialization=%s, fees=%s, gpay_qr=%s WHERE id=%s",
                        (doctor_name, username, email, mobile, specialization, fees_val, gpay_qr,
                         session['doctor_id']))
                    flash('Profile updated!', 'success')
                session['doctor_name'] = doctor_name
                cur.close()
                conn.close()
                return redirect(url_for('doctor_profile'))
    cur.close()
    conn.close()
    return render_template('doctor_profile.html', doctor=doctor)


@app.route('/doctor/logout')
def doctor_logout():
    session.clear()
    flash('You have been logged out.', 'info')
    return redirect(url_for('index'))


# ---------------------------------------------------------------- Doctor app

@app.route('/doctor/dashboard')
@doctor_login_required
def doctor_dashboard():
    conn = get_db()
    cur = conn.cursor()
    today = datetime.date.today()
    cur.execute("SELECT doctor_name FROM doctors WHERE id=%s", (session['doctor_id'],))
    doc = cur.fetchone()
    cur.execute("SELECT COUNT(*) AS c FROM appointments WHERE doctor_id=%s AND appointment_date=%s",
                (session['doctor_id'], today))
    total = cur.fetchone()['c']
    cur.execute("SELECT COUNT(*) AS c FROM appointments WHERE doctor_id=%s AND appointment_date=%s AND status='waiting'",
                (session['doctor_id'], today))
    waiting = cur.fetchone()['c']
    cur.execute("SELECT COUNT(*) AS c FROM appointments WHERE doctor_id=%s AND appointment_date=%s AND status='done'",
                (session['doctor_id'], today))
    done = cur.fetchone()['c']
    cur.execute("SELECT patient_name, token_number, appointment_time, payment_mode, payment_status, status FROM appointments "
                "WHERE doctor_id=%s AND appointment_date=%s ORDER BY token_number LIMIT 8",
                (session['doctor_id'], today))
    preview = cur.fetchall()
    cur.close()
    conn.close()
    return render_template('doctor_dashboard.html', doc=doc, total=total, waiting=waiting,
                           done=done, today=today, preview=preview)


@app.route('/doctor/queue')
@doctor_login_required
def doctor_queue():
    conn = get_db()
    cur = conn.cursor()
    today = datetime.date.today()
    cur.execute("SELECT * FROM appointments WHERE doctor_id=%s AND appointment_date=%s "
                "ORDER BY CASE WHEN payment_status IN ('paid','cash') THEN 0 ELSE 1 END, "
                "COALESCE(paid_at, created_at), created_at, id",
                (session['doctor_id'], today))
    queue = cur.fetchall()
    sess = get_queue_session(conn, session['doctor_id'], today)
    queue_started = sess['started_at'] is not None
    cur.close()
    conn.close()

    attending = None
    attending_name = None
    if queue_started:
        for q in queue:
            if q['status'] not in ('done', 'absent', 'cancelled', 'postponed'):
                attending = q['token_number']
                attending_name = q['patient_name']
                break
    total = len(queue)
    waiting = sum(1 for q in queue if q['status'] == 'waiting')
    done = sum(1 for q in queue if q['status'] == 'done')
    has_postponed = any(q['status'] == 'postponed' for q in queue)
    tomorrow = (today + datetime.timedelta(days=1)).isoformat()
    return render_template('doctor_queue.html', queue=queue, attending=attending, attending_name=attending_name,
                           queue_started=queue_started, total=total, waiting=waiting, done=done, today=today,
                           tomorrow=tomorrow, has_postponed=has_postponed)


@app.route('/doctor/start-queue', methods=['POST'])
@doctor_login_required
def start_queue():
    conn = get_db()
    cur = conn.cursor()
    today = datetime.date.today()
    cur.execute("SELECT COUNT(*) AS c FROM appointments WHERE doctor_id=%s AND appointment_date=%s "
                "AND status NOT IN ('done','absent','cancelled','postponed')", (session['doctor_id'], today))
    has_appointments = cur.fetchone()['c'] > 0
    if not has_appointments:
        cur.close()
        conn.close()
        flash('No active appointments for today yet. Start Queue will work once a patient books a token.',
              'error')
        return redirect(url_for('doctor_queue'))
    try:
        cur.execute("INSERT INTO queue_sessions(doctor_id, queue_date, started_at) VALUES(%s,%s,NOW())",
                    (session['doctor_id'], today))
    except pymysql.err.IntegrityError:
        cur.execute("UPDATE queue_sessions SET started_at=NOW() "
                    "WHERE doctor_id=%s AND queue_date=%s AND started_at IS NULL",
                    (session['doctor_id'], today))
    cur.close()
    conn.close()
    flash('Queue started! Now attending tokens.', 'success')
    return redirect(url_for('doctor_queue'))


@app.route('/doctor/mark-done/<int:appointment_id>', methods=['POST'])
@doctor_login_required
def mark_done(appointment_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE appointments SET status='done' WHERE id=%s AND doctor_id=%s "
                "AND status NOT IN ('done','absent','cancelled','postponed')",
                (appointment_id, session['doctor_id']))
    cur.close()
    conn.close()
    flash('Token marked as done. The patient token is now inactive.', 'success')
    return redirect(url_for('doctor_queue'))


@app.route('/doctor/toggle-payment/<int:appointment_id>', methods=['POST'])
@doctor_login_required
def doctor_toggle_payment(appointment_id):
    conn = get_db()
    cur = conn.cursor()
    today = datetime.date.today()
    cur.execute("SELECT doctor_id, appointment_date, payment_status, status FROM appointments "
                "WHERE id=%s AND doctor_id=%s", (appointment_id, session['doctor_id']))
    appt = cur.fetchone()
    if not appt:
        cur.close()
        conn.close()
        abort(404)
    if appt['appointment_date'] != today:
        cur.close()
        conn.close()
        flash('You can update payment only for today\'s appointments.', 'error')
        return redirect(url_for('doctor_queue'))
    if appt['status'] in ('done', 'absent', 'cancelled', 'postponed'):
        cur.close()
        conn.close()
        flash('This token is closed, so payment cannot be changed.', 'error')
        return redirect(url_for('doctor_queue'))
    if appt['payment_status'] == 'paid':
        cur.execute("UPDATE appointments SET payment_status='pending', paid_at=NULL WHERE id=%s",
                    (appointment_id,))
        msg = 'Token marked as Not Paid.'
        flash_type = 'warning'
    else:
        cur.execute("UPDATE appointments SET payment_status='paid', paid_at=NOW() WHERE id=%s",
                    (appointment_id,))
        msg = 'Token marked as Paid - it now moves ahead in the queue.'
        flash_type = 'success'
    renumber_tokens(conn, session['doctor_id'], today)
    cur.close()
    conn.close()
    flash(msg, flash_type)
    return redirect(url_for('doctor_queue'))


@app.route('/doctor/cancel/<int:appointment_id>', methods=['POST'])
@doctor_login_required
def doctor_cancel_appointment(appointment_id):
    conn = get_db()
    cur = conn.cursor()
    today = datetime.date.today()
    cur.execute("SELECT doctor_id, appointment_date FROM appointments WHERE id=%s AND doctor_id=%s",
                (appointment_id, session['doctor_id']))
    appt = cur.fetchone()
    if not appt:
        cur.close()
        conn.close()
        abort(404)
    if appt['appointment_date'] != today:
        cur.close()
        conn.close()
        flash('You can cancel only today\'s appointments from the live queue.', 'error')
        return redirect(url_for('doctor_queue'))
    cur.execute("UPDATE appointments SET status='cancelled' WHERE id=%s AND doctor_id=%s "
                "AND status NOT IN ('done','absent','cancelled','postponed')",
                (appointment_id, session['doctor_id']))
    renumber_tokens(conn, session['doctor_id'], today)
    cur.close()
    conn.close()
    flash('Appointment cancelled. The queue updated automatically - the next token now has priority.',
          'success')
    return redirect(url_for('doctor_queue'))


@app.route('/doctor/postpone', methods=['POST'])
@doctor_login_required
def doctor_postpone_queue():
    conn = get_db()
    cur = conn.cursor()
    today = datetime.date.today()
    try:
        new_date = datetime.datetime.strptime(request.form.get('new_date', '').strip(), '%Y-%m-%d').date()
    except ValueError:
        new_date = today + datetime.timedelta(days=1)
    if new_date <= today:
        flash('Postponed date must be a future date.', 'error')
        cur.close()
        conn.close()
        return redirect(url_for('doctor_queue'))
    cur.execute("UPDATE appointments SET status='postponed', postponed_date=%s "
                "WHERE doctor_id=%s AND appointment_date=%s "
                "AND status NOT IN ('done','absent','cancelled','postponed')",
                (new_date, session['doctor_id'], today))
    cur.execute("UPDATE queue_sessions SET ended_at=NOW() "
                "WHERE doctor_id=%s AND queue_date=%s AND ended_at IS NULL",
                (session['doctor_id'], today))
    cur.close()
    conn.close()
    flash('Today\'s queue postponed to %s. Patients can see the new date in their dashboard. '
          'Amounts paid are NOT refundable.' % new_date.strftime('%d %b %Y'), 'warning')
    return redirect(url_for('doctor_queue'))


@app.route('/doctor/cancel-postpone', methods=['POST'])
@doctor_login_required
def doctor_cancel_postpone():
    conn = get_db()
    cur = conn.cursor()
    today = datetime.date.today()
    cur.execute("UPDATE appointments SET status='waiting', postponed_date=NULL "
                "WHERE doctor_id=%s AND appointment_date=%s AND status='postponed'",
                (session['doctor_id'], today))
    cur.close()
    conn.close()
    flash('Postponement cancelled. Appointments restored to waiting - you can start the queue now.',
          'success')
    return redirect(url_for('doctor_queue'))


@app.route('/patient/unavailable/<int:appointment_id>', methods=['POST'])
@patient_login_required
def patient_unavailable(appointment_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT doctor_id, appointment_date FROM appointments "
                "WHERE id=%s AND patient_id=%s", (appointment_id, session['patient_id']))
    appt = cur.fetchone()
    if not appt:
        cur.close()
        conn.close()
        abort(404)
    today = datetime.date.today()
    if appt['appointment_date'] != today:
        cur.close()
        conn.close()
        flash('You can mark yourself unavailable only for today\'s appointment.', 'error')
        return redirect(url_for('view_token', appointment_id=appointment_id))
    cur.execute("UPDATE appointments SET status='absent' WHERE id=%s AND patient_id=%s "
                "AND status='waiting'", (appointment_id, session['patient_id']))
    cur.close()
    conn.close()
    flash('Marked as unable to attend. The doctor queue will show this token as unavailable.', 'info')
    return redirect(url_for('view_token', appointment_id=appointment_id))


@app.route('/doctor/report')
@doctor_login_required
def doctor_report_pdf():
    conn = get_db()
    cur = conn.cursor()
    today = datetime.date.today()
    cur.execute("SELECT doctor_name FROM doctors WHERE id=%s", (session['doctor_id'],))
    doc = cur.fetchone()
    cur.execute("SELECT token_number, patient_name, appointment_time, payment_mode, amount FROM appointments "
                "WHERE doctor_id=%s AND appointment_date=%s AND status='done' ORDER BY token_number",
                (session['doctor_id'], today))
    rows = cur.fetchall()
    cur.execute("SELECT COUNT(*) AS c FROM appointments WHERE doctor_id=%s AND appointment_date=%s",
                (session['doctor_id'], today))
    total_count = cur.fetchone()['c']
    cur.close()
    conn.close()

    done_count = len(rows)
    filename = secure_filename('report_%s_%s.pdf' % (doc['doctor_name'], today))
    return send_file(build_report_pdf(doc['doctor_name'], rows, today, done_count, total_count),
                     as_attachment=True, download_name=filename, mimetype='application/pdf')


if __name__ == '__main__':
    try:
        init_database()
    except Exception:
        pass
    app.run(debug=True, host='0.0.0.0', port=5000)
