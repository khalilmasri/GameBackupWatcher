#!/usr/bin/env python3
import os, sys, shutil, fnmatch, threading, time, json
from datetime import datetime
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, 
    QFileDialog, QLabel, QLineEdit, QSpinBox, QCheckBox, 
    QTableWidget, QTableWidgetItem, QAbstractItemView, QHeaderView,
    QListWidget, QStackedWidget
)
from PyQt5.QtCore import Qt, QObject, pyqtSignal, QSize
from PyQt5.QtGui import QPixmap
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from PIL import ImageGrab

# ---------------- Global ---------------- #
g_stop_watching = False

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
    return {"backup_dir": "", "src_dir": "", "filename_pattern": "*.sav", "timeout": 5, "keep_on_top": True}

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

        if os.path.exists(img_path):
            pixmap = QPixmap(img_path)
            pixmap = pixmap.scaled(48, 48, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
            self.setPixmap(pixmap)
        else:
            self.setText("️⛔️")


    def mousePressEvent(self, event):
        if os.path.exists(self.img_path) and self.main_window:
            overlay = ImageOverlay(self.main_window, self.img_path)
            overlay.show()
            overlay.raise_()

# ---------------- Backup Handler ---------------- #
class BackupHandler(QObject, FileSystemEventHandler):
    backup_done = pyqtSignal(str, str, str)  # filename, original path, screenshot

    def __init__(self, parent, backup_dir, timeout, src_dir, filename_pattern, create_date_dir):
        super().__init__()
        self.parent = parent
        self.backup_dir = backup_dir
        self.timeout = int(timeout)
        self.src_dir = src_dir
        self.filename_pattern = filename_pattern
        self.create_date_dir = create_date_dir
        self.stop_requested = False

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
        if fnmatch.fnmatch(os.path.basename(event.src_path), self.filename_pattern):
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

            # Take screenshot of main monitor
            time.sleep(0.5)
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
                self.backup_done.emit(destination_file_name, file_path, screenshot_path)
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
        self.setWindowTitle("Backup Manager")
        self.resize(1100, 600)

        self.config = load_config()
        self.backup_dict = {}
        self.handler = None
        self.watcher_thread = None
        self.at_top = False

        self.initUI()
        self.load_previous_backups()

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
        left_layout.addLayout(row3)

        # Start/Stop buttons
        row4 = QHBoxLayout()
        self.start_btn = QPushButton("Start Watching")
        self.start_btn.clicked.connect(self.start_backup_monitoring)
        row4.addWidget(self.start_btn)
        self.stop_btn = QPushButton("Stop Watching")
        self.stop_btn.clicked.connect(self.stop_backup_monitoring)
        self.stop_btn.setEnabled(False)
        row4.addWidget(self.stop_btn)
        left_layout.addLayout(row4)

        # Backup Table
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Backuped Filename", "Date", "Screenshot", "Restore"])
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
        main_layout.addLayout(right_layout, 1)

    def log(self, msg):
        ts = datetime.now().strftime("[%H:%M:%S]")
        self.log_widget.addItem(f"{ts} {msg}")
        self.log_widget.scrollToBottom()
        print(f"{ts} {msg}")

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

        self.table.setItem(row, 0, QTableWidgetItem(filename))
        self.table.setItem(row, 1, QTableWidgetItem(date_str or datetime.now().strftime("%d-%m-%Y %H:%M:%S")))

        # Load thumbnail
        thumb = HoverThumbnail(screenshot_path, main_window=self)
        # Set row height and column width to match the image
        if thumb.pixmap() is not None:
            pixmap_size = thumb.pixmap().size()
        else:
            pixmap_size = QSize(48, 48)
        self.table.setRowHeight(row, 48)
        self.table.setColumnWidth(2, 48)

        # Put label directly in cell
        self.table.setCellWidget(row, 2, thumb)

        # Restore button
        btn_restore = QPushButton("Restore")
        btn_restore.clicked.connect(lambda _, f=filename: self.restore_backup(f))
        self.table.setCellWidget(row, 3, btn_restore)

        self.backup_dict[filename] = original_file


    def start_backup_monitoring(self):
        src = self.src_input.text()
        dest = self.dest_input.text()
        if not src or not dest:
            self.log("Please set both source and backup directories.")
            return
        self.handler = BackupHandler(self, dest, self.timeout_input.value(),
                                     src, self.filename_pattern_input.text(),
                                     self.date_folder_checkbox.isChecked())
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
            "keep_on_top": True
        })

    def stop_backup_monitoring(self):
        if self.watcher_thread:
            self.watcher_thread.stop()
            self.log("Stopped monitoring.")
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)

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

        backup_path = self.backup_dict[backup_filename]  # get the full path from table
        source_dir = self.src_input.text()
        if not source_dir:
            self.log("Source directory not set!")
            g_stop_watching = False
            return

        # Determine the original watched file/folder name
        watched_name = self.filename_pattern_input.text()
        if "*" in watched_name:
            watched_name = watched_name.split("*")[0]  # get base part

        original_path = os.path.join(source_dir, watched_name)

        try:
            self.log(f"Restoring {backup_filename} -> {original_path}")

            self.log(backup_path)
            if os.path.isfile(backup_path):
                os.makedirs(os.path.dirname(original_path), exist_ok=True)
                shutil.copy2(backup_path, original_path)
                self.log(f"Restore complete: {backup_filename}")
            elif os.path.isdir(backup_path):
                os.makedirs(original_path, exist_ok=True)
                # Copy all contents recursively
                for item in os.listdir(backup_path):
                    src_item = os.path.join(backup_path, item)
                    dest_item = os.path.join(original_path, item)
                    if os.path.isfile(src_item):
                        shutil.copy2(src_item, dest_item)
                    elif os.path.isdir(src_item):
                        if not os.path.exists(dest_item):
                            shutil.copytree(src_item, dest_item)
                        else:
                            # Folder exists, copy contents recursively
                            for root, dirs, files in os.walk(src_item):
                                rel_path = os.path.relpath(root, src_item)
                                target_root = os.path.join(dest_item, rel_path)
                                os.makedirs(target_root, exist_ok=True)
                                for f in files:
                                    shutil.copy2(os.path.join(root, f), os.path.join(target_root, f))
                self.log(f"Restore complete: {backup_filename}")

            else:
                self.log("Restore failed: unknown file type!")

        except Exception as e:
            self.log(f"Error restoring backup: {e}")

        g_stop_watching = False


    def load_previous_backups(self):
        """Scan backup directory and populate table with backups in descending order (newest first)."""
        dest = self.config.get("backup_dir", "")
        if not dest or not os.path.exists(dest):
            return

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
                all_backups.append((backup_datetime, backup_name, backup_path, backup_path + ".png"))

        # Sort all backups globally by timestamp descending (newest first)
        all_backups.sort(key=lambda x: x[0], reverse=True)

        # Add sorted backups to the table
        for timestamp, backup_name, backup_path, screenshot_path in all_backups:
            date_str = datetime.fromtimestamp(timestamp).strftime("%d-%m-%Y %H:%M:%S")
            self.add_backup_to_table(backup_name, backup_path, screenshot_path, date_str)

        self.at_top = True


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = BackupApp()
    window.show()
    sys.exit(app.exec_())
