# Run in an elevated PowerShell after creating a dedicated non-admin service account.
param([Parameter(Mandatory=$true)][string]$ServiceAccount,
      [string]$DataDirectory = (Join-Path $PSScriptRoot 'state\data'))
$ErrorActionPreference = 'Stop'
$account = New-Object System.Security.Principal.NTAccount($ServiceAccount)
$sid = $account.Translate([System.Security.Principal.SecurityIdentifier])
$dir = [System.IO.Directory]::CreateDirectory($DataDirectory)
$acl = New-Object System.Security.AccessControl.DirectorySecurity
$acl.SetAccessRuleProtection($true,$false)
foreach ($entry in @(@($sid,'Modify'),@('S-1-5-32-544','FullControl'),@('S-1-5-18','FullControl'))) {
    $principal = [System.Security.Principal.SecurityIdentifier]$entry[0]
    $rule = New-Object System.Security.AccessControl.FileSystemAccessRule($principal,$entry[1],'ContainerInherit,ObjectInherit','None','Allow')
    $acl.AddAccessRule($rule)
}
Set-Acl -LiteralPath $dir.FullName -AclObject $acl
Get-Acl -LiteralPath $dir.FullName | Format-List Path,AccessToString

