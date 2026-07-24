import json
import logging
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import Flask, flash, g, has_request_context, jsonify, make_response, redirect, render_template, request, url_for
from flask_jwt_extended import (
    JWTManager,
    create_access_token,
    create_refresh_token,
    decode_token,
    get_jwt,
    get_jwt_identity,
    jwt_required,
    set_access_cookies,
    set_refresh_cookies,
    unset_jwt_cookies,
)
from bson.objectid import ObjectId
from flask_pymongo import PyMongo
from flask_wtf import CSRFProtect, FlaskForm
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from pymongo import ASCENDING
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash
from wtforms import PasswordField, StringField, SubmitField
from wtforms.validators import DataRequired, Email, EqualTo, Length, Regexp


def env_bool(name, default=False):
    return os.getenv(name, str(default)).lower() in {"1", "true", "yes", "on"}


app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
app.config.update(
    SECRET_KEY=os.environ["SECRET_KEY"],
    JWT_SECRET_KEY=os.environ["JWT_SECRET_KEY"],
    MONGO_URI=os.environ["MONGO_URI"],
    JWT_TOKEN_LOCATION=["cookies"],
    JWT_COOKIE_SECURE=env_bool("COOKIE_SECURE", True),
    JWT_COOKIE_HTTPONLY=True,
    JWT_COOKIE_SAMESITE="Lax",
    JWT_ACCESS_COOKIE_PATH="/",
    JWT_REFRESH_COOKIE_PATH="/user/refresh",
    JWT_COOKIE_CSRF_PROTECT=True,
    JWT_CSRF_CHECK_FORM=True,
    JWT_ACCESS_TOKEN_EXPIRES=timedelta(minutes=int(os.getenv("ACCESS_TOKEN_MINUTES", "15"))),
    JWT_REFRESH_TOKEN_EXPIRES=timedelta(days=int(os.getenv("REFRESH_TOKEN_DAYS", "7"))),
)

jwt = JWTManager(app)
csrf = CSRFProtect(app)
mongo = PyMongo(app)
users = mongo.db.users
refresh_tokens = mongo.db.refresh_tokens
revoked_tokens = mongo.db.revoked_tokens

REQUESTS = Counter("user_service_http_requests_total", "HTTP requests", ["method", "route", "status"])
LATENCY = Histogram("user_service_http_request_seconds", "Request latency", ["method", "route"])


class JsonFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "service": "user-service",
            "message": record.getMessage(),
            "request_id": getattr(g, "request_id", None) if has_request_context() else None,
            "user_id": getattr(g, "user_id", None) if has_request_context() else None,
        })


handler = logging.StreamHandler()
handler.setFormatter(JsonFormatter())
app.logger.handlers = [handler]
app.logger.setLevel(logging.INFO)


class LoginForm(FlaskForm):
    email = StringField("Email", validators=[DataRequired(), Email(), Length(max=254)])
    password = PasswordField("Password", validators=[DataRequired(), Length(min=8, max=128)])
    submit = SubmitField("Login")


class RegisterForm(FlaskForm):
    email = StringField("Email", validators=[DataRequired(), Email(), Length(max=254)])
    password = PasswordField("Password", validators=[
        DataRequired(), Length(min=12, max=128),
        Regexp(r"^(?=.*[A-Za-z])(?=.*[0-9!@#$%^&*(),.?\":{}|<>])", message="Use at least one letter and one number or symbol."),
    ])
    confirm_password = PasswordField("Confirm password", validators=[DataRequired(), EqualTo("password")])
    submit = SubmitField("Register")


class PasswordForm(FlaskForm):
    password = PasswordField("New password", validators=[DataRequired(), Length(min=12, max=128)])
    confirm_password = PasswordField("Confirm password", validators=[DataRequired(), EqualTo("password")])
    submit = SubmitField("Change password")


def current_user():
    identity = get_jwt_identity()
    return users.find_one({"email": identity}, {"password": 0}) if identity else None


def admin_required(view):
    @wraps(view)
    @jwt_required()
    def wrapped(*args, **kwargs):
        user = current_user()
        if not user or user.get("role") != "admin" or user.get("disabled", False):
            flash("Administrator access required.", "danger")
            return redirect("/user/login")
        return view(*args, **kwargs)
    return wrapped


def persist_refresh(token):
    decoded = decode_token(token)
    refresh_tokens.insert_one({
        "jti": decoded["jti"],
        "email": decoded["sub"],
        "expires_at": datetime.fromtimestamp(decoded["exp"], timezone.utc),
        "revoked": False,
        "created_at": datetime.now(timezone.utc),
    })


def revoke_encoded_token(encoded, collection):
    if not encoded:
        return
    try:
        decoded = decode_token(encoded, allow_expired=True)
        collection.update_one(
            {"jti": decoded["jti"]},
            {"$set": {"jti": decoded["jti"], "expires_at": datetime.fromtimestamp(decoded["exp"], timezone.utc), "revoked": True}},
            upsert=True,
        )
    except Exception:
        app.logger.warning("Unable to decode token during revocation")


def issue_session(email, role, response):
    claims = {"role": role}
    access = create_access_token(identity=email, additional_claims=claims)
    refresh = create_refresh_token(identity=email, additional_claims=claims)
    persist_refresh(refresh)
    set_access_cookies(response, access)
    set_refresh_cookies(response, refresh)
    return response


@jwt.token_in_blocklist_loade
def token_is_revoked(_header, payload):
    if revoked_tokens.find_one({"jti": payload["jti"]}):
        return True
    if payload.get("type") == "refresh":
        record = refresh_tokens.find_one({"jti": payload["jti"]})
        return not record or record.get("revoked", False)
    user = users.find_one({"email": payload.get("sub")}, {"disabled": 1})
    return not user or user.get("disabled", False)


