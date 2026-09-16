# Sourced by setup-windows.ps1; importing this file has no side effects.
function Test-AkiNodeDirectory {
    param([string]$Directory)
    if (-not (Test-Path -LiteralPath (Join-Path $Directory 'node.exe')) -or
        -not (Test-Path -LiteralPath (Join-Path $Directory 'npx.cmd'))) { return $false }
    try {
        $nodeVersion = & (Join-Path $Directory 'node.exe') --version 2>$null
        return ($LASTEXITCODE -eq 0 -and [version]($nodeVersion.Trim().TrimStart('v')) -ge [version]'22.13.0')
    } catch { return $false }
}

function Enable-AkiNodeDirectory {
    param([string]$Directory, [switch]$SkipUserPath)
    # Updating this process lets setup continue without restarting the terminal.
    $env:Path = $Directory + ';' + $env:Path
    if (-not $SkipUserPath) {
        $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
        if (@($userPath -split ';') -notcontains $Directory) {
            [Environment]::SetEnvironmentVariable('Path', ($Directory + ';' + $userPath), 'User')
        }
    }
}

function Ensure-AkiNode {
    param(
        [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA 'Programs\NodeJS'),
        [switch]$SkipUserPath
    )
    $existing = Get-Command node.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($existing -and (Test-AkiNodeDirectory (Split-Path $existing.Source -Parent))) {
        Write-Host ('Using Node.js: ' + $existing.Source)
        return
    }
    # Pin the bootstrap release for reproducibility; do not silently choose Current.
    $version = 'v24.21.0'
    $architecture = $env:PROCESSOR_ARCHITEW6432
    if (-not $architecture) { $architecture = $env:PROCESSOR_ARCHITECTURE }
    switch ($architecture.ToUpperInvariant()) {
        'AMD64' { $arch = 'x64' }
        'ARM64' { $arch = 'arm64' }
        default { throw "Automatic Node.js installation supports Windows x64/ARM64; found $architecture." }
    }
    $name = "node-$version-win-$arch"
    $destination = Join-Path $InstallRoot $name
    if (Test-AkiNodeDirectory $destination) {
        Enable-AkiNodeDirectory $destination -SkipUserPath:$SkipUserPath
        Write-Host ('Using existing user installation: ' + $destination)
        return
    }
    if (Test-Path -LiteralPath $destination) {
        throw "Incomplete Node.js installation at $destination. Inspect or rename it before retrying; setup will not overwrite it."
    }
    Write-Host "Node.js 22.13+ with npx was not found. Installing official Node.js $version ($arch) for this user."
    $stage = Join-Path $InstallRoot ('.install-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $stage -Force | Out-Null
    $archiveName = "$name.zip"
    $archive = Join-Path $stage $archiveName
    $baseUrl = "https://nodejs.org/dist/$version"
    # Windows PowerShell 5.1 may otherwise negotiate an obsolete TLS default.
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    try {
        Invoke-WebRequest -UseBasicParsing -Uri "$baseUrl/$archiveName" -OutFile $archive
        $checksums = (Invoke-WebRequest -UseBasicParsing -Uri "$baseUrl/SHASUMS256.txt").Content
        if ($checksums -is [byte[]]) { $checksums = [Text.Encoding]::UTF8.GetString($checksums) }
        $pattern = '(?m)^([a-fA-F0-9]{64})\s+' + [regex]::Escape($archiveName) + '\s*$'
        $match = [regex]::Match($checksums, $pattern)
        if (-not $match.Success -or (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ne $match.Groups[1].Value) {
            throw 'Official SHA256 verification failed; downloaded Node.js will not be executed.'
        }
        Expand-Archive -LiteralPath $archive -DestinationPath $stage
        $extracted = Join-Path $stage $name
        if (-not (Test-AkiNodeDirectory $extracted)) { throw 'Downloaded Node.js failed its version/npx check.' }
        # Move only the newly downloaded directory inside the named installation root.
        Move-Item -LiteralPath $extracted -Destination $destination
        Enable-AkiNodeDirectory $destination -SkipUserPath:$SkipUserPath
        Remove-Item -LiteralPath $archive
        Remove-Item -LiteralPath $stage
        Write-Host ('Installed Node.js at ' + $destination)
        if (-not $SkipUserPath) { Write-Host 'Existing terminals may need to be reopened to see the updated user PATH.' }
    } catch {
        throw "Node.js setup failed: $($_.Exception.Message) Download files, if any, remain at $stage. Check network access to nodejs.org and rerun Setup-AKI.cmd."
    }
}
