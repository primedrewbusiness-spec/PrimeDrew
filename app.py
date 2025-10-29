from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash
from flask_sqlalchemy import SQLAlchemy
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy.orm import joinedload, backref
from functools import wraps
import firebase_admin
from firebase_admin import credentials, auth
import os
import json
import secrets
from datetime import datetime, timedelta
import math
import razorpay
import decimal 
from twilio.rest import Client
import logging
import random
import requests
from dotenv import load_dotenv  # <<< CRITICAL NEW IMPORT

# Load environment variables from .env file (must be at the top)
load_dotenv()  # <<< CRITICAL NEW FUNCTION CALL

# === CUSTOM JINJA FILTER ===
def datetime_format(value, format_string):
    """Converts a date string (e.g., YYYY-MM-DDTHH:MM or YYYY-MM-DD HH:MM) to a datetime object."""
    if not value: return None
    
    # Handle ISO format from HTML datetime-local (YYYY-MM-DDTHH:MM)
    if 'T' in value:
        value = value.replace('T', ' ')
        format_string = '%Y-%m-%d %H:%M'
        
    try:
        return datetime.strptime(value, format_string)
    except ValueError as e:
        try:
            return datetime.strptime(value, '%Y-%m-%d %H:%M:%S')
        except ValueError:
            print(f"Error parsing date/time '{value}' with format '{format_string}'. Error: {e}")
            return None


# NEW FILTER: To safely extract the date part in templates
def split_date(value):
    """Splits a date-time string (YYYY-MM-DD HH:MM) and returns only the date part."""
    if value and ' ' in value:
        return value.split(' ')[0]
    return value
    
# === CONFIGURATION ===
app = Flask(__name__)

# ---------------------------------------------------------------------
# CRITICAL CHANGE: PostgreSQL Configuration using Environment Variables
# ---------------------------------------------------------------------

DATABASE_URL = os.environ.get('DATABASE_URL', None)

if DATABASE_URL:
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
        
    app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
    print("Using PostgreSQL from Environment Variable.")
else:
    # WARNING: To apply schema changes (like removing columns),  
    # DELETE the 'project_data.db' file before running the app again.
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(app.root_path, 'project_data.db') 
    print("WARNING: DATABASE_URL not found. Using SQLite for local development.")
    
app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET_KEY', secrets.token_hex(32))
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# File Upload Configuration
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp', 'pdf'} # Added PDF for documents
UPLOAD_FOLDER = os.path.join(app.root_path, 'static', 'uploads')
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# === RAZORPAY CONFIGURATION (UPDATED WITH PLACEHOLDERS) ===
app.config['RZP_KEY_ID'] = os.environ.get('RZP_KEY_ID', 'rzp_test_DUMMYID')
app.config['RZP_KEY_SECRET'] = os.environ.get('RZP_KEY_SECRET', 'DUMMYSECRET')
app.config['RZP_CLIENT'] = razorpay.Client(auth=(app.config['RZP_KEY_ID'], app.config['RZP_KEY_SECRET']))

# === TWILIO CONFIGURATION (UPDATED WITH PLACEHOLDERS) ===
app.config['TWILIO_ACCOUNT_SID'] = os.environ.get('TWILIO_ACCOUNT_SID', 'YOUR_TWILIO_ACCOUNT_SID')
app.config['TWILIO_AUTH_TOKEN'] = os.environ.get('TWILIO_AUTH_TOKEN', 'YOUR_TWILIO_AUTH_TOKEN')
app.config['TWILIO_PHONE_NUMBER'] = os.environ.get('TWILIO_PHONE_NUMBER', '+15005550006') # Standard Twilio test number as placeholder
# ==================================

# === GOOGLE MAPS CONFIGURATION (UPDATED WITH PLACEHOLDERS) ===
# NOTE: This key is used for the server-side Geocoding API call
app.config['GOOGLE_MAPS_API_KEY'] = os.environ.get('GOOGLE_MAPS_API_KEY', 'AIzaSyDUMMYKEY')
# ============================================

# NEW: Sub-city data structure
SUB_CITY_MAP = {
    'Pune': ['Akurdi', 'Pimpri Chinchwad', 'Hinjewadi', 'Shivaji Nagar', 'Pune City', 'Kothrud', 'Wakad'],
    'Mumbai': ['Andheri', 'Bandra', 'Dadar', 'Juhu', 'Thane'],
    'Bengaluru': ['Koramangala', 'Indiranagar', 'Whitefield', 'HSR Layout'],
    'Hyderabad': ['Banjara Hills', 'Gachibowli', 'HITECH City'],
    'Delhi': ['Connaught Place', 'Karol Bagh', 'Saket', 'Gurgaon', 'Noida']
}

# --- NEW: APPROXIMATE GEOLOCATION DATA (Used as a fallback only) ---
CITY_GEOLOCATION = {
    'Pune': {'lat': 18.5204, 'lng': 73.8567},
    'Mumbai': {'lat': 19.0760, 'lng': 72.8777},
    'Bengaluru': {'lat': 12.9716, 'lng': 77.5946},
    'Hyderabad': {'lat': 17.3850, 'lng': 78.4867},
    'Delhi': {'lat': 28.7041, 'lng': 77.1025}
}
# ----------------------------------------

def allowed_file(filename):
    if not filename: return False
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

db = SQLAlchemy(app)
# Registering the custom filter
app.jinja_env.filters['to_datetime'] = datetime_format
app.jinja_env.filters['split_date'] = split_date

# Firebase Admin SDK Initialization (kept for completeness)
FIREBASE_PROJECT_ID = 'vehicle-rent-50cc6'
try:
    # serviceAccountKey.json is in .gitignore, so it will only load locally
    if os.path.exists("serviceAccountKey.json"):
        cred = credentials.Certificate("serviceAccountKey.json")
        firebase_admin.initialize_app(cred, {'projectId': FIREBASE_PROJECT_ID})
        print("Firebase Admin SDK initialized successfully.")
    else:
        print("WARNING: serviceAccountKey.json not found. Firebase Admin SDK skipped.")
except Exception as e:
    print(f"WARNING: Firebase Admin SDK failed to initialize. Error: {e}")

# ==================================================
# === DATABASE MODELS ===
# ==================================================

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    firebase_uid = db.Column(db.String(128), unique=True, nullable=False)
    phone = db.Column(db.String(15), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(128), nullable=False)
    first_name = db.Column(db.String(80), nullable=False)
    last_name = db.Column(db.String(80), nullable=False)
    dob = db.Column(db.String(10), nullable=False)
    role = db.Column(db.String(50), nullable=False)
    address1 = db.Column(db.String(150), nullable=False)
    address2 = db.Column(db.String(150), nullable=True)
    city = db.Column(db.String(50), nullable=False)
    state = db.Column(db.String(50), nullable=False)
    pincode = db.Column(db.String(10), nullable=False)
    identity_doc = db.Column(db.String(50), nullable=False)
    dl_number = db.Column(db.String(50), nullable=False)
    dl_expiry = db.Column(db.String(10), nullable=False)
    experience = db.Column(db.Integer, default=0)
    terms_agreed = db.Column(db.Boolean, nullable=False, default=False)
    # NEW: Host Approval Status
    is_approved_host = db.Column(db.Boolean, nullable=False, default=False)
    # NEW: Account Active/Blocked Status (for User/Host blocking)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    # NEW: KYC File Path
    kyc_file_path = db.Column(db.String(255), nullable=True)
    # NEW: Commission Tier (Stores '80' or '70' as Integer)
    commission_tier = db.Column(db.Integer, default=70) # Default to 70% payout
    
    vehicles = db.relationship('Vehicle', backref='host', lazy=True)
    bookings = db.relationship('Booking', backref='customer', lazy=True)
    reviews = db.relationship('Review', backref='reviewer', lazy=True) # NEW Review relationship

    def __repr__(self): return f'<User {self.phone}>'

