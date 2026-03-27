import sqlite3
import os
import sys
import re
import io
import json
import hashlib
import time
import heapq
import secrets
import socket
import webbrowser
import random
from datetime import datetime, timedelta
from threading import Timer
import pytesseract
import cv2
import numpy as np
from PIL import Image
from flask import Flask, render_template, request, jsonify, session, redirect, url_for, flash, g
from google.cloud import vision
from google.oauth2 import service_account
from logger import ActivityLogger
from collections import defaultdict, OrderedDict
import bcrypt
from functools import wraps



# Add this near the top of app.py, after the imports
import os

# Determine if we're in production
IS_PRODUCTION = os.environ.get('PYTHONANYWHERE_DOMAIN') or os.environ.get('RENDER')

# Database paths - adjust for production
if IS_PRODUCTION:
    # On PythonAnywhere, use absolute paths
    BASE_DIR = '/home/mwangi/web2py/CHCH'
else:
    BASE_DIR = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.dirname(__file__)

PROD_DB_PATH = os.path.join(BASE_DIR, "church.db")
TEST_DB_PATH = os.path.join(BASE_DIR, "church_test.db")

# --- 1. EXE COMPATIBILITY & PATHS ---
def resource_path(relative_path):
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.abspath("."), relative_path)

app = Flask(__name__,
            template_folder=resource_path("templates"),
            static_folder=resource_path("static"))

app.secret_key = "church_key_2026"
app.config['SESSION_TYPE'] = 'filesystem'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=24)

BASE_DIR = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.dirname(__file__)

# Database paths - separate for production and test
PROD_DB_PATH = os.path.join(BASE_DIR, "church.db")
TEST_DB_PATH = os.path.join(BASE_DIR, "church_test.db")

def get_db_path():
    """Get the appropriate database path based on session mode"""
    if session.get('test_mode'):
        return TEST_DB_PATH
    return PROD_DB_PATH

activity_logger = ActivityLogger(app)

# --- TESSERACT CONFIGURATION ---
pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'

# --- GOOGLE CLOUD VISION AI INITIALIZATION ---
try:
    credentials = service_account.Credentials.from_service_account_file(
        'church-ocr-key.json'
    )
    vision_client = vision.ImageAnnotatorClient(credentials=credentials)
    AI_AVAILABLE = True
    print("✅ Google Cloud Vision initialized successfully")
