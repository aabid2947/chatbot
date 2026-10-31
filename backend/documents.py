"""Uploaded PDFs: saving, reading, chunking, search and summaries.

An upload goes through these steps in the background:

1. reading     extract the text of every page (pypdf) and store it per page
2. indexing    cut the text into ~1,500-character chunks that overlap a
               little and remember their pages; give each a search vector.
               Chunks are saved in batches, so search works on the first
               pages while later ones are still being indexed.
3. summarizing summarize sections in parallel, then combine them into one
               summary plus a one-line description (map-reduce, haiku).
               Runs at the same time as step 2.

If the summary fails, the document stays usable and just has no summary.

PDFs without a text layer (scans) fail with a clear message: they need OCR,
which isn't supported.
"""

import io
import logging
import os
import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfReadError
from sqlalchemy import delete, select

import bot
import embeddings
from models import Chunk, Document, DocumentPage, db, utcnow

log = logging.getLogger("chatbot.documents")
logging.getLogger("pypdf").setLevel(logging.ERROR)  # it warns about every odd font

# The embedding model reads at most 512 tokens (~2,000 characters), so chunks
# stay under that and are read in full.
CHUNK_CHARS = 1500
CHUNK_OVERLAP = 200
# Pages are grouped into sections of about this size for the first summary pass.
SECTION_CHARS = 20000
# Section summaries are combined in batches of about this size.
COMBINE_CHARS = 30000
PAGE_READ_LIMIT_CHARS = 30000
# Chunks are saved (and become searchable) this many at a time.
INDEX_BATCH = 24
MIN_TEXT_CHARS = 50

_one_at_a_time = threading.Semaphore(1)


class DocumentError(Exception):
    """A PDF we can't use; the message is shown to the user."""


# --- files -------------------------------------------------------------------


def storage_dir():
    default = Path(__file__).resolve().parent.parent / "storage" / "uploads"
    path = Path(os.getenv("UPLOADS_DIR") or default)
    path.mkdir(parents=True, exist_ok=True)
    return path


def file_path(document):
    return storage_dir() / document.stored_name


def looks_like_pdf(data):
    return data[:1024].lstrip().startswith(b"%PDF-")


def save_upload(conversation, filename, data):
    """Write the file to disk and add a `processing` row. The caller commits."""
    name = Path(filename or "document.pdf").name.strip() or "document.pdf"
    stored_name = f"{uuid.uuid4().hex}.pdf"
    (storage_dir() / stored_name).write_bytes(data)
    document = Document(
        conversation=conversation, filename=name[:255], stored_name=stored_name,
        size_bytes=len(data), status="processing", stage="reading",
    )
    db.session.add(document)
    return document


def remove_files(stored_names):
    for name in stored_names:
        try:
            (storage_dir() / name).unlink(missing_ok=True)
        except OSError as err:
            log.warning("Couldn't delete %s: %s", name, err)


# --- reading and chunking ----------------------------------------------------


def _clean_text(text):
    text = (text or "").replace("\x00", "")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_pages(data):
    """The text of each page, in order."""
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            try:
                ok = reader.decrypt("")
            except Exception:
                ok = 0
            if not ok:
                raise DocumentError("This PDF is password protected.")
        pages = []
        for page in reader.pages:
            try:
                pages.append(_clean_text(page.extract_text()))
            except Exception as err:  # one broken page shouldn't sink the rest
                log.warning("Couldn't read a page: %s", err)
                pages.append("")
    except DocumentError:
        raise
    except (PdfReadError, ValueError, KeyError, TypeError, OSError) as err:
        raise DocumentError(f"This file couldn't be read as a PDF ({err}).") from err
    if not pages:
        raise DocumentError("This PDF has no pages.")
    if sum(len(p) for p in pages) < MIN_TEXT_CHARS:
        raise DocumentError(
            "This PDF has no readable text. It's probably a scan, and scanned PDFs need OCR, "
            "which isn't supported yet."
        )
    return pages


def _pieces(page_number, text):
    """Split a page into sentence-sized pieces no longer than CHUNK_CHARS."""
    out = []
    for paragraph in re.split(r"\n\s*\n", text):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        sentences = re.split(r"(?<=[.!?])\s+", paragraph) if len(paragraph) > CHUNK_CHARS else [paragraph]
        for sentence in sentences:
            while len(sentence) > CHUNK_CHARS:
                cut = sentence.rfind(" ", 0, CHUNK_CHARS)
                cut = cut if cut > CHUNK_CHARS // 2 else CHUNK_CHARS
                out.append((page_number, sentence[:cut].strip()))
                sentence = sentence[cut:].strip()
            if sentence:
                out.append((page_number, sentence))
    return out


