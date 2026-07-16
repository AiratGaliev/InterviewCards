@echo off

if not exist .venv (
    echo Creating .venv directory...
    python.exe -m venv .venv
    call .venv\Scripts\activate
    echo Installing dependencies...
    python.exe -m pip install --upgrade pip setuptools wheel
    pip install -r requirements.txt
    if errorlevel 1 (
        echo Error happens. Deleting .venv directory...
        rmdir /s /q .venv
        exit /b
    )
) else (
    call .venv\Scripts\activate
)
python.exe start_cli.py %*
deactivate