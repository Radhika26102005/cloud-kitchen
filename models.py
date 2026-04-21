from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin

db = SQLAlchemy()

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(150), nullable=False)
    role = db.Column(db.String(50), nullable=False) # customer, seller, delivery
    location = db.Column(db.String(250))
    has_agreed_to_terms = db.Column(db.Boolean, default=False)
    terms_agreed_at = db.Column(db.DateTime)
    is_open = db.Column(db.Boolean, default=True) # For Sellers
    is_verified = db.Column(db.Boolean, default=False) # For Sellers
    profile_image = db.Column(db.String(250), default='https://cdn-icons-png.flaticon.com/512/3135/3135715.png')

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

class Notification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    message = db.Column(db.String(500), nullable=False)
    type = db.Column(db.String(50)) # info, success, warning
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=db.func.current_timestamp())
class PushSubscription(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    endpoint = db.Column(db.Text, nullable=False)
    p256dh = db.Column(db.String(250), nullable=False)
    auth = db.Column(db.String(250), nullable=False)
    created_at = db.Column(db.DateTime, default=db.func.current_timestamp())
