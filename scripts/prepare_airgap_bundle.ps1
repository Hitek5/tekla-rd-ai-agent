param(
    [string]$OutputDir = "airgap-bundle",
    [string]$Python = "python",
    [string[]]$DockerImages = @(
        "qdrant/qdrant:v1.12.1",
        "ollama/ollama:latest",
        "nginx:1.27-alpine"
    ),
    [string[]]$GitRepos = @(
        "https://github.com/TrimbleSolutionsCorporation/TSOpenAPIExamples.git",
        "https://github.com/teknovizier/tekla_mcp_server.git",
        "https://github.com/osc-bouw/TeklaMCP.git"
    )
)

$ErrorActionPreference = "Stop"

New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
New-Item -ItemType Directory -Force -Path "$OutputDir\docker" | Out-Null
New-Item -ItemType Directory -Force -Path "$OutputDir\python\wheels" | Out-Null
New-Item -ItemType Directory -Force -Path "$OutputDir\git" | Out-Null
New-Item -ItemType Directory -Force -Path "$OutputDir\models" | Out-Null
New-Item -ItemType Directory -Force -Path "$OutputDir\checksums" | Out-Null

$DockerImages | Set-Content -Encoding UTF8 "$OutputDir\docker\images.txt"

foreach ($image in $DockerImages) {
    Write-Host "Pulling Docker image $image"
    docker pull $image
    $safeName = ($image -replace "[/:]", "_")
    docker save $image -o "$OutputDir\docker\$safeName.tar"
}

Write-Host "Downloading Python wheels"
& $Python -m pip download --dest "$OutputDir\python\wheels" ".[dev,rag]"
& $Python -m pip freeze | Set-Content -Encoding UTF8 "$OutputDir\python\requirements.lock.txt"

foreach ($repo in $GitRepos) {
    $repoName = [System.IO.Path]::GetFileNameWithoutExtension($repo)
    $target = "$OutputDir\git\$repoName.bundle"
    $temp = Join-Path $env:TEMP "airgap-$repoName"
    if (Test-Path $temp) {
        Remove-Item -LiteralPath $temp -Recurse -Force
    }
    git clone --mirror $repo $temp
    git -C $temp bundle create $target --all
    Remove-Item -LiteralPath $temp -Recurse -Force
}

Write-Host "Writing checksums"
Get-ChildItem -LiteralPath $OutputDir -Recurse -File |
    Where-Object { $_.FullName -notlike "*\checksums\SHA256SUMS" } |
    ForEach-Object {
        $hash = Get-FileHash -Algorithm SHA256 -LiteralPath $_.FullName
        "$($hash.Hash)  $($_.FullName.Substring((Resolve-Path $OutputDir).Path.Length + 1))"
    } | Set-Content -Encoding ASCII "$OutputDir\checksums\SHA256SUMS"

Write-Host "Air-gap bundle prepared in $OutputDir"

