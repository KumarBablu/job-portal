@echo off
start cmd /k "python "E:\job_agent_rebuilt-Final\job_agent_rebuilt - Final\run.py""
timeout /t 2 >nul
start http://127.0.0.1:5000