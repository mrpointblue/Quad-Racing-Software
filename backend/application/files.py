from pathlib import Path
from sqlalchemy.orm import Session
from backend.database import SessionLocal
from fastapi import UploadFile
from datetime import datetime
import sqlite3
from backend.constants import CLUB_LOGO_ALLOWED_EXTENSIONS, CLUB_LOGO_MAX_SIZE_BYTES, CLUB_LOGO_UPLOAD_DIR, DATABASE_BACKUP_DIR, DATABASE_IMPORT_ALLOWED_EXTENSIONS, DATABASE_IMPORT_MAX_SIZE_BYTES, REQUIRED_DATABASE_TABLES
from backend.application.settings_service import get_club_logo_path_from_db, set_setting


def get_club_logo_path() -> str:
    db = SessionLocal()

    try:
        return get_club_logo_path_from_db(db)
    finally:
        db.close()

def get_club_logo_file_path_from_db(db: Session) -> str | None:
    logo_path = get_club_logo_path_from_db(db)

    if not logo_path:
        return None

    relative_path = logo_path.lstrip("/")

    if not relative_path.startswith("static/"):
        return None

    file_path = Path("backend") / relative_path

    if not file_path.exists():
        return None

    return str(file_path)

def remove_existing_club_logos():
    CLUB_LOGO_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    for logo_file in CLUB_LOGO_UPLOAD_DIR.glob("club_logo.*"):
        if logo_file.is_file():
            logo_file.unlink(missing_ok=True)

def clear_club_logo_settings(db: Session):
    set_setting(db, "club_logo_path", "")
    set_setting(db, "club_logo_original_filename", "")
    set_setting(db, "club_logo_uploaded_at", "")

def validate_club_logo_upload(file: UploadFile, file_bytes: bytes) -> str:
    original_filename = Path(file.filename or "").name
    extension = Path(original_filename).suffix.lower()

    if extension not in CLUB_LOGO_ALLOWED_EXTENSIONS:
        raise ValueError("Ungültiger Dateityp. Erlaubt sind PNG, JPG und JPEG.")

    if len(file_bytes) > CLUB_LOGO_MAX_SIZE_BYTES:
        raise ValueError("Die Datei ist zu groß. Maximal erlaubt sind 2 MB.")

    if not file_bytes:
        raise ValueError("Die Datei ist leer.")

    return extension

def validate_database_upload(file: UploadFile, file_bytes: bytes) -> str:
    original_filename = Path(file.filename or "").name
    extension = Path(original_filename).suffix.lower()

    if extension not in DATABASE_IMPORT_ALLOWED_EXTENSIONS:
        raise ValueError("Ungültiger Dateityp. Erlaubt sind SQLite-Datenbanken.")

    if len(file_bytes) > DATABASE_IMPORT_MAX_SIZE_BYTES:
        raise ValueError("Die Datei ist zu groß.")

    if not file_bytes:
        raise ValueError("Die Datei ist leer.")

    return extension

def validate_sqlite_database(path: Path) -> bool:
    try:
        connection = sqlite3.connect(path)

        try:
            integrity_result = connection.execute(
                "PRAGMA integrity_check;"
            ).fetchone()

            if not integrity_result or integrity_result[0] != "ok":
                return False

            table_rows = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table';"
            ).fetchall()
            table_names = {row[0] for row in table_rows}
        finally:
            connection.close()
    except sqlite3.DatabaseError:
        return False

    return REQUIRED_DATABASE_TABLES.issubset(table_names)

def get_database_backup_items() -> list[dict]:
    DATABASE_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backup_items = []

    for backup_file in sorted(
        DATABASE_BACKUP_DIR.glob("*.sqlite"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )[:5]:
        stat = backup_file.stat()
        backup_items.append(
            {
                "name": backup_file.name,
                "modified_at": datetime.fromtimestamp(stat.st_mtime).strftime("%d.%m.%Y %H:%M:%S"),
                "size_mb": f"{stat.st_size / (1024 * 1024):.1f} MB",
            }
        )

    return backup_items