except Exception as e:
    print(f"⚠️ Google Vision not available: {e}")
    print("Falling back to Tesseract OCR only")
    AI_AVAILABLE = False

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def get_local_ip():
    """Get local network IP address"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return "127.0.0.1"

def open_browser():
    """Automatically open browser after server starts"""
    webbrowser.open_new('http://127.0.0.1:5000')

# ============================================================================
# DATA STRUCTURES & ALGORITHMS
# ============================================================================

# --- DUPLICATE PREVENTION USING BLOOM FILTER ---
class BloomFilter:
    """Bloom Filter for efficient duplicate detection"""
    def __init__(self, size=10000, hash_count=7):
        self.size = size
        self.hash_count = hash_count
        self.bit_array = [0] * size
        self.count = 0
        
    def _hashes(self, item):
        """Generate multiple hash values for the item"""
        result = []
        for i in range(self.hash_count):
            hash_value = int(hashlib.md5(f"{item}{i}".encode()).hexdigest(), 16)
            result.append(hash_value % self.size)
        return result
    
    def add(self, item):
        """Add item to bloom filter"""
        for hash_value in self._hashes(item):
            self.bit_array[hash_value] = 1
        self.count += 1
    
    def might_contain(self, item):
        """Check if item might exist"""
        return all(self.bit_array[h] for h in self._hashes(item))

# --- LRU CACHE FOR RECENT RECORDS ---
class LRUCache:
    """Least Recently Used Cache for recent record lookups"""
    def __init__(self, capacity=200):
        self.cache = OrderedDict()
        self.capacity = capacity
    
    def get(self, key):
        if key not in self.cache:
            return None
        self.cache.move_to_end(key)
        return self.cache[key]
    
    def put(self, key, value):
        self.cache[key] = value
        self.cache.move_to_end(key)
        if len(self.cache) > self.capacity:
            self.cache.popitem(last=False)
    
    def contains(self, key):
        return key in self.cache

# --- RATE LIMITER USING TOKEN BUCKET ALGORITHM ---
class TokenBucket:
    """Token Bucket algorithm for rate limiting"""
    def __init__(self, capacity, fill_rate):
        self.capacity = capacity
        self.fill_rate = fill_rate
        self.tokens = capacity
        self.last_update = time.time()
    
    def consume(self, tokens=1):
        """Consume tokens from the bucket"""
        now = time.time()
        elapsed = now - self.last_update
        self.tokens = min(self.capacity, self.tokens + elapsed * self.fill_rate)
        self.last_update = now
        
        if self.tokens >= tokens:
            self.tokens -= tokens
            return True
        return False

class LoginRateLimiter:
    """Rate limiter for login attempts using multiple data structures"""
    def __init__(self):
        self.ip_buckets = defaultdict(lambda: TokenBucket(5, 1/60))  # 5 attempts per minute
        self.user_attempts = LRUCache(capacity=1000)
        self.blocked_ips = BloomFilter(size=10000, hash_count=5)
        self.attempt_heap = []
        self.attempt_id = 0
    
    def is_allowed(self, ip_address, username):
        """Check if login attempt is allowed"""
        if self.blocked_ips.might_contain(ip_address):
            return False
        
        if not self.ip_buckets[ip_address].consume():
            self._record_failed_attempt(ip_address, username)
            return False
        
        user_key = f"user:{username}"
        user_attempts = self.user_attempts.get(user_key) or 0
        
        if user_attempts >= 3:
            return False
        
        return True
    
    def _record_failed_attempt(self, ip_address, username):
        """Record failed login attempt"""
        user_key = f"user:{username}"
        self.user_attempts.put(user_key, (self.user_attempts.get(user_key) or 0) + 1)
        
        heapq.heappush(self.attempt_heap, (time.time(), self.attempt_id, ip_address, username))
        self.attempt_id += 1
        
        recent_attempts = self._count_recent_attempts(ip_address)
        if recent_attempts >= 10:
            self.blocked_ips.add(ip_address)
    
    def _count_recent_attempts(self, ip_address, window=300):
        """Count recent attempts using heap"""
        now = time.time()
        count = 0
        
        while self.attempt_heap and self.attempt_heap[0][0] < now - window:
            heapq.heappop(self.attempt_heap)
        
        for attempt in self.attempt_heap:
            if attempt[2] == ip_address:
                count += 1
        
        return count
    
    def reset_user(self, username):
        """Reset attempts for a user after successful login"""
        self.user_attempts.put(f"user:{username}", 0)

# --- PASSWORD MANAGER WITH BCRYPT ---
class PasswordManager:
    """Secure password management using bcrypt"""
    
    @staticmethod
    def hash_password(password):
        """Hash password with salt"""
        salt = bcrypt.gensalt(rounds=12)
        hashed = bcrypt.hashpw(password.encode('utf-8'), salt)
        return hashed.decode('utf-8')
    
    @staticmethod
    def verify_password(password, hashed):
        """Verify password against hash"""
        try:
            return bcrypt.checkpw(
                password.encode('utf-8'),
                hashed.encode('utf-8')
            )
        except:
            return False
    
    @staticmethod
    def is_password_strong(password):
        """Check password strength using regex"""
        if len(password) < 8:
            return False, "Password must be at least 8 characters"
        if not re.search(r"[A-Z]", password):
            return False, "Password must contain at least one uppercase letter"
        if not re.search(r"[a-z]", password):
            return False, "Password must contain at least one lowercase letter"
        if not re.search(r"\d", password):
            return False, "Password must contain at least one number"
        if not re.search(r"[!@#$%^&*(),.?\":{}|<>]", password):
            return False, "Password must contain at least one special character"
        return True, "Password is strong"

# --- SESSION MANAGER ---
class SessionManager:
    """Manage user sessions using graph data structure"""
    def __init__(self):
        self.sessions = {}
        self.user_sessions = defaultdict(set)
        
    def create_session(self, user_id, username, role):
        """Create new session with secure token"""
        session_id = secrets.token_urlsafe(32)
        expiry = datetime.now() + timedelta(hours=24)
        
        session_data = {
            'user_id': user_id,
            'username': username,
            'role': role,
            'created': datetime.now(),
            'expiry': expiry,
            'last_activity': datetime.now()
        }
        
        self.sessions[session_id] = session_data
        self.user_sessions[user_id].add(session_id)
        
        return session_id
    
    def validate_session(self, session_id):
        """Validate session and update last activity"""
        if session_id not in self.sessions:
            return None
        
        session_data = self.sessions[session_id]
        if datetime.now() > session_data['expiry']:
            self.invalidate_session(session_id)
            return None
        
        session_data['last_activity'] = datetime.now()
        return session_data
    
    def invalidate_session(self, session_id):
        """Remove session from all data structures"""
        if session_id in self.sessions:
            user_id = self.sessions[session_id]['user_id']
            del self.sessions[session_id]
            self.user_sessions[user_id].discard(session_id)

# Initialize security components
rate_limiter = LoginRateLimiter()
password_manager = PasswordManager()
session_manager = SessionManager()
record_bloom = BloomFilter(size=10000, hash_count=7)
recent_records_cache = LRUCache(capacity=200)

# ============================================================================
# TEST DATA GENERATOR
# ============================================================================

def generate_test_data(db_path):
    """Generate realistic test data for the test database"""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    print("📊 Generating test data...")
    
    # Check if test data already exists
    count = cursor.execute("SELECT COUNT(*) FROM saturday_records").fetchone()[0]
    if count > 0:
        print("✅ Test data already exists")
        conn.close()
        return
    
    # Generate 12 weeks of test data
    for week in range(1, 13):
        # Generate realistic attendance numbers
        men = random.randint(400, 600)
        women = random.randint(450, 650)
        youth = random.randint(250, 400)
        children = random.randint(150, 300)
        sunday = random.randint(100, 200)
        
        # Generate financial data with trends
        base_tithe = 1500 + (week * 50) + random.randint(-100, 200)
        base_offering = 500 + (week * 20) + random.randint(-50, 100)
        
        # Sometimes add fundraising
        if week % 3 == 0:  # Every 3rd week
            fr_amount = random.randint(1000, 3000)
            fr_type = random.choice(['emergency', 'planned'])
        else:
            fr_amount = 0
            fr_type = 'none'
        
        reg_giving = base_tithe + base_offering
        
        cursor.execute('''
            INSERT INTO saturday_records 
            (saturday_no, men, women, youth, children, sunday, tithe, offering, fr_amount, fr_type, reg_giving, year)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (week, men, women, youth, children, sunday, base_tithe, base_offering, fr_amount, fr_type, reg_giving, 2026))
    
    # Generate test projects
    projects = [
        ('New Sanctuary Fund', 'Building a new sanctuary for the growing congregation', 'ongoing', 500000, 125000),
        ('Youth Center Renovation', 'Renovating the youth meeting space', 'planning', 100000, 25000),
        ('Community Outreach Program', 'Weekly food distribution program', 'ongoing', 50000, 35000),
        ('Church Van Purchase', 'Buying a van for transportation', 'completed', 45000, 45000),
    ]
    
    for proj in projects:
        cursor.execute('''
            INSERT INTO projects (name, description, status, target_amount, raised_amount)
            VALUES (?, ?, ?, ?, ?)
        ''', proj)
    
    # Generate test expenses
    expense_categories = ['Electricity', 'Water', 'Maintenance', 'Salaries', 'Supplies', 'Events']
    for _ in range(20):
        category = random.choice(expense_categories)
        amount = random.randint(500, 5000)
        days_ago = random.randint(1, 90)
        expense_date = (datetime.now() - timedelta(days=days_ago)).strftime('%Y-%m-%d')
        
        cursor.execute('''
            INSERT INTO expenses (category, amount, expense_date)
            VALUES (?, ?, ?)
        ''', (category, amount, expense_date))
    
    conn.commit()
    conn.close()
    print("✅ Test data generated successfully!")

# ============================================================================
# DATABASE INITIALIZATION
# ============================================================================

def init_db(db_path, is_test=False):
    """Initialize a database with the required schema"""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    print(f"{'🧪' if is_test else '📁'} Initializing database: {db_path}")
    
    # Users table
    cursor.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        role TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        last_login TIMESTAMP,
        is_active INTEGER DEFAULT 1
    )''')
    
    # Activity logs table
    cursor.execute('''CREATE TABLE IF NOT EXISTS activity_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
        user_id INTEGER,
        username TEXT,
        action TEXT,
        details TEXT,
        ip_address TEXT,
        user_agent TEXT,
        endpoint TEXT,
        method TEXT,
        status TEXT)''')

    # Saturday records table
    cursor.execute('''CREATE TABLE IF NOT EXISTS saturday_records (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        saturday_no INTEGER,
        men INTEGER DEFAULT 0, 
        women INTEGER DEFAULT 0, 
        youth INTEGER DEFAULT 0, 
        children INTEGER DEFAULT 0,
        sunday INTEGER DEFAULT 0,
        tithe REAL DEFAULT 0.0, 
        offering REAL DEFAULT 0.0, 
        fr_amount REAL DEFAULT 0.0, 
        fr_type TEXT DEFAULT 'none',
        reg_giving REAL DEFAULT 0.0,
        year INTEGER DEFAULT 2026,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')

    # Settings table
    cursor.execute('''CREATE TABLE IF NOT EXISTS settings (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        target_attendance INTEGER DEFAULT 0,
        target_offering REAL DEFAULT 0.0,
        target_men INTEGER DEFAULT 1300,
        target_women INTEGER DEFAULT 1600,
        target_youth INTEGER DEFAULT 780,
        target_children INTEGER DEFAULT 900,
        target_tithe INTEGER DEFAULT 800000,
        target_offering_amount INTEGER DEFAULT 200000,
        church_name TEXT DEFAULT 'My Church',
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')

    # Projects table
    cursor.execute('''CREATE TABLE IF NOT EXISTS projects (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        description TEXT,
        status TEXT DEFAULT 'ongoing',
        target_amount REAL DEFAULT 0.0,
        raised_amount REAL DEFAULT 0.0,
        start_date DATE,
        end_date DATE,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')

    # Expenses table
    cursor.execute('''CREATE TABLE IF NOT EXISTS expenses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        category TEXT NOT NULL,
        amount REAL NOT NULL,
        description TEXT,
        expense_date DATE DEFAULT CURRENT_DATE,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')

    cursor.execute('INSERT OR IGNORE INTO settings (id, target_attendance, target_offering, target_men, target_women, target_youth, target_children, target_tithe, target_offering_amount) VALUES (1, 0, 0.0, 1300, 1600, 780, 900, 800000, 200000)')
    
    conn.commit()
    conn.close()
    print(f"✅ Database initialized: {db_path}")

def init_all_databases():
    """Initialize both production and test databases"""
    # Initialize production database
    init_db(PROD_DB_PATH, is_test=False)
    
    # Initialize test database
    init_db(TEST_DB_PATH, is_test=True)
    
    # Generate test data for test database
    generate_test_data(TEST_DB_PATH)

# ============================================================================
# DECORATORS
# ============================================================================

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            flash("Please log in to access this page.")
            return redirect(url_for('login_page'))
        
        session_id = session.get('session_id')
        if not session_id or not session_manager.validate_session(session_id):
            session.clear()
            flash("Your session has expired. Please log in again.")
            return redirect(url_for('login_page'))
        
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            flash("Please log in to access this page.")
            return redirect(url_for('login_page'))
        
        if session.get('role') != 'admin':
            flash("Access denied. Admin privileges required.")
            return redirect(url_for('mode_select'))
        
        session_id = session.get('session_id')
        if not session_id or not session_manager.validate_session(session_id):
            session.clear()
            flash("Your session has expired. Please log in again.")
            return redirect(url_for('login_page'))
        
        return f(*args, **kwargs)
    return decorated_function

# ============================================================================
# OCR FUNCTION
# ============================================================================

def enhanced_ocr_with_ai(image_bytes):
    """Enhanced OCR using Google Cloud Vision AI with Tesseract fallback"""
    extracted_text = ""
    method_used = "tesseract"
    
    if AI_AVAILABLE:
        try:
            image = vision.Image(content=image_bytes)
            response = vision_client.document_text_detection(image=image)
            
            if response.error.message:
                print(f"AI Error: {response.error.message}")
            else:
                if response.full_text_annotation:
                    extracted_text = response.full_text_annotation.text
                    method_used = "google_vision_document"
                    print("✅ Google Vision AI successful")
                    return extracted_text, method_used
        except Exception as e:
            print(f"Google Vision failed: {e}")
    
    try:
        nparr = np.frombuffer(image_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        
        if img is None:
            return "", "failed"
            
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        _, thresh = cv2.threshold(gray, 150, 255, cv2.THRESH_BINARY)
        
        custom_config = r'--oem 3 --psm 6'
        extracted_text = pytesseract.image_to_string(thresh, config=custom_config)
        method_used = "tesseract"
        print("✅ Tesseract OCR successful")
    except Exception as e:
        print(f"Tesseract failed: {e}")
        extracted_text = ""
        method_used = "failed"
    
    return extracted_text, method_used

# ============================================================================
# AUTHENTICATION ROUTES
# ============================================================================

@app.route('/')
def home():
    # If already logged in, go to mode selection
    if session.get('logged_in'):
        return redirect(url_for('mode_select'))
    
    conn = sqlite3.connect(PROD_DB_PATH)
    user_count = conn.execute('SELECT COUNT(*) FROM users').fetchone()[0]
    conn.close()
    
    if user_count == 0:
        return redirect(url_for('register'))
    return redirect(url_for('login_page'))

@app.route('/mode-select')
@login_required
def mode_select():
    """Page to choose between test mode and production mode"""
    return render_template('mode_select.html')

@app.route('/enter-test-mode')
@login_required
def enter_test_mode():
    """Switch to test mode"""
    session['test_mode'] = True
    session['db_mode'] = 'test'
    flash("🧪 You are now in TEST MODE. All data is temporary and for practice only.")
    return redirect(url_for('dashboard'))

@app.route('/enter-production-mode')
@login_required
def enter_production_mode():
    """Switch to production mode"""
    session['test_mode'] = False
    session['db_mode'] = 'production'
    flash("📁 You are now in PRODUCTION MODE. All changes will affect real data.")
    return redirect(url_for('dashboard'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    # If already logged in, go to mode selection
    if session.get('logged_in'):
        return redirect(url_for('mode_select'))
    
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        role = request.form.get('role')
        
        if not username or not password or not role:
            return render_template('register.html', error="All fields are required")
        
        is_strong, message = password_manager.is_password_strong(password)
        if not is_strong:
            return render_template('register.html', error=message)
        
        hashed_password = password_manager.hash_password(password)
        
        conn = sqlite3.connect(PROD_DB_PATH)
        try:
            conn.execute('INSERT INTO users (username, password, role) VALUES (?, ?, ?)',
                        (username, hashed_password, role))
            conn.commit()
            
            activity_logger.log_event(
                action="user_registered",
                details={"username": username, "role": role},
                status="success"
            )
            
            flash("Registration successful! Please log in.")
            return redirect(url_for('login_page'))
        except sqlite3.IntegrityError:
            return render_template('register.html', error="Username already exists")
        finally:
            conn.close()
    
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login_page():
    if session.get('logged_in'):
        return redirect(url_for('mode_select'))
    
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        ip_address = request.remote_addr
        
        if not username or not password:
            return render_template('login.html', error="Username and password are required")
        
        if not rate_limiter.is_allowed(ip_address, username):
            activity_logger.log_event(
                action="rate_limit_exceeded",
                details={"ip": ip_address, "username": username},
                status="warning"
            )
            return render_template('login.html', 
                                 error="Too many attempts. Please try again later."), 429
        
        conn = sqlite3.connect(PROD_DB_PATH)
        conn.row_factory = sqlite3.Row
        user = conn.execute('SELECT * FROM users WHERE username=?', (username,)).fetchone()
        conn.close()
        
        if user and password_manager.verify_password(password, user['password']):
            session.clear()
            
            session_id = session_manager.create_session(
                user['id'], 
                user['username'], 
                user['role']
            )
            
            session['session_id'] = session_id
            session['username'] = user['username']
            session['user_id'] = user['id']
            session['role'] = user['role']
            session['logged_in'] = True
            session.permanent = True
            # Default to production mode
            session['test_mode'] = False
            session['db_mode'] = 'production'
            
            rate_limiter.reset_user(username)
            activity_logger.log_login(username, success=True)
            
            flash(f"Welcome back, {username}!")
            return redirect(url_for('mode_select'))
        
        rate_limiter._record_failed_attempt(ip_address, username)
        activity_logger.log_login(username, success=False)
        
        return render_template('login.html', error="Invalid username or password"), 401
    
    return render_template('login.html')

@app.route('/logout')
def logout():
    username = session.get('username', 'unknown')
    activity_logger.log_logout(username)
    
    session_id = session.get('session_id')
    if session_id:
        session_manager.invalidate_session(session_id)
    
    session.clear()
    flash("You have been logged out successfully.")
    return redirect(url_for('login_page'))

# ============================================================================
# OCR SCANNING ENDPOINT
# ============================================================================

@app.route('/scan-records', methods=['POST'])
@login_required
def scan_records():
    if 'file' not in request.files:
        return jsonify({"status": "error", "message": "No file uploaded"}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({"status": "error", "message": "No file selected"}), 400
    
    try:
        img_bytes = file.read()
        extracted_text, method_used = enhanced_ocr_with_ai(img_bytes)
        
        # ADVANCED PARSING
        lines = extracted_text.split('\n')
        all_text = extracted_text.lower()
        
        men = women = youth = children = tithe = offering = fr_amount = 0
        fr_type = "none"
        saturday_no = 0
        
        patterns = {
            'men': [r'men\s*[:\-]?\s*(\d+)', r'male\s*[:\-]?\s*(\d+)', r'brothers?\s*[:\-]?\s*(\d+)'],
            'women': [r'women\s*[:\-]?\s*(\d+)', r'female\s*[:\-]?\s*(\d+)', r'sisters?\s*[:\-]?\s*(\d+)'],
            'youth': [r'youth\s*[:\-]?\s*(\d+)', r'young\s*[:\-]?\s*(\d+)', r'teens?\s*[:\-]?\s*(\d+)'],
            'children': [r'children\s*[:\-]?\s*(\d+)', r'kids?\s*[:\-]?\s*(\d+)', r'child\s*[:\-]?\s*(\d+)'],
            'tithe': [r'tithe\s*[:\-]?\s*\$?([\d,]+\.?\d*)', r'tithe\s*[:\-]?\s*([\d,]+\.?\d*)'],
            'offering': [r'offering\s*[:\-]?\s*\$?([\d,]+\.?\d*)', r'offering\s*[:\-]?\s*([\d,]+\.?\d*)'],
            'fundraising': [r'fundraising?\s*[:\-]?\s*\$?([\d,]+\.?\d*)', r'fr[_\s]?amount\s*[:\-]?\s*\$?([\d,]+\.?\d*)'],
            'saturday': [r'saturday\s*[#№]?\s*(\d+)', r'sat[_\s]?no\s*[:\-]?\s*(\d+)', r'week\s*(\d+)']
        }
        
        for category, pattern_list in patterns.items():
            for pattern in pattern_list:
                match = re.search(pattern, extracted_text, re.IGNORECASE)
                if match:
                    value = match.group(1).replace(',', '')
                    if category in ['tithe', 'offering', 'fundraising']:
                        if category == 'tithe':
                            tithe = float(value)
                        elif category == 'offering':
                            offering = float(value)
                        elif category == 'fundraising':
                            fr_amount = float(value)
                            fr_type = "emergency"
                    elif category == 'saturday':
                        saturday_no = int(value)
                    else:
                        if category == 'men':
                            men = int(value)
                        elif category == 'women':
                            women = int(value)
                        elif category == 'youth':
                            youth = int(value)
                        elif category == 'children':
                            children = int(value)
                    break
        
        if men == 0 and women == 0 and youth == 0 and children == 0:
            all_numbers = re.findall(r'\b\d+\b', extracted_text)
            if len(all_numbers) >= 4:
                men = int(all_numbers[0])
                women = int(all_numbers[1])
                youth = int(all_numbers[2])
                children = int(all_numbers[3])
        
        scanned_data = {
            "men": men,
            "women": women,
            "youth": youth,
            "children": children,
            "saturday_no": saturday_no,
            "tithe": tithe,
            "offering": offering,
            "fr_amount": fr_amount,
            "fr_type": fr_type,
            "text_raw": extracted_text,
            "method_used": method_used,
            "ai_available": AI_AVAILABLE,
            "status": "success"
        }
        
        return jsonify(scanned_data)
        
    except Exception as e:
        app.logger.error(f"OCR processing error: {str(e)}")
        return jsonify({"status": "error", "message": f"OCR processing failed: {str(e)}"}), 500

# ============================================================================
# PORTAL MODULES
# ============================================================================

@app.route('/dashboard')
@login_required
def dashboard():
    mode = "TEST MODE 🧪" if session.get('test_mode') else "PRODUCTION MODE 📁"
    return render_template('dashboard.html', mode=mode)

@app.route('/data-entry')
@login_required
def data_entry():
    mode = "TEST MODE 🧪" if session.get('test_mode') else "PRODUCTION MODE 📁"
    return render_template('data_entry.html', mode=mode)

# --- ANALYSIS ROUTE ---
@app.route('/analysis', methods=['GET'])
@login_required
def analysis():
    year1 = request.args.get('year1', 2025, type=int)
    year2 = request.args.get('year2', 2026, type=int)
    
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    
    rows1 = conn.execute("SELECT * FROM saturday_records WHERE year = ? ORDER BY saturday_no ASC", (year1,)).fetchall()
    rows2 = conn.execute("SELECT * FROM saturday_records WHERE year = ? ORDER BY saturday_no ASC", (year2,)).fetchall()
    
    avg_emergency = conn.execute("SELECT AVG(reg_giving) FROM saturday_records WHERE fr_type = 'emergency'").fetchone()[0] or 0
    avg_planned = conn.execute("SELECT AVG(reg_giving) FROM saturday_records WHERE fr_type = 'planned'").fetchone()[0] or 0
    conn.close()

    analysis_text = "Regular giving remains stable."
    if avg_emergency > 0 and avg_planned > 0 and avg_emergency < avg_planned:
        analysis_text = "System Alert: Emergency fundraising is reducing regular giving trends."

    primary_rows = rows2 if rows2 else rows1
    processed_table = []
    chart_labels = []
    att_values = []
    cash_values = []

    for r in primary_rows:
        d = dict(r)
        m, w, y, c = (d.get('men') or 0), (d.get('women') or 0), (d.get('youth') or 0), (d.get('children') or 0)
        ti, of, fr = (d.get('tithe') or 0), (d.get('offering') or 0), (d.get('fr_amount') or 0)
        
        total_att = m + w + y + c
        total_cash = ti + of + fr
        
        d['total_attendance'] = total_att
        d['total_cash'] = total_cash
        processed_table.append(d)
        
        chart_labels.append(f"Sat {d.get('saturday_no')}")
        att_values.append(total_att)
        cash_values.append(total_cash)

    mode = "TEST MODE 🧪" if session.get('test_mode') else "PRODUCTION MODE 📁"
    
    return render_template('results.html',
                           analysis=analysis_text,
                           table_data=processed_table,
                           labels=chart_labels,
                           att_values=att_values,
                           cash_values=cash_values,
                           year1=year1, 
                           year2=year2,
                           mode=mode)

# --- PROJECTS MODULE ---
@app.route('/projects', methods=['GET', 'POST'])
@login_required
def manage_projects():
    db_path = get_db_path()
    
    if request.method == 'POST':
        name = request.form.get('p_name')
        desc = request.form.get('p_desc')
        
        if not name or not desc:
            flash("Project name and description are required")
            return redirect(url_for('manage_projects'))
        
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("INSERT INTO projects (name, description) VALUES (?, ?)", (name, desc))
            conn.commit()
            
            activity_logger.log_event(
                action="project_created",
                details={"name": name},
                status="success"
            )
            
            flash("Project created successfully!")
        except Exception as e:
            flash(f"Error creating project: {str(e)}")
        finally:
            conn.close()
        
        return redirect(url_for('dashboard'))
    
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    projects = conn.execute("SELECT * FROM projects ORDER BY created_at DESC").fetchall()
    conn.close()
    
    mode = "TEST MODE 🧪" if session.get('test_mode') else "PRODUCTION MODE 📁"
    return render_template('projects.html', projects=projects, mode=mode)

# --- EXPENSES MODULE ---
@app.route('/expenses', methods=['GET', 'POST'])
@login_required
def manage_expenses():
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    
    if request.method == 'POST':
        cat = request.form.get('category')
        amt = request.form.get('amount')
        
        if not cat or not amt:
            flash("Category and amount are required")
        else:
            try:
                conn.execute("INSERT INTO expenses (category, amount) VALUES (?, ?)", (cat, amt))
                conn.commit()
                flash("Expense added successfully!")
            except Exception as e:
                flash(f"Error adding expense: {str(e)}")
    
    expense_list = conn.execute("SELECT * FROM expenses ORDER BY expense_date DESC, created_at DESC").fetchall()
    conn.close()
    
    mode = "TEST MODE 🧪" if session.get('test_mode') else "PRODUCTION MODE 📁"
    return render_template('expenses.html', expenses=expense_list, edit_mode=True, mode=mode)

@app.route('/expenses/view')
@login_required
def view_expenses():
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    expense_list = conn.execute("SELECT * FROM expenses ORDER BY expense_date DESC, created_at DESC").fetchall()
    conn.close()
    
    mode = "TEST MODE 🧪" if session.get('test_mode') else "PRODUCTION MODE 📁"
    return render_template('expenses.html', expenses=expense_list, edit_mode=False, mode=mode)

@app.route('/update_expense', methods=['POST'])
@login_required
def update_expense():
    expense_id = request.form.get('expense_id')
    new_category = request.form.get('category')
    new_amount = request.form.get('amount')

    if not expense_id or not new_category or not new_amount:
        flash("All fields are required")
        return redirect(url_for('manage_expenses'))

    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    try:
        conn.execute('UPDATE expenses SET category = ?, amount = ? WHERE id = ?',
                     (new_category, new_amount, expense_id))
        conn.commit()
        
        activity_logger.log_expense_update(
            expense_id, 
            {"category": new_category, "amount": new_amount}
        )
        
        flash("Record updated successfully!")
    except Exception as e:
        conn.rollback()
        activity_logger.log_error(str(e), endpoint="update_expense")
        flash(f"Update failed: {str(e)}")
    finally:
        conn.close()
    
    return redirect(url_for('manage_expenses'))

# ============================================================================
# API ENDPOINTS
# ============================================================================

@app.route('/api/get_targets')
@login_required
def get_targets():
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute('SELECT * FROM settings WHERE id = 1').fetchone()
    conn.close()
    return jsonify(dict(row))

@app.route('/api/save_targets', methods=['POST'])
@login_required
def save_targets():
    data = request.json
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    try:
        conn.execute('UPDATE settings SET target_attendance = ?, target_offering = ? WHERE id = 1',
                     (data['target_attendance'], data['target_offering']))
        conn.commit()
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/get_data')
@login_required
def get_data():
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute('SELECT * FROM saturday_records ORDER BY saturday_no ASC').fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/submit_saturday', methods=['POST'])
@login_required
def submit_saturday():
    # Log the incoming request
    print("\n" + "="*50)
    print("📝 SAVE DATA REQUEST RECEIVED")
    print("="*50)
    
    try:
        data_list = request.json
        print(f"📦 Received data: {json.dumps(data_list, indent=2)}")
        
        if not data_list:
            print("❌ No data received")
            return jsonify({"status": "error", "message": "No data received"}), 400
        
        db_path = get_db_path()
        print(f"📁 Using database: {db_path}")
        
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Check database connection
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = cursor.fetchall()
        print(f"📊 Database tables: {tables}")
        
        successful_inserts = 0
        
        for i, d in enumerate(data_list):
            print(f"\n🔍 Processing record {i+1}: {d}")
            
            # Validate required fields
            required_fields = ['saturday_no', 'men', 'women', 'youth', 'children', 'tithe', 'offering']
            missing = [field for field in required_fields if field not in d]
            if missing:
                print(f"❌ Missing fields: {missing}")
                continue
            
            try:
                saturday_no = int(d['saturday_no'])
                men = int(d['men'] or 0)
                women = int(d['women'] or 0)
                youth = int(d['youth'] or 0)
                children = int(d['children'] or 0)
                sunday = int(d.get('sunday', 0) or 0)
                tithe = float(d['tithe'] or 0)
                offering = float(d['offering'] or 0)
                fr_amount = float(d.get('fr_amount', 0) or 0)
                fr_type = d.get('fr_type', 'none')
                year = 2026
                
                giving = tithe + offering
                
                print(f"✅ Processed values: sat={saturday_no}, men={men}, women={women}, youth={youth}, children={children}")
                print(f"💰 Financial: tithe={tithe}, offering={offering}, giving={giving}")
                
                # Check if record already exists
                cursor.execute("SELECT id FROM saturday_records WHERE saturday_no=? AND year=?", 
                             (saturday_no, year))
                existing = cursor.fetchone()
                
                if existing:
                    print(f"⚠️ Record for Saturday {saturday_no} already exists (ID: {existing[0]})")
                    # Update instead of insert
                    cursor.execute('''
                        UPDATE saturday_records SET
                        men=?, women=?, youth=?, children=?, sunday=?,
                        tithe=?, offering=?, fr_amount=?, fr_type=?, reg_giving=?
                        WHERE saturday_no=? AND year=?
                    ''', (men, women, youth, children, sunday, tithe, offering, 
                          fr_amount, fr_type, giving, saturday_no, year))
                    print(f"🔄 Updated existing record")
                else:
                    # Insert new record
                    cursor.execute('''
                        INSERT INTO saturday_records
                        (saturday_no, men, women, youth, children, sunday, tithe, offering, 
                         fr_amount, fr_type, reg_giving, year)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (saturday_no, men, women, youth, children, sunday, tithe, offering,
                          fr_amount, fr_type, giving, year))
                    print(f"✅ Inserted new record")
                
                successful_inserts += 1
                
            except Exception as e:
                print(f"❌ Error processing record {i+1}: {str(e)}")
                import traceback
                traceback.print_exc()
        
        conn.commit()
        conn.close()
        
        print(f"\n📊 SUMMARY: {successful_inserts} of {len(data_list)} records saved successfully")
        
        activity_logger.log_data_entry({
            "record_count": successful_inserts,
            "total_received": len(data_list)
        })
        
        return jsonify({
            "status": "success", 
            "message": f"Saved {successful_inserts} records",
            "saved": successful_inserts
        })
        
    except Exception as e:
        print(f"❌ CRITICAL ERROR: {str(e)}")
        import traceback
        traceback.print_exc()
        
        if 'conn' in locals():
            conn.rollback()
            conn.close()
        
        activity_logger.log_error(str(e), endpoint="submit_saturday")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/admin/logs')
@admin_required
def view_logs():
    user_filter = request.args.get('user', '')
    action_filter = request.args.get('action', '')
    date_from = request.args.get('from', '')
    date_to = request.args.get('to', '')
    page = int(request.args.get('page', 1))
    per_page = 50
    
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    
    query = "SELECT * FROM activity_logs WHERE 1=1"
    params = []
    
    if user_filter:
        query += " AND username LIKE ?"
        params.append(f"%{user_filter}%")
    
    if action_filter:
        query += " AND action = ?"
        params.append(action_filter)
    
    if date_from:
        query += " AND date(timestamp) >= ?"
        params.append(date_from)
    
    if date_to:
        query += " AND date(timestamp) <= ?"
        params.append(date_to)
    
    count_query = query.replace("SELECT *", "SELECT COUNT(*) as count")
    total = conn.execute(count_query, params).fetchone()['count']
    
    query += " ORDER BY timestamp DESC LIMIT ? OFFSET ?"
    params.extend([per_page, (page-1) * per_page])
    
    logs = conn.execute(query, params).fetchall()
    actions = conn.execute("SELECT DISTINCT action FROM activity_logs ORDER BY action").fetchall()
    
    conn.close()
    
    total_pages = (total + per_page - 1) // per_page
    
    mode = "TEST MODE 🧪" if session.get('test_mode') else "PRODUCTION MODE 📁"
    
    return render_template('admin_logs.html',
                          logs=logs,
                          actions=[a['action'] for a in actions],
                          total=total,
                          page=page,
                          total_pages=total_pages,
                          user_filter=user_filter,
                          action_filter=action_filter,
                          date_from=date_from,
                          date_to=date_to,
                          mode=mode)

@app.route('/reset-test-data')
@admin_required
def reset_test_data():
    """Admin function to reset test data"""
    if not session.get('test_mode'):
        flash("This function is only available in TEST MODE")
        return redirect(url_for('dashboard'))
    
    db_path = get_db_path()
    
    # Clear existing data
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM saturday_records")
    cursor.execute("DELETE FROM projects")
    cursor.execute("DELETE FROM expenses")
    conn.commit()
    conn.close()
    
    # Generate fresh test data
    generate_test_data(db_path)
    
    flash("✅ Test data has been reset with fresh samples!")
    return redirect(url_for('dashboard'))

# ============================================================================
# MAIN
# ============================================================================

if __name__ == '__main__':
    # Initialize both databases
    init_all_databases()
    
    is_exe = getattr(sys, 'frozen', False)
    
    print("\n" + "="*60)
    print("🚀 CHURCH MANAGEMENT SYSTEM STARTING...")
    print("="*60)
    print(f"📁 Production DB: {PROD_DB_PATH}")
    print(f"🧪 Test DB: {TEST_DB_PATH}")
    print(f"📱 Local URL: http://127.0.0.1:5000")
    print(f"🌐 Network URL: http://{get_local_ip()}:5000")
    print(f"📋 Press CTRL+C to stop the server")
    print("="*60 + "\n")
    
    if not is_exe and app.debug:
        Timer(1.5, open_browser).start()
    
    app.run(debug=not is_exe, host='0.0.0.0', port=5000)