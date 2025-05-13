@echo off
chcp 65001 > nul
setlocal enabledelayedexpansion

echo ===================================
echo Welcome to Hajimi Auto Setup
echo ===================================

:: Set variables
set PYTHON_VERSION=3.12.3
set PYTHON_URL=https://www.python.org/ftp/python/%PYTHON_VERSION%/python-%PYTHON_VERSION%-embed-amd64.zip
set PYTHON_ZIP=python-%PYTHON_VERSION%-embed-amd64.zip
set PYTHON_DIR=%~dp0python
set GET_PIP_URL=https://bootstrap.pypa.io/get-pip.py

:: Add Python to PATH at the beginning to ensure it's used
set PATH=%PYTHON_DIR%;%PYTHON_DIR%\Scripts;%PATH%

:: Check if Python is already installed
if exist "%PYTHON_DIR%\python.exe" (
    echo Python already installed, skipping installation...
) else (
    echo Downloading Python %PYTHON_VERSION%...
    powershell -Command "Invoke-WebRequest -Uri '%PYTHON_URL%' -OutFile '%PYTHON_ZIP%'"
    
    echo Extracting Python...
    powershell -Command "Expand-Archive -Path '%PYTHON_ZIP%' -DestinationPath '%PYTHON_DIR%' -Force"
    
    echo Removing downloaded zip...
    del "%PYTHON_ZIP%"
    
    :: Modify python312._pth file to enable imports
    echo Configuring Python environment...
    powershell -Command "(Get-Content '%PYTHON_DIR%\python312._pth') -replace '#import site', 'import site' | Set-Content '%PYTHON_DIR%\python312._pth'"
    
    :: Download and install pip
    echo Downloading and installing pip...
    powershell -Command "Invoke-WebRequest -Uri '%GET_PIP_URL%' -OutFile '%PYTHON_DIR%\get-pip.py'"
    "%PYTHON_DIR%\python.exe" "%PYTHON_DIR%\get-pip.py" --no-warn-script-location
    
    echo Python and pip installation complete!
)

:: Verify Python installation
echo Verifying Python installation...
"%PYTHON_DIR%\python.exe" --version
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: Python installation failed or not accessible.
    goto :error
)

:: Ensure Python Scripts directory exists in PATH
if not exist "%PYTHON_DIR%\Scripts" (
    echo Creating Scripts directory...
    mkdir "%PYTHON_DIR%\Scripts"
)

:: Create site-packages directory if it doesn't exist
if not exist "%PYTHON_DIR%\Lib\site-packages" (
    echo Creating site-packages directory...
    mkdir "%PYTHON_DIR%\Lib\site-packages"
)

:: Load environment variables
echo Loading environment variables...
if exist ".env" (
    for /f "tokens=1,2 delims==" %%a in (.env) do (
        set "%%a=%%b"
        echo Loaded: %%a
    )
    echo Environment variables loaded successfully!
) else (
    echo Warning: .env file not found
)

:: Check if requirements.txt exists
if not exist "requirements.txt" (
    echo Creating requirements.txt file...
    (
        echo fastapi==0.104.1
        echo uvicorn==0.23.2
        echo pydantic==2.4.2
        echo starlette==0.27.0
        echo python-multipart==0.0.6
        echo jinja2==3.1.2
    ) > requirements.txt
    echo Created requirements.txt with basic dependencies.
)

:: Install dependencies directly in the embedded Python
echo Installing dependencies...

:: Upgrade pip first
echo Upgrading pip...
"%PYTHON_DIR%\python.exe" -m pip install --upgrade pip

:: Install key packages directly first
echo Installing key packages...
"%PYTHON_DIR%\python.exe" -m pip install fastapi uvicorn
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: Failed to install key packages.
    goto :error
)

:: Install all dependencies
echo Installing all dependencies from requirements.txt...
"%PYTHON_DIR%\python.exe" -m pip install -r requirements.txt
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: Failed to install all dependencies.
    goto :error
)

:: Verify installation
echo Verifying package installation...
"%PYTHON_DIR%\python.exe" -c "import fastapi; import uvicorn; print('Packages verified!')"
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: Package installation failed.
    goto :error
)

echo All dependencies installed successfully!

:: Start the application with delayed browser launch
echo Starting Hajimi application...

:: Start the application in a separate process
start /b cmd /c "%PYTHON_DIR%\python.exe" -m uvicorn app.main:app --host 0.0.0.0 --port 7860

:: Wait for the server to initialize (5 seconds)
echo Waiting for server to initialize...
timeout /t 5 /nobreak > nul

:: Open browser after delay
echo Opening browser...
start "" http://127.0.0.1:7860

:: Wait for user to close the application
echo.
echo Press Ctrl+C to stop the server when finished.
pause > nul
goto :end

:error
echo.
echo An error occurred during setup. Please check the messages above.
pause > nul

:end
endlocal
