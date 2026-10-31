"""Builds small, real PDFs for tests, one string per page."""

import io


def _escape(text):
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_pdf(pages):
    """A PDF whose page i shows pages[i], one line per "\\n". An empty
    string makes a page with no text, like a scan."""
    objects = []  # bodies; object number = index + 1

    def add(body):
        objects.append(body)
        return len(objects)

    catalog = add(None)
    page_tree = add(None)
    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    page_ids = []
    for text in pages:
        lines = []
        y = 750
        for line in text.split("\n"):
            lines.append(f"BT /F1 10 Tf 50 {y} Td ({_escape(line)}) Tj ET")
            y -= 14
        stream = "\n".join(lines).encode("latin-1") if text else b""
        content = add(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        page_ids.append(add(
            f"<< /Type /Page /Parent {page_tree} 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 {font} 0 R >> >> /Contents {content} 0 R >>".encode()
        ))
    objects[catalog - 1] = f"<< /Type /Catalog /Pages {page_tree} 0 R >>".encode()
    kids = " ".join(f"{i} 0 R" for i in page_ids)
    objects[page_tree - 1] = f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode()

    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % number + body + b"\nendobj\n")
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1))
    for offset in offsets:
        out.write(b"%010d 00000 n \n" % offset)
    out.write(b"trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, catalog, xref))
    return out.getvalue()


def encrypt(data, password="secret"):
    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(data)))
    writer.encrypt(password, algorithm="RC4-128")
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()
