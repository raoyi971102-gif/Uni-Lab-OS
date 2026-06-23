#!/usr/bin/env bash
# 构建并推送 Uni-Lab-OS Docker 镜像（需已 docker login）
set -euo pipefail

IMAGE="${UNILABOS_IMAGE:-registry-1.docker.io/styxhuang/unilabos}"
TAG="${UNILABOS_TAG:-latest}"
PLATFORM="${UNILABOS_PLATFORM:-linux/amd64}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "==> 构建 ${IMAGE}:${TAG} (${PLATFORM})"
docker build --platform "$PLATFORM" -t "${IMAGE}:${TAG}" -f Dockerfile .

echo "==> 推送 ${IMAGE}:${TAG}"
docker push "${IMAGE}:${TAG}"

echo "==> 完成。启动 UI："
cat <<EOF
docker run --rm \\
  --name unilabos-ui \\
  --platform ${PLATFORM} \\
  -p 50003:8000 \\
  ${IMAGE}:${TAG}
EOF
