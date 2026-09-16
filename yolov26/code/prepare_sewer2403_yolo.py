from __future__ import annotations

import argparse
import csv
import math
import random
import re
import shutil
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

import yaml


SOURCE_CLASSES = [
    "AJ",
    "BX",
    "CJ",
    "CK",
    "CQ",
    "CR",
    "FS",
    "JG",
    "Joint",
    "PL",
    "QF",
    "SG",
    "SL",
    "TJ",
    "TL",
    "YW",
    "ZW",
]
SOURCE_CLASS_TO_ID = {name: idx for idx, name in enumerate(SOURCE_CLASSES)}
FIVE_CLASSES = ["PL", "BX", "FS", "BLOCK", "AJ"]
FIVE_CLASS_MAPPING = {
    "PL": "PL",
    "BX": "BX",
    "FS": "FS",
    "CJ": "BLOCK",
    "JG": "BLOCK",
    "ZW": "BLOCK",
    "AJ": "AJ",
}
CLASSES = SOURCE_CLASSES.copy()
CLASS_TO_ID = {name: idx for idx, name in enumerate(CLASSES)}
SOURCE_TO_TARGET = {name: name for name in SOURCE_CLASSES}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp"}
SPLIT_NAMES = ("train", "val", "test")
ROBOFLOW_SUFFIX_RE = re.compile(r"\.rf\.[0-9a-f]{32}$", re.IGNORECASE)
AUGMENTED_SET_RE = re.compile(
    r"^(test|val)(?:autocontrast|contrast|empty|fanzhuan|mosaicmix|whitenoise|saturation)-(\d+)-?$",
    re.IGNORECASE,
)
VIDEO_FRAME_RES = (
    re.compile(r"^(.*?-(?:mp4|mpg|avi|mov))-t-\d", re.IGNORECASE),
    re.compile(r"^(.*?-(?:mp4|mpg|avi|mov))[_-]\d{6}[-_]", re.IGNORECASE),
    re.compile(r"^(.*?)[_-]frame\d+$", re.IGNORECASE),
)


@dataclass(frozen=True)
class BoxLabel:
    class_id: int
    x_center: float
    y_center: float
    width: float
    height: float


@dataclass(frozen=True)
class Sample:
    image_path: Path
    labels: tuple[BoxLabel, ...]
    source_class_ids: frozenset[int]

    @property
    def class_ids(self) -> frozenset[int]:
        return frozenset(label.class_id for label in self.labels)


@dataclass
class SampleGroup:
    key: str
    samples: list[Sample]
    class_image_counts: Counter[int]

    @property
    def size(self) -> int:
        return len(self.samples)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert sewer2403 VOC annotations to a grouped, multilabel-stratified YOLO dataset."
    )
    parser.add_argument("--source", type=Path, default=Path("../shaoguan-data/sewer2403"))
    parser.add_argument(
        "--profile",
        choices=["17class", "five"],
        default="17class",
        help="Use all 17 source classes, or PL/BX/FS/BLOCK/AJ only.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output directory. Defaults depend on --profile.",
    )
    parser.add_argument("--train-ratio", type=float, default=0.8)
    parser.add_argument("--val-ratio", type=float, default=0.1)
    parser.add_argument("--seed", type=int, default=3407)
    parser.add_argument(
        "--group-mode",
        choices=["none", "source", "video"],
        default="video",
        help="Keep exact source variants together; video also keeps frames from the same video together.",
    )
    parser.add_argument(
        "--min-eval-class-ratio",
        type=float,
        default=0.08,
        help="Minimum fraction of each class-positive image count required in both val and test.",
    )
    parser.add_argument("--split-attempts", type=int, default=64)
    parser.add_argument(
        "--image-mode",
        choices=["copy", "hardlink", "symlink"],
        default="hardlink",
        help="How to place images into the derived YOLO dataset.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and split the dataset without creating output files.",
    )
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def activate_profile(profile: str) -> None:
    global CLASSES, CLASS_TO_ID, SOURCE_TO_TARGET

    if profile == "five":
        CLASSES = FIVE_CLASSES.copy()
        SOURCE_TO_TARGET = FIVE_CLASS_MAPPING.copy()
    else:
        CLASSES = SOURCE_CLASSES.copy()
        SOURCE_TO_TARGET = {name: name for name in SOURCE_CLASSES}
    CLASS_TO_ID = {name: idx for idx, name in enumerate(CLASSES)}


