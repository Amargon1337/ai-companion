"""Document upload processing."""
from __future__ import annotations

import os
import uuid
from collections.abc import Callable
from typing import Any

from aiogram import types

from companion.config import BASE_DIR, MAX_DOCUMENT_CHARS, TEXT_EXTENSIONS
from companion.llm import client as llm
from companion.memory.store import MemoryStore


def read_text_file(path: str) -> str:
    for enc in ("utf-8", "utf-8-sig", "cp1251", "latin-1"):
        try:
            with open(path, encoding=enc) as f:
                return f.read()
        except UnicodeDecodeError:
            continue
    raise ValueError("Не удалось определить кодировку")


def extract_pdf(path: str) -> str:
    try:
        from pypdf import PdfReader
        reader = PdfReader(path)
        return "\n".join(p.extract_text() for p in reader.pages if p.extract_text()).strip()
    except Exception:
        return ""


def extract_docx(path: str) -> str:
    try:
        from docx import Document
        doc = Document(path)
        return "\n".join(p.text for p in doc.paragraphs if p.text.strip()).strip()
    except Exception:
        return ""


async def extract_content(
    path: str, file_name: str, mime: str | None
) -> tuple[str | None, Any | None]:
    ext = os.path.splitext(file_name)[1].lower()
    if ext in TEXT_EXTENSIONS or (mime and mime.startswith("text/")):
        return read_text_file(path), None
    if ext == ".pdf" or mime == "application/pdf":
        t = extract_pdf(path)
        return (t, None) if t.strip() else (None, await llm.async_upload_file(path))
    if ext == ".docx":
        t = extract_docx(path)
        return (t, None) if t.strip() else (None, await llm.async_upload_file(path))
    if os.path.getsize(path) <= 512_000:
        try:
            return read_text_file(path), None
        except ValueError:
            pass
    return None, await llm.async_upload_file(path)


async def process_document(
    message: types.Message,
    bot: Any,
    process_llm: Callable,
    store: MemoryStore,
) -> None:
    doc = message.document
    if not doc:
        return
    file_name = doc.file_name or "file"
    await message.answer(f"Читаю {file_name}...")
    ext = os.path.splitext(file_name)[1].lower() or ".bin"
    file_path = os.path.join(BASE_DIR, f"{uuid.uuid4().hex}{ext}")
    try:
        tg_file = await bot.get_file(doc.file_id)
        await bot.download_file(tg_file.file_path, file_path)
        text, gemini_file = await extract_content(file_path, file_name, doc.mime_type)
        ctx = store.build_personality_snapshot_text()
        user_prompt = message.caption or "Проанализируй с учётом личности пользователя."
        if gemini_file:
            from companion.bot_core import wait_gemini_file_ready

            gemini_file = await wait_gemini_file_ready(gemini_file)
            payload = [f"{ctx}\n\n{user_prompt}", gemini_file]
        elif text and text.strip():
            if len(text) > MAX_DOCUMENT_CHARS:
                text = text[:MAX_DOCUMENT_CHARS] + "\n[обрезано]"
            payload = f"{ctx}\n\n[Файл: {file_name}]\n{text}\n\n[Запрос]\n{user_prompt}"
        else:
            await message.answer("Не смог прочитать файл.")
            return
        await process_llm(message, payload)
    except Exception as e:
        await message.answer(f"Ошибка файла: {e}")
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)
