# CLAUDE.md

This file provides guidance to AI assistants and developers when working with code in this repository.

## Project Overview

Infoboard is a Flask-based digital signage web application designed for Raspberry Pi kiosk displays. It supports multiple displays, multi-zone layouts (up to 4 zones), per-zone playlists, multiple media types (PDF, image, image gallery, local video, YouTube embed, proxied website), multi-user content isolation, and weekday-based scheduling.

## Technology Stack

- **Backend**: Flask 3 (Python web framework)
- **WSGI Server**: Waitress 3.0 (production server)
- **Authentication**: Flask-Login 0.6.3 with bcrypt 4.1.2
- **Database**: SQLite (`infoboard.db`, managed via `models.py` and `migrations/`)
- **PDF Rendering**: `pdftoppm` (`poppler-utils`) + Pillow for spread stitching — pre-renders PDFs server-side into `renders/<display_id>/`
- **Frontend**: Vanilla JS + SortableJS (`static/js/sortable.min.js`)
- **Storage**: Local filesystem (`uploads/`, `renders/`)

## Common Commands

### Setup and Installation

```bash
# Install dependencies
pip install -r requirements.txt

# Generate admin password hash
python3 generate_password_hash.py

# Create configuration from template
cp config.json.example config.json
# Then edit config.json with your settings
```

### Running and Migrating

```bash
# Apply pending database migrations
python3 migrate.py

# Start production server with Waitress
python3 app.py

# URLs:
# Display: http://localhost:8080/display/<slug>
# Admin:   http://localhost:8080/admin
```

### Database Reset

```bash
rm infoboard.db
python3 app.py  # Reinitializes database via models.init_db()
```

## Architecture

### Application Structure

- **`app.py`**: Main Flask application, routes, background PDF rendering, website proxy, authentication, and API endpoints.
- **`models.py`**: SQLite database schema initialization (`init_db`) and CRUD operations with `get_db()` context manager.
- **`migrate.py` & `migrations/`**: Numbered migration scripts (`0001_...` through `0006_multi_user.py`) applied during updates via `./update.sh`.
- **`config.py`**: Loads configuration from `config.json`.
- **`templates/admin.html`**: Role-aware management UI (displays, playlists, media library, user & schedule management).
- **`templates/display.html`**: Fullscreen kiosk display client polling `/api/display/<slug>/zone/<zi>/current` every 10 seconds.
- **`templates/login.html`**: Login form for both Administrator and standard users.

### Key Components

1. **Multi-Display & Multi-Zone Display View** (`/display/<slug>`):
   - Supports 7 `LAYOUT_PRESETS` (`fullscreen`, `split-h`, `split-v`, `sidebar-r`, `sidebar-b`, `thirds-h`, `quad`).
   - Each zone runs a `SlideEngine` polling `/api/display/<slug>/zone/<zone_index>/current` every 10 seconds.
   - Automatically detects weekday transitions (00:00), content updates, layout changes, and app deploys (`APP_VERSION`).
   - When `layout_preset == 'fullscreen'`, `zone_api(..., 0)` delegates directly to `display_api(slug)`.

2. **Multi-User Roles & Permissions**:
   - **Administrator** (`config.json` credentials, `db_id = 0`, `is_admin = True`):
     - Full access to display settings, layout presets, global cleanup settings, user management, weekday schedules, and all media/playlists.
     - Can inspect/edit any user's playlists via `?view_user=<id>`.
   - **Standard Users** (`users` table, `role = 'user'`, `db_id > 0`):
     - Isolated media library (`media_items.user_id = current_user.db_id`).
     - Can only view and edit playlists for zones explicitly permitted in `user_zone_permissions`.
     - Cannot access display settings, layouts, schedules, global settings, or other users' media (`@admin_required` protects admin-only routes; `_can_access_media` and `_resolve_playlist_user_id` enforce ownership and zone permissions).

3. **Weekday Scheduling & Fallback**:
   - `display_schedules` maps `(display_id, weekday 0..6)` to a `user_id` (`0` = Administrator, `> 0` = user).
   - `_effective_user_for_zone(display_id, zone_index)` checks today's scheduled user and whether that user has permission for `zone_index`.
   - Unpermitted zones and empty user playlists automatically fall back to the Administrator's playlist (`user_id = 0`) or default content (`get_newest_media(user_id=0)`).

