"""Add multi-user support, weekday schedules, and zone permissions."""
import sqlite3
import os

DATABASE_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'infoboard.db')


def run():
    conn = sqlite3.connect(DATABASE_PATH)
    try:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                display_name TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'user',
                created_at TIMESTAMP NOT NULL
            )
        ''')

        conn.execute('''
            CREATE TABLE IF NOT EXISTS display_schedules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                display_id INTEGER NOT NULL,
                weekday INTEGER NOT NULL,
                user_id INTEGER NOT NULL DEFAULT 0,
                UNIQUE(display_id, weekday),
                FOREIGN KEY (display_id) REFERENCES displays(id)
            )
        ''')

        conn.execute('''
            CREATE TABLE IF NOT EXISTS user_zone_permissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                display_id INTEGER NOT NULL,
                zone_index INTEGER NOT NULL,
                UNIQUE(user_id, display_id, zone_index),
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (display_id) REFERENCES displays(id)
            )
        ''')

        for table in ('media_items', 'playlist_items', 'zone_playlist_items'):
            try:
                conn.execute(f'ALTER TABLE {table} ADD COLUMN user_id INTEGER NOT NULL DEFAULT 0')
            except Exception:
                pass  # Column already exists

        conn.commit()
        print('Migration 0006: multi-user tables and columns created')
    finally:
        conn.close()
