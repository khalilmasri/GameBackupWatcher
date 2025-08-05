#!/usr/bin/env python3

import os
import shutil
import fnmatch
import threading
import time
import sys
from datetime import datetime
from PyQt5.QtWidgets import QApplication, QWidget, QVBoxLayout, QPushButton, QFileDialog, QLabel, QLineEdit, QSpinBox, QListWidget, QCheckBox, QAbstractItemView
from PyQt5.QtCore import QThread, Qt, QTimer, QTime
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
            config = json.load(f)
            # Ensure keep_on_top defaults to True if not present
            if 'keep_on_top' not in config:
                config['keep_on_top'] = True
            return config
    else:
        return {
            "backup_dir": "",
            "src_dir": "",
            "filename_pattern": "*.sav",
            "timeout": 5,
            "keep_on_top": True
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
        if g_stop_watching or self.stop_requested:
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
            try:
                self.parent.log("Creating a backup....")
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
                self.parent.log(f"Backup created: {destination_file_name}")
                self.parent.add_to_backup_dict(destination_file_name, file_path)
            except Exception as e:
                try:
                    shutil.copytree(file_path, destination_path)
                    self.parent.log(f"Backup created: {destination_file_name}")
                except Exception as e:
                    self.parent.log(f"Error backing up file: {e}")

class WatcherThread(QThread):
    def __init__(self, backup_handler, src_dir, parent=None):
        super().__init__()
        self.backup_handler = backup_handler
        self.src_dir = src_dir
        self.parent = parent

    def run(self):
        self.observer = Observer()
        self.observer.schedule(self.backup_handler, path=self.src_dir, recursive=False)
        self.observer.start()
        self.exec_()

    def stop(self):
        if self.observer:
            try:
                self.parent.log("Stopping observer...")
                self.observer.stop()
                self.observer.join(timeout=5)
                self.parent.log("Observer stopped.")
            except Exception as e:
                self.parent.log(f"Error stopping observer: {e}")
        self.quit()
        self.wait()

class BackupApp(QWidget):
    def __init__(self):
        super().__init__()

        self.log_file_path = os.path.join(os.getcwd(), "backup_manager.log")
        with open(self.log_file_path, 'w') as f:
            f.write("=== Backup Manager Started ===\n")

        self.setWindowTitle("Backup Manager")
        self.setGeometry(200, 200, 400, 817)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_title_with_time)
        self.timer.start(1000)

        self.backup_dict = {}
        self.monitoring = False

        self.config = load_config()
        self.src_dir = self.config.get("src_dir", "")
        self.backup_dir = self.config.get("backup_dir", "")
        self.filename_pattern = self.config.get("filename_pattern", "*.sav")
        self.timeout = self.config.get("timeout", 5)

        self.initUI()

        self.src_input.setText(self.src_dir)
        self.dest_input.setText(self.backup_dir)
        self.filename_pattern_input.setText(self.filename_pattern)
        self.timeout_input.setValue(self.timeout)

        if self.config.get("keep_on_top", True):
            self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        else:
            self.setWindowFlag(Qt.WindowStaysOnTopHint, False)

        if self.src_dir and self.backup_dir and self.filename_pattern:
            QTimer.singleShot(500, self.start_backup_monitoring)


    def toggle_on_top(self, state):
        if state == Qt.Checked:
            self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
            self.log("Window set to stay on top.")
            self.config['keep_on_top'] = True
        else:
            self.setWindowFlag(Qt.WindowStaysOnTopHint, False)
            self.log("Window will no longer stay on top.")
            self.config['keep_on_top'] = False

        save_config(self.config)  # Save current config immediately

        self.show()  # Re-apply flags, required to update window


    def update_title_with_time(self):
        current_time = QTime.currentTime().toString("HH:mm:ss")
        self.setWindowTitle(f"Backup Manager - {current_time}")

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

        self.log_label = QLabel("Backups:")
        layout.addWidget(self.log_label)
        self.file_list_widget = QListWidget(self)
        layout.addWidget(self.file_list_widget)

        self.restore_button = QPushButton("Restore Backup", self)
        self.restore_button.clicked.connect(self.restore_backup)
        layout.addWidget(self.restore_button)

        self.log_label = QLabel("Logs:")
        layout.addWidget(self.log_label)

        self.log_widget = QListWidget(self)
        self.log_widget.setMinimumHeight(100)
        layout.addWidget(self.log_widget)

        self.clear_log_button = QPushButton("Clear Logs", self)
        self.clear_log_button.clicked.connect(self.clear_logs)
        layout.addWidget(self.clear_log_button)

        self.keep_on_top_checkbox = QCheckBox("Keep window on top", self)
        self.keep_on_top_checkbox.setChecked(self.config.get("keep_on_top", True))  # Load default from config
        self.keep_on_top_checkbox.stateChanged.connect(self.toggle_on_top)
        layout.addWidget(self.keep_on_top_checkbox)

        self.setLayout(layout)

        self.backup_handler = None
        self.watcher_thread = None

    def log(self, message):
        timestamp = datetime.now().strftime("[%H:%M:%S]")
        full_msg = f"{timestamp} {message}"

        # Add the message to the GUI log list
        self.log_widget.addItem(full_msg)

        # Keep only the last 10 log messages visible in the widget
        while self.log_widget.count() > 10:
            self.log_widget.takeItem(0)

        # Scroll to the newest item
        last_index = self.log_widget.count() - 1
        if last_index >= 0:
            self.log_widget.scrollToItem(self.log_widget.item(last_index), QAbstractItemView.PositionAtBottom)

        # Append the log message to the log file
        try:
            # Append to log file
            with open(self.log_file_path, 'a') as f:
                f.write(full_msg + "\n")
        except Exception as e:
            print(f"Failed to write log to file: {e}")

        # Also print to console
        print(full_msg)


    def clear_logs(self):
        self.log_widget.clear()

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
            self.watcher_thread = WatcherThread(self.backup_handler, src_dir, self)
            self.watcher_thread.start()
            self.monitoring = True
            self.start_button.setEnabled(False)
            self.stop_button.setEnabled(True)
            self.log("Monitoring started.")

            config = {
                "backup_dir": backup_dir,
                "src_dir": src_dir,
                "filename_pattern": filename_pattern,
                "timeout": timeout,
                "keep_on_top": self.keep_on_top_checkbox.isChecked()
            }
            save_config(config)
            self.config = config
        else:
            self.log("Please select both the source and backup directories and enter a valid filename pattern.")

    def stop_backup_monitoring(self):
        if self.watcher_thread:
            try:
                self.log("Attempting to stop watcher thread...")
                self.watcher_thread.stop()
                self.log("Watcher thread stopped.")
                self.monitoring = False
                self.start_button.setEnabled(True)
                self.stop_button.setEnabled(False)
                self.log("Monitoring stopped.")
            except Exception as e:
                self.log(f"Error stopping watcher thread: {e}")

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
                    self.log(f"Restoring {backup_file} to {original_file}")
                    shutil.copy2(full_path, original_file)
                    self.log(f"Restored {backup_file}. Watching again....")
                    time.sleep(1)
                except Exception as e:
                    self.log(f"Error restoring file: {e}")
            else:
                self.log("No backup found for the selected file.")
        else:
            self.log("Please select a backup file to restore.")
        g_stop_watching = False


    def add_to_backup_dict(self, destination_file_name, original_file_path):
        if self.date_folder_checkbox.isChecked():
            original_dest_file_name = destination_file_name
            destination_file_name = os.path.join(datetime.now().strftime("%d-%m-%Y"), original_dest_file_name)
            self.log(f"{destination_file_name}")
        self.backup_dict[destination_file_name] = original_file_path
        self.file_list_widget.addItem(destination_file_name)
        self.file_list_widget.scrollToBottom()

    def closeEvent(self, event):
        if self.watcher_thread:
            self.watcher_thread.stop()
        event.accept()

if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = BackupApp()
    window.show()
    sys.exit(app.exec_())