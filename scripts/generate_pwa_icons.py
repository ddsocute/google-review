import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


BASE_DIR = Path(__file__).resolve().parent.parent
ICONS_DIR = BASE_DIR / "static" / "icons"


def _create_icon(size: int, bg_color: str = "#1a73e8", text: str = "GM") -> None:
    """Create a simple square PNG icon with text in the center."""
    ICONS_DIR.mkdir(parents=True, exist_ok=True)

    img = Image.new("RGBA", (size, size), bg_color)
    draw = ImageDraw.Draw(img)

    # Try to load a system font; fall back to default if not available.
    try:
        # Windows 常見字型，若不存在會自動 fallback
        font_paths = [
            "C:/Windows/Fonts/segoeui.ttf",
            "C:/Windows/Fonts/msjh.ttc",
        ]
        font = None
        for fp in font_paths:
            if os.path.exists(fp):
                font = ImageFont.truetype(fp, int(size * 0.45))
                break
        if font is None:
            raise FileNotFoundError
    except Exception:
        font = ImageFont.load_default()

    text_color = "#ffffff"

    # Compute text width/height and center it
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    x = (size - text_w) / 2
    y = (size - text_h) / 2

    draw.text((x, y), text, font=font, fill=text_color)

    out_path = ICONS_DIR / f"icon-{size}.png"
    img.save(out_path, format="PNG")
    print(f"Generated {out_path}")


def main() -> None:
    _create_icon(192)
    _create_icon(512)


if __name__ == "__main__":
    main()

