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
            # If the subscription is expired, delete it
            if ex.response and ex.response.status_code == 410:
                db.session.delete(sub)
                db.session.commit()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'super-secret-key-for-cloud-kitchen')
# Use PostgreSQL on Render, SQLite locally
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL', 'sqlite:///cloud_kitchen_v3.db')
if app.config['SQLALCHEMY_DATABASE_URI'].startswith("postgres://"):
    app.config['SQLALCHEMY_DATABASE_URI'] = app.config['SQLALCHEMY_DATABASE_URI'].replace("postgres://", "postgresql://", 1)
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024 # 16MB max-limit
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

# Cloudinary Configuration
cloudinary_url = os.getenv('CLOUDINARY_URL')
if cloudinary_url:
    cloudinary.config_from_url(cloudinary_url.strip())
else:
    cloudinary.config( 
      cloud_name = os.getenv('CLOUDINARY_CLOUD_NAME'), 
      api_key = os.getenv('CLOUDINARY_API_KEY'), 
      api_secret = os.getenv('CLOUDINARY_API_SECRET') 
    )

if not os.path.exists(app.config['UPLOAD_FOLDER']):
    os.makedirs(app.config['UPLOAD_FOLDER'])

# Email Configuration
app.config['MAIL_SERVER'] = os.getenv('MAIL_SERVER', 'smtp.gmail.com')
app.config['MAIL_PORT'] = int(os.getenv('MAIL_PORT', 587))
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = os.getenv('MAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.getenv('MAIL_PASSWORD')
app.config['MAIL_DEFAULT_SENDER'] = os.getenv('MAIL_DEFAULT_SENDER')

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

# PRO TIP: Move this to a .env file for production security
stripe.api_key = os.getenv('STRIPE_SECRET_KEY', 'sk_test_51Px9XBRp1zG6L2eFm6f6f6f6f6f6f6f6') 

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

login_manager = LoginManager()
login_manager.login_view = 'login'
login_manager.init_app(app)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

@app.route('/')
def index():
    if current_user.is_authenticated:
        if current_user.role == 'delivery':
            return redirect(url_for('delivery_dashboard'))
        elif current_user.role == 'seller':
            return redirect(url_for('seller_dashboard'))
            
    try:
        search = request.args.get('search', '')
        is_veg = request.args.get('is_veg')
        
        # Safe Query: Only show items if the table exists
        items = []
        recommendations = []
        
        try:
            query = FoodItem.query.join(User, FoodItem.seller_id == User.id).filter(User.is_open == True, FoodItem.quantity > 0)
            if search:
                query = query.filter(FoodItem.name.ilike(f'%{search}%'))
            if is_veg == 'true':
                query = query.filter(FoodItem.is_veg == True)
            
            items = query.all()
            recommendations = FoodItem.query.filter(FoodItem.quantity > 0).limit(4).all()
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
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        user = User.query.filter_by(email=email).first()
        if user and check_password_hash(user.password, password):
            login_user(user)
            if user.role == 'seller':
                return redirect(url_for('seller_dashboard'))
            return redirect(url_for('index'))
        else:
            flash('Login failed. Check your email and password.', 'danger')
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')
        role = request.form.get('role')
        location = request.form.get('location')
        
        # 1. Strict Email Validation (Regex)
        email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        if not re.match(email_pattern, email):
            flash('Invalid email format. Please enter a valid email address.', 'danger')
            return redirect(url_for('register'))

        user = User.query.filter_by(email=email).first()
        if user:
            flash('Email address already exists', 'danger')
            return redirect(url_for('register'))
            
        new_user = User(
            username=username, 
            email=email, 
            password=generate_password_hash(password, method='pbkdf2:sha256'), 
            role=role,
            location=location,
            is_verified=True # Simple: Auto-verified
        )
        db.session.add(new_user)
        db.session.commit()
        flash('Account created! You can now log in.', 'success')
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
    
    return render_template('dashboard_seller.html', 
                           items=items, 
                           revenue=total_revenue, 
                           popular=popular_dishes,
                           stats=order_stats)

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
        
        # Handle File Upload
        if 'image_file' in request.files:
            file = request.files['image_file']
            if file and allowed_file(file.filename):
                filename = secure_filename(file.filename)
                # Add timestamp to filename to avoid collisions
                filename = f"{int(os.path.getmtime('app.py'))}_{filename}"
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
                image_url = url_for('static', filename=f'uploads/{filename}')
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
        
        # Handle File Upload
        if 'image_file' in request.files:
            file = request.files['image_file']
            if file and allowed_file(file.filename):
                try:
                    upload_result = cloudinary.uploader.upload(file)
                    item.image_url = upload_result['secure_url']
                except Exception as e:
                    print(f"Cloudinary Error: {e}")
                    filename = secure_filename(f"{int(time.time())}_{file.filename}")
                    file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
                    item.image_url = url_for('static', filename=f'uploads/{filename}')
        elif request.form.get('image_url'):
            item.image_url = request.form.get('image_url')
        
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
    reviews = Review.query.filter_by(food_item_id=item.id).all()
    
    if request.method == 'POST' and current_user.is_authenticated:
        if 'rating' in request.form:
            # Submit review
            rating = int(request.form.get('rating'))
            comment = request.form.get('comment')
            image_url = None
            
            if 'review_image' in request.files:
                file = request.files['review_image']
                if file and allowed_file(file.filename):
                    filename = secure_filename(file.filename)
                    filename = f"rev_{int(os.path.getmtime('app.py'))}_{filename}"
                    file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
                    image_url = url_for('static', filename=f'uploads/{filename}')
            
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
            
    return render_template('food_detail.html', item=item, reviews=reviews)

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
        # Create Stripe Checkout Session
        try:
            flat = request.form.get('flat', '')
            street = request.form.get('street', '')
            landmark = request.form.get('landmark', '')
            delivery_address = f"{flat}, {street}" + (f", near {landmark}" if landmark else "")
            target_lat = request.form.get('target_lat')
            target_lng = request.form.get('target_lng')

            # Create session
            checkout_session = stripe.checkout.Session.create(
                payment_method_types=['card'],
                line_items=[{
                    'price_data': {
                        'currency': 'inr',
                        'unit_amount': int(total * 100),
                        'product_data': {'name': 'Cloud Kitchen Order'},
                    },
                    'quantity': 1,
                }],
                mode='payment',
                success_url=url_for('payment_success', _external=True) + 
                            f"?lat={target_lat}&lng={target_lng}&addr={delivery_address}",
                cancel_url=url_for('checkout', _external=True),
            )
            return redirect(checkout_session.url, code=303)
        except Exception as e:
            flash(f"Payment Error: {str(e)}", "danger")
            return redirect(url_for('checkout'))

    return render_template('checkout.html', total=total, item_total=item_total, delivery_fee=delivery_fee, service_fee=service_fee)

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
    # Customer pays = Food Total + Delivery Fee + Service Fee
    # Seller gets = Food Total - 20% Platform Commission
    # Delivery Rider gets = Delivery Fee
    # Platform gets = 20% Commission + Service Fee
    
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
    socketio.emit('new_order_alert', {'message': 'New order placed!'}, room='sellers')
    socketio.emit('new_order_alert', {'message': 'New delivery available!'}, room='delivery')
    
    flash('Payment successful! Your order has been split between the kitchen and delivery partner.', 'success')
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
        }, broadcast=True)
        
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

@app.route('/secret_debug_error')
def secret_debug_error():
    import traceback
    try:
        # Test DB connection
        db.session.execute(text('SELECT 1'))
        return "Database Connection: OK! <br> Tables: " + str(db.metadata.tables.keys())
    except Exception as e:
        return f"<h1>Real Error Found:</h1><pre>{traceback.format_exc()}</pre>"

if __name__ == '__main__':
    socketio.run(app, debug=True, host='0.0.0.0')
