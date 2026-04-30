#!/usr/bin/env python3
"""Apply small MuseTalk ``scripts/inference.py`` fixes (idempotent): image cleanup, non-zero exit, libx264 even dims."""
from __future__ import annotations

import sys
from pathlib import Path

MARKER = 'if get_file_type(video_path) == "video":'
EXIT_MARKER = "sys.exit(1)"
FFMPEG_EVEN_MARKER = "scale=trunc(iw/2)*2"


def main() -> int:
    root = Path(__file__).resolve().parent.parent.parent / "third_party" / "MuseTalk"
    path = root / "scripts" / "inference.py"
    if not path.is_file():
        print(f"skip: {path} not found", file=sys.stderr)
        return 0
    text = path.read_text(encoding="utf-8")
    changed = False
    if MARKER not in text:
        old = """            shutil.rmtree(result_img_save_path)
            os.remove(temp_vid_path)
            
            shutil.rmtree(save_dir_full)
            if not args.saved_coord:"""
        new = """            shutil.rmtree(result_img_save_path)
            os.remove(temp_vid_path)

            if get_file_type(video_path) == "video":
                shutil.rmtree(save_dir_full, ignore_errors=True)
            if not args.saved_coord:"""
        if old not in text:
            print("musetalk inference.py: image cleanup block not found; manual edit may be needed", file=sys.stderr)
            return 1
        text = text.replace(old, new, 1)
        changed = True
        print("musetalk inference.py: patched image cleanup guard")

    if EXIT_MARKER not in text:
        old_e = """        except Exception as e:
            print("Error occurred during processing:", e)
"""
        new_e = """        except Exception as e:
            print("Error occurred during processing:", e)
            sys.exit(1)
"""
        if old_e not in text:
            print("musetalk inference.py: exit-code block not found; manual edit may be needed", file=sys.stderr)
            return 1 if not changed else 0
        text = text.replace(old_e, new_e, 1)
        changed = True
        print("musetalk inference.py: patched non-zero exit on failure")

    if FFMPEG_EVEN_MARKER not in text:
        old_ff = (
            'cmd_img2video = f"ffmpeg -y -v warning -r {fps} -f image2 -i '
            "{result_img_save_path}/%08d.png -vcodec libx264 -vf format=yuv420p -crf 18 {temp_vid_path}\""
        )
        new_ff = (
            'cmd_img2video = f"ffmpeg -y -v warning -r {fps} -f image2 -i '
            "{result_img_save_path}/%08d.png -vf \\\"scale=trunc(iw/2)*2:trunc(ih/2)*2,"
            'format=yuv420p\\\" -c:v libx264 -crf 18 {temp_vid_path}"'
        )
        if old_ff not in text:
            print(
                "musetalk inference.py: ffmpeg img2video line not found; "
                "may already use even dimensions",
                file=sys.stderr,
            )
        else:
            text = text.replace(old_ff, new_ff, 1)
            changed = True
            print("musetalk inference.py: patched libx264 even width/height for frame sequence")

    if not changed:
        print("musetalk inference.py: already patched")
    else:
        path.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
