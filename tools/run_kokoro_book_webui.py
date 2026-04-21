from __future__ import annotations

import argparse
import re
import posixpath
import shutil
import tempfile
import sys
import io
from datetime import datetime
from pathlib import Path

import gradio as gr
import numpy as np
import soundfile as sf

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

    raise RuntimeError("Could not locate repository root")

setup_project_root()

from tools.ebook_to_audiobook import extract_book_chapters, slugify, split_for_tts

try:
    from kokoro import KPipeline
except Exception as exc:
    raise RuntimeError(
        "Kokoro is not installed in this environment. Install with: pip install 'kokoro>=0.9.4' soundfile"
    ) from exc

PIPELINES: dict[str, KPipeline] = {}

def infer_lang_code(voice: str, fallback: str) -> str:
    if voice and "_" in voice and len(voice.split("_", maxsplit=1)[0]) >= 1:
        return voice.split("_", maxsplit=1)[0][0]
    return fallback

def get_pipeline(lang_code: str) -> KPipeline:
    pipeline = PIPELINES.get(lang_code)
    if pipeline is None:
        pipeline = KPipeline(lang_code=lang_code)
        PIPELINES[lang_code] = pipeline
    return pipeline

def normalize_spaces(text: str) -> str:
    text = re.sub(r"\s+", " ", text)
    return text.strip()

def synthesize_kokoro(text: str, voice: str, speed: float, lang_code: str) -> np.ndarray:
    pipeline = get_pipeline(lang_code)
    pieces: list[np.ndarray] = []

    for _, _, audio in pipeline(text, voice=voice, speed=speed, split_pattern=r"\n+"):
        arr = np.asarray(audio, dtype=np.float32)
        if arr.size > 0:
            pieces.append(arr)

    if not pieces:
        return np.array([], dtype=np.float32)

    return np.concatenate(pieces, axis=0)

def convert_uploaded_book(
    uploaded_file,
    voice: str,
    lang_code: str,
    speed: float,
    style_tags: str,
    max_chars: int,
    resume: bool = True,
):
    if uploaded_file is None:
        raise gr.Error("Upload a file first.")

    source = Path(uploaded_file)
    if resume:
        folder_name = slugify(f"{source.stem}_{voice}")
    else:
        folder_name = datetime.now().strftime("%Y%m%d_%H%M%S")

    session_root = Path("outputs") / "kokoro_book_webui" / folder_name
    session_root.mkdir(parents=True, exist_ok=True)

    local_input = session_root / source.name
    with open(source, "rb") as f_in:
        with open(local_input, "wb") as f_out:
            f_out.write(f_in.read())

    chapters = extract_book_chapters(local_input, ocr_mode="auto", ocr_language="eng", ocr_timeout=1800)
    tags = normalize_spaces(style_tags or "")

    chapter_files: list[Path] = []
    chapter_audio_arrays: list[np.ndarray] = []

    selected_lang = infer_lang_code(voice, lang_code)
    for index, chapter in enumerate(chapters, start=1):
        chapter_name = f"{index:03d}_{slugify(chapter.title)}"
        chapter_path = session_root / f"{chapter_name}.wav"
        
        if resume and chapter_path.exists() and chapter_path.stat().st_size > 0:
            print(f"Resuming: Skipping '{chapter_name}' because it already exists.")
            try:
                data, _ = sf.read(str(chapter_path))
                chapter_audio_arrays.append(np.array(data, dtype=np.float32))
                chapter_files.append(chapter_path)
                continue
            except Exception as e:
                print(f"Failed to read existing chapter {chapter_name}, regenerating. Error: {e}")

        chunks = split_for_tts(chapter.text, max_chars)
        if not chunks:
            continue

        chunk_audio: list[np.ndarray] = []
        for chunk in chunks:
            text = normalize_spaces(chunk)
            if tags:
                text = f"{tags} {text}"
            generated = synthesize_kokoro(text=text, voice=voice, speed=speed, lang_code=selected_lang)
            if generated.size > 0:
                chunk_audio.append(generated)

        if not chunk_audio:
            continue

        chapter_audio = np.concatenate(chunk_audio, axis=0)
        
        buf = io.BytesIO()
        sf.write(buf, chapter_audio, 24000, format="WAV")
        with open(chapter_path, "wb") as f_out:
            f_out.write(buf.getvalue())
            
        chapter_audio_arrays.append(chapter_audio)
        chapter_files.append(chapter_path)

    if not chapter_audio_arrays:
        raise gr.Error("No audio was generated.")

    combined = np.concatenate(chapter_audio_arrays, axis=0)
    combined_path = session_root / f"{local_input.stem}_kokoro.wav"
    
    buf_combined = io.BytesIO()
    sf.write(buf_combined, combined, 24000, format="WAV")
    with open(combined_path, "wb") as f_out:
        f_out.write(buf_combined.getvalue())

    try:
        zip_base = session_root / f"{local_input.stem}_kokoro_chapters"
        # Workaround for WSL2 [Errno 5] Input/Output error when zipping to mounted Windows drive
        with tempfile.TemporaryDirectory() as td:
            temp_zip_base = Path(td) / f"{local_input.stem}_kokoro_chapters"
            temp_zip_path = Path(shutil.make_archive(str(temp_zip_base), "zip", root_dir=session_root))
            zip_path = zip_base.with_suffix(".zip")
            shutil.move(temp_zip_path, zip_path)
    except Exception as e:
        print(f"Warning: Could not create ZIP file due to: {e}")
        zip_path = session_root # Fallback

    status = (
        f"Done with Kokoro. Chapters: {len(chapter_files)}\n"
        f"Combined: {combined_path.name}\n"
        f"ZIP: {zip_path.name}\n"
        f"Voice: {voice} | Lang: {selected_lang}"
    )

    return str(combined_path), str(zip_path), status

