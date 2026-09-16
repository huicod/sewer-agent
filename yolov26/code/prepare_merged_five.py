"""Clean local VOC datasets and export grouped 90/5/5 YOLO data. No model/API calls.

Preserves raw data. Uncertain annotations are listed, not guessed or silently
converted to normal images. Existing evaluation outputs are never used to select data.
"""

from __future__ import annotations

import argparse
import csv
import filecmp
import html
import json
import math
import random
import re
import shutil
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import yaml
from PIL import Image, ImageDraw
from prepare_sewer2403_yolo import (
    FIVE_CLASS_MAPPING,
    FIVE_CLASSES,
    IMAGE_EXTS,
    SOURCE_CLASSES,
    BoxLabel,
    Sample,
    SampleGroup,
    group_key,
    group_priority,
    split_score,
    split_targets,
    write_label,
)
from scipy.spatial import cKDTree

CLASSES = tuple(FIVE_CLASSES)
SEDD_MAPPING = {"crack": "PL", "deposit": "BLOCK"}
PREAUG = re.compile(
    r"^(test|val)(autocontrast|contrast|empty|fanzhuan|mosaicmix|whitenoise|saturation)-", re.I
)
ROOT = Path(__file__).resolve().parents[2]


class Rejected(ValueError):
    """A sample needs manual repair or is outside this training selection."""


@dataclass
class Record:
    dataset: str
    image: Path
    xml: Path
    original_name: str
    width: int
    height: int
    labels: tuple[BoxLabel, ...]
    source_classes: tuple[str, ...]
    aliases: tuple[str, ...]
    repairs: dict[str, int]

    @property
    def identifier(self):
        return f"{self.dataset}__{self.image.stem}"

    @property
    def background_kind(self):
        if self.labels:
            return "target_positive"
        return "non_target_only" if self.source_classes else "empty_annotation"


def read_sample(image: Path, xml: Path, dataset: str) -> Record:
    root = ET.parse(xml).getroot()
    original = Path(root.findtext("filename", "").replace("\\", "/")).name
    repairs = Counter()
    if original != image.name:
        # Sewer export renamed some files to their existing Roboflow suffix.
        match = re.search(r"\.rf\.([a-f0-9]{32})$", Path(original).stem, re.I)
        if dataset == "sewer2403" and match and match[1].lower() == image.stem.lower():
            repairs["recovered_filename"] += 1
        else:
            raise Rejected("filename_mismatch")
    if PREAUG.match(original) or PREAUG.match(image.name):
        raise Rejected("preaugmented_image")
    with Image.open(image) as im:
        im.load()  # Header-only checks miss truncated/corrupt image payloads.
        width, height = im.size
        if im.getexif().get(274, 1) != 1:
            raise Rejected("exif_orientation")
    declared = (int(root.findtext("size/width", "0")), int(root.findtext("size/height", "0")))
    if declared != (width, height):
        raise Rejected("size_mismatch")
    mapping = FIVE_CLASS_MAPPING if dataset == "sewer2403" else SEDD_MAPPING
    known = set(SOURCE_CLASSES) if dataset == "sewer2403" else {"crack", "deposit", "root"}
    labels, source_classes = [], set()
    for obj in root.findall("object"):
        name = obj.findtext("name", "").strip()
        if name not in known:
            raise Rejected("unknown_class")
        source_classes.add(name)
        coords = [
            float(obj.findtext(f"bndbox/{key}", "nan")) for key in ("xmin", "ymin", "xmax", "ymax")
        ]
        x1, y1, x2, y2 = coords
        if not all(math.isfinite(v) for v in coords) or x1 >= x2 or y1 >= y2:
            raise Rejected("invalid_box")
        if x1 < -1 or y1 < -1 or x2 > width + 1 or y2 > height + 1:
            raise Rejected("box_out_of_bounds")
        clipped = [max(0, x1), max(0, y1), min(width, x2), min(height, y2)]
        if clipped != coords:
            repairs["clipped_boxes"] += 1
        x1, y1, x2, y2 = clipped
        if x1 >= x2 or y1 >= y2:
            raise Rejected("invalid_box")
        if name not in mapping:
            continue
        label = BoxLabel(
            CLASSES.index(mapping[name]),
            (x1 + x2) / (2 * width),
            (y1 + y2) / (2 * height),
            (x2 - x1) / width,
            (y2 - y1) / height,
        )
        if label in labels:
            repairs["duplicate_boxes"] += 1
        else:
            labels.append(label)
    if dataset == "sedd" and not labels:
        raise Rejected("sedd_without_target")
    aliases = tuple(
        sorted({f"{dataset}:{group_key(Path(name), 'video')}" for name in (original, image.name)})
    )
    return Record(
        dataset,
        image,
        xml,
        original,
        width,
        height,
        tuple(labels),
        tuple(sorted(source_classes)),
        aliases,
        dict(repairs),
    )


