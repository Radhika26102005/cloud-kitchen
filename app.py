import eventlet
eventlet.monkey_patch(all=True)

import os
import sys
from flask import Flask, render_template, redirect, url_for, request, flash, jsonify
from flask_login import LoginManager, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from flask_socketio import SocketIO, emit
import stripe
import razorpay
from models import db, User, FoodItem, Order, Review, OrderItem, Notification, CartItem, PushSubscription
from sqlalchemy import func, text
from dotenv import load_dotenv
from flask_mail import Mail, Message
from itsdangerous import URLSafeTimedSerializer
import re
import time
import cloudinary
import cloudinary.uploader
import cloudinary.api

load_dotenv() # Load variables from .env


app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'super-secret-key-for-cloud-kitchen')
# Use PostgreSQL on Render, SQLite locally
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL', 'sqlite:///cloud_kitchen_v3.db')
if app.config['SQLALCHEMY_DATABASE_URI'].startswith("postgres://"):
    app.config['SQLALCHEMY_DATABASE_URI'] = app.config['SQLALCHEMY_DATABASE_URI'].replace("postgres://", "postgresql://", 1)
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024 # 16MB max-limit
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
app.config['REMEMBER_COOKIE_DURATION'] = 60 * 60 * 24 * 30 # 30 Days
app.config['PERMANENT_SESSION_LIFETIME'] = 60 * 60 * 24 * 30 # 30 Days

# Cloudinary Configuration (Ultra-Robust for Render)
try:
    c_url = os.getenv('CLOUDINARY_URL')
    if c_url and 'cloudinary://' in c_url:
        # Manually parse to be 100% sure
        clean_url = c_url.strip().replace('"', '').replace("'", "")
        # format: cloudinary://api_key:api_secret@cloud_name
        parts = clean_url.replace('cloudinary://', '').split('@')
        if len(parts) == 2:
            creds = parts[0].split(':')
            cloud_name = parts[1]
            if len(creds) == 2:
                cloudinary.config(
                    cloud_name = cloud_name,
                    api_key = creds[0],
                    api_secret = creds[1],
                    secure = True
                )
                print(f"Cloudinary Configured Manually for: {cloud_name}")
            else:
                cloudinary.config_from_url(clean_url)
        else:
            cloudinary.config_from_url(clean_url)
    else:
        cloudinary.config( 
          cloud_name = os.getenv('CLOUDINARY_CLOUD_NAME'), 
          api_key = os.getenv('CLOUDINARY_API_KEY'), 
          api_secret = os.getenv('CLOUDINARY_API_SECRET'),
          secure = True
        )
except Exception as e:
    print(f"Cloudinary Config Error: {e}")

# Payment Configuration
stripe.api_key = os.getenv('STRIPE_SECRET_KEY')
razor_client = razorpay.Client(auth=(os.getenv("RAZORPAY_KEY_ID", ""), os.getenv("RAZORPAY_KEY_SECRET", "")))

if not os.path.exists(app.config['UPLOAD_FOLDER']):
    os.makedirs(app.config['UPLOAD_FOLDER'])

# Email Configuration
app.config['MAIL_SERVER'] = os.getenv('MAIL_SERVER', 'smtp.gmail.com')
app.config['MAIL_PORT'] = int(os.getenv('MAIL_PORT', 587))
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = os.getenv('MAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.getenv('MAIL_PASSWORD')
app.config['MAIL_DEFAULT_SENDER'] = os.getenv('MAIL_DEFAULT_SENDER') or os.getenv('MAIL_USERNAME')


# VAPID Keys for Web Push
VAPID_PUBLIC_KEY = os.getenv('VAPID_PUBLIC_KEY', 'BMtQCEiMi5RXTf-67i7HyiJC1d-4eEVP_cKTr4MEKcVizTwnbkZXb5bcDJGA61RdiQILqAX9uYSi_296J_ANuqc')
VAPID_PRIVATE_KEY = os.getenv('VAPID_PRIVATE_KEY')
VAPID_CLAIM_EMAIL = os.getenv('MAIL_DEFAULT_SENDER', 'admin@cloudkitchen.com')

mail = Mail(app)
serializer = URLSafeTimedSerializer(app.config['SECRET_KEY'])

db.init_app(app)
try:
    with app.app_context():
        db.create_all()
except Exception as e:
    print(f"Startup DB Error: {e}")

socketio = SocketIO(app)

def send_notification(user_id, message, msg_type='info'):
    new_notif = Notification(user_id=user_id, message=message, type=msg_type)
    db.session.add(new_notif)
    db.session.commit()
    
    # Emit for real-time (SocketIO)
    socketio.emit('new_notification', {'message': message, 'type': msg_type}, room=f'user_{user_id}')
    
    # Send Web Push (Background)
    from pywebpush import webpush, WebPushException
    import json
    
    subscriptions = PushSubscription.query.filter_by(user_id=user_id).all()
    for sub in subscriptions:
        try:
            webpush(
                subscription_info={
                    "endpoint": sub.endpoint,
                    "keys": {"p256dh": sub.p256dh, "auth": sub.auth}
                },
                data=json.dumps({"title": "Cloud Kitchen", "body": message}),
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims={"sub": f"mailto:{VAPID_CLAIM_EMAIL}"}
            )
        except WebPushException as ex:
            print(f"Web Push Error: {ex}")
            if ex.response and ex.response.status_code == 410:
                db.session.delete(sub)
                db.session.commit()
        except Exception as e:
            print(f"Push notification error: {e}")

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

login_manager = LoginManager()
login_manager.login_view = 'login'
login_manager.init_app(app)

@login_manager.user_loader
def load_user(user_id):
    try:
        return User.query.get(int(user_id))
    except Exception as e:
        # If DB schema is out of sync (e.g. missing column), log them out safely instead of crashing
        print(f"load_user DB Error: {e}")
        return None

@app.route('/')
def index():
    if current_user.is_authenticated:
        if current_user.role == 'delivery':
            return redirect(url_for('delivery_dashboard'))
        elif current_user.role == 'seller':
            return redirect(url_for('seller_dashboard'))
            
    try:
        # Safe Query: Only show items if the table exists
        items = []
        recommendations = []
        search = request.args.get('search', '')
        is_veg = request.args.get('is_veg')
        max_price = request.args.get('max_price')
        category = request.args.get('category')
        
        try:
            query = FoodItem.query.join(User, FoodItem.seller_id == User.id).filter(User.is_open == True, FoodItem.quantity > 0)
            
            if search:
                query = query.filter(FoodItem.name.ilike(f'%{search}%'))
            if is_veg:
                query = query.filter(FoodItem.is_veg == (is_veg == 'true'))
            if max_price:
                query = query.filter(FoodItem.price <= float(max_price))
            if category:
                query = query.filter(FoodItem.category.ilike(category))
            
            # Sorting logic
            sort_by = request.args.get('sort_by', 'newest')
            if sort_by == 'price_low':
                query = query.order_by(FoodItem.price.asc())
            elif sort_by == 'price_high':
                query = query.order_by(FoodItem.price.desc())
            else:
                query = query.order_by(FoodItem.id.desc())

            all_filtered_items = query.all()
            
            # Use top 4 for recommendations, the rest for food_items
            recommendations = all_filtered_items[:4]
            items = all_filtered_items[4:]
            
        except Exception as db_err:
            print(f"DB Query Error: {db_err}")

        return render_template('index.html', food_items=items, recommendations=recommendations)
        
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"Index Critical Error: {error_details}")
        return f"<h1>Homepage Snag Found!</h1><p>Please copy this and tell Antigravity:</p><pre>{error_details}</pre>"