def make_chunks(pages):
    """[(page_from, page_to, text)]: passages of about CHUNK_CHARS, each
    starting with the last ~CHUNK_OVERLAP characters of the one before, so an
    idea cut at a boundary is still whole in one of them."""
    pieces = [p for number, text in enumerate(pages, start=1) for p in _pieces(number, text)]
    chunks = []
    current = []
    size = 0
    for piece in pieces:
        if current and size + len(piece[1]) + 1 > CHUNK_CHARS:
            chunks.append(current)
            carry = []
            carried = 0
            for prev in reversed(current):
                if carried + len(prev[1]) > CHUNK_OVERLAP:
                    break
                carry.insert(0, prev)
                carried += len(prev[1]) + 1
            # The overlap gives way when the next piece is long, so no chunk
            # grows past CHUNK_CHARS.
            while carry and carried + len(piece[1]) + 1 > CHUNK_CHARS:
                carried -= len(carry.pop(0)[1]) + 1
            current, size = carry, carried
        current.append(piece)
        size += len(piece[1]) + 1
    if current:
        chunks.append(current)
    return [(c[0][0], c[-1][0], "\n".join(text for _, text in c)) for c in chunks]


# --- summaries ---------------------------------------------------------------


def make_sections(pages):
    """[(page_from, page_to, text)] of about SECTION_CHARS, split on page boundaries."""
    sections = []
    start, parts, size = 1, [], 0
    for number, text in enumerate(pages, start=1):
        if parts and size + len(text) > SECTION_CHARS:
            sections.append((start, number - 1, "\n\n".join(parts)))
            start, parts, size = number, [], 0
        if text:
            parts.append(f"[page {number}]\n{text[:SECTION_CHARS * 2]}")
            size += len(text)
    if parts:
        sections.append((start, len(pages), "\n\n".join(parts)))
    return sections


def _workers():
    return max(1, int(os.getenv("PDF_SUMMARY_WORKERS", "3")))


def summarize_document(filename, pages):
    """(one_line, summary) for the whole document."""
    sections = make_sections(pages)
    if len(sections) == 1:
        start, end, text = sections[0]
        return bot.summarize_document(filename, len(pages), [(start, end, text)], is_full_text=True)

    with ThreadPoolExecutor(max_workers=_workers()) as pool:
        notes = list(pool.map(lambda s: (s[0], s[1], bot.summarize_section(filename, *s)), sections))
    # Very long documents: combine section notes in batches until they fit in one call.
    while sum(len(n[2]) for n in notes) > COMBINE_CHARS and len(notes) > 1:
        batches, batch, size = [], [], 0
        for note in notes:
            if batch and size + len(note[2]) > COMBINE_CHARS:
                batches.append(batch)
                batch, size = [], 0
            batch.append(note)
            size += len(note[2])
        batches.append(batch)
        if len(batches) == len(notes):  # can't shrink by batching; just go on
            break
        with ThreadPoolExecutor(max_workers=_workers()) as pool:
            notes = list(pool.map(
                lambda b: (b[0][0], b[-1][1], bot.summarize_section(
                    filename, b[0][0], b[-1][1], "\n\n".join(f"[pages {s}-{e}]\n{t}" for s, e, t in b))),
                batches,
            ))
    return bot.summarize_document(filename, len(pages), notes, is_full_text=False)


# --- the background job ------------------------------------------------------


def start_processing(app, document_id):
    if app.config.get("BACKGROUND_INLINE"):
        process(app, document_id)
    else:
        threading.Thread(target=process, args=(app, document_id), name=f"pdf-{document_id}", daemon=True).start()


def resume_unfinished(app):
    """Start again on documents a restart interrupted."""
    with app.app_context():
        ids = db.session.scalars(select(Document.id).where(Document.status == "processing")).all()
        db.session.remove()
    for document_id in ids:
        log.info("Resuming document %d after a restart", document_id)
        start_processing(app, document_id)


def _set(document_id, **values):
    document = db.session.get(Document, document_id)
    if document is None:
        raise _Deleted()
    for key, value in values.items():
        setattr(document, key, value)
    db.session.commit()
    return document


class _Deleted(Exception):
    pass


def process(app, document_id):
    with _one_at_a_time, app.app_context():
        try:
            _process(document_id)
        except _Deleted:
            log.info("Document %d was deleted while processing", document_id)
        except DocumentError as err:
            log.warning("Document %d failed: %s", document_id, err)
            _fail(document_id, str(err))
        except Exception:
            log.exception("Document %d failed", document_id)
            _fail(document_id, "Something went wrong while reading this PDF.")
        finally:
            db.session.remove()


def _fail(document_id, message):
    db.session.rollback()
    try:
        _set(document_id, status="failed", stage=None, error=message)
    except _Deleted:
        pass


