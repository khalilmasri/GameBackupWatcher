#!/usr/bin/env python3

import os
import shutil
import fnmatch
import threading
import time
import sys
from datetime import datetime
from PyQt5.QtWidgets import QApplication, QWidget, QVBoxLayout, QPushButton, QFileDialog, QLabel, QLineEdit, QSpinBox, QListWidget, QCheckBox
from PyQt5.QtCore import QThread, Qt
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import json

g_stop_watching = False

# Function to get the path to the configuration file
def get_config_file_path():
    if os.name == 'nt':  # Windows
        return os.path.join(os.environ['USERPROFILE'], 'Documents', 'backupwatcher.json')
    else:  # Linux
        return os.path.expanduser('~/backupwatcher.json')

# Function to load configuration from the file
def load_config():
    config_path = get_config_file_path()
    if os.path.exists(config_path):
        with open(config_path, 'r') as f:
            return json.load(f)
    else:
        return {
            "backup_dir": "",
            "src_dir": "",
            "filename_pattern": "*.sav",
            "timeout": 5
        }

# Function to save the current configuration to the file
def save_config(config):
    config_path = get_config_file_path()
    with open(config_path, 'w') as f:
        json.dump(config, f, indent=4)


# Handler for file system events to monitor changes in the source directory
class BackupHandler(FileSystemEventHandler):
    def __init__(self, backup_dir, timeout, file_list_widget, src_dir, filename_pattern, create_date_dir, parent=None):
        super().__init__()
        self.backup_dir = backup_dir
        self.timeout = timeout
        self.file_list_widget = file_list_widget
        self.src_dir = src_dir
        self.filename_pattern = filename_pattern
        self.create_date_dir = create_date_dir
        self.parent = parent
        self.current_file = None
        self.timer = None
        self.stop_requested = False

    def on_modified(self, event):
        self.handle_event(event)

    def on_created(self, event):
        self.handle_event(event)

    def on_moved(self, event):
        self.handle_event(event)
     
    def stop(self):
        self.stop_requested = True

    def handle_event(self, event):
        global g_stop_watching
        if g_stop_watching == True or self.stop_requested == True:
            return
        if fnmatch.fnmatch(os.path.basename(event.src_path), self.filename_pattern):
            if event.event_type in {"created", "modified", "moved"}:
                self.backup_file(event.src_path)

    def wait_for_next_timeout(self):
        global g_stop_watching
        if self.timeout == 0:
            return

        g_stop_watching = True
        time.sleep(self.timeout)
        g_stop_watching = False

    def backup_file(self, file_path):
        self.current_file = file_path
        self.backup_next()
        thread = threading.Thread(target=self.wait_for_next_timeout)
        thread.start()

    def backup_next(self):
        if self.current_file:
            file_path = self.current_file
            timestamp = ""
            try:
                self.parent.update_status("Creating a backup....")
                time.sleep(5)
                file_name = os.path.basename(file_path)
                if self.create_date_dir:
                    timestamp = datetime.now().strftime("%H-%M")
                else:
                    timestamp = datetime.now().strftime("%d-%m-%Y_%H-%M")
                
                date_folder = datetime.now().strftime("%d-%m-%Y") if self.create_date_dir else ""

                backup_path = os.path.join(self.backup_dir, date_folder)
                if self.create_date_dir and not os.path.exists(backup_path):
                    os.makedirs(backup_path)

                destination_file_name = f"{os.path.splitext(file_name)[0]}_{timestamp}{os.path.splitext(file_name)[1]}"
                destination_path = os.path.join(backup_path, destination_file_name)

                shutil.copy2(file_path, destination_path)
                self.parent.update_status(f"Backup created: {destination_file_name}")

                self.parent.add_to_backup_dict(destination_file_name, file_path)
            except Exception as e:
                try:
                    shutil.copytree(file_path, destination_path) 
                    self.parent.update_status(f"Backup created: {destination_file_name}")
                except Exception as e:
                    self.parent.update_status(f"Error backing up file: {e}")


class WatcherThread(QThread):
    def __init__(self, backup_handler, src_dir):
        super().__init__()
        self.backup_handler = backup_handler
        self.src_dir = src_dir

    def run(self):
        self.observer = Observer()
        self.observer.schedule(self.backup_handler, path=self.src_dir, recursive=False)
        self.observer.start()
        self.exec_()

    def stop(self):
        if self.observer:
            try:
                print("Stopping observer...")
                self.observer.stop()
                self.observer.join(timeout=5)
                print("Observer stopped.")
            except Exception as e:
                print(f"Error stopping observer: {e}")
        self.quit()  # Quit the thread's event loop
        self.wait()  # Ensure the thread is fully stopped

