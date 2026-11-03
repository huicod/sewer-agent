import type { EvidenceRef } from "./types";

type Props = { url: string; region: NonNullable<EvidenceRef["region"]>; label?: string };

export function EvidenceThumbnail({ url, region, label }: Props) {
  const [left, top, right, bottom] = region.bbox_xyxy;
  return (
    <svg viewBox={`0 0 ${region.width} ${region.height}`}
      role={label ? "img" : undefined} aria-label={label} aria-hidden={label ? undefined : true}>
      <image href={url} width={region.width} height={region.height} preserveAspectRatio="xMidYMid meet" />
      <rect x={left} y={top} width={right - left} height={bottom - top} />
    </svg>
  );
}