@app.route('/login', methods=['GET', 'POST'])
def login():
    """Step 1: Enter email or phone to receive OTP"""
    if request.method == 'POST':
        identifier = request.form.get('identifier', '').strip()
        
        # Search by email OR phone
        user = User.query.filter((User.email == identifier) | (User.phone == identifier)).first()
        
        if not user:
            flash('No account found with this email or phone number. Please register first.', 'danger')
            return render_template('login.html')

        # Generate and store OTP
        import random
        from datetime import datetime, timedelta
        otp = str(random.randint(100000, 999999))
        user.otp_code = otp
        user.otp_expiry = datetime.utcnow() + timedelta(minutes=10)
        db.session.commit()
        
        # Send OTP via Email (if email exists)
        try:
            if user.email:
                if not app.config['MAIL_USERNAME'] or not app.config['MAIL_PASSWORD']:
                    flash(f'🚨 EMAIL NOT CONFIGURED: Using Dev Mode OTP: {otp}', 'warning')
                else:
                    msg = Message(
                        subject="Your Cloud Kitchen Verification Code",
                        recipients=[user.email],
                        body=f"Hello {user.username},\n\nYour verification code is: {otp}\n\nValid for 10 minutes.",
                        sender=app.config['MAIL_DEFAULT_SENDER']
                    )
                    mail.send(msg)
                    flash(f'Verification code sent to your email: {user.email}', 'info')
            else:
                flash(f'Phone Login: Using Dev Mode OTP: {otp}', 'warning')
        except Exception as e:
            flash(f'[OTP NOT SENT] Please use this code: {otp}', 'danger')
        
        return redirect(url_for('verify_otp', email=user.email or user.phone))
    return render_template('login.html')



@app.route('/verify_otp', methods=['GET', 'POST'])
def verify_otp():
    """Step 2: Enter OTP to complete login"""
    from datetime import datetime
    email = request.args.get('email') or request.form.get('email')

    if request.method == 'POST':
        entered_otp = request.form.get('otp', '').strip()
        user = User.query.filter_by(email=email).first()

        if not user:
            flash('Session expired. Please try again.', 'danger')
            return redirect(url_for('login'))
        if user.otp_code == entered_otp and user.otp_expiry > datetime.utcnow():
            # Clear OTP after successful login
            user.otp_code = None
            user.otp_expiry = None
            db.session.commit()
            login_user(user, remember=True)
            flash(f'Welcome back, {user.username}!', 'success')
            if user.role == 'delivery':
                return redirect(url_for('delivery_dashboard'))
            elif user.role == 'seller':
                return redirect(url_for('seller_dashboard'))
            return redirect(url_for('index'))
        else:
            flash('Invalid or expired OTP. Please try again.', 'danger')
    return render_template('verify_otp.html', email=email)


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        phone = request.form.get('phone', '').strip()
        email = request.form.get('email', '').strip()
        role = request.form.get('role')
        location = request.form.get('location')

        # 1. Compulsory Email and Phone Check
        if not email or not phone:
            flash('Both Email and Contact Number are compulsory.', 'danger')
            return redirect(url_for('register'))

        # 2. Phone Number Validation (India format check)
        import re
        # Basic check for 10-digit Indian mobile numbers
        if not re.match(r'^[6-9]\d{9}$', phone) and not re.match(r'^\+91[6-9]\d{9}$', phone):
            flash('Please provide a valid Indian mobile number starting with 6-9.', 'danger')
            return redirect(url_for('register'))

        # Normalize phone
        if phone and not phone.startswith('+91'):
            phone = '+91' + phone.lstrip('0')

        # Check duplicates
        if User.query.filter_by(username=username).first():
            flash('This username is already taken. Please choose another one.', 'danger')
            return redirect(url_for('register'))
        if User.query.filter_by(phone=phone).first():
            flash('An account with this phone number already exists.', 'danger')
            return redirect(url_for('register'))
        if User.query.filter_by(email=email).first():
            flash('An account with this email already exists.', 'danger')
            return redirect(url_for('register'))

        new_user = User(
            username=username,
            phone=phone,
            email=email,
            password=None,
            role=role,
            location=location,
            is_verified=False # Start unverified, will verify via Email OTP
        )
        db.session.add(new_user)
        db.session.commit()
        
        # Trigger OTP for new user
        return redirect(url_for('login'))
    return render_template('register.html')