def normalized_source_stem(image_path: Path) -> str:
    stem = ROBOFLOW_SUFFIX_RE.sub("", image_path.stem)
    stem = re.sub(r"_(?:jpg|jpeg|png|bmp)$", "", stem, flags=re.IGNORECASE)
    return stem.rstrip("_-")


def group_key(image_path: Path, mode: str) -> str:
    if mode == "none":
        return image_path.stem.lower()

    stem = normalized_source_stem(image_path)
    augmented_match = AUGMENTED_SET_RE.match(stem)
    if augmented_match:
        split_origin, source_id = augmented_match.groups()
        return f"augmentation:{split_origin.lower()}:{int(source_id)}"

    if mode == "video":
        for pattern in VIDEO_FRAME_RES:
            match = pattern.match(stem)
            if match:
                return f"video:{match.group(1).lower()}"

    return f"source:{stem.lower()}"


def read_voc(xml_path: Path) -> tuple[tuple[BoxLabel, ...], frozenset[int]]:
    root = ET.parse(xml_path).getroot()
    size = root.find("size")
    width = int(size.findtext("width", "0")) if size is not None else 0
    height = int(size.findtext("height", "0")) if size is not None else 0
    if width <= 0 or height <= 0:
        raise ValueError(f"Invalid image size in {xml_path}")

    labels: list[BoxLabel] = []
    source_class_ids: set[int] = set()
    for obj in root.findall("object"):
        source_name = (obj.findtext("name") or "").strip()
        if source_name not in SOURCE_CLASSES:
            raise ValueError(f"Unknown class {source_name!r} in {xml_path}")
        source_class_ids.add(SOURCE_CLASS_TO_ID[source_name])
        target_name = SOURCE_TO_TARGET.get(source_name)
        if target_name is None:
            continue

        box = obj.find("bndbox")
        if box is None:
            continue
        xmin = max(0.0, min(float(box.findtext("xmin", "0")), width))
        ymin = max(0.0, min(float(box.findtext("ymin", "0")), height))
        xmax = max(0.0, min(float(box.findtext("xmax", "0")), width))
        ymax = max(0.0, min(float(box.findtext("ymax", "0")), height))
        if xmax <= xmin or ymax <= ymin:
            continue

        labels.append(
            BoxLabel(
                class_id=CLASS_TO_ID[target_name],
                x_center=((xmin + xmax) / 2.0) / width,
                y_center=((ymin + ymax) / 2.0) / height,
                width=(xmax - xmin) / width,
                height=(ymax - ymin) / height,
            )
        )
    return tuple(labels), frozenset(source_class_ids)


def load_samples(source: Path) -> list[Sample]:
    image_dir = source / "images"
    label_dir = source / "labels"
    if not image_dir.is_dir() or not label_dir.is_dir():
        raise FileNotFoundError(f"Expected {image_dir} and {label_dir}")

    images = sorted(
        (path for path in image_dir.iterdir() if path.suffix.lower() in IMAGE_EXTS),
        key=lambda path: (path.name.casefold(), path.name),
    )
    if not images:
        raise ValueError(f"No supported images found in {image_dir}")

    samples: list[Sample] = []
    missing_labels: list[Path] = []
    for image_path in images:
        xml_path = label_dir / f"{image_path.stem}.xml"
        if not xml_path.exists():
            missing_labels.append(xml_path)
            continue
        labels, source_class_ids = read_voc(xml_path)
        samples.append(
            Sample(
                image_path=image_path,
                labels=labels,
                source_class_ids=source_class_ids,
            )
        )

    if missing_labels:
        preview = "\n".join(str(path) for path in missing_labels[:10])
        raise FileNotFoundError(f"{len(missing_labels)} images have no matching XML annotation:\n{preview}")

    image_stems = {sample.image_path.stem for sample in samples}
    extra_xml = [path for path in label_dir.glob("*.xml") if path.stem not in image_stems]
    if extra_xml:
        preview = "\n".join(str(path) for path in extra_xml[:10])
        raise FileNotFoundError(f"{len(extra_xml)} XML annotations have no matching image:\n{preview}")
    return samples


