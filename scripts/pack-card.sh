#!/usr/bin/env bash
set -euo pipefail
if [[ $# -ne 2 ]]; then
  echo '用法: scripts/pack-card.sh 输入.img 输出.img.gz' >&2
  exit 2
fi
if [[ ! -f $1 || $2 != *.img.gz ]]; then
  echo '请选择 FAT32 镜像和以 .img.gz 结尾的输出路径。' >&2
  exit 2
fi
set -o noclobber
gzip -n -c -- "$1" > "$2"