4. **Admin UI Design (`templates/admin.html`)**:
   - Collapsible drawers per display card (`⚙ Einstellungen ▾`, `📅 Wochenplan ▾`) and collapsible creation forms (`+ Neues Display`, `+ Neuer Benutzer`), with open drawer state persisted in `sessionStorage`.
   - Display preview thumbnails (`240×136 px`) showing layout zones (`Admin` label on unpermitted zones when viewing as a user).
   - In multi-zone layouts, the fullscreen playlist editor is hidden with an informational banner pointing to the zone playlists below.
   - Dedicated `Benutzer` tab (admin-only) to manage user labels, usernames, passwords, per-display zone checkboxes, and weekday assignments.

5. **PDF Rendering**:
   - PDFs are pre-rendered in a background thread (`_render_pdf_background`) using `pdftoppm` and Pillow (`_render_pdf_spreads` for `paired`/`book` spreads).
   - Output images are stored in `renders/<display_id>/` and tracked via `/api/render-jobs` (filtered by owner for non-admin users).

### Database Schema (`models.py`)

- `displays`: `id`, `name`, `slug`, `width`, `height`, `selected_media_id`, `cycle_interval`, `background_color`, `progress_indicator`, `video_fit`, `ambient_bg`, `layout_preset`
- `zones`: `id`, `display_id`, `zone_index`, `selected_media_id`, `cycle_interval`
- `media_items`: `id`, `content_type`, `filename`, `original_name`, `url`, `upload_date`, `file_size`, `scale_to_fit`, `user_id` (`0` = Admin)
- `gallery_images`: `id`, `media_id`, `filename`, `original_name`, `file_size`, `position`
- `pdf_renders` & `pdf_spread_renders`: pre-rendered page PNG references per `media_id` and `display_id`
- `playlist_items`: `id`, `display_id`, `media_id`, `duration`, `position`, `spread_mode`, `user_id`
- `zone_playlist_items`: `id`, `zone_id`, `media_id`, `duration`, `position`, `spread_mode`, `user_id`
- `users`: `id`, `username`, `password_hash`, `display_name`, `role`, `created_at`
- `display_schedules`: `id`, `display_id`, `weekday` (`0`–`6`), `user_id` (`0` = Admin)
- `user_zone_permissions`: `id`, `user_id`, `display_id`, `zone_index`
- `settings`: global key/value settings (`auto_cleanup_enabled`, `auto_cleanup_days`)

## Important Patterns

### Database Access

Always use the context manager from `models.py`:

```python
from models import get_db

with get_db() as conn:
    result = conn.execute('SELECT * FROM displays').fetchall()
    # conn.commit() called automatically on success
    # conn.rollback() called on exception
```

### Permission & Isolation Enforcement

- Admin-only routes must use `@admin_required`.
- Media mutations must check `_can_access_media(media)`.
- Playlist mutations must resolve the effective `user_id` via `_resolve_playlist_user_id(display_id, zone_index)` and scope SQL queries by `user_id`.

## Routes Overview

**Public / Display Routes**:
- `GET /` & `GET /display`: Redirect to first display (`/display/<slug>`)
- `GET /display/<slug>`: Fullscreen kiosk view
- `GET /api/display/<slug>` & `GET /api/display/<slug>/zone/<int:zone_index>/current`: JSON APIs for display/zone content (respecting weekday schedule & zone permissions)
- `GET /api/current-pdf`: Legacy display API proxy
- `GET /uploads/<filename>` & `GET /renders/<int:display_id>/<filename>`: Serve media and rendered PDF pages
- `GET /proxy?url=...`: Website proxy for whitelisted `url`-type media items
- `GET|POST /login`: Authentication for Admin and DB users

**Authenticated Routes** (Users & Admin, scoped by ownership & zone permissions):
- `GET /admin`: Management UI (role-filtered)
- `GET /logout`: Logout
- `GET /api/render-jobs`: Background PDF render status
- `POST /admin/upload`, `POST /admin/url`, `POST /admin/gallery/create`
- `POST /admin/gallery/<id>/upload`, `.../image/<id>/remove`, `.../image/reorder`
- `POST /admin/media/<id>/url-options`, `.../youtube-options`, `POST /admin/rename/<id>`, `POST /admin/delete/<id>`
- `POST /admin/display/<id>/playlist/*` and `POST /admin/zone/<id>/playlist/*`

**Admin-Only Routes** (`@admin_required`):
- `POST /admin/display/create`, `.../settings`, `.../delete`, `.../layout`, `.../regenerate`, `.../schedule`
- `POST /admin/users/create`, `.../update`, `.../delete`, `.../access`
- `POST /admin/settings`, `POST /admin/cleanup`

## Workflow
workflow_initialized = true
