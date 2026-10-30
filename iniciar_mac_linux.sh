#!/usr/bin/env bash
cd "$(dirname "$0")"
[ -x .venv/bin/python ] || { echo "Ejecuta primero ./instalar_mac_linux.sh"; exit 1; }
exec .venv/bin/python main.py
