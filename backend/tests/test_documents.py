import io
import os
from pathlib import Path

import pytest

import documents
import memory
from models import Chunk, Document, DocumentPage, ToolCall, db
from pdfs import encrypt, make_pdf
from prompt import Turn, build_chat_prompt

LEASE = [
    "Lease agreement for a flat in Pune.\nThe tenant is Asha Verma.",
    "Rent is 25000 rupees a month, due on the 5th.\nLate payment costs 500 rupees.",
    "The security deposit is two months of rent.\nIt is returned within 30 days of moving out.",
]


# --- reading and chunking ----------------------------------------------------


def test_chunks_keep_their_pages_overlap_and_cover_everything(monkeypatch):
    monkeypatch.setattr(documents, "CHUNK_CHARS", 200)
    monkeypatch.setattr(documents, "CHUNK_OVERLAP", 60)
    pages = [" ".join(f"Page {p} sentence {i} is here." for i in range(12)) for p in (1, 2, 3)]
    chunks = documents.make_chunks(pages)
    assert len(chunks) > 3
    assert all(len(text) <= 200 for _, _, text in chunks)
    assert chunks[0][0] == 1 and chunks[-1][1] == 3
    assert any(start != end for start, end, _ in chunks)  # some chunks span a page break
    # Each chunk starts with the tail of the one before.
    for (_, _, before), (_, _, after) in zip(chunks, chunks[1:]):
        assert after.split("\n")[0] in before
    joined = "\n".join(text for _, _, text in chunks)
    for p in (1, 2, 3):
        for i in range(12):
            assert f"Page {p} sentence {i} is here." in joined


def test_a_very_long_line_is_split_instead_of_dropped(monkeypatch):
    monkeypatch.setattr(documents, "CHUNK_CHARS", 100)
    word_soup = " ".join(f"word{i}" for i in range(200))
    chunks = documents.make_chunks([word_soup])
    assert all(len(text) <= 100 for _, _, text in chunks)
    assert "word0" in chunks[0][2] and "word199" in chunks[-1][2]


def test_sections_split_on_page_boundaries(monkeypatch):
    monkeypatch.setattr(documents, "SECTION_CHARS", 100)
    sections = documents.make_sections(["a" * 60, "b" * 60, "", "c" * 30])
    assert [(s, e) for s, e, _ in sections] == [(1, 1), (2, 4)]
    assert "[page 4]" in sections[1][2] and "[page 3]" not in sections[1][2]


def test_unreadable_pdfs_say_why():
    with pytest.raises(documents.DocumentError, match="password"):
        documents.extract_pages(encrypt(make_pdf(LEASE)))
    with pytest.raises(documents.DocumentError, match="scan"):
        documents.extract_pages(make_pdf(["", ""]))
    with pytest.raises(documents.DocumentError, match="couldn't be read"):
        documents.extract_pages(b"%PDF-1.4 not really")


# --- upload and processing ---------------------------------------------------


def upload(client, chat_id, data, name="lease.pdf"):
    return client.post(f"/api/conversations/{chat_id}/documents",
                       data={"file": (io.BytesIO(data), name)}, content_type="multipart/form-data")


@pytest.fixture
def chat(client, fake_bot):
    return client.post("/api/conversations", json={}).get_json()


def test_upload_reads_indexes_and_summarizes(app, client, chat, fake_bot):
    res = upload(client, chat["id"], make_pdf(LEASE))
    assert res.status_code == 201, res.get_json()
    doc = res.get_json()
    assert doc["filename"] == "lease.pdf"
    # Processing ran inline in tests, so it's already done.
    assert doc["status"] == "ready" and doc["pages"] == 3 and doc["stage"] is None
    assert doc["one_line"] == "A test document."

    details = client.get(f"/api/documents/{doc['id']}").get_json()
    assert details["summary"] == "It says test things (p. 1)."
    # A short PDF is summarized from its full text in one call.
    (filename, page_count, sections, is_full_text), = fake_bot.document_calls
    assert (filename, page_count, is_full_text) == ("lease.pdf", 3, True)
    assert "25000 rupees" in sections[0][2]
    assert fake_bot.section_calls == []

    listed = client.get(f"/api/conversations/{chat['id']}/documents").get_json()
    assert [d["id"] for d in listed] == [doc["id"]]
    with app.app_context():
        stored = db.session.get(Document, doc["id"])
        assert documents.file_path(stored).read_bytes()[:5] == b"%PDF-"
        assert [p.number for p in stored.page_texts] == [1, 2, 3]
        assert stored.chunks and all(c.embedding for c in stored.chunks)

    pdf = client.get(f"/api/documents/{doc['id']}/file")
    assert pdf.status_code == 200 and pdf.mimetype == "application/pdf"
    assert pdf.data[:5] == b"%PDF-"


def test_long_pdfs_are_summarized_section_by_section(client, chat, fake_bot, monkeypatch):
    monkeypatch.setattr(documents, "SECTION_CHARS", 120)
    doc = upload(client, chat["id"], make_pdf(LEASE)).get_json()
    assert doc["status"] == "ready"
    assert sorted((s, e) for _, s, e, _ in fake_bot.section_calls) == [(1, 1), (2, 2), (3, 3)]
    (_, _, notes, is_full_text), = fake_bot.document_calls
    assert is_full_text is False
    assert [n[2] for n in notes] == ["notes on pages 1-1", "notes on pages 2-2", "notes on pages 3-3"]


