#!/bin/sh
set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname "$0")" && pwd)"
ROOT_DIR="$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)"
BUILD_DIR="$SCRIPT_DIR/build"
MAIN_DIR="$BUILD_DIR/classes/main"
SOURCES_FILE="$BUILD_DIR/main-sources.txt"

rm -rf "$BUILD_DIR"
mkdir -p "$MAIN_DIR"

cargo build --release -p lsmsg-ffi >/dev/null

find "$SCRIPT_DIR/src/main/java" -name '*.java' | sort >"$SOURCES_FILE"

javac \
  --release 21 \
  --enable-preview \
  -d "$MAIN_DIR" \
  @"$SOURCES_FILE"

