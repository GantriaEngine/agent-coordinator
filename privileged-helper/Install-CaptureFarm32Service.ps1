param(
    [Parameter(Mandatory=$true)][string]$EvidenceRoot,
    [Parameter(Mandatory=$true)][string]$LeaseImagePath,
    [Parameter(Mandatory=$true)][string]$HookSource
)
$ErrorActionPreference = 'Stop'
$ServiceName = 'GantriaAgentCoordinatorCaptureFarm32'
$InstallDirectory = Join-Path $env:ProgramFiles 'GantriaEngine\AgentCoordinatorCaptureFarm32'
$DataDirectory = Join-Path $env:ProgramData 'GantriaEngine\AgentCoordinatorCaptureFarm32'
$PublishDirectory = Join-Path $PSScriptRoot 'publish-farm32'
$ExecutableSource = Join-Path $PublishDirectory 'AgentCoordinator.CaptureFarm32Service.exe'
$Principal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (!$Principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Farm32 installation requires an Administrator PowerShell session.'
}
if (!(Test-Path -LiteralPath $ExecutableSource -PathType Leaf)) {
    throw 'The separately published Farm32 service executable is missing.'
}
$HookSource = [IO.Path]::GetFullPath($HookSource)
$LeaseImagePath = [IO.Path]::GetFullPath($LeaseImagePath)
$EvidenceRoot = [IO.Path]::GetFullPath($EvidenceRoot)
if (!(Test-Path -LiteralPath $HookSource -PathType Leaf) -or
    !(Test-Path -LiteralPath $LeaseImagePath -PathType Leaf)) {
    throw 'The fixed hook or endpoint lease runtime is missing.'
}
if (!$EvidenceRoot.StartsWith([IO.Path]::GetPathRoot($EvidenceRoot), [StringComparison]::OrdinalIgnoreCase) -or
    $EvidenceRoot.StartsWith('\\') -or $EvidenceRoot -eq [IO.Path]::GetPathRoot($EvidenceRoot)) {
    throw 'Evidence root must be a dedicated local absolute directory.'
}
if (Test-Path -LiteralPath $EvidenceRoot) { throw 'Farm32 evidence root already exists.' }
if (!(Test-Path -LiteralPath (Split-Path -Parent $EvidenceRoot) -PathType Container)) {
    throw 'Farm32 evidence parent must already exist.'
}
foreach ($PathName in @($InstallDirectory, $DataDirectory)) {
    if (Test-Path -LiteralPath $PathName) { throw "Farm32 path already exists: $PathName" }
}
if (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue) {
    throw 'Farm32 service already exists; inspect it before any update.'
}
$Identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$AuthorizedSid = $Identity.User.Value
New-Item -ItemType Directory -Path $InstallDirectory | Out-Null
New-Item -ItemType Directory -Path $DataDirectory | Out-Null
New-Item -ItemType Directory -Path $EvidenceRoot | Out-Null
Copy-Item -Path (Join-Path $PublishDirectory '*') -Destination $InstallDirectory -Recurse -Force
Copy-Item -LiteralPath $HookSource -Destination (Join-Path $InstallDirectory 'CaptureFarm32.ps1')

function Set-FixedAcl([string]$PathName, [bool]$AllowUserModify) {
    $Acl = New-Object Security.AccessControl.DirectorySecurity
    $Acl.SetAccessRuleProtection($true, $false)
    foreach ($Rule in @(
        (New-Object Security.AccessControl.FileSystemAccessRule('SYSTEM','FullControl','ContainerInherit,ObjectInherit','None','Allow')),
        (New-Object Security.AccessControl.FileSystemAccessRule('BUILTIN\Administrators','FullControl','ContainerInherit,ObjectInherit','None','Allow')),
        (New-Object Security.AccessControl.FileSystemAccessRule($Identity.Name,
            $(if ($AllowUserModify) { 'Modify' } else { 'ReadAndExecute' }),
            'ContainerInherit,ObjectInherit','None','Allow'))
    )) { $Acl.AddAccessRule($Rule) }
    Set-Acl -LiteralPath $PathName -AclObject $Acl
}
Set-FixedAcl $InstallDirectory $false
Set-FixedAcl $EvidenceRoot $true
$DataAcl = New-Object Security.AccessControl.DirectorySecurity
$DataAcl.SetAccessRuleProtection($true, $false)
foreach ($Rule in @(
    (New-Object Security.AccessControl.FileSystemAccessRule('SYSTEM','FullControl','ContainerInherit,ObjectInherit','None','Allow')),
    (New-Object Security.AccessControl.FileSystemAccessRule('BUILTIN\Administrators','FullControl','ContainerInherit,ObjectInherit','None','Allow'))
)) { $DataAcl.AddAccessRule($Rule) }
Set-Acl -LiteralPath $DataDirectory -AclObject $DataAcl

$HookPath = Join-Path $InstallDirectory 'CaptureFarm32.ps1'
$HookSha256 = (Get-FileHash -LiteralPath $HookPath -Algorithm SHA256).Hash.ToUpperInvariant()
$Config = [ordered]@{
    AuthorizedSid = $AuthorizedSid
    HookPath = $HookPath
    HookSha256 = $HookSha256
    EvidenceRoot = $EvidenceRoot
    LeaseImagePath = $LeaseImagePath
}
$ConfigPath = Join-Path $DataDirectory 'service.json'
($Config | ConvertTo-Json) | Set-Content -LiteralPath $ConfigPath -Encoding UTF8
$ConfigAcl = New-Object Security.AccessControl.FileSecurity
$ConfigAcl.SetAccessRuleProtection($true, $false)
$ConfigAcl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule('SYSTEM','FullControl','Allow')))
$ConfigAcl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule('BUILTIN\Administrators','FullControl','Allow')))
Set-Acl -LiteralPath $ConfigPath -AclObject $ConfigAcl

$Executable = Join-Path $InstallDirectory 'AgentCoordinator.CaptureFarm32Service.exe'
New-Service -Name $ServiceName -DisplayName 'Gantria Agent Coordinator Farm32 Capture' -Description 'Fixed 600-second capture capability for the locally approved 32-client campaign.' -BinaryPathName ('"' + $Executable + '" --service') -StartupType Manual | Out-Null
try {
    Start-Service -Name $ServiceName
    (Get-Service -Name $ServiceName).WaitForStatus('Running', [TimeSpan]::FromSeconds(15))
} catch {
    Stop-Service -Name $ServiceName -Force -ErrorAction SilentlyContinue
    sc.exe delete $ServiceName | Out-Null
    throw
}
Write-Output "[Coordinator:CaptureFarm32] Service=$ServiceName HookSha256=$HookSha256 EvidenceRoot=$EvidenceRoot"
