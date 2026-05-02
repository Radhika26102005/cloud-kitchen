import sqlite3
import os

db_path = 'instance/cloud_kitchen.db'
if not os.path.exists(db_path):
    db_path = 'cloud_kitchen.db'

print(f"Connecting to {db_path}...")
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

def add_column(table, column, type):
    try:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {type}")
        print(f"Added column {column} to {table}")
    except sqlite3.OperationalError as e:
        print(f"Column {column} in {table} already exists or error: {e}")

# Add missing columns
add_column('food_item', 'is_veg', 'BOOLEAN DEFAULT 1')
add_column('user', 'lat', 'FLOAT')
add_column('user', 'lng', 'FLOAT')

conn.commit()
conn.close()
print("Migration complete!")
