@echo off
title MusicLinker Streamlit Analytics Dashboard
set PYTHONPATH=%~dp0src
py -m streamlit run src/musiclinker/entrypoints/streamlit_app.py