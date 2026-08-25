from __future__ import annotations

import argparse
import random
from pathlib import Path

import yaml
from PIL import Image, ImageDraw, ImageFont


IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp"}
COLORS = {
    0: (230, 57, 70),
    1: (42, 157, 143),
    2: (69, 123, 157),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Draw YOLO-format ground-truth labels on sample images.")
    parser.add_argument("--data", type=Path, default=Path("runs_data/sedd_yolo/sedd.yaml"))
    parser.add_argument("--split", choices=["train", "val", "test"], default="val")
    parser.add_argument("--out", type=Path, default=Path("runs_data/sedd_yolo/visualized/val"))
    parser.add_argument("--max-images", type=int, default=50)
    parser.add_argument("--seed", type=int, default=3407)
    parser.add_argument("--include-empty", action="store_true", help="Also export images without labels.")
    return parser.parse_args()


def load_dataset(data_path: Path) -> tuple[Path, dict[int, str]]:
    data = yaml.safe_load(data_path.read_text(encoding="utf-8"))
    root = Path(data["path"])
    if not root.is_absolute():
        root = (data_path.parent / root).resolve()
    names_raw = data["names"]
    if isinstance(names_raw, dict):
        names = {int(k): v for k, v in names_raw.items()}
    else:
        names = {idx: name for idx, name in enumerate(names_raw)}
    return root, names


def read_labels(label_path: Path) -> list[tuple[int, float, float, float, float]]:
    if not label_path.exists():
        return []
    labels = []
    for line in label_path.read_text(encoding="utf-8").splitlines():
        parts = line.strip().split()
        if len(parts) != 5:
            continue
        cls_id, x, y, w, h = parts
        labels.append((int(cls_id), float(x), float(y), float(w), float(h)))
    return labels


def draw_image(image_path: Path, label_path: Path, out_path: Path, names: dict[int, str]) -> None:
    image = Image.open(image_path).convert("RGB")
    draw = ImageDraw.Draw(image)
    width, height = image.size
    font = ImageFont.load_default()

    for cls_id, x, y, box_w, box_h in read_labels(label_path):
        color = COLORS.get(cls_id, (255, 183, 3))
        x1 = int((x - box_w / 2) * width)
        y1 = int((y - box_h / 2) * height)
        x2 = int((x + box_w / 2) * width)
        y2 = int((y + box_h / 2) * height)
        label = names.get(cls_id, str(cls_id))

        draw.rectangle((x1, y1, x2, y2), outline=color, width=max(2, width // 450))
        text_box = draw.textbbox((x1, y1), label, font=font)
        text_w = text_box[2] - text_box[0]
        text_h = text_box[3] - text_box[1]
        y_text = max(0, y1 - text_h - 4)
        draw.rectangle((x1, y_text, x1 + text_w + 6, y_text + text_h + 4), fill=color)
        draw.text((x1 + 3, y_text + 2), label, fill=(255, 255, 255), font=font)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(out_path)


def main() -> None:
    args = parse_args()
    data_path = args.data.resolve()
    root, names = load_dataset(data_path)

    image_dir = root / "images" / args.split
    label_dir = root / "labels" / args.split
    if not image_dir.exists() or not label_dir.exists():
        raise FileNotFoundError(f"Expected {image_dir} and {label_dir}")

    images = sorted(p for p in image_dir.iterdir() if p.suffix.lower() in IMAGE_EXTS)
    if not args.include_empty:
        images = [p for p in images if (label_dir / f"{p.stem}.txt").read_text(encoding="utf-8").strip()]

    rng = random.Random(args.seed)
    rng.shuffle(images)
    selected = images[: args.max_images]

    for image_path in selected:
        label_path = label_dir / f"{image_path.stem}.txt"
        draw_image(image_path, label_path, args.out / image_path.name, names)

    print(f"Wrote {len(selected)} visualized images to {args.out}")


if __name__ == "__main__":
    main()
