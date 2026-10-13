param(
    [Parameter(Mandatory=$true)]
    [ValidateSet('prepare','start','stop','status','audit')]
    [string]$Action,
    [string]$RemoteProject = '/home/leo/projects/robot-playground',
    [switch]$Demo
)
$ErrorActionPreference = 'Stop'
if ($Action -eq 'audit') {
    & powershell -NoProfile -ExecutionPolicy Bypass -File 'C:/Users/Leo/.codex/skills/n-gb10/scripts/Test-NGb10Host.ps1'
    exit $LASTEXITCODE
}
# Restrict the one operator-specified project path before embedding shell text.
if ($RemoteProject -notmatch '^/home/leo/[A-Za-z0-9/_. -]+$' -or $RemoteProject.Contains('..')) {
    throw 'RemoteProject must be an absolute project path under /home/leo without shell metacharacters or parent traversal.'
}
$identityFile = Join-Path $env:LOCALAPPDATA 'NVIDIA Corporation/Sync/config/nvsync.key'
if (-not (Test-Path -LiteralPath $identityFile -PathType Leaf)) { throw 'NVIDIA Sync SSH key is unavailable.' }
$scriptName = "spark-$Action.sh"
$remoteCommand = "bash '$RemoteProject/scripts/$scriptName'"
if ($Demo) {
    if ($Action -ne 'start') { throw '-Demo only applies to start.' }
    $remoteCommand += ' --demo'
}
& ssh -i $identityFile -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=yes 'leo@gx10-ddb2.local' $remoteCommand
if ($LASTEXITCODE -ne 0) { throw "Spark $Action returned exit code $LASTEXITCODE." }
