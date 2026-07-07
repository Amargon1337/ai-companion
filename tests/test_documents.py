from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from companion.documents import process_document


@pytest.mark.anyio
async def test_process_document_removes_temp_dir(tmp_path, monkeypatch):
    temp_dir = tmp_path / "companion-doc-test"

    def make_temp_dir(prefix):
        temp_dir.mkdir()
        return str(temp_dir)

    monkeypatch.setattr("companion.documents.tempfile.mkdtemp", make_temp_dir)

    message = SimpleNamespace(
        document=SimpleNamespace(
            file_name="note.txt",
            file_id="file-id",
            mime_type="text/plain",
        ),
        caption=None,
        answer=AsyncMock(),
    )
    bot = SimpleNamespace(get_file=AsyncMock(return_value=SimpleNamespace(file_path="remote/path")))

    async def download_file(_remote_path, local_path):
        assert temp_dir.exists()
        with open(local_path, "w", encoding="utf-8") as file:
            file.write("plain document text")

    bot.download_file = AsyncMock(side_effect=download_file)
    process_llm = AsyncMock()
    store = SimpleNamespace(build_canonical_profile_text=lambda: "profile")

    await process_document(message, bot, process_llm, store)

    assert not temp_dir.exists()
    process_llm.assert_awaited_once()
