import json
import logging
import os
import re
import time
import uuid
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path

from bson.objectid import ObjectId
from flask import Flask, flash, g, has_request_context, jsonify, redirect, render_template, request, send_from_directory, url_for
from flask_jwt_extended import JWTManager, get_jwt, get_jwt_identity, jwt_required, verify_jwt_in_request
from flask_limiter import Limite
from flask_limiter.util import get_remote_address
from flask_pymongo import PyMongo
from flask_wtf import CSRFProtect, FlaskForm
from flask_wtf.file import FileAllowed, FileField
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from pymongo import ASCENDING, DESCENDING, MongoClient
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.utils import secure_filename
from wtforms import FloatField, StringField, TextAreaField
from wtforms.validators import InputRequired, Length, NumberRange, Optional


def env_bool(name, default=False):
    return os.getenv(name, str(default)).lower() in {"1", "true", "yes", "on"}


app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
upload_dir = Path(os.getenv("UPLOAD_FOLDER", "/app/uploads"))
upload_dir.mkdir(parents=True, exist_ok=True)
app.config.update(
    SECRET_KEY=os.environ["SECRET_KEY"],
    MONGO_URI=os.environ["MONGO_URI"],
    JWT_SECRET_KEY=os.environ["JWT_SECRET_KEY"],
    JWT_TOKEN_LOCATION=["cookies"],
    JWT_COOKIE_SECURE=env_bool("COOKIE_SECURE", True),
    JWT_COOKIE_HTTPONLY=True,
    JWT_COOKIE_SAMESITE="Lax",
    JWT_ACCESS_COOKIE_PATH="/",
    JWT_COOKIE_CSRF_PROTECT=True,
    JWT_CSRF_CHECK_FORM=True,
    MAX_CONTENT_LENGTH=int(os.getenv("MAX_UPLOAD_MB", "5")) * 1024 * 1024,
)

mongo = PyMongo(app)
products = mongo.db.products
audit_logs = mongo.db.audit_logs
user_client = MongoClient(os.environ["USER_MONGO_URI"], connect=False)
user_db = user_client.get_default_database()
jwt = JWTManager(app)
csrf = CSRFProtect(app)
limiter = Limiter(get_remote_address, app=app, default_limits=["200 per day", "50 per hour"], storage_uri="memory://")

REQUESTS = Counter("product_service_http_requests_total", "HTTP requests", ["method", "route", "status"])
LATENCY = Histogram("product_service_http_request_seconds", "Request latency", ["method", "route"])


class JsonFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "service": "product-service",
            "message": record.getMessage(),
            "request_id": getattr(g, "request_id", None) if has_request_context() else None,
            "user_id": getattr(g, "user_id", None) if has_request_context() else None,
        })


handler = logging.StreamHandler()
handler.setFormatter(JsonFormatter())
app.logger.handlers = [handler]
app.logger.setLevel(logging.INFO)


class ProductForm(FlaskForm):
    name = StringField("Product name", validators=[InputRequired(), Length(min=2, max=100)])
    description = TextAreaField("Description", validators=[InputRequired(), Length(min=2, max=1000)])
    price = FloatField("Price", validators=[InputRequired(), NumberRange(min=0.01, max=1000000)])
    image = FileField("Product image", validators=[Optional(), FileAllowed(["jpg", "jpeg", "png", "webp"], "Use a JPG, PNG, or WebP image.")])


def get_database_user(email):
    return user_db.users.find_one({"email": email}, {"password": 0}) if email else None


def admin_required(view):
    @wraps(view)
    @jwt_required()
    def wrapped(*args, **kwargs):
        user = get_database_user(get_jwt_identity())
        if not user or user.get("role") != "admin" or user.get("disabled", False):
            flash("Administrator access required.", "danger")
            return redirect("/user/login")
        return view(*args, **kwargs)
    return wrapped


@jwt.token_in_blocklist_loade
def token_is_revoked(_header, payload):
    if user_db.revoked_tokens.find_one({"jti": payload["jti"]}):
        return True
    user = get_database_user(payload.get("sub"))
    return not user or user.get("disabled", False)


def audit(action, product_id, details=None):
    audit_logs.insert_one({
        "actor": get_jwt_identity(),
        "action": action,
        "product_id": str(product_id),
        "details": details or {},
        "request_id": g.request_id,
        "created_at": datetime.now(timezone.utc),
    })


def save_image(upload):
    if not upload or not upload.filename:
        return None
    suffix = Path(secure_filename(upload.filename)).suffix.lower()
    filename = f"{uuid.uuid4().hex}{suffix}"
    upload.save(upload_dir / filename)
    return filename


