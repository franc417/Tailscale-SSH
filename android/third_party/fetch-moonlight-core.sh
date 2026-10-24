#!/usr/bin/env bash
# Fetches the native half of Moonlight (moonlight-common-c + ENet + the prebuilt OpenSSL/Opus static libraries
# + the JNI glue) at ONE pinned commit of moonlight-stream/moonlight-android, into
# android/stream/src/main/jni/moonlight-core (git-ignored: ~50 MB of mostly prebuilt binaries).
# The Java half is vendored in the repo (android/stream/src/main/java/com/limelight, see android/stream/NOTICE.md).
# Both halves are GPL-3.0 (see android/stream/LICENSE-moonlight-android.txt).
set -euo pipefail

PIN="b48494cb96bff23d8886c4775cc4f39a1075495d"   # moonlight-android 12.2
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="$HERE/../stream/src/main/jni/moonlight-core"

if [ -f "$DEST/.fetched-$PIN" ]; then
  echo "moonlight-core already fetched ($PIN)"
  exit 0
fi

tmp="$(mktemp -d)"
trap "rm -rf \"$tmp\"" EXIT
git init -q "$tmp/ml"
cd "$tmp/ml"
git remote add origin https://github.com/moonlight-stream/moonlight-android.git
git fetch -q --depth 1 origin "$PIN"
git checkout -q FETCH_HEAD
git submodule update --init --recursive --depth 1 -q

rm -rf "$DEST"
mkdir -p "$(dirname "$DEST")"
cp -r app/src/main/jni/moonlight-core "$DEST"
# submodules leave .git files behind; drop them so nothing in the tree looks like a nested repository
find "$DEST" -name .git -prune -exec rm -rf {} +
touch "$DEST/.fetched-$PIN"
echo "fetched moonlight-core $PIN into $DEST"
