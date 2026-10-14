@echo off
title MusicLinker Android APK Builder
echo ========================================================
echo   MusicLinker v3.0 — Native Android APK Builder
echo ========================================================
if not exist node_modules (
    echo [1/3] Installing Capacitor dependencies...
    call npm install
)
if not exist android\capacitor-android (
    echo [2/3] Syncing web assets into Android Capacitor project...
    call npx cap sync android
) else (
    call npx cap copy android
)
echo [3/3] Building APK via Gradle...
cd android
call gradlew.bat assembleDebug
cd ..
echo.
echo Done! APK generated at:
echo   android\app\build\outputs\apk\debug\app-debug.apk
pause