@app.before_request
def begin_request():
    g.started_at = time.perf_counter()
    g.request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    g.user_id = None
    try:
        users.create_index([("email", ASCENDING)], unique=True)
        refresh_tokens.create_index([("jti", ASCENDING)], unique=True)
        refresh_tokens.create_index([("expires_at", ASCENDING)], expireAfterSeconds=0)
        revoked_tokens.create_index([("jti", ASCENDING)], unique=True)
        revoked_tokens.create_index([("expires_at", ASCENDING)], expireAfterSeconds=0)
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
def auth_context():
    identity = None
    role = None
    try:
        from flask_jwt_extended import verify_jwt_in_request
        verify_jwt_in_request(optional=True)
        identity = get_jwt_identity()
        role = get_jwt().get("role") if identity else None
    except Exception:
        pass
    return {"current_identity": identity, "current_role": role}


@app.get("/")
def home():
    return redirect("/user/login")


@app.route("/register", methods=["GET", "POST"])
def register():
    form = RegisterForm()
    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        if users.find_one({"email": email}):
            form.email.errors.append("That email is already registered.")
        else:
            users.insert_one({
                "email": email,
                "password": generate_password_hash(form.password.data, method="pbkdf2:sha256:600000"),
                "role": "user",
                "disabled": False,
                "force_password_reset": False,
                "created_at": datetime.now(timezone.utc),
            })
            flash("Registration complete. You can now sign in.", "success")
            return redirect("/user/login")
    return render_template("register.html", form=form)


@app.route("/login", methods=["GET", "POST"])
def login():
    form = LoginForm()
    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        user = users.find_one({"email": email})
        if user and not user.get("disabled", False) and check_password_hash(user["password"], form.password.data):
            target = "/user/change-password" if user.get("force_password_reset") else ("/product/manage" if user.get("role") == "admin" else "/")
            return issue_session(email, user.get("role", "user"), make_response(redirect(target)))
        flash("Invalid credentials or disabled account.", "danger")
    return render_template("login.html", form=form)


@app.post("/refresh")
@csrf.exempt
@jwt_required(refresh=True)
def refresh():
    old_jti = get_jwt()["jti"]
    refresh_tokens.update_one({"jti": old_jti}, {"$set": {"revoked": True}})
    user = current_user()
    if not user or user.get("disabled", False):
        return jsonify(error="account unavailable"), 403
    response = make_response(redirect(request.form.get("next") or "/"))
    return issue_session(user["email"], user.get("role", "user"), response)


@app.post("/logout")
def logout():
    revoke_encoded_token(request.cookies.get("access_token_cookie"), revoked_tokens)
    revoke_encoded_token(request.cookies.get("refresh_token_cookie"), refresh_tokens)
    response = make_response(redirect("/user/login"))
    unset_jwt_cookies(response)
    flash("You have been signed out.", "info")
    return response


@app.route("/change-password", methods=["GET", "POST"])
@jwt_required()
def change_password():
    form = PasswordForm()
    if form.validate_on_submit():
        users.update_one({"email": get_jwt_identity()}, {"$set": {
            "password": generate_password_hash(form.password.data, method="pbkdf2:sha256:600000"),
            "force_password_reset": False,
        }})
        refresh_tokens.update_many({"email": get_jwt_identity()}, {"$set": {"revoked": True}})
        response = make_response(redirect("/user/login"))
        unset_jwt_cookies(response)
        flash("Password changed. Please sign in again.", "success")
        return response
    return render_template("change_password.html", form=form)


@app.get("/admin/users")
@admin_required
def manage_users():
    return render_template("users.html", users=users.find({}, {"password": 0}).sort("email", ASCENDING))


@app.post("/admin/users/<user_id>/role")
@admin_required
def update_role(user_id):
    role = request.form.get("role")
    if role not in {"user", "admin"}:
        return jsonify(error="invalid role"), 400
    users.update_one({"_id": ObjectId(user_id)}, {"$set": {"role": role}})
    refresh_tokens.update_many({"email": users.find_one({"_id": ObjectId(user_id)})["email"]}, {"$set": {"revoked": True}})
    flash("Role updated; existing refresh sessions were revoked.", "success")
    return redirect("/user/admin/users")


@app.post("/admin/users/<user_id>/status")
@admin_required
def update_status(user_id):
    user = users.find_one({"_id": ObjectId(user_id)})
    users.update_one({"_id": user["_id"]}, {"$set": {"disabled": not user.get("disabled", False)}})
    refresh_tokens.update_many({"email": user["email"]}, {"$set": {"revoked": True}})
    flash("Account status updated.", "success")
    return redirect("/user/admin/users")


@app.post("/admin/users/<user_id>/force-reset")
@admin_required
def force_reset(user_id):
    user = users.find_one({"_id": ObjectId(user_id)})
    users.update_one({"_id": user["_id"]}, {"$set": {"force_password_reset": True}})
    refresh_tokens.update_many({"email": user["email"]}, {"$set": {"revoked": True}})
    flash("The user must change their password after the next login.", "warning")
    return redirect("/user/admin/users")


@app.get("/health")
def health():
    return jsonify(status="ok", service="user-service")


@app.get("/ready")
def ready():
    try:
        mongo.cx.admin.command("ping")
        return jsonify(status="ready")
    except Exception:
        return jsonify(status="not-ready"), 503


@app.get("/metrics")
def metrics():
    return app.response_class(generate_latest(), mimetype=CONTENT_TYPE_LATEST)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
