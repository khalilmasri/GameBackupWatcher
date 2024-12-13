#!/bin/bash

# Define the script file
SCRIPT_NAME="GameBackupWatcher.py"
BUILD_DIR="dist"
DESKTOP_SHORTCUT="$HOME/Desktop/"
ICON_PATH="" # Optional: path to an icon for the shortcut

# Helper function to create a desktop shortcut
create_shortcut() {
    local exe_name=$1
    echo "Creating desktop shortcut..."
# Create or replace the desktop shortcut
    if [ -f "$DESKTOP_SHORTCUT" ]; then
        echo "Shortcut exists. Replacing it."
    fi
    cp ./$BUILD_DIR/$exe_name $DESKTOP_SHORTCUT/$exe_name
    echo "Shortcut created: $DESKTOP_SHORTCUT"
}

# Helper function to build with PyInstaller
build() {
    local debug_flag=$1
    local exe_name=$2
    echo "Building with debug flag: $debug_flag..."
    pyinstaller --onefile $debug_flag --name="$exe_name" $SCRIPT_NAME
    if [ $? -eq 0 ]; then
        echo "Build successful."
    else
        echo "Build failed. Exiting."
        exit 1
    fi
}

# Process command-line arguments
while [[ "$#" -gt 0 ]]; do
    case $1 in
        -d|--debug)
            EXE_NAME="GameBackupWatcher_debug"  # Name for debug version
            build "" "$EXE_NAME"
            create_shortcut "$EXE_NAME"
            ;;
        -n|--no-debug)
            EXE_NAME="GameBackupWatcher"  # Name for non-debug version
            build "--windowed" "$EXE_NAME"
            create_shortcut "$EXE_NAME"
            ;;
        -a|--all)
            EXE_NAME="GameBackupWatcher_debug"
            build "" "$EXE_NAME"
            EXE_NAME="GameBackupWatcher"
            build "--windowed" "$EXE_NAME"
            create_shortcut "$EXE_NAME"
            ;;
        *|"")
            echo "Unknown option: $1"
            echo "Usage: $0 [--debug | -d] [--no-debug | -n] [--all | -a]"
            exit 1
            ;;
    esac
    shift
done
