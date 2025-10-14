import os
from app import app, db

with app.app_context():
    os.makedirs(app.instance_path, exist_ok=True)