class BackupApp(QWidget):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("Backup Manager")
        self.setGeometry(200, 200, 400, 300)

        self.backup_dict = {}
        self.monitoring = False

        # Load the configuration from the file
        config = load_config()
        self.src_dir = config.get("src_dir", "")
        self.backup_dir = config.get("backup_dir", "")
        self.filename_pattern = config.get("filename_pattern", "*.sav")
        self.timeout = config.get("timeout", 5)

        self.initUI()

        # Pre-fill the UI with loaded values
        self.src_input.setText(self.src_dir)
        self.dest_input.setText(self.backup_dir)
        self.filename_pattern_input.setText(self.filename_pattern)
        self.timeout_input.setValue(self.timeout)

    def initUI(self):
        layout = QVBoxLayout()

        self.label = QLabel("Choose source directory (to watch):")
        layout.addWidget(self.label)

        self.src_input = QLineEdit(self)
        self.src_input.setReadOnly(True)
        layout.addWidget(self.src_input)

        self.select_src_button = QPushButton("Select Source Directory", self)
        self.select_src_button.clicked.connect(self.select_src_directory)
        layout.addWidget(self.select_src_button)

        self.dest_label = QLabel("Choose backup directory:")
        layout.addWidget(self.dest_label)

        self.dest_input = QLineEdit(self)
        self.dest_input.setReadOnly(True)
        layout.addWidget(self.dest_input)

        self.select_dest_button = QPushButton("Select Backup Directory", self)
        self.select_dest_button.clicked.connect(self.select_dest_directory)
        layout.addWidget(self.select_dest_button)

        self.timeout_label = QLabel("Backup Timeout (seconds):")
        layout.addWidget(self.timeout_label)

        self.timeout_input = QSpinBox(self)
        self.timeout_input.setRange(1, 9999)
        self.timeout_input.setValue(self.timeout)
        layout.addWidget(self.timeout_input)

        self.filename_pattern_label = QLabel("Enter the filename or pattern (e.g., *.sav):")
        layout.addWidget(self.filename_pattern_label)

        self.filename_pattern_input = QLineEdit(self)
        layout.addWidget(self.filename_pattern_input)

        self.date_folder_checkbox = QCheckBox("Create backup folder with today's date", self)
        self.date_folder_checkbox.setChecked(True)
        layout.addWidget(self.date_folder_checkbox)

        self.start_button = QPushButton("Start Watching", self)
        self.start_button.clicked.connect(self.start_backup_monitoring)
        layout.addWidget(self.start_button)

        self.stop_button = QPushButton("Stop Watching", self)
        self.stop_button.clicked.connect(self.stop_backup_monitoring)
        self.stop_button.setEnabled(False)
        layout.addWidget(self.stop_button)

        self.file_list_widget = QListWidget(self)
        layout.addWidget(self.file_list_widget)

        self.restore_button = QPushButton("Restore Backup", self)
        self.restore_button.clicked.connect(self.restore_backup)
        layout.addWidget(self.restore_button)

        # Add a label for showing the latest status
        self.status_label = QLabel("Status: Ready")
        self.status_label.setAlignment(Qt.AlignLeft)
        layout.addWidget(self.status_label)

        self.setLayout(layout)

        self.backup_handler = None
        self.watcher_thread = None
        
    def update_status(self, message):
        """Update the status label with the latest message."""
        print(f"{message}")
        self.status_label.setText(f"Status: {message}")

    def select_src_directory(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Source Directory")
        if folder:
            self.src_input.setText(folder)

    def select_dest_directory(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Backup Directory")
        if folder:
            self.dest_input.setText(folder)

    def start_backup_monitoring(self):
        src_dir = self.src_input.text()
        backup_dir = self.dest_input.text()
        timeout = self.timeout_input.value()
        filename_pattern = self.filename_pattern_input.text()
        create_date_dir = self.date_folder_checkbox.isChecked()

        if src_dir and backup_dir and filename_pattern:
            self.backup_handler = BackupHandler(backup_dir, timeout, self.file_list_widget, src_dir, filename_pattern, create_date_dir, self)
            self.watcher_thread = WatcherThread(self.backup_handler, src_dir)
            self.watcher_thread.start()
            self.monitoring = True
            self.start_button.setEnabled(False)
            self.stop_button.setEnabled(True)
            self.update_status("Monitoring started.")

            # Save updated configuration to file
            config = {
                "backup_dir": backup_dir,
                "src_dir": src_dir,
                "filename_pattern": filename_pattern,
                "timeout": timeout
            }
            save_config(config)

        else:
            self.update_status("Please select both the source and backup directories and enter a valid filename pattern.")

    def stop_backup_monitoring(self):
        if self.watcher_thread:
            try:
                print("1. Attempting to stop watcher thread...")
                self.watcher_thread.stop()
                print("2. Watcher thread stopped.")
                self.monitoring = False
                self.start_button.setEnabled(True)
                self.stop_button.setEnabled(False)
                print("Monitoring stopped.")
            except Exception as e:
                print(f"Error stopping watcher thread: {e}")

    def restore_backup(self):
        global g_stop_watching
        g_stop_watching = True
        selected_item = self.file_list_widget.currentItem()
        if selected_item:
            backup_file = selected_item.text()
            if backup_file in self.backup_dict:
                original_file = self.backup_dict[backup_file]
                try:
                    full_path = os.path.join(self.dest_input.text(), backup_file)
                    print(f"Restoring {backup_file} to {original_file}")
                    shutil.copy2(full_path, original_file)
                   
                except Exception as e:
                    self.update_status(f"Error restoring file: {e}")
            else:
                self.update_status("No backup found for the selected file.")
        else:
            self.update_status("Please select a backup file to restore.")
        time.sleep(5)
        g_stop_watching = False
        self.update_status(f"Restored {backup_file}. Watching again....")

    def add_to_backup_dict(self, destination_file_name, original_file_path):
        if self.date_folder_checkbox.isChecked():
            original_dest_file_name = destination_file_name
            destination_file_name = os.path.join(datetime.now().strftime("%d-%m-%Y"), original_dest_file_name)
            print(f"{destination_file_name}")
        self.backup_dict[destination_file_name] = original_file_path
        self.file_list_widget.addItem(destination_file_name)

    def closeEvent(self, event):
        if self.watcher_thread:
            self.watcher_thread.stop()
        event.accept()

if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = BackupApp()
    window.show()
    sys.exit(app.exec_())