def build_groups(samples: list[Sample], mode: str) -> list[SampleGroup]:
    grouped: dict[str, list[Sample]] = defaultdict(list)
    for sample in samples:
        grouped[group_key(sample.image_path, mode)].append(sample)

    groups: list[SampleGroup] = []
    for key, group_samples in grouped.items():
        class_image_counts: Counter[int] = Counter()
        for sample in group_samples:
            class_image_counts.update(sample.source_class_ids)
        groups.append(
            SampleGroup(
                key=key,
                samples=sorted(group_samples, key=lambda sample: sample.image_path.name),
                class_image_counts=class_image_counts,
            )
        )
    return groups


def split_targets(total: int, train_ratio: float, val_ratio: float) -> tuple[dict[str, float], dict[str, int]]:
    test_ratio = 1.0 - train_ratio - val_ratio
    if train_ratio <= 0 or val_ratio <= 0 or test_ratio <= 0:
        raise ValueError("Require train_ratio > 0, val_ratio > 0, and train_ratio + val_ratio < 1.")

    ratios = {"train": train_ratio, "val": val_ratio, "test": test_ratio}
    sizes = {
        "train": int(total * train_ratio),
        "val": int(total * val_ratio),
    }
    sizes["test"] = total - sizes["train"] - sizes["val"]
    return ratios, sizes


def group_priority(group: SampleGroup, class_totals: Counter[int], random_tie: float) -> tuple[float, int, int, float]:
    rarity = min((class_totals[class_id] for class_id in group.class_image_counts), default=math.inf)
    return rarity, -len(group.class_image_counts), -group.size, random_tie


def assign_once(
    groups: list[SampleGroup],
    ratios: dict[str, float],
    target_sizes: dict[str, int],
    class_totals: Counter[int],
    seed: int,
) -> dict[str, list[SampleGroup]]:
    rng = random.Random(seed)
    ordered = sorted(
        groups,
        key=lambda group: group_priority(group, class_totals, rng.random()),
    )
    assigned = {split: [] for split in SPLIT_NAMES}
    current_sizes = Counter({split: 0 for split in SPLIT_NAMES})
    current_classes = {split: Counter() for split in SPLIT_NAMES}
    total_samples = sum(target_sizes.values())

    for group in ordered:
        fitting = [
            split
            for split in SPLIT_NAMES
            if current_sizes[split] + group.size <= target_sizes[split]
        ]
        candidates = fitting or list(SPLIT_NAMES)
        rng.shuffle(candidates)

        def assignment_gain(split: str) -> tuple[float, float, int]:
            class_gain = 0.0
            for class_id, count in group.class_image_counts.items():
                target = class_totals[class_id] * ratios[split]
                before = target - current_classes[split][class_id]
                after = before - count
                class_gain += (before * before - after * after) / max(class_totals[class_id], 1)

            size_before = target_sizes[split] - current_sizes[split]
            size_after = size_before - group.size
            size_gain = (size_before * size_before - size_after * size_after) / max(total_samples, 1)
            overflow = max(0, current_sizes[split] + group.size - target_sizes[split])
            return class_gain + 0.05 * size_gain, -overflow, target_sizes[split] - current_sizes[split]

        selected = max(candidates, key=assignment_gain)
        assigned[selected].append(group)
        current_sizes[selected] += group.size
        current_classes[selected].update(group.class_image_counts)

    return assigned


def split_statistics(
    assigned: dict[str, list[SampleGroup]],
) -> tuple[Counter[str], dict[str, Counter[int]], dict[str, Counter[int]]]:
    split_sizes: Counter[str] = Counter()
    class_images = {split: Counter() for split in SPLIT_NAMES}
    class_objects = {split: Counter() for split in SPLIT_NAMES}
    for split, groups in assigned.items():
        for group in groups:
            split_sizes[split] += group.size
            for sample in group.samples:
                class_images[split].update(sample.class_ids)
                class_objects[split].update(label.class_id for label in sample.labels)
    return split_sizes, class_images, class_objects


