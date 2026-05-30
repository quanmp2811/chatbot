"""Backward-compatible file reader wrapper."""

from pathlib import Path

from app.services.file_parser import extract_text


def read_file_content(file_path: str) -> str:
    return extract_text(Path(file_path))
