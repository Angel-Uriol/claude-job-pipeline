#!/usr/bin/env bash
# One-click start for macOS / Linux (run by Start.command).
#
# Installs what's missing — without admin rights — then starts the app and
# opens it in the browser:
#   1. uv     (Python package manager; also downloads Python itself)
#   2. Node   (official binary, checksum-verified, kept in .tools/node)
#   3. Python + JavaScript packages (only when the lockfiles changed)
#   4. a production build of the web UI (only when the UI code changed)
# Everything it adds lives in this folder (.tools/, .venv/, node_modules/)
# except uv, which goes to ~/.local/bin like its official installer does.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
TOOLS="$ROOT/.tools"
LOGS="$TOOLS/logs"
NODE_VERSION="v22.12.0"          # LTS; bump deliberately
BACKEND_PORT=8765
UI_PORT=8766
URL="http://localhost:$UI_PORT"
mkdir -p "$LOGS"
export PATH="$TOOLS/node/bin:$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

say()  { printf '\n\033[1;35m●\033[0m %s\n' "$*"; }
fail() { printf '\n\033[1;31m✗ %s\033[0m\n' "$*"; printf '  Details are in %s\n' "$LOGS"; exit 1; }

open_browser() {
  # JP_NO_BROWSER=1 skips this (used by automated tests of the launcher).
  if [ -n "${JP_NO_BROWSER:-}" ]; then return 0; fi
  if command -v open >/dev/null 2>&1; then open "$URL"
  elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$URL" >/dev/null 2>&1 || true
  else say "Open $URL in your browser."; fi
}

port_busy() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }

# Already running (e.g. double-clicked twice)? Just open it.
if port_busy "$BACKEND_PORT" && port_busy "$UI_PORT"; then
  say "The app is already running — opening it."
  open_browser
  exit 0
fi

# --- 1. uv -------------------------------------------------------------------
if ! command -v uv >/dev/null 2>&1; then
  say "Installing uv (Python tools) — one time only…"
  curl -LsSf https://astral.sh/uv/install.sh | sh >"$LOGS/uv-install.log" 2>&1 \
    || fail "Couldn't install uv. Check your internet connection and run Start again."
fi

# --- 2. Node -----------------------------------------------------------------
node_ok() {
  command -v node >/dev/null 2>&1 || return 1
  local major; major="$(node -v | sed -E 's/^v([0-9]+).*/\1/')"
  [ "$major" -ge 20 ]
}
if ! node_ok; then
  case "$(uname -s)" in Darwin) os=darwin ;; Linux) os=linux ;; *) fail "Unsupported system: $(uname -s)" ;; esac
  case "$(uname -m)" in arm64|aarch64) arch=arm64 ;; x86_64) arch=x64 ;; *) fail "Unsupported CPU: $(uname -m)" ;; esac
  name="node-$NODE_VERSION-$os-$arch"
  say "Downloading Node.js $NODE_VERSION — one time only…"
  tmp="$(mktemp -d)"
  base="https://nodejs.org/dist/$NODE_VERSION"
  curl -fsSL "$base/$name.tar.gz" -o "$tmp/$name.tar.gz" \
    || fail "Couldn't download Node.js. Check your internet connection and run Start again."
  curl -fsSL "$base/SHASUMS256.txt" -o "$tmp/SHASUMS256.txt" || fail "Couldn't verify Node.js."
  expected="$(grep " $name.tar.gz\$" "$tmp/SHASUMS256.txt" | awk '{print $1}')"
  actual="$(shasum -a 256 "$tmp/$name.tar.gz" 2>/dev/null | awk '{print $1}' || sha256sum "$tmp/$name.tar.gz" | awk '{print $1}')"
  [ -n "$expected" ] && [ "$expected" = "$actual" ] || fail "Node.js download didn't match its checksum — not installing it."
  rm -rf "$TOOLS/node" && mkdir -p "$TOOLS/node"
  tar -xzf "$tmp/$name.tar.gz" -C "$TOOLS/node" --strip-components=1
  rm -rf "$tmp"
  node_ok || fail "Node.js didn't install correctly."
fi

# --- 3. packages (only when lockfiles changed) --------------------------------
fingerprint() { cat "$@" 2>/dev/null | shasum -a 256 2>/dev/null | awk '{print $1}' || cat "$@" | sha256sum | awk '{print $1}'; }
deps_hash="$(fingerprint uv.lock pyproject.toml ui/package-lock.json job_pipeline/resume/node/package-lock.json)"
if [ ! -d .venv ] || [ ! -d ui/node_modules ] || [ ! -d job_pipeline/resume/node/node_modules ] \
   || [ "$(cat "$TOOLS/.deps" 2>/dev/null)" != "$deps_hash" ]; then
  say "Installing app packages — this takes a few minutes the first time…"
  uv sync >"$LOGS/uv-sync.log" 2>&1 || fail "Couldn't install the Python packages."
  (cd ui && npm ci --no-audit --no-fund >"$LOGS/npm-ui.log" 2>&1) || fail "Couldn't install the web UI packages."
  (cd job_pipeline/resume/node && npm ci --no-audit --no-fund >"$LOGS/npm-resume.log" 2>&1) \
    || fail "Couldn't install the resume tools."
  echo "$deps_hash" >"$TOOLS/.deps"
  rm -f "$TOOLS/.ui-build"
fi

# --- 4. UI build (only when the UI code changed) ------------------------------
ui_hash="$(find ui/app ui/components ui/lib ui/package.json ui/next.config.ts -type f -print0 2>/dev/null \
           | sort -z | xargs -0 cat | fingerprint /dev/stdin)"
if [ ! -f ui/.next/BUILD_ID ] || [ "$(cat "$TOOLS/.ui-build" 2>/dev/null)" != "$ui_hash" ]; then
  say "Preparing the web interface…"
  (cd ui && npm run build >"$LOGS/ui-build.log" 2>&1) || fail "Couldn't build the web interface."
  echo "$ui_hash" >"$TOOLS/.ui-build"
fi

# --- 5. start ------------------------------------------------------------------
say "Starting the app…"
uv run python -m job_pipeline.cli serve --port "$BACKEND_PORT" >"$LOGS/backend.log" 2>&1 &
backend=$!
(cd ui && exec npm run start >"$LOGS/ui.log" 2>&1) &
frontend=$!
cleanup() { kill "$backend" "$frontend" 2>/dev/null || true; pkill -P "$frontend" 2>/dev/null || true; }
trap cleanup EXIT INT TERM HUP

for _ in $(seq 1 90); do
  if port_busy "$BACKEND_PORT" && port_busy "$UI_PORT"; then break; fi
  kill -0 "$backend" 2>/dev/null || fail "The app stopped while starting."
  sleep 1
done
port_busy "$UI_PORT" || fail "The app took too long to start."

open_browser
printf '\n\033[1;32m✓ Job Pipeline is running at %s\033[0m\n' "$URL"
printf '  Leave this window open while you use the app — close it (or press Ctrl+C) to stop.\n'
wait "$backend"