def _process(document_id):
    document = _set(document_id, status="processing", stage="reading", error=None)
    filename = document.filename
    try:
        data = file_path(document).read_bytes()
    except OSError as err:
        raise DocumentError("The uploaded file is missing from storage.") from err

    # 1. reading
    pages = extract_pages(data)
    db.session.execute(delete(Chunk).where(Chunk.document_id == document_id))
    db.session.execute(delete(DocumentPage).where(DocumentPage.document_id == document_id))
    db.session.add_all(DocumentPage(document_id=document_id, number=i, content=text)
                       for i, text in enumerate(pages, start=1))
    _set(document_id, pages=len(pages), stage="indexing")
    log.info("Document %d (%s): read %d pages, %d characters", document_id, filename, len(pages),
             sum(len(p) for p in pages))

    # 3. summarizing starts now, alongside indexing: it waits on Claude while
    # indexing keeps the CPU busy.
    summarizer = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"pdf-{document_id}-summary")
    summary_job = summarizer.submit(summarize_document, filename, pages)
    try:
        # 2. indexing, saved in batches so the first pages are searchable early
        chunks = make_chunks(pages)
        for start in range(0, len(chunks), INDEX_BATCH):
            batch = chunks[start:start + INDEX_BATCH]
            vectors = embeddings.embed_documents([text for _, _, text in batch])
            db.session.add_all(
                Chunk(document_id=document_id, page_from=first, page_to=last, content=text,
                      embedding=embeddings.to_bytes(vector))
                for (first, last, text), vector in zip(batch, vectors)
            )
            _set(document_id)  # commits the batch; stops if the document was deleted
        _set(document_id, stage="summarizing")
        log.info("Document %d: %d searchable chunks", document_id, len(chunks))

        try:
            one_line, summary = summary_job.result()
            error = None
        except Exception as err:
            log.warning("Document %d summary failed: %s", document_id, err)
            one_line, summary, error = None, None, "The summary couldn't be written; search still works."
    finally:
        summarizer.shutdown(wait=False, cancel_futures=True)
    _set(document_id, status="ready", stage=None, one_line=one_line, summary=summary, error=error,
         ready_at=utcnow())
    log.info("Document %d ready", document_id)


# --- finding things for the tools --------------------------------------------


def find(conversation_id, reference):
    """A document of this chat by "d3", "3", or (part of) its file name."""
    reference = str(reference or "").strip()
    documents = db.session.scalars(
        select(Document).where(Document.conversation_id == conversation_id).order_by(Document.id)
    ).all()
    match = re.fullmatch(r"\[?d?(\d+)\]?", reference, flags=re.I)
    if match:
        wanted = int(match.group(1))
        return next((d for d in documents if d.id == wanted), None)
    lowered = reference.lower()
    exact = [d for d in documents if d.filename.lower() in (lowered, lowered + ".pdf")]
    if exact:
        return exact[-1]
    partial = [d for d in documents if lowered and lowered in d.filename.lower()]
    return partial[-1] if partial else None


def search(conversation_id, query, document_id=None, limit=6):
    """The passages of this chat's PDFs that best match: [(chunk, document, similarity)]."""
    stmt = (
        select(Chunk, Document)
        .join(Document, Chunk.document_id == Document.id)
        .where(Document.conversation_id == conversation_id)
    )
    if document_id is not None:
        stmt = stmt.where(Document.id == document_id)
    rows = db.session.execute(stmt).all()
    if not rows:
        return []
    sims = embeddings.rank(embeddings.embed_query(query), [chunk.embedding for chunk, _ in rows])
    words = [w for w in re.findall(r"\w+", query.lower()) if len(w) > 3]
    if words:
        for i, (chunk, _) in enumerate(rows):
            text = chunk.content.lower()
            hits = sum(1 for w in words if w in text)
            sims[i] += 0.1 * hits / len(words)  # exact words help, e.g. names and codes
    order = sims.argsort()[::-1][:limit]
    return [(rows[i][0], rows[i][1], float(sims[i])) for i in order if sims[i] >= 0.25]


def read_pages(document, first, last):
    """([(number, text)], cut) for pages first..last. Stops at about
    PAGE_READ_LIMIT_CHARS; `cut` says whether anything was left out."""
    rows = db.session.scalars(
        select(DocumentPage)
        .where(DocumentPage.document_id == document.id, DocumentPage.number.between(first, last))
        .order_by(DocumentPage.number)
    ).all()
    out, used, cut = [], 0, False
    for row in rows:
        room = PAGE_READ_LIMIT_CHARS - used
        if room <= 0:
            cut = True
            break
        text = row.content[:room]
        cut = cut or len(text) < len(row.content)
        out.append((row.number, text))
        used += len(text)
    return out, cut
