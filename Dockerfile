# syntax=docker/dockerfile:1
FROM node:22.21.1-bookworm-slim AS frontend
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.12.12-slim-bookworm AS runtime
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
    HF_HOME=/tmp/huggingface TORCH_HOME=/tmp/torch \
    YOLO_CONFIG_DIR=/tmp/ultralytics MPLCONFIGDIR=/tmp/matplotlib
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
      ffmpeg libgl1 libglib2.0-0 libgomp1 ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 sewer && useradd --uid 10001 --gid sewer --create-home sewer
# CUDA user-space libraries come with these official wheels; the host supplies the driver.
RUN python -m pip install torch==2.8.0 torchvision==0.23.0 \
      --index-url https://download.pytorch.org/whl/cu128
COPY pyproject.toml ./
COPY backend/src/ backend/src/
RUN python -m pip install '.[gpu,enhancement,geometry]' && python -m pip check
COPY scripts/ scripts/
COPY deploy/ deploy/
COPY --chown=sewer:sewer example/ example/
COPY --from=frontend /web/dist/ frontend/dist/
RUN mkdir -p /app/var/assets /app/var/settings && chmod 700 /app/var/settings \
    && chown -R sewer:sewer /app/var /home/sewer
USER sewer
EXPOSE 8000
ENTRYPOINT ["python", "deploy/docker/entrypoint.py"]
CMD ["api"]
