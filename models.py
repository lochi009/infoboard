import sqlite3
import os
from datetime import datetime
from contextlib import contextmanager

DATABASE_PATH = 'infoboard.db'


@contextmanager
def get_db():
    """Context manager for database connections."""
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """Initialize the database with required tables and migrate legacy data."""
    with get_db() as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS displays (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                slug TEXT NOT NULL UNIQUE,
                width INTEGER NOT NULL DEFAULT 1920,
                height INTEGER NOT NULL DEFAULT 1080,
                selected_media_id INTEGER NOT NULL DEFAULT 0,
                cycle_interval INTEGER NOT NULL DEFAULT 10,
                background_color TEXT NOT NULL DEFAULT '#ffffff',
                progress_indicator TEXT NOT NULL DEFAULT 'countdown',
                video_fit TEXT NOT NULL DEFAULT 'contain'
            )
        ''')

        # Migrate: add video_fit column if upgrading from older schema
        try:
            conn.execute("ALTER TABLE displays ADD COLUMN video_fit TEXT NOT NULL DEFAULT 'contain'")
        except Exception:
            pass  # Column already exists

        # Migrate: add ambient_bg column if upgrading from older schema
        try:
            conn.execute("ALTER TABLE displays ADD COLUMN ambient_bg INTEGER NOT NULL DEFAULT 1")
        except Exception:
            pass  # Column already exists

        # Migrate: add layout_preset column if upgrading from older schema
        try:
            conn.execute("ALTER TABLE displays ADD COLUMN layout_preset TEXT NOT NULL DEFAULT 'fullscreen'")
        except Exception:
            pass  # Column already exists

        conn.execute('''
            CREATE TABLE IF NOT EXISTS media_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content_type TEXT NOT NULL,
                filename TEXT,
                original_name TEXT NOT NULL,
                url TEXT,
                upload_date TIMESTAMP NOT NULL,
                file_size INTEGER NOT NULL DEFAULT 0,
                scale_to_fit INTEGER NOT NULL DEFAULT 0,
                user_id INTEGER NOT NULL DEFAULT 0
            )
        ''')

        # Migrate: add scale_to_fit column if upgrading from older schema
        try:
            conn.execute("ALTER TABLE media_items ADD COLUMN scale_to_fit INTEGER NOT NULL DEFAULT 0")
        except Exception:
            pass  # Column already exists

        conn.execute('''
            CREATE TABLE IF NOT EXISTS pdf_renders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                media_id INTEGER NOT NULL,
                display_id INTEGER NOT NULL,
                page_number INTEGER NOT NULL,
                render_filename TEXT NOT NULL,
                UNIQUE(media_id, display_id, page_number)
            )
        ''')

        conn.execute('''
            CREATE TABLE IF NOT EXISTS pdf_spread_renders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                media_id INTEGER NOT NULL,
                display_id INTEGER NOT NULL,
                spread_type TEXT NOT NULL,
                page_number INTEGER NOT NULL,
                render_filename TEXT NOT NULL,
                UNIQUE(media_id, display_id, spread_type, page_number)
            )
        ''')

        conn.execute('''
            CREATE TABLE IF NOT EXISTS gallery_images (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                media_id INTEGER NOT NULL,
                filename TEXT NOT NULL,
                original_name TEXT NOT NULL,
                file_size INTEGER NOT NULL DEFAULT 0,
                position INTEGER NOT NULL DEFAULT 0
            )
        ''')

        conn.execute('''
            CREATE TABLE IF NOT EXISTS playlist_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                display_id INTEGER NOT NULL,
                media_id INTEGER NOT NULL,
                duration INTEGER NOT NULL DEFAULT 10,
                position INTEGER NOT NULL DEFAULT 0
            )
        ''')

        conn.execute('''
            CREATE TABLE IF NOT EXISTS zones (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                display_id INTEGER NOT NULL,
                zone_index INTEGER NOT NULL,
                selected_media_id INTEGER NOT NULL DEFAULT 0,
                cycle_interval INTEGER NOT NULL DEFAULT 10,
                UNIQUE(display_id, zone_index),
                FOREIGN KEY (display_id) REFERENCES displays(id)
            )
        ''')

        conn.execute('''
            CREATE TABLE IF NOT EXISTS zone_playlist_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                zone_id INTEGER NOT NULL,
                media_id INTEGER NOT NULL,
                duration INTEGER NOT NULL DEFAULT 10,
                position INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (zone_id) REFERENCES zones(id)
            )
        ''')

        # Migrate: add spread_mode to playlist tables if upgrading
        try:
            conn.execute("ALTER TABLE playlist_items ADD COLUMN spread_mode TEXT NOT NULL DEFAULT 'none'")
        except Exception:
            pass  # Column already exists
        try:
            conn.execute("ALTER TABLE zone_playlist_items ADD COLUMN spread_mode TEXT NOT NULL DEFAULT 'none'")
        except Exception:
            pass  # Column already exists

        # Multi-user tables
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

        # Global settings (auto-cleanup only; display settings live in displays table)
        conn.execute('''
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        ''')
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('auto_cleanup_enabled', 'true')")
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('auto_cleanup_days', '180')")

        # --- Migrate legacy pdf_files table if present ---
        legacy_table = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='pdf_files'"
        ).fetchone()

        if legacy_table:
            migrated = conn.execute(
                "SELECT COUNT(*) FROM media_items WHERE content_type = 'pdf'"
            ).fetchone()[0]

            if migrated == 0:
                old_pdfs = conn.execute(
                    'SELECT * FROM pdf_files ORDER BY upload_date ASC'
                ).fetchall()
                for pdf in old_pdfs:
                    conn.execute(
                        '''INSERT OR IGNORE INTO media_items
                           (content_type, filename, original_name, upload_date, file_size)
                           VALUES (?, ?, ?, ?, ?)''',
                        ('pdf', pdf['filename'], pdf['original_name'],
                         pdf['upload_date'], pdf['file_size'])
                    )

        # --- Create default display if none exist ---
        if conn.execute('SELECT COUNT(*) FROM displays').fetchone()[0] == 0:
            # Pull legacy per-display settings if available
            def _legacy(key, default):
                row = conn.execute(
                    'SELECT value FROM settings WHERE key = ?', (key,)
                ).fetchone()
                return row['value'] if row else default

            cycle_interval = int(_legacy('cycle_interval', '10'))
            background_color = _legacy('background_color', '#ffffff')
            progress_indicator = _legacy('progress_indicator', 'progress')

            # Map legacy selected_pdf_id → media_item id
            selected_media_id = 0
            old_sel_id = int(_legacy('selected_pdf_id', '0'))
            if old_sel_id > 0 and legacy_table:
                old_pdf = conn.execute(
                    'SELECT filename FROM pdf_files WHERE id = ?', (old_sel_id,)
                ).fetchone()
                if old_pdf:
                    media = conn.execute(
                        'SELECT id FROM media_items WHERE filename = ?',
                        (old_pdf['filename'],)
                    ).fetchone()
                    if media:
                        selected_media_id = media['id']

            conn.execute(
                '''INSERT INTO displays
                   (name, slug, width, height, selected_media_id,
                    cycle_interval, background_color, progress_indicator)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                ('Standard', 'default', 1920, 1080, selected_media_id,
                 cycle_interval, background_color, progress_indicator)
            )


