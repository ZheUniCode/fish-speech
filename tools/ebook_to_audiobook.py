from __future__ import annotations

import argparse
import base64
import html
import json
import os
import posixpath
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import wave
import zipfile
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable
from xml.etree import ElementTree as ET

try:
    import pyrootutils
except Exception:
    pyrootutils = None


def setup_project_root() -> None:
    if pyrootutils is not None:
        pyrootutils.setup_root(__file__, indicator=".project-root", pythonpath=True)
        return

    current = Path(__file__).resolve()
    for parent in [current.parent, *current.parents]:
        if (parent / ".project-root").exists():
            parent_str = str(parent)
            if parent_str not in sys.path:
                sys.path.insert(0, parent_str)
            return

    raise RuntimeError(
        "Could not locate repository root. Install pyrootutils or run this script from inside the fish-speech repository."
    )


setup_project_root()

try:
    from pypdf import PdfReader
except Exception as exc:  # pragma: no cover - dependency check happens at runtime
    PdfReader = None
    PDF_IMPORT_ERROR = exc
else:
    PDF_IMPORT_ERROR = None

from fish_speech.text.clean import clean_text


CHAPTER_HEADING_RE = re.compile(
    r"^\s*(?:chapter|chap\.?|part|book|prologue|epilogue|preface|foreword)\b(?:[\s:.-]+.*)?$",
    re.IGNORECASE,
)


@dataclass(slots=True)
class Chapter:
    title: str
    text: str


class HtmlTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag: str, attrs):
        if tag in {"script", "style", "noscript"}:
            self.skip_depth += 1
        elif tag in {"p", "div", "section", "article", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "hr"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str):
        if tag in {"script", "style", "noscript"} and self.skip_depth > 0:
            self.skip_depth -= 1
        elif tag in {"p", "div", "section", "article", "li", "h1", "h2", "h3", "h4", "h5", "h6"}:
            self.parts.append("\n")

    def handle_data(self, data: str):
        if self.skip_depth == 0 and data.strip():
            self.parts.append(html.unescape(data))

    def get_text(self) -> str:
        text = "".join(self.parts)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