def test_a_failed_summary_still_leaves_the_pdf_searchable(app, client, chat, fake_bot):
    fake_bot.document_error = "haiku is down"
    doc = upload(client, chat["id"], make_pdf(LEASE)).get_json()
    assert doc["status"] == "ready"
    assert doc["one_line"] is None and "summary couldn't be written" in doc["error"]
    with app.app_context():
        assert documents.search(chat["id"], "security deposit")


def test_bad_uploads_are_refused(app, client, chat):
    assert upload(client, chat["id"], b"hello", name="notes.txt").status_code == 400
    assert upload(client, chat["id"], b"").status_code == 400
    no_file = client.post(f"/api/conversations/{chat['id']}/documents", data={}, content_type="multipart/form-data")
    assert no_file.status_code == 400
    app.config["MAX_CONTENT_LENGTH"] = 1000
    too_big = upload(client, chat["id"], make_pdf(["x" * 2000]))
    assert too_big.status_code == 413 and "error" in too_big.get_json()
    assert upload(client, "no-such-chat", make_pdf(LEASE)).status_code == 404


def test_a_scanned_pdf_fails_with_a_clear_message_and_can_be_retried(client, chat, fake_bot):
    doc = upload(client, chat["id"], make_pdf(["", ""]), name="scan.pdf").get_json()
    assert doc["status"] == "failed"
    assert "OCR" in doc["error"]
    retried = client.post(f"/api/documents/{doc['id']}/retry")
    assert retried.status_code == 200 and retried.get_json()["status"] == "failed"

    ok = upload(client, chat["id"], make_pdf(LEASE)).get_json()
    assert client.post(f"/api/documents/{ok['id']}/retry").status_code == 409


def test_deleting_a_document_or_its_chat_removes_the_files(app, client, chat, fake_bot):
    first = upload(client, chat["id"], make_pdf(LEASE)).get_json()
    second = upload(client, chat["id"], make_pdf(LEASE), name="copy.pdf").get_json()
    folder = Path(os.environ["UPLOADS_DIR"])
    assert len(list(folder.iterdir())) == 2

    assert client.delete(f"/api/documents/{first['id']}").status_code == 204
    assert len(list(folder.iterdir())) == 1
    assert client.get(f"/api/documents/{first['id']}").status_code == 404

    assert client.delete(f"/api/conversations/{chat['id']}").status_code == 204
    assert list(folder.iterdir()) == []
    with app.app_context():
        assert db.session.get(Document, second["id"]) is None
        assert Chunk.query.count() == 0 and DocumentPage.query.count() == 0


def test_a_document_deleted_mid_processing_is_skipped_quietly(app, client, chat, fake_bot):
    doc = upload(client, chat["id"], make_pdf(LEASE)).get_json()
    with app.app_context():
        db.session.delete(db.session.get(Document, doc["id"]))
        db.session.commit()
    documents.process(app, doc["id"])  # no error


def test_unfinished_documents_resume_after_a_restart(app, client, chat, fake_bot):
    doc = upload(client, chat["id"], make_pdf(LEASE)).get_json()
    with app.app_context():
        stuck = db.session.get(Document, doc["id"])
        stuck.status, stuck.stage, stuck.summary = "processing", "indexing", None
        db.session.commit()
    documents.resume_unfinished(app)
    after = client.get(f"/api/documents/{doc['id']}").get_json()
    assert after["status"] == "ready" and after["summary"]
    with app.app_context():
        # Re-processing replaced the pages and chunks rather than doubling them.
        assert DocumentPage.query.filter_by(document_id=doc["id"]).count() == 3


# --- what Claude sees --------------------------------------------------------


def test_each_turn_lists_the_chats_pdfs(client, chat, fake_bot):
    doc = upload(client, chat["id"], make_pdf(LEASE)).get_json()
    client.post(f"/api/conversations/{chat['id']}/messages", json={"content": "What's the rent?"})
    (listed,) = fake_bot.seen[-1].documents
    assert listed["ref"] == f"d{doc['id']}"
    assert listed["state"] == "ready" and listed["pages"] == 3 and listed["one_line"] == "A test document."

    other = client.post("/api/conversations", json={}).get_json()
    client.post(f"/api/conversations/{other['id']}/messages", json={"content": "hi"})
    assert fake_bot.seen[-1].documents == []  # PDFs belong to their chat


