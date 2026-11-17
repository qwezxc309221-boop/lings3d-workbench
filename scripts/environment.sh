#!/usr/bin/env bash
LINGS_REPO_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -f "$LINGS_REPO_DIR/.env" ]]; then
  set -a
  source "$LINGS_REPO_DIR/.env"
  set +a
fi
export LINGS_PYTHON="${LINGS_PYTHON:-${CONDA_PREFIX:-$HOME/miniconda3/envs/pytorch}/bin/python}"
export LINGS_MODEL="${LINGS_MODEL:-$HOME/models/lingbot-map/lingbot-map.pt}"
export LINGS_ROS_ROOT="${LINGS_ROS_ROOT:-$LINGS_REPO_DIR/docker/catkin_ws/src/lings_3d}"
if [[ ! -x "$LINGS_PYTHON" ]]; then
  echo "找不到 Python：$LINGS_PYTHON。请在 .env 中设置 LINGS_PYTHON。" >&2
  return 1
fi