def source_split_statistics(
    assigned: dict[str, list[SampleGroup]],
) -> tuple[Counter[str], dict[str, Counter[int]]]:
    split_sizes: Counter[str] = Counter()
    class_images = {split: Counter() for split in SPLIT_NAMES}
    for split, groups in assigned.items():
        for group in groups:
            split_sizes[split] += group.size
            class_images[split].update(group.class_image_counts)
    return split_sizes, class_images


def split_score(
    assigned: dict[str, list[SampleGroup]],
    ratios: dict[str, float],
    target_sizes: dict[str, int],
    class_totals: Counter[int],
    min_eval_class_ratio: float,
) -> tuple[int, float]:
    split_sizes, class_images = source_split_statistics(assigned)
    violations = 0
    error = 0.0

    for split in SPLIT_NAMES:
        error += abs(split_sizes[split] - target_sizes[split]) / max(target_sizes[split], 1)
        for class_id, total in class_totals.items():
            error += abs(class_images[split][class_id] / total - ratios[split])

    for split in ("val", "test"):
        for class_id, total in class_totals.items():
            required = max(1, math.floor(total * min_eval_class_ratio))
            violations += max(0, required - class_images[split][class_id])
    return violations, error


def stratified_group_split(
    groups: list[SampleGroup],
    total_samples: int,
    train_ratio: float,
    val_ratio: float,
    seed: int,
    attempts: int,
    min_eval_class_ratio: float,
) -> tuple[dict[str, list[SampleGroup]], dict[str, float], dict[str, int]]:
    ratios, target_sizes = split_targets(total_samples, train_ratio, val_ratio)
    if not 0 <= min_eval_class_ratio <= min(ratios["val"], ratios["test"]):
        raise ValueError("min_eval_class_ratio must be between 0 and the smaller evaluation split ratio.")
    if attempts <= 0:
        raise ValueError("split_attempts must be positive.")

    class_totals: Counter[int] = Counter()
    for group in groups:
        class_totals.update(group.class_image_counts)
    missing_classes = [
        SOURCE_CLASSES[class_id]
        for class_id in range(len(SOURCE_CLASSES))
        if class_totals[class_id] == 0
    ]
    if missing_classes:
        raise ValueError(f"Dataset contains no positive images for classes: {missing_classes}")

    best_assignment: dict[str, list[SampleGroup]] | None = None
    best_score = (math.inf, math.inf)
    for attempt in range(attempts):
        assignment = assign_once(groups, ratios, target_sizes, class_totals, seed + attempt)
        score = split_score(assignment, ratios, target_sizes, class_totals, min_eval_class_ratio)
        if score < best_score:
            best_assignment = assignment
            best_score = score
        if score == (0, 0.0):
            break

    if best_assignment is None or best_score[0] > 0:
        raise RuntimeError(
            f"Could not satisfy minimum evaluation class ratio after {attempts} attempts; "
            f"remaining class-image shortfall={best_score[0]}."
        )
    return best_assignment, ratios, target_sizes


def place_image(src: Path, dst: Path, mode: str) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if mode == "copy":
        shutil.copy2(src, dst)
    elif mode == "hardlink":
        try:
            dst.hardlink_to(src)
        except OSError:
            shutil.copy2(src, dst)
    else:
        try:
            dst.symlink_to(src.resolve())
        except OSError:
            shutil.copy2(src, dst)