def rejected_row(dataset, image, xml, reason, **extra):
    return {"dataset": dataset, "image": str(image), "xml": str(xml), "reason": reason, **extra}


def collect(source: Path, dataset: str):
    image_dir, label_dir = (
        (source / "images", source / "labels")
        if dataset == "sewer2403"
        else (source / "image", source / "bbox")
    )
    if not image_dir.is_dir() or not label_dir.is_dir():
        raise FileNotFoundError(f"Expected source directories: {image_dir}, {label_dir}")
    images = sorted(p for p in image_dir.iterdir() if p.suffix.lower() in IMAGE_EXTS)
    if not images:
        raise ValueError(f"No images in {image_dir}")

    def load(image):
        xml = label_dir / f"{image.stem}.xml"
        if not xml.is_file():
            return rejected_row(dataset, image, xml, "missing_annotation")
        try:
            return read_sample(image, xml, dataset)
        except Rejected as exc:
            return rejected_row(dataset, image, xml, str(exc))
        except (ValueError, OSError, ET.ParseError) as exc:
            return rejected_row(dataset, image, xml, "unreadable_image_or_xml", detail=str(exc))

    rows, rejected = [], []
    with ThreadPoolExecutor(max_workers=4) as pool:
        for index, result in enumerate(pool.map(load, images), 1):
            (rows if isinstance(result, Record) else rejected).append(result)
            if index % 2000 == 0:
                print(f"{dataset}: validated {index}/{len(images)}", flush=True)
    stems = {p.stem for p in images}
    for xml in sorted(label_dir.glob("*.xml")):
        if xml.stem not in stems:
            rejected.append(rejected_row(dataset, "", xml, "orphan_annotation"))
    return rows, rejected


def deduplicate(rows):
    # Exact byte comparisons only within equal-size buckets; no integrity hashes.
    by_size = defaultdict(list)
    for index, row in enumerate(rows):
        by_size[row.image.stat().st_size].append(index)
    families = []
    for indices in by_size.values():
        representatives = []
        for i in indices:
            family = next(
                (
                    f
                    for f in representatives
                    if filecmp.cmp(rows[f[0]].image, rows[i].image, shallow=False)
                ),
                None,
            )
            if family is None:
                representatives.append([i])
            else:
                family.append(i)
        families.extend(representatives)
    rejected, excluded = [], set()
    for family in families:
        if len(family) < 2:
            continue
        signatures = {
            tuple(
                sorted(
                    (b.class_id, b.x_center, b.y_center, b.width, b.height) for b in rows[i].labels
                )
            )
            for i in family
        }
        conflict = len(signatures) > 1
        # Preserve every alias even when a duplicate image is removed.
        rows[family[0]].aliases = tuple(
            sorted({alias for i in family for alias in rows[i].aliases})
        )
        for i in family if conflict else family[1:]:
            excluded.add(i)
            row = rows[i]
            rejected.append(
                rejected_row(
                    row.dataset,
                    row.image,
                    row.xml,
                    "duplicate_annotation_conflict" if conflict else "exact_duplicate",
                    representative=rows[family[0]].identifier,
                )
            )
    return [row for i, row in enumerate(rows) if i not in excluded], rejected


def thumbnail(row):
    with Image.open(row.image) as im:
        return np.asarray(im.convert("RGB").resize((32, 32), Image.Resampling.BILINEAR))


