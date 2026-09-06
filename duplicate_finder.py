#!/usr/bin/env python3
"""
Duplicate File Finder & Remover
--------------------------------
Scans one or more folders (including subfolders) for duplicate
images, videos, and documents, then lets you delete all duplicates
with a single click (keeping one copy of each file).

Duplicates are detected by content (MD5 hash), not just by name,
so renamed copies are caught too.

Before deleting, click any file in the list to preview it (image
thumbnail, or an "Open File" button for videos/documents) and
double-click a file to toggle whether it gets kept or deleted -
so you can inspect everything and change your mind before the
one-click delete.

Run:
    python duplicate_finder.py

Requires only the Python standard library (tkinter, hashlib, os).
Optional: install Pillow ("pip install Pillow") to see image
thumbnails in the preview panel; without it, images still work,
just without the thumbnail.
"""

import os
import sys
import hashlib
import subprocess
import threading
from collections import defaultdict
from datetime import datetime

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

# ---------------------------------------------------------------------------
# File types considered
# ---------------------------------------------------------------------------
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tiff", ".heic", ".svg"}
VIDEO_EXT = {".mp4", ".mov", ".avi", ".mkv", ".wmv", ".flv", ".webm", ".m4v"}
DOC_EXT = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt", ".odt", ".csv", ".rtf"}
ALL_EXT = IMAGE_EXT | VIDEO_EXT | DOC_EXT


def hash_file(path, chunk_size=1024 * 1024):
    """Return the MD5 hash of a file's contents, or None on error."""
    h = hashlib.md5()
    try:
        with open(path, "rb") as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()
    except (OSError, PermissionError):
        return None


def human_size(num_bytes):
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if num_bytes < 1024:
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024
    return f"{num_bytes:.1f} PB"


class DuplicateFinderApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Duplicate File Finder")
        self.root.geometry("900x600")

        self.folders = []
        self.duplicate_groups = []  # list of lists of file paths
        self.files_to_delete = []   # flattened list, recomputed from tree tags
        self._preview_image_ref = None  # keep a reference so Tk doesn't garbage-collect it

        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_ui(self):
        top = tk.Frame(self.root, padx=10, pady=10)
        top.pack(fill="x")

        tk.Button(top, text="Add Folder…", command=self.add_folder).pack(side="left")
        tk.Button(top, text="Clear Folders", command=self.clear_folders).pack(side="left", padx=5)

        self.folder_label = tk.Label(top, text="No folders selected", fg="gray")
        self.folder_label.pack(side="left", padx=10)

        scan_frame = tk.Frame(self.root, padx=10)
        scan_frame.pack(fill="x")
        self.scan_btn = tk.Button(scan_frame, text="Scan for Duplicates", command=self.start_scan,
                                   bg="#2563eb", fg="white", padx=10, pady=5)
        self.scan_btn.pack(side="left")

        self.progress = ttk.Progressbar(scan_frame, mode="indeterminate")
        self.progress.pack(side="left", fill="x", expand=True, padx=10)

        hint = tk.Label(
            self.root,
            text="Click a file to preview it. Double-click a file to toggle Keep / Delete.",
            fg="gray", padx=10
        )
        hint.pack(fill="x")

        # Middle area: results tree (left) + preview panel (right)
        middle = tk.Frame(self.root)
        middle.pack(fill="both", expand=True, padx=10, pady=5)

        tree_frame = tk.Frame(middle)
        tree_frame.pack(side="left", fill="both", expand=True)

        columns = ("size", "path")
        self.tree = ttk.Treeview(tree_frame, columns=columns, show="tree headings", selectmode="browse")
        self.tree.heading("#0", text="Group / File")
        self.tree.heading("size", text="Size")
        self.tree.heading("path", text="Full Path")
        self.tree.column("size", width=90, anchor="e")
        self.tree.column("path", width=380)
        self.tree.pack(fill="both", expand=True)
        self.tree.tag_configure("keep", foreground="#15803d")
        self.tree.tag_configure("delete", foreground="#b91c1c")
        self.tree.bind("<<TreeviewSelect>>", self.on_select_file)
        self.tree.bind("<Double-1>", self.on_toggle_keep_delete)

        # Preview panel
        preview_frame = tk.LabelFrame(middle, text="Preview", width=300, padx=10, pady=10)
        preview_frame.pack(side="left", fill="y", padx=(10, 0))
        preview_frame.pack_propagate(False)

        self.preview_image_label = tk.Label(preview_frame, bg="#f3f4f6", width=280, height=200)
        self.preview_image_label.pack(pady=(0, 10))

        self.preview_info_label = tk.Label(
            preview_frame, text="Select a file to see details.",
            justify="left", anchor="nw", wraplength=270
        )
        self.preview_info_label.pack(fill="x")

        self.open_file_btn = tk.Button(
            preview_frame, text="Open File to Inspect", command=self.open_selected_file, state="disabled"
        )
        self.open_file_btn.pack(pady=10, fill="x")

        self.toggle_btn = tk.Button(
            preview_frame, text="Toggle Keep / Delete", command=self.on_toggle_keep_delete, state="disabled"
        )
        self.toggle_btn.pack(fill="x")

        bottom = tk.Frame(self.root, padx=10, pady=10)
        bottom.pack(fill="x")

        self.summary_label = tk.Label(bottom, text="Select folders and click Scan.")
        self.summary_label.pack(side="left")

        self.delete_btn = tk.Button(
            bottom, text="🗑 Delete All Duplicates", command=self.delete_duplicates,
            bg="#dc2626", fg="white", padx=10, pady=5, state="disabled"
        )
        self.delete_btn.pack(side="right")

    # ------------------------------------------------------------------
    # Folder management
    # ------------------------------------------------------------------
    def add_folder(self):
        folder = filedialog.askdirectory(title="Select a folder to include")
        if folder and folder not in self.folders:
            self.folders.append(folder)
            self.folder_label.config(
                text=f"{len(self.folders)} folder(s): " + ", ".join(self.folders),
                fg="black"
            )

    def clear_folders(self):
        self.folders = []
        self.folder_label.config(text="No folders selected", fg="gray")

    # ------------------------------------------------------------------
    # Scanning (runs in a background thread so the UI doesn't freeze)
    # ------------------------------------------------------------------
    def start_scan(self):
        if not self.folders:
            messagebox.showwarning("No folders", "Please add at least one folder first.")
            return

        self.scan_btn.config(state="disabled")
        self.delete_btn.config(state="disabled")
        self.tree.delete(*self.tree.get_children())
        self.summary_label.config(text="Scanning…")
        self.progress.start(10)

        thread = threading.Thread(target=self._scan_worker, daemon=True)
        thread.start()

    def _scan_worker(self):
        size_map = defaultdict(list)  # size -> [paths]  (cheap first pass)

        for folder in self.folders:
            for dirpath, _, filenames in os.walk(folder):
                for name in filenames:
                    ext = os.path.splitext(name)[1].lower()
                    if ext not in ALL_EXT:
                        continue
                    full_path = os.path.join(dirpath, name)
                    try:
                        size = os.path.getsize(full_path)
                    except OSError:
                        continue
                    if size == 0:
                        continue
                    size_map[size].append(full_path)

        # Only hash files that share a size with at least one other file
        hash_map = defaultdict(list)
        for size, paths in size_map.items():
            if len(paths) < 2:
                continue
            for path in paths:
                digest = hash_file(path)
                if digest:
                    hash_map[(size, digest)].append(path)

        groups = [paths for paths in hash_map.values() if len(paths) > 1]
        self.root.after(0, self._scan_complete, groups)

    def _scan_complete(self, groups):
        self.progress.stop()
        self.scan_btn.config(state="normal")
        self.duplicate_groups = groups
        self.files_to_delete = []

        total_wasted = 0
        for i, group in enumerate(groups, start=1):
            group_sorted = sorted(group, key=lambda p: os.path.getmtime(p))
            keeper = group_sorted[0]
            dupes = group_sorted[1:]
            size = os.path.getsize(keeper)
            total_wasted += size * len(dupes)

            parent = self.tree.insert("", "end", text=f"Group {i} ({len(group_sorted)} copies, {human_size(size)} each)",
                                       open=True)
            self.tree.insert(parent, "end", text="✅ KEEP", values=(human_size(size), keeper), tags=("keep",))
            for d in dupes:
                self.tree.insert(parent, "end", text="🗑 DELETE", values=(human_size(size), d), tags=("delete",))
                self.files_to_delete.append(d)

        if groups:
            self.summary_label.config(
                text=f"Found {len(groups)} duplicate group(s), "
                     f"{len(self.files_to_delete)} file(s) can be deleted, "
                     f"freeing {human_size(total_wasted)}."
            )
            self.delete_btn.config(state="normal")
        else:
            self.summary_label.config(text="No duplicates found.")
            self.delete_btn.config(state="disabled")

    # ------------------------------------------------------------------
    # Inspection: preview, open file, toggle keep/delete
    # ------------------------------------------------------------------
    def _selected_leaf(self):
        """Return the selected tree item id if it's a file row (not a group header)."""
        selection = self.tree.selection()
        if not selection:
            return None
        item = selection[0]
        if self.tree.parent(item) == "":  # it's a group header, not a file
            return None
        return item

    def on_select_file(self, event=None):
        item = self._selected_leaf()
        if not item:
            self.open_file_btn.config(state="disabled")
            self.toggle_btn.config(state="disabled")
            return

        path = self.tree.item(item, "values")[1]
        self.open_file_btn.config(state="normal")
        self.toggle_btn.config(state="normal")
        self._show_preview(path)

    def _show_preview(self, path):
        ext = os.path.splitext(path)[1].lower()
        self._preview_image_ref = None
        self.preview_image_label.config(image="", text="")

        if ext in IMAGE_EXT and PIL_AVAILABLE and ext != ".svg":
            try:
                img = Image.open(path)
                img.thumbnail((280, 200))
                self._preview_image_ref = ImageTk.PhotoImage(img)
                self.preview_image_label.config(image=self._preview_image_ref, text="")
            except Exception:
                self.preview_image_label.config(text="(preview unavailable)")
        elif ext in IMAGE_EXT and not PIL_AVAILABLE:
            self.preview_image_label.config(text="Install Pillow to see\nimage thumbnails\n(pip install Pillow)")
        elif ext in VIDEO_EXT:
            self.preview_image_label.config(text="🎬\nVideo file\n(use Open File to play it)")
        else:
            self.preview_image_label.config(text="📄\nDocument\n(use Open File to view it)")

        try:
            stat = os.stat(path)
            info = (
                f"Name: {os.path.basename(path)}\n"
                f"Folder: {os.path.dirname(path)}\n"
                f"Size: {human_size(stat.st_size)}\n"
                f"Modified: {datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M')}"
            )
        except OSError:
            info = f"Name: {os.path.basename(path)}\n(file details unavailable)"
        self.preview_info_label.config(text=info)

    def open_selected_file(self):
        item = self._selected_leaf()
        if not item:
            return
        path = self.tree.item(item, "values")[1]
        try:
            if sys.platform.startswith("win"):
                os.startfile(path)  # noqa: on Windows only
            elif sys.platform == "darwin":
                subprocess.run(["open", path], check=False)
            else:
                subprocess.run(["xdg-open", path], check=False)
        except Exception as e:
            messagebox.showerror("Couldn't open file", str(e))

    def on_toggle_keep_delete(self, event=None):
        item = self._selected_leaf()
        if not item:
            return

        current_tags = self.tree.item(item, "tags")
        parent = self.tree.parent(item)

        if "keep" in current_tags:
            # Demote this file: it needs another sibling promoted to keep.
            siblings = [c for c in self.tree.get_children(parent) if c != item]
            if not siblings:
                return  # only file left in group, can't demote
            new_keeper = siblings[0]
            self.tree.item(item, text="🗑 DELETE", tags=("delete",))
            self.tree.item(new_keeper, text="✅ KEEP", tags=("keep",))
        else:
            # Promote this file to keep; demote whichever sibling currently is keep.
            for sibling in self.tree.get_children(parent):
                if sibling != item and "keep" in self.tree.item(sibling, "tags"):
                    self.tree.item(sibling, text="🗑 DELETE", tags=("delete",))
            self.tree.item(item, text="✅ KEEP", tags=("keep",))

        self._rebuild_files_to_delete()

    def _rebuild_files_to_delete(self):
        """Recompute the delete list from current tree tags, and refresh the summary."""
        self.files_to_delete = []
        total_wasted = 0
        for group in self.tree.get_children(""):
            for leaf in self.tree.get_children(group):
                tags = self.tree.item(leaf, "tags")
                path = self.tree.item(leaf, "values")[1]
                if "delete" in tags:
                    self.files_to_delete.append(path)
                    try:
                        total_wasted += os.path.getsize(path)
                    except OSError:
                        pass

        if self.files_to_delete:
            self.summary_label.config(
                text=f"{len(self.files_to_delete)} file(s) marked for deletion, "
                     f"freeing {human_size(total_wasted)}."
            )
            self.delete_btn.config(state="normal")
        else:
            self.summary_label.config(text="No files marked for deletion.")
            self.delete_btn.config(state="disabled")

    # ------------------------------------------------------------------
    # Deletion (one click, single confirmation)
    # ------------------------------------------------------------------
    def delete_duplicates(self):
        if not self.files_to_delete:
            return

        count = len(self.files_to_delete)
        confirm = messagebox.askyesno(
            "Confirm deletion",
            f"This will permanently delete {count} duplicate file(s).\n"
            "One copy of each duplicate group will be kept.\n\n"
            "This cannot be undone. Continue?"
        )
        if not confirm:
            return

        errors = []
        deleted = 0
        for path in self.files_to_delete:
            try:
                os.remove(path)
                deleted += 1
            except OSError as e:
                errors.append(f"{path}: {e}")

        self.tree.delete(*self.tree.get_children())
        self.files_to_delete = []
        self.duplicate_groups = []
        self.delete_btn.config(state="disabled")

        msg = f"Deleted {deleted} file(s)."
        if errors:
            msg += f"\n{len(errors)} file(s) could not be deleted."
        self.summary_label.config(text=msg)
        messagebox.showinfo("Done", msg)


def main():
    root = tk.Tk()
    app = DuplicateFinderApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