def page_query(collection, query, page, per_page=9):
    total = collection.count_documents(query)
    items = list(collection.find(query).sort("name", ASCENDING).skip((page - 1) * per_page).limit(per_page))
    return items, total, max(1, (total + per_page - 1) // per_page)


@app.before_request
def begin_request():
    g.started_at = time.perf_counter()
    g.request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    g.user_id = None
    try:
        verify_jwt_in_request(optional=True)
        g.user_id = get_jwt_identity()
    except Exception:
        pass
    try:
        products.create_index([("name", ASCENDING)])
        audit_logs.create_index([("created_at", DESCENDING)])
        audit_logs.create_index([("product_id", ASCENDING), ("created_at", DESCENDING)])
    except Exception:
        pass


@app.after_request
def finish_request(response):
    route = request.url_rule.rule if request.url_rule else "unmatched"
    elapsed = time.perf_counter() - g.started_at
    REQUESTS.labels(request.method, route, response.status_code).inc()
    LATENCY.labels(request.method, route).observe(elapsed)
    response.headers["X-Request-ID"] = g.request_id
    app.logger.info(json.dumps({"method": request.method, "route": route, "status": response.status_code, "latency_ms": round(elapsed * 1000, 2)}))
    return response


@app.context_processo
def template_context():
    role = None
    if g.get("user_id"):
        user = get_database_user(g.user_id)
        role = user.get("role") if user else None
    return {"current_identity": g.get("user_id"), "current_role": role}


@app.get("/")
def index():
    page = max(request.args.get("page", 1, type=int), 1)
    search = request.args.get("q", "").strip()
    query = {"name": {"$regex": re.escape(search), "$options": "i"}} if search else {}
    rows, total, pages = page_query(products, query, page)
    return render_template("index.html", products=rows, page=page, pages=pages, total=total, search=search)


@app.route("/add", methods=["GET", "POST"])
@admin_required
@limiter.limit("10 per minute")
def add_product():
    form = ProductForm()
    if form.validate_on_submit():
        document = {
            "name": form.name.data.strip(),
            "description": form.description.data.strip(),
            "price": float(form.price.data),
            "image_filename": save_image(form.image.data),
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        }
        result = products.insert_one(document)
        audit("created", result.inserted_id, {"name": document["name"]})
        flash("Product added.", "success")
        return redirect(url_for("manage_products"))
    return render_template("product_form.html", form=form, product=None)


@app.get("/manage")
@admin_required
def manage_products():
    page = max(request.args.get("page", 1, type=int), 1)
    search = request.args.get("q", "").strip()
    query = {"name": {"$regex": re.escape(search), "$options": "i"}} if search else {}
    rows, total, pages = page_query(products, query, page, per_page=10)
    activity = list(audit_logs.find().sort("created_at", DESCENDING).limit(10))
    return render_template("manage.html", products=rows, page=page, pages=pages, total=total, search=search, activity=activity)


@app.route("/edit/<product_id>", methods=["GET", "POST"])
@admin_required
@limiter.limit("10 per minute")
def edit_product(product_id):
    try:
        oid = ObjectId(product_id)
    except Exception:
        flash("Invalid product identifier.", "danger")
        return redirect(url_for("manage_products"))
    product = products.find_one({"_id": oid})
    if not product:
        flash("Product not found.", "danger")
        return redirect(url_for("manage_products"))
    form = ProductForm(data=product)
    if form.validate_on_submit():
        changes = {
            "name": form.name.data.strip(),
            "description": form.description.data.strip(),
            "price": float(form.price.data),
            "updated_at": datetime.now(timezone.utc),
        }
        filename = save_image(form.image.data)
        if filename:
            changes["image_filename"] = filename
        products.update_one({"_id": oid}, {"$set": changes})
        audit("updated", oid, {"name": changes["name"]})
        flash("Product updated.", "success")
        return redirect(url_for("manage_products"))
    return render_template("product_form.html", form=form, product=product)


@app.post("/delete/<product_id>")
@admin_required
@limiter.limit("10 per minute")
def delete_product(product_id):
    try:
        product = products.find_one({"_id": ObjectId(product_id)})
    except Exception:
        product = None
    if not product:
        flash("Product not found.", "danger")
        return redirect(url_for("manage_products"))
    products.delete_one({"_id": product["_id"]})
    audit("deleted", product["_id"], {"name": product["name"]})
    flash("Product deleted.", "info")
    return redirect(url_for("manage_products"))


@app.get("/uploads/<path:filename>")
def uploaded_file(filename):
    return send_from_directory(upload_dir, filename)


@app.get("/api/products")
@limiter.limit("60 per minute")
def products_api():
    output = [{
        "id": str(product["_id"]),
        "name": product["name"],
        "description": product["description"],
        "price": product["price"],
        "image": url_for("uploaded_file", filename=product["image_filename"], _external=True) if product.get("image_filename") else None,
    } for product in products.find().sort("name", ASCENDING).limit(100)]
    return jsonify(products=output)


@app.get("/health")
def health():
    return jsonify(status="ok", service="product-service")


@app.get("/ready")
def ready():
    try:
        mongo.cx.admin.command("ping")
        user_client.admin.command("ping")
        return jsonify(status="ready")
    except Exception:
        return jsonify(status="not-ready"), 503


@app.get("/metrics")
def metrics():
    return app.response_class(generate_latest(), mimetype=CONTENT_TYPE_LATEST)


@app.errorhandler(413)
def too_large(_error):
    flash("Image exceeds the upload size limit.", "danger")
    return redirect(request.referrer or url_for("manage_products"))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=False)
