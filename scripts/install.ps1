# Installs safeagents with uv: irm https://raw.githubusercontent.com/huggingface/safeagents/main/scripts/install.ps1 | iex
$ErrorActionPreference = "Stop"

function Say($message) {
    # Built from its code point so the script stays plain ASCII for Windows PowerShell.
    $hug = [char]::ConvertFromUtf32(0x1F917)
    Write-Host "$hug safeagents" -ForegroundColor Yellow -NoNewline
    Write-Host " - $message"
}

if (Get-Command uv -ErrorAction SilentlyContinue) {
    Say "found uv"
} else {
    Say "installing uv .."
    irm https://astral.sh/uv/install.ps1 | iex
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}

Say "installing safeagents .."
$ref = if ($env:SAFEAGENTS_VERSION) { "@$env:SAFEAGENTS_VERSION" } else { "" }
uv tool install --reinstall "git+https://github.com/huggingface/safeagents$ref"

Say "adding it to your PATH .."
uv tool update-shell

Say "done! Open a new terminal, then run: safeagents --help"