def test_prompt_shows_document_state():
    text = build_chat_prompt(Turn(
        messages=[{"role": "user", "content": "hi"}],
        documents=[
            {"ref": "d1", "filename": "lease.pdf", "pages": 3, "state": "ready", "one_line": "A lease."},
            {"ref": "d2", "filename": "big.pdf", "pages": 300, "state": "summarizing", "one_line": None},
            {"ref": "d3", "filename": "new.pdf", "pages": None, "state": "processing", "one_line": None},
            {"ref": "d4", "filename": "scan.pdf", "pages": 2, "state": "failed", "error": "needs OCR"},
        ],
    ))
    assert "- [d1] lease.pdf · 3 pages: A lease." in text
    assert "- [d2] big.pdf · 300 pages (searchable; its summary is still being written)" in text
    assert "- [d3] new.pdf · pages unknown (still being read; you can't search it yet)" in text
    assert "- [d4] scan.pdf · 2 pages (couldn't be read: needs OCR)" in text
    assert text.index("<documents>") < text.index("<conversation>")


# --- the tools ---------------------------------------------------------------


def tool(client, app, chat_id, name, turn=1, **arguments):
    url = f"/api/mcp?key={app.config['MCP_SECRET']}&conv={chat_id}&turn={turn}&before=1"
    res = client.post(url, json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                 "params": {"name": name, "arguments": arguments}})
    assert res.status_code == 200
    result = res.get_json()["result"]
    return result["content"][0]["text"], result.get("isError", False)


@pytest.fixture
def lease(client, chat, fake_bot):
    return upload(client, chat["id"], make_pdf(LEASE)).get_json()


def test_search_pdfs_returns_passages_with_pages(app, client, chat, lease):
    text, is_error = tool(client, app, chat["id"], "search_pdfs", query="security deposit returned")
    assert not is_error
    assert f"[d{lease['id']} · lease.pdf · p" in text
    assert "security deposit is two months" in text
    with app.app_context():
        assert ToolCall.query.one().tool == "search_pdfs"
        # PDF searches are not "dug up" memories, so they never become facts.
        assert memory.dug_up_this_turn(1) == ([], [])


def test_search_pdfs_can_target_one_document(app, client, chat, lease):
    other = upload(client, chat["id"], make_pdf(["Recipe for dal baati.\nBake the baati until golden."]),
                   name="recipes.pdf").get_json()
    text, _ = tool(client, app, chat["id"], "search_pdfs", query="baati golden", document="recipes")
    assert "recipes.pdf" in text and "lease.pdf" not in text
    text, _ = tool(client, app, chat["id"], "search_pdfs", query="baati golden", document=f"d{lease['id']}")
    assert "recipes.pdf" not in text
    text, is_error = tool(client, app, chat["id"], "search_pdfs", query="x", document="tax return")
    assert is_error and "lease.pdf" in text and f"d{other['id']}" in text


def test_pdf_tools_only_see_this_chats_documents(app, client, chat, lease, fake_bot):
    other = client.post("/api/conversations", json={}).get_json()
    text, _ = tool(client, app, other["id"], "search_pdfs", query="security deposit")
    assert "deposit" not in text
    text, is_error = tool(client, app, other["id"], "get_document_summary", document=f"d{lease['id']}")
    assert is_error and "No PDFs have been uploaded" in text


def test_get_document_summary(app, client, chat, lease):
    text, is_error = tool(client, app, chat["id"], "get_document_summary", document="lease.pdf")
    assert not is_error
    assert text == "Summary of lease.pdf (3 pages):\n\nIt says test things (p. 1)."


def test_read_pdf_pages(app, client, chat, lease, monkeypatch):
    text, _ = tool(client, app, chat["id"], "read_pdf_pages", document=lease["id"], first_page=2, last_page=3)
    assert text.startswith("lease.pdf, pp. 2-3:")
    assert "[page 2]\nRent is 25000" in text and "[page 3]\nThe security deposit" in text
    assert "[page 1]" not in text

    text, _ = tool(client, app, chat["id"], "read_pdf_pages", document="lease", first_page=3, last_page=99)
    assert text.startswith("lease.pdf, p. 3:")
    text, is_error = tool(client, app, chat["id"], "read_pdf_pages", document="lease", first_page=9)
    assert is_error and "pages 1 to 3" in text

    monkeypatch.setattr(documents, "PAGE_READ_LIMIT_CHARS", 80)
    text, _ = tool(client, app, chat["id"], "read_pdf_pages", document="lease", first_page=1, last_page=3)
    assert "Cut off" in text and "[page 3]" not in text


def test_indexing_in_batches_saves_every_chunk(app, client, chat, fake_bot, monkeypatch):
    monkeypatch.setattr(documents, "INDEX_BATCH", 2)
    monkeypatch.setattr(documents, "CHUNK_CHARS", 60)
    monkeypatch.setattr(documents, "CHUNK_OVERLAP", 0)
    doc = upload(client, chat["id"], make_pdf(LEASE)).get_json()
    with app.app_context():
        expected = len(documents.make_chunks([p.content for p in db.session.get(Document, doc["id"]).page_texts]))
        assert expected > 4
        assert Chunk.query.filter_by(document_id=doc["id"]).count() == expected


def test_prompt_says_when_a_pdf_is_only_partly_indexed():
    text = build_chat_prompt(Turn(
        messages=[{"role": "user", "content": "hi"}],
        documents=[{"ref": "d5", "filename": "book.pdf", "pages": 300, "state": "indexing", "one_line": None}],
    ))
    assert "- [d5] book.pdf · 300 pages (still being indexed: only its first pages are searchable so far)" in text
