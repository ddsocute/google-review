@echo off
chcp 65001 >nul 2>&1
title Google Maps 餐廳評論 AI 分析器

echo ============================================
echo   Google Maps 餐廳評論 AI 分析器
echo ============================================
echo.

:: 檢查 Python 是否已安裝
python --version >nul 2>&1
if errorlevel 1 (
    echo [錯誤] 找不到 Python, 請先安裝 Python 3.9 以上版本
    echo 下載: https://www.python.org/downloads/
    echo.
    pause
    exit /b 1
)

:: 檢查 .env 是否存在, 不存在就從 .env.example 複製
if not exist ".env" (
    if exist ".env.example" (
        echo [設定] 首次啟動, 複製 .env.example 為 .env ...
        copy ".env.example" ".env" >nul
        echo [設定] 已建立 .env, 請確認裡面的 API Key 是否正確
        echo.
    ) else (
        echo [警告] 找不到 .env.example, 請手動建立 .env 檔案
        echo.
    )
)

:: 檢查虛擬環境是否存在, 不存在就建立
if not exist "venv\Scripts\activate.bat" (
    echo [安裝] 建立 Python 虛擬環境 ...
    python -m venv venv
    echo [安裝] 虛擬環境建立完成
    echo.
)

:: 啟用虛擬環境
call venv\Scripts\activate.bat

:: 安裝依賴套件
echo [安裝] 檢查並安裝依賴套件 ...
pip install -r requirements.txt -q
echo [安裝] 依賴套件已就緒
echo.

:: 啟動伺服器
echo ============================================
echo   伺服器啟動中 ...
echo   開啟瀏覽器前往: http://localhost:5000
echo   按 Ctrl+C 可停止伺服器
echo ============================================
echo.

start "" "http://localhost:5000/?v=20260225"
python app.py
