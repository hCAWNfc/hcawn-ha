#!/usr/bin/env bash
# Installs the engine wheel in a clean virtualenv, downloads the real weights and runs a few sentences.
# Run from the ha_pypi folder after build_release.sh:   bash tools/smoke.sh
set -euo pipefail
cd "$(dirname "$0")/.."
V=$(mktemp -d)/venv
python -m venv "$V"
"$V/bin/pip" install -q dist_release/hcawn_ha-*manylinux*.whl pytest
"$V/bin/hcawn-ha" info
"$V/bin/hcawn-ha" download all
for q in "turn off the kitchen light" "set the living room lamp to 40 percent" "unlock the front door" "turn on the bedroom fan and pause the living room speaker"; do
  echo "--- $q"; "$V/bin/hcawn-ha" say "$q" --model int8
done
"$V/bin/hcawn-ha" say "turn off the kitchen light" --model int4 | head -8
echo "smoke test finished"
