@echo off
:: kb.bat — run the Knowledge Base CLI from any directory (Windows)
::
:: Usage:
::   kb init C:\Users\you\my-research --name "My Research"
::   cd C:\Users\you\my-research && kb ingest
::   cd C:\Users\you\my-research && kb compile-llm
::
:: Add this script's directory to PATH to use 'kb' from anywhere.

python "%~dp0builder\src\cli.py" %*