# ---------- global settings ----------

def get_setting(key, default=None):
    with get_db() as conn:
        result = conn.execute(
            'SELECT value FROM settings WHERE key = ?', (key,)
        ).fetchone()
        return result['value'] if result else default


def set_setting(key, value):
    with get_db() as conn:
        conn.execute(
            'INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)',
            (key, str(value))
        )


# ---------- displays ----------

def get_all_displays():
    with get_db() as conn:
        return conn.execute('SELECT * FROM displays ORDER BY id').fetchall()


def get_display(display_id):
    with get_db() as conn:
        return conn.execute(
            'SELECT * FROM displays WHERE id = ?', (display_id,)
        ).fetchone()


def get_display_by_slug(slug):
    with get_db() as conn:
        return conn.execute(
            'SELECT * FROM displays WHERE slug = ?', (slug,)
        ).fetchone()


def create_display(name, slug, width, height):
    with get_db() as conn:
        conn.execute(
            "INSERT INTO displays (name, slug, width, height, progress_indicator) "
            "VALUES (?, ?, ?, ?, 'countdown')",
            (name, slug, width, height)
        )
        return conn.execute('SELECT last_insert_rowid()').fetchone()[0]


def update_display(display_id, **kwargs):
    allowed = {'name', 'width', 'height', 'selected_media_id',
               'cycle_interval', 'background_color', 'progress_indicator', 'video_fit', 'ambient_bg',
               'layout_preset'}
    fields = [(k, v) for k, v in kwargs.items() if k in allowed and v is not None]
    if not fields:
        return
    set_clause = ', '.join(f'{k} = ?' for k, _ in fields)
    values = [v for _, v in fields] + [display_id]
    with get_db() as conn:
        conn.execute(f'UPDATE displays SET {set_clause} WHERE id = ?', values)