@app.route('/confirm_email/<token>')
def confirm_email(token):
    try:
        email = serializer.loads(token, salt='email-confirm', max_age=3600)
    except:
        flash('The confirmation link is invalid or has expired.', 'danger')
        return redirect(url_for('login'))

    user = User.query.filter_by(email=email).first_or_404()
    if user.is_verified:
        flash('Account already verified. Please login.', 'info')
    else:
        user.is_verified = True
        db.session.commit()
        flash('You have confirmed your account. Thanks!', 'success')
    return redirect(url_for('login'))

@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    if request.method == 'POST':
        # Update user profile
        current_user.username = request.form.get('username', current_user.username)
        current_user.email = request.form.get('email', '').strip() or current_user.email
        current_user.phone = request.form.get('phone', current_user.phone).strip()
        current_user.location = request.form.get('location', current_user.location)
        
        # Normalize phone
        if current_user.phone and not current_user.phone.startswith('+91'):
            current_user.phone = '+91' + current_user.phone.lstrip('0')
            
        # Handle profile image update
        file = request.files.get('profile_image')
        new_url = request.form.get('profile_image_url', '').strip()
        if file and file.filename != '' and allowed_file(file.filename):
            try:
                # FIXED: Pass the file object directly, not file.read()
                upload_result = cloudinary.uploader.upload(file)
                current_user.profile_image = upload_result['secure_url']
            except Exception as e:
                flash(f'Cloudinary Error: {e}', 'danger')

        elif new_url:
            current_user.profile_image = new_url
            
        db.session.commit()
        flash('Profile updated successfully!', 'success')
        return redirect(url_for('profile'))

    return render_template('profile.html')

@app.route('/rate_order/<int:order_id>', methods=['POST'])
@login_required
def rate_order(order_id):
    if current_user.role != 'customer':
        return redirect(url_for('index'))
        
    order = Order.query.get_or_404(order_id)
    if order.customer_id != current_user.id or order.status != 'Delivered':
        flash('You can only rate delivered orders.', 'warning')
        return redirect(url_for('orders'))
        
    # Rate Seller
    if request.form.get('seller_rating'):
        order.seller_rating = int(request.form.get('seller_rating'))
        order.seller_review = request.form.get('seller_review')
        # Upload seller review image
        seller_file = request.files.get('seller_review_image')
        if seller_file and seller_file.filename != '' and allowed_file(seller_file.filename):
            try:
                res = cloudinary.uploader.upload(seller_file.read())
                order.seller_review_image = res['secure_url']
            except Exception as e:
                print(f"Cloudinary Seller Review Upload Error: {e}")
                
    # Rate Delivery
    if request.form.get('delivery_rating'):
        order.delivery_rating = int(request.form.get('delivery_rating'))
        order.delivery_review = request.form.get('delivery_review')
        # Upload delivery review image
        delivery_file = request.files.get('delivery_review_image')
        if delivery_file and delivery_file.filename != '' and allowed_file(delivery_file.filename):
            try:
                res = cloudinary.uploader.upload(delivery_file.read())
                order.delivery_review_image = res['secure_url']
            except Exception as e:
                print(f"Cloudinary Delivery Review Upload Error: {e}")

    db.session.commit()
    flash('Thank you! Your ratings have been submitted.', 'success')
    return redirect(url_for('order_tracking', order_id=order.id))

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))

@app.route('/agree_to_terms', methods=['POST'])
@login_required
def agree_to_terms():
    if current_user.role == 'seller':
        current_user.has_agreed_to_terms = True
        current_user.terms_agreed_at = db.func.current_timestamp()
        db.session.commit()
    return jsonify({'success': True})

@app.route('/seller/dashboard')
@login_required
def seller_dashboard():
    if current_user.role != 'seller':
        return redirect(url_for('index'))
    menu_cat = request.args.get('menu_cat', '')
    if menu_cat:
        items = FoodItem.query.filter_by(seller_id=current_user.id, category=menu_cat).all()
    else:
        items = FoodItem.query.filter_by(seller_id=current_user.id).all()
    
    # Analytics - Showing Net Earnings
    try:
        total_revenue = db.session.query(func.sum(Order.seller_earnings)).\
            join(OrderItem).join(FoodItem).filter(FoodItem.seller_id == current_user.id).scalar() or 0
        
        popular_dishes = db.session.query(FoodItem.name, func.sum(OrderItem.quantity).label('total_sold')).\
            join(OrderItem).filter(FoodItem.seller_id == current_user.id).\
            group_by(FoodItem.id).order_by(func.sum(OrderItem.quantity).desc()).limit(5).all()
        
        order_stats = db.session.query(Order.status, func.count(Order.id)).\
            join(OrderItem).join(FoodItem).filter(FoodItem.seller_id == current_user.id).\
            group_by(Order.status).all()
    except Exception as e:
        print(f"Analytics Error: {e}")
        total_revenue = 0
        popular_dishes = []
        order_stats = []
    
    # Fetch active kitchen orders
    try:
        active_orders = Order.query.join(OrderItem).join(FoodItem).\
            filter(FoodItem.seller_id == current_user.id, Order.status.in_(['Paid', 'Preparing'])).all()
        active_orders = list(set(active_orders))
        active_orders.sort(key=lambda x: x.id, reverse=True)
    except:
        active_orders = []

    # Fetch handed over orders
    try:
        handed_over = Order.query.join(OrderItem).join(FoodItem).\
            filter(FoodItem.seller_id == current_user.id, Order.status.in_(['Out for Delivery', 'Delivered'])).all()
        handed_over = list(set(handed_over))
        handed_over.sort(key=lambda x: x.id, reverse=True)
        handed_over = handed_over[:5] # Show last 5
    except:
        handed_over = []

    return render_template('dashboard_seller.html', 
                           items=items, 
                           revenue=total_revenue, 
                           popular=popular_dishes,
                           stats=order_stats,
                           orders=active_orders,
                           handed_over=handed_over)