def build_app() -> gr.Blocks:
    with gr.Blocks(title="Kokoro Book Audiobook") as app:
        gr.Markdown("# Kokoro Book Audiobook\nUpload book -> Convert.")

        book_file = gr.File(
            label="Book File",
            file_types=[".epub", ".pdf", ".txt", ".md", ".html", ".htm"],
            type="filepath",
        )

        with gr.Row():
            voice = gr.Dropdown(
                label="Voice",
                choices=[
                    "af_heart", "af_alloy", "af_bella", "af_jessica", "af_nicole", "af_nova", "af_river", "af_sarah", "af_sky",
                    "am_adam", "am_echo", "am_eric", "am_fenrir", "am_liam", "am_michael", "am_onyx", "am_puck",
                    "bf_alice", "bf_emma", "bf_isabella", "bf_lily",
                    "bm_daniel", "bm_fable", "bm_george", "bm_lewis"
                ],
                value="af_heart",
                allow_custom_value=True,
                info="Type a custom voice name if it is not in the list"
            )
            lang_code = gr.Dropdown(
                label="Fallback Lang Code",
                choices=["a", "b", "e", "f", "h", "i", "j", "p", "z"],
                value="a",
            )
            speed = gr.Slider(label="Speed", minimum=0.7, maximum=1.4, step=0.05, value=1.0)

        style_tags = gr.Dropdown(
            label="Style Prefix (optional)",
            choices=["", "(whisper)", "(shout)", "(happy)", "(sad)", "(angry)"],
            value="",
            allow_custom_value=True,
            info="Optional prefix to add before each chunk to influence emotion (e.g., '(whisper)')",
        )
        max_chars = gr.Slider(label="Max chars per chunk", minimum=800, maximum=3500, step=100, value=2200)
        resume = gr.Checkbox(label="Resume (Skip generated chapters)", value=True, info="If checked, avoids regenerating existing chapters in the folder.")

        run_btn = gr.Button("Convert with Kokoro", variant="primary")
        out_audio = gr.Audio(label="Combined Audiobook", type="filepath")
        out_zip = gr.File(label="Download Chapter ZIP")
        status = gr.Textbox(label="Status", lines=4)

        run_btn.click(
            fn=convert_uploaded_book,
            inputs=[book_file, voice, lang_code, speed, style_tags, max_chars, resume],
            outputs=[out_audio, out_zip, status],
        )

    return app

def main() -> int:
    parser = argparse.ArgumentParser(description="Upload-based Kokoro book app")
    parser.add_argument("--listen", default="0.0.0.0:7863", help="host:port")
    args = parser.parse_args()

    host, port = args.listen.split(":")
    app = build_app()
    app.launch(server_name=host, server_port=int(port), inbrowser=False, show_error=True)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
