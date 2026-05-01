# 🍳 Cloud Kitchen Ecosystem: Complete Technical & Workflow Guide

Welcome to the **Cloud Kitchen Ecosystem**. This document provides a comprehensive "In-and-Out" guide to how the application works, the technologies powering it, and the journey an order takes from creation to delivery.

---

## 🛠 1. The Technology Stack
The application is built using a modern, scalable stack designed for real-time interactions and secure financial transactions.

| Layer | Technology | Purpose |
| :--- | :--- | :--- |
| **Backend** | **Python (Flask)** | The "brain" of the app, handling logic, routing, and server-side processing. |
| **Database** | **PostgreSQL (via SQLAlchemy)** | A robust relational database to store users, dishes, orders, and reviews. |
| **Real-time** | **Socket.IO** | Enables instant "Zomato-style" updates (e.g., rider claiming an order) without refreshing. |
| **Frontend** | **HTML5 / CSS3 / Jinja2** | Premium, responsive UI with dynamic templates and custom animations. |
| **Payments** | **Razorpay & Stripe** | Secure, enterprise-grade payment processing for cards, UPI, and wallets. |
| **Images** | **Cloudinary** | Cloud storage for high-quality food and profile images. |
| **Email** | **SendGrid (SMTP)** | Transactional email delivery for OTPs and order confirmations. |
| **Deployment** | **Render & Gunicorn** | High-performance production hosting and WSGI server. |

---

## 👥 2. User Roles & Capabilities
The system supports four distinct roles, each with a custom-tailored experience:

1.  **Customer**: Browses food, manages cart, pays, and tracks live delivery.
2.  **Seller (Cook)**: Manages kitchen status, adds dishes, tracks active orders, and views earnings.
3.  **Delivery Partner**: Claims available jobs, shares live location, and manages pickups/deliveries.
4.  **Admin**: High-level monitoring of platform health and total orders.

---

## 🔄 3. End-to-End Workflow

### Step 1: Authentication (Secure OTP Login)
*   **Process**: User enters their email/phone. The system generates a 6-digit OTP.
*   **Technical Detail**: 
    *   **Flask-Login** manages the user session. 
    *   **SendGrid** sends a branded HTML email with the OTP.
    *   **SQLAlchemy** stores a temporary `otp_code` and `otp_expiry` in the database for verification.

### Step 2: Discovery & Shopping (Customer)
*   **Process**: Customer explores the homepage, views dish details, and adds items to the cart.
*   **Technical Detail**: 
    *   The **CartItem** model tracks what's in the basket.
    *   **Cloudinary** serves optimized images for fast page loads.
    *   **Dish Ratings** are calculated dynamically based on historical reviews.

### Step 3: Checkout & Payment
*   **Process**: Customer provides a delivery address and pays via Razorpay/Stripe.
*   **Technical Detail**: 
    *   **Razorpay API** creates a unique `order_id`. 
    *   **Automated Revenue Splitting**: On success, the system calculates the **Cook's Earnings** (80%), **Delivery Fee**, and **Platform Commission** (20%) immediately.
    *   **Socket.IO Broadcast**: A `new_order_alert` is instantly sent to all logged-in Sellers and Delivery Partners.

### Step 4: Kitchen Preparation (Seller)
*   **Process**: The Cook sees the order in their dashboard and uses a **Progress Checklist**.
*   **Technical Detail**: 
    *   Cook checks **"Preparing"** → Status updates to `Preparing`.
    *   Cook checks **"Ready for Pickup"** → Status updates to `Ready for Pickup`.
    *   **AJAX (fetch)** is used so the cook doesn't have to reload the page to save status changes.

### Step 5: Delivery Assignment (Rider)
*   **Process**: Riders see "Available Orders." The first rider to click **"Claim Order"** gets it.
*   **Technical Detail**: 
    *   **Socket.IO** emits a `status_change` event. The moment a rider claims an order, it **disappears** from all other riders' dashboards automatically.
    *   The rider's details (Name/Phone) are instantly pushed to the Customer's tracking page.

### Step 6: Transit & Live Tracking
*   **Process**: Rider marks order as **"Picked Up."** Customer sees the rider move on a live map.
*   **Technical Detail**: 
    *   **Leaflet.js** renders the map.
    *   The rider's phone sends GPS coordinates to the server via `/api/update_location`.
    *   The customer's browser "polls" the `/api/get_location` endpoint every 4 seconds to move the bike icon smoothly.

### Step 7: Completion & Feedback
*   **Process**: Rider marks as **"Delivered."** Customer rates the food and the delivery.
*   **Technical Detail**: 
    *   **Database Transaction**: The order is moved to `Delivered` status, finalizing the financial record.
    *   **Review System**: Ratings are aggregated to update the Cook's overall kitchen rating.

---

## 🛡 4. Key Security & Stability Features
*   **Status Locking**: A Cook cannot accidentally move an order backward (e.g., from Ready back to Preparing).
*   **Merchant Verification**: Cooks must sign a digital "Merchant Agreement" before they can start selling.
*   **CSRF & Security**: Werkzeug hashes all passwords, and Flask-Login prevents unauthorized access to sensitive dashboard routes.
*   **Real-time Toasts**: The system uses persistent "Toasts" (pop-up notifications) to keep users informed of status changes even if they are on a different page.

---

## 📈 5. Data Flow Summary
1.  **Request**: Browser makes a request (e.g., `POST /login`).
2.  **Processing**: `app.py` processes the logic and queries the `models.py` (PostgreSQL).
3.  **Real-time Update**: `SocketIO` sends signals to other connected clients.
4.  **Response**: `Jinja2` renders the HTML template with the latest data and sends it back to the user.

---
*Created by Antigravity AI for the Cloud Kitchen Project.*
