import os
import sys
import json
import shutil
import hashlib
from datetime import datetime
from PyQt6.QtCore import QRunnable, QThreadPool, pyqtSignal, QObject, Qt, QTimer
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import QApplication, QWidget, QVBoxLayout, QHBoxLayout, QFileDialog, QTreeWidgetItem, QStackedWidget
from qfluentwidgets import (
    LineEdit, ProgressRing, TitleLabel, BodyLabel, CardWidget, setTheme, Theme, 
    InfoBar, InfoBarPosition, setThemeColor, PrimaryPushButton, PushButton, FluentIcon, 
    TreeWidget, CheckBox, Pivot, MessageBoxBase
)

CONFIG_FILE = "tree_app_config.json"
SNAPSHOT_FILE = "tree_tracking_snapshot.txt"

class WorkerSignals(QObject):
    progress = pyqtSignal(str)      
    finished = pyqtSignal(list)     
    error = pyqtSignal(str)        

class TreeLoaderWorker(QRunnable):
    def __init__(self, root_path, track_enabled, saved_snapshot=None):
        super().__init__()
        self.root_path = os.path.normpath(root_path)
        self.track_enabled = track_enabled
        self.saved_snapshot = saved_snapshot or {}
        self.signals = WorkerSignals()
        self.current_snapshot = {}

    def run(self):
        try:
            tree_data = self._build_tree_data(self.root_path)
            if self.track_enabled and self.saved_snapshot:
                self._inject_deleted_items(self.root_path, tree_data, self.saved_snapshot)
            self.signals.finished.emit([tree_data, self.current_snapshot])
        except Exception as e:
            self.signals.error.emit(str(e))

    def _build_tree_data(self, current_dir):
        current_dir = os.path.normpath(current_dir)
        rel_path = os.path.relpath(current_dir, self.root_path) if current_dir != self.root_path else "."
        node_key = "." if rel_path == "." else rel_path
        
        node = {
            "name": os.path.basename(current_dir) or current_dir,
            "path": current_dir,
            "is_dir": True,
            "status": "standard",
            "children": []
        }
        self.current_snapshot[node_key] = {"is_dir": True}

        try:
            self.signals.progress.emit(f"Reading: {node['name']}")
            items = os.listdir(current_dir)
            items.sort(key=lambda x: (not os.path.isdir(os.path.join(current_dir, x)), x.lower()))

            for item in items:
                full_path = os.path.normpath(os.path.join(current_dir, item))
                is_directory = os.path.isdir(full_path)
                item_rel_path = os.path.relpath(full_path, self.root_path)
                
                status = "standard"
                if self.track_enabled and self.saved_snapshot:
                    if item_rel_path not in self.saved_snapshot:
                        status = "added"

                if is_directory:
                    node["children"].extend(self._build_tree_data(full_path))
                else:
                    self.current_snapshot[item_rel_path] = {"is_dir": False}
                    node["children"].append({
                        "name": item,
                        "path": full_path,
                        "is_dir": False,
                        "status": status,
                        "children": []
                    })
        except PermissionError:
            node["children"].append({
                "name": "⚠️ [Permission Denied]", "path": current_dir, "is_dir": False, "status": "standard", "children": []
            })
            
        if rel_path == ".":
            if self.track_enabled and self.saved_snapshot and node_key not in self.saved_snapshot:
                node["status"] = "added"
            return node
        return [node]

    def _inject_deleted_items(self, root_path, tree_data, snapshot):
        for rel_path, info in snapshot.items():
            if rel_path not in self.current_snapshot:
                parts = rel_path.split(os.sep)
                deleted_node = {
                    "name": parts[-1], "path": os.path.normpath(os.path.join(root_path, rel_path)),
                    "is_dir": info.get("is_dir", False), "status": "deleted", "children": []
                }
                parent_node = self._find_parent_node(tree_data, parts[:-1])
                if parent_node:
                    parent_node["children"].append(deleted_node)
                elif len(parts) == 1:
                    tree_data["children"].append(deleted_node)

    def _find_parent_node(self, current_node, parent_parts):
        if not parent_parts: return current_node
        target_rel = os.path.join(*parent_parts)
        node_rel = os.path.relpath(current_node["path"], self.root_path)
        node_rel = "." if node_rel == "." else node_rel
        if node_rel == target_rel: return current_node
        for child in current_node["children"]:
            if child["is_dir"]:
                found = self._find_parent_node(child, parent_parts)
                if found: return found
        return None


