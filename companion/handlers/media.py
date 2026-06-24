"""Media handlers — voice, photo, video, document, tiktok."""
from __future__ import annotations

import asyncio
import os
import uuid

from aiogram import F, types

from companion import bot_core as core
from companion.config import BASE_DIR
from companion.llm import client as llm
import speech_recognition as sr
from pydub import AudioSegment


def register(dp, bot) -> None:
    store = core.memory_store

    @dp.message(F.voice)
    async def voice_handler(message: types.Message):

        if not message.voice:
            return
        await message.answer("Разбираю голос...")
        await core.send_typing(message)
        ogg = os.path.join(BASE_DIR, f"{uuid.uuid4().hex}.ogg")
        wav = os.path.join(BASE_DIR, f"{uuid.uuid4().hex}.wav")
        temp_files = [ogg, wav]
        try:
            file = await bot.get_file(message.voice.file_id)
            await bot.download_file(file.file_path, ogg)
            await asyncio.to_thread(
                lambda: AudioSegment.from_file(ogg).export(wav, format="wav")
            )

            def recognize() -> str:
                r = sr.Recognizer()
                with sr.AudioFile(wav) as src:
                    return r.recognize_google(r.record(src), language="ru-RU")

            text = await asyncio.to_thread(recognize)
            await message.answer(f"Голос: {text}")
            await core.process_llm_request(message, text)
        except sr.UnknownValueError:
            await message.answer("Не понял.")
        except Exception as e:
            await message.answer(f"Ошибка: {e}")
        finally:
            for f in temp_files:
                if os.path.exists(f):
                    os.remove(f)

    @dp.message(F.document)
    async def document_handler(message: types.Message):
        from companion.documents import process_document
        await core.send_typing(message)
        await process_document(message, bot, core.process_llm_request, store)

    @dp.message(F.photo | F.sticker)
    async def media_handler(message: types.Message):
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
            await message.answer(f"Ошибка медиа: {e}")

    @dp.message(F.video | F.video_note)
    async def video_handler(message: types.Message):
        await message.answer("Качаю видео...")
        await core.send_typing(message)
        fp = os.path.join(BASE_DIR, f"{uuid.uuid4().hex}.mp4")
        try:
            fid = message.video.file_id if message.video else message.video_note.file_id
            file = await bot.get_file(fid)
            await bot.download_file(file.file_path, fp)
            vf = await llm.run_llm(llm.upload_file, fp)
            vf = await core.wait_gemini_file_ready(vf)
            await core.process_llm_request(message, ["Опиши видео:", vf])
        except Exception as e:
            await message.answer(f"Ошибка: {e}")
        finally:
            if os.path.exists(fp):
                os.remove(fp)