def visual_pairs(thumbnails):
    if len(thumbnails) < 2:
        return []
    # Broad 8x8 RGB candidate gate, then 32x32 pixel MAE. Similarity groups are
    # only kept together; unlike exact copies, near images are never deleted.
    coarse = np.stack(
        [a.astype(np.float32).reshape(8, 4, 8, 4, 3).mean(axis=(1, 3)).ravel() for a in thumbnails]
    )
    pairs = cKDTree(coarse).query_pairs(r=8.0, p=np.inf, output_type="ndarray")
    result = []
    for i, j in pairs:
        a, b = thumbnails[i].astype(np.float32), thumbnails[j].astype(np.float32)
        if np.abs(a - b).mean() <= 4.0:
            result.append((int(i), int(j)))
    return sorted(result)


def connected_groups(aliases, pairs):
    parent = list(range(len(aliases)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i, j):
        a, b = find(i), find(j)
        parent[max(a, b)] = min(a, b)

    seen = {}
    for index, keys in enumerate(aliases):
        for key in keys:
            if key in seen:
                union(index, seen[key])
            else:
                seen[key] = index
    for i, j in pairs:
        union(i, j)
    return [find(i) for i in range(len(aliases))]


def load_preparation(directory):
    rows = []
    for line in (directory / "cleaned_manifest.jsonl").read_text().splitlines():
        data = json.loads(line)
        group = data.pop("group_id")
        data["image"], data["xml"] = Path(data["image"]), Path(data["xml"])
        data["labels"] = tuple(BoxLabel(**label) for label in data["labels"])
        data["source_classes"] = tuple(data["source_classes"])
        # Preserve transitive links through images excluded in final selection.
        data["aliases"] = (*data["aliases"], f"preparation-group:{group}")
        rows.append(Record(**data))
    rejected = [
        json.loads(line) for line in (directory / "excluded.jsonl").read_text().splitlines()
    ]
    return rows, rejected


def select_five_only(rows):
    kept, rejected = [], []
    for row in rows:
        known = FIVE_CLASS_MAPPING if row.dataset == "sewer2403" else SEDD_MAPPING
        if set(row.source_classes) - set(known):
            reason = "mixed_non_target" if row.labels else "non_target_only"
            rejected.append(rejected_row(row.dataset, row.image, row.xml, reason))
        else:
            kept.append(row)
    return kept, rejected


def assign_balanced_groups(groups, ratios, targets, totals, seed):
    """Balance relative deficits: shared classes must not swamp a smaller source."""
    rng = random.Random(seed)
    ordered = sorted(groups, key=lambda g: group_priority(g, totals, rng.random()))
    assigned = {split: [] for split in ratios}
    sizes = Counter()
    counts = {split: Counter() for split in ratios}
    for group in ordered:
        candidates = [s for s in ratios if sizes[s] + group.size <= targets[s]] or list(ratios)
        rng.shuffle(candidates)

        def gain(split):
            score = 0.0
            for label, count in group.class_image_counts.items():
                target = totals[label] * ratios[split]
                before = target - counts[split][label]
                score += (before**2 - (before - count) ** 2) / max(target, 1)
            before = targets[split] - sizes[split]
            score += 0.05 * (before**2 - (before - group.size) ** 2) / max(targets[split], 1)
            return score, -max(0, sizes[split] + group.size - targets[split])

        selected = max(candidates, key=gain)
        assigned[selected].append(group)
        sizes[selected] += group.size
        counts[selected].update(group.class_image_counts)
    return assigned


def split_rows(rows, group_ids, *, seed):
    grouped = defaultdict(list)
    for row, group in zip(rows, group_ids, strict=True):
        grouped[group].append(row)
    groups = []
    for key, members in sorted(grouped.items()):
        counts = Counter()
        for row in members:
            labels = {b.class_id for b in row.labels} or {5}
            counts.update(labels)  # Overall class/background balance.
            counts.update(10 + (0 if row.dataset == "sewer2403" else 10) + c for c in labels)
        groups.append(
            SampleGroup(str(key), [Sample(r.image, r.labels, frozenset()) for r in members], counts)
        )
    totals = sum((g.class_image_counts for g in groups), Counter())
    ratios, targets = split_targets(len(rows), 0.9, 0.05)
    best, best_score = None, (math.inf, math.inf)
    for attempt in range(32):
        assignment = assign_balanced_groups(groups, ratios, targets, totals, seed + attempt)
        score = split_score(assignment, ratios, targets, totals, 0.025)
        if score < best_score:
            best, best_score = assignment, score
    if best is None or best_score[0]:
        raise ValueError(f"Cannot obtain balanced grouped splits; shortfall={best_score[0]}")
    lookup = {int(g.key): split for split, groups in best.items() for g in groups}
    return [lookup[g] for g in group_ids]


def write_preparation(out, rows, groups, rejected, pairs):
    """Persist cleaning work while a material split-policy choice is pending."""
    out.mkdir(parents=True, exist_ok=False)
    with (out / "cleaned_manifest.jsonl").open("w", encoding="utf-8") as f:
        for row, group in zip(rows, groups, strict=True):
            data = asdict(row)
            data.update(image=str(row.image.resolve()), xml=str(row.xml.resolve()), group_id=group)
            f.write(json.dumps(data, ensure_ascii=False) + "\n")
    with (out / "excluded.jsonl").open("w", encoding="utf-8") as f:
        for row in rejected:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    with (out / "similar_pairs.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["first_id", "second_id", "group_id"])
        writer.writerows((rows[i].identifier, rows[j].identifier, groups[i]) for i, j in pairs)
    summary = {
        "status": "cleaned_candidates_pending_split_policy",
        "images": len(rows),
        "source_images": dict(Counter(r.dataset for r in rows)),
        "class_boxes": dict(Counter(CLASSES[b.class_id] for r in rows for b in r.labels)),
        "groups": len(set(groups)),
        "largest_group": max(Counter(groups).values()),
        "similar_pairs": len(pairs),
        "excluded_by_source": {
            d: dict(Counter(r["reason"] for r in rejected if r["dataset"] == d))
            for d in ("sewer2403", "sedd")
        },
        "repairs": dict(sum((Counter(r.repairs) for r in rows), Counter())),
    }
    (out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return summary


def export_dataset(out, rows, group_ids, splits, rejected, pairs, *, seed):
    out.mkdir(parents=True, exist_ok=False)
    identifiers = [r.identifier for r in rows]
    if len(set(identifiers)) != len(rows):
        raise ValueError("duplicate_output_identifiers")
    split_counts, source_counts, class_images, class_boxes, backgrounds = (
        Counter(splits),
        {},
        {},
        {},
        {},
    )
    for split in ("train", "val", "test"):
        (out / "images" / split).mkdir(parents=True)
        (out / "labels" / split).mkdir(parents=True)
        source_counts[split] = Counter()
        class_images[split] = Counter({c: 0 for c in CLASSES})
        class_boxes[split] = Counter({c: 0 for c in CLASSES})
        backgrounds[split] = Counter()
    with (out / "split_manifest.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "id",
                "dataset",
                "split",
                "group_id",
                "image",
                "source_image",
                "source_xml",
                "original_filename",
                "width",
                "height",
                "classes",
                "source_classes",
                "background_kind",
                "group_aliases",
                "repairs",
            ],
        )
        writer.writeheader()
        for row, group, split in zip(rows, group_ids, splits, strict=True):
            name = row.identifier + row.image.suffix.lower()
            shutil.copy2(row.image, out / "images" / split / name)
            write_label(out / "labels" / split / f"{row.identifier}.txt", row.labels)
            names = [CLASSES[b.class_id] for b in row.labels]
            source_counts[split][row.dataset] += 1
            class_images[split].update(set(names))
            class_boxes[split].update(names)
            backgrounds[split][row.background_kind] += 1
            writer.writerow(
                dict(
                    id=row.identifier,
                    dataset=row.dataset,
                    split=split,
                    group_id=group,
                    image=f"images/{split}/{name}",
                    source_image=str(row.image.resolve()),
                    source_xml=str(row.xml.resolve()),
                    original_filename=row.original_name,
                    width=row.width,
                    height=row.height,
                    classes="|".join(sorted(set(names))),
                    source_classes="|".join(row.source_classes),
                    background_kind=row.background_kind,
                    group_aliases="|".join(row.aliases),
                    repairs=json.dumps(row.repairs),
                )
            )
    config = {
        "path": str(out.resolve()),
        "train": "images/train",
        "val": "images/val",
        "test": "images/test",
        "names": dict(enumerate(CLASSES)),
    }
    (out / "data.yaml").write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    (out / "eval").mkdir()
    for dataset in ("sewer2403", "sedd"):
        source_config = dict(config)
        for split in ("val", "test"):
            paths = [
                str((out / "images" / split / (r.identifier + r.image.suffix.lower())).resolve())
                for r, s in zip(rows, splits, strict=True)
                if r.dataset == dataset and s == split
            ]
            relative = f"eval/{dataset}_{split}.txt"
            (out / relative).write_text("".join(p + "\n" for p in paths), encoding="utf-8")
            source_config[split] = relative
        (out / f"eval_{dataset}.yaml").write_text(
            yaml.safe_dump(source_config, sort_keys=False), encoding="utf-8"
        )
    with (out / "excluded.jsonl").open("w", encoding="utf-8") as f:
        for row in rejected:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    with (out / "similar_pairs.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["first_id", "second_id", "group_id"])
        for i, j in pairs:
            assert group_ids[i] == group_ids[j] and splits[i] == splits[j]
            writer.writerow([rows[i].identifier, rows[j].identifier, group_ids[i]])
    group_splits = defaultdict(set)
    for group, split in zip(group_ids, splits, strict=True):
        group_splits[group].add(split)
    assert all(len(values) == 1 for values in group_splits.values())
    summary = {
        "version": "merged-five-v1",
        "seed": seed,
        "requested_ratios": {"train": 0.9, "val": 0.05, "test": 0.05},
        "images": len(rows),
        "split_images": split_counts,
        "actual_ratios": {k: v / len(rows) for k, v in split_counts.items()},
        "source_images": source_counts,
        "class_positive_images": class_images,
        "class_boxes": class_boxes,
        "background_kinds": backgrounds,
        "groups": len(set(group_ids)),
        "largest_group": max(Counter(group_ids).values()),
        "cross_split_groups": 0,
        "near_image_pairs": len(pairs),
        "excluded_by_source": {
            d: dict(Counter(r["reason"] for r in rejected if r["dataset"] == d))
            for d in ("sewer2403", "sedd")
        },
        "repairs_in_retained_images": dict(sum((Counter(r.repairs) for r in rows), Counter())),
        "mapping": {"sewer2403": FIVE_CLASS_MAPPING, "sedd": SEDD_MAPPING},
        "evaluation_scope": {"sewer2403": list(CLASSES), "sedd": ["PL", "BLOCK"]},
        "limitations": [
            "Known filename/video aliases and detected visual-near-copy groups are isolated. "
            "Unknown pipe/video identity cannot be guaranteed independent.",
            "SEDD supplies PL/BLOCK annotations, not exhaustive FS/BX/AJ truth. "
            "No automatic semantic relabeling or five-class human audit performed.",
            "Missing SEDD XML alone is not evidence of normality. Final selection excludes "
            "all explicitly annotated non-target defects, including mixed-label images.",
            "The old sewer-trained best.pt may have seen new val/test images; "
            "this split is intended for fresh training from generic pretrained weights.",
        ],
    }
    (out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return summary


def write_gallery(out, rows, splits):
    gallery = out / "preview"
    gallery.mkdir()
    chosen, seen = [], set()
    for dataset in ("sewer2403", "sedd"):
        for split in dict.fromkeys(splits):
            for label in range(5):
                selected = next(
                    (
                        r
                        for r, s in zip(rows, splits)
                        if r.dataset == dataset
                        and s == split
                        and r.identifier not in seen
                        and any(b.class_id == label for b in r.labels)
                    ),
                    None,
                )
                if selected:
                    chosen.append((selected, split))
                    seen.add(selected.identifier)
    entries = []
    for row, split in chosen:
        with Image.open(row.image) as original:
            im = original.convert("RGB")
        im.thumbnail((960, 720))
        draw = ImageDraw.Draw(im)
        for box in row.labels:
            x1, y1 = (
                (box.x_center - box.width / 2) * im.width,
                (box.y_center - box.height / 2) * im.height,
            )
            x2, y2 = (
                (box.x_center + box.width / 2) * im.width,
                (box.y_center + box.height / 2) * im.height,
            )
            draw.rectangle((x1, y1, x2, y2), outline="lime", width=2)
            draw.text(
                (x1 + 2, y1 + 2),
                CLASSES[box.class_id],
                fill="yellow",
                stroke_width=1,
                stroke_fill="black",
            )
        name = row.identifier + ".jpg"
        im.save(gallery / name, quality=90)
        entries.append(
            f"<h3>{html.escape(split + ' / ' + row.identifier)}</h3>"
            f"<img loading='lazy' style='max-width:100%' src='{html.escape(name, quote=True)}'>"
        )
    (gallery / "index.html").write_text(
        "<!doctype html><meta charset='utf-8'><title>Five-class label preview</title>"
        "<h1>原有标注转换预览（不是检测预测）</h1>" + "\n".join(entries),
        encoding="utf-8",
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sewer",
        type=Path,
        default=ROOT / "var/datasets/sewer2403_source/extracted/市政下水雨污管道缺陷数据2403",
    )
    parser.add_argument("--sedd", type=Path, default=ROOT / "var/datasets/sedd_external")
    parser.add_argument("--out", type=Path, default=ROOT / "var/datasets/merged_five_v1")
    parser.add_argument("--seed", type=int, default=20260916)
    parser.add_argument(
        "--from-preparation", type=Path, help="Reuse a verified cleaning checkpoint."
    )
    parser.add_argument(
        "--prepare-only",
        action="store_true",
        help="Save cleaned candidates without deciding splits.",
    )
    args = parser.parse_args()
    if args.out.exists():
        raise FileExistsError(args.out)
    if args.from_preparation:
        rows, rejected = load_preparation(args.from_preparation)
    else:
        rows, rejected = [], []
        for dataset, source in (("sewer2403", args.sewer), ("sedd", args.sedd)):
            accepted, excluded = collect(source, dataset)
            rows.extend(accepted)
            rejected.extend(excluded)
            print(f"{dataset}: accepted={len(accepted)}, excluded={len(excluded)}", flush=True)
        rows, duplicates = deduplicate(rows)
        rejected.extend(duplicates)
        print(f"Exact-copy check: retained={len(rows)}, excluded={len(duplicates)}", flush=True)
    if not args.prepare_only:
        rows, outside_scope = select_five_only(rows)
        rejected.extend(outside_scope)
        print(f"Five-class-only selection: retained={len(rows)}", flush=True)
    if args.from_preparation:
        indices = {r.identifier: i for i, r in enumerate(rows)}
        with (args.from_preparation / "similar_pairs.csv").open() as f:
            pairs = [
                (indices[r["first_id"]], indices[r["second_id"]])
                for r in csv.DictReader(f)
                if r["first_id"] in indices and r["second_id"] in indices
            ]
    else:
        with ThreadPoolExecutor(max_workers=4) as pool:
            thumbnails = list(pool.map(thumbnail, rows))
        pairs = visual_pairs(thumbnails)
    groups = connected_groups([r.aliases for r in rows], pairs)
    print(f"Grouping: {len(set(groups))} groups, {len(pairs)} near-copy pairs", flush=True)
    if args.prepare_only:
        summary = write_preparation(args.out, rows, groups, rejected, pairs)
        write_gallery(args.out, rows, ["unassigned"] * len(rows))
        print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
        return
    splits = split_rows(rows, groups, seed=args.seed)
    summary = export_dataset(args.out, rows, groups, splits, rejected, pairs, seed=args.seed)
    write_gallery(args.out, rows, splits)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
