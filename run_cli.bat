@echo off
title MusicLinker Interactive CLI Wizard
set PYTHONPATH=%~dp0src
py -m musiclinker.entrypoints.cli %*
pause