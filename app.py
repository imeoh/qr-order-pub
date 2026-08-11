
import os
from io import BytesIO
from datetime import datetime
from flask import Flask, render_template, request, jsonify, redirect, url_for, send_file, abort
from flask_sqlalchemy import SQLAlchemy
import qrcode

app = Flask(__name__)

database_url = os.getenv("DATABASE_URL", "sqlite:///orders.db")
if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)

TABLE_COUNT = int(os.getenv("TABLE_COUNT", "20"))
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "1234")

MENU = [
    {"id": 1, "name": "소주", "price": 5000, "category": "주류"},
    {"id": 2, "name": "맥주", "price": 5000, "category": "주류"},
    {"id": 3, "name": "닭꼬치", "price": 4000, "category": "안주"},
    {"id": 4, "name": "떡볶이", "price": 8000, "category": "안주"},
    {"id": 5, "name": "어묵탕", "price": 9000, "category": "안주"},
]

class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    table_no = db.Column(db.Integer, nullable=False)
    total_price = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(20), nullable=False, default="주문접수")
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.now)
    items = db.relationship("OrderItem", backref="order", cascade="all, delete-orphan")

class OrderItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("order.id"), nullable=False)
    menu_name = db.Column(db.String(100), nullable=False)
    price = db.Column(db.Integer, nullable=False)
    qty = db.Column(db.Integer, nullable=False)

with app.app_context():
    db.create_all()

@app.route("/")
def home():
    return render_template("home.html", table_count=TABLE_COUNT)

@app.route("/t/<int:table_no>")
def table_order(table_no):
    if table_no < 1 or table_no > TABLE_COUNT:
        abort(404)
    categories = []
    for item in MENU:
        if item["category"] not in categories:
            categories.append(item["category"])
    return render_template("order.html", menu=MENU, table_no=table_no, categories=categories)

@app.route("/api/order", methods=["POST"])
def create_order():
    data = request.get_json(force=True)
    table_no = int(data.get("table_no", 0))
    items = data.get("items", [])

    if table_no < 1 or table_no > TABLE_COUNT:
        return jsonify({"ok": False, "message": "잘못된 테이블 번호입니다."}), 400

    menu_map = {m["id"]: m for m in MENU}
    valid_items = []
    total = 0

    for item in items:
        try:
            menu_id = int(item.get("id", 0))
            qty = int(item.get("qty", 0))
        except (TypeError, ValueError):
            continue

        if menu_id in menu_map and qty > 0:
            menu = menu_map[menu_id]
            valid_items.append((menu, qty))
            total += menu["price"] * qty

    if not valid_items:
        return jsonify({"ok": False, "message": "메뉴를 1개 이상 선택해주세요."}), 400

    order = Order(
        table_no=table_no,
        total_price=total,
        status="주문접수"
    )
    db.session.add(order)
    db.session.flush()

    for menu, qty in valid_items:
        db.session.add(OrderItem(
            order_id=order.id,
            menu_name=menu["name"],
            price=menu["price"],
            qty=qty
        ))

    db.session.commit()
    return jsonify({"ok": True, "order_id": order.id, "total": total})

def check_admin_password():
    supplied = request.args.get("pw") or request.form.get("pw") or request.headers.get("X-Admin-Password")
    return supplied == ADMIN_PASSWORD

@app.route("/admin")
def admin():
    if not check_admin_password():
        return render_template("admin_login.html"), 401

    orders = Order.query.order_by(Order.id.desc()).all()
    table_totals = {}
    for order in orders:
        if order.status != "취소":
            table_totals[order.table_no] = table_totals.get(order.table_no, 0) + order.total_price

    return render_template(
        "admin.html",
        orders=orders,
        pw=request.args.get("pw", ""),
        table_totals=table_totals
    )

@app.route("/admin/login", methods=["POST"])
def admin_login():
    pw = request.form.get("pw", "")
    if pw == ADMIN_PASSWORD:
        return redirect(url_for("admin", pw=pw))
    return render_template("admin_login.html", error="비밀번호가 틀렸습니다."), 401

@app.route("/api/order/<int:order_id>/status", methods=["POST"])
def update_status(order_id):
    if request.headers.get("X-Admin-Password") != ADMIN_PASSWORD:
        return jsonify({"ok": False, "message": "관리자 인증 실패"}), 401

    data = request.get_json(force=True)
    status = data.get("status", "")
    allowed = {"주문접수", "조리중", "완료", "취소"}

    if status not in allowed:
        return jsonify({"ok": False, "message": "잘못된 상태입니다."}), 400

    order = db.session.get(Order, order_id)
    if not order:
        return jsonify({"ok": False, "message": "주문을 찾을 수 없습니다."}), 404

    order.status = status
    db.session.commit()
    return jsonify({"ok": True})

@app.route("/admin/qrs")
def qr_page():
    if not check_admin_password():
        return render_template("admin_login.html"), 401
    return render_template(
        "qrs.html",
        table_count=TABLE_COUNT,
        pw=request.args.get("pw", "")
    )

@app.route("/qr/<int:table_no>.png")
def table_qr(table_no):
    if table_no < 1 or table_no > TABLE_COUNT:
        abort(404)

    # 배포된 실제 도메인을 자동으로 사용하므로 QR 생성 시 주소를 따로 수정할 필요 없음
    target = request.url_root.rstrip("/") + url_for("table_order", table_no=table_no)

    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(target)
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white")

    buf = BytesIO()
    image.save(buf, format="PNG")
    buf.seek(0)
    return send_file(buf, mimetype="image/png", download_name=f"table_{table_no}.png")

@app.route("/api/orders/latest")
def latest_orders():
    if request.headers.get("X-Admin-Password") != ADMIN_PASSWORD:
        return jsonify({"ok": False}), 401
    latest = Order.query.order_by(Order.id.desc()).limit(30).all()
    return jsonify({
        "ok": True,
        "orders": [
            {
                "id": o.id,
                "table_no": o.table_no,
                "status": o.status,
                "total_price": o.total_price,
                "created_at": o.created_at.strftime("%H:%M:%S")
            }
            for o in latest
        ]
    })

if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=True)
