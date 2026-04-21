from __future__ import annotations

import argparse
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

import gradio as gr
import pyrootutils

pyrootutils.setup_root(__file__, indicator=".project-root", pythonpath=True)

from tools.ebook_to_audiobook import (
    build_reference_payload,
    extract_book_chapters,
    fetch_wav_from_server,
    slugify,
    split_for_tts,
    write_wav,
)


def _convert_uploaded_book(
    uploaded_file,
    server_url: str,
    ocr_mode: str,
    ocr_language: str,
    reference_audio,
    reference_text: str,
    style_tags: str,
    use_speaker_token: bool,
    temperature: float,
    top_p: float,
    repetition_penalty: float,
    max_chars: int,
    chunk_length: int,
):
    if uploaded_file is None:
        raise gr.Error("Upload a file first.")

    input_path = Path(uploaded_file)
    session_root = Path("outputs") / "book_webui" / datetime.now().strftime("%Y%m%d_%H%M%S")
    session_root.mkdir(parents=True, exist_ok=True)

    local_input = session_root / input_path.name
    shutil.copy2(input_path, local_input)

    chapters = extract_book_chapters(
        local_input,
        ocr_mode=ocr_mode,
        ocr_language=ocr_language,
        ocr_timeout=1800,
    )

    references = build_reference_payload(reference_audio, reference_text)
    tags = (style_tags or "").strip()

    temp_dir = Path(tempfile.mkdtemp(prefix="fish-book-ui-"))
    chapter_outputs: list[Path] = []

    try:
        for chapter_index, chapter in enumerate(chapters, start=1):
            chapter_base = f"{chapter_index:03d}_{slugify(chapter.title)}"
            chapter_chunks = split_for_tts(chapter.text, max_chars)
            if not chapter_chunks:
                continue

            chunk_paths: list[Path] = []
            for chunk_index, chunk_text in enumerate(chapter_chunks, start=1):
                tts_text = chunk_text
                if tags:
                    tts_text = f"{tags} {tts_text}"
                if use_speaker_token and references and "<|speaker:" not in tts_text:
                    tts_text = f"<|speaker:0|> {tts_text}"

                audio_bytes = fetch_wav_from_server(
                    server_url=server_url,
                    text=tts_text,
                    timeout=600,
                    chunk_length=chunk_length,
                    references=references,
                    temperature=temperature,
                    top_p=top_p,
                    repetition_penalty=repetition_penalty,
                )
                chunk_path = temp_dir / f"{chapter_base}-{chunk_index:03d}.wav"
                chunk_path.write_bytes(audio_bytes)
                chunk_paths.append(chunk_path)

            chapter_path = session_root / f"{chapter_base}.wav"
            write_wav(chapter_path, chunk_paths)
            chapter_outputs.append(chapter_path)

        if not chapter_outputs:
            raise gr.Error("No audio was generated from this file.")

        combined_path = session_root / f"{local_input.stem}.wav"
        write_wav(combined_path, chapter_outputs)

        zip_base = session_root / f"{local_input.stem}_chapters"
        zip_path = Path(shutil.make_archive(str(zip_base), "zip", root_dir=session_root))

        summary = (
            f"Done. Generated {len(chapter_outputs)} chapter files.\n"
            f"Combined audiobook: {combined_path.name}\n"
            f"Chapter ZIP: {zip_path.name}\n"
            f"Voice clone: {'on' if references else 'off'} | Tags: {tags if tags else 'none'}"
        )

        return str(combined_path), str(zip_path), summary
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def build_app(default_api_url: str) -> gr.Blocks:
    with gr.Blocks(title="Fish Book Audiobook") as app:
        gr.Markdown("# Fish Book Audiobook\nUpload a book file and click Convert.")

        with gr.Row():
            uploaded = gr.File(
                label="Book File",
                file_types=[".epub", ".pdf", ".txt", ".md", ".html", ".htm"],
                type="filepath",
            )

        with gr.Row():
            server_url = gr.Textbox(label="Fish API URL", value=default_api_url)
            ocr_mode = gr.Dropdown(label="PDF OCR", choices=["auto", "always", "none"], value="auto")
            ocr_language = gr.Textbox(label="OCR Language", value="eng")

        with gr.Row():
            reference_audio = gr.File(
                label="Clone Voice Sample (optional)",
                file_types=[".wav", ".mp3", ".m4a", ".flac", ".ogg", ".opus"],
                type="filepath",
            )
            reference_text = gr.Textbox(
                label="Reference Transcript (recommended)",
                placeholder="Type the exact words spoken in the sample audio",
            )

        with gr.Row():
            style_tags = gr.Textbox(
                label="Style Tags (optional)",
                placeholder="Example: [calm][warm][narration tone]",
            )
            use_speaker_token = gr.Checkbox(
                label="Force speaker token for clone",
                value=True,
            )

        with gr.Row():
            max_chars = gr.Slider(label="Max chars per request", minimum=800, maximum=3500, step=100, value=2200)
            chunk_length = gr.Slider(label="chunk_length", minimum=100, maximum=1000, step=10, value=200)

        with gr.Row():
            temperature = gr.Slider(label="temperature", minimum=0.1, maximum=1.0, step=0.05, value=0.8)
            top_p = gr.Slider(label="top_p", minimum=0.1, maximum=1.0, step=0.05, value=0.8)
            repetition_penalty = gr.Slider(label="repetition_penalty", minimum=0.9, maximum=2.0, step=0.05, value=1.1)

        convert_btn = gr.Button("Convert", variant="primary")

        with gr.Row():
            output_audio = gr.Audio(label="Combined Audiobook", type="filepath")
            output_zip = gr.File(label="Download Chapter Files (.zip)")

        status = gr.Textbox(label="Status", lines=4)

        convert_btn.click(
            fn=_convert_uploaded_book,
            inputs=[
                uploaded,
                server_url,
                ocr_mode,
                ocr_language,
                reference_audio,
                reference_text,
                style_tags,
                use_speaker_token,
                temperature,
                top_p,
                repetition_penalty,
                max_chars,
                chunk_length,
            ],
            outputs=[output_audio, output_zip, status],
        )

    return app


def main() -> int:
    parser = argparse.ArgumentParser(description="Upload-based ebook to audiobook web app")
    parser.add_argument("--listen", default="0.0.0.0:7861", help="host:port for the web app")
    parser.add_argument("--api-url", default="http://127.0.0.1:8888", help="Fish API server URL")
    args = parser.parse_args()

    host, port = args.listen.split(":")
    app = build_app(default_api_url=args.api_url)
    app.launch(server_name=host, server_port=int(port), inbrowser=False, show_error=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
