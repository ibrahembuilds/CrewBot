param([switch]$Slack, [switch]$GitHub, [int]$Port = 8765, [switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$crewbotCredentials = @()
if ($Slack) { $crewbotCredentials += 'SLACK_BOT_TOKEN' }
if ($GitHub) { $crewbotCredentials += 'GITHUB_TOKEN' }
foreach ($crewbotVariable in $crewbotCredentials) {
    if (-not [Environment]::GetEnvironmentVariable($crewbotVariable, 'Process')) {
        $crewbotSecret = Read-Host "Enter $crewbotVariable (hidden; kept in this process)" -AsSecureString
        $crewbotPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($crewbotSecret)
        try {
            [Environment]::SetEnvironmentVariable($crewbotVariable, [Runtime.InteropServices.Marshal]::PtrToStringBSTR($crewbotPointer), 'Process')
        } finally {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($crewbotPointer)
            $crewbotSecret.Dispose()
        }
    }
}
& (Join-Path $PSScriptRoot 'dashboard.ps1') -Port $Port -NoBrowser:$NoBrowser
exit $LASTEXITCODE