def write_label(path: Path, labels: tuple[BoxLabel, ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"{label.class_id} {label.x_center:.6f} {label.y_center:.6f} {label.width:.6f} {label.height:.6f}"
        for label in labels
    ]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def write_dataset(
    out: Path,
    assigned: dict[str, list[SampleGroup]],
    image_mode: str,
    group_mode: str,
    ratios: dict[str, float],
    seed: int,
    min_eval_class_ratio: float,
    split_attempts: int,
    profile: str,
) -> None:
    manifest_path = out / "split_manifest.csv"
    summary_path = out / "split_summary.csv"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    with manifest_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["split", "image", "group_key", "classes"])
        for split in SPLIT_NAMES:
            for group in sorted(assigned[split], key=lambda item: item.key):
                for sample in group.samples:
                    image_out = out / "images" / split / sample.image_path.name
                    label_out = out / "labels" / split / f"{sample.image_path.stem}.txt"
                    place_image(sample.image_path, image_out, image_mode)
                    write_label(label_out, sample.labels)
                    names = "|".join(CLASSES[class_id] for class_id in sorted(sample.class_ids))
                    writer.writerow([split, sample.image_path.name, group.key, names])

    split_sizes, class_images, class_objects = split_statistics(assigned)
    with summary_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "class",
                "total_positive_images",
                "train_images",
                "val_images",
                "test_images",
                "train_objects",
                "val_objects",
                "test_objects",
            ]
        )
        for class_id, name in enumerate(CLASSES):
            writer.writerow(
                [
                    name,
                    sum(class_images[split][class_id] for split in SPLIT_NAMES),
                    class_images["train"][class_id],
                    class_images["val"][class_id],
                    class_images["test"][class_id],
                    class_objects["train"][class_id],
                    class_objects["val"][class_id],
                    class_objects["test"][class_id],
                ]
            )

    dataset_yaml = {
        "path": str(out).replace("\\", "/"),
        "train": "images/train",
        "val": "images/val",
        "test": "images/test",
        "names": {idx: name for idx, name in enumerate(CLASSES)},
    }
    split_metadata = {
        "algorithm_version": 2,
        "strategy": "grouped_multilabel_stratified_source_classes",
        "profile": profile,
        "source_to_target": SOURCE_TO_TARGET,
        "group_mode": group_mode,
        "ratios": ratios,
        "seed": seed,
        "min_eval_class_ratio": min_eval_class_ratio,
        "split_attempts": split_attempts,
    }
    yaml_name = "sewer2403_5class.yaml" if profile == "five" else "sewer2403.yaml"
    (out / yaml_name).write_text(
        yaml.safe_dump(dataset_yaml, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    (out / "split_metadata.yaml").write_text(
        yaml.safe_dump(split_metadata, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )

    print(f"Wrote YOLO dataset: {out}")
    print(f"Wrote data yaml: {out / yaml_name}")
    print(f"Wrote split metadata: {out / 'split_metadata.yaml'}")
    print(f"Wrote split manifest: {manifest_path}")
    print(f"Wrote split summary: {summary_path}")
    print_split_summary(assigned)


def print_split_summary(assigned: dict[str, list[SampleGroup]]) -> None:
    split_sizes, class_images, _ = split_statistics(assigned)
    print("Split sizes:")
    for split in SPLIT_NAMES:
        print(f"  {split}: {split_sizes[split]} images")
    print("Per-class positive image counts:")
    print("  class       total   train     val    test")
    for class_id, name in enumerate(CLASSES):
        total = sum(class_images[split][class_id] for split in SPLIT_NAMES)
        print(
            f"  {name:<10} {total:>5} {class_images['train'][class_id]:>7} "
            f"{class_images['val'][class_id]:>7} {class_images['test'][class_id]:>7}"
        )


def main() -> None:
    args = parse_args()
    activate_profile(args.profile)
    source = args.source.resolve()
    default_out = (
        Path("runs_data/sewer2403_5class_yolo")
        if args.profile == "five"
        else Path("runs_data/sewer2403_yolo")
    )
    out = (args.out or default_out).resolve()
    if not args.dry_run and out.exists():
        if not args.overwrite:
            raise FileExistsError(f"Output already exists: {out}. Pass --overwrite to replace it.")
        shutil.rmtree(out)

    samples = load_samples(source)
    groups = build_groups(samples, args.group_mode)
    assigned, ratios, _ = stratified_group_split(
        groups=groups,
        total_samples=len(samples),
        train_ratio=args.train_ratio,
        val_ratio=args.val_ratio,
        seed=args.seed,
        attempts=args.split_attempts,
        min_eval_class_ratio=args.min_eval_class_ratio,
    )
    print(f"Loaded {len(samples)} images in {len(groups)} groups using group_mode={args.group_mode}.")
    if args.dry_run:
        print_split_summary(assigned)
        print("Dry run complete; no output files were created.")
        return
    write_dataset(
        out=out,
        assigned=assigned,
        image_mode=args.image_mode,
        group_mode=args.group_mode,
        ratios=ratios,
        seed=args.seed,
        min_eval_class_ratio=args.min_eval_class_ratio,
        split_attempts=args.split_attempts,
        profile=args.profile,
    )


if __name__ == "__main__":
    main()
