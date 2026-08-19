@echo off
rem Arrete le serveur D&D (conteneur detached). Les volumes (parties,
rem fiches, ChromaDB) sont conserves.

cd /d "%~dp0.."
docker compose down
echo.
echo Serveur arrete.
timeout /t 3 >nul
