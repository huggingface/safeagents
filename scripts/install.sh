#!/bin/sh
# Installs safeagents with uv: curl -LsSf https://raw.githubusercontent.com/huggingface/safeagents/main/scripts/install.sh | sh
set -eu

say() {
    printf '\033[1;33m🤗 safeagents\033[0m - %s\n' "$1"
}

main() {
    if command -v uv >/dev/null 2>&1; then
        uv=uv
        say "found uv"
    else
        say "installing uv .."
        curl -LsSf https://astral.sh/uv/install.sh | sh
        uv="$HOME/.local/bin/uv"
    fi

    say "installing safeagents .."
    "$uv" tool install --reinstall "git+https://github.com/huggingface/safeagents${SAFEAGENTS_VERSION:+@$SAFEAGENTS_VERSION}"

    say "adding it to your PATH .."
    "$uv" tool update-shell

    say "done! Open a new terminal, then run: safeagents --help"
}

main