@app.route('/seller/toggle_status', methods=['POST'])
@login_required
def toggle_kitchen_status():
    if current_user.role == 'seller':
        current_user.is_open = not current_user.is_open
        db.session.commit()
        status = "Open" if current_user.is_open else "Closed"
        flash(f'Kitchen is now {status}!', 'info')
    return redirect(url_for('seller_dashboard'))

@app.route('/admin/dashboard')
@login_required
def admin_dashboard():
    if current_user.email != os.getenv('ADMIN_EMAIL', 'admin@cloudkitchen.com'):
        flash('Unauthorized access', 'danger')
        return redirect(url_for('index'))
        
    total_sales = db.session.query(func.sum(Order.total_amount)).scalar() or 0
    total_commission = db.session.query(func.sum(Order.platform_commission + Order.service_fee)).scalar() or 0
    sellers = User.query.filter_by(role='seller').all()
    recent_orders = Order.query.order_by(Order.id.desc()).limit(10).all()
    
    return render_template('dashboard_admin.html', 
                           sales=total_sales, 
                           commission=total_commission, 
                           sellers=sellers, 
                           orders=recent_orders)

@app.route('/seller/add_food', methods=['GET', 'POST'])
@login_required
def add_food():
    if current_user.role != 'seller':
        return redirect(url_for('index'))
    if request.method == 'POST':
        name = request.form.get('name')
        description = request.form.get('description')
        price = float(request.form.get('price'))
        quantity = int(request.form.get('quantity'))
        category = request.form.get('category')
        image_url = 'https://images.unsplash.com/photo-1546069901-ba9599a7e63c?w=500&h=500&fit=crop'
        
        # Handle File Upload → Cloudinary (Permanent Storage)
        if 'image_file' in request.files:
            file = request.files['image_file']
            if file and allowed_file(file.filename):
                try:
                    upload_result = cloudinary.uploader.upload(file)
                    image_url = upload_result['secure_url']
                except Exception as e:
                    print(f"Cloudinary Upload Error: {e}")
                    flash(f"📸 Image upload failed: {str(e)[:100]}", "warning")
        elif request.form.get('image_url'):
            image_url = request.form.get('image_url')
        
        is_veg = request.form.get('is_veg') == 'true'
        
        new_item = FoodItem(
            seller_id=current_user.id,
            name=name,
            description=description,
            price=price,
            quantity=quantity,
            category=category,
            is_veg=is_veg,
            image_url=image_url
        )
        db.session.add(new_item)
        db.session.commit()
        return redirect(url_for('seller_dashboard'))
    return render_template('add_food.html')

@app.route('/seller/edit_food/<int:item_id>', methods=['GET', 'POST'])
@login_required
def edit_food(item_id):
    if current_user.role != 'seller':
        return redirect(url_for('index'))
    item = FoodItem.query.get_or_404(item_id)
    if item.seller_id != current_user.id:
        return redirect(url_for('seller_dashboard'))
        
    if request.method == 'POST':
        item.name = request.form.get('name')
        item.description = request.form.get('description')
        item.price = float(request.form.get('price'))
        item.quantity = int(request.form.get('quantity'))
        item.category = request.form.get('category')
        
        # Handle Image Update
        file = request.files.get('image_file')
        new_url = request.form.get('image_url', '').strip()

        if file and file.filename != '' and allowed_file(file.filename):
            # User uploaded a new image file → send to Cloudinary
            try:
                file_bytes = file.read()  # Read into memory first
                upload_result = cloudinary.uploader.upload(file_bytes)
                item.image_url = upload_result['secure_url']
            except Exception as e:
                error_msg = str(e)
                print(f"Cloudinary Edit Error: {error_msg}")
                flash(f'Cloudinary Error: {error_msg[:200]}', 'danger')
        elif new_url:
            # User pasted a new image URL
            item.image_url = new_url
        # else: keep existing image
        db.session.commit()
        flash('Dish updated successfully!', 'success')
        return redirect(url_for('seller_dashboard'))
    return render_template('edit_food.html', item=item)

@app.route('/seller/delete_food/<int:item_id>', methods=['POST'])
@login_required
def delete_food(item_id):
    if current_user.role != 'seller':
        return redirect(url_for('index'))
    item = FoodItem.query.get_or_404(item_id)
    if item.seller_id == current_user.id:
        db.session.delete(item)
        db.session.commit()
        flash('Dish deleted successfully!', 'success')
    return redirect(url_for('seller_dashboard'))

@app.route('/food/<int:item_id>', methods=['GET', 'POST'])
def food_detail(item_id):
    item = FoodItem.query.get_or_404(item_id)
    reviews = Review.query.filter_by(food_item_id=item.id).order_by(Review.id.desc()).all()
    
    # Check if current user has ordered this item and it was delivered
    can_review = False
    if current_user.is_authenticated:
        delivered_orders = Order.query.filter_by(customer_id=current_user.id, status='Delivered').all()
        for order in delivered_orders:
            for o_item in order.items:
                if o_item.food_item_id == item.id:
                    can_review = True
                    break
            if can_review: break

    if request.method == 'POST' and current_user.is_authenticated:
        if not can_review:
            flash('You can only review items you have ordered and received.', 'warning')
            return redirect(url_for('food_detail', item_id=item.id))
            
        if 'rating' in request.form:

            # Submit review
            rating = int(request.form.get('rating'))
            comment = request.form.get('comment')
            image_url = None
            
            if 'review_image' in request.files:
                file = request.files['review_image']
                if file and allowed_file(file.filename):
                    try:
                        upload_result = cloudinary.uploader.upload(file)
                        image_url = upload_result['secure_url']
                    except Exception as e:
                        print(f"Cloudinary Review Upload Error: {e}")
            
            review = Review(
                customer_id=current_user.id, 
                food_item_id=item.id, 
                rating=rating, 
                comment=comment,
                image_url=image_url
            )
            db.session.add(review)
            db.session.commit()
            flash('Review added successfully!', 'success')
            return redirect(url_for('food_detail', item_id=item.id))
            
    return render_template('food_detail.html', item=item, reviews=reviews, can_review=can_review)


