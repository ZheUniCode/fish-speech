import os
import soundfile as sf
from kokoro import KPipeline

def main():
    voices = [
        "af_heart", "af_alloy", "af_bella", "af_jessica", "af_nicole", "af_nova", "af_river", "af_sarah", "af_sky",
        "am_adam", "am_echo", "am_eric", "am_fenrir", "am_liam", "am_michael", "am_onyx", "am_puck",
        "bf_alice", "bf_emma", "bf_isabella", "bf_lily",
        "bm_daniel", "bm_fable", "bm_george", "bm_lewis"
    ]
    
    out_dir = "kokoro_voice_demos"
    os.makedirs(out_dir, exist_ok=True)
    
    print(f"Generating Kokoro voice demos in '{out_dir}'...")
    
    # We will instantiate pipelines for 'a' (American) and 'b' (British)
    pipeline_a = KPipeline(lang_code='a')
    pipeline_b = KPipeline(lang_code='b')
    
    for voice in voices:
        pipeline = pipeline_a if voice.startswith("a") else pipeline_b
        print(f"Generating demo for {voice}...")
        
        text = f"Hello. I am the {voice} voice. This is a short demonstration of how I sound."
        
        generator = pipeline(
            text, voice=voice, # <= changed voice here
            speed=1, split_pattern=r'\n+'
        )
        
        try:
            audio_pieces = []
            for i, (gs, ps, audio) in enumerate(generator):
                audio_pieces.append(audio)
            
            if audio_pieces:
                import numpy as np
                final_audio = np.concatenate(audio_pieces)
                out_path = os.path.join(out_dir, f"{voice}.wav")
                sf.write(out_path, final_audio, 24000)
        except Exception as e:
            print(f"Failed to generate {voice}: {e}")
            
    print("All demos generated successfully!")

if __name__ == "__main__":
    main()
