import os

from pymongo import MongoClient
from werkzeug.security import generate_password_hash

email = os.environ["ADMIN_EMAIL"].strip().lower()
password = os.environ["ADMIN_PASSWORD"]
if len(password) < 12 or password.startswith("change-me"):
    raise SystemExit("ADMIN_PASSWORD must be a non-placeholder value of at least 12 characters")

db = MongoClient(os.environ["MONGO_URI"]).get_default_database()
db.users.update_one(
    {"email": email},
    {"$set": {
        "email": email,
        "password": generate_password_hash(password, method="pbkdf2:sha256:600000"),
        "role": "admin",
        "disabled": False,
        "force_password_reset": True,
    }},
    upsert=True,
)
print(f"Administrator {email} created; a password change is required at first login.")
