from flask import current_app
import sqlite3
import os

def get_all_participants():
    db_path = os.path.join(current_app.instance_path, "participants.db")
    print("📂 Using DB path:", db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT id, name, gender, level, active FROM participants")
    rows = cur.fetchall()
    conn.close()
    return [dict(row) for row in rows]