@echo off
title Last.fm Music Linker (Web Dashboard)
cd /d "%~dp0"
py -m streamlit run app.py
pause