def read_text_file(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def html_to_text(source: str) -> str:
    extractor = HtmlTextExtractor()
    extractor.feed(source)
    extractor.close()
    return extractor.get_text()


def strip_namespace(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def find_first_heading(source: str, fallback: str) -> str:
    match = re.search(r"<h[1-6][^>]*>(.*?)</h[1-6]>", source, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        title = Path(fallback).stem
        return title.replace("_", " ").replace("-", " ").strip() or "Chapter"

    heading = html_to_text(match.group(1)).strip()
    return heading or Path(fallback).stem


def join_path(base: str, href: str) -> str:
    base_dir = posixpath.dirname(base)
    return posixpath.normpath(posixpath.join(base_dir, href))


def extract_epub_chapters(path: Path) -> list[Chapter]:
    with zipfile.ZipFile(path) as archive:
        container = ET.fromstring(archive.read("META-INF/container.xml"))
        rootfile = None
        for element in container.iter():
            if strip_namespace(element.tag) == "rootfile":
                rootfile = element.attrib.get("full-path")
                break

        if not rootfile:
            raise RuntimeError("Could not locate EPUB rootfile metadata")

        root_dir = posixpath.dirname(rootfile)
        package = ET.fromstring(archive.read(rootfile))
        manifest: dict[str, str] = {}
        spine_ids: list[str] = []

        for element in package.iter():
            tag = strip_namespace(element.tag)
            if tag == "item":
                item_id = element.attrib.get("id")
                href = element.attrib.get("href")
                if item_id and href:
                    manifest[item_id] = join_path(rootfile, href)
            elif tag == "itemref":
                idref = element.attrib.get("idref")
                if idref:
                    spine_ids.append(idref)

        chapters: list[Chapter] = []
        for index, idref in enumerate(spine_ids, start=1):
            href = manifest.get(idref)
            if not href:
                continue

            raw = archive.read(href).decode("utf-8", errors="ignore")
            text = html_to_text(raw)
            if not text.strip():
                continue

            title = find_first_heading(raw, href)
            for chapter in split_text_into_chapters(text, default_title=title, source_hint=f"{path.stem}-{index}"):
                chapters.append(chapter)

        return chapters


def extract_pdf_text(path: Path) -> tuple[list[str], int, int]:
    reader = PdfReader(str(path))
    pages: list[str] = []
    empty_pages = 0

    for page in reader.pages:
        text = page.extract_text() or ""
        cleaned = text.strip()
        if not cleaned:
            empty_pages += 1
            continue
        pages.append(cleaned)

    return pages, empty_pages, len(reader.pages)


def pdf_looks_scanned(pages: list[str], empty_pages: int, total_pages: int) -> bool:
    if not pages:
        return True

    return empty_pages >= max(1, total_pages // 2) and sum(len(page) for page in pages) < total_pages * 80


def run_ocrmypdf(input_pdf: Path, output_pdf: Path, ocr_language: str, timeout: int) -> None:
    executable = shutil.which("ocrmypdf")
    if executable is None:
        raise RuntimeError(
            "OCR is required for this scanned PDF, but 'ocrmypdf' was not found in PATH. "
            "Install it first (for WSL Ubuntu: 'sudo apt install ocrmypdf tesseract-ocr') and retry."
        )

    command = [
        executable,
        "--skip-text",
        "--force-ocr",
        "--language",
        ocr_language,
        str(input_pdf),
        str(output_pdf),
    ]

    try:
        subprocess.run(command, check=True, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(f"OCR timed out after {timeout} seconds") from error
    except subprocess.CalledProcessError as error:
        stderr = (error.stderr or "").strip()
        stdout = (error.stdout or "").strip()
        detail = stderr or stdout or str(error)
        raise RuntimeError(f"OCR failed: {detail}") from error


def extract_pdf_chapters(
    path: Path,
    ocr_mode: str = "auto",
    ocr_language: str = "eng",
    ocr_timeout: int = 1800,
) -> list[Chapter]:
    if PdfReader is None:
        raise RuntimeError(
            "PDF support requires the 'pypdf' package. Install dependencies with 'uv sync'."
        ) from PDF_IMPORT_ERROR

    pages, empty_pages, total_pages = extract_pdf_text(path)
    looks_scanned = pdf_looks_scanned(pages, empty_pages, total_pages)

    should_ocr = ocr_mode == "always" or (ocr_mode == "auto" and looks_scanned)
    if should_ocr:
        with tempfile.TemporaryDirectory(prefix="fish-ocr-") as ocr_temp:
            ocr_output = Path(ocr_temp) / f"{path.stem}.ocr.pdf"
            print(f"Running OCR for scanned PDF: {path.name}")
            run_ocrmypdf(path, ocr_output, ocr_language=ocr_language, timeout=ocr_timeout)
            pages, empty_pages, total_pages = extract_pdf_text(ocr_output)

    if not pages:
        if ocr_mode == "none":
            raise RuntimeError(
                "No text could be extracted from this PDF. It appears scanned/image-only. "
                "Retry with '--ocr auto' (or '--ocr always')."
            )
        raise RuntimeError(
            "No text could be extracted from this PDF even after OCR. "
            "Try another OCR language with '--ocr-language' (for example 'eng+chi_sim')."
        )

    if ocr_mode == "none" and pdf_looks_scanned(pages, empty_pages, total_pages):
        raise RuntimeError(
            "This PDF appears mostly scanned/image-based. Retry with '--ocr auto' to extract text from page images."
        )

    joined = "\n\n".join(pages)
    return split_text_into_chapters(joined, default_title=path.stem.replace("_", " "), source_hint=path.stem)


def extract_plaintext_chapters(path: Path) -> list[Chapter]:
    text = read_text_file(path)
    return split_text_into_chapters(text, default_title=path.stem.replace("_", " "), source_hint=path.stem)


def split_text_into_chapters(text: str, default_title: str, source_hint: str) -> list[Chapter]:
    normalized = re.sub(r"\r\n?", "\n", text)
    lines = [line.rstrip() for line in normalized.split("\n")]
    chapters: list[Chapter] = []
    current_title = default_title.strip() or source_hint
    current_lines: list[str] = []
    saw_heading = False

    def flush() -> None:
        nonlocal current_lines, current_title
        body = "\n".join(line for line in current_lines).strip()
        if body:
            chapters.append(Chapter(title=current_title.strip() or source_hint, text=body))
        current_lines = []

    for line in lines:
        stripped = line.strip()
        if stripped and CHAPTER_HEADING_RE.match(stripped):
            if current_lines:
                flush()
            current_title = stripped
            current_lines = [stripped]
            saw_heading = True
            continue

        current_lines.append(line)

    flush()

    if not saw_heading and len(chapters) > 1:
        return chapters

    if not chapters and normalized.strip():
        return [Chapter(title=current_title.strip() or source_hint, text=normalized.strip())]

    return chapters


def split_for_tts(text: str, max_chars: int) -> list[str]:
    cleaned_text = clean_text(text)
    cleaned_text = re.sub(r"\r\n?", "\n", cleaned_text)
    if not cleaned_text.strip():
        return []

    paragraphs = [
        re.sub(r"[ \t]+", " ", paragraph).strip()
        for paragraph in re.split(r"\n\s*\n", cleaned_text)
        if paragraph.strip()
    ]
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    def flush() -> None:
        nonlocal current, current_len
        if current:
            chunks.append(" ".join(current).strip())
            current = []
            current_len = 0

    for paragraph in paragraphs:
        if len(paragraph) > max_chars:
            flush()
            chunks.extend(split_long_paragraph(paragraph, max_chars))
            continue

        extra = len(paragraph) + (1 if current else 0)
        if current_len + extra > max_chars:
            flush()

        current.append(paragraph)
        current_len += extra

    flush()
    return [chunk for chunk in chunks if chunk.strip()]


def split_long_paragraph(paragraph: str, max_chars: int) -> list[str]:
    sentence_parts = re.split(r"(?<=[.!?])\s+", paragraph)
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    def flush() -> None:
        nonlocal current, current_len
        if current:
            chunks.append(" ".join(current).strip())
            current = []
            current_len = 0

    for sentence in sentence_parts:
        sentence = sentence.strip()
        if not sentence:
            continue

        if len(sentence) > max_chars:
            flush()
            for start in range(0, len(sentence), max_chars):
                chunks.append(sentence[start : start + max_chars].strip())
            continue

        extra = len(sentence) + (1 if current else 0)
        if current_len + extra > max_chars:
            flush()

        current.append(sentence)
        current_len += extra

    flush()
    return [chunk for chunk in chunks if chunk.strip()]


def slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return value or "chapter"


def fetch_wav_from_server(
    server_url: str,
    text: str,
    timeout: int,
    chunk_length: int,
    references: list[dict] | None = None,
    max_new_tokens: int = 1024,
    top_p: float = 0.8,
    repetition_penalty: float = 1.1,
    temperature: float = 0.8,
    normalize: bool = True,
) -> bytes:
    payload = {
        "text": text,
        "chunk_length": chunk_length,
        "format": "wav",
        "latency": "normal",
        "normalize": normalize,
        "references": references or [],
        "streaming": False,
        "max_new_tokens": max_new_tokens,
        "top_p": top_p,
        "repetition_penalty": repetition_penalty,
        "temperature": temperature,
    }

    request = urllib.request.Request(
        server_url.rstrip("/") + "/v1/tts",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="ignore") if error.fp else ""
        detail = parse_error_detail(body) or error.reason or f"HTTP {error.code}"
        raise RuntimeError(f"TTS request failed: {detail}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"Could not reach the Fish Speech server at {server_url}: {error.reason}") from error


def parse_error_detail(body: str) -> str:
    body = body.strip()
    if not body:
        return ""

    try:
        data = json.loads(body)
    except Exception:
        return body

    for key in ("error", "content", "message", "detail"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return body


def build_reference_payload(reference_audio_path: str | None, reference_text: str | None) -> list[dict]:
    if not reference_audio_path:
        return []

    audio_bytes = Path(reference_audio_path).read_bytes()
    audio_b64 = base64.b64encode(audio_bytes).decode("ascii")
    return [{"audio": audio_b64, "text": (reference_text or "").strip()}]


def write_wav(output_path: Path, wav_paths: Iterable[Path]) -> None:
    wav_paths = list(wav_paths)
    if not wav_paths:
        raise RuntimeError("No WAV files were produced")

    with wave.open(str(wav_paths[0]), "rb") as first_reader:
        params = first_reader.getparams()

    with wave.open(str(output_path), "wb") as writer:
        writer.setparams(params)
        for wav_path in wav_paths:
            with wave.open(str(wav_path), "rb") as reader:
                if reader.getparams() != params:
                    raise RuntimeError(f"Audio format mismatch in {wav_path}")
                writer.writeframes(reader.readframes(reader.getnframes()))


def extract_book_chapters(
    path: Path,
    ocr_mode: str = "auto",
    ocr_language: str = "eng",
    ocr_timeout: int = 1800,
) -> list[Chapter]:
    suffix = path.suffix.lower()
    if suffix == ".epub":
        chapters = extract_epub_chapters(path)
    elif suffix == ".pdf":
        chapters = extract_pdf_chapters(
            path,
            ocr_mode=ocr_mode,
            ocr_language=ocr_language,
            ocr_timeout=ocr_timeout,
        )
    elif suffix in {".txt", ".md", ".rst", ".log", ".html", ".htm"}:
        text = read_text_file(path)
        if suffix in {".html", ".htm"}:
            text = html_to_text(text)
        chapters = split_text_into_chapters(
            text,
            default_title=path.stem.replace("_", " "),
            source_hint=path.stem,
        )
    else:
        raise RuntimeError(
            f"Unsupported input type: {path.suffix}. Supported types: .epub, .pdf, .txt, .md, .rst, .log, .html, .htm"
        )

    cleaned = [Chapter(title=chapter.title, text=chapter.text.strip()) for chapter in chapters if chapter.text.strip()]
    if not cleaned:
        raise RuntimeError(f"No readable text found in {path}")

    return cleaned


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert an ebook or document into chapterized audiobook WAV files.")
    parser.add_argument("input", type=Path, help="Input .epub, .pdf, .txt, .md, .html, or .htm file")
    parser.add_argument("--server-url", default="http://127.0.0.1:8888", help="Fish Speech API base URL")
    parser.add_argument("--output-dir", type=Path, default=None, help="Directory to write chapter WAVs into")
    parser.add_argument("--combined-name", default=None, help="Name of the final combined WAV without extension")
    parser.add_argument("--max-chars", type=int, default=2200, help="Maximum characters per TTS request chunk")
    parser.add_argument("--chunk-length", type=int, default=200, help="Fish Speech chunk_length request value")
    parser.add_argument("--timeout", type=int, default=600, help="HTTP timeout in seconds for each TTS request")
    parser.add_argument("--ocr", choices=["none", "auto", "always"], default="auto", help="OCR strategy for PDFs")
    parser.add_argument("--ocr-language", default="eng", help="OCR language code(s) for OCRmyPDF/Tesseract, e.g. 'eng' or 'eng+chi_sim'")
    parser.add_argument("--ocr-timeout", type=int, default=1800, help="Timeout in seconds for the OCR subprocess")
    parser.add_argument("--keep-temp", action="store_true", help="Keep temporary chunk WAVs for inspection")
    args = parser.parse_args()

    if not args.input.exists():
        raise SystemExit(f"Input file not found: {args.input}")

    chapters = extract_book_chapters(
        args.input,
        ocr_mode=args.ocr,
        ocr_language=args.ocr_language,
        ocr_timeout=args.ocr_timeout,
    )
    output_dir = args.output_dir or args.input.with_suffix("")
    output_dir.mkdir(parents=True, exist_ok=True)

    combined_name = args.combined_name or args.input.stem
    combined_path = output_dir / f"{combined_name}.wav"

    temp_dir = Path(tempfile.mkdtemp(prefix="fish-book-"))
    chapter_outputs: list[Path] = []

    try:
        for chapter_index, chapter in enumerate(chapters, start=1):
            chapter_base = f"{chapter_index:03d}_{slugify(chapter.title)}"
            chapter_chunks = split_for_tts(chapter.text, args.max_chars)
            if not chapter_chunks:
                continue

            chunk_paths: list[Path] = []
            for chunk_index, chunk_text in enumerate(chapter_chunks, start=1):
                print(f"[{chapter_index}/{len(chapters)}] {chapter.title} chunk {chunk_index}/{len(chapter_chunks)}")
                audio_bytes = fetch_wav_from_server(args.server_url, chunk_text, args.timeout, args.chunk_length)
                chunk_path = temp_dir / f"{chapter_base}-{chunk_index:03d}.wav"
                chunk_path.write_bytes(audio_bytes)
                chunk_paths.append(chunk_path)

            chapter_path = output_dir / f"{chapter_base}.wav"
            write_wav(chapter_path, chunk_paths)
            chapter_outputs.append(chapter_path)

        if not chapter_outputs:
            raise RuntimeError("No audio was generated")

        write_wav(combined_path, chapter_outputs)
        print(f"Wrote chapter files to: {output_dir}")
        print(f"Wrote combined audiobook to: {combined_path}")
    finally:
        if args.keep_temp:
            print(f"Keeping temporary chunk files in: {temp_dir}")
        else:
            shutil.rmtree(temp_dir, ignore_errors=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())