param(
    [Parameter(Mandatory)][string]$OutputDir,
    [string]$Python = 'python.exe',
    [string]$Makensis = 'makensis.exe'
)
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$destination = [IO.Path]::GetFullPath($OutputDir)
$stage = Join-Path $destination ('build-' + [guid]::NewGuid().ToString('N').Substring(0,8))
$payload = Join-Path $stage 'Research Hub'
[IO.Directory]::CreateDirectory($payload) | Out-Null
if (-not $env:NUGET_PACKAGES) { $env:NUGET_PACKAGES = Join-Path $destination 'nuget' }

Push-Location -LiteralPath $repo
try {
    & $Python -m PyInstaller --noconfirm --onedir --name ResearchHubBackend --noupx --optimize 1 --paths . --distpath "$stage/frozen" --workpath "$stage/obj" --specpath "$stage/spec" --add-data "$repo/static:static" --add-data "$repo/hub.example.toml:." --add-data "$repo/prompts:prompts" --collect-data tzdata --collect-data mammoth --collect-submodules uvicorn tools/backend_entry.py
    if ($LASTEXITCODE) { throw 'Bundling the Python backend failed.' }

    dotnet publish desktop/ResearchHub.csproj --nologo -c Release -r win-x64 --self-contained true -o $payload
    if ($LASTEXITCODE) { throw 'Publishing the native window failed.' }
    Move-Item -LiteralPath "$stage/frozen/ResearchHubBackend" -Destination "$payload/Backend"
    Copy-Item -LiteralPath @('LICENSE','README.md','THIRD_PARTY.md') -Destination $payload
    & $Python tools/release_payload.py $payload --nsis $Makensis
    if ($LASTEXITCODE) { throw 'Preparing the release payload failed.' }

    & $Python tools/package_windows.py $payload (Join-Path $destination 'release') --nsis $Makensis
    if ($LASTEXITCODE) { throw 'Creating Windows release archives failed.' }
} finally { Pop-Location }
