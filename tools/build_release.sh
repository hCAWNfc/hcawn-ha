#!/usr/bin/env bash
# Builds the two release wheels (WITHOUT weights and WITHOUT the engine source inside the wheel):
#   dist_release/hcawn_ha-X-py3-none-manylinux_2_*_x86_64.whl   (with the engine, Linux x86-64)
#   dist_release/hcawn_ha-X-py3-none-any.whl                    (pipeline and resolver only, every other system)
# Needs Docker. Run from the ha_pypi folder:   bash tools/build_release.sh
set -euo pipefail
cd "$(dirname "$0")/.."
OUT=dist_release
FC="${FC_PYPI:-../../fc_pypi}"
rm -rf "$OUT" dist; mkdir -p "$OUT"

# 1) refresh the core and the private engine source from fc_pypi
python tools/sync_core.py --fc "$FC"

# 2) engine wheel inside a manylinux container (so it runs on older Linux systems)
docker run --rm -e HOST_UID="$(id -u)" -e HOST_GID="$(id -g)" -v "$PWD":/io quay.io/pypa/manylinux_2_28_x86_64 bash -c '
  set -e
  mkdir /tmp/src && cd /io && tar cf - --exclude=./build --exclude=./dist --exclude=./dist_release . | tar xf - -C /tmp/src
  cd /tmp/src
  PY=/opt/python/cp312-cp312/bin
  $PY/pip install -q build auditwheel
  HCAWN_REQUIRE_NATIVE=1 $PY/python -m build --wheel --outdir /tmp/w .
  mkdir -p /io/'"$OUT"'
  $PY/auditwheel repair --plat manylinux_2_28_x86_64 -w /io/'"$OUT"' /tmp/w/*.whl
  chown -R "$HOST_UID:$HOST_GID" /io/'"$OUT"'
'

# 3) wheel without the engine, works everywhere
HCAWN_PURE=1 python -m build --wheel --outdir "$OUT" .
rm -rf build

ls -la "$OUT"
echo "--- contents (there must be no .cpp and no .bin; .so files only in the manylinux wheel):"
for w in "$OUT"/*.whl; do echo "$w"; unzip -l "$w" | grep -E "\.cpp|\.bin|\.so" || true; done