class Vehicle(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    host_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    vehicle_id_code = db.Column(db.String(100), unique=True, nullable=False)
    name = db.Column(db.String(100), nullable=False)
    brand = db.Column(db.String(50), nullable=False)
    type = db.Column(db.String(20), nullable=False)
    fuel = db.Column(db.String(20), nullable=False)
    gear = db.Column(db.String(20), nullable=False)
    city = db.Column(db.String(50), nullable=False)
    sub_city = db.Column(db.String(100), nullable=True)
    # --- NEW: GEOLOCATION FIELDS (NOW PRECISE) ---
    latitude = db.Column(db.Float, nullable=True) 
    longitude = db.Column(db.Float, nullable=True)
    # ---------------------------------------------
    base_price = db.Column(db.Float, nullable=False)
    rating = db.Column(db.Float, default=4.0)
    image_url = db.Column(db.String(255), nullable=False)
    kms_per_unit = db.Column(db.Integer, default=50)
    features = db.Column(db.String(255), default="")
    # NEW: SPECIFICATION FIELD
    specification = db.Column(db.String(255), nullable=True)
    is_available = db.Column(db.Boolean, nullable=False, default=True)
    
    bookings = db.relationship(
        'Booking', 
        backref='vehicle_info', 
        lazy='select', 
        order_by='Booking.start_date.desc()' 
    )
    reviews = db.relationship('Review', backref='vehicle', lazy=True) # NEW Review relationship

    def to_dict(self, booked_dates=[]): 
        return {
            'id': self.vehicle_id_code,
            'db_id': self.id, 
            'name': self.name,
            'brand': self.brand,
            'type': self.type,
            'fuel': self.fuel,
            'gear': self.gear,
            'city': self.city,
            'sub_city': self.sub_city,
            # --- NEW EXPORT (PRECISE) ---
            'lat': self.latitude,
            'lng': self.longitude,
            # ----------------------------
            'base': self.base_price,
            'rating': self.rating,
            'img': self.image_url,
            'features': self.features.split(',') if self.features else [],
            'kms': self.kms_per_unit,
            # NEW: EXPORT SPECIFICATION
            'specification': self.specification,
            'booked': booked_dates
        }

    def __repr__(self): return f'<Vehicle {self.name} in {self.city}>'


class Booking(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    vehicle_id = db.Column(db.Integer, db.ForeignKey('vehicle.id'), nullable=False) 
    start_date = db.Column(db.DateTime, nullable=False) 
    end_date = db.Column(db.DateTime, nullable=False) 
    total_price = db.Column(db.Float, nullable=False)
    # --- NEW DEPOSIT FIELDS ---
    deposit_amount = db.Column(db.Float, default=0.0)
    deposit_refund_status = db.Column(db.String(20), default='Pending') # Pending, Processed, Denied
    # --------------------------
    status = db.Column(db.String(20), default='Confirmed') 
    booked_at = db.Column(db.DateTime, default=datetime.utcnow)
    payment_id = db.Column(db.String(100), nullable=True) 
    refund_status = db.Column(db.String(20), default='NotApplicable') # NotApplicable, Pending, Processed

    def __repr__(self): return f'<Booking {self.id} for Vehicle {self.vehicle_id}>'

# --- NEW REVIEW MODEL ---
class Review(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    booking_id = db.Column(db.Integer, db.ForeignKey('booking.id'), unique=True, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False) # The rider/customer
    vehicle_id = db.Column(db.Integer, db.ForeignKey('vehicle.id'), nullable=False)
    rating = db.Column(db.Float, nullable=False)
    comment = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Relationship back to Booking
    booking = db.relationship('Booking', backref=backref('review', uselist=False))

    def __repr__(self):
        return f'<Review {self.id} for Vehicle {self.vehicle_id}>'
# ------------------------

# --- NEW COMPLAINT MODEL (FOR CONTACT FORM) ---
class Complaint(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), nullable=False)
    subject = db.Column(db.String(100), nullable=False)
    message = db.Column(db.Text, nullable=False)
    submitted_at = db.Column(db.DateTime, default=datetime.utcnow)
    status = db.Column(db.String(20), nullable=False, default='New') # Status: New, In Progress, Resolved

    def __repr__(self):
        return f'<Complaint {self.id} from {self.email}>'
# ---------------------------------------------

# ==================================
# === AUTH & UTILITY FUNCTIONS ===
# ==================================

# --- NEW: GOOGLE MAPS GEOCODING HELPER (CRITICAL FOR ACCURATE MAP MARKERS) ---
def get_precise_lat_lng(address_line_1, address_line_2, city, state, pincode, api_key):
    """
    Uses Google Geocoding API to get precise latitude and longitude from a full address.
    If Geocoding fails, falls back to a city-center estimate.
    """
    # 1. Full address string for best accuracy (Ensuring country is specified for India)
    full_address = f"{address_line_1}, {address_line_2 or ''}, {city}, {state}, India, {pincode}"
    
    # 2. Base API URL
    GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"

    # 3. Parameters for the API request
    params = {
        'address': full_address,
        'key': api_key
    }
    
    # Get fallback location first, in case of failure
    base_loc = CITY_GEOLOCATION.get(city, {'lat': 20.5937, 'lng': 78.9629})

    try:
        # 4. Make the request using the requests library
        response = requests.get(GEOCODE_URL, params=params)
        response.raise_for_status() # Raises an HTTPError for bad responses (4xx or 5xx)
        
        data = response.json()
        
        # 5. Process the JSON response
        if data['status'] == 'OK' and data['results']:
            location = data['results'][0]['geometry']['location']
            return {
                'lat': round(location['lat'], 6), # Store with higher precision
                'lng': round(location['lng'], 6) # Store with higher precision
            }
        else:
            # Fallback to city-center if precise address fails
            print(f"⚠️ Geocoding failed for address: {full_address}. Status: {data.get('status', 'No Status')}. Falling back to city center estimate.")
            return base_loc

    except requests.exceptions.RequestException as e:
        print(f"❌ Geocoding API Request Error: {e}. Falling back to city center estimate.")
        # Fallback in case of network error or API failure
        return base_loc
# ----------------------------------------------------------------------


# --- NEW: TWILIO SMS SENDING FUNCTION (REUSED) ---
def send_approval_sms(recipient_phone_number, host_name):
    """Sends an SMS notification to the host upon approval using Twilio."""
    
    account_sid = app.config.get('TWILIO_ACCOUNT_SID')
    auth_token = app.config.get('TWILIO_AUTH_TOKEN')
    twilio_number = app.config.get('TWILIO_PHONE_NUMBER')

    # Note: The comparison is now against the DUMMY value set in config if no real value is in .env
    if account_sid == 'YOUR_TWILIO_ACCOUNT_SID' or not all([account_sid, auth_token, twilio_number]):
        print("❌ WARNING: Twilio credentials not configured. SMS not sent.")
        return False
        
    # Twilio के लिए फोन नंबर फॉर्मेटिंग (यह सुनिश्चित करें कि यह +91 या +1 से शुरू हो)
    if not recipient_phone_number.startswith('+'):
        # भारत के नंबर मानकर +91 जोड़ना (अपने क्षेत्र के अनुसार बदलें)
        recipient_phone_number = f'+91{recipient_phone_number.strip()}' 
        
    message_body = (
        f"🎉 Congrats, {host_name}! Your PrimeDrew Host application is APPROVED! "
        "Log in now to list your vehicles. Happy hosting!"
    )
    
    try:
        client = Client(account_sid, auth_token)
        message = client.messages.create(
            to=recipient_phone_number,
            from_=twilio_number,
            body=message_body
        )
        print(f"✅ SMS sent successfully to {recipient_phone_number}. SID: {message.sid}")
    except Exception as e:
        print(f"❌ SMS Failed to send to {recipient_phone_number}. Error: {e}")
        return False
    return True
# ------------------------------------------------------------------------


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            session['next_url'] = request.url
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def host_required(f):
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        user_id = session.get('user_id')
        user = User.query.get(user_id) # Fetch user status from DB

        # --- BLOCK CHECK FOR HOSTS (NEW) ---
        if user and not user.is_active:
            flash("❌ Your account has been temporarily blocked by the Administrator. Please contact support.", 'error')
            session.clear()
            return redirect(url_for('login'))
        # -----------------------------------
        
        # Check if user is host AND if the host is approved
        if user and (user.role != 'host' or not user.is_approved_host): # <--- UPDATED CHECK
            flash("❌ Unauthorized: You must be an **approved host** to access this page. Please wait for Admin approval.", 'error')
            
            # Redirect unapproved hosts to their regular dashboard
            if user.role == 'host':
                                    return redirect(url_for('dashboard'))
            return redirect(url_for('dashboard'))
            
        if not user:
                                    # Safety check, should be covered by login_required
            flash("❌ User session invalid. Please log in again.", 'error')
            session.clear()
            return redirect(url_for('login'))
            
        return f(*args, **kwargs)
    return decorated_function

# Admin Access Decorator (Enhanced Security Check)
def admin_required(f):
    """Restricts access to super_admin role and verifies user existence."""
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        user_id = session.get('user_id')
        user_role = session.get('user_role')
        
        if user_role != 'super_admin':
            flash("❌ Unauthorized: You must be a Super Admin to access this page.", 'error')
            if user_role == 'host':
                return redirect(url_for('host_dashboard'))
            return redirect(url_for('dashboard'))
        
        # Verify the user exists in DB and still holds the role
        if user_id is not None and user_id > 0:
            user = User.query.get(user_id)
            if not user or user.role != 'super_admin':
                session.clear()
                flash("❌ Your administrative access has been revoked or user was deleted. Please log in again.", 'error')
                return redirect(url_for('login'))
                
        return f(*args, **kwargs)
    return decorated_function


# --- MODIFIED: Dynamic Deposit Calculation Function ---
def calculate_deposit(subtotal, total_hours):
    """Calculates the dynamic refundable deposit based on duration."""
    
    # Tier 1: Short Rentals (< 24 hours)
    if total_hours < 24:
        return 500
    
    # Tier 2: Mid-Rentals (24 hours to 72 hours)
    elif total_hours >= 24 and total_hours < 72:
        return 1500

    # Tier 3: Long Rentals (72 hours and above)
    elif total_hours >= 72:
        # Base deposit + 10% of subtotal, capped at max 5000
        deposit_calc = 2000 + (subtotal * 0.10)
        # Round to nearest 100, max 5000
        return min(round(deposit_calc / 100) * 100, 5000) 

    return 500 # Default fallback
# -----------------------------------------------


# --- MODIFIED: Long-Term Rental Pricing Logic (Discount) ---
def price_for_server(base_price_per_hour, fuel_type, start_date_str_iso, end_date_str_iso):
    DATE_FORMAT = '%Y-%m-%d %H:%M:%S'
    
    if not start_date_str_iso or not end_date_str_iso:
        return 0 

    try:
        s = datetime.strptime(start_date_str_iso, DATE_FORMAT)
        e = datetime.strptime(end_date_str_iso, DATE_FORMAT)
    except ValueError as ve:
        print(f"Date Parsing Error in price_for_server: {ve}")
        return 0 

    time_difference = e - s
    total_hours = time_difference.total_seconds() / 3600
    
    if total_hours <= 0: 
        return 0 
    
    billed_hours = total_hours
    if total_hours < 24:
        billed_hours = math.ceil(total_hours)
    
    subtotal = billed_hours * base_price_per_hour
    
    # Apply Fuel Surcharge/Discount
    if fuel_type == 'Electric': 
        subtotal *= 0.95
    elif fuel_type == 'Diesel':
        subtotal *= 1.05
    
    # --- MODIFIED LONG-TERM DISCOUNT LOGIC ---
    if total_hours >= 48 and total_hours < 96:
        subtotal *= 0.95 # 5% discount
    elif total_hours >= 96: 
        subtotal *= 0.85 # 15% discount 
    # -----------------------------------------
        
    return round(subtotal)
# -----------------------------------------------------------


def is_vehicle_available(vehicle_db_id, start_date_str_iso, end_date_str_iso):
    DATE_FORMAT_IN = '%Y-%m-%d %H:%M:%S'
    try:
        start_dt = datetime.strptime(start_date_str_iso, DATE_FORMAT_IN)
        end_dt = datetime.strptime(end_date_str_iso, DATE_FORMAT_IN)
    except ValueError:
        return False

    overlaps = Booking.query.filter(
        Booking.vehicle_id == vehicle_db_id,
        Booking.status == 'Confirmed',
        Booking.start_date < end_dt, 
        Booking.end_date > start_dt
    ).first()
    return overlaps is None 

# --- NEW: Review Eligibility Check ---
def is_booking_reviewable(booking):
    """Checks if a booking is confirmed, past its end date, and has no existing review."""
    if booking.status != 'Confirmed':
        return False
        
    # Check if the end date is in the past
    if booking.end_date > datetime.utcnow():
        return False

    # Check if a review already exists for this booking
    existing_review = Review.query.filter_by(booking_id=booking.id).first()
    if existing_review:
        return False

    return True
# -------------------------------------

# ==================================
# === ROUTES (Authentication) ===
# ==================================

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        # Now accepts EITHER phone or email as login_id
        login_id = request.form.get('login_id') 
        password = request.form.get('password')
        
        user = None
        
        # 1. Try finding user by phone number (if input looks like digits)
        if login_id and login_id.isdigit() and len(login_id) >= 10:
            user = User.query.filter_by(phone=login_id).first()
            
        # 2. If not found or if it looks like an email, try email
        if not user and login_id and '@' in login_id:
            user = User.query.filter_by(email=login_id).first()
        
        
        # --- GENERAL USER/ADMIN LOGIN CHECK (Priority 1) ---
        if user:
            
            # --- NEW: ACTIVE STATUS CHECK (APPLIES TO ALL ROLES) ---
            if not user.is_active:
                flash("❌ Your account has been temporarily blocked by the Administrator. Please contact support.", 'error')
                return render_template('login.html', error="Your account has been blocked. Please contact support.")
            # --------------------------------------------------------

            # Check if the user is a super_admin or a regular user/host
            if check_password_hash(user.password, password):
                session['user_id'] = user.id
                session['logged_in'] = True
                # Set user name based on role
                session['user_name'] = user.first_name if user.role != 'super_admin' else 'Super Admin' 
                session['user_role'] = user.role
                
                # --- START OF REQUIRED CHANGE FOR PROFILE POPUP ---
                session['user_email'] = user.email # <--- NEW: Email stored in session
                session['user_phone'] = user.phone # <--- NEW: Phone stored in session
                # --- END OF REQUIRED CHANGE FOR PROFILE POPUP ---
                
                # Check for unapproved host status at login
                if user.role == 'host' and not user.is_approved_host:
                    flash("⚠️ Host registration successful, but you must be approved by the Admin before listing vehicles.", 'warning')
                
                flash(f"🎉 Login Successful! Welcome, {session['user_name']}.", 'success')
                
                next_url = session.pop('next_url', url_for('index'))
                
                if user.role == 'super_admin':
                    return redirect(url_for('admin_dashboard'))
                # Hosts go to their specific dashboard
                if user.role == 'host':
                    return redirect(url_for('host_dashboard'))
                
                return redirect(next_url)
            else:
                # Password check failed
                return render_template('login.html', error="Invalid login ID or password. Please try again.")
        
        # If no user found in DB (by phone or email)
        return render_template('login.html', error="Invalid login ID or password. Please try again.")
            
    return render_template('login.html', error=None) 

# NEW ROUTE: Checks if phone number exists before OTP process
@app.route('/api/check-phone-exists', methods=['POST'])
def check_phone_exists():
    """Checks if a phone number is already in the database."""
    data = request.get_json()
    phone = data.get('phone')
    
    if not phone:
        return jsonify({'exists': False, 'message': 'Phone number not provided.'}), 400

    user_by_phone = User.query.filter_by(phone=phone).first()
    if user_by_phone:
        return jsonify({'exists': True, 'message': 'This phone number is already registered. Please log in.'})

    return jsonify({'exists': False})

@app.route('/register', methods=['POST'])
def register():
    if request.method == 'POST':
        firebase_uid = request.form.get('firebase_uid')
        phone = request.form.get('phone')
        email = request.form.get('email')
        password = request.form.get('password')
        role = request.form.get('role')

        # --- File Upload Handling (Now mandatory for all roles) ---
        kyc_file_path = None
        
        if 'kyc_document' not in request.files or not request.files['kyc_document'].filename:
                return render_template('registration_form.html', error="Driving Licence upload is mandatory for registration."), 400

        file = request.files['kyc_document']
        if file and allowed_file(file.filename):
            try:
                filename = secure_filename(file.filename)
                unique_filename = f"kyc-{phone}-{datetime.now().strftime('%Y%m%d%H%M%S')}-{filename}"
                save_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
                file.save(save_path)
                kyc_file_path = url_for('static', filename=f'uploads/{unique_filename}')
            except Exception as e:
                print(f"KYC File Upload Error: {e}")
                return render_template('registration_form.html', error="Error uploading Driving Licence. Please try again."), 400
        else:
                return render_template('registration_form.html', error="Invalid file type for Driving Licence. Only images and PDFs are allowed."), 400
        # ----------------------------

        if not firebase_uid or not phone or not email:
            return render_template('registration_form.html', error="Error: Missing essential data."), 400

        if User.query.filter_by(phone=phone).first() or User.query.filter_by(email=email).first():
            return render_template('registration_form.html', error="Error: Mobile number or email is already registered."), 409

        hashed_password = generate_password_hash(password) 
        
        is_approved = True if role == 'renter' else False 
        is_active_default = True

        try:
            new_user = User(
                firebase_uid=firebase_uid, phone=phone, email=email, 
                password=hashed_password, 
                first_name=request.form.get('firstName'), last_name=request.form.get('lastName'), dob=request.form.get('dob'), role=role,
                address1=request.form.get('address1'), address2=request.form.get('address2'), city=request.form.get('city'), state=request.form.get('state'), pincode=request.form.get('pincode'), 
                identity_doc='dl', # Hardcoded as Driving Licence is the only option
                dl_number=request.form.get('dlNumber'), dl_expiry=request.form.get('dlExpiry'), 
                experience=request.form.get('experience', 0),
                terms_agreed='terms' in request.form,
                is_approved_host=is_approved, 
                is_active=is_active_default,
                kyc_file_path=kyc_file_path
            )
            db.session.add(new_user)
            db.session.commit()
            
            flash("✅ Registration Successful! Please log in.", 'success')
            return redirect(url_for('login'))

        except Exception as e:
            db.session.rollback()
            print(f"Database Save Error: {e}")
            return render_template('registration_form.html', error="An unexpected error occurred while saving user data. Try again."), 500

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    session.pop('user_name', None)
    session.pop('user_id', None)
    session.pop('user_role', None)
    session.pop('user_email', None) # Clear new session variables
    session.pop('user_phone', None) # Clear new session variables
    flash("👋 You have been logged out.", 'info') 
    return redirect(url_for('index'))


# ==================================================
# === ROUTES (FORGOT PASSWORD/FIREBASE OTP) ===
# ==================================================

@app.route('/forgot-password', methods=['GET'])
def forgot_password_page():
    """Displays the page where the Firebase OTP reset process begins."""
    return render_template('forgot_password_firebase.html', error=None)

@app.route('/api/reset-password-firebase', methods=['POST'])
def reset_password_via_firebase():
    """
    Called by the frontend after successful Firebase phone verification to update the password in the DB.
    """
    data = request.get_json()
    phone = data.get('phone')
    new_password = data.get('new_password')
    
    if not phone or not new_password:
        return jsonify({'success': False, 'message': 'Phone number or new password is missing.'}), 400

    # Find the user by phone number
    user = User.query.filter_by(phone=phone).first()

    if not user:
        return jsonify({'success': False, 'message': 'User not found in PrimeDrew records.'}), 404

    try:
        # 1. Hash the new password securely
        hashed_password = generate_password_hash(new_password)
        
        # 2. Update the password in the database
        user.password = hashed_password
        db.session.commit()
        
        return jsonify({'success': True, 'message': 'Password successfully reset. You can now log in.'}), 200

    except Exception as e:
        db.session.rollback()
        print(f"Password Reset Error: {e}")
        return jsonify({'success': False, 'message': 'Database update failed due to a server error.'}), 500


# ==================================
# === ROUTES (Front-end Pages) ===
# ==================================

@app.route('/')
def index():
    return render_template('index.html') 

@app.route('/about')
def about_page():
    return render_template('about.html')

@app.route('/contact')
def contact_page():
    """Renders the contact page template."""
    return render_template('contact_page.html')

# --- NEW ROUTES FOR LEGAL PAGES ---
@app.route('/privacy-terms')
def privacy_terms_page():
    """Renders the page covering Privacy Policy and Terms & Conditions."""
    return render_template('privacy_terms.html')

@app.route('/faq')
def faq_page():
    """Renders the help center and frequently asked questions page."""
    return render_template('faq.html')
# ----------------------------------

# --- NEW ROUTE: Handle Contact Form Submission ---
@app.route('/submit-contact-form', methods=['POST'])
def submit_contact_form():
    if request.method == 'POST':
        try:
            name = request.form.get('name')
            email = request.form.get('email')
            subject = request.form.get('subject')
            message = request.form.get('message')

            if not all([name, email, subject, message]):
                flash('❌ Please fill out all fields in the contact form.', 'error')
                return redirect(url_for('contact_page'))

            # Correctly indented Complaint object creation
            new_complaint = Complaint(
                name=name,
                email=email,
                subject=subject,
                message=message,
                status='New'
            )
            
            db.session.add(new_complaint)
            db.session.commit()
            
            flash('✅ Thank you! Your message has been sent. We will get back to you shortly.', 'success')
            return redirect(url_for('contact_page'))
            
        except Exception as e:
            db.session.rollback()
            print(f"Contact Form Submission Error: {e}")
            flash('❌ An unexpected error occurred. Please try again.', 'error')
            return redirect(url_for('contact_page'))
# -----------------------------------------------

# --- FIX IMPLEMENTED HERE ---
@app.route('/register_page', methods=['GET'])
def register_page():
    # --- FIX: Redirect logged-in users to their appropriate dashboard ---
    if session.get('logged_in'):
        user_role = session.get('user_role')
        if user_role == 'super_admin':
            return redirect(url_for('admin_dashboard'))
        elif user_role == 'host':
            # Note: The host_required decorator handles the unapproved host case
            return redirect(url_for('host_dashboard')) 
        else: # Regular renter/customer
            return redirect(url_for('my_bookings')) # Redirect customer to a useful page
    # ------------------------------------------------------------------

    role = request.args.get('role', 'customer')
    return render_template('registration_form.html', error=None, role=role) 
# ----------------------------

@app.route('/dashboard') 
@login_required
def dashboard():
    user_role = session.get('user_role', 'rider')
    
    if user_role == 'super_admin':
        # Fix: Ensure admin always redirects to the admin dashboard endpoint
        return redirect(url_for('admin_dashboard')) 
        
    user_id = session.get('user_id')
    user = User.query.get(user_id)
    
    if user_role == 'host':
        # Host: Show status if unapproved, then redirect to host_dashboard (if approved)
        if not user.is_approved_host:
            # Display a dedicated waiting page for unapproved hosts (or a restricted dashboard)
            return render_template('host_waiting_approval.html', user=user) 
        return redirect(url_for('host_dashboard'))
    
    # Customer Dashboard
    user_name = session.get('user_name', 'User')
    return f"<h1>Dashboard</h1><p>Hello, {user_name}. You are logged in as a {user_role}.</p><p><a href='{url_for('search_page')}'>Find a Vehicle</a> | <a href='/my-bookings'>View My Bookings</a> | <a href='/logout'>Logout</a></p>"
    
@app.route('/my-bookings')
@login_required
def my_bookings():
    user_id = session.get('user_id')
    
    bookings = Booking.query.options(
        joinedload(Booking.vehicle_info).joinedload(Vehicle.host)
    ).filter_by(user_id=user_id).order_by(Booking.start_date.desc()).all()
    
    booking_history = []
    for booking in bookings:
        vehicle = booking.vehicle_info
        host_name = vehicle.host.first_name if vehicle and vehicle.host else "N/A"
        
        # --- NEW REVIEW/REFUND LOGIC ---
        reviewable = is_booking_reviewable(booking)
        # ------------------------

        booking_history.append({
            'booking_id': booking.id,
            'vehicle_name': vehicle.name if vehicle else "N/A",
            'vehicle_image_url': vehicle.image_url if vehicle else url_for('static', filename='images/default_vehicle.png'),
            'host_name': host_name,
            'start_date': booking.start_date.strftime('%Y-%m-%d %H:%M'), 
            'end_date': booking.end_date.strftime('%Y-%m-%d %H:%M'),
            'total_price': booking.total_price,
            'deposit_amount': booking.deposit_amount, # NEW
            'deposit_refund_status': booking.deposit_refund_status, # NEW
            'status': booking.status,
            'refund_status': booking.refund_status, # Existing for booking refund
            'booked_at': booking.booked_at.strftime('%Y-%m-%d %H:%M'),
            'reviewable': reviewable, # NEW FIELD
            'vehicle_id_code': vehicle.vehicle_id_code if vehicle else None, 
            'vehicle_db_id': vehicle.id if vehicle else None,
        })
        
    return render_template('my_bookings.html', bookings=booking_history)

# --- NEW ROUTE FOR RECEIPT DOWNLOAD ---
@app.route('/receipt/<int:booking_id>')
@login_required
def download_receipt(booking_id):
    """Generates a detailed receipt for a specific booking ID."""
    user_id = session.get('user_id')

    # Fetch the booking, ensuring it belongs to the logged-in user
    booking = Booking.query.options(
        joinedload(Booking.vehicle_info).joinedload(Vehicle.host)
    ).filter_by(id=booking_id, user_id=user_id).first()

    if not booking:
        flash("❌ Error: Booking receipt not found or unauthorized.", 'error')
        return redirect(url_for('my_bookings'))

    vehicle = booking.vehicle_info
    host = vehicle.host
    customer = booking.customer # Assuming customer details are needed

    # Calculate duration details for the receipt
    time_difference = booking.end_date - booking.start_date
    total_hours = time_difference.total_seconds() / 3600
    billed_hours = math.ceil(total_hours) if total_hours < 24 else total_hours
    
    # Calculate components (for display verification only, total_price is final)
    subtotal = booking.total_price - booking.deposit_amount
    gst = round(subtotal * 0.18)
    subtotal_base = subtotal - gst # Approximate pre-tax subtotal
    
    # Check if this booking was a cancellation (to adjust receipt title)
    is_cancellation_refund = booking.status == 'Cancelled' and booking.refund_status != 'NotApplicable'

    context = {
        'booking': booking,
        'vehicle': vehicle,
        'host': host,
        'customer': customer,
        'billed_hours': round(billed_hours, 1),
        'duration_str': f"{billed_hours:.1f} Hours",
        'subtotal_base': round(subtotal_base), 
        'gst': gst,
        'deposit': round(booking.deposit_amount),
        'final_price': round(booking.total_price),
        'is_cancellation_refund': is_cancellation_refund,
    }

    # Render a dedicated receipt template
    # NOTE: You need to create this file: templates/receipt_template.html
    return render_template('receipt_template.html', **context)
# -------------------------------------


@app.route('/search')
def search_page():
    inventory_data = get_inventory().get_json() 
    # MODIFIED: Pass sub-city map and Google Maps API Key to the template
    return render_template('vehicle_search.html', 
                             inventory_json=json.dumps(inventory_data),
                             sub_city_map_json=json.dumps(SUB_CITY_MAP),
                             google_maps_api_key=app.config['GOOGLE_MAPS_API_KEY'])


# ==================================
# === ROUTES (Host Actions) ===
# ==================================

# --- DYNAMIC PRICE SUGGESTION LOGIC (NEW) ---
def get_demand_insights(current_user_city):
    """
    Calculates demand insights based on confirmed bookings in the host's city 
    and suggests whether the host should raise or maintain their price.
    """
    
    # Analyze bookings confirmed in the last 30 days
    thirty_days_ago = datetime.utcnow() - timedelta(days=30)
    
    # Fetch all relevant confirmed bookings in the host's city
    recent_bookings = db.session.query(Booking).join(Vehicle).filter(
        Booking.status == 'Confirmed',
        Vehicle.city == current_user_city,
        Booking.booked_at >= thirty_days_ago
    ).all()
    
    total_bookings = len(recent_bookings)
    
    if total_bookings < 10:
        return {
            'advice': "Gathering Data: More booking data needed for accurate insights.",
            'action': "green",
            'type_data': []
        }

    # 1. Analyze Demand by Vehicle Type
    demand_by_type = {}
    for booking in recent_bookings:
        v_type = booking.vehicle_info.type 
        demand_by_type[v_type] = demand_by_type.get(v_type, 0) + 1

    # 2. Find the highest demand type
    max_bookings = 0
    most_demanded_type = 'N/A'
    if demand_by_type:
        most_demanded_type = max(demand_by_type, key=demand_by_type.get)
        max_bookings = demand_by_type[most_demanded_type]
        
    # 3. Formulate Price Advice based on overall demand/supply (Simple Heuristic)
    if total_bookings > 50 and (max_bookings / total_bookings) > 0.4:
        advice_action = "Consider a 5-10% price increase on high-demand vehicle types."
        advice_color = "red"
    else:
        advice_action = "Maintain current competitive prices. Market demand is balanced."
        advice_color = "green"

    # Convert demand dictionary to list for Jinja rendering
    type_data_list = sorted([
        {'type': k, 'count': v} for k, v in demand_by_type.items()
    ], key=lambda x: x['count'], reverse=True)
    
    return {
        'advice': advice_action,
        'action': advice_color,
        'most_demanded_type': most_demanded_type,
        'total_bookings': total_bookings,
        'type_data': type_data_list
    }
# ----------------------------------------------------------------------


@app.route('/host/dashboard', methods=['GET', 'POST'])
@host_required # <--- Now checks for role=='host' AND is_approved_host==True
def host_dashboard():
    user_id = session.get('user_id')
    user = User.query.get(user_id)
    error = None
    
    # 1. Get Demand Insights (NEW)
    demand_insights = get_demand_insights(user.city)
    
    # NEW: Initialize submitted_data to pass back to the template if there's an error (UX Improvement)
    submitted_data = {} 

    if request.method == 'POST':
        # NEW: Capture submitted data
        submitted_data = request.form
        image_url_to_db = None
        
        # --- 1. Basic Field Validation ---
        required_fields = ['name', 'brand', 'type', 'city', 'base_price', 'fuel', 'gear', 'kms_per_unit']
        if not all(request.form.get(field) for field in required_fields):
            error = "Error: Please fill all required text/select fields."
        
        # --- 2. Sub-city Validation (Data Integrity Improvement) ---
        city_val = request.form.get('city')
        sub_city_val = request.form.get('sub_city')
        
        if not error and sub_city_val:
            if city_val not in SUB_CITY_MAP or sub_city_val not in SUB_CITY_MAP[city_val]:
                error = f"Error: The selected sub-city '{sub_city_val}' is invalid for the chosen city '{city_val}'. Please select a valid location."
        
        # --- 3. Image Validation ---
        if not error:
            try:
                if 'vehicle_image' not in request.files or request.files['vehicle_image'].filename == '':
                    error = "No vehicle image selected or missing file part."
                else:
                    file = request.files['vehicle_image']
                    if file and allowed_file(file.filename):
                        filename = secure_filename(file.filename)
                        vehicle_count = Vehicle.query.filter_by(host_id=user_id).count()
                        unique_filename = f"{user.id}-{vehicle_count + 1}-{filename}"
                        save_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
                        file.save(save_path)
                        image_url_to_db = url_for('static', filename=f'uploads/{unique_filename}')
                    elif not error:
                        error = "Invalid file type. Only JPG, PNG, WEBP allowed."
            except Exception as e:
                error = f"Image Upload Error: {e}"


        # --- Handle Errors ---
        if error:
            listed_vehicles = Vehicle.query.filter_by(host_id=user_id).options(
                joinedload(Vehicle.bookings).joinedload(Booking.customer)
            ).all()
            for vehicle in listed_vehicles:
                vehicle.bookings = [b for b in vehicle.bookings if b.status == 'Confirmed']
            # NEW: Pass submitted_data back to the template
            return render_template('host_vehicle_add.html', 
                                   error=error, 
                                   user=user, 
                                   listed_vehicles=listed_vehicles, 
                                   sub_city_map_json=json.dumps(SUB_CITY_MAP),
                                   submitted_data=submitted_data,
                                   demand_insights=demand_insights) # ADDED INSIGHTS TO CONTEXT
            
        
        # --- Final Save (Only if no errors) ---
        if not error:
            try:
                # --- CRITICAL: Get PRECISE Geolocation Data using the full Host address as a reference ---
                host_address_1 = user.address1
                host_address_2 = user.address2
                host_city = city_val # Use the City selected for the vehicle
                host_state = user.state
                host_pincode = user.pincode

                # Call the precise Geocoding function
                precise_loc = get_precise_lat_lng(
                    address_line_1=host_address_1,
                    address_line_2=host_address_2,
                    city=host_city,
                    state=host_state,
                    pincode=host_pincode,
                    api_key=app.config['GOOGLE_MAPS_API_KEY']
                )
                # ---------------------------------
                
                vehicle_count = Vehicle.query.filter_by(host_id=user_id).count()
                vehicle_id_code = f"{request.form.get('name').lower().replace(' ', '-')}-{request.form.get('city').lower()}-{user.id}-{vehicle_count + 1}"
                
                new_vehicle = Vehicle(
                    host_id=user.id,
                    vehicle_id_code=vehicle_id_code,
                    name=request.form.get('name'),
                    brand=request.form.get('brand'),
                    type=request.form.get('type'),
                    fuel=request.form.get('fuel'),
                    gear=request.form.get('gear'),
                    city=host_city,
                    sub_city=sub_city_val, 
                    # --- NEW PRECISE GEOLOCATION DATA INSERTION ---
                    latitude=precise_loc['lat'],
                    longitude=precise_loc['lng'],
                    # --------------------------------------
                    base_price=float(request.form.get('base_price')), 
                    image_url=image_url_to_db, 
                    kms_per_unit=int(request.form.get('kms_per_unit')),
                    features=','.join(request.form.getlist('features')),
                    # NEW: Get specification from form
                    specification=request.form.get('specification')
                )
                
                db.session.add(new_vehicle)
                db.session.commit()
                
                flash(f"✅ Vehicle '{new_vehicle.name}' listed successfully!", 'success')
                return redirect(url_for('host_dashboard'))

            except Exception as e:
                db.session.rollback()
                print(f"Vehicle Add Error: {e}")
                error = f"An unexpected database error occurred: {e}"
                
                # NEW: Pass submitted_data back if a late database error occurs
                listed_vehicles = Vehicle.query.filter_by(host_id=user_id).options(joinedload(Vehicle.bookings).joinedload(Booking.customer)).all()
                for vehicle in listed_vehicles:
                    vehicle.bookings = [b for b in vehicle.bookings if b.status == 'Confirmed']
                return render_template('host_vehicle_add.html', 
                                       error=error, 
                                       user=user, 
                                       listed_vehicles=listed_vehicles, 
                                       sub_city_map_json=json.dumps(SUB_CITY_MAP),
                                       submitted_data=submitted_data,
                                       demand_insights=demand_insights) # ADDED INSIGHTS TO CONTEXT

    
    # --- GET Request (Initial Load) ---
    listed_vehicles = Vehicle.query.filter_by(host_id=user_id).options(
        joinedload(Vehicle.bookings).joinedload(Booking.customer)
    ).order_by(Vehicle.id.desc()).all() 
    
    for vehicle in listed_vehicles:
        # Filter for confirmed bookings for display
        confirmed_bookings = [
            b for b in vehicle.bookings if b.status == 'Confirmed' 
        ]
        vehicle.bookings = confirmed_bookings
        
        # NEW: Check for future bookings to enable/disable the edit button
        future_booking = any(b.end_date > datetime.utcnow() for b in confirmed_bookings)
        vehicle.has_future_booking = future_booking

    
    # When loading initially, submitted_data is empty
    return render_template('host_vehicle_add.html', 
                           user=user, 
                           listed_vehicles=listed_vehicles, 
                           error=error,
                           sub_city_map_json=json.dumps(SUB_CITY_MAP),
                           submitted_data={},
                           demand_insights=demand_insights) # ADDED INSIGHTS TO CONTEXT

# --- NEW: ROUTE FOR EDITING A VEHICLE ---
@app.route('/host/edit-vehicle/<int:vehicle_id>', methods=['GET', 'POST'])
@host_required
def edit_vehicle(vehicle_id):
    user_id = session.get('user_id')
    vehicle = Vehicle.query.filter_by(id=vehicle_id, host_id=user_id).first()

    if not vehicle:
        flash("❌ Vehicle not found or you don't have permission to edit it.", 'error')
        return redirect(url_for('host_dashboard'))

    # Security Check: Prevent editing if there's a future confirmed booking
    future_booking = Booking.query.filter(
        Booking.vehicle_id == vehicle_id,
        Booking.status == 'Confirmed',
        Booking.end_date > datetime.utcnow()
    ).first()

    if future_booking:
        flash(f"❌ Cannot edit '{vehicle.name}' as it has an upcoming booking.", 'error')
        return redirect(url_for('host_dashboard'))

    if request.method == 'POST':
        try:
            # Update basic details
            vehicle.name = request.form.get('name')
            vehicle.brand = request.form.get('brand')
            vehicle.type = request.form.get('type')
            vehicle.fuel = request.form.get('fuel')
            vehicle.gear = request.form.get('gear')
            vehicle.base_price = float(request.form.get('base_price'))
            vehicle.kms_per_unit = int(request.form.get('kms_per_unit'))
            vehicle.features = ','.join(request.form.getlist('features'))
            vehicle.specification = request.form.get('specification')

            # Handle optional image upload
            if 'vehicle_image' in request.files:
                file = request.files['vehicle_image']
                if file.filename != '' and allowed_file(file.filename):
                    # Delete old image if it exists
                    if vehicle.image_url:
                        try:
                            # Construct absolute path to the old file
                            old_filename = vehicle.image_url.split('/')[-1]
                            old_filepath = os.path.join(app.config['UPLOAD_FOLDER'], old_filename)
                            if os.path.exists(old_filepath):
                                os.remove(old_filepath)
                        except Exception as e:
                            print(f"Could not delete old image file: {e}")
                    
                    # Save new image
                    filename = secure_filename(file.filename)
                    unique_filename = f"{user_id}-{vehicle.id}-{datetime.now().strftime('%Y%m%d%H%M%S')}-{filename}"
                    save_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
                    file.save(save_path)
                    vehicle.image_url = url_for('static', filename=f'uploads/{unique_filename}')

            db.session.commit()
            flash(f"✅ Vehicle '{vehicle.name}' updated successfully!", 'success')
            return redirect(url_for('host_dashboard'))

        except Exception as e:
            db.session.rollback()
            flash(f"❌ An error occurred while updating the vehicle: {e}", 'error')
            print(f"Vehicle Edit Error: {e}")

    # For GET request, render the edit form
    return render_template('host_vehicle_edit.html', vehicle=vehicle)
# --- END NEW ROUTE ---

@app.route('/host/toggle_availability/<int:vehicle_id>', methods=['POST'])
@host_required
def toggle_availability(vehicle_id):
    user_id = session.get('user_id')
    vehicle = Vehicle.query.filter_by(id=vehicle_id, host_id=user_id).first()

    if not vehicle:
        flash("❌ Vehicle not found or you don't have permission to edit it.", 'error')
        return redirect(url_for('host_dashboard'))

    # Check for future bookings ONLY if the host is trying to make it UNAVAILABLE
    if vehicle.is_available: # If it's currently available, check before disabling
        future_booking = Booking.query.filter(
            Booking.vehicle_id == vehicle_id,
            Booking.status == 'Confirmed',
            Booking.end_date > datetime.utcnow()
        ).first()

        if future_booking:
            flash(f"❌ Cannot make '{vehicle.name}' unavailable. It has a confirmed future booking ending on {future_booking.end_date.strftime('%Y-%m-%d %H:%M')}.", 'error')
            return redirect(url_for('host_dashboard'))
    
    # If no future bookings or if re-enabling, proceed to toggle
    try:
        vehicle.is_available = not vehicle.is_available
        db.session.commit()
        
        new_status = "Available" if vehicle.is_available else "Unavailable"
        flash(f"✅ Status for '{vehicle.name}' updated to {new_status}.", 'success')
    
    except Exception as e:
        db.session.rollback()
        print(f"Availability Toggle Error: {e}")
        flash("An error occurred while updating the vehicle's status.", 'error')

    return redirect(url_for('host_dashboard'))

# --- NEW ROUTE FOR HOST TO SET EARNINGS TIER ---
@app.route('/host/set-tier', methods=['GET', 'POST'])
@host_required
def set_host_tier():
    user_id = session.get('user_id')
    user = User.query.get(user_id)
    
    # 80% tier ka rule: Host must be approved and active
    can_access_80_tier = user.is_approved_host and user.is_active and len(user.vehicles) >= 1

    if request.method == 'POST':
        selected_tier = request.form.get('commission_tier')
        
        if selected_tier == '80' and not can_access_80_tier:
            flash("❌ You do not meet the minimum vehicle/availability requirements for the 80% tier yet.", 'error')
            return redirect(url_for('set_host_tier'))
            
        try:
            user.commission_tier = int(selected_tier)
            db.session.commit()
            flash(f"✅ Success! Your commission tier has been updated to {user.commission_tier}%.", 'success')
            return redirect(url_for('host_dashboard'))
        except ValueError:
            flash("❌ Invalid tier selection.", 'error')
        except Exception:
            flash("❌ An unexpected error occurred while saving your tier.", 'error')
            db.session.rollback()
            
    context = {
        'user': user,
        'current_tier': user.commission_tier,
        'can_access_80_tier': can_access_80_tier,
    }
    return render_template('host_set_tier.html', **context)
# --------------------------------------------------

# --- NEW ROUTE FOR HOST EARNINGS VIEW ---
@app.route('/host/earnings')
@host_required
def host_earnings_view():
    user_id = session.get('user_id')
    user = User.query.get(user_id)
    
    # Host's current payout rate (e.g., 0.70 or 0.80)
    payout_rate = (user.commission_tier / 100.0) if user and user.commission_tier else 0.70
    
    # Fetch all confirmed bookings associated with this Host's vehicles
    host_bookings = Booking.query.options(joinedload(Booking.vehicle_info)).join(Vehicle).filter(
        Vehicle.host_id == user_id,
        Booking.status == 'Confirmed'
    ).order_by(Booking.start_date.desc()).all()
    
    earnings_summary = []
    total_lifetime_earnings = 0
    
    for booking in host_bookings:
        # Calculate commissionable base (Total Price - Deposit)
        commissionable_base = booking.total_price - booking.deposit_amount
        
        host_share = round(commissionable_base * payout_rate)
        platform_commission = round(commissionable_base - host_share)
        
        earnings_summary.append({
            'booking_id': booking.id,
            'vehicle_name': booking.vehicle_info.name,
            'total_booked_price': round(booking.total_price),
            'deposit_amount': round(booking.deposit_amount), # NEW
            'host_earning': host_share,
            'platform_commission': platform_commission,
            'start_date': booking.start_date.strftime('%Y-%m-%d %H:%M'),
        })
        total_lifetime_earnings += host_share
        
    context = {
        'user': user,
        'payout_rate': round(payout_rate * 100),
        'total_lifetime_earnings': round(total_lifetime_earnings),
        'earnings_summary': earnings_summary,
    }
    
    return render_template('host_earnings.html', **context)
# ----------------------------------------


@app.route('/host/add-vehicle', methods=['GET'])
@host_required
def add_vehicle_page():
    # Redirecting to host_dashboard is intended behavior as per previous code
    return redirect(url_for('host_dashboard'))

@app.route('/host/success')
@login_required
def host_success():
    flash("✅ Vehicle added successfully. Check your dashboard for listing status.", 'success')
    return redirect(url_for('host_dashboard'))

# ==================================
# === ROUTES (ADMIN ACTIONS) ===
# ==================================

# --- ADDED: ADMIN USERS ROUTE (Fix for BuildError) ---
@app.route('/admin/users', methods=['GET'])
@admin_required
def admin_users():
    """Displays a sortable/filterable list of all Users (Hosts and Customers)."""
    # Fetch all users who are not super_admin
    all_users = User.query.filter(User.role != 'super_admin').order_by(User.id.desc()).all()
    
    # For User List page, we pass all users
    # NOTE: You will need an admin_user_list.html template for this.
    return render_template('admin_user_list.html', all_users=all_users) 

# --- NEW: ADMIN COMPLAINTS ROUTE ---
@app.route('/admin/complaints', methods=['GET'])
@admin_required
def admin_complaints():
    """Displays a sortable/filterable list of all submitted complaints."""
    # Get the status filter from the query parameters, default to 'New'
    status_filter = request.args.get('status', 'New')

    query = Complaint.query
    
    if status_filter and status_filter != 'All':
        query = query.filter(Complaint.status == status_filter)

    all_complaints = query.order_by(Complaint.submitted_at.desc()).all()
    
    # NOTE: You will need a new admin_complaints.html template for this.
    return render_template('admin_complaints.html', 
                             complaints=all_complaints, 
                             current_filter=status_filter)
# ----------------------------------------------------

# --- ADDED: ADMIN USER PROFILE ROUTE ---
@app.route('/admin/user/view/<int:user_id>', methods=['GET'])
@admin_required
def view_user_profile(user_id):
    """Displays a detailed, comprehensive profile for any given user/host."""
    # Ensure all user data is loaded, including vehicles
    user = User.query.options(joinedload(User.vehicles)).get(user_id)
    
    if not user or user.role == 'super_admin':
        flash("❌ Error: User or Host profile not found.", 'error')
        return redirect(url_for('admin_users'))
        
    # Fetch all confirmed bookings where this user is either the customer or the host
    bookings = Booking.query.options(joinedload(Booking.vehicle_info)).filter(
        (Booking.user_id == user_id) | (Vehicle.host_id == user_id)
    ).join(Vehicle, Booking.vehicle_id == Vehicle.id).order_by(Booking.start_date.desc()).all()

    # Calculate Host Earnings (if user is a host)
    host_earnings = 0
    if user.role == 'host':
        confirmed_host_bookings = Booking.query.join(Vehicle).filter(
            Vehicle.host_id == user_id,
            Booking.status == 'Confirmed'
        ).all()
        # Using 80% Host share based on default commission calculation
        host_earnings = sum((b.total_price - b.deposit_amount) * 0.80 for b in confirmed_host_bookings)


    context = {
        'user': user,
        'bookings': bookings,
        'host_earnings': round(host_earnings)
    }
    
    # NOTE: You will need an admin_user_profile.html template for this.
    return render_template('admin_user_profile.html', **context)
# --------------------------------------------------------------------


@app.route('/admin')
@admin_required
def admin_dashboard():
    # --- Data for Admin Panel ---
    
    # 1. Booking Stats & Revenue
    confirmed_bookings = Booking.query.filter_by(status='Confirmed').all()
    
    total_bookings_count = Booking.query.count()
    confirmed_count = len(confirmed_bookings)

    total_revenue = sum(b.total_price for b in confirmed_bookings)
    
    # 2. Host and Vehicle Data
    total_hosts = db.session.query(User).filter_by(role='host').count()
    total_vehicles = db.session.query(Vehicle).count()
    
    # 3. Detailed Cancelled/Refund Request View (Now filtered by refund_status='Pending')
    # This remains for the main booking refund flow (Cancellation/Fee)
    pending_booking_refunds = Booking.query.options(
        joinedload(Booking.customer),
        joinedload(Booking.vehicle_info).joinedload(Vehicle.host)
    ).filter(
        Booking.status == 'Cancelled',
        Booking.refund_status == 'Pending'
    ).all()
    
    # --- NEW: Deposit Refund Requests ---
    # This covers completed bookings where deposit refund is pending
    pending_deposit_refunds = Booking.query.options(
            joinedload(Booking.customer),
            joinedload(Booking.vehicle_info).joinedload(Vehicle.host)
    ).filter(
        Booking.end_date < datetime.utcnow(),
        Booking.status == 'Confirmed',
        Booking.deposit_refund_status == 'Pending'
    ).all()
    # -----------------------------------
    
    cancelled_details = []
    for booking in pending_booking_refunds:
        user = booking.customer
        vehicle = booking.vehicle_info
        
        cancellation_fee = 0
        refund_amount = booking.total_price
        time_difference = datetime.utcnow() - booking.booked_at
        
        if time_difference >= timedelta(hours=1):
            cancellation_fee = round(booking.total_price * 0.10) 
            refund_amount = booking.total_price - cancellation_fee
            
        cancelled_details.append({
            'booking_id': booking.id,
            'customer_name': f"{user.first_name} {user.last_name}" if user else 'N/A',
            'vehicle_name': vehicle.name if vehicle else 'N/A',
            'vehicle_host': vehicle.host.first_name if vehicle and vehicle.host else 'N/A',
            'total_price': round(booking.total_price),
            'refund_due': round(refund_amount),
            'cancellation_fee': cancellation_fee,
            'payment_id': booking.payment_id,
            'booked_at': booking.booked_at.strftime('%Y-%m-%d %H:%M')
        })
    
    cancelled_count = len(pending_booking_refunds) # Count only pending booking refunds for dashboard stat
    deposit_refund_count = len(pending_deposit_refunds) # NEW stat

    # 4. Host Approval Overview (NEW)
    pending_hosts = db.session.query(User).filter(
        User.role == 'host',
        User.is_approved_host == False
    ).order_by(User.id.asc()).all()
    
    # 5. All Hosts for Blocking/Activating (NOTE: Moved to admin_users for clarity)
    all_hosts = db.session.query(User).filter(
        User.role == 'host',
    ).order_by(User.id.desc()).all()


    # 6. Host Booking Overview 
    host_booking_summary = db.session.query(
        User.first_name, 
        db.func.count(Booking.id)
    ).join(Vehicle, User.id == Vehicle.host_id).join(
        Booking, Vehicle.id == Booking.vehicle_id
    ).filter(
        User.role == 'host',
        Booking.status == 'Confirmed'
    ).group_by(User.first_name).order_by(db.func.count(Booking.id).desc()).limit(10).all()

    
    # 7. FINANCIAL HEALTH CALCULATION (Updated to exclude deposit from commission)
    total_platform_commission = 0
    total_payout_due = 0
    host_financial_summary = {}

    for booking in confirmed_bookings:
        host = booking.vehicle_info.host
        
        payout_rate = (host.commission_tier / 100.0) if host and host.commission_tier else 0.70
        
        # Calculate commissionable base (Total Price - Deposit)
        commissionable_base = booking.total_price - booking.deposit_amount
        
        commission = commissionable_base * (1.0 - payout_rate) 
        payout = commissionable_base * payout_rate      
        
        total_platform_commission += commission
        total_payout_due += payout
        
        host_id = host.id
        host_name = host.first_name

        if host_id not in host_financial_summary:
            host_financial_summary[host_id] = {
                'name': host_name,
                'total_earnings': 0,
                'total_bookings': 0,
                'tier': host.commission_tier
            }
        host_financial_summary[host_id]['total_earnings'] += payout
        host_financial_summary[host_id]['total_bookings'] += 1

    host_payouts_list = sorted(
        host_financial_summary.values(), 
        key=lambda x: x['total_earnings'], 
        reverse=True
    )
    # ========================================================


    context = {
        'total_bookings': total_bookings_count,
        'confirmed_bookings_count': confirmed_count,
        'cancelled_bookings_count': cancelled_count, # Pending booking refunds count
        'deposit_refund_count': deposit_refund_count, # NEW
        'total_revenue': round(total_revenue),
        'total_hosts': total_hosts,
        'total_vehicles': total_vehicles,
        'cancelled_details': cancelled_details,
        'pending_deposit_refunds': pending_deposit_refunds, # NEW
        'host_booking_summary': host_booking_summary,
        'pending_hosts': pending_hosts, 
        'all_hosts': all_hosts, 
        # NEW FINANCIAL CONTEXT
        'total_platform_commission': round(total_platform_commission),
        'total_payout_due': round(total_payout_due),
        'host_payouts_list': host_payouts_list
    }
    
    return render_template('admin_dashboard.html', **context)

# ... (other admin and general routes) ...

# --- ROUTE TO TOGGLE BLOCK/ACTIVATE STATUS ---
@app.route('/api/admin/toggle-host-status/<int:user_id>', methods=['POST'])
@admin_required
def toggle_host_status(user_id):
    """Toggles the is_active status for a host user and their vehicles."""
    user = User.query.filter_by(id=user_id, role='host').first()
    
    if not user:
        return jsonify({'success': False, 'message': 'Host user not found.'}), 404
        
    try:
        new_status = not user.is_active
        user.is_active = new_status
        
        # --- CRITICAL: Deactivate ALL vehicles if Host is Blocked ---
        vehicle_count = len(user.vehicles)
        if not new_status:
            # Blocked: Set all host vehicles to is_available=False
            for vehicle in user.vehicles:
                vehicle.is_available = False
            flash(f"⚠️ Host '{user.first_name}' blocked. All {vehicle_count} associated vehicles are now unavailable.", 'warning')
        elif new_status:
            # Activated: Do NOT automatically activate vehicles; Host must do this.
            flash(f"✅ Host '{user.first_name}' activated. Host must manually re-activate their vehicles.", 'success')

        db.session.commit()

        action = "activated" if user.is_active else "blocked"
        
        # Return a simple success message, Flash will show the details on reload
        return jsonify({'success': True, 'message': f"Host successfully {action}. Reloading dashboard..."}), 200
        
    except Exception as e:
        db.session.rollback()
        print(f"Host Status Toggle Error: {e}")
        return jsonify({'success': False, 'message': f'Database update failed: {e}'}), 500


@app.route('/api/admin/process_refund/<int:booking_id>', methods=['POST'])
@admin_required
def process_refund_api(booking_id):
    booking = Booking.query.filter_by(id=booking_id).first()
    
    if not booking:
        return jsonify({'success': False, 'message': 'Booking not found.'}), 404
        
    if booking.status != 'Cancelled' or booking.refund_status == 'Processed':
        return jsonify({'success': False, 'message': 'Refund already processed or status is not Cancelled.'}), 400

    try:
        booking.refund_status = 'Processed'
        db.session.commit()
        
        flash(f"✅ Refund for Booking #{booking_id} marked as PROCESSED.", 'success')
        return jsonify({'success': True, 'message': 'Refund status updated to Processed.'}), 200
        
    except Exception as e:
        db.session.rollback()
        print(f"Refund API Error: {e}")
        return jsonify({'success': False, 'message': f'Database update failed: {e}'}), 500

# --- NEW ROUTE TO PROCESS DEPOSIT REFUND ---
@app.route('/api/admin/process_deposit_refund/<int:booking_id>', methods=['POST'])
@admin_required
def process_deposit_refund_api(booking_id):
    """Marks the refundable deposit as processed."""
    booking = Booking.query.filter_by(id=booking_id).first()
    
    if not booking:
        return jsonify({'success': False, 'message': 'Booking not found.'}), 404
        
    if booking.deposit_refund_status != 'Pending':
        return jsonify({'success': False, 'message': 'Deposit refund already processed or denied.'}), 400

    try:
        booking.deposit_refund_status = 'Processed'
        db.session.commit()
        
        flash(f"✅ Deposit Refund for Booking #{booking_id} (₹{booking.deposit_amount}) marked as PROCESSED.", 'success')
        return jsonify({'success': True, 'message': 'Deposit refund status updated to Processed.'}), 200
        
    except Exception as e:
        db.session.rollback()
        print(f"Deposit Refund API Error: {e}")
        return jsonify({'success': False, 'message': f'Database update failed: {e}'}), 500
# -------------------------------------------

# --- NEW API ROUTE TO UPDATE COMPLAINT STATUS ---
@app.route('/api/admin/update_complaint_status/<int:complaint_id>', methods=['POST'])
@admin_required
def update_complaint_status(complaint_id):
    """Updates the status of a specific complaint."""
    data = request.get_json()
    new_status = data.get('status')
    
    if not new_status:
        return jsonify({'success': False, 'message': 'New status not provided.'}), 400

    complaint = Complaint.query.get(complaint_id)
    if not complaint:
        return jsonify({'success': False, 'message': 'Complaint not found.'}), 404
        
    try:
        complaint.status = new_status
        db.session.commit()
        flash(f"✅ Status for Complaint #{complaint_id} updated to '{new_status}'.", 'success')
        return jsonify({'success': True, 'message': f'Status updated to {new_status}.'}), 200
    except Exception as e:
        db.session.rollback()
        print(f"Complaint Status Update Error: {e}")
        return jsonify({'success': False, 'message': 'Database update failed.'}), 500
# ------------------------------------------------


@app.route('/api/admin/approve_host/<int:user_id>', methods=['POST'])
@admin_required
def approve_host_api(user_id):
    """Marks a host user as approved and triggers SMS notification using Twilio."""
    user = User.query.filter_by(id=user_id, role='host').first()
    
    if not user:
        return jsonify({'success': False, 'message': 'Host user not found.'}), 404
        
    if user.is_approved_host:
        return jsonify({'success': False, 'message': f'Host {user.first_name} is already approved.'}), 400
        
    if not user.is_active:
        return jsonify({'success': False, 'message': f'Host {user.first_name} is currently blocked. Activate the account before approval.'}), 400

    try:
        # 1. Update DB Status
        user.is_approved_host = True
        
        # 2. Try to Send SMS (This often causes failure if Twilio setup is incomplete/incorrect)
        send_success = send_approval_sms(user.phone, user.first_name)
        sms_message = "SMS triggered." if send_success else "WARNING: SMS failed to send (Check phone number format/Twilio credits)."
        
        # 3. Commit the DB change only if the update attempt was successful
        db.session.commit()
        
        flash(f"✅ Host '{user.first_name} {user.last_name}' successfully approved and can now list vehicles. {sms_message}", 'success')
        return jsonify({'success': True, 'message': f'Host approved successfully. {sms_message}'}), 200
        
    except Exception as e:
        db.session.rollback()
        # Log the detailed error on the server console for debugging
        print(f"❌ CRITICAL HOST APPROVAL ERROR for User ID {user_id}: {e}")
        
        # Return a generic error message to the client
        return jsonify({'success': False, 'message': f'Failed to process approval due to a server error. Check server console for details.'}), 500


# ... (rest of the API and utility routes) ...

@app.route('/api/submit_review', methods=['POST'])
@login_required
def submit_review():
    data = request.get_json()
    user_id = session.get('user_id')
    
    booking_id = data.get('booking_id')
    rating = data.get('rating')
    comment = data.get('comment')
    
    if not all([booking_id, rating]):
        return jsonify({'success': False, 'message': 'Missing rating or booking ID.'}), 400

    try:
        booking = Booking.query.filter_by(id=booking_id, user_id=user_id).first()
        
        if not booking or not is_booking_reviewable(booking):
            return jsonify({'success': False, 'message': 'Booking not found or not eligible for review.'}), 403

        # 1. Create the new Review object
        new_review = Review(
            booking_id=booking.id,
            user_id=user_id,
            vehicle_id=booking.vehicle_id,
            rating=float(rating),
            comment=comment
        )
        db.session.add(new_review)
        db.session.commit()

        # 2. Recalculate Vehicle's Average Rating
        vehicle = Vehicle.query.get(booking.vehicle_id)
        if vehicle:
            # Fetch all existing reviews for this vehicle
            all_reviews = Review.query.filter_by(vehicle_id=vehicle.id).all()
            
            total_rating = sum(r.rating for r in all_reviews)
            # Default to 4.0 if somehow no reviews exist after this one (shouldn't happen)
            new_avg_rating = total_rating / len(all_reviews) if all_reviews else 4.0 
            
            # Update the vehicle's rating (rounding to 1 decimal)
            vehicle.rating = round(new_avg_rating, 1)
            db.session.commit()

        # Flash message for the user's next page load
        flash("⭐ Thank you! Your review has been submitted and the vehicle's rating updated.", 'success')
        return jsonify({'success': True, 'message': 'Review submitted successfully.'}), 200

    except ValueError:
        return jsonify({'success': False, 'message': 'Invalid rating format.'}), 400
    except Exception as e:
        db.session.rollback()
        print(f"Review Submission Error: {e}")
        return jsonify({'success': False, 'message': f'Server error: {e}'}), 500


@app.route('/api/inventory', methods=['GET'])
def get_inventory():
    # ... (Function body remains the same) ...
    all_confirmed_bookings = Booking.query.filter_by(status='Confirmed').all()
    
    booked_dates_map = {}
    for booking in all_confirmed_bookings:
        if booking.vehicle_id not in booked_dates_map:
            booked_dates_map[booking.vehicle_id] = []
        booked_dates_map[booking.vehicle_id].append([
            booking.start_date.strftime('%Y-%m-%d %H:%M'), 
            booking.end_date.strftime('%Y-%m-%d %H:%M')
        ])
    
    # Filter only available vehicles
    db_vehicles = Vehicle.query.filter_by(is_available=True).all()
    db_inventory_data = []
    for vehicle in db_vehicles:
        booked_info = booked_dates_map.get(vehicle.id, [])
        # Fetch rating directly from the Vehicle object and export lat/lng via to_dict
        db_inventory_data.append(vehicle.to_dict(booked_dates=booked_info))
    
    full_inventory = db_inventory_data 

    return jsonify(full_inventory)

# --- MODIFIED RAZORPAY ORDER CREATION ROUTE ---
@app.route('/api/create_razorpay_order', methods=['POST']) 
@login_required
def create_razorpay_order():
    # ... (Function body remains the same) ...
    data = request.get_json()
    user_id = session.get('user_id')
    user_details = User.query.get(user_id) # Fetch user for prefill details
    
    vehicle_id_code = data.get('vehicle_id') 
    start_date_str_iso = data.get('start_date')
    end_date_str_iso = data.get('end_date')
    
    if not all([vehicle_id_code, start_date_str_iso, end_date_str_iso, user_id]):
        return jsonify({'success': False, 'message': 'Missing booking details.'}), 400

    vehicle = Vehicle.query.filter_by(vehicle_id_code=vehicle_id_code).first()
    
    if not vehicle:
        return jsonify({'success': False, 'message': 'Vehicle not found.'}), 404
        
    if not is_vehicle_available(vehicle.id, start_date_str_iso, end_date_str_iso):
        return jsonify({'success': False, 'message': 'Vehicle is booked for these times.'}), 409
        
    # --- Date calculation for pricing/deposit
    DATE_FORMAT = '%Y-%m-%d %H:%M:%S'
    s = datetime.strptime(start_date_str_iso, DATE_FORMAT)
    e = datetime.strptime(end_date_str_iso, DATE_FORMAT)
    time_difference = e - s
    total_hours = time_difference.total_seconds() / 3600

    # --- SERVER PRICE CALCULATION (Crucial for Order creation) ---
    server_total_subtotal = price_for_server(vehicle.base_price, vehicle.fuel, start_date_str_iso, end_date_str_iso)
    
    # --- NEW: Dynamic Deposit Calculation ---
    server_deposit = calculate_deposit(server_total_subtotal, total_hours)
    # ---------------------------------------
    
    server_gst = round(server_total_subtotal * 0.18)
    server_total_final = server_total_subtotal + server_gst + server_deposit
    
    # Razorpay amount is in paise (cents), so multiply by 100 and cast to int
    amount_in_paise = int(round(server_total_final * 100))
    
    # Store temporary booking data in session to be used in the verification step
    session['temp_booking_data'] = {
        'vehicle_id_code': vehicle_id_code,
        'start_date': start_date_str_iso,
        'end_date': end_date_str_iso,
        'expected_total': round(server_total_final), # Stored in Rupees
        'expected_deposit': server_deposit # NEW: Store calculated deposit
    }

    try:
        # Create a Razorpay Order using the Secret Key (via client object)
        order_data = {
            'amount': amount_in_paise, # Amount in paise
            'currency': 'INR',
            'receipt': f'rcpt_prime_drew_{user_id}_{datetime.utcnow().timestamp()}',
            'payment_capture': '1' # Auto capture payment
        }
        
        razorpay_order = app.config['RZP_CLIENT'].order.create(data=order_data)

        return jsonify({
            'success': True, 
            'order_id': razorpay_order['id'],
            'amount': amount_in_paise,
            'key_id': app.config['RZP_KEY_ID'],
            'name': f"{user_details.first_name} {user_details.last_name}" if user_details else 'Rider',
            'email': user_details.email if user_details else '',
            'contact': user_details.phone if user_details else '',
            'message': 'Razorpay order created successfully.'
        }), 200

    except Exception as e:
        print(f"Razorpay Order Creation Error: {e}")
        return jsonify({'success': False, 'message': f'Failed to create payment order: {e}'}), 500

# --- MODIFIED CONFIRM BOOKING ROUTE ---
@app.route('/api/confirm_booking', methods=['POST'])
@login_required
def confirm_booking():
    # ... (Function body remains the same) ...
    data = request.get_json()
    user_id = session.get('user_id')
    temp_data = session.pop('temp_booking_data', None) # Retrieve and clear temp data
    
    payment_id = data.get('payment_id') 
    razorpay_order_id = data.get('razorpay_order_id')

    if not temp_data:
        return jsonify({'success': False, 'message': 'Session expired or order was not initialized.'}), 400

    vehicle_id_code = temp_data['vehicle_id_code'] 
    start_date_str_iso = temp_data['start_date']
    end_date_str_iso = temp_data['end_date']
    expected_total_price = temp_data['expected_total']
    expected_deposit = temp_data['expected_deposit'] # NEW: Retrieve expected deposit

    if not payment_id or not razorpay_order_id:
        return jsonify({'success': False, 'message': 'Payment verification data missing.'}), 400

    # --- CRITICAL: Verify Payment with Razorpay (using Secret Key on the server) ---
    try:
        payment_details = app.config['RZP_CLIENT'].payment.fetch(payment_id)
        
        expected_amount_paise = int(expected_total_price * 100)
        
        if payment_details['order_id'] != razorpay_order_id:
            raise ValueError("Order ID mismatch.")
            
        if payment_details['amount'] != expected_amount_paise or payment_details['status'] != 'captured':
            raise ValueError(f"Payment validation failed. Amount: {payment_details['amount']}, Status: {payment_details['status']}")

    except Exception as e:
        print(f"Razorpay Verification Error: {e}")
        return jsonify({'success': False, 'message': f'Payment verification failed. Please contact support with Payment ID: {payment_id}. Error: {e}'}), 400

    # --- Final DB Check (Race Condition) ---
    vehicle = Vehicle.query.filter_by(vehicle_id_code=vehicle_id_code).first()
    if not is_vehicle_available(vehicle.id, start_date_str_iso, end_date_str_iso):
        return jsonify({'success': False, 'message': 'Vehicle was booked by another user during payment. Refund will be processed shortly.'}), 409

    # --- Final Price Re-verification ---
    DATE_FORMAT = '%Y-%m-%d %H:%M:%S'
    s = datetime.strptime(start_date_str_iso, DATE_FORMAT)
    e = datetime.strptime(end_date_str_iso, DATE_FORMAT)
    time_difference = e - s
    total_hours = time_difference.total_seconds() / 3600
    
    server_total_subtotal = price_for_server(vehicle.base_price, vehicle.fuel, start_date_str_iso, end_date_str_iso)
    server_deposit = calculate_deposit(server_total_subtotal, total_hours)
    server_gst = round(server_total_subtotal * 0.18)
    server_total = server_total_subtotal + server_gst + server_deposit
    
    if abs(server_total - expected_total_price) > 1:
        return jsonify({'success': False, 'message': 'Price calculation mismatch after payment. Contact support.'}), 400
        
    # --- Save Booking ---
    try:
        
        new_booking = Booking(
            user_id=user_id,
            vehicle_id=vehicle.id, 
            start_date=s,
            end_date=e,
            total_price=round(server_total), 
            deposit_amount=server_deposit, # NEW
            deposit_refund_status='Pending', # NEW: Always Pending initially
            status='Confirmed', 
            payment_id=payment_id, 
            refund_status='NotApplicable' 
        )
        db.session.add(new_booking)
        db.session.commit()
        
        return jsonify({'success': True, 'message': 'Booking confirmed and paid!', 'booking_id': new_booking.id, 'total': round(server_total)}), 200

    except Exception as e:
        db.session.rollback()
        print(f"Booking Save Error after Payment: {e}")
        return jsonify({'success': False, 'message': 'Payment captured, but failed to save booking. Contact support for assistance.'}), 500


@app.route('/api/cancel_booking/<int:booking_id>', methods=['POST'])
@login_required
def cancel_booking(booking_id):
    # ... (Function body remains the same) ...
    user_id = session.get('user_id')

    booking = Booking.query.filter_by(id=booking_id, user_id=user_id).first()

    if not booking:
        return jsonify({'success': False, 'message': 'Booking not found or unauthorized.'}), 404
    
    if booking.status in ['Cancelled', 'Completed']:
        return jsonify({'success': False, 'message': f"Booking status '{booking.status}' cannot be cancelled."}), 400

    booked_at = booking.booked_at
    time_difference = datetime.utcnow() - booked_at
    cancellation_fee = 0
    
    if time_difference < timedelta(hours=1):
        refund_amount = booking.total_price 
    else:
        cancellation_fee = round(booking.total_price * 0.10)
        refund_amount = booking.total_price - cancellation_fee
        
    try:
        # Update DB status and set refund_status to Pending
        booking.status = 'Cancelled'
        booking.refund_status = 'Pending' # Mark for Admin review (Full Refund Flow)
        booking.deposit_refund_status = 'NotApplicable' # Deposit refund handled as part of the total refund here
        db.session.commit()

        flash(f"✅ Booking #{booking_id} has been cancelled. Refund request submitted for ₹{refund_amount}.", 'success')
        return jsonify({
            'success': True, 
            'message': 'Booking cancelled successfully. Refund is pending.', 
            'refund_amount': refund_amount,
            'fee': cancellation_fee
        }), 200

    except Exception as e:
        db.session.rollback()
        print(f"Cancellation Error: {e}")
        return jsonify({'success': False, 'message': 'Failed to cancel booking due to a database error.'}), 500


# ==================================
# === ADMIN CREATION UTILITY (DB-BASED) ===
# ==================================

def create_admin_user(admin_email, admin_phone, admin_password):
    """Creates or updates a Super Admin user in the database."""
    # Check if an admin user with this email already exists
    user = User.query.filter_by(email=admin_email).first()
    
    # 1. Generate the secure hash for the password
    hashed_password = generate_password_hash(admin_password)

    if user:
        # Update existing user to be super_admin and set statuses
        if user.role != 'super_admin':
            user.role = 'super_admin'
        user.password = hashed_password
        user.is_approved_host = True
        user.is_active = True # Admin must be active
        db.session.commit()
        print(f"✅ Existing user '{admin_email}' updated to Super Admin with new password.")
        
    else:
        # Create a new super_admin user
        new_admin = User(
            firebase_uid=secrets.token_hex(16), # Placeholder UID
            phone=admin_phone,
            email=admin_email,
            password=hashed_password,
            first_name="Super",
            last_name="Admin",
            dob="2000-01-01",
            role='super_admin',
            address1="Admin HQ",
            address2=None,
            city="Global",
            state="State",
            pincode="000000",
            identity_doc="Aadhar",
            dl_number="ADMN12345",
            dl_expiry="2030-01-01",
            terms_agreed=True,
            is_approved_host=True,
            is_active=True # Admin must be active
        )
        db.session.add(new_admin)
        db.session.commit()
        print(f"✅ New Super Admin user '{admin_email}' created successfully.")

# ==================================
# === MAIN RUN ===
# ==================================

if __name__ == '__main__':
    with app.app_context():
        # --- CRITICAL STEP: DELETE 'project_data.db' BEFORE RUNNING THIS BLOCK FOR SCHEMA CHANGES ---
        print("Creating all database tables...")
        db.create_all() 
        
        # # --- Create Admin User (Use these credentials to log in) ---
        print("Creating/Updating Super Admin...")
        create_admin_user("admin@primedrew.com", "9999999999", "adminpass")
        # # -----------------------------------------------------------
            
    app.run(debug=True, port=5000)