def delete_display(display_id):
    with get_db() as conn:
        conn.execute('DELETE FROM display_schedules WHERE display_id = ?', (display_id,))
        conn.execute('DELETE FROM user_zone_permissions WHERE display_id = ?', (display_id,))
        conn.execute('DELETE FROM playlist_items WHERE display_id = ?', (display_id,))
        zone_ids = [r['id'] for r in conn.execute(
            'SELECT id FROM zones WHERE display_id = ?', (display_id,)
        ).fetchall()]
        for zone_id in zone_ids:
            conn.execute('DELETE FROM zone_playlist_items WHERE zone_id = ?', (zone_id,))
        conn.execute('DELETE FROM zones WHERE display_id = ?', (display_id,))
        conn.execute('DELETE FROM displays WHERE id = ?', (display_id,))


# ---------- media items ----------

def add_media(content_type, original_name, filename=None, url=None, file_size=0, scale_to_fit=False, user_id=0):
    with get_db() as conn:
        conn.execute(
            '''INSERT INTO media_items
               (content_type, filename, original_name, url, upload_date, file_size, scale_to_fit, user_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
            (content_type, filename, original_name, url, datetime.now(), file_size, 1 if scale_to_fit else 0, user_id)
        )
        return conn.execute('SELECT last_insert_rowid()').fetchone()[0]


def get_media(media_id):
    with get_db() as conn:
        return conn.execute(
            'SELECT * FROM media_items WHERE id = ?', (media_id,)
        ).fetchone()


def get_all_media(limit=None, offset=0, user_id=None):
    with get_db() as conn:
        where = 'WHERE m.user_id = ?' if user_id is not None else ''
        params = [user_id] if user_id is not None else []
        sql = f'''SELECT m.*, COALESCE(u.display_name, 'Admin') AS owner_name
                  FROM media_items m
                  LEFT JOIN users u ON m.user_id = u.id
                  {where}
                  ORDER BY m.upload_date DESC'''
        if limit:
            sql += ' LIMIT ? OFFSET ?'
            params.extend([limit, offset])
        return conn.execute(sql, params).fetchall()


def get_url_media_by_url(url):
    """Return the media_item with content_type='url' matching this URL, or None."""
    with get_db() as conn:
        return conn.execute(
            "SELECT * FROM media_items WHERE content_type = 'url' AND url = ?", (url,)
        ).fetchone()


def get_all_pdf_media():
    with get_db() as conn:
        return conn.execute(
            "SELECT * FROM media_items WHERE content_type = 'pdf'"
        ).fetchall()


def get_media_count(user_id=None):
    with get_db() as conn:
        if user_id is not None:
            return conn.execute('SELECT COUNT(*) FROM media_items WHERE user_id = ?', (user_id,)).fetchone()[0]
        return conn.execute('SELECT COUNT(*) FROM media_items').fetchone()[0]


def get_newest_media(user_id=None):
    with get_db() as conn:
        if user_id is not None:
            return conn.execute(
                'SELECT * FROM media_items WHERE user_id = ? ORDER BY upload_date DESC LIMIT 1',
                (user_id,)
            ).fetchone()
        return conn.execute(
            'SELECT * FROM media_items ORDER BY upload_date DESC LIMIT 1'
        ).fetchone()


def update_media_scale_to_fit(media_id, scale_to_fit):
    with get_db() as conn:
        conn.execute(
            'UPDATE media_items SET scale_to_fit = ? WHERE id = ?',
            (1 if scale_to_fit else 0, media_id)
        )


def update_media_url(media_id, url):
    with get_db() as conn:
        conn.execute('UPDATE media_items SET url = ? WHERE id = ?', (url, media_id))


def update_media_name(media_id, new_name):
    with get_db() as conn:
        conn.execute(
            'UPDATE media_items SET original_name = ? WHERE id = ?',
            (new_name, media_id)
        )


def _delete_media_in_conn(conn, media_id):
    """Delete a media item within an existing DB connection. Returns cleanup dict or None."""
    item = conn.execute(
        'SELECT * FROM media_items WHERE id = ?', (media_id,)
    ).fetchone()
    if not item:
        return None

    renders = conn.execute(
        'SELECT display_id, render_filename FROM pdf_renders WHERE media_id = ?',
        (media_id,)
    ).fetchall()

    spread_renders = conn.execute(
        'SELECT display_id, render_filename FROM pdf_spread_renders WHERE media_id = ?',
        (media_id,)
    ).fetchall()

    gallery_images = []
    if item['content_type'] == 'gallery':
        gallery_images = conn.execute(
            'SELECT filename FROM gallery_images WHERE media_id = ?', (media_id,)
        ).fetchall()
        conn.execute('DELETE FROM gallery_images WHERE media_id = ?', (media_id,))

    conn.execute('DELETE FROM pdf_renders WHERE media_id = ?', (media_id,))
    conn.execute('DELETE FROM pdf_spread_renders WHERE media_id = ?', (media_id,))
    conn.execute('DELETE FROM playlist_items WHERE media_id = ?', (media_id,))
    conn.execute('DELETE FROM zone_playlist_items WHERE media_id = ?', (media_id,))
    conn.execute('DELETE FROM media_items WHERE id = ?', (media_id,))
    conn.execute(
        'UPDATE displays SET selected_media_id = 0 WHERE selected_media_id = ?',
        (media_id,)
    )
    conn.execute(
        'UPDATE zones SET selected_media_id = 0 WHERE selected_media_id = ?',
        (media_id,)
    )

    return {
        'filename': item['filename'],
        'renders': (
            [(r['display_id'], r['render_filename']) for r in renders] +
            [(r['display_id'], r['render_filename']) for r in spread_renders]
        ),
        'gallery_images': [r['filename'] for r in gallery_images],
    }


def delete_media(media_id):
    """Delete a media item. Returns dict {filename, renders} for filesystem cleanup."""
    with get_db() as conn:
        return _delete_media_in_conn(conn, media_id)


# ---------- pdf renders ----------

def get_pdf_page_count(media_id, display_id):
    with get_db() as conn:
        return conn.execute(
            'SELECT COUNT(*) FROM pdf_renders WHERE media_id = ? AND display_id = ?',
            (media_id, display_id)
        ).fetchone()[0]


def get_all_pdf_page_counts():
    """Return {media_id: page_count} for all rendered PDFs (display-agnostic)."""
    with get_db() as conn:
        rows = conn.execute(
            '''SELECT media_id,
                      COUNT(*) / COUNT(DISTINCT display_id) AS cnt
               FROM pdf_renders
               GROUP BY media_id'''
        ).fetchall()
    return {r['media_id']: r['cnt'] for r in rows}


def add_pdf_render(media_id, display_id, page_number, render_filename):
    with get_db() as conn:
        conn.execute(
            '''INSERT OR REPLACE INTO pdf_renders
               (media_id, display_id, page_number, render_filename)
               VALUES (?, ?, ?, ?)''',
            (media_id, display_id, page_number, render_filename)
        )


def get_pdf_renders(media_id, display_id):
    with get_db() as conn:
        return conn.execute(
            '''SELECT * FROM pdf_renders
               WHERE media_id = ? AND display_id = ?
               ORDER BY page_number''',
            (media_id, display_id)
        ).fetchall()


def delete_pdf_renders(media_id, display_id):
    """Delete renders for a media+display pair. Returns list of render filenames."""
    with get_db() as conn:
        renders = conn.execute(
            '''SELECT render_filename FROM pdf_renders
               WHERE media_id = ? AND display_id = ?''',
            (media_id, display_id)
        ).fetchall()
        conn.execute(
            'DELETE FROM pdf_renders WHERE media_id = ? AND display_id = ?',
            (media_id, display_id)
        )
        return [r['render_filename'] for r in renders]


def delete_pdf_renders_for_display(display_id):
    """Delete all renders for a display. Returns list of render filenames."""
    with get_db() as conn:
        renders = conn.execute(
            'SELECT render_filename FROM pdf_renders WHERE display_id = ?',
            (display_id,)
        ).fetchall()
        conn.execute('DELETE FROM pdf_renders WHERE display_id = ?', (display_id,))
        return [r['render_filename'] for r in renders]


# ---------- pdf spread renders ----------

def add_pdf_spread_render(media_id, display_id, spread_type, page_number, render_filename):
    with get_db() as conn:
        conn.execute(
            '''INSERT OR REPLACE INTO pdf_spread_renders
               (media_id, display_id, spread_type, page_number, render_filename)
               VALUES (?, ?, ?, ?, ?)''',
            (media_id, display_id, spread_type, page_number, render_filename)
        )


def get_pdf_spread_renders(media_id, display_id, spread_type):
    with get_db() as conn:
        return conn.execute(
            '''SELECT * FROM pdf_spread_renders
               WHERE media_id = ? AND display_id = ? AND spread_type = ?
               ORDER BY page_number''',
            (media_id, display_id, spread_type)
        ).fetchall()


def delete_pdf_spread_renders(media_id, display_id):
    """Delete spread renders for a media+display pair. Returns list of filenames."""
    with get_db() as conn:
        rows = conn.execute(
            'SELECT render_filename FROM pdf_spread_renders WHERE media_id = ? AND display_id = ?',
            (media_id, display_id)
        ).fetchall()
        conn.execute(
            'DELETE FROM pdf_spread_renders WHERE media_id = ? AND display_id = ?',
            (media_id, display_id)
        )
        return [r['render_filename'] for r in rows]


def delete_pdf_spread_renders_for_display(display_id):
    """Delete all spread renders for a display. Returns list of filenames."""
    with get_db() as conn:
        rows = conn.execute(
            'SELECT render_filename FROM pdf_spread_renders WHERE display_id = ?',
            (display_id,)
        ).fetchall()
        conn.execute('DELETE FROM pdf_spread_renders WHERE display_id = ?', (display_id,))
        return [r['render_filename'] for r in rows]


# ---------- cleanup ----------

def cleanup_old_media(upload_folder):
    """Delete file-based media items older than the configured threshold."""
    from datetime import timedelta

    if get_setting('auto_cleanup_enabled', 'true').lower() != 'true':
        return 0

    cleanup_days = int(get_setting('auto_cleanup_days', '180'))
    cutoff_date = datetime.now() - timedelta(days=cleanup_days)

    with get_db() as conn:
        # Collect all actively-selected media IDs across displays and playlists
        active_ids = set()
        displays = conn.execute('SELECT id, selected_media_id FROM displays').fetchall()
        for d in displays:
            sid = d['selected_media_id']
            if sid == 0:
                newest = conn.execute(
                    'SELECT id FROM media_items WHERE user_id = 0 ORDER BY upload_date DESC LIMIT 1'
                ).fetchone()
                if newest:
                    active_ids.add(newest['id'])
            else:
                active_ids.add(sid)

        for row in conn.execute('SELECT DISTINCT media_id FROM playlist_items').fetchall():
            active_ids.add(row['media_id'])
        for row in conn.execute('SELECT DISTINCT media_id FROM zone_playlist_items').fetchall():
            active_ids.add(row['media_id'])

        if active_ids:
            placeholders = ','.join('?' * len(active_ids))
            old_items = conn.execute(
                f'''SELECT * FROM media_items
                    WHERE upload_date < ?
                    AND filename IS NOT NULL
                    AND id NOT IN ({placeholders})''',
                [cutoff_date, *active_ids]
            ).fetchall()
        else:
            old_items = conn.execute(
                '''SELECT * FROM media_items
                   WHERE upload_date < ? AND filename IS NOT NULL''',
                (cutoff_date,)
            ).fetchall()

        deleted_count = 0
        for item in old_items:
            renders = conn.execute(
                'SELECT display_id, render_filename FROM pdf_renders WHERE media_id = ?',
                (item['id'],)
            ).fetchall()
            conn.execute('DELETE FROM pdf_renders WHERE media_id = ?', (item['id'],))
            conn.execute('DELETE FROM media_items WHERE id = ?', (item['id'],))

            filepath = os.path.join(upload_folder, item['filename'])
            if os.path.exists(filepath):
                try:
                    os.remove(filepath)
                    deleted_count += 1
                except OSError:
                    pass

            for r in renders:
                render_path = os.path.join('renders', str(r['display_id']), r['render_filename'])
                if os.path.exists(render_path):
                    try:
                        os.remove(render_path)
                    except OSError:
                        pass

        return deleted_count


# ---------- playlists ----------

def get_playlist_items(display_id, user_id=0):
    """Return ordered playlist items joined with media info."""
    with get_db() as conn:
        return conn.execute(
            '''SELECT pi.id, pi.display_id, pi.media_id, pi.duration, pi.position, pi.spread_mode, pi.user_id,
                      m.content_type, m.original_name, m.filename, m.url, m.scale_to_fit
               FROM playlist_items pi
               JOIN media_items m ON pi.media_id = m.id
               WHERE pi.display_id = ? AND pi.user_id = ?
               ORDER BY pi.position''',
            (display_id, user_id)
        ).fetchall()


def add_playlist_item(display_id, media_id, duration, user_id=0):
    with get_db() as conn:
        max_pos = conn.execute(
            'SELECT COALESCE(MAX(position), 0) FROM playlist_items WHERE display_id = ? AND user_id = ?',
            (display_id, user_id)
        ).fetchone()[0]
        conn.execute(
            'INSERT INTO playlist_items (display_id, media_id, duration, position, user_id) VALUES (?, ?, ?, ?, ?)',
            (display_id, media_id, duration, max_pos + 1, user_id)
        )
        return conn.execute('SELECT last_insert_rowid()').fetchone()[0]


def remove_playlist_item(item_id, display_id, user_id=0):
    with get_db() as conn:
        conn.execute(
            'DELETE FROM playlist_items WHERE id = ? AND display_id = ? AND user_id = ?',
            (item_id, display_id, user_id)
        )
        # Repack positions to stay gapless
        items = conn.execute(
            'SELECT id FROM playlist_items WHERE display_id = ? AND user_id = ? ORDER BY position',
            (display_id, user_id)
        ).fetchall()
        for i, row in enumerate(items, 1):
            conn.execute('UPDATE playlist_items SET position = ? WHERE id = ?', (i, row['id']))


def update_playlist_item_duration(item_id, display_id, duration, user_id=0):
    with get_db() as conn:
        conn.execute(
            'UPDATE playlist_items SET duration = ? WHERE id = ? AND display_id = ? AND user_id = ?',
            (duration, item_id, display_id, user_id)
        )


def update_playlist_item_spread_mode(item_id, display_id, spread_mode, user_id=0):
    with get_db() as conn:
        conn.execute(
            'UPDATE playlist_items SET spread_mode = ? WHERE id = ? AND display_id = ? AND user_id = ?',
            (spread_mode, item_id, display_id, user_id)
        )


def reorder_playlist_items(display_id, ordered_ids, user_id=0):
    """Set positions from an ordered list of item IDs."""
    with get_db() as conn:
        for i, item_id in enumerate(ordered_ids, 1):
            conn.execute(
                'UPDATE playlist_items SET position = ? WHERE id = ? AND display_id = ? AND user_id = ?',
                (i, item_id, display_id, user_id)
            )


def move_playlist_item(item_id, display_id, direction, user_id=0):
    """Swap item with its neighbour. direction: -1 = up, +1 = down."""
    with get_db() as conn:
        items = conn.execute(
            'SELECT id, position FROM playlist_items WHERE display_id = ? AND user_id = ? ORDER BY position',
            (display_id, user_id)
        ).fetchall()
        ids = [row['id'] for row in items]
        if item_id not in ids:
            return
        idx = ids.index(item_id)
        swap_idx = idx + direction
        if swap_idx < 0 or swap_idx >= len(ids):
            return
        pos_a = items[idx]['position']
        pos_b = items[swap_idx]['position']
        conn.execute('UPDATE playlist_items SET position = ? WHERE id = ?', (pos_b, ids[idx]))
        conn.execute('UPDATE playlist_items SET position = ? WHERE id = ?', (pos_a, ids[swap_idx]))


# ---------- galleries ----------

def add_gallery(name, user_id=0):
    with get_db() as conn:
        conn.execute(
            'INSERT INTO media_items (content_type, original_name, upload_date, file_size, user_id) VALUES (?, ?, ?, ?, ?)',
            ('gallery', name, datetime.now(), 0, user_id)
        )
        return conn.execute('SELECT last_insert_rowid()').fetchone()[0]


def get_gallery_images(media_id):
    with get_db() as conn:
        return conn.execute(
            'SELECT * FROM gallery_images WHERE media_id = ? ORDER BY position',
            (media_id,)
        ).fetchall()


def add_gallery_image(media_id, filename, original_name, file_size):
    with get_db() as conn:
        max_pos = conn.execute(
            'SELECT COALESCE(MAX(position), 0) FROM gallery_images WHERE media_id = ?',
            (media_id,)
        ).fetchone()[0]
        conn.execute(
            'INSERT INTO gallery_images (media_id, filename, original_name, file_size, position) VALUES (?, ?, ?, ?, ?)',
            (media_id, filename, original_name, file_size, max_pos + 1)
        )
        return conn.execute('SELECT last_insert_rowid()').fetchone()[0]


def remove_gallery_image(image_id, media_id):
    """Delete one image from a gallery. Returns filename for filesystem cleanup, or None."""
    with get_db() as conn:
        row = conn.execute(
            'SELECT filename FROM gallery_images WHERE id = ? AND media_id = ?',
            (image_id, media_id)
        ).fetchone()
        if not row:
            return None
        conn.execute(
            'DELETE FROM gallery_images WHERE id = ? AND media_id = ?',
            (image_id, media_id)
        )
        items = conn.execute(
            'SELECT id FROM gallery_images WHERE media_id = ? ORDER BY position',
            (media_id,)
        ).fetchall()
        for i, r in enumerate(items, 1):
            conn.execute('UPDATE gallery_images SET position = ? WHERE id = ?', (i, r['id']))
        return row['filename']


def reorder_gallery_images(media_id, ordered_ids):
    with get_db() as conn:
        for i, image_id in enumerate(ordered_ids, 1):
            conn.execute(
                'UPDATE gallery_images SET position = ? WHERE id = ? AND media_id = ?',
                (i, image_id, media_id)
            )


# ---------- zones ----------

def get_zones_for_display(display_id):
    with get_db() as conn:
        return conn.execute(
            'SELECT * FROM zones WHERE display_id = ? ORDER BY zone_index',
            (display_id,)
        ).fetchall()


def get_zone(zone_id):
    with get_db() as conn:
        return conn.execute('SELECT * FROM zones WHERE id = ?', (zone_id,)).fetchone()


def get_zone_by_display_and_index(display_id, zone_index):
    with get_db() as conn:
        return conn.execute(
            'SELECT * FROM zones WHERE display_id = ? AND zone_index = ?',
            (display_id, zone_index)
        ).fetchone()


def create_zone(display_id, zone_index):
    with get_db() as conn:
        conn.execute(
            'INSERT OR IGNORE INTO zones (display_id, zone_index) VALUES (?, ?)',
            (display_id, zone_index)
        )
        return conn.execute(
            'SELECT id FROM zones WHERE display_id = ? AND zone_index = ?',
            (display_id, zone_index)
        ).fetchone()['id']


def delete_zone(zone_id):
    with get_db() as conn:
        conn.execute('DELETE FROM zone_playlist_items WHERE zone_id = ?', (zone_id,))
        conn.execute('DELETE FROM zones WHERE id = ?', (zone_id,))


def update_zone_settings(zone_id, selected_media_id, cycle_interval):
    with get_db() as conn:
        conn.execute(
            'UPDATE zones SET selected_media_id = ?, cycle_interval = ? WHERE id = ?',
            (selected_media_id, cycle_interval, zone_id)
        )


def get_zone_playlist_items(zone_id, user_id=0):
    with get_db() as conn:
        return conn.execute(
            '''SELECT zpi.id, zpi.zone_id, zpi.media_id, zpi.duration, zpi.position, zpi.spread_mode, zpi.user_id,
                      m.content_type, m.original_name, m.filename, m.url, m.scale_to_fit
               FROM zone_playlist_items zpi
               JOIN media_items m ON zpi.media_id = m.id
               WHERE zpi.zone_id = ? AND zpi.user_id = ?
               ORDER BY zpi.position''',
            (zone_id, user_id)
        ).fetchall()


def add_zone_playlist_item(zone_id, media_id, duration, user_id=0):
    with get_db() as conn:
        max_pos = conn.execute(
            'SELECT COALESCE(MAX(position), 0) FROM zone_playlist_items WHERE zone_id = ? AND user_id = ?',
            (zone_id, user_id)
        ).fetchone()[0]
        conn.execute(
            'INSERT INTO zone_playlist_items (zone_id, media_id, duration, position, user_id) VALUES (?, ?, ?, ?, ?)',
            (zone_id, media_id, duration, max_pos + 1, user_id)
        )
        return conn.execute('SELECT last_insert_rowid()').fetchone()[0]


def remove_zone_playlist_item(item_id, zone_id, user_id=0):
    with get_db() as conn:
        conn.execute(
            'DELETE FROM zone_playlist_items WHERE id = ? AND zone_id = ? AND user_id = ?',
            (item_id, zone_id, user_id)
        )
        items = conn.execute(
            'SELECT id FROM zone_playlist_items WHERE zone_id = ? AND user_id = ? ORDER BY position',
            (zone_id, user_id)
        ).fetchall()
        for i, row in enumerate(items, 1):
            conn.execute('UPDATE zone_playlist_items SET position = ? WHERE id = ?', (i, row['id']))


def update_zone_playlist_item_duration(item_id, zone_id, duration, user_id=0):
    with get_db() as conn:
        conn.execute(
            'UPDATE zone_playlist_items SET duration = ? WHERE id = ? AND zone_id = ? AND user_id = ?',
            (duration, item_id, zone_id, user_id)
        )


def update_zone_playlist_item_spread_mode(item_id, zone_id, spread_mode, user_id=0):
    with get_db() as conn:
        conn.execute(
            'UPDATE zone_playlist_items SET spread_mode = ? WHERE id = ? AND zone_id = ? AND user_id = ?',
            (spread_mode, item_id, zone_id, user_id)
        )


def reorder_zone_playlist_items(zone_id, ordered_ids, user_id=0):
    with get_db() as conn:
        for i, item_id in enumerate(ordered_ids, 1):
            conn.execute(
                'UPDATE zone_playlist_items SET position = ? WHERE id = ? AND zone_id = ? AND user_id = ?',
                (i, item_id, zone_id, user_id)
            )


# ---------- users ----------

def get_all_users():
    with get_db() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE role != 'admin' ORDER BY display_name COLLATE NOCASE, id"
        ).fetchall()


def get_user(user_id):
    with get_db() as conn:
        return conn.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()


def get_user_by_username(username):
    with get_db() as conn:
        return conn.execute(
            'SELECT * FROM users WHERE LOWER(username) = LOWER(?)', (username,)
        ).fetchone()


def create_user(username, password_hash, display_name, role='user'):
    with get_db() as conn:
        conn.execute(
            'INSERT INTO users (username, password_hash, display_name, role, created_at) VALUES (?, ?, ?, ?, ?)',
            (username, password_hash, display_name, role, datetime.now())
        )
        return conn.execute('SELECT last_insert_rowid()').fetchone()[0]


def update_user(user_id, username=None, display_name=None, password_hash=None):
    fields = []
    values = []
    if username is not None:
        fields.append('username = ?')
        values.append(username)
    if display_name is not None:
        fields.append('display_name = ?')
        values.append(display_name)
    if password_hash is not None:
        fields.append('password_hash = ?')
        values.append(password_hash)
    if not fields:
        return
    values.append(user_id)
    with get_db() as conn:
        conn.execute(f"UPDATE users SET {', '.join(fields)} WHERE id = ?", values)


def delete_user(user_id):
    """Delete a user, reset their schedules, and delete their media items.
    Returns list of media cleanup dicts for physical file removal."""
    cleanups = []
    with get_db() as conn:
        conn.execute('DELETE FROM user_zone_permissions WHERE user_id = ?', (user_id,))
        conn.execute('UPDATE display_schedules SET user_id = 0 WHERE user_id = ?', (user_id,))
        conn.execute('DELETE FROM playlist_items WHERE user_id = ?', (user_id,))
        conn.execute('DELETE FROM zone_playlist_items WHERE user_id = ?', (user_id,))
        media_rows = conn.execute(
            'SELECT id FROM media_items WHERE user_id = ?', (user_id,)
        ).fetchall()
        for row in media_rows:
            res = _delete_media_in_conn(conn, row['id'])
            if res:
                cleanups.append(res)
        conn.execute('DELETE FROM users WHERE id = ?', (user_id,))
    return cleanups


# ---------- display schedules ----------

def get_display_schedule(display_id):
    """Return {weekday (0..6): user_id} for a display (defaults to 0 = Admin)."""
    schedule = {wd: 0 for wd in range(7)}
    with get_db() as conn:
        rows = conn.execute(
            'SELECT weekday, user_id FROM display_schedules WHERE display_id = ?',
            (display_id,)
        ).fetchall()
        for r in rows:
            if 0 <= r['weekday'] <= 6:
                schedule[r['weekday']] = r['user_id']
    return schedule


def get_scheduled_user_for_display(display_id, weekday):
    with get_db() as conn:
        row = conn.execute(
            'SELECT user_id FROM display_schedules WHERE display_id = ? AND weekday = ?',
            (display_id, weekday)
        ).fetchone()
        return row['user_id'] if row else 0


def set_display_schedule(display_id, weekday, user_id):
    with get_db() as conn:
        conn.execute(
            '''INSERT INTO display_schedules (display_id, weekday, user_id)
               VALUES (?, ?, ?)
               ON CONFLICT(display_id, weekday) DO UPDATE SET user_id = excluded.user_id''',
            (display_id, weekday, user_id)
        )


def get_user_weekdays_for_display(user_id, display_id):
    with get_db() as conn:
        rows = conn.execute(
            'SELECT weekday FROM display_schedules WHERE display_id = ? AND user_id = ? ORDER BY weekday',
            (display_id, user_id)
        ).fetchall()
        return [r['weekday'] for r in rows]


# ---------- user zone permissions ----------

def get_user_zone_permissions(user_id, display_id):
    """Return set of permitted zone_index ints for a user on a display."""
    with get_db() as conn:
        rows = conn.execute(
            'SELECT zone_index FROM user_zone_permissions WHERE user_id = ? AND display_id = ?',
            (user_id, display_id)
        ).fetchall()
        return {r['zone_index'] for r in rows}


def has_user_zone_permission(user_id, display_id, zone_index):
    with get_db() as conn:
        row = conn.execute(
            'SELECT 1 FROM user_zone_permissions WHERE user_id = ? AND display_id = ? AND zone_index = ?',
            (user_id, display_id, zone_index)
        ).fetchone()
        return row is not None


def set_user_zone_permissions(user_id, display_id, zone_indices):
    with get_db() as conn:
        conn.execute(
            'DELETE FROM user_zone_permissions WHERE user_id = ? AND display_id = ?',
            (user_id, display_id)
        )
        for zi in zone_indices:
            conn.execute(
                'INSERT OR IGNORE INTO user_zone_permissions (user_id, display_id, zone_index) VALUES (?, ?, ?)',
                (user_id, display_id, int(zi))
            )
