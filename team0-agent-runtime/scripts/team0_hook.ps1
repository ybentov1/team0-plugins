param(
    [Parameter(Mandatory = $true)]
    [string]$RuntimeCommand
)

$HookScript = Join-Path $PSScriptRoot "team0_hook.py"
$PythonLauncher = Get-Command py -ErrorAction SilentlyContinue
if ($PythonLauncher) {
    & py -3 $HookScript $RuntimeCommand
    exit $LASTEXITCODE
}

$Python = Get-Command python -ErrorAction SilentlyContinue
if ($Python) {
    & python $HookScript $RuntimeCommand
    exit $LASTEXITCODE
}

Write-Output '{"systemMessage":"Team0 could not start on this device. Reopen the Team0 connection guide for the one remaining setup step."}'
exit 0