@app.route('/cart/add/<int:item_id>', methods=['POST'])
@login_required
def add_to_cart(item_id):
    from models import CartItem
    if current_user.role != 'customer':
        return jsonify({'error': 'Only customers can add to cart'}), 403
    quantity = int(request.form.get('quantity', 1))
    
    existing = CartItem.query.filter_by(customer_id=current_user.id, food_item_id=item_id).first()
    if existing:
        existing.quantity += quantity
    else:
        new_cart_item = CartItem(customer_id=current_user.id, food_item_id=item_id, quantity=quantity)
        db.session.add(new_cart_item)
    db.session.commit()
    flash('Added to cart!', 'success')
    return redirect(url_for('index'))

@app.route('/cart')
@login_required
def view_cart():
    if current_user.role != 'customer':
        return redirect(url_for('index'))
    cart_items = CartItem.query.filter_by(customer_id=current_user.id).all()
    total = sum(item.quantity * FoodItem.query.get(item.food_item_id).price for item in cart_items)
    
    detailed_items = []
    for c in cart_items:
        f = FoodItem.query.get(c.food_item_id)
        detailed_items.append({'cart_item': c, 'food': f, 'subtotal': c.quantity * f.price})
        
    return render_template('cart.html', items=detailed_items, total=total)

@app.route('/cart/remove/<int:cart_id>', methods=['POST'])
@login_required
def remove_from_cart(cart_id):
    c = CartItem.query.get_or_404(cart_id)
    if c.customer_id == current_user.id:
        db.session.delete(c)
        db.session.commit()
    return redirect(url_for('view_cart'))

@app.route('/update_cart/<int:item_id>', methods=['POST'])
@login_required
def update_cart(item_id):
    item = CartItem.query.get_or_404(item_id)
    if item.customer_id != current_user.id:
        return redirect(url_for('view_cart'))
        
    action = request.form.get('action')
    if action == 'increase':
        item.quantity += 1
    elif action == 'decrease':
        if item.quantity > 1:
            item.quantity -= 1
        else:
            db.session.delete(item)
            
    db.session.commit()
    return redirect(url_for('view_cart'))


@app.route('/checkout', methods=['GET', 'POST'])
@login_required
def checkout():
    if current_user.role != 'customer':
        flash('Only customers can place orders.', 'warning')
        return redirect(url_for('index'))
        
    cart_items = CartItem.query.filter_by(customer_id=current_user.id).all()
    if not cart_items:
        flash('Your cart is empty', 'warning')
        return redirect(url_for('index'))
        
    item_total = sum(item.quantity * FoodItem.query.get(item.food_item_id).price for item in cart_items)
    delivery_fee = 5.0
    service_fee = 2.0
    total = item_total + delivery_fee + service_fee
    platform_commission = item_total * 0.20 # 20% commission on food
        
    if request.method == 'POST':
        payment_method = request.form.get('payment_method', 'online')
        
        flat = request.form.get('flat', '')
        street = request.form.get('street', '')
        landmark = request.form.get('landmark', '')
        delivery_address = f"{flat}, {street}" + (f", near {landmark}" if landmark else "")
        target_lat = request.form.get('target_lat')
        target_lng = request.form.get('target_lng')

        if payment_method == 'cod':
            # Create Order Immediately for COD
            seller_earnings = item_total - platform_commission
            delivery_earnings = delivery_fee

            new_order = Order(
                customer_id=current_user.id,
                total_amount=total,
                delivery_fee=delivery_fee,
                service_fee=service_fee,
                platform_commission=platform_commission,
                seller_earnings=seller_earnings,
                delivery_earnings=delivery_earnings,
                status='Paid', # For COD, we treat it as 'Pending' or 'Paid' based on flow, but let's say 'Paid' for simplified dashboard logic, or add a 'COD' status.
                delivery_address=delivery_address,
                delivery_target_lat=float(target_lat) if target_lat else None,
                delivery_target_lng=float(target_lng) if target_lng else None,
                is_cod=True # I should add this column or use status
            )
            db.session.add(new_order)
            db.session.flush()

            for c in cart_items:
                f = FoodItem.query.get(c.food_item_id)
                oi = OrderItem(order_id=new_order.id, food_item_id=f.id, quantity=c.quantity, price=f.price)
                f.quantity -= c.quantity
                db.session.add(oi)
                db.session.delete(c)

            db.session.commit()
            
            # Notify
            socketio.emit('new_order_alert', {'message': f'New COD Order #{new_order.id}'}, room='sellers')
            flash(f'Order #{new_order.id} placed successfully (Cash on Delivery)!', 'success')
            return redirect(url_for('order_tracking', order_id=new_order.id))

        else:
            # Razorpay / Online Flow
            try:
                razor_order = razor_client.order.create({
                    "amount": int(total * 100), # paise
                    "currency": "INR",
                    "payment_capture": 1
                })
                
                # We return the order details to frontend to trigger Razorpay checkout
                return render_template('checkout.html', 
                                     total=total, 
                                     item_total=item_total, 
                                     delivery_fee=delivery_fee, 
                                     service_fee=service_fee,
                                     razorpay_order_id=razor_order['id'],
                                     razorpay_key_id=os.getenv("RAZORPAY_KEY_ID"),
                                     delivery_address=delivery_address,
                                     lat=target_lat,
                                     lng=target_lng)
            except Exception as e:
                flash(f"Payment Error: {str(e)}", "danger")
                return redirect(url_for('checkout'))

    return render_template('checkout.html', total=total, item_total=item_total, delivery_fee=delivery_fee, service_fee=service_fee)

