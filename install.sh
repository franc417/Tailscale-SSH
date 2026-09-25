#!/usr/bin/env bash
# tailscale-ssh installer
#
#   curl -fsSL https://raw.githubusercontent.com/franc417/Tailscale-SSH/main/install.sh | bash
#
# Installs one command (default name: mesh — override with TSSH_NAME=whatever).
# Install it under the same name on every device so the habit stays consistent,
# though the name is purely cosmetic: any install can talk to any device.
#
# If the repo is private, add a GitHub token with read access:
#   curl -fsSL https://raw.githubusercontent.com/franc417/Tailscale-SSH/main/install.sh \
#     | TSSH_TOKEN=ghp_xxx bash
# (TSSH_TOKEN or GITHUB_TOKEN env var, or an already-logged-in `gh` also work.)
set -euo pipefail

REPO="franc417/Tailscale-SSH"
REF="${TSSH_REF:-main}"
NAME="${TSSH_NAME:-mesh}"

info() { printf '\033[38;5;80m%s\033[0m\n' "$*"; }
warn() { printf '\033[38;5;215m%s\033[0m\n' "$*" >&2; }
err()  { printf '\033[38;5;203m%s\033[0m\n' "$*" >&2; exit 1; }

command -v python3 >/dev/null 2>&1 || err "python3 is required — install it first, then re-run."

detect_platform() {
  if [ -n "${TERMUX_VERSION:-}" ] || { [ -n "${PREFIX:-}" ] && printf '%s' "$PREFIX" | grep -q com.termux; }; then
    echo termux; return
  fi
  case "$(uname -s)" in
    Darwin) echo macos ;;
    Linux)
      if [ -r /etc/os-release ]; then
        # shellcheck disable=SC1091
        . /etc/os-release
        case " ${ID:-} ${ID_LIKE:-} " in
          *arch*)             echo arch ;;
          *debian*|*ubuntu*)  echo debian ;;
          *fedora*|*rhel*)    echo fedora ;;
          *)                  echo linux ;;
        esac
      else
        echo linux
      fi ;;
    *) echo linux ;;
  esac
}

fetch() {  # fetch <url> <dest> -- uses AUTH_HEADER/AUTH_HEADER_WGET if set (empty is fine: public repo)
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL "${AUTH_HEADER[@]}" "$1" -o "$2"
  elif command -v wget >/dev/null 2>&1; then
    wget -q "${AUTH_HEADER_WGET[@]}" "$1" -O "$2"
  else
    err "Need curl or wget to download the engine. On Termux: pkg install curl"
  fi
}

fetch_engine() {
  local dest="$1" token=""
  token="${TSSH_TOKEN:-${GITHUB_TOKEN:-}}"
  if [ -z "$token" ] && command -v gh >/dev/null 2>&1; then
    token="$(gh auth token 2>/dev/null || true)"
  fi
  AUTH_HEADER=(); AUTH_HEADER_WGET=()
  if [ -n "$token" ]; then
    AUTH_HEADER=(-H "Authorization: Bearer $token")
    AUTH_HEADER_WGET=(--header="Authorization: Bearer $token")
  fi
  info "Downloading tailscale-ssh engine..."
  if ! fetch "https://raw.githubusercontent.com/$REPO/$REF/tailscale_ssh.py" "$dest"; then
    if [ -z "$token" ]; then
      err "Download failed." \
          "If the repo is private, re-run with a token: curl -fsSL <url> | TSSH_TOKEN=ghp_xxx bash"
    else
      err "Download failed. Check your token and network connection."
    fi
  fi
  python3 -c "import ast,sys; ast.parse(open(sys.argv[1]).read())" "$dest" \
    || err "Downloaded file failed a sanity check — not installing it."
}

main() {
  local plat; plat="$(detect_platform)"
  info "◈ tailscale-ssh installer  ($plat)"

  local bindir libdir
  if [ "$plat" = termux ]; then
    bindir="$PREFIX/bin"; libdir="$PREFIX/share/tailscale-ssh"
  else
    bindir="$HOME/.local/bin"; libdir="$HOME/.local/share/tailscale-ssh"
  fi
  mkdir -p "$bindir" "$libdir"

  local here engine
  here="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
  engine="$libdir/tailscale_ssh.py"
  if [ -f "$here/tailscale_ssh.py" ]; then
    info "Installing from local checkout"
    cp "$here/tailscale_ssh.py" "$engine"
  else
    fetch_engine "$engine"
  fi
  chmod +x "$engine"

  cat > "$bindir/$NAME" <<WRAP
#!/usr/bin/env bash
exec env TSSH_BRAND="$NAME" python3 "$engine" "\$@"
WRAP
  chmod +x "$bindir/$NAME"
  info "Installed: $NAME  ->  $bindir/$NAME"
  if command -v "$NAME" >/dev/null 2>&1 && [ "$(command -v "$NAME")" != "$bindir/$NAME" ]; then
    warn "Heads up: another '$NAME' already exists on your PATH at $(command -v "$NAME")."
    warn "Pick a different name next time with: TSSH_NAME=something $0"
  fi

  case ":$PATH:" in
    *":$bindir:"*) : ;;
    *)
      local rc="$HOME/.bashrc"
      [ -n "${ZSH_VERSION:-}" ] || [ "${SHELL:-}" = "$(command -v zsh 2>/dev/null)" ] && rc="$HOME/.zshrc"
      printf '\nexport PATH="%s:$PATH"\n' "$bindir" >> "$rc" 2>/dev/null || true
      warn "$bindir isn't on your PATH yet — added it to $rc."
      warn "Run: export PATH=\"$bindir:\$PATH\"   (or restart your shell)"
      export PATH="$bindir:$PATH"
      ;;
  esac

  echo
  info "Next: run '$NAME' — first run walks you through Tailscale, SSH, and a key."
}

main "$@"
