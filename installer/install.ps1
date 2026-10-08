# Lithify installer for Windows:
#
#   irm https://raw.githubusercontent.com/OWNER/lithify/main/installer/get.ps1 | iex
#   .\installer\install.ps1           (from a checkout: uses it in place)
#   Lithify-Windows.cmd               (the same with a double-click)
#
# (This file is UTF-8 with a BOM: Windows PowerShell 5.1 reads its Polish texts right only so. It
# is therefore always run as a file, never pasted: get.ps1 downloads it for the one-line way.)
#
# It gets this computer ready, and asks once before it installs anything:
#   1. Lithify itself: with git when it is there, otherwise from GitHub's zip archive;
#   2. Python 3.11 or newer: one that is installed, else Python 3.12 with winget (for this user),
#      else a private Python 3.12 for Lithify only (installed by uv); no administrator rights;
#   3. the `lithify` command;
#   4. in one step that Windows asks permission for once: Git, Docker Desktop and WSL 2, which
#      build the speaker's software (not needed when a prebuilt bundle is there or published), and
#      the firewall rule that lets the speaker download it (TCP 8095 and 18096-18099). When WSL
#      needs a restart, Windows restarts (after asking) and this installer goes on by itself.
# Then it opens the Lithify wizard in the web browser: find the speaker, choose its name, install.
# Over SSH, or with LITHIFY_HOST, it does the same in this window: `lithify install --reboot`,
# then `lithify serve --install-service`.
#
# Environment: LITHIFY_YES=1 (yes to every question but the restart), LITHIFY_LANG (pl or en: the
#              language of the messages; default: Windows' display language), LITHIFY_HOST (the
#              speaker's address: no search, no wizard), LITHIFY_NAME (its name in Spotify),
#              LITHIFY_NO_WIZARD=1 (this window instead of the browser), LITHIFY_NO_SETUP=1 (only
#              steps 1-3), LITHIFY_HOME (install dir), LITHIFY_REPO (git URL), LITHIFY_ARCHIVE_URL
#              (the .zip to use instead of git).

