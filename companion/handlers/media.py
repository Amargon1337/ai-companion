"""Media handlers — voice, photo, video, document, tiktok."""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tempfile
import uuid

from aiogram import F, types

from companion import bot_core as core
from companion.config import MAX_VIDEO_DOWNLOAD_BYTES, SPEECH_RECOGNITION_LANGUAGE
from companion.llm import client as llm
import speech_recognition as sr
from pydub import AudioSegment

logger = logging.getLogger(__name__)


def _make_temp_dir() -> str:
    return tempfile.mkdtemp(prefix="companion-media-")


def register(dp, bot) -> None:
    store = core.memory_store

    @dp.message(F.voice)
    async def voice_handler(message: types.Message):
        if not core.check_rate_limit(message.from_user.id, message):
            return
        if not message.voice:
            return
        await message.answer("Разбираю голос...")
        await core.send_typing(message)
        temp_dir = _make_temp_dir()
        ogg = os.path.join(temp_dir, f"{uuid.uuid4().hex}.ogg")
        wav = os.path.join(temp_dir, f"{uuid.uuid4().hex}.wav")
        try:
            file = await bot.get_file(message.voice.file_id)
            await bot.download_file(file.file_path, ogg)
            await asyncio.to_thread(
                lambda: AudioSegment.from_file(ogg).export(wav, format="wav")
            )

            def recognize() -> str:
                r = sr.Recognizer()
                with sr.AudioFile(wav) as src:
                    return r.recognize_google(r.record(src), language=SPEECH_RECOGNITION_LANGUAGE)

            text = await asyncio.to_thread(recognize)
            await message.answer(f"Голос: {text}")
            await core.process_llm_request(message, text)
        except sr.UnknownValueError:
            await message.answer("Не понял.")
        except Exception as e:
            logger.error("Voice processing error: %s", e)
            await message.answer("Произошла ошибка при обработке голоса.")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    @dp.message(F.document)
    async def document_handler(message: types.Message):
        if not core.check_rate_limit(message.from_user.id, message):
            return
        from companion.documents import process_document
        await core.send_typing(message)
        await process_document(message, bot, core.process_llm_request, store)

    @dp.message(F.photo | F.sticker)
    async def media_handler(message: types.Message):
        if not core.check_rate_limit(message.from_user.id, message):
            return
        from google.genai import types as google_types
        try:
            if message.photo:
                fid, mime = message.photo[-1].file_id, "image/jpeg"
            elif message.sticker:
                if message.sticker.is_animated or message.sticker.is_video:
                    return await message.answer("Анимацию не читаю.")
                fid, mime = message.sticker.file_id, "image/webp"
            else:
                return
            file = await bot.get_file(fid)
            raw = await bot.download_file(file.file_path)
            img = google_types.Part.from_bytes(data=raw.getvalue(), mime_type=mime)
            await core.process_llm_request(message, [message.caption or "Опиши.", img])
        except Exception as e:
            logger.error("Media processing error: %s", e)
            await message.answer("Произошла ошибка при обработке медиа.")

    @dp.message(F.video | F.video_note)
    async def video_handler(message: types.Message):
        if not core.check_rate_limit(message.from_user.id, message):
            return
        await message.answer("Качаю видео...")
        await core.send_typing(message)
        temp_dir = _make_temp_dir()
        fp = os.path.join(temp_dir, f"{uuid.uuid4().hex}.mp4")
        vf = None
        try:
            media = message.video or message.video_note
            if media is None:
                return
            file_size = getattr(media, "file_size", 0) or 0
            if file_size > MAX_VIDEO_DOWNLOAD_BYTES:
                limit_mb = MAX_VIDEO_DOWNLOAD_BYTES // (1024 * 1024)
                await message.answer(f"Видео слишком большое. Лимит: {limit_mb} MB.")
                return

            fid = media.file_id
            file = await bot.get_file(fid)
            await bot.download_file(file.file_path, fp)
            vf = await llm.run_llm(llm.upload_file, fp)
            vf = await core.wait_gemini_file_ready(vf)
            await core.process_llm_request(message, ["Опиши видео:", vf])
        except Exception as e:
            logger.error("Video processing error: %s", e)
            await message.answer("Произошла ошибка при обработке видео.")
        finally:
            if vf:
                try:
                    await llm.run_llm(llm.delete_file, vf.name)
                except Exception:
                    pass
            shutil.rmtree(temp_dir, ignore_errors=True)
