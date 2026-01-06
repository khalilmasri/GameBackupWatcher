#!/usr/bin/env python3
import os, sys, shutil, fnmatch, threading, time, json, gc, re
from datetime import datetime
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, 
    QFileDialog, QLabel, QLineEdit, QSpinBox, QDoubleSpinBox, QCheckBox, 
    QTableWidget, QTableWidgetItem, QAbstractItemView, QHeaderView,
    QListWidget, QStackedWidget, QListWidgetItem, QMessageBox, QInputDialog
)
from PyQt5.QtCore import Qt, QObject, pyqtSignal, QSize
from PyQt5.QtGui import QPixmap
from PyQt5 import QtGui
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from PIL import ImageGrab

# ---------------- Global ---------------- #
g_stop_watching = False
g_version = "2.1.0"

# ---------------- Config ---------------- #
def get_config_file_path():
    if os.name == 'nt':  # Windows
        return os.path.join(os.environ['USERPROFILE'], 'Documents', 'backupwatcher.json')
    else:
        return os.path.expanduser('~/backupwatcher.json')

def load_config():
    path = get_config_file_path()
    if os.path.exists(path):
        with open(path, 'r') as f:
            return json.load(f)
    return {
        "backup_dir": "",
        "src_dir": "",
        "filename_pattern": "*.sav",
        "timeout": 5,
        "keep_on_top": True,
        "dark_mode": False,
        "watch_filename_changes": False,
        "max_logs": 500,
        "num_previous_backups": 10,  # default number of previous backups to load
        "screenshot_offset": 0.5     # default screenshot delay in seconds
    }

def save_config(config):
    path = get_config_file_path()
    with open(path, 'w') as f:
        json.dump(config, f, indent=4)