@app.route('/razorpay_verify', methods=['POST'])
@login_required
def razorpay_verify():
    try:
        data = request.json
        params_dict = {
            'razorpay_order_id': data.get('razorpay_order_id'),
            'razorpay_payment_id': data.get('razorpay_payment_id'),
            'razorpay_signature': data.get('razorpay_signature')
        }

        # Verify signature
        razor_client.utility.verify_payment_signature(params_dict)
        
        # If verification successful, create order (Same as payment_success logic)
        cart_items = CartItem.query.filter_by(customer_id=current_user.id).all()
        item_total = sum(item.quantity * FoodItem.query.get(item.food_item_id).price for item in cart_items)
        delivery_fee = 5.0
        service_fee = 2.0
        total = item_total + delivery_fee + service_fee
        platform_commission = item_total * 0.20
        seller_earnings = item_total - platform_commission
        delivery_earnings = delivery_fee

        new_order = Order(
            customer_id=current_user.id,
            total_amount=total,
            delivery_fee=delivery_fee,
            service_fee=service_fee,
            platform_commission=platform_commission,
            seller_earnings=seller_earnings,
            delivery_earnings=delivery_earnings,
            status='Paid',
            delivery_address=data.get('addr'),
            delivery_target_lat=float(data.get('lat')) if data.get('lat') else None,
            delivery_target_lng=float(data.get('lng')) if data.get('lng') else None
        )
        db.session.add(new_order)
        db.session.flush()

        for c in cart_items:
            f = FoodItem.query.get(c.food_item_id)
            oi = OrderItem(order_id=new_order.id, food_item_id=f.id, quantity=c.quantity, price=f.price)
            f.quantity -= c.quantity
            db.session.add(oi)
            db.session.delete(c)

        db.session.commit()
        
        socketio.emit('new_order_alert', {'message': f'New Order #{new_order.id}'}, room='sellers')
        
        return jsonify({'status': 'success', 'order_id': new_order.id})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 400

