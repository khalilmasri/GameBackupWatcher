#!/bin/bash

SCRIPT_NAME="GameBackupWatcher.py"
BUILD_DIR="dist"
DESKTOP_SHORTCUT="$USERPROFILE/Desktop"
ICON_PATH="C:\\Users\\khali\\Documents\\GameBackupWatcher - Copy\\icon.ico" # Must be .ico
EXE_NAME="GameBackupWatcher.exe"

# Build with PyInstaller
build() {
    local debug_flag=$1
    local exe_name=$2
    pyinstaller --onefile $debug_flag --icon="$ICON_PATH" --name="${exe_name%.*}" "$SCRIPT_NAME"
    if [ $? -eq 0 ]; then
        echo "Build successful."
    else
        echo "Build failed. Exiting."
        exit 1
    fi
}

# Create Windows desktop shortcut with icon
create_shortcut() {
    local exe_name=$1
    local exe_path="$PWD\\$BUILD_DIR\\$exe_name"  # Use backslashes for Windows
    exe_path="${exe_path//\//\\}"                # Replace any remaining forward slashes
    local shortcut_path="$DESKTOP_SHORTCUT\\$exe_name.lnk"

    echo "Creating desktop shortcut at $shortcut_path..."

    powershell -NoProfile -Command "
    \$WScriptShell = New-Object -ComObject WScript.Shell;
    \$Shortcut = \$WScriptShell.CreateShortcut('$shortcut_path');
    \$Shortcut.TargetPath = '$exe_path';
    \$Shortcut.IconLocation = '$ICON_PATH';
    \$Shortcut.Save();
    "

    echo "Shortcut created with icon: $ICON_PATH"
}


# Example usage
build "--windowed" "$EXE_NAME"
create_shortcut "$EXE_NAME"
