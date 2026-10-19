"""Walk an estate directory or zip and parse the formats the miners understand."""

from __future__ import annotations

import sqlite3
import zipfile
from pathlib import Path
from typing import Any

import pandas as pd
import yaml
from pypdf import PdfReader

KIND_BY_SUFFIX = {
    ".csv": "csv",
    ".tsv": "csv",
    ".xlsx": "xlsx",
    ".sqlite": "sqlite",
    ".db": "sqlite",
    ".md": "markdown",
    ".txt": "markdown",
    ".pdf": "pdf",
    ".py": "python",
    ".sql": "sql",
    ".yaml": "yaml",
    ".yml": "yaml",
}


def extract_zip(zip_path: Path, dest: Path) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as archive:
        names = [name for name in archive.namelist() if not name.endswith("/") and ".." not in name]
        for name in names:
            target = dest / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(name))
    return dest


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _pdf_text(path: Path) -> str:
    reader = PdfReader(str(path))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def _frame_profile(frame: pd.DataFrame) -> dict[str, Any]:
    columns = []
    for column in frame.columns:
        columns.append(
            {
                "name": str(column),
                "dtype": str(frame[column].dtype),
                "nulls": int(frame[column].isna().sum()),
                "unique": int(frame[column].nunique(dropna=True)),
            }
        )
    return {"rows": int(len(frame)), "columns": columns}


def scan_estate(estate: Path) -> tuple[list[dict[str, Any]], dict[str, pd.DataFrame]]:
    documents: list[dict[str, Any]] = []
    frames: dict[str, pd.DataFrame] = {}
    paths = [path for path in estate.rglob("*") if path.is_file() and not path.name.startswith(".")]
    paths.sort()
    for path in paths:
        if path.name == "ground_truth.json" or "__MACOSX" in path.parts:
            continue
        kind = KIND_BY_SUFFIX.get(path.suffix.lower())
        if kind is None:
            continue
        relative = str(path.relative_to(estate))
        doc: dict[str, Any] = {"path": relative, "kind": kind, "text": None, "tables": []}
        if kind == "csv":
            frame = pd.read_csv(path)
            table = path.stem
            frames[table] = frame
            doc["tables"] = [table]
            doc["profile"] = {table: _frame_profile(frame)}
        elif kind == "xlsx":
            book = pd.read_excel(path, sheet_name=None)
            doc["profile"] = {}
            for sheet, frame in book.items():
                table = f"{path.stem}_{sheet}" if len(book) > 1 else path.stem
                if table not in frames:
                    frames[table] = frame
                doc["tables"].append(table)
                doc["profile"][table] = _frame_profile(frame)
        elif kind == "sqlite":
            connection = sqlite3.connect(path)
            try:
                names = [
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                    )
                ]
                doc["profile"] = {}
                for name in names:
                    frame = pd.read_sql_query(f'SELECT * FROM "{name}"', connection)
                    # CSV/XLSX already captured the same table. Keep one frame for mining.
                    if name not in frames:
                        frames[name] = frame
                    doc["tables"].append(name)
                    doc["profile"][name] = _frame_profile(frame)
            finally:
                connection.close()
        elif kind == "pdf":
            doc["text"] = _pdf_text(path)
        elif kind == "yaml":
            doc["text"] = _read_text(path)
            try:
                doc["parsed"] = yaml.safe_load(doc["text"])
            except yaml.YAMLError:
                doc["parsed"] = None
        else:
            doc["text"] = _read_text(path)
        documents.append(doc)
    return documents, frames