@app.route('/collect_cod_payment/<int:order_id>', methods=['POST'])
@login_required
def collect_cod_payment(order_id):
    """Generate a Razorpay order for a COD shipment at the door"""
    order = Order.query.get_or_404(order_id)
    if current_user.role != 'delivery' or order.delivery_person_id != current_user.id:
        return jsonify({'error': 'Unauthorized'}), 403
    
    try:
        razor_order = razor_client.order.create({
            "amount": int(order.total_amount * 100),
            "currency": "INR",
            "payment_capture": 1
        })
        return jsonify({
            'razorpay_order_id': razor_order['id'],
            'razorpay_key_id': os.getenv("RAZORPAY_KEY_ID"),
            'total': order.total_amount,
            'order_id': order.id
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 400



@app.route('/checkout/debug_bypass', methods=['POST'])
@login_required
def debug_checkout_bypass():
    """Developer Bypass: Create order without Stripe"""
    cart_items = CartItem.query.filter_by(customer_id=current_user.id).all()
    if not cart_items:
        return redirect(url_for('index'))

    # Same logic as payment_success
    item_total = sum(item.quantity * FoodItem.query.get(item.food_item_id).price for item in cart_items)
    delivery_fee = 5.0
    service_fee = 2.0
    total = item_total + delivery_fee + service_fee
    platform_commission = item_total * 0.20
    
    seller_earnings = item_total - platform_commission
    delivery_earnings = delivery_fee

    flat = request.form.get('flat', 'Dev House')
    street = request.form.get('street', 'Bypass St')
    delivery_address = f"{flat}, {street}"

    new_order = Order(
        customer_id=current_user.id,
        total_amount=total,
        delivery_fee=delivery_fee,
        service_fee=service_fee,
        platform_commission=platform_commission,
        seller_earnings=seller_earnings,
        delivery_earnings=delivery_earnings,
        status='Paid',
        delivery_address=delivery_address,
        delivery_target_lat=float(request.form.get('target_lat', 0)),
        delivery_target_lng=float(request.form.get('target_lng', 0))
    )
    db.session.add(new_order)
    db.session.flush()

    for c in cart_items:
        f = FoodItem.query.get(c.food_item_id)
        oi = OrderItem(order_id=new_order.id, food_item_id=f.id, quantity=c.quantity, price=f.price)
        f.quantity -= c.quantity
        db.session.add(oi)
        db.session.delete(c)

    db.session.commit()
    
    # LIVE ALERT: Notify all sellers and delivery people of new order
    socketio.emit('new_order_alert', {'message': 'New order placed!'}, room='sellers')
    socketio.emit('new_order_alert', {'message': 'New delivery available!'}, room='delivery')
    
    flash('Developer Bypass: Order created successfully!', 'info')
    return redirect(url_for('order_tracking', order_id=new_order.id))

@app.route('/payment_success')
@login_required
def payment_success():
    cart_items = CartItem.query.filter_by(customer_id=current_user.id).all()
    if not cart_items:
        return redirect(url_for('index'))

    # Financial Logic: Automated Revenue Splitting
    item_total = sum(item.quantity * FoodItem.query.get(item.food_item_id).price for item in cart_items)
    delivery_fee = 5.0
    service_fee = 2.0
    total = item_total + delivery_fee + service_fee
    platform_commission = item_total * 0.20  # 20% from food
    seller_earnings = item_total - platform_commission  # Seller gets 80%
    delivery_earnings = delivery_fee  # Rider gets full delivery fee


    new_order = Order(
        customer_id=current_user.id,
        total_amount=total,
        delivery_fee=delivery_fee,
        service_fee=service_fee,
        platform_commission=platform_commission,
        seller_earnings=seller_earnings,
        delivery_earnings=delivery_earnings,
        status='Paid',
        delivery_address=request.args.get('addr'),
        delivery_target_lat=float(request.args.get('lat')) if request.args.get('lat') else None,
        delivery_target_lng=float(request.args.get('lng')) if request.args.get('lng') else None
    )
    db.session.add(new_order)
    db.session.flush()

    for c in cart_items:
        f = FoodItem.query.get(c.food_item_id)
        oi = OrderItem(order_id=new_order.id, food_item_id=f.id, quantity=c.quantity, price=f.price)
        f.quantity -= c.quantity
        db.session.add(oi)
        db.session.delete(c)

    db.session.commit()
    
    # LIVE ALERT: Notify all sellers and delivery people of new order
    socketio.emit('new_order_alert', {'message': f'New order #{new_order.id} placed! ₹{total:.0f}'}, room='sellers')
    socketio.emit('new_order_alert', {'message': f'New delivery available! Earn ₹{delivery_earnings:.0f}'}, room='delivery')
    
    flash(f'Payment successful! Order #{new_order.id} placed. Kitchen gets ₹{seller_earnings:.2f}, Rider gets ₹{delivery_earnings:.2f}.', 'success')
    return redirect(url_for('order_tracking', order_id=new_order.id))

@app.route('/orders')
@login_required
def orders():
    if current_user.role == 'customer':
        orders = Order.query.filter_by(customer_id=current_user.id).order_by(Order.id.desc()).all()
    elif current_user.role == 'seller':
        orders_q = Order.query.join(OrderItem).join(FoodItem).filter(FoodItem.seller_id == current_user.id).all()
        orders = list(set(orders_q))
        orders.sort(key=lambda x: x.id, reverse=True)
    else:
        orders = Order.query.order_by(Order.id.desc()).all()
    return render_template('orders.html', orders=orders)

@app.route('/order_tracking/<int:order_id>')
@login_required
def order_tracking(order_id):
    order = Order.query.get_or_404(order_id)
    if current_user.role == 'customer' and order.customer_id != current_user.id:
        return redirect(url_for('index'))
    return render_template('order_tracking.html', order=order)

@app.route('/update_order_status/<int:order_id>', methods=['POST'])
@login_required
def update_order_status(order_id):
    order = Order.query.get_or_404(order_id)
    if current_user.role in ['seller', 'delivery']:
        new_status = request.form.get('status')
        order.status = new_status
        db.session.commit()
        
        # BROADCAST to everyone to force dashboard refreshes
        socketio.emit('status_change', {
            'order_id': order.id,
            'status': new_status,
            'customer_id': order.customer_id
        })
        
        # Save to DB Notification (Wrapped in Try to prevent blocking the status update)
        try:
            send_notification(order.customer_id, f"Your order #{order.id} is now: {new_status}", 'success')
        except Exception as e:
            print(f"Notification Error: {e}")
        
    if current_user.role == 'delivery':
        return redirect(url_for('delivery_dashboard'))
    return redirect(url_for('orders'))

@app.route('/delivery/dashboard')
@login_required
def delivery_dashboard():
    if current_user.role != 'delivery':
        return redirect(url_for('index'))
    # Orders available for delivery
    available_orders = Order.query.filter_by(delivery_person_id=None, status='Preparing').all()
    
    # Active orders (claimed but not delivered)
    active_orders = Order.query.filter(Order.delivery_person_id == current_user.id, Order.status != 'Delivered').all()
    
    # Completed orders
    delivered_orders = Order.query.filter_by(delivery_person_id=current_user.id, status='Delivered').limit(5).all()
    
    total_earnings = db.session.query(func.sum(Order.delivery_earnings)).filter_by(delivery_person_id=current_user.id, status='Delivered').scalar() or 0
    
    return render_template('dashboard_delivery.html', 
                           available_orders=available_orders, 
                           my_orders=active_orders, 
                           delivered_orders=delivered_orders,
                           earnings=total_earnings)

@app.route('/delivery/claim/<int:order_id>')
@login_required
def claim_order(order_id):
    if current_user.role != 'delivery':
        return redirect(url_for('index'))
    order = Order.query.get_or_404(order_id)
    if order.delivery_person_id is None:
        order.delivery_person_id = current_user.id
        order.status = 'Out for Delivery'
        db.session.commit()
        flash('Order claimed successfully!', 'success')
    return redirect(url_for('delivery_dashboard'))

@app.route('/api/update_location/<int:order_id>', methods=['POST'])
@login_required
def update_location(order_id):
    if current_user.role != 'delivery':
        return jsonify({'error': 'Unauthorized'}), 403
    order = Order.query.get_or_404(order_id)
    if order.delivery_person_id != current_user.id:
        return jsonify({'error': 'Unauthorized'}), 403
        
    data = request.json
    order.lat = data.get('lat')
    order.lng = data.get('lng')
    db.session.commit()
    return jsonify({'success': True})

@app.route('/api/get_location/<int:order_id>')
def get_location(order_id):
    order = Order.query.get_or_404(order_id)
    return jsonify({
        'lat': order.lat,
        'lng': order.lng,
        'target_lat': order.delivery_target_lat,
        'target_lng': order.delivery_target_lng,
        'status': order.status,
        'delivery_address': order.delivery_address
    })

@app.route('/api/subscribe', methods=['POST'])
@login_required
def subscribe():
    data = request.json
    subscription = data.get('subscription')
    
    if not subscription:
        return jsonify({'error': 'No subscription data'}), 400
        
    # Check if subscription already exists
    existing = PushSubscription.query.filter_by(
        endpoint=subscription['endpoint'], 
        user_id=current_user.id
    ).first()
    
    if not existing:
        new_sub = PushSubscription(
            user_id=current_user.id,
            endpoint=subscription['endpoint'],
            p256dh=subscription['keys']['p256dh'],
            auth=subscription['keys']['auth']
        )
        db.session.add(new_sub)
        db.session.commit()
        
    return jsonify({'success': True})

@app.errorhandler(500)
def handle_500(e):
    import traceback
    return f"<h1>Global Error Caught!</h1><pre>{traceback.format_exc()}</pre>", 500

@app.route('/secret_debug_error/')
@app.route('/secret_debug_error')

def secret_debug_error():
    import traceback
    try:
        # Test DB connection
        db.session.execute(text('SELECT 1'))
        return "Database Connection: OK! <br> Tables: " + str(db.metadata.tables.keys())
    except Exception as e:
        return f"<h1>Real Error Found:</h1><pre>{traceback.format_exc()}</pre>"

@app.route('/debug_sms/')
@app.route('/debug_sms')


def debug_sms():
    server_name = os.getenv("RENDER_SERVICE_NAME", "Local")
    key = os.getenv('FAST2SMS_API_KEY')
    
    # Check actual DB columns for 'user' table
    columns = []
    try:
        res = db.session.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name = 'user'"))
        columns = [row[0] for row in res]
    except Exception as e:
        columns = [f"Error checking columns: {e}"]

    if key:
        masked_key = key[:5] + "..." + key[-5:] if len(key) > 10 else "TOO SHORT"
        return f"✅ SUCCESS! Server: {server_name}<br>Found FAST2SMS_API_KEY: {masked_key}<br>User Columns: {columns}"
    else:
        return f"❌ ERROR! Server: {server_name}<br>The server cannot see FAST2SMS_API_KEY.<br>User Columns: {columns}"

@app.route('/secret_db_migrate/')
@app.route('/secret_db_migrate')

def secret_db_migrate():
    server_name = os.getenv("RENDER_SERVICE_NAME", "Local")
    # Helper to add columns to Order and User tables
    results = []
    try:
        queries = [
            # User table migrations
            'ALTER TABLE "user" ADD COLUMN phone VARCHAR(15);',
            'ALTER TABLE "user" ADD COLUMN otp_code VARCHAR(6);',
            'ALTER TABLE "user" ADD COLUMN otp_expiry TIMESTAMP;',
            # Cleanup: Provide dummy phone for existing users so we can set NOT NULL
            "UPDATE \"user\" SET phone = '+910000000000' WHERE phone IS NULL;",
            # Make columns mandatory in DB
            'ALTER TABLE "user" ALTER COLUMN email SET NOT NULL;',
            'ALTER TABLE "user" ALTER COLUMN phone SET NOT NULL;',
            'ALTER TABLE "user" ALTER COLUMN password DROP NOT NULL;',
            # Order table migrations


            'ALTER TABLE "order" ADD COLUMN seller_rating INTEGER;',
            'ALTER TABLE "order" ADD COLUMN seller_review TEXT;',
            'ALTER TABLE "order" ADD COLUMN seller_review_image VARCHAR(250);',
            'ALTER TABLE "order" ADD COLUMN delivery_rating INTEGER;',
            'ALTER TABLE "order" ADD COLUMN delivery_review TEXT;',
            'ALTER TABLE "order" ADD COLUMN delivery_review_image VARCHAR(250);',
            'ALTER TABLE "order" ADD COLUMN payment_method VARCHAR(50) DEFAULT \'online\';',
            'ALTER TABLE "order" ADD COLUMN is_cod BOOLEAN DEFAULT FALSE;'
        ]

        for q in queries:
            try:
                db.session.execute(text(q))
                db.session.commit()
                results.append(f"✅ Success: {q}")
            except Exception as inner_e:
                db.session.rollback()
                results.append(f"❌ Failed: {q} | Error: {str(inner_e)[:100]}")
        
        # Verify columns
        res = db.session.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name = 'user'"))
        cols = [row[0] for row in res]
        
        return f"<h3>Migration Report (Server: {server_name})</h3><ul>" + "".join([f"<li>{r}</li>" for r in results]) + f"</ul><p>Current Columns in 'user': {cols}</p>"
    except Exception as e:
        import traceback
        return f"Migration Critical Failure: <pre>{traceback.format_exc()}</pre>"

@app.route('/debug_db_status')
def debug_db_status():
    uri = app.config['SQLALCHEMY_DATABASE_URI']
    db_type = "POSTGRES" if "postgresql" in uri else "SQLITE (TEMPORARY)"
    
    # Check Cloudinary
    c_url = os.getenv('CLOUDINARY_URL', 'NOT FOUND')
    c_status = "Configured ✅" if c_url != 'NOT FOUND' and 'cloudinary://' in c_url else "NOT CONFIGURED ❌"
    
    # Try a fake ping to Cloudinary API
    ping_result = "Not Tested"
    try:
        from cloudinary.api import ping
        ping().get('status')
        ping_result = "API PING SUCCESSFUL ✅"
    except Exception as e:
        ping_result = f"API PING FAILED ❌: {str(e)}"

    try:
        user_count = User.query.count()
        food_count = FoodItem.query.count()
        return f"""
        <h1>System Status</h1>
        <p><b>Database:</b> {db_type} (Users: {user_count}, Dishes: {food_count})</p>
        <p><b>Cloudinary (Images):</b> {c_status}</p>
        <p><b>Cloudinary API Ping:</b> {ping_result}</p>
        <hr>
        <p><b>Debug Cloudinary URL (Masked):</b> {c_url[:15]}...{c_url[-5:] if len(c_url)>10 else ''}</p>
        <p><i>If Ping fails, check if your API Secret is correct in the URL.</i></p>
        """
    except Exception as e:
        return f"Error: {str(e)}"

if __name__ == '__main__':

    socketio.run(app, debug=True, host='0.0.0.0')
