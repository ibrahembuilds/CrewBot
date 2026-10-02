param([Parameter(ValueFromRemainingArguments = $true)][string[]]$ScoutArguments)
$ErrorActionPreference = 'Stop'
if (-not $env:OPENAI_API_KEY) {
    $scoutSecureKey = Read-Host 'Enter OPENAI_API_KEY (hidden; kept in this terminal process)' -AsSecureString
    $scoutKeyPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($scoutSecureKey)
    try {
        $env:OPENAI_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($scoutKeyPointer)
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($scoutKeyPointer)
        $scoutSecureKey.Dispose()
    }
}
if (-not $ScoutArguments) { $ScoutArguments = @('run') }
& python (Join-Path $PSScriptRoot 'scout.py') @ScoutArguments
exit $LASTEXITCODE
