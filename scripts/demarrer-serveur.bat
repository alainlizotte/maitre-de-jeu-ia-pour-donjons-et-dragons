@echo off
rem Demarre le serveur D&D en arriere-plan (docker compose -d).
rem Aucune fenetre de terminal ne reste ouverte ; le conteneur redemarre
rem automatiquement avec Docker (restart: unless-stopped).
rem Port par defaut : 8123 (surcharger avec DND35_PORT, cf. docker-compose.yml).

cd /d "%~dp0.."
docker compose up -d --build
if errorlevel 1 (
    echo.
    echo Echec du demarrage. Verifiez que Docker Desktop est lance.
    pause
    exit /b 1
)
echo.
echo Serveur lance en arriere-plan : http://localhost:8123
timeout /t 3 >nul
