# Lithify installer for Windows:
#
#   irm https://raw.githubusercontent.com/OWNER/lithify/main/installer/get.ps1 | iex
#   .\installer\install.ps1           (from a checkout: uses it in place)
#   Lithify-Windows.cmd               (the same with a double-click)
#
# It gets this computer ready, and asks before it installs anything:
#   1. Python 3.11 or newer: one that is installed, else Python 3.12 with winget, else a private
#      Python 3.12 for Lithify only (installed by uv; no administrator rights);
#   2. Lithify itself: with git when it is there, otherwise from GitHub's zip archive;
#   3. the `lithify` command;
#   4. Git and Docker Desktop, which build Lithify for the speaker (not needed when a prebuilt
#      bundle is already there).
# Then it opens the Lithify wizard in the web browser: find the speaker, choose its name, install.
# Over SSH, or with LITHIFY_HOST, it does the same in this window: `lithify install --reboot`,
# then `lithify serve --install-service`. Windows asks once to let the speaker reach this
# computer through the firewall (TCP 8095 and 18096-18099).
#
# Environment: LITHIFY_YES=1 (answer yes to every question), LITHIFY_HOST (the speaker's address:
#              no search, no wizard), LITHIFY_NAME (its name in Spotify), LITHIFY_NO_WIZARD=1 (this
#              window instead of the browser), LITHIFY_NO_SETUP=1 (only steps 1-3), LITHIFY_HOME
#              (install dir), LITHIFY_REPO (git URL), LITHIFY_ARCHIVE_URL (the .zip to use without
#              git).

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
    $Ports = 'TCP 8095 and 18096-18099'
    # winget's answers (https://github.com/microsoft/winget-cli/blob/master/doc/windows/package-manager/winget/returnCodes.md)
    $WingetRebootToFinish = -1978334967
    $WingetRebootFirst = -1978334966
    $WingetRebootInitiated = -1978334965
    $WingetCancelled = -1978334964
    $WingetAlreadyInstalled = -1978335135
    # The exit code when Windows restarts to go on (ERROR_SUCCESS_REBOOT_REQUIRED): the launcher
    # then says so instead of "not installed yet".
    $RestartCode = 3010
    $State = @{ Restarting = $false }

    function Say([string]$m) { Write-Host "==> $m" }
    function Info([string]$m) { Write-Host "    $m" }
    function Warn([string]$m) { Write-Host "warning: $m" -ForegroundColor Yellow }
    # A failure: what went wrong, then what to do next (a line each).
    function Fail([string]$m, [string[]]$next = @()) { throw ((@("error: $m") + $next) -join "`n") }
    # No failure, but the install cannot go on yet (a restart, a program to start first): why,
    # and what to do then.
    function Later([string]$m, [string[]]$next = @()) { throw ((@("==> $m") + $next) -join "`n") }
    # A path from its parts, with this system's separator.
    function P { return [IO.Path]::Combine([string[]]@($args | ForEach-Object { "$_" })) }

    # The Program Files folders: 64-bit first (a 32-bit PowerShell's ProgramFiles is the "(x86)" one).
    function Get-ProgramDirs { return @(@($env:ProgramW6432, $env:ProgramFiles) | Where-Object { $_ } | Select-Object -Unique) }

    # PATH as a new window would see it (after an installer changed it), with the command's folder.
    function Update-Path {
        $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' +
                    [Environment]::GetEnvironmentVariable('Path', 'User')
        if (($env:Path -split ';') -notcontains $Bin) { $env:Path += ";$Bin" }
    }

    function Agree([string]$question) {
        if ($env:LITHIFY_YES -eq '1') { Info "$question yes (LITHIFY_YES=1)"; return $true }
        $a = Read-Host "    $question [Y/n]"
        return ($a -eq '' -or $a -match '^[YyTt]')
    }

    # Returns winget's exit code, or $null without winget or when the answer is no.
    function Install-WithWinget([string]$id, [string]$what, [string[]]$extra = @(), [string]$note = '') {
        if (-not (Get-Command winget -ErrorAction SilentlyContinue)) { return $null }
        if ($note) { Info $note }
        if (-not (Agree "Install $what now with winget?")) { return $null }
        Say "installing $what (winget)"
        # From winget's own catalogue only: on a fresh Windows the Microsoft Store source often fails
        # ("The server certificate did not match", 0x8a15005e), and winget then refuses to choose
        # between sources even when the package is only in this one.
        $wingetArgs = @('install', '-e', '--id', $id, '--source', 'winget', '--silent', '--accept-package-agreements',
                        '--accept-source-agreements') + $extra
        # Straight to this window, where its progress bar draws in place (through the pipeline each
        # frame of it would be a line of its own)
        $p = Start-Process -FilePath (Get-Command winget).Source -ArgumentList $wingetArgs -NoNewWindow -PassThru
        $null = $p.Handle  # (keeps the exit code readable once it ends)
        $p.WaitForExit()
        $code = $p.ExitCode
        Update-Path
        return $code
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
        catch { Fail "could not download $url" @("$($_.Exception.Message)", 'check the internet connection, then run this again') }
    }

    # The files byte for byte as they are in the archive (LF line endings: the speaker runs some).
    function Expand-Zip([string]$zip, [string]$to) {
        $ProgressPreference = 'SilentlyContinue'
        try { Expand-Archive -LiteralPath $zip -DestinationPath $to -Force -ErrorAction Stop }
        catch { Fail "cannot unpack $zip" @("$($_.Exception.Message)") }
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
            Say "downloading uv (Astral's Python installer, about 20 MB) for $target"
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
                        Fail 'the uv download does not match its checksum' @('run this again in a few minutes (a new uv release may have come out meanwhile)')
                    }
                    Info 'the download does not match its checksum: once more'
                }
                Info 'checksum ok'
                Expand-Zip $zip (P $tmp 'x')
                if (-not (Test-Path -LiteralPath (P $tmp 'x' 'uv.exe'))) { Fail "uv-$target.zip holds no uv.exe" }
                New-Item -ItemType Directory -Force -Path $Tools -ErrorAction Stop | Out-Null
                Copy-Item -LiteralPath (P $tmp 'x' 'uv.exe') -Destination $uv -Force -ErrorAction Stop
            } finally {
                Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
            }
        }
        Say 'installing Python 3.12 (uv python install 3.12)'
        Invoke-Uv @('python', 'install', '--no-bin', '--no-registry', '3.12') | Out-Host
        if ($LASTEXITCODE -ne 0) { Fail 'uv could not install Python 3.12 (see above)' @('check the internet connection, then run this again') }
        $p = Find-PrivatePython
        if (-not $p) { Fail 'uv installed Python 3.12, but it does not start' @("remove $Tools, then run this again") }
        # (uv's download cache: not needed any more)
        Remove-Item -LiteralPath (P $Tools 'cache') -Recurse -Force -ErrorAction SilentlyContinue
        return $p
    }

    function Get-Python {
        $py = Find-Python
        if ($py) { Say "Python: $py"; return $py }
        $py = Find-PrivatePython
        if ($py) { Say "Python: $py (Lithify's private Python)"; return $py }
        Say 'Lithify needs Python 3.11 or newer, and this computer has none (or only an older one).'
        $rc = Install-WithWinget 'Python.Python.3.12' 'Python 3.12 from python.org (for this user, no administrator rights)' @('--scope', 'user')
        if ($null -ne $rc) {
            $py = Find-Python
            if ($py) { Say "Python: $py"; return $py }
            Warn "winget did not give this window a working Python (code $rc)"
        }
        Info "Lithify can install a private Python 3.12 just for itself, in ${Tools}:"
        Info 'no administrator rights, nothing else on this computer changes (about 50 MB to download).'
        if (-not (Agree 'Install the private Python?')) {
            Fail 'Python 3.11 or newer is needed' @('install it from https://www.python.org/downloads/ (tick "Add python.exe to PATH"), then run this again')
        }
        $py = Install-PrivatePython | Select-Object -Last 1
        Say "Python: $py (Lithify's private Python)"
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
            Fail "cannot copy Lithify into $to (robocopy: $LASTEXITCODE)" @('close what may use files there (lithify serve), then run this again')
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
        Fail "$url does not hold Lithify" @('set LITHIFY_ARCHIVE_URL to a .zip of Lithify, or install Git, then run this again')
    }

    function Get-Lithify {
        # The Lithify folder this file is in (installer\ is in it); none when it was pasted
        $here = if ($PSScriptRoot) { Split-Path -Parent $PSScriptRoot } else { '' }
        $copyFrom = $null
        if (Test-LithifyTree $here) {
            # A checkout run directly is used where it is; a downloaded folder (not a git checkout)
            # that Lithify-Windows.cmd runs is copied into $Dest, so it can be deleted afterwards.
            if ((Test-Path -LiteralPath (P $here '.git')) -or $env:LITHIFY_LAUNCHER -ne '1') {
                Say "using this checkout: $here"
                return $here
            }
            $copyFrom = $here
        }
        $git = Find-Git
        if (Test-Path -LiteralPath (P $Dest '.git')) {
            if (-not $git) {
                Fail "$Dest is a git checkout, but Git is not installed" @("install Git (https://git-scm.com/download/win), or remove $Dest to get a fresh copy, then run this again")
            }
            Say "updating Lithify in $Dest (git pull)"
            git -C $Dest pull --ff-only -q
            if ($LASTEXITCODE -ne 0) {
                Fail "git could not update $Dest (see above)" @('check the internet connection; if you changed files there, commit or undo the changes; then run this again')
            }
            return $Dest
        }
        if ((Test-Path -LiteralPath $Dest) -and -not (Test-LithifyTree $Dest) -and
            (Get-ChildItem -LiteralPath $Dest -Force -ErrorAction SilentlyContinue | Select-Object -First 1)) {
            Fail "$Dest is not empty, and it is not Lithify" @('set LITHIFY_HOME to another folder (or empty this one), then run this again')
        }
        if ($copyFrom) {
            Say "copying Lithify from $copyFrom to $Dest"
            Sync-Tree $copyFrom $Dest
            return $Dest
        }
        $stage = New-TempDir
        try {
            $new = $null
            if ($git) {
                Say "downloading Lithify with git into $Dest"
                # LF line endings exactly as published: the speaker runs some of these files.
                git clone -q --depth 1 --config core.autocrlf=false $Repo (P $stage 'lithify')
                if ($LASTEXITCODE -eq 0) { $new = P $stage 'lithify' } else { Warn "git could not download $Repo" }
            }
            if (-not $new) {
                $url = Get-ArchiveUrl
                if (-not $url) {
                    Fail "Git is not installed, and $Repo is not a GitHub address to download a zip from" @('install Git, or set LITHIFY_ARCHIVE_URL to a .zip of Lithify, then run this again')
                }
                Say "downloading Lithify into $Dest ($url)"
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
            if ($LASTEXITCODE -ne 0) { Fail 'the lithify command does not start:' @($version, "remove $dest, then run this again (or report it)") }
            $version = " ($version)"
        }
        Say "installed the command: $shim$version (new windows know it as `"lithify`")"
        return $shim
    }

    # ---- Git and Docker Desktop: they build Lithify for the speaker --------

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

    function Wait-Docker([int]$seconds) {
        Write-Host -NoNewline "    waiting for Docker Desktop to start (up to $([int]($seconds / 60)) minutes)"
        $deadline = (Get-Date).AddSeconds($seconds)
        while ((Get-Date) -lt $deadline) {
            Add-DockerPath
            if (Test-Docker) { Write-Host ' ready'; return $true }
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

    # How to start this installer again: the launcher it came from, or this file; none when pasted.
    function Get-AgainCommand {
        $launcher = $env:LITHIFY_LAUNCHER_FILE
        if ($launcher -and (Test-Path -LiteralPath $launcher)) {
            # Through cmd.exe: opened as a file, a downloaded launcher would bring up Windows'
            # "publisher could not be verified" question again, which was answered already.
            $cmd = P $env:SystemRoot 'System32' 'cmd.exe'
            return "`"$cmd`" /c `"`"$launcher`"`""
        }
        if ($PSCommandPath) {
            $ps = P $env:SystemRoot 'System32' 'WindowsPowerShell' 'v1.0' 'powershell.exe'
            return "`"$ps`" -NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`""
        }
        return $null
    }

    # The restart can be now: Windows then starts this installer again once the person signs in
    # (RunOnce: one time). Never without asking, not even with LITHIFY_YES: a restart closes
    # everything that is open.
    function Stop-ForRestart([string]$why) {
        $again = Get-AgainCommand
        if ($again -and $env:LITHIFY_YES -ne '1') {
            Say "Windows needs a restart $why"
            Info 'Lithify can restart it now and go on by itself once you sign in again.'
            $a = Read-Host '    Restart Windows now? Save your work in other programs first. [Y/n]'
            if ($a -eq '' -or $a -match '^[YyTt]') {
                $key = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\RunOnce'
                try {
                    if (-not (Test-Path $key)) { New-Item -Path $key -ErrorAction Stop | Out-Null }
                    Set-ItemProperty -Path $key -Name 'Lithify' -Value $again -ErrorAction Stop
                    & (P $env:SystemRoot 'System32' 'shutdown.exe') /r /t 20 /c 'Lithify: restarting Windows to finish the installation'
                    if ($LASTEXITCODE -eq 0) {
                        $State.Restarting = $true
                        Later 'Windows restarts in 20 seconds' @(
                            'Once you sign in again, Lithify goes on by itself in a window like this one.',
                            '(Windows may show a welcome window of WSL then, and Docker Desktop starting: close the',
                            'WSL window; Lithify waits for Docker Desktop.)',
                            'To stop the restart: shutdown /a (in a terminal); then restart later yourself.')
                    }
                    Remove-ItemProperty -Path $key -Name 'Lithify' -ErrorAction SilentlyContinue
                    Warn "Windows did not restart (code $LASTEXITCODE)"
                } catch [System.Management.Automation.RuntimeException] {
                    if ("$($_.Exception.Message)".StartsWith('==>')) { throw }
                    Warn "cannot restart Windows from here: $($_.Exception.Message)"
                }
            }
        }
        Later "Windows needs a restart $why" @(
            'Restart it (Start > Power > Restart). Afterwards double-click Lithify-Windows.cmd again (or run',
            'the same command again): it skips what is already done and goes on with Docker Desktop.')
    }

    # Returns when the answer is no, or when it did not work (Docker Desktop then says what it needs).
    function Install-Wsl {
        Say 'Docker Desktop needs WSL 2 (the Windows Subsystem for Linux, a part of Windows), and it is not set up yet'
        $wsl = Find-Wsl
        if (-not $wsl) {
            Fail 'this Windows has no WSL: Docker Desktop needs Windows 10 22H2 or Windows 11' @('update Windows (Settings > Windows Update), then run this again')
        }
        Info 'Installing it takes a few minutes: Windows asks for permission, and must restart afterwards.'
        if (-not (Agree 'Install WSL now?')) { return }
        $why = $null
        try {
            $p = Start-Process -FilePath $wsl -ArgumentList '--install', '--no-distribution' -Verb RunAs -Wait -PassThru -ErrorAction Stop
            if ($p.ExitCode -ne 0) { $why = "it ended with code $($p.ExitCode)" }
        } catch { $why = 'Windows did not get permission' }
        if (-not $why) { Stop-ForRestart 'to finish installing WSL.' }
        Warn "WSL was not installed ($why): Docker Desktop says what it needs when it starts"
        Info 'To install WSL yourself: wsl --install --no-distribution (in a terminal opened as administrator), then restart.'
    }

    function Confirm-Git {
        if (Find-Git) { return }
        Say 'Git is missing: the build downloads librespot''s source code with it'
        $rc = Install-WithWinget 'Git.Git' 'Git' @() 'Windows asks for permission.'
        if ($null -ne $rc -and (Find-Git)) { return }
        Fail 'Git is needed to build Lithify for the speaker' @('install it from https://git-scm.com/download/win (the default choices are fine), then run this installer again')
    }

    function Confirm-Docker {
        Add-DockerPath
        if (Test-Docker) { Say 'Docker is running'; return }
        $arch = if ((Get-Arch) -eq 'aarch64') { 'arm64' } else { 'amd64' }
        $link = "https://desktop.docker.com/win/main/$arch/Docker%20Desktop%20Installer.exe"
        $app = Find-DockerDesktop
        $fresh = $false
        if (-not $app -and -not (Get-Command docker -ErrorAction SilentlyContinue)) {
            Say 'Docker Desktop is not installed: Lithify is built for the speaker inside it (free for personal use)'
            $rc = Install-WithWinget 'Docker.DockerDesktop' 'Docker Desktop' @() 'Windows asks for permission; the download is about 600 MB.'
            if ($null -eq $rc) {
                Fail 'Docker Desktop is needed to build Lithify for the speaker' @("download it: $link", 'install it and start it once, then run this installer again')
            }
            if ($rc -eq $WingetRebootToFinish -or $rc -eq $WingetRebootInitiated -or $rc -eq $WingetRebootFirst) {
                Stop-ForRestart 'to finish installing Docker Desktop.'
            }
            if ($rc -eq $WingetCancelled) {
                Fail 'the installation of Docker Desktop was cancelled' @('run this again and allow it, or install it yourself:', $link)
            }
            if ($rc -ne 0 -and $rc -ne $WingetAlreadyInstalled) {
                Fail "winget could not install Docker Desktop (code $rc)" @("install it yourself: $link", 'then run this installer again')
            }
            $fresh = $true
            Add-DockerPath
            $app = Find-DockerDesktop
        }
        if (-not $app) {
            Later 'docker is installed, but its engine does not answer' @('start it (Docker Desktop, Rancher Desktop, ...), then run this installer again')
        }
        if (-not (Test-Wsl)) { Install-Wsl }
        Say 'starting Docker Desktop'
        if ($fresh) { Info 'Its first start takes a few minutes; it may offer a sign-in or a survey: "Skip" is fine.' }
        Info 'If it asks you to accept its terms (the Docker Subscription Service Agreement), click Accept.'
        Start-Process -FilePath $app
        if (Wait-Docker 180) { return }
        if (Test-RebootPending) { Stop-ForRestart 'to finish setting up Docker Desktop.' }
        Later 'Docker Desktop has not finished starting' @(
            'Look at its window: accept its terms if it asks, update WSL if it says so (wsl --update),',
            'and wait until it shows "Engine running". If it says you must be in the "docker-users" group,',
            'sign out of Windows and back in. Then run this installer again: it skips what is already done.')
    }

    # The published bundle versions.toml names (`lithify install` downloads it first).
    function Get-ReleaseUrl([string]$py, [string]$dest) {
        $code = "import sys, tomllib`nwith open(sys.argv[1], 'rb') as f:`n    print(tomllib.load(f).get('release', {}).get('url') or '')"
        $url = & $py -I -c $code (P $dest 'versions.toml') 2>$null | Select-Object -Last 1
        if ($LASTEXITCODE -ne 0) { return '' }
        return "$url"
    }

    function Confirm-BuildTools([string]$py, [string]$dest) {
        $bundle = P $Cache 'bundle'
        if (Test-Path -LiteralPath (P $bundle 'VERSIONS')) {
            Say "a prebuilt Lithify bundle is already on this computer ($bundle)"
            Info 'Docker Desktop and Git are not needed for this install.'
            Warn 'to build updates later ("Update everything" on the speaker''s page), this computer needs Docker Desktop and Git'
            return
        }
        $release = Get-ReleaseUrl $py $dest
        if ($release.StartsWith('https://')) {
            Say "Lithify's prebuilt bundle is published: the install downloads it ($release)"
            Info 'Docker Desktop and Git are needed only when that download fails (Lithify is built here then).'
            Add-DockerPath
            if (-not ((Test-Docker) -and (Find-Git))) {
                Warn 'to build updates later ("Update everything" on the speaker''s page), this computer needs Docker Desktop and Git'
            }
            return
        }
        Say 'checking Git and Docker Desktop: they build Lithify for the speaker on this computer (10-20 minutes the first time)'
        Confirm-Git
        Confirm-Docker
    }

    # ---- the speaker -------------------------------------------------------

    # The browser wizard, unless told otherwise or over SSH; "invalid choice": a Lithify from before it.
    function Use-Wizard([string]$shim) {
        if ($env:LITHIFY_HOST -or $env:LITHIFY_NO_WIZARD -eq '1' -or $env:SSH_CONNECTION) { return $false }
        $out = & $shim wizard --help 2>&1 | Out-String
        if ($LASTEXITCODE -eq 0) { return $true }
        if ($out -match 'invalid choice') { Info 'this Lithify has no browser wizard yet: on in this window' }
        else { Warn '"lithify wizard" does not start: on in this window' }
        return $false
    }

    function Install-OnSpeaker([string]$shim) {
        Info "Windows asks once to let the speaker reach this computer through the firewall ($Ports): allow it."
        if (Use-Wizard $shim) {
            # (after installing, the wizard also sets up the helper that keeps the speaker updatable)
            Say 'opening the Lithify wizard in your web browser: it finds the speaker, asks for its name, installs'
            Info 'A browser that starts for the first time shows its own welcome screens first: click through them.'
            & $shim wizard
            if ($LASTEXITCODE -ne 0) {
                Fail 'Lithify was not installed on the speaker (the wizard ended before that)' @('start it again: lithify wizard   (or in this window: lithify install)')
            }
        } else {
            Say 'installing Lithify on the speaker (in this window)'
            $hostArg = @()
            if ($env:LITHIFY_HOST) { $hostArg = @('--host', $env:LITHIFY_HOST) }
            & $shim install --reboot @hostArg
            if ($LASTEXITCODE -ne 0) { Fail 'the install did not finish (see above)' @('fix what it says, then run this installer again (or: lithify install)') }
            # The helper starts at logon (a scheduled task): the speaker's web page installs updates through it.
            & $shim serve --install-service
            if ($LASTEXITCODE -ne 0) { Warn 'to install updates from the speaker''s web page, keep "lithify serve" running on this computer' }
        }
        $page = & $shim ui 2>$null
        Write-Host ''
        Say 'done! Open Spotify and pick the speaker in its list of devices (the Spotify Connect icon).'
        if ($page) { Say "its page (settings, tests, updates): $page" }
    }

    $code = 0
    # git and Git Credential Manager never ask (a wrong address would wait for a password); put
    # back afterwards, for a window that pasted this script
    $savedGitEnv = @{ GIT_TERMINAL_PROMPT = $env:GIT_TERMINAL_PROMPT; GCM_INTERACTIVE = $env:GCM_INTERACTIVE }
    $env:GIT_TERMINAL_PROMPT = '0'
    $env:GCM_INTERACTIVE = 'never'
    try {
        # (TLS 1.2 for GitHub: old Windows PowerShell 5.1 setups offer only TLS 1.0 by default)
        try { [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12 } catch { Warn 'cannot turn on TLS 1.2: downloads may fail' }
        if ($env:LITHIFY_LAUNCHER -ne '1') { Write-Host "Lithify installer: Spotify Connect (librespot) for Lithe Audio speakers`n" }
        $py = Get-Python | Select-Object -Last 1
        $dest = Get-Lithify | Select-Object -Last 1
        $shim = Install-Command $py $dest | Select-Object -Last 1
        if ($env:LITHIFY_NO_SETUP -ne '1') {
            Confirm-BuildTools $py $dest
            Install-OnSpeaker $shim
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
    # Run as a file (Lithify-Windows.cmd, .\installer\install.ps1): its exit code. Pasted (irm | iex): no
    # exit, which would close the window; the code is in $LASTEXITCODE.
    if ($PSCommandPath) { exit $code }
    $global:LASTEXITCODE = $code
}
