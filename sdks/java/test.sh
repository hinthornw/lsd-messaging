#!/bin/sh
set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname "$0")" && pwd)"
BUILD_DIR="$SCRIPT_DIR/build"
MAIN_DIR="$BUILD_DIR/classes/main"
TEST_DIR="$BUILD_DIR/classes/test"
SOURCES_FILE="$BUILD_DIR/test-sources.txt"

sh "$SCRIPT_DIR/build.sh"

mkdir -p "$TEST_DIR"
find "$SCRIPT_DIR/src/test/java" -name '*.java' | sort >"$SOURCES_FILE"

javac \
  --release 21 \
  --enable-preview \
  --add-modules jdk.httpserver \
  -cp "$MAIN_DIR" \
  -d "$TEST_DIR" \
  @"$SOURCES_FILE"

java \
  --enable-preview \
  --enable-native-access=ALL-UNNAMED \
  --add-modules jdk.httpserver \
  -cp "$MAIN_DIR:$TEST_DIR" \
  dev.lsmsg.TestMain
