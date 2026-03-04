import os
from PIL import Image

BASE_DIR = r"C:\Users\miyos\Documents\seibi_calendar\画像共有フォルダ"
THUMB_DIR = os.path.join(BASE_DIR, "_thumbs")
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "bmp"}


def ensure_thumb_dir():
    if not os.path.exists(THUMB_DIR):
        os.makedirs(THUMB_DIR, exist_ok=True)


def make_thumbnail(src_full_path, rel_path, size=(320, 240), quality=60):
    ensure_thumb_dir()
    thumb_full_path = os.path.join(THUMB_DIR, rel_path)
    thumb_dir = os.path.dirname(thumb_full_path)
    if not os.path.exists(thumb_dir):
        os.makedirs(thumb_dir, exist_ok=True)
    if os.path.exists(thumb_full_path):
        return

    try:
        img = Image.open(src_full_path)
        img.thumbnail(size)
        if img.mode != "RGB":
            img = img.convert("RGB")
        img.save(thumb_full_path, format="JPEG", quality=quality, optimize=True)
        print("thumb:", rel_path)
    except Exception as e:
        print("error:", rel_path, e)


def main():
    for root, dirs, files in os.walk(BASE_DIR):
        if THUMB_DIR in root:
            continue
        for name in files:
            if "." not in name:
                continue
            ext = name.rsplit(".", 1)[1].lower()
            if ext in ALLOWED_EXTENSIONS:
                full = os.path.join(root, name)
                rel = os.path.relpath(full, BASE_DIR).replace("\\", "/")
                make_thumbnail(full, rel)


if __name__ == "__main__":
    main()