# ---------------- Image Overlay ---------------- #
class ImageOverlay(QWidget):
    def __init__(self, parent, img_path):
        super().__init__(parent)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet("background-color: rgba(0, 0, 0, 180);")
        self.setGeometry(parent.rect())   # cover parent only

        # Centered image
        self.label = QLabel(self)
        pixmap = QPixmap(img_path)
        if not pixmap.isNull():
            scaled = pixmap.scaled(600, 450, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.label.setPixmap(scaled)
            self.label.adjustSize()

            x = (self.width() - self.label.width()) // 2
            y = (self.height() - self.label.height()) // 2
            self.label.move(x, y)

    def mousePressEvent(self, event):
        self.close()

# ---------------- Hover Thumbnail ---------------- #
class HoverThumbnail(QLabel):
    def __init__(self, img_path, main_window=None):
        super().__init__()
        self.img_path = img_path
        self.main_window = main_window

        self.setFixedSize(48,48)

        # Only try to load pixmap if file exists and is a file
        if img_path and os.path.isfile(img_path):
            pixmap = QPixmap(img_path)
            pixmap = pixmap.scaled(48, 48, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
            self.setPixmap(pixmap)
        else:
            self.setText("️⛔️")

    def mousePressEvent(self, event):
        if self.img_path and os.path.isfile(self.img_path) and self.main_window:
            overlay = ImageOverlay(self.main_window, self.img_path)
            overlay.show()
            overlay.raise_()

# ---------------- Backup Handler ---------------- #
class BackupHandler(QObject, FileSystemEventHandler):
    backup_done = pyqtSignal(str, str, str)  # filename, original path, screenshot

    def __init__(self, parent, backup_dir, timeout, src_dir, filename_pattern, create_date_dir, screenshot_offset=0.5, watch_filename_changes=False):
        super().__init__()
        self.parent = parent
        self.backup_dir = backup_dir
        self.timeout = int(timeout)
        self.src_dir = src_dir
        self.filename_pattern = filename_pattern
        self.create_date_dir = create_date_dir
        self.screenshot_offset = float(screenshot_offset)
        self.watch_filename_changes = bool(watch_filename_changes)
        # map of target file -> last known mtime to detect changes
        self._prev_mtimes = {}
        self.stop_requested = False
        # initialize previous mtime for all matching files (literal or wildcard patterns)
        if self.watch_filename_changes and os.path.isdir(self.src_dir):
            try:
                for root, _, files in os.walk(self.src_dir):
                    for f in files:
                        if fnmatch.fnmatch(f, self.filename_pattern):
                            p = os.path.join(root, f)
                            try:
                                self._prev_mtimes[p] = os.path.getmtime(p)
                            except Exception:
                                pass
            except Exception:
                pass

    def wait_for_next_timeout(self):
        global g_stop_watching
        if self.timeout == 0:
            g_stop_watching = False
            return
        g_stop_watching = True
        time.sleep(self.timeout)
        g_stop_watching = False

    def on_modified(self, event):
        self.handle_event(event)

    def on_created(self, event):
        self.handle_event(event)

    def on_moved(self, event):
        self.handle_event(event)

    def stop(self):
        self.stop_requested = True

    def wait_for_stable_file(self, path, wait_time=1, retries=3):
        stable_count = 0
        last_size = -1
        while stable_count < retries:
            if os.path.isfile(path):
                size = os.path.getsize(path)
            elif os.path.isdir(path):
                size = sum(os.path.getsize(os.path.join(dp, f)) for dp, dn, filenames in os.walk(path) for f in filenames)
            else:
                break
            if size == last_size:
                stable_count += 1
            else:
                stable_count = 0
                last_size = size
            time.sleep(wait_time)

    def handle_event(self, event):
        global g_stop_watching
        if g_stop_watching:
            return
        if self.stop_requested or not os.path.exists(event.src_path):
            return
        basename = os.path.basename(event.src_path)
        if fnmatch.fnmatch(basename, self.filename_pattern):
            # If configured to watch for filename changes, trigger full-source backup when a matching file changes
            if self.watch_filename_changes:
                # Use the actual event path as the target (handles files in subfolders)
                target_path = event.src_path
                try:
                    if os.path.exists(target_path) and os.path.isfile(target_path):
                        mtime = os.path.getmtime(target_path)
                    else:
                        mtime = None
                except Exception:
                    mtime = None

                prev = self._prev_mtimes.get(target_path)
                # If we haven't seen this file before, treat this as a change and trigger a backup
                if prev is None:
                    if mtime is not None:
                        self._prev_mtimes[target_path] = mtime
                    # trigger full-source backup on first detection
                    g_stop_watching = True
                    threading.Thread(target=self.backup_file, args=(self.src_dir,), daemon=True).start()
                    return

                # If mtime changed, copy entire source directory instead of only the file
                if mtime is not None and prev != mtime:
                    self._prev_mtimes[target_path] = mtime
                    g_stop_watching = True
                    threading.Thread(target=self.backup_file, args=(self.src_dir,), daemon=True).start()
                    return

            # Default behavior: back up the matched file/folder
            g_stop_watching = True
            threading.Thread(target=self.backup_file, args=(event.src_path,), daemon=True).start()

    def backup_file(self, file_path):
        self.parent.log("Detected change, backing up...")
        global g_stop_watching
        if not os.path.exists(file_path):
            g_stop_watching = False
            return

        try:

            file_name = os.path.basename(file_path)
            timestamp = datetime.now().strftime("%d-%m-%Y_%H-%M-%S")
            date_folder = datetime.now().strftime("%d-%m-%Y") if self.create_date_dir else ""
            backup_path = os.path.join(self.backup_dir, date_folder)
            os.makedirs(backup_path, exist_ok=True)

            destination_file_name = f"{file_name}_{timestamp}"
            destination_path = os.path.join(backup_path, destination_file_name)

            # Take screenshot of main monitor (use configurable offset)
            time.sleep(self.screenshot_offset)
            screenshot_path = destination_path + ".png"
            try:
                from PIL import ImageGrab
                screenshot = ImageGrab.grab()
                screenshot.save(screenshot_path)
            except Exception:
                screenshot_path = None

            # Wait until the file/folder stops changing
            self.wait_for_stable_file(file_path, wait_time=1, retries=3)

            if os.path.isfile(file_path):
                shutil.copy2(file_path, destination_path)
            elif os.path.isdir(file_path):
                # Safe copy for folders (merge if already exists)
                shutil.copytree(file_path, destination_path, dirs_exist_ok=True)
            else:
                if self.parent and hasattr(self.parent, "log"):
                    self.parent.log(f"Unknown file type: {file_path}")
                return

            # Emit signal to safely update GUI
            if hasattr(self, "backup_done"):
                # emit the actual backup path so UI operations (restore/delete/rename) act on the backup copy
                self.backup_done.emit(destination_file_name, destination_path, screenshot_path)
            if self.parent and hasattr(self.parent, "log"):
                self.parent.log(f"Backup created: {destination_file_name}")
                self.wait_for_next_timeout()
        except PermissionError:
            if self.parent and hasattr(self.parent, "log"):
                self.parent.log(f"Permission denied: {file_path}")
        except Exception as e:
            if self.parent and hasattr(self.parent, "log"):
                self.parent.log(f"Error backing up {file_path}: {e}")
        

# ---------------- Watcher Thread ---------------- #
class WatcherThread(threading.Thread):
    def __init__(self, handler, src_dir):
        super().__init__(daemon=True)
        self.handler = handler
        self.src_dir = src_dir
        self.observer = Observer()

    def run(self):
        self.observer.schedule(self.handler, path=self.src_dir, recursive=False)
        self.observer.start()
        self.observer.join()

    def stop(self):
        self.handler.stop()
        self.observer.stop()
        self.observer.join()

# ---------------- Main App ---------------- #
class BackupApp(QWidget):
    def __init__(self):
        super().__init__()
        global g_version
        self.setWindowTitle(f"Backup Manager: v{g_version}")
        self.resize(1100, 600)

        self.config = load_config()
        self.backup_dict = {}
        self.handler = None
        self.watcher_thread = None
        self.at_top = False

        self.initUI()
        self.load_previous_backups()

        # Apply dark mode if enabled
        if self.config.get("dark_mode", False):
            self.apply_dark_mode(True)
            self.dark_mode_checkbox.setChecked(True)

        # Start watching automatically if directories are set
        if self.src_input.text() and self.dest_input.text():
            self.start_backup_monitoring()

    def initUI(self):
        main_layout = QHBoxLayout(self)
        left_layout = QVBoxLayout()

        # Source dir
        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Choose source directory"))
        self.src_input = QLineEdit(self.config.get("src_dir", ""))
        self.src_input.setReadOnly(True)
        row1.addWidget(self.src_input)
        btn_src = QPushButton("Browse")
        btn_src.clicked.connect(self.select_src_directory)
        row1.addWidget(btn_src)
        left_layout.addLayout(row1)

        # Backup dir
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Choose backup directory"))
        self.dest_input = QLineEdit(self.config.get("backup_dir", ""))
        self.dest_input.setReadOnly(True)
        row2.addWidget(self.dest_input)
        btn_dest = QPushButton("Browse")
        btn_dest.clicked.connect(self.select_dest_directory)
        row2.addWidget(btn_dest)
        left_layout.addLayout(row2)

        # Filename + Timeout + Checkbox
        row3 = QHBoxLayout()
        row3.addWidget(QLabel("Filename:"))
        self.filename_pattern_input = QLineEdit(self.config.get("filename_pattern", "*.sav"))
        row3.addWidget(self.filename_pattern_input)
        row3.addWidget(QLabel("Backup timeout:"))
        self.timeout_input = QSpinBox()
        self.timeout_input.setValue(self.config.get("timeout", 5))
        self.timeout_input.setRange(1, 9999)
        row3.addWidget(self.timeout_input)
        self.date_folder_checkbox = QCheckBox("Create backup folder with today's date")
        self.date_folder_checkbox.setChecked(True)
        row3.addWidget(self.date_folder_checkbox)
        # If a specific filename is being watched, optionally copy whole src dir on changes
        self.watch_filename_change_checkbox = QCheckBox("Copy source dir on filename change")
        self.watch_filename_change_checkbox.setChecked(self.config.get("watch_filename_changes", False))
        row3.addWidget(self.watch_filename_change_checkbox)
        # Screenshot offset control
        row3.addWidget(QLabel("Screenshot offset (s):"))
        self.screenshot_offset_input = QDoubleSpinBox()
        self.screenshot_offset_input.setRange(0.0, 30.0)
        self.screenshot_offset_input.setDecimals(2)
        self.screenshot_offset_input.setSingleStep(0.1)
        self.screenshot_offset_input.setValue(self.config.get("screenshot_offset", 0.5))
        self.screenshot_offset_input.valueChanged.connect(self.on_screenshot_offset_changed)
        row3.addWidget(self.screenshot_offset_input)
        left_layout.addLayout(row3)

        # Number of previous backups to load + Dark mode in one row
        row4 = QHBoxLayout()
        row4.addWidget(QLabel("Number of previous backups to load:"))
        self.num_previous_backups_input = QSpinBox()
        self.num_previous_backups_input.setRange(1, 100)
        self.num_previous_backups_input.setValue(self.config.get("num_previous_backups", 10))
        self.num_previous_backups_input.valueChanged.connect(self.on_num_previous_backups_changed)
        row4.addWidget(self.num_previous_backups_input)
        # Max logs control
        row4.addWidget(QLabel("Max logs:"))
        self.max_logs_input = QSpinBox()
        self.max_logs_input.setRange(1, 10000)
        self.max_logs_input.setValue(self.config.get("max_logs", 500))
        self.max_logs_input.valueChanged.connect(self.on_max_logs_changed)
        row4.addWidget(self.max_logs_input)
        # Add stretch to separate the widgets
        row4.addStretch()
        self.dark_mode_checkbox = QCheckBox("Dark Mode")
        self.dark_mode_checkbox.setChecked(self.config.get("dark_mode", False))
        self.dark_mode_checkbox.stateChanged.connect(self.on_dark_mode_toggle)
        row4.addWidget(self.dark_mode_checkbox)
        left_layout.addLayout(row4)

        # Start/Stop buttons
        row5 = QHBoxLayout()
        self.start_btn = QPushButton("Start Watching")
        self.start_btn.clicked.connect(self.start_backup_monitoring)
        row5.addWidget(self.start_btn)
        self.stop_btn = QPushButton("Stop Watching")
        self.stop_btn.clicked.connect(self.stop_backup_monitoring)
        self.stop_btn.setEnabled(False)
        row5.addWidget(self.stop_btn)
        left_layout.addLayout(row5)

        # Backup Table (now with Rename column)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Backuped Filename", "Date", "Screenshot", "Restore", "Delete", "Rename"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)

        # make table read-only and not selectable
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setFocusPolicy(Qt.NoFocus)

        left_layout.addWidget(self.table)
        main_layout.addLayout(left_layout, 3)

        # Logs
        right_layout = QVBoxLayout()
        right_layout.addWidget(QLabel("Logs"))
        self.log_widget = QListWidget()
        right_layout.addWidget(self.log_widget)
        # Clear logs button
        btn_clear_logs = QPushButton("Clear Logs")
        btn_clear_logs.clicked.connect(self.clear_logs)
        right_layout.addWidget(btn_clear_logs)
        main_layout.addLayout(right_layout, 1)

    def log(self, msg, error=False, color=None):
        ts = datetime.now().strftime("[%H:%M:%S]")
        item = QListWidgetItem(f"{ts} {msg}")
        # Color logic
        if color:
            item.setForeground(QtGui.QColor(color))
        elif error or "error" in msg.lower() or "failed" in msg.lower():
            item.setForeground(QtGui.QColor("red"))
        elif "backup created" in msg.lower():
            item.setForeground(QtGui.QColor("green"))
        self.log_widget.addItem(item)
        self.log_widget.scrollToBottom()
        print(f"{ts} {msg}")
        # Enforce max logs limit (remove oldest entries)
        try:
            max_logs = self.max_logs_input.value() if hasattr(self, 'max_logs_input') else self.config.get('max_logs', 500)
            while self.log_widget.count() > int(max_logs):
                self.log_widget.takeItem(0)
        except Exception:
            pass

    def clear_logs(self):
        """Clear the logs list widget."""
        self.log_widget.clear()

    def select_src_directory(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Source Directory")
        if folder: self.src_input.setText(folder)

    def select_dest_directory(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Backup Directory")
        if folder: self.dest_input.setText(folder)

    def add_backup_to_table(self, filename, original_file, screenshot_path, date_str=None):
        if self.at_top:
            row = 0
        else:
            row = self.table.rowCount()

        self.table.insertRow(row)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeToContents)

        self.table.setItem(row, 0, QTableWidgetItem(filename))
        self.table.setItem(row, 1, QTableWidgetItem(date_str or datetime.now().strftime("%d-%m-%Y %H:%M:%S")))

        # Only pass screenshot_path if it exists and is a file
        valid_screenshot = screenshot_path if screenshot_path and os.path.isfile(screenshot_path) else None
        thumb = HoverThumbnail(valid_screenshot, main_window=self)
        # Set row height and column width to match the image
        if thumb.pixmap() is not None:
            pixmap_size = thumb.pixmap().size()
        else:
            pixmap_size = QSize(48, 48)
        self.table.setRowHeight(row, 48)
        self.table.setColumnWidth(2, 48)
        # ensure action columns have reasonable sizing
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeToContents)
 
         # Put label directly in cell
        self.table.setCellWidget(row, 2, thumb)
 
        # Restore button in its own cell
        btn_restore = QPushButton("Restore")
        btn_restore.clicked.connect(lambda _, f=filename: self.restore_backup(f))
        self.table.setCellWidget(row, 3, btn_restore)

        # Delete button in its own cell (small)
        btn_delete = QPushButton("🗑")
        btn_delete.setToolTip("Delete")
        btn_delete.clicked.connect(lambda _, f=filename: self.delete_backup(f))
        self.table.setCellWidget(row, 4, btn_delete)

        # Rename button in its own cell
        btn_rename = QPushButton("Rename")
        btn_rename.clicked.connect(lambda _, f=filename: self.rename_backup(f))
        self.table.setCellWidget(row, 5, btn_rename)
 
        

        self.backup_dict[filename] = original_file

    def delete_backup(self, backup_filename):
        """
        Delete a backup file or folder and its screenshot, with confirmation.
        """
        if backup_filename not in self.backup_dict:
            self.log(f"Backup not found in table: {backup_filename}", error=True)
            return

        # Confirmation dialog
        reply = QMessageBox.question(
            self,
            "Delete Backup",
            f"Are you sure you want to delete backup '{backup_filename}'?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        backup_path = self.backup_dict[backup_filename]
        screenshot_path = backup_path + ".png"
        try:
            if os.path.isfile(backup_path):
                os.remove(backup_path)
            elif os.path.isdir(backup_path):
                shutil.rmtree(backup_path)
            if os.path.isfile(screenshot_path):
                os.remove(screenshot_path)
            self.log(f"Deleted backup: {backup_filename}", color="orange")
            # Remove from table and dict
            for row in range(self.table.rowCount()):
                if self.table.item(row, 0) and self.table.item(row, 0).text() == backup_filename:
                    self.table.removeRow(row)
                    break
            del self.backup_dict[backup_filename]
        except Exception as e:
            self.log(f"Error deleting backup: {e}", error=True)

    def rename_backup(self, backup_filename):
        """Prompt for a new base name, rename backup file/folder and its .png screenshot, update table and mapping."""
        if backup_filename not in self.backup_dict:
            self.log(f"Backup not found in table: {backup_filename}", error=True)
            return

        current_path = self.backup_dict[backup_filename]
        parent = os.path.dirname(current_path)
        base = os.path.basename(current_path)

        # extract timestamp suffix if present: _DD-MM-YYYY_HH-MM-SS
        m = re.search(r'(_\d{2}-\d{2}-\d{4}_\d{2}-\d{2}-\d{2})$', base)
        timestamp_suffix = m.group(1) if m else ""

        new_name, ok = QInputDialog.getText(self, "Rename Backup", "New base name (without timestamp):", text=(base.replace(timestamp_suffix,"") if timestamp_suffix else base))
        if not ok or not new_name.strip():
            return
        new_name = new_name.strip()

        new_path = os.path.join(parent, new_name)

        # guard: don't overwrite existing
        if os.path.exists(new_path) or os.path.exists(new_path + ".png"):
            self.log("A file with the new name already exists.", error=True)
            return

        try:
            # rename file/folder
            shutil.move(current_path, new_path)
            # rename screenshot if exists
            old_screenshot = current_path + ".png"
            new_screenshot = new_path + ".png"
            if os.path.exists(old_screenshot):
                shutil.move(old_screenshot, new_screenshot)

            # Update mapping and table display
            # find row and update name cell
            for row in range(self.table.rowCount()):
                if self.table.item(row, 0) and self.table.item(row, 0).text() == backup_filename:
                    self.table.item(row, 0).setText(new_name)
                    break

            # update dict key -> new path: remove old entry and insert new
            del self.backup_dict[backup_filename]
            self.backup_dict[new_name] = new_path

            self.log(f"Renamed backup: {backup_filename} -> {new_name}", color="blue")
        except Exception as e:
            self.log(f"Error renaming backup: {e}", error=True)

    def on_dark_mode_toggle(self, state):
        enabled = bool(state)
        self.apply_dark_mode(enabled)
        # Save preference
        self.config["dark_mode"] = enabled
        save_config(self.config)

    def on_num_previous_backups_changed(self, value):
        self.config["num_previous_backups"] = value
        save_config(self.config)
        # Reload table with new value
        self.table.setRowCount(0)
        self.load_previous_backups()

    def on_screenshot_offset_changed(self, value):
        self.config["screenshot_offset"] = float(value)
        save_config(self.config)
        # If handler exists, update its value for future backups
        if self.handler:
            try:
                self.handler.screenshot_offset = float(value)
            except Exception:
                pass

    def on_max_logs_changed(self, value):
        try:
            self.config["max_logs"] = int(value)
            save_config(self.config)
            # Trim existing logs if needed
            while self.log_widget.count() > int(value):
                self.log_widget.takeItem(0)
        except Exception:
            pass

    def apply_dark_mode(self, enabled):
        if enabled:
            # Simple dark stylesheet
            self.setStyleSheet("""
                QWidget {
                    background-color: #232629;
                    color: #f0f0f0;
                }
                QLineEdit, QSpinBox, QListWidget, QTableWidget {
                    background-color: #2b2b2b;
                    color: #f0f0f0;
                    border: 1px solid #444;
                }
                QPushButton {
                    background-color: #444;
                    color: #f0f0f0;
                    border: 1px solid #666;
                }
                QPushButton:hover {
                    background-color: #555;
                }
                QHeaderView::section {
                    background-color: #333;
                    color: #f0f0f0;
                }
                QCheckBox {
                    color: #f0f0f0;
                }
            """)
        else:
            self.setStyleSheet("")

    def start_backup_monitoring(self):
        src = self.src_input.text()
        dest = self.dest_input.text()
        if not src or not dest:
            self.log("Please set both source and backup directories.")
            return
        self.handler = BackupHandler(
            self, dest, self.timeout_input.value(),
            src, self.filename_pattern_input.text(),
            self.date_folder_checkbox.isChecked(),
            screenshot_offset=self.screenshot_offset_input.value(),
            watch_filename_changes=self.watch_filename_change_checkbox.isChecked()
        )
        self.handler.backup_done.connect(self.add_backup_to_table)
        self.watcher_thread = WatcherThread(self.handler, src)
        self.watcher_thread.start()
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.log("Started monitoring.")
        save_config({
            "src_dir": src,
            "backup_dir": dest,
            "filename_pattern": self.filename_pattern_input.text(),
            "timeout": self.timeout_input.value(),
            "keep_on_top": True,
            "dark_mode": self.dark_mode_checkbox.isChecked(),
            "watch_filename_changes": self.watch_filename_change_checkbox.isChecked(),
            "num_previous_backups": self.num_previous_backups_input.value(),
            "max_logs": self.max_logs_input.value(),
            "screenshot_offset": self.screenshot_offset_input.value()
        })

    def stop_backup_monitoring(self):
        if self.watcher_thread:
            self.watcher_thread.stop()
            self.log("Stopped monitoring.")
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)

    def is_file_locked(self, filepath):
        """Return True if file is locked by another process (including this one)."""
        if not os.path.isfile(filepath):
            return False
        try:
            with open(filepath, 'a'):
                return False
        except Exception:
            return True

    def restore_backup(self, backup_filename):
        """
        Restore a backup file or folder into the original source directory.
        Uses the full backup path stored in self.backup_dict.
        """
        global g_stop_watching
        g_stop_watching = True

        if backup_filename not in self.backup_dict:
            self.log(f"Backup not found in table: {backup_filename}")
            g_stop_watching = False
            return

        backup_path = self.backup_dict[backup_filename]  # full path to the backup copy
        source_dir = self.src_input.text()
        if not source_dir:
            self.log("Source directory not set!")
            g_stop_watching = False
            return

        # Determine original filename by removing timestamp suffix if present.
        # Example backup basename: "save1.sav_23-10-2025_12-00-00" -> original "save1.sav"
        base_backup = os.path.basename(backup_path)
        original_basename = re.sub(r'_\d{2}-\d{2}-\d{4}_\d{2}-\d{2}-\d{2}$', '', base_backup)
        # If the backup is a directory that matches the source directory's basename,
        # restore its contents directly into the source directory (don't create a nested folder).
        try:
            src_basename = os.path.basename(source_dir.rstrip('/\\'))
        except Exception:
            src_basename = os.path.basename(source_dir)

        if os.path.isdir(backup_path) and original_basename == src_basename:
            original_path = source_dir
        else:
            original_path = os.path.join(source_dir, original_basename)

        # --- Stop watcher before restoring ---
        watcher_was_running = False
        if self.watcher_thread and self.stop_btn.isEnabled():
            watcher_was_running = True
            self.stop_backup_monitoring()
            # Give the OS a moment to release file handles
            time.sleep(0.5)
            gc.collect()
            # Wait up to 2 seconds for file(s) to be unlocked
            if os.path.isdir(backup_path):
                files_to_check = [os.path.join(root, f) for root, _, files in os.walk(backup_path) for f in files]
            else:
                files_to_check = [backup_path]
            for _ in range(20):
                if not any(self.is_file_locked(f) for f in files_to_check):
                    break
                time.sleep(0.1)
                gc.collect()

        try:
            self.log(f"Restoring {backup_filename} -> {original_path}")
            self.log(backup_path)
            if os.path.isfile(backup_path):
                os.makedirs(os.path.dirname(original_path) or source_dir, exist_ok=True)
                shutil.copy2(backup_path, original_path)
                self.log(f"Restore complete: {original_basename}")
            elif os.path.isdir(backup_path):
                # create/ensure target folder exists
                os.makedirs(original_path, exist_ok=True)
                # Copy all contents recursively into original_path
                for item in os.listdir(backup_path):
                    src_item = os.path.join(backup_path, item)
                    dest_item = os.path.join(original_path, item)
                    if os.path.isfile(src_item):
                        os.makedirs(os.path.dirname(dest_item), exist_ok=True)
                        shutil.copy2(src_item, dest_item)
                    elif os.path.isdir(src_item):
                        if not os.path.exists(dest_item):
                            shutil.copytree(src_item, dest_item)
                        else:
                            # Merge contents if dest exists
                            for root, dirs, files in os.walk(src_item):
                                rel_path = os.path.relpath(root, src_item)
                                target_root = os.path.join(dest_item, rel_path)
                                os.makedirs(target_root, exist_ok=True)
                                for f in files:
                                    shutil.copy2(os.path.join(root, f), os.path.join(target_root, f))
                self.log(f"Restore complete: {original_basename}")
            else:
                self.log("Restore failed: unknown file type!")
        except Exception as e:
            self.log(f"Error restoring backup: {e}")
        finally:
            g_stop_watching = False
            # --- Restart watcher if it was running before ---
            if watcher_was_running:
                self.start_backup_monitoring()

    def load_previous_backups(self):
        """Scan backup directory and populate table with up to N newest backups."""
        dest = self.config.get("backup_dir", "")
        if not dest or not os.path.exists(dest):
            return

        num_to_load = self.config.get("num_previous_backups", 10)
        all_backups = []

        # Collect all backups from all date folders
        for date_folder in os.listdir(dest):
            date_folder_path = os.path.join(dest, date_folder)
            if not os.path.isdir(date_folder_path):
                continue  # skip files at top level

            for backup_name in os.listdir(date_folder_path):
                backup_path = os.path.join(date_folder_path, backup_name)

                # Skip screenshots
                if backup_name.endswith(".png"):
                    continue

                backup_datetime = os.path.getmtime(backup_path)

                backup_datetime = os.path.getmtime(backup_path)
                all_backups.append((backup_datetime, backup_name, backup_path, backup_path + ".png"))

        # Sort all backups globally by timestamp descending (newest first)
        all_backups.sort(key=lambda x: x[0], reverse=True)

        # Only keep the N most recent
        all_backups = all_backups[:num_to_load]

        # Add sorted backups to the table
        for timestamp, backup_name, backup_path, screenshot_path in all_backups:
            date_str = datetime.fromtimestamp(timestamp).strftime("%d-%m-%Y %H:%M:%S")
            self.add_backup_to_table(backup_name, backup_path, screenshot_path, date_str)

        self.at_top = True


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = BackupApp()
    window.setWindowIcon(QtGui.QIcon("C:\\Users\\khali\\Documents\\GameBackupWatcher - Copy\\icon.ico"))
    window.show()
    sys.exit(app.exec_())
