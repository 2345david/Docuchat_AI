#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
echo "=== Instalando DocuChat ==="
command -v python3 >/dev/null || { echo "Instala Python 3.10 o superior"; exit 1; }
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
echo
echo "Listo. Instala Ollama desde https://ollama.com y abre la app con ./iniciar_mac_linux.sh"
