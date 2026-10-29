import re


def chunk_text(text: str, size: int = 800, overlap: int = 120) -> list[str]:
    """Paragraph-aware chunking: pack paragraphs up to `size` chars, carrying `overlap` tail."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    current = ""
    for para in paragraphs:
        # Hard-split paragraphs that alone exceed the budget.
        pieces = [para[i : i + size] for i in range(0, len(para), size)] if len(para) > size else [para]
        for piece in pieces:
            if current and len(current) + len(piece) + 2 > size:
                chunks.append(current)
                current = current[-overlap:] + "\n\n" + piece if overlap else piece
            else:
                current = f"{current}\n\n{piece}" if current else piece
    if current:
        chunks.append(current)
    return chunks
