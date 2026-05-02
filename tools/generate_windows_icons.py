from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
RES_DIR = ROOT / "res"
WIN_RES_DIR = ROOT / "windows" / "runner" / "resources"

DARK_SRC = RES_DIR / "new_dark.png"
FOREGROUND_SRC = RES_DIR / "new_foreground.png"

DARK_OUT = WIN_RES_DIR / "app_icon_dark.ico"
LIGHT_OUT = WIN_RES_DIR / "app_icon_light.ico"
DEFAULT_OUT = WIN_RES_DIR / "app_icon.ico"

SIZES = [(16, 16), (20, 20), (24, 24), (32, 32), (40, 40), (48, 48), (64, 64), (128, 128), (256, 256)]


def build_dark() -> Image.Image:
    return Image.open(DARK_SRC).convert("RGBA")


def build_light() -> Image.Image:
    bg = Image.new("RGBA", (1024, 1024), "#FFFFFF")
    fg = Image.open(FOREGROUND_SRC).convert("RGBA")

    target_side = int(bg.width * 0.62)
    fg = fg.resize((target_side, target_side), Image.Resampling.LANCZOS)
    offset = ((bg.width - target_side) // 2, (bg.height - target_side) // 2)
    bg.alpha_composite(fg, offset)
    return bg


def save_ico(base_image: Image.Image, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    base_image.save(out_path, format="ICO", sizes=SIZES)


def main() -> None:
    dark = build_dark()
    light = build_light()

    save_ico(dark, DARK_OUT)
    save_ico(light, LIGHT_OUT)
    save_ico(dark, DEFAULT_OUT)

    print(f"Generated: {DARK_OUT}")
    print(f"Generated: {LIGHT_OUT}")
    print(f"Generated: {DEFAULT_OUT}")


if __name__ == "__main__":
    main()
