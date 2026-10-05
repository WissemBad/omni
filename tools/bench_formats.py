"""Export a mixed sample (Vorbis + ADPCM) as ogg and mp3 and print sizes/tags/durations. Usage: bench_formats.py <dir>"""
import shutil
import subprocess
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    from omni.core.config import CONFIG
    from omni.sources.registry import get_source
    from omni.targets.audio.export import export_sounds
    out = Path(sys.argv[1])
    refs = get_source("007fl").list_sounds(progress=lambda m: None)
    shutil.rmtree(out, ignore_errors=True)
    for fmt in ("ogg", "mp3"):
        sm = export_sounds(refs, out / fmt, fmt, 16, match="voices/english(us)/ai_dialog/mercmrm04", limit=300, progress=lambda m: None)
        print(fmt, {k: sm[k] for k in ("written", "errors", "bytes", "by_type", "seconds")})
        for f in sorted((out / fmt).rglob("*.*"))[:300:100]:
            if f.suffix in (".ogg", ".mp3", ".flac"):
                r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_name:format_tags=title,artist",
                                    "-of", "compact", str(f)], capture_output=True, text=True)
                print("  ", f.name, r.stdout.strip()[:140])


if __name__ == "__main__":
    main()
