"""
Run once: compress_templates.py
Creates *_small.pptx versions of both templates with images compressed to ~quality 70.
These become the templates app.py uses, reducing output from ~19MB to ~4-5MB.
"""
import zipfile, io, os
from PIL import Image

TEMPLATES = [
    "3_Mos_Pricing_Diligence.pptx",
    "Datasite Proposal_6_9_options .pptx",
]
MAX_DIM   = 1920   # max pixel dimension
JPEG_Q    = 72     # JPEG quality (72 is plenty for slides)
PNG_Q     = 80     # PNG compression level (0-95)

def compress_image(data, ext):
    try:
        img = Image.open(io.BytesIO(data))
        # Downscale if wider/taller than MAX_DIM
        w, h = img.size
        if max(w, h) > MAX_DIM:
            scale = MAX_DIM / max(w, h)
            img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
        # Convert RGBA/P to RGB for JPEG
        fmt = ext.upper().replace("JPG", "JPEG")
        if fmt == "JPEG" and img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        buf = io.BytesIO()
        if fmt == "JPEG":
            img.save(buf, format="JPEG", quality=JPEG_Q, optimize=True)
        else:
            img.save(buf, format="PNG", optimize=True)
        return buf.getvalue()
    except Exception as e:
        print(f"    skipped ({e})")
        return data   # return original if compression fails

for src in TEMPLATES:
    if not os.path.exists(src):
        print(f"NOT FOUND: {src}")
        continue
    base, _ = os.path.splitext(src)
    dst = base + "_small.pptx"
    saved_bytes = 0
    with zipfile.ZipFile(src, "r") as zin, \
         zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            name = item.filename.lower()
            if name.startswith("ppt/media/") and name.endswith((".jpg", ".jpeg", ".png")):
                ext = "JPEG" if name.endswith((".jpg", ".jpeg")) else "PNG"
                orig = len(data)
                print(f"  compressing {item.filename} ({orig/1024:.0f}KB) ...", end="")
                data = compress_image(data, ext)
                saved = orig - len(data)
                saved_bytes += saved
                print(f" → {len(data)/1024:.0f}KB  (saved {saved/1024:.0f}KB)")
            zout.writestr(item, data)
    orig_mb  = os.path.getsize(src) / 1024 / 1024
    new_mb   = os.path.getsize(dst) / 1024 / 1024
    print(f"\n✓ {src}\n  {orig_mb:.1f}MB → {new_mb:.1f}MB  (saved {saved_bytes/1024/1024:.1f}MB)\n  → {dst}\n")
