# Lithify for Windows in one line of PowerShell:
#
#   irm https://raw.githubusercontent.com/TomaszKondraciuk/lithify/main/installer/get.ps1 | iex
#
# It downloads the installer (installer/install.ps1) and runs it as a file: that one is UTF-8 with
# a BOM for its Polish texts, which Windows PowerShell 5.1 cannot take through `iex`. (This file
# stays plain ASCII for the same reason.) LITHIFY_INSTALL_URL: another installer to download.
& {
    $url = if ($env:LITHIFY_INSTALL_URL) { $env:LITHIFY_INSTALL_URL } else { 'https://raw.githubusercontent.com/TomaszKondraciuk/lithify/main/installer/install.ps1' }
    $file = Join-Path ([IO.Path]::GetTempPath()) 'lithify-install.ps1'
    # (TLS 1.2 for GitHub: old Windows PowerShell 5.1 setups offer only TLS 1.0 by default)
    try { [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12 } catch { $null = $_ }
    $ProgressPreference = 'SilentlyContinue'
    try { Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $file -ErrorAction Stop }
    catch {
        Write-Host "error: could not download the Lithify installer ($url): $($_.Exception.Message)" -ForegroundColor Red
        $global:LASTEXITCODE = 1
        return
    }
    $ps = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    & $ps -NoProfile -ExecutionPolicy Bypass -File $file
    $global:LASTEXITCODE = $LASTEXITCODE
}