class StartupBackupDialog(MessageBoxBase):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.titleLabel = TitleLabel("Backup Protection", self)
        self.contentLabel = BodyLabel("Backups are important. Do you want to enable them and configure your storage directory path right now?", self)
        self.contentLabel.setWordWrap(True)
        self.cb = CheckBox("Don't show again", self)
        
        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addWidget(self.contentLabel)
        self.viewLayout.addWidget(self.cb)
        
        self.yesButton.setText("Yes")
        self.cancelButton.setText("No")
        self.widget.setMinimumWidth(400)


class FluentFileTreeApp(QWidget):
    def __init__(self):
        super().__init__()
        setTheme(Theme.DARK) 
        setThemeColor('#0078d4') 
        self.thread_pool = QThreadPool()
        self.config = {"backup_path": "", "show_startup_dialog": True}
        self.loaded_snapshot = {}
        self.current_root = ""
        self._is_updating_checks = False
        
        self.load_config()
        self.init_ui()
        
        if self.config.get("show_startup_dialog", True) and not self.config.get("backup_path"):
            QTimer.singleShot(200, self.show_startup_backup_dialog)

    def load_config(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    self.config.update(json.load(f))
            except Exception: pass

    def save_config(self):
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(self.config, f, indent=4)
        except Exception: pass

    def init_ui(self):
        self.setWindowTitle("Fluent File Tree Visualizer Pro")
        self.resize(1000, 800)
        
        self.root_layout = QVBoxLayout(self)
        self.navigation_bar = Pivot(self)
        self.stacked_widget = QStackedWidget(self)
        
        self.root_layout.addWidget(self.navigation_bar)
        self.root_layout.addWidget(self.stacked_widget)
        
        self.init_viewer_tab()
        self.init_backups_tab()
        self.init_settings_tab()
        
        self.navigation_bar.setCurrentItem(self.viewer_widget.objectName())

    def show_startup_backup_dialog(self):
        dialog = StartupBackupDialog(self)
        
        if dialog.exec():
            self.stacked_widget.setCurrentWidget(self.settings_widget)
            self.navigation_bar.setCurrentItem(self.settings_widget.objectName())
            self.browse_backup_path()
            if self.config["backup_path"]:
                self.config["show_startup_dialog"] = False
        else:
            if dialog.cb.isChecked():
                self.config["show_startup_dialog"] = False
        self.save_config()

    # --- TAB 1: TREE VIEWER ---
    def init_viewer_tab(self):
        self.viewer_widget = QWidget()
        self.viewer_widget.setObjectName("ViewerTab")
        layout = QVBoxLayout(self.viewer_widget)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(12)

        card = CardWidget(self)
        clayout = QHBoxLayout(card)
        self.path_input = LineEdit(self)
        self.path_input.setPlaceholderText("Select workspace tree root...")
        self.path_input.setReadOnly(True)
        self.browse_btn = PrimaryPushButton(FluentIcon.FOLDER, "Select Folder", self)
        self.browse_btn.clicked.connect(self.browse_folder)
        clayout.addWidget(self.path_input, stretch=4)
        clayout.addWidget(self.browse_btn, stretch=1)
        layout.addWidget(card)

        top_ctrl_bar = QHBoxLayout()
        self.select_all_checkbox = CheckBox("Select All", self)
        self.select_all_checkbox.stateChanged.connect(self.handle_select_all_changed)
        top_ctrl_bar.addWidget(self.select_all_checkbox)
        top_ctrl_bar.addStretch(1)
        layout.addLayout(top_ctrl_bar)

        ops_row = QHBoxLayout()
        self.track_checkbox = CheckBox("Track changes", self)
        self.save_checkbox = CheckBox("Save tracking status in .txt document", self)
        self.backup_checkbox = CheckBox("Automatically backup changes", self)
        self.run_backup_btn = PrimaryPushButton(FluentIcon.COPY, "Run Backup", self)
        self.run_backup_btn.clicked.connect(self.execute_backup)
        self.run_backup_btn.setEnabled(False)

        ops_row.addWidget(self.track_checkbox)
        ops_row.addWidget(self.save_checkbox)
        ops_row.addWidget(self.backup_checkbox)
        ops_row.addWidget(self.run_backup_btn)
        layout.addLayout(ops_row)

        self.loading_container = QHBoxLayout()
        self.loading_ring = ProgressRing(self)
        self.loading_ring.setFixedSize(18, 18)
        self.loading_ring.setValue(-1)
        self.loading_ring.hide()
        self.status_label = BodyLabel("", self)
        self.status_label.setStyleSheet("color: #0078d4;")
        self.loading_container.addWidget(self.loading_ring)
        self.loading_container.addWidget(self.status_label, stretch=1)
        layout.addLayout(self.loading_container)

        self.tree_widget = TreeWidget(self)
        self.tree_widget.setHeaderLabel("Active Tree Workspace System")
        self.tree_widget.itemChanged.connect(self.handle_item_check_changed)
        layout.addWidget(self.tree_widget, stretch=1)

        self.stacked_widget.addWidget(self.viewer_widget)
        self.navigation_bar.addItem(self.viewer_widget.objectName(), "Workspace Viewer", lambda: self.stacked_widget.setCurrentWidget(self.viewer_widget))

    # --- TAB 2: BACKUPS HISTORY VIEW ---
    def init_backups_tab(self):
        self.backups_widget = QWidget()
        self.backups_widget.setObjectName("BackupsTab")
        layout = QHBoxLayout(self.backups_widget)
        
        self.history_tree = TreeWidget(self)
        self.history_tree.setHeaderLabel("Archived Snapshots By Projects")
        self.history_tree.itemClicked.connect(self.load_backup_snapshot_contents)
        layout.addWidget(self.history_tree, stretch=1)
        
        self.backup_content_tree = TreeWidget(self)
        self.backup_content_tree.setHeaderLabel("Archived Tree File Payload Profile")
        layout.addWidget(self.backup_content_tree, stretch=2)

        self.stacked_widget.addWidget(self.backups_widget)
        self.navigation_bar.addItem(self.backups_widget.objectName(), "Backup History", self.on_backup_tab_opened)

    # --- TAB 3: APP SYSTEM SETTINGS ---
    def init_settings_tab(self):
        self.settings_widget = QWidget()
        self.settings_widget.setObjectName("SettingsTab")
        layout = QVBoxLayout(self.settings_widget)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)

        layout.addWidget(TitleLabel("Application Engine Settings", self))
        
        card = CardWidget(self)
        card_layout = QVBoxLayout(card)
        card_layout.addWidget(BodyLabel("Target Repository Destination Backup Directory Path Allocation:", self))
        
        path_row = QHBoxLayout()
        self.backup_path_display = LineEdit(self)
        self.backup_path_display.setReadOnly(True)
        
        self.cfg_browse_btn = PushButton("Set Target Storage Path", self)
        self.cfg_browse_btn.clicked.connect(self.browse_backup_path)
        path_row.addWidget(self.backup_path_display, stretch=4)
        path_row.addWidget(self.cfg_browse_btn, stretch=1)
        card_layout.addLayout(path_row)
        
        layout.addWidget(card)
        layout.addStretch(1)

        self.stacked_widget.addWidget(self.settings_widget)
        self.navigation_bar.addItem(self.settings_widget.objectName(), "Settings Panel", lambda: self.stacked_widget.setCurrentWidget(self.settings_widget))
        self.refresh_settings_styles()

    def refresh_settings_styles(self):
        path = self.config.get("backup_path", "")
        self.backup_path_display.setText(path if path else "NOT CONFIGURATED (CRITICAL ERROR)")
        if not path:
            self.backup_path_display.setStyleSheet("border: 2px solid #A80000; color: #A80000; font-weight: bold;")
        else:
            self.backup_path_display.setStyleSheet("border: 2px solid #107C41; color: #107C41; font-weight: bold;")

    def browse_backup_path(self):
        path = QFileDialog.getExistingDirectory(self, "Allocate System Backup Repository Location")
        if path:
            self.config["backup_path"] = os.path.normpath(path)
            self.save_config()
            self.refresh_settings_styles()

    def browse_folder(self):
        selected_dir = QFileDialog.getExistingDirectory(self, "Select Root Directory")
        if selected_dir:
            self.current_root = os.path.normpath(selected_dir)
            self.path_input.setText(self.current_root)
            self.load_snapshot_file()
            self.start_async_scan(self.current_root)

    def load_snapshot_file(self):
        self.loaded_snapshot = {}
        if self.track_checkbox.isChecked() and os.path.exists(SNAPSHOT_FILE):
            try:
                with open(SNAPSHOT_FILE, "r", encoding="utf-8") as f:
                    self.loaded_snapshot = json.load(f)
            except Exception: pass

    def start_async_scan(self, target_path):
        self.browse_btn.setEnabled(False)
        self.run_backup_btn.setEnabled(False)
        self.tree_widget.clear()
        self.loading_ring.show()
        self.status_label.show()

        worker = TreeLoaderWorker(target_path, self.track_checkbox.isChecked(), self.loaded_snapshot)
        worker.signals.progress.connect(self.update_status_text)
        worker.signals.finished.connect(self.populate_ui_tree)
        worker.signals.error.connect(self.handle_error)
        self.thread_pool.start(worker)

    def update_status_text(self, text):
        self.status_label.setText(text)
        QApplication.processEvents()

    def handle_error(self, err_msg):
        self.reset_ui_loading_state()
        InfoBar.error("Internal Engine Fault", f"Error encountered: {err_msg}", parent=self)

    def populate_ui_tree(self, result_data):
        tree_data, current_snapshot = result_data
        self.status_label.setText("Applying visual node formatting and rules...")
        QApplication.processEvents()

        if self.save_checkbox.isChecked():
            try:
                with open(SNAPSHOT_FILE, "w", encoding="utf-8") as f:
                    json.dump(current_snapshot, f, indent=4)
            except Exception: pass

        self._is_updating_checks = True

        def render_tree_nodes(parent_item, node):
            item = QTreeWidgetItem()
            icon = FluentIcon.FOLDER if node["is_dir"] else FluentIcon.DOCUMENT
            item.setIcon(0, icon.icon())
            item.setText(0, node["name"])
            item.setData(0, Qt.ItemDataRole.UserRole, node["path"])
            item.setCheckState(0, Qt.CheckState.Unchecked)
            
            if node["status"] == "added":
                item.setForeground(0, QColor("#107C41")) 
            elif node["status"] == "deleted":
                item.setForeground(0, QColor("#A80000")) 
                font = item.font(0)
                font.setStrikeOut(True)
                item.setFont(0, font)
            else:
                item.setForeground(0, QColor("#FFFFFF")) 

            if parent_item: parent_item.addChild(item)
            else: self.tree_widget.addTopLevelItem(item)
            for child in node["children"]: render_tree_nodes(item, child)

        if tree_data:
            render_tree_nodes(None, tree_data)
            if self.tree_widget.topLevelItemCount() > 0:
                self.tree_widget.topLevelItem(0).setExpanded(True)

        self._is_updating_checks = False
        self.update_master_checkbox_ui()
        self.reset_ui_loading_state()
        self.run_backup_btn.setEnabled(True)

    # --- SELECTION CHECKBOX LOGIC ---
    def handle_item_check_changed(self, item, column):
        if self._is_updating_checks: return
        self._is_updating_checks = True
        state = item.checkState(0)
        
        def cascade_down(parent):
            for i in range(parent.childCount()):
                child = parent.child(i)
                child.setCheckState(0, state)
                cascade_down(child)
        cascade_down(item)
        self._is_updating_checks = False
        self.update_master_checkbox_ui()

    def handle_select_all_changed(self, state):
        if self._is_updating_checks: return
        self._is_updating_checks = True
        target_state = Qt.CheckState.Checked if state == Qt.CheckState.Checked else Qt.CheckState.Unchecked
        
        def force_global_state(parent=None):
            count = self.tree_widget.topLevelItemCount() if parent is None else parent.childCount()
            for i in range(count):
                item = self.tree_widget.topLevelItem(i) if parent is None else parent.child(i)
                item.setCheckState(0, target_state)
                force_global_state(item)
        force_global_state()
        self._is_updating_checks = False

    def update_master_checkbox_ui(self):
        if self._is_updating_checks: return
        checked, total = 0, 0
        def eval_stats(parent=None):
            nonlocal checked, total
            count = self.tree_widget.topLevelItemCount() if parent is None else parent.childCount()
            for i in range(count):
                item = self.tree_widget.topLevelItem(i) if parent is None else parent.child(i)
                total += 1
                if item.checkState(0) == Qt.CheckState.Checked: checked += 1
                eval_stats(item)
        eval_stats()
        
        self._is_updating_checks = True
        if total == 0 or checked == 0:
            self.select_all_checkbox.setCheckState(Qt.CheckState.Unchecked)
        elif checked == total:
            self.select_all_checkbox.setCheckState(Qt.CheckState.Checked)
        else:
            self.select_all_checkbox.setCheckState(Qt.CheckState.Unchecked)
        self._is_updating_checks = False

    # --- ADVANCED VERSION CONTROL ENGINE ---
    def execute_backup(self):
        bpath = self.config.get("backup_path", "")
        if not bpath or not self.current_root:
            InfoBar.error("Backup Denied", "Please ensure workspace and target storage paths are loaded properly.", parent=self)
            return

        bpath = os.path.normpath(bpath)
        current_root = os.path.normpath(self.current_root)
        project_name = os.path.basename(current_root) or "UnknownProject"
        
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        # Organize sorted directories by project name -> timestamp profile
        current_backup_dir = os.path.join(bpath, project_name, timestamp)
        os.makedirs(current_backup_dir, exist_ok=True)

        copied_count = 0
        error_logs = []

        def get_file_hash(p):
            h = hashlib.sha256()
            try:
                with open(p, 'rb') as f:
                    while chunk := f.read(8192): h.update(chunk)
                return h.hexdigest()
            except Exception: return ""

        def archive_file_item(src, rel):
            base_dest = os.path.join(current_backup_dir, rel)
            dir_name, file_name = os.path.split(base_dest)
            name_part, ext_part = os.path.splitext(file_name)
            
            os.makedirs(dir_name, exist_ok=True)
            target_dest = base_dest

            if os.path.exists(target_dest):
                if get_file_hash(src) != get_file_hash(target_dest):
                    old_path = os.path.join(dir_name, f"{name_part} (old, backup){ext_part}")
                    shutil.move(target_dest, old_path)
                    
                    target_dest = os.path.join(dir_name, f"{name_part} (new, backup){ext_part}")
                    counter = 2
                    while os.path.exists(target_dest):
                        target_dest = os.path.join(dir_name, f"{name_part} (new{counter}, backup){ext_part}")
                        counter += 1
            shutil.copy2(src, target_dest)

        def extract_selections(parent=None):
            nonlocal copied_count
            count = self.tree_widget.topLevelItemCount() if parent is None else parent.childCount()
            for i in range(count):
                item = self.tree_widget.topLevelItem(i) if parent is None else parent.child(i)
                if item.checkState(0) == Qt.CheckState.Checked:
                    src = item.data(0, Qt.ItemDataRole.UserRole)
                    if src:
                        src = os.path.normpath(src)
                        if os.path.isfile(src):
                            rel = os.path.relpath(src, current_root)
                            try: 
                                archive_file_item(src, rel)
                                copied_count += 1
                            except Exception as e:
                                error_logs.append(str(e))
                extract_selections(item)

        extract_selections()
        
        if error_logs:
            InfoBar.warning("Backup Notice", f"Copied {copied_count} items. Errors hit on {len(error_logs)} files.", parent=self)
        elif copied_count == 0:
            InfoBar.warning("No Files Checked", "No active files were selected to copy. Check structural folder tree checkboxes.", parent=self)
        else:
            InfoBar.success("Success", f"Archived {copied_count} items safely to internal project registry profile.", parent=self)

    # --- HISTORY SNAPSHOT PANEL MANAGEMENT ---
    def on_backup_tab_opened(self):
        self.stacked_widget.setCurrentWidget(self.backups_widget)
        self.history_tree.clear()
        self.backup_content_tree.clear()
        
        bpath = self.config.get("backup_path", "")
        if not bpath or not os.path.exists(bpath): return
        
        bpath = os.path.normpath(bpath)
        try:
            # First loop tracks available project names
            projects = [d for d in os.listdir(bpath) if os.path.isdir(os.path.join(bpath, d))]
            projects.sort(key=lambda x: x.lower())
            
            for proj in projects:
                proj_path = os.path.join(bpath, proj)
                snapshots = [s for s in os.listdir(proj_path) if os.path.isdir(os.path.join(proj_path, s))]
                snapshots.sort(reverse=True) # Newest records placed at top
                
                if snapshots:
                    proj_item = QTreeWidgetItem()
                    proj_item.setIcon(0, FluentIcon.FOLDER.icon())
                    proj_item.setText(0, proj)
                    self.history_tree.addTopLevelItem(proj_item)
                    
                    for snap in snapshots:
                        snap_item = QTreeWidgetItem()
                        snap_item.setIcon(0, FluentIcon.HISTORY.icon())
                        snap_item.setText(0, snap)
                        snap_item.setData(0, Qt.ItemDataRole.UserRole, os.path.join(proj_path, snap))
                        proj_item.addChild(snap_item)
                    
                    proj_item.setExpanded(True)
        except Exception: pass

    def load_backup_snapshot_contents(self, item, column):
        self.backup_content_tree.clear()
        target_path = item.data(0, Qt.ItemDataRole.UserRole)
        if not target_path or not os.path.exists(target_path): return
        
        def render_snapshot_tree(parent_ui, current_disk_path):
            try:
                items = os.listdir(current_disk_path)
                items.sort(key=lambda x: (not os.path.isdir(os.path.join(current_disk_path, x)), x.lower()))
                for name in items:
                    full = os.path.normpath(os.path.join(current_disk_path, name))
                    node = QTreeWidgetItem()
                    node.setText(0, name)
                    
                    if "(old, backup)" in name:
                        node.setForeground(0, QColor("#E6A23C")) 
                        f = node.font(0)
                        f.setStrikeOut(True)
                        node.setFont(0, f)
                    elif "(new" in name:
                        node.setForeground(0, QColor("#107C41")) 
                    else:
                        node.setForeground(0, QColor("#FFFFFF"))
                        
                    node.setIcon(0, (FluentIcon.FOLDER if os.path.isdir(full) else FluentIcon.DOCUMENT).icon())
                    if parent_ui: parent_ui.addChild(node)
                    else: self.backup_content_tree.addTopLevelItem(node)
                    
                    if os.path.isdir(full):
                        render_snapshot_tree(node, full)
            except Exception: pass

        render_snapshot_tree(None, target_path)
        if self.backup_content_tree.topLevelItemCount() > 0:
            self.backup_content_tree.topLevelItem(0).setExpanded(True)

    def reset_ui_loading_state(self):
        self.browse_btn.setEnabled(True)
        self.loading_ring.hide()
        self.status_label.hide()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = FluentFileTreeApp()
    window.show()
    sys.exit(app.exec())