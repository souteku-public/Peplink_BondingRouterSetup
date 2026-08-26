@echo off
rem ============================================================
rem  ボンディング回線スループット計測 - かんたん起動 (Windows)
rem  このファイルをダブルクリックすると、日本語の対話メニューが
rem  立ち上がります。番号を選んでEnterを押すだけで計測できます。
rem ============================================================
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo Python が見つかりません。https://www.python.org/downloads/ から
    echo インストールしてください(インストール時に "Add Python to PATH" にチェック^)。
    pause
    exit /b 1
)

rem 初回のみ依存パッケージ(PyYAML)を入れる
python -c "import yaml" >nul 2>nul
if errorlevel 1 (
    echo 初回準備中です。しばらくお待ちください...
    python -m pip install --quiet -r requirements.txt
)

python run.py --wizard
pause
