from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin

db = SQLAlchemy()

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    phone = db.Column(db.String(15), unique=True, nullable=False)  # Primary login
    email = db.Column(db.String(150), unique=True, nullable=True)  # Optional
    password = db.Column(db.String(150), nullable=True)  # Optional now
    role = db.Column(db.String(50), nullable=False) # customer, seller, delivery
    location = db.Column(db.String(250))
    lat = db.Column(db.Float, nullable=True)
    lng = db.Column(db.Float, nullable=True)
    has_agreed_to_terms = db.Column(db.Boolean, default=False)
    terms_agreed_at = db.Column(db.DateTime)
    is_open = db.Column(db.Boolean, default=True) # For Sellers
    is_verified = db.Column(db.Boolean, default=False)
    profile_image = db.Column(db.String(250), default='https://cdn-icons-png.flaticon.com/512/3135/3135715.png')
    # OTP Fields
    otp_code = db.Column(db.String(6), nullable=True)
    otp_expiry = db.Column(db.DateTime, nullable=True)
    
    # Seller Ratings (Aggregated)
    avg_rating = db.Column(db.Float, default=0.0)
    total_reviews = db.Column(db.Integer, default=0)
    
    # Financials
    wallet_balance = db.Column(db.Float, default=0.0)

class FoodItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    seller_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    name = db.Column(db.String(150), nullable=False)
    description = db.Column(db.Text, nullable=False)
    price = db.Column(db.Float, nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    category = db.Column(db.String(50), nullable=False) # organic, vegetarian, non-vegetarian
    is_veg = db.Column(db.Boolean, default=True)
    image_url = db.Column(db.String(250))
    avg_rating = db.Column(db.Float, default=0.0)
    total_reviews = db.Column(db.Integer, default=0)
    
    seller = db.relationship('User', backref='food_items', lazy=True)

class CartItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    food_item_id = db.Column(db.Integer, db.ForeignKey('food_item.id'), nullable=False)
    quantity = db.Column(db.Integer, nullable=False, default=1)

class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    total_amount = db.Column(db.Float, nullable=False)
    delivery_fee = db.Column(db.Float, default=0.0)
    service_fee = db.Column(db.Float, default=0.0)
    platform_commission = db.Column(db.Float, default=0.0) # Platform's 20% cut from food
    seller_earnings = db.Column(db.Float, default=0.0)    # What the cook gets
    delivery_earnings = db.Column(db.Float, default=0.0)  # What the rider gets
    status = db.Column(db.String(50), nullable=False, default='Pending') # Pending, Paid, Preparing, Out for Delivery, Delivered
    delivery_address = db.Column(db.String(250), nullable=True)
    delivery_person_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    lat = db.Column(db.Float, nullable=True) # Delivery person current lat
    lng = db.Column(db.Float, nullable=True) # Delivery person current lng
    delivery_target_lat = db.Column(db.Float, nullable=True) # Customer exact lat
    delivery_target_lng = db.Column(db.Float, nullable=True) # Customer exact lng
    created_at = db.Column(db.DateTime, default=db.func.current_timestamp())
    payment_method = db.Column(db.String(50), default='online') # online, cod
    is_cod = db.Column(db.Boolean, default=False)
    
    customer = db.relationship('User', foreign_keys=[customer_id], backref='customer_orders')
    delivery_person = db.relationship('User', foreign_keys=[delivery_person_id], backref='delivery_orders')
    
    # Reviews directly tied to the Order
    seller_rating = db.Column(db.Integer, nullable=True)
    seller_review = db.Column(db.Text, nullable=True)
    seller_review_image = db.Column(db.String(250), nullable=True)
    
    delivery_rating = db.Column(db.Integer, nullable=True)
    delivery_review = db.Column(db.Text, nullable=True)
    delivery_review_image = db.Column(db.String(250), nullable=True)
    
    items = db.relationship('OrderItem', backref='order', lazy=True)

class OrderItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('order.id'), nullable=False)
    food_item_id = db.Column(db.Integer, db.ForeignKey('food_item.id'), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    price = db.Column(db.Float, nullable=False)
    
    food_item = db.relationship('FoodItem', lazy=True)
    
class Review(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    food_item_id = db.Column(db.Integer, db.ForeignKey('food_item.id'), nullable=False)
    rating = db.Column(db.Integer, nullable=False)
    comment = db.Column(db.Text)
    image_url = db.Column(db.String(250))
    
    customer = db.relationship('User', backref='product_reviews')

class Notification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    message = db.Column(db.String(500), nullable=False)
    type = db.Column(db.String(50)) # info, success, warning
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=db.func.current_timestamp())
class Address(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    label = db.Column(db.String(50), default='Home') # Home, Work, Other
    full_name = db.Column(db.String(150), nullable=False)
    phone_number = db.Column(db.String(15), nullable=False)
    address_line = db.Column(db.String(250), nullable=False)
    pincode = db.Column(db.String(10), nullable=False)
    lat = db.Column(db.Float)
    lng = db.Column(db.Float)
    is_default = db.Column(db.Boolean, default=False)

class PushSubscription(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    endpoint = db.Column(db.Text, nullable=False)
    p256dh = db.Column(db.String(250), nullable=False)
    auth = db.Column(db.String(250), nullable=False)
    created_at = db.Column(db.DateTime, default=db.func.current_timestamp())

class PayoutRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(50), default='Pending') # Pending, Approved, Rejected, Completed
    payment_method = db.Column(db.String(100)) # UPI, Bank, etc.
    details = db.Column(db.String(250)) # UPI ID or Bank Details
    created_at = db.Column(db.DateTime, default=db.func.current_timestamp())
    
    user = db.relationship('User', backref='payout_requests')