& {
    # Native programs report through their exit codes ($LASTEXITCODE), checked after each one:
    # with 'Stop', Windows PowerShell 5.1 would end the script on any line a program writes to stderr.
    $ErrorActionPreference = 'Continue'
    # Programs this window runs write UTF-8 (winget's progress bar, names with diacritics): read
    # them as such, not in the console's OEM code page (852 on a Polish Windows: garbled bars).
    try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false } catch { $null = $_ }
    $Repo = if ($env:LITHIFY_REPO) { $env:LITHIFY_REPO } else { 'https://github.com/OWNER/lithify.git' }
    $Root = Join-Path $env:LOCALAPPDATA 'lithify'
    $Dest = if ($env:LITHIFY_HOME) { $env:LITHIFY_HOME } else { Join-Path $Root 'app' }
    $Bin = Join-Path $Root 'bin'
    # uv and the private Python, when this computer has no suitable Python (never inside $Dest,
    # which may be a git checkout that has to stay clean)
    $Tools = Join-Path $Root 'tools'
    # where `lithify build` keeps the bundle (hostos.cache_dir)
    $Cache = if ($env:LITHIFY_CACHE) { $env:LITHIFY_CACHE } else { Join-Path $Root 'cache' }
    $UvReleases = 'https://github.com/astral-sh/uv/releases/latest/download'
    # The rule that lets speakers on the local network download from this computer: the same as
    # lithify/hostos.py's (FIREWALL_RULE, firewall_rule_command), which asks for it when it is missing.
    $FirewallRule = 'Lithify'
    $FirewallPorts = '8095,18096-18099'
    $FirewallArgs = @('advfirewall', 'firewall', 'add', 'rule', "name=$FirewallRule", 'dir=in', 'action=allow',
                      'protocol=TCP', "localport=$FirewallPorts", 'remoteip=localsubnet', 'profile=any')
    # winget's answers (https://github.com/microsoft/winget-cli/blob/master/doc/windows/package-manager/winget/returnCodes.md)
    $WingetRebootToFinish = -1978334967
    $WingetRebootFirst = -1978334966
    $WingetRebootInitiated = -1978334965
    $WingetCancelled = -1978334964
    $WingetAlreadyInstalled = -1978335135
    # The exit code when Windows restarts to go on (ERROR_SUCCESS_REBOOT_REQUIRED)
    $RestartCode = 3010
    $State = @{ Restarting = $false }

    # The messages in Polish when Windows is shown in Polish (or LITHIFY_LANG=pl), else in English.
    $Lang = "$env:LITHIFY_LANG".ToLowerInvariant()
    if ($Lang -notin @('pl', 'en')) {
        $Lang = 'en'
        try { if ((Get-UICulture).Name -like 'pl*') { $Lang = 'pl' } } catch { $null = $_ }
    }
    # L 'English', 'Polski': the text in the language of the messages (one array: it may go on on the
    # next line after the comma).
    function L([string[]]$t) { if ($Lang -eq 'pl') { return $t[1] } return $t[0] }
    $PortsText = L 'TCP 8095 and 18096-18099', 'TCP 8095 i 18096-18099'

    function Say([string]$m) { Write-Host "==> $m" }
    function Info([string]$m) { Write-Host "    $m" }
    function Warn([string]$m) { Write-Host ((L 'warning: ', 'uwaga: ') + $m) -ForegroundColor Yellow }
    # A failure: what went wrong, then what to do next (a line each).
    function Fail([string]$m, [string[]]$next = @()) { throw ((@((L 'error: ', 'błąd: ') + $m) + $next) -join "`n") }
    # No failure, but the install cannot go on yet (a restart, a program to start first): why,
    # and what to do then.
    function Later([string]$m, [string[]]$next = @()) { throw ((@("==> $m") + $next) -join "`n") }
    # A path from its parts, with this system's separator.
    function P { return [IO.Path]::Combine([string[]]@($args | ForEach-Object { "$_" })) }
    # A text as a PowerShell literal (for the script that runs with administrator rights)
    function Q([string]$s) { return "'" + $s.Replace("'", "''") + "'" }

    # The Program Files folders: 64-bit first (a 32-bit PowerShell's ProgramFiles is the "(x86)" one).
    function Get-ProgramDirs { return @(@($env:ProgramW6432, $env:ProgramFiles) | Where-Object { $_ } | Select-Object -Unique) }

    # PATH as a new window would see it (after an installer changed it), with the command's folder.
    function Update-Path {
        $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' +
                    [Environment]::GetEnvironmentVariable('Path', 'User')
        if (($env:Path -split ';') -notcontains $Bin) { $env:Path += ";$Bin" }
    }

    function Agree([string]$question) {
        if ($env:LITHIFY_YES -eq '1') { Info ("$question " + (L 'yes (LITHIFY_YES=1)', 'tak (LITHIFY_YES=1)')); return $true }
        $a = Read-Host ("    $question " + (L '[Y/n]', '[T/n]'))
        return ($a -eq '' -or $a -match '^[YyTt]')
    }

    # winget as this user (Python for this user: no administrator rights). Its output goes straight
    # to this window, where its progress bar draws in place (through the pipeline each frame of it
    # would be a line of its own). Returns winget's exit code.
    function Invoke-Winget([string]$id, [string[]]$extra = @()) {
        $wingetArgs = @('install', '-e', '--id', $id, '--source', 'winget', '--silent', '--accept-package-agreements',
                        '--accept-source-agreements') + $extra
        $p = Start-Process -FilePath (Get-Command winget).Source -ArgumentList $wingetArgs -NoNewWindow -PassThru
        $null = $p.Handle  # (keeps the exit code readable once it ends)
        $p.WaitForExit()
        Update-Path
        return $p.ExitCode
    }

    # The machine's processor: a 32-bit (or, on Windows on Arm, an emulated x64) PowerShell sees
    # its own in PROCESSOR_ARCHITECTURE.
    function Get-Arch {
        $a = if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { $env:PROCESSOR_ARCHITECTURE }
        try { if ("$([Runtime.InteropServices.RuntimeInformation]::OSArchitecture)" -eq 'Arm64') { $a = 'ARM64' } } catch { $a = "$a" }  # (an older .NET: PROCESSOR_ARCHITECTURE it is)
        switch ($a) {
            'ARM64' { return 'aarch64' }
            'x86' { return 'i686' }
            default { return 'x86_64' }
        }
    }

    function New-TempDir {
        $d = Join-Path ([IO.Path]::GetTempPath()) ('lithify-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
        New-Item -ItemType Directory -Force -Path $d -ErrorAction Stop | Out-Null
        return $d
    }

    function Get-Url([string]$url, [string]$file) {
        # (Windows PowerShell 5.1 downloads many times slower while it draws its progress bar)
        $ProgressPreference = 'SilentlyContinue'
        try { Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $file -ErrorAction Stop }
        catch {
            Fail (L "could not download $url", "nie udało się pobrać $url") @("$($_.Exception.Message)",
                (L 'check the internet connection, then run this again', 'sprawdź połączenie z internetem i uruchom to ponownie'))
        }
    }

    # The files byte for byte as they are in the archive (LF line endings: the speaker runs some).
    function Expand-Zip([string]$zip, [string]$to) {
        $ProgressPreference = 'SilentlyContinue'
        try { Expand-Archive -LiteralPath $zip -DestinationPath $to -Force -ErrorAction Stop }
        catch { Fail (L "cannot unpack $zip", "nie można rozpakować $zip") @("$($_.Exception.Message)") }
    }

    # ---- Python ------------------------------------------------------------

    # The Python's own path when it is 3.11 or newer (the "python" of the Microsoft Store is only a
    # link to the Store).
    function Test-Python([string]$exe, [string[]]$pre = @()) {
        try {
            $out = & $exe @pre -c "import sys; print(sys.executable); print(int(sys.version_info >= (3, 11)))" 2>$null
        } catch { return $null }
        if ($LASTEXITCODE -eq 0 -and $out.Count -ge 2 -and $out[1] -eq '1') { return $out[0] }
        return $null
    }

    function Find-Python {
        $candidates = @(@{ exe = 'py'; args = @('-3') }, @{ exe = 'python'; args = @() }, @{ exe = 'python3'; args = @() })
        foreach ($c in $candidates) {
            if (-not (Get-Command $c.exe -ErrorAction SilentlyContinue)) { continue }
            $p = Test-Python $c.exe $c.args
            if ($p) { return $p }
        }
        # python.org's installer (for this user) puts it here, also before this window's PATH knows it
        $dirs = @(Get-ChildItem -LiteralPath (P $env:LOCALAPPDATA 'Programs' 'Python') -Directory -ErrorAction SilentlyContinue |
                  Where-Object { $_.Name -like 'Python3*' } | Sort-Object Name -Descending)
        foreach ($d in $dirs) {
            $exe = P $d.FullName 'python.exe'
            if (Test-Path -LiteralPath $exe) {
                $p = Test-Python $exe
                if ($p) { return $p }
            }
        }
        return $null
    }

    # uv with Lithify's own folders, and none of the user's uv settings or virtualenv.
    function Invoke-Uv([string[]]$uvArgs, [switch]$Quiet) {
        $set = [ordered]@{
            UV_PYTHON_INSTALL_DIR = (P $Tools 'python'); UV_CACHE_DIR = (P $Tools 'cache'); UV_NO_CONFIG = '1'
            VIRTUAL_ENV = $null; CONDA_PREFIX = $null; UV_PYTHON = $null; UV_OFFLINE = $null; UV_PYTHON_DOWNLOADS = $null
            UV_MANAGED_PYTHON = $null; UV_NO_MANAGED_PYTHON = $null; UV_PYTHON_PREFERENCE = $null; UV_SYSTEM_PYTHON = $null
        }
        $saved = @{}
        foreach ($k in @($set.Keys)) {
            $saved[$k] = [Environment]::GetEnvironmentVariable($k, 'Process')
            [Environment]::SetEnvironmentVariable($k, $set[$k], 'Process')
        }
        try {
            if ($Quiet) { & (P $Tools 'uv.exe') @uvArgs 2>$null } else { & (P $Tools 'uv.exe') @uvArgs }
        } finally {
            foreach ($k in @($set.Keys)) { [Environment]::SetEnvironmentVariable($k, $saved[$k], 'Process') }
        }
    }

    # Lithify's private Python, from an earlier run.
    function Find-PrivatePython {
        if (-not (Test-Path -LiteralPath (P $Tools 'uv.exe'))) { return $null }
        $p = Invoke-Uv @('python', 'find', '--managed-python', '--no-project', '3.12') -Quiet | Select-Object -Last 1
        if ($LASTEXITCODE -ne 0 -or -not $p) { return $null }
        return (Test-Python $p)
    }

    function Install-PrivatePython {
        $uv = P $Tools 'uv.exe'
        if (-not (Test-Path -LiteralPath $uv)) {
            $target = "$(Get-Arch)-pc-windows-msvc"
            Say (L "downloading uv (Astral's Python installer, about 20 MB) for $target", "pobieranie uv (instalatora Pythona od Astral, ok. 20 MB) dla $target")
            $tmp = New-TempDir
            try {
                $zip = P $tmp "uv-$target.zip"
                for ($attempt = 1; $attempt -le 2; $attempt++) {
                    Get-Url "$UvReleases/uv-$target.zip" $zip
                    Get-Url "$UvReleases/uv-$target.zip.sha256" "$zip.sha256"
                    $want = @(([IO.File]::ReadAllText("$zip.sha256")).Trim() -split '\s+')[0]
                    $got = (Get-FileHash -Algorithm SHA256 -LiteralPath $zip).Hash
                    if ($want -and $want -eq $got) { break }
                    if ($attempt -eq 2) {
                        Fail (L 'the uv download does not match its checksum', 'pobrany uv nie zgadza się z sumą kontrolną') @(
                            (L 'run this again in a few minutes (a new uv release may have come out meanwhile)',
                               'uruchom to ponownie za kilka minut (mogła właśnie wyjść nowa wersja uv)'))
                    }
                    Info (L 'the download does not match its checksum: once more', 'pobrany plik nie zgadza się z sumą kontrolną: jeszcze raz')
                }
                Info (L 'checksum ok', 'suma kontrolna się zgadza')
                Expand-Zip $zip (P $tmp 'x')
                if (-not (Test-Path -LiteralPath (P $tmp 'x' 'uv.exe'))) { Fail (L "uv-$target.zip holds no uv.exe", "w uv-$target.zip nie ma uv.exe") }
                New-Item -ItemType Directory -Force -Path $Tools -ErrorAction Stop | Out-Null
                Copy-Item -LiteralPath (P $tmp 'x' 'uv.exe') -Destination $uv -Force -ErrorAction Stop
            } finally {
                Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
            }
        }
        Say (L 'installing Python 3.12 (uv python install 3.12)', 'instalowanie Pythona 3.12 (uv python install 3.12)')
        Invoke-Uv @('python', 'install', '--no-bin', '--no-registry', '3.12') | Out-Host
        if ($LASTEXITCODE -ne 0) {
            Fail (L 'uv could not install Python 3.12 (see above)', 'uv nie zainstalował Pythona 3.12 (szczegóły powyżej)') @(
                (L 'check the internet connection, then run this again', 'sprawdź połączenie z internetem i uruchom to ponownie'))
        }
        $p = Find-PrivatePython
        if (-not $p) {
            Fail (L 'uv installed Python 3.12, but it does not start', 'uv zainstalował Pythona 3.12, ale ten się nie uruchamia') @(
                (L "remove $Tools, then run this again", "usuń $Tools i uruchom to ponownie"))
        }
        # (uv's download cache: not needed any more)
        Remove-Item -LiteralPath (P $Tools 'cache') -Recurse -Force -ErrorAction SilentlyContinue
        return $p
    }

    # A Python this computer has, or $null.
    function Get-Python {
        $py = Find-Python
        if ($py) { Say "Python: $py"; return $py }
        $py = Find-PrivatePython
        if ($py) { Say (L "Python: $py (Lithify's private Python)", "Python: $py (prywatny Python Lithify)"); return $py }
        return $null
    }

    # Python for this user (the person agreed to it already): python.org's with winget, else a
    # private one for Lithify only. Neither needs administrator rights.
    function Install-Python {
        if (Get-Command winget -ErrorAction SilentlyContinue) {
            Say (L 'installing Python 3.12 from python.org (winget, for this user)', 'instalowanie Pythona 3.12 z python.org (winget, dla tego użytkownika)')
            $rc = Invoke-Winget 'Python.Python.3.12' @('--scope', 'user')
            $py = Find-Python
            if ($py) { Say "Python: $py"; return $py }
            Warn (L "winget did not give this window a working Python (code $rc): Lithify installs a private one in $Tools",
                    "winget nie dał temu oknu działającego Pythona (kod $rc): Lithify zainstaluje własnego w $Tools")
        }
        $py = Install-PrivatePython | Select-Object -Last 1
        Say (L "Python: $py (Lithify's private Python)", "Python: $py (prywatny Python Lithify)")
        return $py
    }

    # ---- Lithify itself ----------------------------------------------------

    function Test-LithifyTree([string]$dir) {
        if (-not $dir) { return $false }
        return ((Test-Path -LiteralPath (P $dir 'lithify' 'cli.py')) -and (Test-Path -LiteralPath (P $dir 'bin' 'lithify')))
    }

    # Git on PATH, or where its installer puts it before this window's PATH knows it.
    function Find-Git {
        if (Get-Command git -ErrorAction SilentlyContinue) { return $true }
        $dirs = @(Get-ProgramDirs | ForEach-Object { P $_ 'Git' 'cmd' }) + @(P $env:LOCALAPPDATA 'Programs' 'Git' 'cmd')
        foreach ($d in $dirs) {
            if ([IO.Path]::IsPathRooted($d) -and (Test-Path -LiteralPath (P $d 'git.exe'))) {
                $env:Path = "$d;$env:Path"
                return $true
            }
        }
        return $false
    }

    # GitHub's zip of the main branch, for computers without git.
    function Get-ArchiveUrl {
        if ($env:LITHIFY_ARCHIVE_URL) { return $env:LITHIFY_ARCHIVE_URL }
        $u = $Repo.TrimEnd('/')
        if ($u.EndsWith('.git')) { $u = $u.Substring(0, $u.Length - 4) }
        if ($u -match '^git@github\.com:(.+)$') { $u = "https://github.com/$($Matches[1])" }
        if ($u -match '^https://github\.com/[^/]+/[^/]+$') { return "$u/archive/refs/heads/main.zip" }
        return $null
    }

    # $to becomes a copy of $from (robocopy /MIR), also while the companion runs from $to: Windows
    # lets nobody rename the folder a program is working in.
    function Sync-Tree([string]$from, [string]$to) {
        # (no trailing backslash: before a closing quote it would escape it)
        $from = $from.TrimEnd('\', '/'); $to = $to.TrimEnd('\', '/')
        & robocopy $from $to /MIR /NFL /NDL /NJH /NJS /NP /R:2 /W:1 | Out-Null
        if ($LASTEXITCODE -ge 8) {
            Fail (L "cannot copy Lithify into $to (robocopy: $LASTEXITCODE)", "nie można skopiować Lithify do $to (robocopy: $LASTEXITCODE)") @(
                (L 'close what may use files there (lithify serve), then run this again',
                   'zamknij to, co może używać plików w tym folderze (lithify serve), i uruchom to ponownie'))
        }
    }

    function Expand-LithifyArchive([string]$url, [string]$into) {
        $zip = P $into 'lithify.zip'
        Get-Url $url $zip
        $x = P $into 'x'
        Expand-Zip $zip $x
        # GitHub's archives hold one folder (lithify-main\); a zip of the files themselves works too
        if (Test-LithifyTree $x) { return $x }
        foreach ($d in @(Get-ChildItem -LiteralPath $x -Directory -ErrorAction SilentlyContinue)) {
            if (Test-LithifyTree $d.FullName) { return $d.FullName }
        }
        Fail (L "$url does not hold Lithify", "$url nie zawiera Lithify") @(
            (L 'set LITHIFY_ARCHIVE_URL to a .zip of Lithify, or install Git, then run this again',
               'ustaw LITHIFY_ARCHIVE_URL na plik .zip z Lithify albo zainstaluj Git, i uruchom to ponownie'))
    }

    function Get-Lithify {
        # The Lithify folder this file is in (installer\ is in it); none when it was downloaded alone
        $here = if ($PSScriptRoot) { Split-Path -Parent $PSScriptRoot } else { '' }
        $copyFrom = $null
        if (Test-LithifyTree $here) {
            # A checkout run directly is used where it is; a downloaded folder (not a git checkout)
            # that Lithify-Windows.cmd runs is copied into $Dest, so it can be deleted afterwards.
            if ((Test-Path -LiteralPath (P $here '.git')) -or $env:LITHIFY_LAUNCHER -ne '1') {
                Say (L "using this checkout: $here", "używam tego folderu Lithify: $here")
                return $here
            }
            $copyFrom = $here
        }
        $git = Find-Git
        if (Test-Path -LiteralPath (P $Dest '.git')) {
            if (-not $git) {
                Fail (L "$Dest is a git checkout, but Git is not installed", "$Dest to kopia z git, ale Git nie jest zainstalowany") @(
                    (L "install Git (https://git-scm.com/download/win), or remove $Dest to get a fresh copy, then run this again",
                       "zainstaluj Git (https://git-scm.com/download/win) albo usuń $Dest, żeby pobrać świeżą kopię, i uruchom to ponownie"))
            }
            Say (L "updating Lithify in $Dest (git pull)", "aktualizowanie Lithify w $Dest (git pull)")
            git -C $Dest pull --ff-only -q
            if ($LASTEXITCODE -ne 0) {
                Fail (L "git could not update $Dest (see above)", "git nie zaktualizował $Dest (szczegóły powyżej)") @(
                    (L 'check the internet connection; if files there were changed, commit or undo the changes; then run this again',
                       'sprawdź połączenie z internetem; jeśli w tym folderze zmieniono pliki, zatwierdź albo cofnij zmiany; potem uruchom to ponownie'))
            }
            return $Dest
        }
        if ((Test-Path -LiteralPath $Dest) -and -not (Test-LithifyTree $Dest) -and
            (Get-ChildItem -LiteralPath $Dest -Force -ErrorAction SilentlyContinue | Select-Object -First 1)) {
            Fail (L "$Dest is not empty, and it is not Lithify", "$Dest nie jest pusty i nie jest folderem Lithify") @(
                (L 'set LITHIFY_HOME to another folder (or empty this one), then run this again',
                   'ustaw LITHIFY_HOME na inny folder (albo opróżnij ten) i uruchom to ponownie'))
        }
        if ($copyFrom) {
            Say (L "copying Lithify from $copyFrom to $Dest", "kopiowanie Lithify z $copyFrom do $Dest")
            Sync-Tree $copyFrom $Dest
            return $Dest
        }
        $stage = New-TempDir
        try {
            $new = $null
            if ($git -and -not $env:LITHIFY_ARCHIVE_URL) {  # (an archive named is the one to use)
                Say (L "downloading Lithify with git into $Dest", "pobieranie Lithify przez git do $Dest")
                # LF line endings exactly as published: the speaker runs some of these files.
                git clone -q --depth 1 --config core.autocrlf=false $Repo (P $stage 'lithify')
                if ($LASTEXITCODE -eq 0) { $new = P $stage 'lithify' } else { Warn (L "git could not download $Repo", "git nie pobrał $Repo") }
            }
            if (-not $new) {
                $url = Get-ArchiveUrl
                if (-not $url) {
                    Fail (L "Git is not installed, and $Repo is not a GitHub address to download a zip from",
                            "Git nie jest zainstalowany, a $Repo to nie adres GitHuba, z którego da się pobrać ZIP") @(
                        (L 'install Git, or set LITHIFY_ARCHIVE_URL to a .zip of Lithify, then run this again',
                           'zainstaluj Git albo ustaw LITHIFY_ARCHIVE_URL na plik .zip z Lithify, i uruchom to ponownie'))
                }
                Say (L "downloading Lithify into $Dest ($url)", "pobieranie Lithify do $Dest ($url)")
                $new = Expand-LithifyArchive $url $stage
            }
            Sync-Tree $new $Dest
        } finally {
            Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue
        }
        return $Dest
    }

    # The `lithify` command: a small .cmd that runs this Python on the checkout.
    function Install-Command([string]$py, [string]$dest) {
        New-Item -ItemType Directory -Force -Path $Bin -ErrorAction Stop | Out-Null
        $shim = P $Bin 'lithify.cmd'
        Set-Content -Path $shim -Encoding Oem -ErrorAction Stop -Value @(
            '@echo off',
            ('"{0}" -X utf8 "{1}" %*' -f $py, (P $dest 'bin' 'lithify'))
        )
        $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
        if (-not $userPath) { $userPath = '' }
        if (($userPath -split ';') -notcontains $Bin) {
            [Environment]::SetEnvironmentVariable('Path', (($userPath.TrimEnd(';') + ';' + $Bin).TrimStart(';')), 'User')
        }
        if (($env:Path -split ';') -notcontains $Bin) { $env:Path += ";$Bin" }
        $version = ''
        if ($IsWindows -ne $false) {  # ($IsWindows: PowerShell 7's; this is Windows where it is not set)
            $version = (& $shim --version 2>&1 | Out-String).Trim()
            if ($LASTEXITCODE -ne 0) {
                Fail (L 'the lithify command does not start:', 'polecenie lithify się nie uruchamia:') @($version,
                    (L "remove $dest, then run this again (or report it)", "usuń $dest i uruchom to ponownie (albo zgłoś błąd)"))
            }
            $version = " ($version)"
        }
        Say (L "installed the command: $shim$version (new windows know it as `"lithify`")",
               "zainstalowano polecenie: $shim$version (nowe okna znają je jako `"lithify`")")
        return $shim
    }

    # ---- what building the speaker's software needs: Git, Docker Desktop, WSL ----

    function Test-Docker {
        if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { return $false }
        & docker info *> $null
        return ($LASTEXITCODE -eq 0)
    }

    # docker.exe of a Docker Desktop installed just now, before this window's PATH knows it.
    function Add-DockerPath {
        if (Get-Command docker -ErrorAction SilentlyContinue) { return }
        foreach ($root in Get-ProgramDirs) {
            $dir = P $root 'Docker' 'Docker' 'resources' 'bin'
            if ([IO.Path]::IsPathRooted($dir) -and (Test-Path -LiteralPath (P $dir 'docker.exe'))) { $env:Path = "$dir;$env:Path"; return }
        }
    }

    function Find-DockerDesktop {
        $apps = @(Get-ProgramDirs | ForEach-Object { P $_ 'Docker' 'Docker' 'Docker Desktop.exe' }) +
                @(P $env:LOCALAPPDATA 'Programs' 'Docker' 'Docker' 'Docker Desktop.exe')
        foreach ($p in $apps) {
            if ([IO.Path]::IsPathRooted($p) -and (Test-Path -LiteralPath $p)) { return $p }
        }
        return $null
    }

    function Get-DockerLink {
        $arch = if ((Get-Arch) -eq 'aarch64') { 'arm64' } else { 'amd64' }
        return "https://desktop.docker.com/win/main/$arch/Docker%20Desktop%20Installer.exe"
    }

    function Wait-Docker([int]$seconds) {
        Write-Host -NoNewline ('    ' + (L "waiting for Docker Desktop to start (up to $([int]($seconds / 60)) minutes)",
                                            "czekanie na uruchomienie Docker Desktop (do $([int]($seconds / 60)) min)"))
        $deadline = (Get-Date).AddSeconds($seconds)
        while ((Get-Date) -lt $deadline) {
            Add-DockerPath
            if (Test-Docker) { Write-Host (L ' ready', ' gotowe'); return $true }
            Write-Host -NoNewline '.'
            Start-Sleep -Seconds 3
        }
        Write-Host ''
        return $false
    }

    function Find-Wsl {
        # (Sysnative: the real System32 for a 32-bit PowerShell)
        foreach ($p in @((P $env:SystemRoot 'Sysnative' 'wsl.exe'), (P $env:SystemRoot 'System32' 'wsl.exe'))) {
            if ([IO.Path]::IsPathRooted($p) -and (Test-Path -LiteralPath $p)) { return $p }
        }
        return $null
    }

    function Test-Wsl {
        $wsl = Find-Wsl
        if (-not $wsl) { return $false }
        $saved = $env:WSL_UTF8
        $env:WSL_UTF8 = '1'
        & $wsl --status *> $null
        $ok = ($LASTEXITCODE -eq 0)
        $env:WSL_UTF8 = $saved
        return $ok
    }

    function Test-RebootPending {
        return (Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending')
    }

    # ---- the firewall: the speaker downloads from this computer -------------

    # Lithify's Python: the one that runs it, and its windowless twin (the helper's scheduled task).
    function Get-FirewallPrograms([string]$py) {
        if (-not $py) { return @() }
        return @($py, (P (Split-Path -Parent $py) 'pythonw.exe'))
    }

    # The rule is there: for the ports, and for each of Lithify's Pythons. With a rule of its own,
    # Windows never asks whether Python may accept connections, so it never makes the block that
    # asking makes. (hostos.firewall_rule_present checks the same.)
    function Test-FirewallRule([string]$py) {
        $want = @('any') + @(Get-FirewallPrograms $py | ForEach-Object { $_.ToLowerInvariant() })
        try {
            $have = @(Get-NetFirewallRule -DisplayName $FirewallRule -ErrorAction Stop |
                      Where-Object { $_.Enabled -eq 'True' -and $_.Action -eq 'Allow' } |
                      Get-NetFirewallApplicationFilter | ForEach-Object { "$($_.Program)".ToLowerInvariant() })
        } catch { return $false }
        foreach ($w in $want) { if ($have -notcontains $w) { return $false } }
        return $true
    }

    # Rules that block connections to Lithify's Python (their names): Windows makes them when
    # someone answers "Cancel" as it asks whether Python may accept connections, and a block wins
    # over the rule that lets the speakers in. (hostos.python_blocks finds them the same way.)
    function Get-PythonBlocks([string]$py) {
        $progs = @(Get-FirewallPrograms $py)
        if (-not $progs.Count) { return @() }
        try {
            return @(Get-NetFirewallApplicationFilter -ErrorAction Stop | Where-Object { $progs -contains $_.Program } |
                     Get-NetFirewallRule -ErrorAction SilentlyContinue |
                     Where-Object { $_.Direction -eq 'Inbound' -and $_.Action -eq 'Block' -and $_.Enabled -eq 'True' } |
                     ForEach-Object { $_.Name })
        } catch { return @() }
    }

    # ---- restarting Windows ------------------------------------------------

    # How to start this installer again: the launcher it came from, else Lithify's own copy of this
    # file, else this file.
    function Get-AgainCommand {
        $launcher = $env:LITHIFY_LAUNCHER_FILE
        if ($launcher -and (Test-Path -LiteralPath $launcher)) {
            # Through cmd.exe: opened as a file, a downloaded launcher would bring up Windows'
            # "publisher could not be verified" question again, which was answered already.
            $cmd = P $env:SystemRoot 'System32' 'cmd.exe'
            return "`"$cmd`" /c `"`"$launcher`"`""
        }
        $ps = P $env:SystemRoot 'System32' 'WindowsPowerShell' 'v1.0' 'powershell.exe'
        foreach ($f in @((P $Dest 'installer' 'install.ps1'), $PSCommandPath)) {
            if ($f -and (Test-Path -LiteralPath $f)) { return "`"$ps`" -NoProfile -ExecutionPolicy Bypass -File `"$f`"" }
        }
        return $null
    }

    # The restart can be now: Windows then starts this installer again once the person signs in
    # (RunOnce: one time). Never without asking, not even with LITHIFY_YES: a restart closes
    # everything that is open.
    function Stop-ForRestart([string]$why) {
        $again = Get-AgainCommand
        if ($again -and $env:LITHIFY_YES -ne '1') {
            Say $why
            Info (L 'Lithify can restart it now and go on by itself once you sign in again.',
                    'Lithify może teraz uruchomić Windows ponownie i sam będzie kontynuować, gdy się zalogujesz.')
            $a = Read-Host ('    ' + (L 'Restart Windows now? Save your work in other programs first. [Y/n]',
                                       'Uruchomić Windows ponownie teraz? Najpierw zapisz pracę w innych programach. [T/n]'))
            if ($a -eq '' -or $a -match '^[YyTt]') {
                $key = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\RunOnce'
                try {
                    if (-not (Test-Path $key)) { New-Item -Path $key -ErrorAction Stop | Out-Null }
                    Set-ItemProperty -Path $key -Name 'Lithify' -Value $again -ErrorAction Stop
                    & (P $env:SystemRoot 'System32' 'shutdown.exe') /r /t 20 /c (L 'Lithify: restarting Windows to finish the installation',
                                                                                 'Lithify: ponowne uruchomienie Windows, żeby dokończyć instalację')
                    if ($LASTEXITCODE -eq 0) {
                        $State.Restarting = $true
                        Later (L 'Windows restarts in 20 seconds', 'Windows uruchomi się ponownie za 20 sekund') @(
                            (L 'Once you sign in again, Lithify goes on by itself in a window like this one.',
                               'Gdy zalogujesz się ponownie, Lithify sam będzie kontynuować w takim samym oknie.'),
                            (L '(Windows may show a welcome window of WSL then, and Docker Desktop starting: close the',
                               '(Windows może wtedy pokazać okno powitalne WSL i uruchamiać Docker Desktop: zamknij okno'),
                            (L 'WSL window; Lithify waits for Docker Desktop.)',
                               'WSL, Lithify poczeka na Docker Desktop.)'),
                            (L 'To stop the restart: shutdown /a (in a terminal); then restart later yourself.',
                               'Żeby zatrzymać restart: shutdown /a (w terminalu); potem uruchom Windows ponownie samodzielnie.'))
                    }
                    Remove-ItemProperty -Path $key -Name 'Lithify' -ErrorAction SilentlyContinue
                    Warn (L "Windows did not restart (code $LASTEXITCODE)", "Windows nie uruchomił się ponownie (kod $LASTEXITCODE)")
                } catch [System.Management.Automation.RuntimeException] {
                    if ("$($_.Exception.Message)".StartsWith('==>')) { throw }
                    Warn (L "cannot restart Windows from here: $($_.Exception.Message)", "nie można stąd uruchomić ponownie Windows: $($_.Exception.Message)")
                }
            }
        }
        Later $why @(
            (L 'Restart it (Start > Power > Restart). Afterwards double-click Lithify-Windows.cmd again (or run',
               'Uruchom go ponownie (Start > Zasilanie > Uruchom ponownie). Potem znowu kliknij dwukrotnie Lithify-Windows.cmd'),
            (L 'the same command again): it skips what is already done and goes on.',
               '(albo uruchom to samo polecenie): pominie to, co już zrobione, i przejdzie dalej.'))
    }

    # ---- what this computer needs, asked for once ---------------------------

    # The published bundle versions.toml names (`lithify install` downloads it first). Read here
    # without Python, which may not be installed yet.
    function Get-ReleaseUrl([string]$dest) {
        $f = P $dest 'versions.toml'
        if (-not (Test-Path -LiteralPath $f)) { return '' }
        $m = [regex]::Match([IO.File]::ReadAllText($f), '(?ms)^\[release\][^\[]*?^url\s*=\s*"([^"]*)"')
        if ($m.Success) { return $m.Groups[1].Value }
        return ''
    }

    # Whether the speaker's software is built on this computer: not when a bundle is there already,
    # or published (Docker Desktop and Git are needed then only when its download fails).
    function Test-BuildHere([string]$dest) {
        $bundle = P $Cache 'bundle'
        if (Test-Path -LiteralPath (P $bundle 'VERSIONS')) {
            Say (L "a prebuilt Lithify bundle is already on this computer ($bundle)", "gotowa paczka Lithify jest już na tym komputerze ($bundle)")
            Info (L 'Docker Desktop and Git are not needed for this install.', 'Docker Desktop i Git nie są potrzebne do tej instalacji.')
            if (-not (Get-ReleaseUrl $dest).StartsWith('https://')) { Show-UpdatesTip }
            return $false
        }
        $release = Get-ReleaseUrl $dest
        if ($release.StartsWith('https://')) {
            # ("Update everything" downloads the next release too: no tip about building)
            Say (L "Lithify's prebuilt bundle is published: the install downloads it ($release)",
                   "gotowa paczka Lithify jest opublikowana: instalacja ją pobierze ($release)")
            Info (L 'Docker Desktop and Git are needed only when that download fails (Lithify is built here then).',
                    'Docker Desktop i Git są potrzebne tylko wtedy, gdy to pobieranie się nie uda (paczka powstaje wtedy tutaj).')
            return $false
        }
        return $true
    }

    # Without published releases, "Update everything" on the speaker's page builds on this computer,
    # with Git and a docker (installed, running or not: Docker Desktop starts when a build needs it):
    # the ones missing.
    function Show-UpdatesTip {
        Add-DockerPath
        $docker = [bool]((Find-DockerDesktop) -or (Get-Command docker -ErrorAction SilentlyContinue))
        $git = [bool](Find-Git)
        if ($docker -and $git) { return }
        $what = if ($docker) { L 'Git', 'Gita' } elseif ($git) { 'Docker Desktop' } else { L 'Docker Desktop and Git', 'Docker Desktop i Gita' }
        Warn (L "to build updates later (`"Update everything`" on the speaker's page), this computer needs $what",
                "żeby później budować aktualizacje (`"Zaktualizuj wszystko`" na stronie głośnika), ten komputer potrzebuje $what")
    }

    # What is missing: python, git, docker, wsl, firewall (true), blocks (the rules' names).
    function Get-Needs([string]$py, [bool]$build, [bool]$setup) {
        $needs = [ordered]@{}
        if (-not $py) { $needs.python = $true }
        if (-not $setup) { return $needs }
        if ($build) {
            if (-not (Find-Git)) { $needs.git = $true }
            Add-DockerPath
            if (-not (Test-Docker)) {
                $desktop = Find-DockerDesktop
                if (-not $desktop -and -not (Get-Command docker -ErrorAction SilentlyContinue)) { $needs.docker = $true }
                # (Docker Desktop runs on WSL 2; another engine, as Rancher Desktop, is not looked after)
                if (($desktop -or $needs.docker) -and -not (Test-Wsl)) { $needs.wsl = $true }
            }
        }
        # (a Python installed now needs its own rule too)
        if (-not $py -or -not (Test-FirewallRule $py)) { $needs.firewall = $true }
        $blocks = @(Get-PythonBlocks $py)
        if ($blocks.Count) { $needs.blocks = $blocks }
        return $needs
    }

    function Test-NeedsAdmin($needs) {
        return [bool]($needs.git -or $needs.docker -or $needs.wsl -or $needs.firewall -or $needs.blocks)
    }

    # Where to get each one by hand.
    function Get-ByHand($needs) {
        $lines = @()
        if ($needs.python) { $lines += (L 'Python 3.11 or newer: https://www.python.org/downloads/ (tick "Add python.exe to PATH")',
                                          'Python 3.11 lub nowszy: https://www.python.org/downloads/ (zaznacz "Add python.exe to PATH")') }
        if ($needs.git) { $lines += (L 'Git: https://git-scm.com/download/win (the default choices are fine)',
                                       'Git: https://git-scm.com/download/win (domyślne ustawienia są dobre)') }
        if ($needs.docker) { $lines += "Docker Desktop: $(Get-DockerLink)" }
        if ($needs.wsl) { $lines += (L 'WSL: wsl --install --no-distribution (in a terminal opened as administrator), then restart',
                                       'WSL: wsl --install --no-distribution (w terminalu otwartym jako administrator), potem restart') }
        $lines += (L 'then run this again: it skips what is already done', 'potem uruchom to ponownie: pominie to, co już zrobione')
        return $lines
    }

    function Show-Plan($needs) {
        Say (L 'Lithify needs a few things on this computer:', 'Lithify potrzebuje na tym komputerze kilku rzeczy:')
        if ($needs.python) { Info (L '- Python 3.12 (from python.org, for this user)', '- Python 3.12 (z python.org, dla tego użytkownika)') }
        if ($needs.git) { Info (L '- Git: it downloads the source code of librespot', '- Git: pobiera kod źródłowy librespot') }
        if ($needs.docker) {
            Info (L '- Docker Desktop: it builds the speaker''s software on this computer (free for personal use, about 600 MB)',
                    '- Docker Desktop: buduje oprogramowanie głośnika na tym komputerze (bezpłatny do użytku osobistego, ok. 600 MB)')
        }
        if ($needs.wsl) {
            Info (L '- WSL 2, a part of Windows that Docker Desktop needs (Windows restarts once afterwards)',
                    '- WSL 2, część Windows potrzebna Docker Desktop (potem jeden restart Windows)')
        }
        if ($needs.firewall -or $needs.blocks) {
            Info (L "- a firewall rule, so the speaker can download its software from this computer ($PortsText)",
                    "- reguła zapory, żeby głośnik mógł pobrać oprogramowanie z tego komputera ($PortsText)")
        }
        if (Test-NeedsAdmin $needs) {
            Info (L 'Windows asks once for permission (a "User Account Control" window): choose "Yes".',
                    'Windows raz zapyta o zgodę (okno "Kontrola konta użytkownika"): wybierz "Tak".')
        }
    }

    # Git, Docker Desktop, WSL and the firewall rule, in one window with administrator rights: one
    # question from Windows for all of them. Returns what each step ended with (name -> code).
    function Invoke-AdminStep($needs, [string]$py) {
        $out = P ([IO.Path]::GetTempPath()) ('lithify-admin-' + [guid]::NewGuid().ToString('N').Substring(0, 8) + '.txt')
        $tasks = @('git', 'docker', 'wsl', 'firewall', 'blocks') | Where-Object { $needs[$_] }
        $winget = ''
        $w = Get-Command winget -ErrorAction SilentlyContinue
        if ($w) { $winget = $w.Source }
        $wsl = Find-Wsl
        $vars = @(
            "`$Out = $(Q $out)",
            "`$Tasks = @($((@($tasks) | ForEach-Object { Q $_ }) -join ', '))",
            "`$Winget = $(Q $winget)",
            "`$Wsl = $(Q "$wsl")",
            "`$FirewallArgs = @($((@($FirewallArgs) | ForEach-Object { Q $_ }) -join ', '))",
            "`$FirewallName = $(Q $FirewallRule)",
            "`$FirewallPrograms = @($((@(Get-FirewallPrograms $py) | ForEach-Object { Q $_ }) -join ', '))",
            "`$Blocks = @($((@($needs.blocks) | Where-Object { $_ } | ForEach-Object { Q $_ }) -join ', '))",
            "`$Title = $(Q (L 'Lithify: installing what the speaker''s software needs (this window closes by itself)',
                               'Lithify: instalowanie tego, czego potrzebuje oprogramowanie głośnika (to okno samo się zamknie)'))",
            "`$Text = @{ git = $(Q (L 'installing Git', 'instalowanie Gita')); docker = $(Q (L 'installing Docker Desktop (about 600 MB)', 'instalowanie Docker Desktop (ok. 600 MB)')); wsl = $(Q (L 'installing WSL 2', 'instalowanie WSL 2')); firewall = $(Q (L 'adding the firewall rule "Lithify"', 'dodawanie reguły zapory "Lithify"')); blocks = $(Q (L 'removing the firewall''s blocks of Python', 'usuwanie blokad Pythona w zaporze')); done = $(Q (L 'done', 'gotowe')) }"
        )
        $body = @'
$ErrorActionPreference = 'Continue'
try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false } catch { $null = $_ }
try { $Host.UI.RawUI.WindowTitle = 'Lithify' } catch { $null = $_ }
function Done([string]$key, $code) { Add-Content -LiteralPath $Out -Value "$key=$code" -Encoding ASCII }
$wingetArgs = @('--source', 'winget', '--silent', '--accept-package-agreements', '--accept-source-agreements')
Write-Host ''
Write-Host "==> $Title"
foreach ($task in $Tasks) {
    Write-Host ''
    Write-Host "==> $($Text[$task])"
    switch ($task) {
        'git' { & $Winget install -e --id Git.Git @wingetArgs; Done git $LASTEXITCODE }
        'docker' { & $Winget install -e --id Docker.DockerDesktop @wingetArgs; Done docker $LASTEXITCODE }
        'wsl' { & $Wsl --install --no-distribution; Done wsl $LASTEXITCODE }
        'firewall' {
            # (the whole rule again: for the ports, and for each of Lithify's Pythons)
            & netsh advfirewall firewall delete rule "name=$FirewallName" *> $null
            & netsh @FirewallArgs; $code = $LASTEXITCODE
            foreach ($p in $FirewallPrograms) { & netsh @FirewallArgs "program=$p"; if ($LASTEXITCODE) { $code = $LASTEXITCODE } }
            Done firewall $code
        }
        'blocks' { Remove-NetFirewallRule -Name $Blocks -ErrorAction SilentlyContinue; Done blocks 0 }
    }
}
Done end 0
Write-Host ''
Write-Host "==> $($Text.done)"
Start-Sleep -Seconds 3
'@
        $script = ($vars -join "`n") + "`n" + $body
        $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($script))
        Say (L 'Windows asks for permission now: choose "Yes". A second window shows the installation and closes by itself.',
               'Windows prosi teraz o zgodę: wybierz "Tak". Drugie okno pokaże instalację i samo się zamknie.')
        $ps = P $env:SystemRoot 'System32' 'WindowsPowerShell' 'v1.0' 'powershell.exe'
        try {
            Start-Process -FilePath $ps -Verb RunAs -Wait -ErrorAction Stop -ArgumentList @(
                '-NoProfile', '-ExecutionPolicy', 'Bypass', '-EncodedCommand', $encoded)
        } catch {
            Fail (L 'Windows did not get permission (the answer was "No")', 'Windows nie dostał zgody (odpowiedź "Nie")') (@(
                (L 'run this again and choose "Yes" - or install these yourself:', 'uruchom to ponownie i wybierz "Tak" - albo zainstaluj to samodzielnie:')) + @(Get-ByHand $needs))
        }
        $codes = @{}
        if (Test-Path -LiteralPath $out) {
            foreach ($line in [IO.File]::ReadAllLines($out)) {
                $k, $v = $line -split '=', 2
                if ($k) { $codes[$k] = [int64]$v }
            }
            Remove-Item -LiteralPath $out -Force -ErrorAction SilentlyContinue
        }
        Update-Path
        return $codes
    }

    # What the step with administrator rights did; true when Windows must restart (WSL).
    function Confirm-AdminStep($needs, $codes, [string]$py) {
        $restart = $false
        if ($needs.git -and -not (Find-Git)) {
            Fail (L "Git was not installed (winget: code $($codes.git))", "Git nie został zainstalowany (winget: kod $($codes.git))") @(
                (L 'install it from https://git-scm.com/download/win (the default choices are fine), then run this again',
                   'zainstaluj go z https://git-scm.com/download/win (domyślne ustawienia są dobre) i uruchom to ponownie'))
        }
        if ($needs.docker) {
            $rc = $codes.docker
            $link = Get-DockerLink
            if ($rc -eq $WingetCancelled) {
                Fail (L 'the installation of Docker Desktop was cancelled', 'instalacja Docker Desktop została przerwana') @(
                    (L 'run this again and allow it, or install it yourself:', 'uruchom to ponownie i pozwól na nią albo zainstaluj go samodzielnie:'), $link)
            }
            if ($rc -eq $WingetRebootToFinish -or $rc -eq $WingetRebootInitiated -or $rc -eq $WingetRebootFirst) { $restart = $true }
            elseif ($rc -ne 0 -and $rc -ne $WingetAlreadyInstalled) {
                Fail (L "winget could not install Docker Desktop (code $rc)", "winget nie zainstalował Docker Desktop (kod $rc)") @(
                    (L "install it yourself: $link", "zainstaluj go samodzielnie: $link"),
                    (L 'then run this again', 'potem uruchom to ponownie'))
            }
            Add-DockerPath
        }
        if ($needs.wsl) {
            if ($codes.wsl -eq 0) { $restart = $true }
            else {
                Warn (L "WSL was not installed (code $($codes.wsl)): Docker Desktop says what it needs when it starts",
                        "WSL nie został zainstalowany (kod $($codes.wsl)): Docker Desktop powie przy starcie, czego potrzebuje")
                Info (L 'To install WSL yourself: wsl --install --no-distribution (in a terminal opened as administrator), then restart.',
                        'Żeby zainstalować WSL samodzielnie: wsl --install --no-distribution (w terminalu otwartym jako administrator), potem restart.')
            }
        }
        if (($needs.firewall -and -not (Test-FirewallRule $py)) -or ($needs.blocks -and @(Get-PythonBlocks $py).Count)) {
            Warn (L 'the firewall rule was not added: Windows asks for it again when the wizard installs',
                    'reguła zapory nie została dodana: Windows zapyta o nią ponownie podczas instalacji w kreatorze')
        }
        return $restart
    }

    function Start-Docker {
        Add-DockerPath
        if (Test-Docker) { Say (L 'Docker is running', 'Docker działa'); return }
        $app = Find-DockerDesktop
        if (-not $app) {
            Later (L 'docker is installed, but its engine does not answer', 'docker jest zainstalowany, ale jego silnik nie odpowiada') @(
                (L 'start it (Docker Desktop, Rancher Desktop, ...), then run this installer again',
                   'uruchom go (Docker Desktop, Rancher Desktop, ...) i uruchom ten instalator ponownie'))
        }
        Say (L 'starting Docker Desktop', 'uruchamianie Docker Desktop')
        Info (L 'Its first start takes a few minutes; it may offer a sign-in or a survey: "Skip" is fine.',
                'Pierwsze uruchomienie trwa kilka minut; może zaproponować logowanie albo ankietę: "Skip" wystarczy.')
        Info (L 'If it asks you to accept its terms (the Docker Subscription Service Agreement), click Accept.',
                'Jeśli poprosi o akceptację warunków (Docker Subscription Service Agreement), kliknij Accept.')
        Start-Process -FilePath $app
        if (Wait-Docker 180) { return }
        if (Test-RebootPending) {
            Stop-ForRestart (L 'Windows needs a restart to finish setting up Docker Desktop.',
                               'Windows musi się uruchomić ponownie, żeby dokończyć konfigurację Docker Desktop.')
        }
        Later (L 'Docker Desktop has not finished starting', 'Docker Desktop jeszcze się nie uruchomił') @(
            (L 'Look at its window: accept its terms if it asks, update WSL if it says so (wsl --update),',
               'Spójrz na jego okno: zaakceptuj warunki, jeśli o to prosi, zaktualizuj WSL, jeśli tak napisze (wsl --update),'),
            (L 'and wait until it shows "Engine running". If it says you must be in the "docker-users" group,',
               'i poczekaj, aż pokaże "Engine running". Jeśli napisze, że musisz być w grupie "docker-users",'),
            (L 'sign out of Windows and back in. Then run this installer again: it skips what is already done.',
               'wyloguj się z Windows i zaloguj ponownie. Potem uruchom ten instalator ponownie: pominie to, co już zrobione.'))
    }

    # ---- the speaker -------------------------------------------------------

    # The browser wizard, unless told otherwise or over SSH; "invalid choice": a Lithify from before it.
    function Use-Wizard([string]$shim) {
        if ($env:LITHIFY_HOST -or $env:LITHIFY_NO_WIZARD -eq '1' -or $env:SSH_CONNECTION) { return $false }
        $out = & $shim wizard --help 2>&1 | Out-String
        if ($LASTEXITCODE -eq 0) { return $true }
        if ($out -match 'invalid choice') { Info (L 'this Lithify has no browser wizard yet: on in this window', 'ten Lithify nie ma jeszcze kreatora w przeglądarce: dalej w tym oknie') }
        else { Warn (L '"lithify wizard" does not start: on in this window', '"lithify wizard" się nie uruchamia: dalej w tym oknie') }
        return $false
    }

    function Install-OnSpeaker([string]$shim, [string]$py) {
        if (-not (Test-FirewallRule $py)) {
            Info (L "Windows asks once to let the speaker reach this computer through the firewall ($PortsText): choose ""Yes"".",
                    "Windows raz zapyta, czy głośnik może łączyć się z tym komputerem przez zaporę ($PortsText): wybierz ""Tak"".")
        }
        if (Use-Wizard $shim) {
            # (after installing, the wizard also sets up the helper that keeps the speaker updatable)
            Say (L 'opening the Lithify wizard in your web browser: it finds the speaker, asks for its name, installs',
                   'otwieranie kreatora Lithify w przeglądarce: znajdzie głośnik, zapyta o nazwę i zainstaluje Lithify')
            Info (L 'A browser that starts for the first time shows its own welcome screens first: click through them.',
                    'Przeglądarka uruchamiana pierwszy raz najpierw pokaże własne ekrany powitalne: przejdź przez nie.')
            $wizardArgs = @('wizard')
            if ($env:LITHIFY_LANG) { $wizardArgs += @('--lang', $Lang) }
            & $shim @wizardArgs
            if ($LASTEXITCODE -ne 0) {
                Fail (L 'Lithify was not installed on the speaker (the wizard ended before that)',
                        'Lithify nie został zainstalowany na głośniku (kreator zakończył się wcześniej)') @(
                    (L 'start it again: lithify wizard   (or in this window: lithify install)',
                       'uruchom go ponownie: lithify wizard   (albo w tym oknie: lithify install)'))
            }
        } else {
            Say (L 'installing Lithify on the speaker (in this window)', 'instalowanie Lithify na głośniku (w tym oknie)')
            $hostArg = @()
            if ($env:LITHIFY_HOST) { $hostArg = @('--host', $env:LITHIFY_HOST) }
            & $shim install --reboot @hostArg
            if ($LASTEXITCODE -ne 0) {
                Fail (L 'the install did not finish (see above)', 'instalacja się nie zakończyła (szczegóły powyżej)') @(
                    (L 'fix what it says, then run this installer again (or: lithify install)',
                       'napraw to, co tam opisano, i uruchom ten instalator ponownie (albo: lithify install)'))
            }
            # The helper starts at logon (a scheduled task): the speaker's web page installs updates through it.
            & $shim serve --install-service
            if ($LASTEXITCODE -ne 0) {
                Warn (L 'to install updates from the speaker''s web page, keep "lithify serve" running on this computer',
                        'żeby instalować aktualizacje ze strony głośnika, zostaw na tym komputerze uruchomione "lithify serve"')
            }
        }
        $page = & $shim ui 2>$null
        Write-Host ''
        Say (L 'done! Open Spotify and pick the speaker in its list of devices (the Spotify Connect icon).',
               'gotowe! Otwórz Spotify i wybierz głośnik z listy urządzeń (ikona Spotify Connect).')
        if ($page) { Say (L "its page (settings, tests, updates): $page", "jego strona (ustawienia, testy, aktualizacje): $page") }
    }

    function Show-Banner {
        if ($env:LITHIFY_LAUNCHER -ne '1') {
            Write-Host ((L 'Lithify installer: Spotify Connect (librespot) for Lithe Audio speakers',
                           'Instalator Lithify: Spotify Connect (librespot) dla głośników Lithe Audio') + "`n")
            return
        }
        Write-Host ''
        Write-Host '  =============================================================='
        Write-Host ('    ' + (L 'Lithify - Spotify Connect for your Lithe Audio speaker', 'Lithify - Spotify Connect dla głośników Lithe Audio'))
        Write-Host '  =============================================================='
        Write-Host ''
        foreach ($line in @(
                (L 'This gets your computer ready (it asks before it installs anything),',
                   'Ten program przygotuje komputer (zapyta, zanim cokolwiek zainstaluje),'),
                (L 'then opens a page in your web browser that finds the speaker and',
                   'a potem otworzy w przeglądarce stronę, która znajdzie głośnik'),
                (L 'installs Lithify on it. The first time takes up to an hour; the',
                   'i zainstaluje na nim Lithify. Za pierwszym razem trwa to do godziny;'),
                (L 'computer and the speaker must be on the same network.',
                   'komputer i głośnik muszą być w tej samej sieci.'))) {
            Write-Host "  $line"
        }
        Write-Host ''
    }

    # How it ended, for the launcher's window (it waits for a key afterwards).
    function Show-End([int]$code) {
        if ($env:LITHIFY_LAUNCHER -ne '1') { return }
        Write-Host ''
        if ($code -eq 0) { Write-Host ('  ' + (L 'Lithify is ready. Enjoy the music!', 'Lithify jest gotowy. Miłego słuchania!')) -ForegroundColor Green }
        elseif ($code -eq $RestartCode) {
            Write-Host ('  ' + (L 'Windows restarts now. Once you sign in again, Lithify goes on by itself.',
                                  'Windows uruchamia się ponownie. Gdy się zalogujesz, Lithify sam będzie kontynuować.'))
        } else {
            Write-Host ('  ' + (L 'Lithify is not installed yet: the messages above say why, and what to do.',
                                  'Lithify nie jest jeszcze zainstalowany: komunikaty powyżej mówią dlaczego i co zrobić.'))
            Write-Host ('  ' + (L 'You can run this again any time: it skips what is already done.',
                                  'Możesz to uruchomić ponownie w każdej chwili: pominie to, co już zrobione.'))
        }
        Write-Host ''
    }

    $code = 0
    # git and Git Credential Manager never ask (a wrong address would wait for a password); put
    # back afterwards, for a window that ran this script
    $savedGitEnv = @{ GIT_TERMINAL_PROMPT = $env:GIT_TERMINAL_PROMPT; GCM_INTERACTIVE = $env:GCM_INTERACTIVE }
    $env:GIT_TERMINAL_PROMPT = '0'
    $env:GCM_INTERACTIVE = 'never'
    try {
        # (TLS 1.2 for GitHub: old Windows PowerShell 5.1 setups offer only TLS 1.0 by default)
        try { [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12 }
        catch { Warn (L 'cannot turn on TLS 1.2: downloads may fail', 'nie można włączyć TLS 1.2: pobieranie może się nie udać') }
        Show-Banner
        $setup = $env:LITHIFY_NO_SETUP -ne '1'
        $dest = Get-Lithify | Select-Object -Last 1
        $py = Get-Python | Select-Object -Last 1
        $build = $setup -and (Test-BuildHere $dest)
        $needs = Get-Needs $py $build $setup
        if ($needs.Count) {
            if (($needs.git -or $needs.docker) -and -not (Get-Command winget -ErrorAction SilentlyContinue)) {
                Fail (L 'Git and Docker Desktop are needed, and this Windows has no winget to install them with',
                        'potrzebne są Git i Docker Desktop, a ten Windows nie ma winget, żeby je zainstalować') (@(
                    (L 'install "App Installer" from the Microsoft Store - or these yourself:',
                       'zainstaluj "Instalator aplikacji" ze Sklepu Microsoft - albo to samodzielnie:')) + @(Get-ByHand $needs))
            }
            Show-Plan $needs
            if (-not (Agree (L 'Set this up now?', 'Przygotować to teraz?'))) {
                if ($needs.python -or $needs.git -or $needs.docker -or $needs.wsl) {
                    Fail (L 'Lithify cannot go on without them', 'Lithify nie może bez nich kontynuować') @(Get-ByHand $needs)
                }
                $needs = [ordered]@{}  # (only the firewall rule: the wizard asks for it again)
            }
        }
        if ($needs.python) { $py = Install-Python | Select-Object -Last 1 }
        $shim = Install-Command $py $dest | Select-Object -Last 1
        if ($setup) {
            if (Test-NeedsAdmin $needs) {
                $codes = Invoke-AdminStep $needs $py
                if (Confirm-AdminStep $needs $codes $py) {
                    Stop-ForRestart (L 'Windows needs a restart to finish installing WSL and Docker Desktop.',
                                       'Windows musi się uruchomić ponownie, żeby dokończyć instalację WSL i Docker Desktop.')
                }
            }
            if ($build) { Start-Docker }
            Install-OnSpeaker $shim $py
        }
    } catch {
        $lines = @("$($_.Exception.Message)" -split "`n")
        Write-Host ''
        if ($lines[0].StartsWith('==>')) {
            Write-Host $lines[0] -ForegroundColor Yellow
            foreach ($l in @($lines | Select-Object -Skip 1)) { Write-Host "    $l" }
        } else {
            Write-Host $lines[0] -ForegroundColor Red
            foreach ($l in @($lines | Select-Object -Skip 1)) { Write-Host "       $l" }
        }
        $code = if ($State.Restarting) { $RestartCode } else { 1 }
    } finally {
        foreach ($k in @($savedGitEnv.Keys)) { [Environment]::SetEnvironmentVariable($k, $savedGitEnv[$k], 'Process') }
    }
    Show-End $code
    # Run as a file (Lithify-Windows.cmd, get.ps1, .\installer\install.ps1): its exit code.
    if ($PSCommandPath) { exit $code }
    $global:LASTEXITCODE = $code
}
