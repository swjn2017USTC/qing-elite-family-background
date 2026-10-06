#!/usr/bin/env bash
# Recover the frozen v0.2 linkage artifacts from git history into the U06R interim dir.
#
# They were removed from the working tree by the public-repository prep commit but remain in
# history, and U06R needs them (the gold labels and the legacy canonical ids). Nothing is
# rewritten: the blobs are read with `git cat-file` at the frozen commit.
set -euo pipefail

REF="${1:-bd64fba}"
DEST="data/interim_v03/legacy_v02"

if ! git rev-parse --verify --quiet "${REF}^{commit}" >/dev/null; then
  echo "Cannot recover v0.2 artifacts: ${REF} is absent from this Git history." >&2
  echo "The GitHub release is intentionally a history-free public snapshot." >&2
  echo "Use an authorized full-history research clone, or rebuild from the documented source data." >&2
  exit 2
fi

mkdir -p "$DEST"

for name in linkage_gold cgedq_canonical entity_links; do
  git cat-file blob "${REF}:data/processed_v02/${name}.parquet" > "${DEST}/${name}.parquet"
  echo "recovered ${name}.parquet ($(stat -f%z "${DEST}/${name}.parquet" 2>/dev/null || stat -c%s "${DEST}/${name}.parquet") bytes) from ${REF}"
done
