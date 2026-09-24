import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type HTMLAttributes,
  type PointerEvent as ReactPointerEvent,
  type RefObject,
} from "react";

const MIN_WIDTH = 320;
const MAX_WIDTH = 760;
const DEFAULT_WIDTH = 420;
const MIN_CENTRAL_WIDTH = 360;
const SEPARATOR_WIDTH = 8;
const KEYBOARD_STEP = 16;
const DEFAULT_STORAGE_KEY = "pipe-inspector.assistant-width";

type AssistantResizeOptions = {
  storageKey?: string;
  defaultWidth?: number;
};

type AssistantResizeResult = {
  width: number;
  separatorProps: HTMLAttributes<HTMLDivElement>;
};

function clamp(value: number, minimum: number, maximum: number) {
  return Math.min(maximum, Math.max(minimum, value));
}

function availableMaximum(containerWidth: number) {
  if (containerWidth <= 0) return MAX_WIDTH;
  return Math.max(
    MIN_WIDTH,
    Math.min(MAX_WIDTH, containerWidth - MIN_CENTRAL_WIDTH - SEPARATOR_WIDTH),
  );
}

function readStoredWidth(storageKey: string, fallback: number) {
  const stored = Number.parseFloat(localStorage.getItem(storageKey) ?? "");
  return Number.isFinite(stored) ? clamp(stored, MIN_WIDTH, MAX_WIDTH) : fallback;
}

export function useAssistantResize(
  workspaceRef: RefObject<HTMLElement | null>,
  options: AssistantResizeOptions = {},
): AssistantResizeResult {
  const storageKey = options.storageKey ?? DEFAULT_STORAGE_KEY;
  const fallback = clamp(options.defaultWidth ?? DEFAULT_WIDTH, MIN_WIDTH, MAX_WIDTH);
  const [preferredWidth, setPreferredWidth] = useState(() =>
    readStoredWidth(storageKey, fallback),
  );
  const [containerWidth, setContainerWidth] = useState(0);
  const dragCleanupRef = useRef<(() => void) | null>(null);

  const maximum = availableMaximum(containerWidth);
  const width = clamp(preferredWidth, MIN_WIDTH, maximum);

  const updatePreferredWidth = useCallback(
    (nextWidth: number) => {
      const normalized = clamp(nextWidth, MIN_WIDTH, availableMaximum(workspaceRef.current?.clientWidth ?? 0));
      setPreferredWidth(normalized);
      localStorage.setItem(storageKey, String(normalized));
    },
    [storageKey, workspaceRef],
  );

  const stopDragging = useCallback(() => {
    dragCleanupRef.current?.();
    dragCleanupRef.current = null;
  }, []);

  useLayoutEffect(() => {
    const workspace = workspaceRef.current;
    if (!workspace) return;

    const measure = () => setContainerWidth(workspace.clientWidth);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(workspace);
    return () => observer.disconnect();
  }, [workspaceRef]);

  useEffect(() => stopDragging, [stopDragging]);

  const onPointerDown = useCallback(
    (event: ReactPointerEvent<HTMLDivElement>) => {
      if (event.button !== 0) return;
      event.preventDefault();
      stopDragging();

      const pointerId = event.pointerId;
      const startX = event.clientX;
      const startWidth = width;
      const onPointerMove = (moveEvent: PointerEvent) => {
        if (moveEvent.pointerId !== pointerId) return;
        updatePreferredWidth(startWidth + startX - moveEvent.clientX);
      };
      const cleanup = () => {
        window.removeEventListener("pointermove", onPointerMove);
        window.removeEventListener("pointerup", cleanup);
        window.removeEventListener("pointercancel", cleanup);
        dragCleanupRef.current = null;
      };

      window.addEventListener("pointermove", onPointerMove);
      window.addEventListener("pointerup", cleanup);
      window.addEventListener("pointercancel", cleanup);
      dragCleanupRef.current = cleanup;
    },
    [stopDragging, updatePreferredWidth, width],
  );

  const onKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLDivElement>) => {
      let nextWidth: number | undefined;
      if (event.key === "ArrowLeft") nextWidth = width + KEYBOARD_STEP;
      if (event.key === "ArrowRight") nextWidth = width - KEYBOARD_STEP;
      if (event.key === "Home") nextWidth = MIN_WIDTH;
      if (event.key === "End") nextWidth = maximum;
      if (nextWidth === undefined) return;
      event.preventDefault();
      updatePreferredWidth(nextWidth);
    },
    [maximum, updatePreferredWidth, width],
  );

  const separatorProps = useMemo<HTMLAttributes<HTMLDivElement>>(
    () => ({
      role: "separator",
      "aria-orientation": "vertical",
      "aria-valuemin": MIN_WIDTH,
      "aria-valuemax": maximum,
      "aria-valuenow": width,
      tabIndex: 0,
      onPointerDown,
      onKeyDown,
    }),
    [maximum, onKeyDown, onPointerDown, width],
  );

  return { width, separatorProps };
}
