# download_model.ps1 — Télécharge le modèle Gemma 4 12B Q4 + MTP drafter
# Usage : py scripts/download_model.ps1
# ou     : powershell -ExecutionPolicy Bypass -File scripts/download_model.ps1

$ErrorActionPreference = "Stop"

$modelsDir = Join-Path $PSScriptRoot ".." "models"
if (-not (Test-Path $modelsDir)) {
    New-Item -ItemType Directory -Path $modelsDir -Force | Out-Null
    Write-Host "[OK] Dossier models/ cree : $modelsDir"
}

# Fichiers a telecharger depuis ggml-org/gemma-4-12B-it-GGUF
$files = @(
    @{ Name = "gemma-4-12B-it-Q4_0.gguf";   Size = "7.22 GB"; Url = "https://huggingface.co/ggml-org/gemma-4-12B-it-GGUF/resolve/main/gemma-4-12B-it-Q4_0.gguf" }
    @{ Name = "mtp-gemma-4-12B-it-Q4_0.gguf"; Size = "254 MB"; Url = "https://huggingface.co/ggml-org/gemma-4-12B-it-GGUF/resolve/main/mtp-gemma-4-12B-it-Q4_0.gguf" }
)

foreach ($f in $files) {
    $dest = Join-Path $modelsDir $f.Name
    if (Test-Path $dest) {
        Write-Host "[SKIP] $($f.Name) ($($f.Size)) deja present."
        continue
    }
    Write-Host "[DL] Telechargement de $($f.Name) ($($f.Size))..."
    Write-Host "     Source : $($f.Url)"
    Write-Host "     Dest   : $dest"
    Write-Host "     (Cela peut prendre plusieurs minutes selon votre connexion.)"

    # curl avec progression
    & curl.exe -L -o $dest --progress-bar $f.Url
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERR] Echec du telechargement de $($f.Name)."
        exit 1
    }
    Write-Host "[OK] $($f.Name) telecharge."
}

# ── Modèle d'embeddings RAG (embeddinggemma-300M QAT Q4_0, ~265 Mo, CPU) ──
$embedDir = Join-Path $PSScriptRoot ".." "models-embed"
New-Item -ItemType Directory -Path $embedDir -Force | Out-Null
$embedDest = Join-Path $embedDir "embeddinggemma-300M-qat-Q4_0.gguf"
if (Test-Path $embedDest) {
    Write-Host "[SKIP] embeddinggemma-300M-qat-Q4_0.gguf deja present."
} else {
    Write-Host "[DL] Telechargement de embeddinggemma-300M-qat-Q4_0.gguf (265 Mo)..."
    & curl.exe -L -f -o $embedDest --progress-bar "https://huggingface.co/ggml-org/embeddinggemma-300M-qat-q4_0-GGUF/resolve/main/embeddinggemma-300M-qat-Q4_0.gguf"
    if ($LASTEXITCODE -ne 0) { Write-Host "[ERR] Echec du telechargement."; exit 1 }
    Write-Host "[OK] embeddinggemma-300M-qat-Q4_0.gguf telecharge."
}

Write-Host ""
Write-Host "=== Modeles prets dans $modelsDir ==="
Get-ChildItem $modelsDir -Filter "*.gguf" | ForEach-Object {
    $sizeGB = [math]::Round($_.Length / 1GB, 2)
    Write-Host "  $($_.Name)  ($sizeGB GB)"
}
Write-Host ""
Write-Host "Prochaine etape : docker compose up -d llamacpp"
