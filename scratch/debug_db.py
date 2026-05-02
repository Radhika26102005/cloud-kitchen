import sys
import os
sys.path.append(os.getcwd())

from app import app, db, FoodItem, User

with app.app_context():
    items = FoodItem.query.all()
    print(f"Total FoodItems: {len(items)}")
    for item in items:
        seller = User.query.get(item.seller_id)
        print(f"- {item.name}: Qty={item.quantity}, Seller={seller.username if seller else 'None'}, SellerOpen={seller.is_open if seller else 'N/A'}")
    
    sellers = User.query.filter_by(role='seller').all()
    print(f"\nTotal Sellers: {len(sellers)}")
    for s in sellers:
        print(f"- {s.username}: IsOpen={s.is_open}, IsVerified={s.is_verified}")
