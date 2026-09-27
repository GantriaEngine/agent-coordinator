param(
    [Parameter(Mandatory=$true)][string]$EvidenceRoot,
    [Parameter(Mandatory=$true)][string]$LeaseImagePath,
    [Parameter(Mandatory=$true)][string]$HookSource
)
$ErrorActionPreference = 'Stop'
$Principal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (!$Principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this installer once from an Administrator PowerShell session.'
}
$ServiceName = 'GantriaAgentCoordinatorCapture'
$InstallDirectory = Join-Path $env:ProgramFiles 'GantriaEngine\AgentCoordinatorCapture'
$DataDirectory = Join-Path $env:ProgramData 'GantriaEngine\AgentCoordinatorCapture'
$PublishDirectory = Join-Path $PSScriptRoot 'publish'
$HookSource = [IO.Path]::GetFullPath($HookSource)
if (!(Test-Path -LiteralPath (Join-Path $PublishDirectory 'AgentCoordinator.CaptureService.exe'))) {
    throw 'Published service executable is missing.'
}
if (!(Test-Path -LiteralPath $HookSource)) { throw 'Capture hook source is missing.' }
$LeaseImagePath = [IO.Path]::GetFullPath($LeaseImagePath)
if (!(Test-Path -LiteralPath $LeaseImagePath -PathType Leaf)) { throw 'Configured endpoint helper runtime is missing.' }
$EvidenceRoot = [IO.Path]::GetFullPath($EvidenceRoot)
if ([IO.Path]::IsPathRooted($EvidenceRoot) -eq $false -or $EvidenceRoot.StartsWith('\\')) {
    throw 'Evidence root must be a local absolute directory.'
}
if (Test-Path -LiteralPath $EvidenceRoot) { throw 'Dedicated evidence root already exists; inspect it and choose a fresh empty root.' }
$EvidenceParent = Split-Path -Parent $EvidenceRoot
if (!(Test-Path -LiteralPath $EvidenceParent -PathType Container)) { throw 'Evidence root parent must already exist.' }
if (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue) {
    throw 'A service with this name already exists; inspect it before replacing anything.'
}
$Identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$AuthorizedSid = $Identity.User.Value

New-Item -ItemType Directory -Path $InstallDirectory -Force | Out-Null
New-Item -ItemType Directory -Path $DataDirectory -Force | Out-Null
New-Item -ItemType Directory -Path $EvidenceRoot | Out-Null
Copy-Item -Path (Join-Path $PublishDirectory '*') -Destination $InstallDirectory -Recurse -Force
Copy-Item -LiteralPath $HookSource -Destination (Join-Path $InstallDirectory 'PktMonCapture.ps1') -Force

$InstallAcl = New-Object Security.AccessControl.DirectorySecurity
$InstallAcl.SetAccessRuleProtection($true, $false)
foreach ($Rule in @(
    (New-Object Security.AccessControl.FileSystemAccessRule('SYSTEM','FullControl','ContainerInherit,ObjectInherit','None','Allow')),
    (New-Object Security.AccessControl.FileSystemAccessRule('BUILTIN\Administrators','FullControl','ContainerInherit,ObjectInherit','None','Allow')),
    (New-Object Security.AccessControl.FileSystemAccessRule($Identity.Name,'ReadAndExecute','ContainerInherit,ObjectInherit','None','Allow'))
)) { $InstallAcl.AddAccessRule($Rule) }
Set-Acl -LiteralPath $InstallDirectory -AclObject $InstallAcl

$DataAcl = New-Object Security.AccessControl.DirectorySecurity
$DataAcl.SetAccessRuleProtection($true, $false)
foreach ($Rule in @(
    (New-Object Security.AccessControl.FileSystemAccessRule('SYSTEM','FullControl','ContainerInherit,ObjectInherit','None','Allow')),
    (New-Object Security.AccessControl.FileSystemAccessRule('BUILTIN\Administrators','FullControl','ContainerInherit,ObjectInherit','None','Allow'))
)) { $DataAcl.AddAccessRule($Rule) }
Set-Acl -LiteralPath $DataDirectory -AclObject $DataAcl

$EvidenceAcl = New-Object Security.AccessControl.DirectorySecurity
$EvidenceAcl.SetAccessRuleProtection($true, $false)
foreach ($Rule in @(
    (New-Object Security.AccessControl.FileSystemAccessRule('SYSTEM','FullControl','ContainerInherit,ObjectInherit','None','Allow')),
    (New-Object Security.AccessControl.FileSystemAccessRule('BUILTIN\Administrators','FullControl','ContainerInherit,ObjectInherit','None','Allow')),
    (New-Object Security.AccessControl.FileSystemAccessRule($Identity.Name,'Modify','ContainerInherit,ObjectInherit','None','Allow'))
)) { $EvidenceAcl.AddAccessRule($Rule) }
Set-Acl -LiteralPath $EvidenceRoot -AclObject $EvidenceAcl

$HookPath = Join-Path $InstallDirectory 'PktMonCapture.ps1'
$HookSha256 = (Get-FileHash -LiteralPath $HookPath -Algorithm SHA256).Hash.ToUpperInvariant()
$Config = [ordered]@{ AuthorizedSid=$AuthorizedSid; HookPath=$HookPath; HookSha256=$HookSha256; EvidenceRoot=$EvidenceRoot; LeaseImagePath=$LeaseImagePath }
($Config | ConvertTo-Json) | Set-Content -LiteralPath (Join-Path $DataDirectory 'service.json') -Encoding UTF8
$DataFileAcl = New-Object Security.AccessControl.FileSecurity
$DataFileAcl.SetAccessRuleProtection($true, $false)
$DataFileAcl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule('SYSTEM','FullControl','Allow')))
$DataFileAcl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule('BUILTIN\Administrators','FullControl','Allow')))
Set-Acl -LiteralPath (Join-Path $DataDirectory 'service.json') -AclObject $DataFileAcl

$Executable = Join-Path $InstallDirectory 'AgentCoordinator.CaptureService.exe'
New-Service -Name $ServiceName -DisplayName 'Gantria Agent Coordinator Capture' -Description 'Bounded Packet Monitor capture capability for the locally approved endpoint adapter.' -BinaryPathName ('"' + $Executable + '" --service') -StartupType Automatic | Out-Null
try {
    Start-Service -Name $ServiceName
    (Get-Service -Name $ServiceName).WaitForStatus('Running', [TimeSpan]::FromSeconds(15))
} catch {
    Stop-Service -Name $ServiceName -Force -ErrorAction SilentlyContinue
    sc.exe delete $ServiceName | Out-Null
    throw
}
Write-Output "Installed $ServiceName for SID $AuthorizedSid."
Write-Output "Capture hook SHA-256: $HookSha256"
Write-Output "Evidence root: $EvidenceRoot"
Write-Output 'The pipe accepts only the configured user and LocalSystem; service operations are limited to the fixed capture hook.'
