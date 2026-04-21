from __future__ import annotations

import argparse
import json
import re
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

import numpy as np
import soundfile as sf

try:
    from kokoro import KPipeline
except Exception as exc:
    raise RuntimeError("Kokoro not installed. Install with: pip install 'kokoro>=0.9.4' soundfile") from exc


def fetch_fish_wav(server_url: str, text: str, chunk_length: int = 200) -> bytes:
    payload = {
        "text": text,
        "chunk_length": chunk_length,
        "format": "wav",
        "latency": "normal",
        "normalize": True,
        "references": [],
        "streaming": False,
        "max_new_tokens": 1024,
        "top_p": 0.8,
        "repetition_penalty": 1.1,
        "temperature": 0.8,
    }

    request = urllib.request.Request(
        server_url.rstrip("/") + "/v1/tts",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=600) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="ignore") if error.fp else ""
        raise RuntimeError(f"Fish request failed ({error.code}): {body}") from error


def infer_lang_code(voice: str, fallback: str) -> str:
    if voice and "_" in voice and len(voice.split("_", maxsplit=1)[0]) >= 1:
        return voice.split("_", maxsplit=1)[0][0]
    return fallback


def synth_kokoro(text: str, voice: str, lang_code: str, speed: float) -> np.ndarray:
    pipeline = KPipeline(lang_code=lang_code)
    arrays: list[np.ndarray] = []

    for _, _, audio in pipeline(text, voice=voice, speed=speed, split_pattern=r"\n+"):
        arr = np.asarray(audio, dtype=np.float32)
        if arr.size > 0:
            arrays.append(arr)

    if not arrays:
        return np.array([], dtype=np.float32)

    return np.concatenate(arrays, axis=0)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate same text with Fish and Kokoro for comparison")
    parser.add_argument("--text", required=True, help="Text to synthesize")
    parser.add_argument("--fish-url", default="http://127.0.0.1:8888", help="Fish API base URL")
    parser.add_argument("--kokoro-voice", default="af_heart", help="Kokoro voice")
    parser.add_argument("--kokoro-lang", default="a", help="Fallback Kokoro language code")
    parser.add_argument("--kokoro-speed", type=float, default=1.0, help="Kokoro speed")
    parser.add_argument("--output-dir", default="outputs/compare", help="Output directory")
    args = parser.parse_args()

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.output_dir) / stamp
    out_dir.mkdir(parents=True, exist_ok=True)

    fish_path = out_dir / "fish.wav"
    kokoro_path = out_dir / "kokoro.wav"
    text_path = out_dir / "text.txt"

    fish_wav = fetch_fish_wav(server_url=args.fish_url, text=args.text)
    fish_path.write_bytes(fish_wav)

    lang = infer_lang_code(args.kokoro_voice, args.kokoro_lang)
    kokoro_audio = synth_kokoro(args.text, args.kokoro_voice, lang, args.kokoro_speed)
    if kokoro_audio.size == 0:
        raise RuntimeError("Kokoro returned empty audio")

    sf.write(str(kokoro_path), kokoro_audio, 24000)
    text_path.write_text(args.text, encoding="utf-8")

    print(f"Output directory: {out_dir}")
    print(f"Fish:   {fish_path}")
    print(f"Kokoro: {kokoro_path}")
    print(f"Text:   {text_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
