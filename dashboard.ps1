param([switch]$WithoutKey, [int]$Port = 8765, [switch]$NoBrowser, [switch]$PromptOpenAI)
$ErrorActionPreference = 'Stop'
if ($PromptOpenAI -and -not $WithoutKey -and -not $env:OPENAI_API_KEY) {
    $scoutSecureKey = Read-Host 'Enter OPENAI_API_KEY (hidden; kept in this terminal process)' -AsSecureString
    $scoutKeyPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($scoutSecureKey)
    try {
        $env:OPENAI_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($scoutKeyPointer)
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($scoutKeyPointer)
        $scoutSecureKey.Dispose()
    }
}
$scoutUrl = "http://127.0.0.1:$Port"
$scoutExisting = $null
try { $scoutExisting = Invoke-RestMethod "$scoutUrl/api/state" -TimeoutSec 2 } catch { }
if ($scoutExisting -and $scoutExisting.app -eq 'crewbot-workspace') {
    if ($scoutExisting.employees | Where-Object { $_.busy }) {
        throw 'The CrewBot workspace has active work. Finish or stop those tasks before restarting.'
    }
    Invoke-RestMethod "$scoutUrl/api/shutdown" -Method Post -ContentType 'application/json' -Body '{}' -Headers @{ 'X-Scout-Token' = $scoutExisting.token } -TimeoutSec 5 | Out-Null
    Start-Sleep -Milliseconds 750
}
$scoutArguments = @((Join-Path $PSScriptRoot 'company_dashboard.py'), '--port', $Port)
if (-not $NoBrowser) { $scoutArguments += '--open' }
& python @scoutArguments
exit $LASTEXITCODE
