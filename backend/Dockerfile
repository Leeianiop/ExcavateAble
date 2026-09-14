# 3D Scanning Reconstruction Backend — Dockerfile
#
# Layered so deps cache aggressively.
# NOTE: COLMAP and OpenMVS are system-level binaries. The default image
#       runs the pipeline in **mock/fallback mode** which is perfect for
#       local dev and end-to-end testing without 50GB of photogrammetry
#       build tooling. For production, layer in:
#         - colmap  (from nvidia/cuda:11.8 base for GPU acceleration)
#         - openMVS  (build from source with CUDA)
#         - trimesh + open3d (pip) for decimation fallback.

FROM python:3.11-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        ca-certificates \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY app /app/app

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
