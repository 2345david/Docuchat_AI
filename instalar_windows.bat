@echo off
chcp 65001 >nul
echo === Instalando DocuChat ===
where python >nul 2>nul || (echo No se encuentra Python. Instalalo desde https://www.python.org ^(marca "Add Python to PATH"^) & pause & exit /b 1)
python -m venv .venv || (echo Error creando el entorno virtual & pause & exit /b 1)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt || (echo Error instalando dependencias & pause & exit /b 1)
echo.
echo Listo. Recuerda instalar Ollama desde https://ollama.com
echo Para abrir la aplicacion, haz doble clic en iniciar_windows.bat
pause
