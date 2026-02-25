"""
Download functionality mixin for BrowserWindow.

Extracted from browser_window.py to reduce file size and improve maintainability.
This mixin provides all download-related methods that BrowserWindow needs.

Usage:
    class BrowserWindow(QMainWindow, DownloadMixin):
        ...
"""
from pathlib import Path
from typing import Optional, List, Dict, Set, Tuple
import logging
import subprocess
import sys
import os

from PyQt6.QtWidgets import QMessageBox, QFileDialog, QMenu

from src.ui.common.utils import strip_html, sanitize_path_segment, sanitize_filename
from src.core.media_manager import MediaManager
from src.core.dto.post import PostDTO
from src.core.dto.file import FileDTO
from src.ui.widgets.download_panel import DownloadPanel, DownloadStatus
from src.core.jdownloader_export import JDownloaderExporter

logger = logging.getLogger(__name__)


def file_to_url(platform: str, file_dto: Optional[FileDTO]) -> Optional[str]:
    """Convert a FileDTO to a full media URL."""
    if not file_dto or not file_dto.path:
        return None
    return MediaManager.build_media_url(platform, file_dto.path)


def url_filename(url: str) -> str:
    """Extract filename from a URL."""
    if not url:
        return ""
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        return Path(parsed.path).name
    except Exception:
        return Path(url).name


class DownloadMixin:
    """
    Mixin class providing download functionality for BrowserWindow.

    This mixin expects the following attributes on the host class:
    - self.db: Database manager with get_config/set_config methods
    - self.current_platform: Current platform string
    - self.toast: ToastNotification widget
    - self.download_panel: DownloadPanel widget (new Chromium-style panel)
    - self.detail_view: GalleryPostView (optional, for single post downloads)
    - self.grid_view: PostGridView (for multi-select downloads)
    - self._download_thread: DownloadWorker thread (managed by mixin)
    - self._download_item_map: Dict mapping panel item IDs to worker indices

    And the following methods:
    - self._resolve_creator_name(platform, service, creator_id): Resolve creator display name
    """

    def _init_download_panel_signals(self):
        """Initialize download panel signal connections. Call this in __init__."""
        if hasattr(self, 'download_panel') and self.download_panel:
            self.download_panel.item_cancelled.connect(self._on_panel_item_cancelled)
            self.download_panel.item_paused.connect(self._on_panel_item_paused)
            self.download_panel.item_resumed.connect(self._on_panel_item_resumed)
            self.download_panel.item_retried.connect(self._on_panel_item_retried)
            self.download_panel.open_folder_requested.connect(self._on_open_download_folder)
        
        # Mapping from panel item ID to worker index
        self._download_item_map: Dict[int, int] = {}
        self._download_index_to_item: Dict[int, int] = {}  # Reverse mapping

    def _download_files(self, urls: List[str]) -> None:
        """
        Handle file downloads from detail view.

        Args:
            urls: List of file URLs to download
        """
        if not urls:
            return

        download_path = self._get_download_root()
        if not download_path:
            return

        structured = self._structured_downloads_enabled()
        items = None
        post_data = getattr(self.detail_view, "post_data", None)
        if post_data and structured:
            items = self._build_post_download_items(
                post_data,
                download_path,
                allowed_urls=set(urls),
            )
            if not items:
                items = None

        self._start_download_worker(urls, download_path, items=items)

    def _cancel_download(self) -> None:
        """Cancel all active downloads."""
        if hasattr(self, '_download_thread') and self._download_thread.isRunning():
            self._download_thread.cancel()
            if hasattr(self, 'download_panel'):
                # Mark all items as cancelled in the panel
                for item_id in self._download_item_map.keys():
                    self.download_panel.update_download(
                        item_id,
                        status=DownloadStatus.CANCELLED
                    )
            self.toast.show_message(
                "Download cancelled",
                icon_name='fa5s.times-circle',
                icon_color='#909090',
                duration=2000
            )

    def _on_panel_item_cancelled(self, item_id: int) -> None:
        """Handle cancel request from download panel."""
        if item_id in self._download_item_map:
            index = self._download_item_map[item_id]
            if hasattr(self, '_download_thread') and self._download_thread.isRunning():
                self._download_thread.cancel_item(index)

    def _on_panel_item_paused(self, item_id: int) -> None:
        """Handle pause request from download panel."""
        if item_id in self._download_item_map:
            index = self._download_item_map[item_id]
            if hasattr(self, '_download_thread') and self._download_thread.isRunning():
                self._download_thread.pause_item(index)

    def _on_panel_item_resumed(self, item_id: int) -> None:
        """Handle resume request from download panel."""
        if item_id in self._download_item_map:
            index = self._download_item_map[item_id]
            if hasattr(self, '_download_thread') and self._download_thread.isRunning():
                self._download_thread.resume_item(index)
                self.download_panel.update_download(
                    item_id,
                    status=DownloadStatus.DOWNLOADING
                )

    def _on_panel_item_retried(self, item_id: int) -> None:
        """Handle retry request from download panel."""
        item = self.download_panel.get_item(item_id)
        if not item:
            return
        
        # Reset the item state in the panel
        self.download_panel.update_download(
            item_id,
            status=DownloadStatus.PENDING,
            progress=0.0,
            downloaded_bytes=0,
            error_message=""
        )
        
        # Start a single-file retry download that reuses the existing item
        self._start_retry_download(item_id, item.url, item.destination)

    def _start_retry_download(self, item_id: int, url: str, destination: Path) -> None:
        """
        Start a retry download for a single file, reusing its existing panel item.
        
        Args:
            item_id: The existing panel item ID to update
            url: URL to download
            destination: Destination path
        """
        from src.core.download_manager import DownloadManager
        from src.core.download_worker import DownloadWorker
        
        # Check if a download is already running
        if hasattr(self, '_download_thread') and self._download_thread.isRunning():
            self.toast.show_message(
                "Download in progress - retry queued",
                icon_name='fa5s.info-circle',
                icon_color='#909090',
                duration=2500
            )
            return
        
        # Update panel item to downloading state
        self.download_panel.update_download(
            item_id,
            status=DownloadStatus.DOWNLOADING
        )
        
        # Create mappings for this single retry
        self._download_item_map = {item_id: 0}
        self._download_index_to_item = {0: item_id}
        
        # Initialize download manager
        download_manager = DownloadManager(self.db)
        
        # Setup worker for single file
        self._download_thread = DownloadWorker(
            download_manager,
            [url],
            destination.parent,
            items=[(url, destination)],
        )
        
        def on_file_started(index: int, url: str, filename: str):
            if index in self._download_index_to_item:
                self.download_panel.update_download(
                    self._download_index_to_item[index],
                    status=DownloadStatus.DOWNLOADING
                )
        
        def on_file_progress(index: int, progress: float, downloaded: int, total: int, speed: str):
            if index in self._download_index_to_item:
                self.download_panel.update_download(
                    self._download_index_to_item[index],
                    progress=progress,
                    downloaded_bytes=downloaded,
                    total_bytes=total,
                    speed=speed
                )
        
        def on_file_completed(index: int, success: bool, error: str):
            if index in self._download_index_to_item:
                pid = self._download_index_to_item[index]
                if success:
                    self.download_panel.update_download(
                        pid,
                        status=DownloadStatus.COMPLETED,
                        progress=100.0
                    )
                    self.toast.show_message(
                        "Retry successful",
                        icon_name='fa5s.check-circle',
                        icon_color='#4a9eff',
                        duration=2000
                    )
                else:
                    self.download_panel.update_download(
                        pid,
                        status=DownloadStatus.FAILED,
                        error_message=error
                    )
                    self.toast.show_message(
                        f"Retry failed: {error}",
                        icon_name='fa5s.exclamation-triangle',
                        icon_color='#ff6b35',
                        duration=3000
                    )
        
        def on_complete(results):
            pass  # Handled by on_file_completed
        
        # Connect signals
        self._download_thread.file_started.connect(on_file_started)
        self._download_thread.file_progress.connect(on_file_progress)
        self._download_thread.file_completed.connect(on_file_completed)
        self._download_thread.completed.connect(on_complete)
        self._download_thread.failed.connect(lambda err: self._on_download_failed(err))
        self._download_thread.start()

    def _on_open_download_folder(self, item_id: int) -> None:
        """Open the folder containing the downloaded file."""
        item = self.download_panel.get_item(item_id)
        if item and item.destination.exists():
            folder = item.destination.parent
            try:
                if sys.platform == 'win32':
                    subprocess.run(['explorer', '/select,', str(item.destination)])
                elif sys.platform == 'darwin':
                    subprocess.run(['open', '-R', str(item.destination)])
                else:
                    subprocess.run(['xdg-open', str(folder)])
            except Exception as e:
                logger.error(f"Failed to open folder: {e}")

    def _on_download_failed(self, error: str) -> None:
        """Handle download failure."""
        # Mark all pending items as failed
        if hasattr(self, 'download_panel'):
            for item_id in self._download_item_map.keys():
                item = self.download_panel.get_item(item_id)
                if item and item.status in (DownloadStatus.PENDING, DownloadStatus.DOWNLOADING):
                    self.download_panel.update_download(
                        item_id,
                        status=DownloadStatus.FAILED,
                        error_message=error
                    )
        self.toast.show_message(
            f"Download failed: {error}",
            icon_name='fa5s.exclamation-triangle',
            icon_color='#ff6b35',
            duration=4000
        )

    def _get_download_root(self) -> Optional[Path]:
        """
        Get the download directory, prompting user if not configured.

        Returns:
            Path to download directory, or None if user cancelled
        """
        download_dir = ""
        if hasattr(self.db, "get_config"):
            download_dir = self.db.get_config('download_dir', str(Path.home() / "Downloads"))
        if not download_dir:
            download_dir = QFileDialog.getExistingDirectory(
                self, "Select Download Directory", str(Path.home() / "Downloads")
            )
            if not download_dir:
                return None
            if hasattr(self.db, "set_config"):
                self.db.set_config('download_dir', download_dir)

        download_path = Path(download_dir)
        download_path.mkdir(parents=True, exist_ok=True)
        return download_path

    def _structured_downloads_enabled(self) -> bool:
        """Check if structured downloads (organized folders) are enabled."""
        if hasattr(self.db, "get_config"):
            return self.db.get_config('structured_downloads', 'true') == 'true'
        return True

    def _dedupe_destination(self, destination: Path, seen: Dict[Path, int]) -> Path:
        """
        Deduplicate a destination path to avoid overwrites.

        Args:
            destination: Target file path
            seen: Dictionary tracking already-used paths

        Returns:
            Unique destination path (may have _N suffix)
        """
        if destination in seen or destination.exists():
            count = seen.get(destination, 1)
            while True:
                count += 1
                candidate = destination.with_name(f"{destination.stem}_{count}{destination.suffix}")
                if candidate not in seen and not candidate.exists():
                    seen[candidate] = 1
                    return candidate
        seen[destination] = 1
        return destination

    def _build_post_download_items(
        self,
        post: PostDTO,
        base_dir: Path,
        *,
        allowed_urls: Optional[Set[str]] = None,
    ) -> List[Tuple[str, Path]]:
        """
        Build list of (url, destination) tuples for a post's files.

        Creates structured paths: platform/service/creator/post_title/filename

        Args:
            post: Post to build download items for
            base_dir: Base download directory
            allowed_urls: If provided, only include these URLs

        Returns:
            List of (url, destination_path) tuples
        """
        if not post:
            return []
        platform = sanitize_path_segment(self.current_platform, "platform")
        service = sanitize_path_segment(post.service or "unknown", "unknown")

        creator_id = post.user_id or ""
        creator_name = self._resolve_creator_name(self.current_platform, post.service, creator_id)
        creator_segment = sanitize_path_segment(creator_name or creator_id, creator_id or "creator")

        title_raw = strip_html(post.title or post.substring or "")
        title_segment = sanitize_path_segment(title_raw, f"post_{post.id}")

        post_dir = base_dir / platform / service / creator_segment / title_segment

        files = []
        if post.file:
            files.append(post.file)
        if post.attachments:
            files.extend(post.attachments)

        items = []
        seen = {}
        for index, file_dto in enumerate(files, start=1):
            url = file_to_url(self.current_platform, file_dto)
            if not url:
                continue
            if allowed_urls and url not in allowed_urls:
                continue
            raw_name = file_dto.name or url_filename(url)
            safe_name = sanitize_filename(raw_name, f"file_{index}")
            destination = post_dir / safe_name
            destination = self._dedupe_destination(destination, seen)
            items.append((url, destination))

        return items

    def _start_download_worker(
        self,
        urls: List[str],
        download_path: Path,
        *,
        items: Optional[List[Tuple[str, Path]]] = None,
    ) -> None:
        """
        Start a background download worker.

        Args:
            urls: List of URLs to download
            download_path: Base download directory
            items: Optional list of (url, destination) tuples for structured downloads
        """
        from src.core.download_manager import DownloadManager
        from src.core.download_worker import DownloadWorker

        if hasattr(self, '_download_thread') and self._download_thread.isRunning():
            logger.info("Download already in progress; ignoring new request")
            self.toast.show_message(
                "Download already in progress",
                icon_name='fa5s.info-circle',
                icon_color='#909090',
                duration=2500
            )
            return

        # Determine download items
        if items:
            download_items = items
        else:
            download_items = [(url, download_path / Path(url).name) for url in urls]

        # Add items to download panel and create mapping
        self._download_item_map.clear()
        self._download_index_to_item.clear()
        
        if hasattr(self, 'download_panel'):
            for index, (url, dest) in enumerate(download_items):
                item_id = self.download_panel.add_download(url, dest.name, dest)
                self._download_item_map[item_id] = index
                self._download_index_to_item[index] = item_id

        # Initialize download manager
        download_manager = DownloadManager(self.db)

        # Setup worker
        self._download_thread = DownloadWorker(
            download_manager,
            urls,
            download_path,
            items=items,
        )

        def on_file_started(index: int, url: str, filename: str):
            """Handle file download started."""
            if index in self._download_index_to_item:
                item_id = self._download_index_to_item[index]
                self.download_panel.update_download(
                    item_id,
                    status=DownloadStatus.DOWNLOADING
                )

        def on_file_progress(index: int, progress: float, downloaded: int, total: int, speed: str):
            """Handle per-file progress update."""
            if index in self._download_index_to_item:
                item_id = self._download_index_to_item[index]
                self.download_panel.update_download(
                    item_id,
                    progress=progress,
                    downloaded_bytes=downloaded,
                    total_bytes=total,
                    speed=speed
                )

        def on_file_completed(index: int, success: bool, error: str):
            """Handle file download completed."""
            if index in self._download_index_to_item:
                item_id = self._download_index_to_item[index]
                if success:
                    self.download_panel.update_download(
                        item_id,
                        status=DownloadStatus.COMPLETED,
                        progress=100.0
                    )
                else:
                    self.download_panel.update_download(
                        item_id,
                        status=DownloadStatus.FAILED,
                        error_message=error
                    )

        def on_complete(results):
            success_count = sum(1 for r in results if r)
            failed_count = len(results) - success_count
            
            # Show toast notification
            if failed_count > 0:
                self.toast.show_message(
                    f"Downloaded {success_count} file(s) - {failed_count} failed",
                    icon_name='fa5s.exclamation-triangle',
                    icon_color='#ff6b35',
                    duration=4000
                )
            else:
                self.toast.show_message(
                    f"Downloaded {success_count} file(s) successfully",
                    icon_name='fa5s.check-circle',
                    icon_color='#4a9eff',
                    duration=3000
                )

        # Connect signals
        self._download_thread.file_started.connect(on_file_started)
        self._download_thread.file_progress.connect(on_file_progress)
        self._download_thread.file_completed.connect(on_file_completed)
        self._download_thread.completed.connect(on_complete)
        self._download_thread.failed.connect(lambda err: self._on_download_failed(err))
        self._download_thread.start()

    def _jdownloader_enabled(self) -> bool:
        """Check if JDownloader export is enabled in settings."""
        if hasattr(self.db, 'get_config'):
            return self.db.get_config('jdownloader_enabled', 'false') == 'true'
        return False

    def _get_jdownloader_watch_dir(self) -> Optional[Path]:
        """
        Get the JDownloader watch folder path.
        
        Returns configured path, or auto-detects the default path if not set.
        Returns None if JDownloader is not enabled or no valid path found.
        """
        if not self._jdownloader_enabled():
            return None
        
        # Check configured path first
        if hasattr(self.db, 'get_config'):
            configured_path = self.db.get_config('jdownloader_watch_dir', '')
            if configured_path:
                path = Path(configured_path)
                if path.exists() or path.parent.exists():
                    path.mkdir(parents=True, exist_ok=True)
                    return path
        
        # Auto-detect default JDownloader watch folder
        return JDownloaderExporter.find_default_watch_folder()

    def _export_to_jdownloader(self, items: List[Tuple[str, Path]], package_name: str = None) -> bool:
        """
        Export download items to JDownloader crawljob file.
        
        Args:
            items: List of (url, destination_path) tuples
            package_name: Optional package name for grouping
            
        Returns:
            True if export successful
        """
        watch_dir = self._get_jdownloader_watch_dir()
        if not watch_dir:
            QMessageBox.warning(
                self,
                "JDownloader Not Configured",
                "JDownloader watch folder is not configured.\n\n"
                "Please configure it in Settings → Downloads → JDownloader Integration"
            )
            return False
        
        if not items:
            return False
        
        try:
            exporter = JDownloaderExporter()
            
            # Add entries for each file
            for url, destination in items:
                exporter.add_entry(
                    url=url,
                    download_folder=str(destination.parent),
                    filename=destination.name,
                    package_name=package_name,
                    enabled=True,
                    auto_start=True,
                    force_download=False,
                    auto_confirm="TRUE",
                )
            
            # Export to crawljob file
            crawljob_path = exporter.export_to_file(watch_dir)
            
            logger.info(f"Exported {len(items)} URLs to JDownloader: {crawljob_path}")
            
            self.toast.show_message(
                f"Exported {len(items)} files to JDownloader",
                icon_name='fa5s.external-link-alt',
                icon_color='#4a9eff',
                duration=3000
            )
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to export to JDownloader: {e}")
            QMessageBox.critical(
                self,
                "Export Failed",
                f"Failed to export to JDownloader:\n{e}"
            )
            return False

    def _show_download_menu(self, items: List[Tuple[str, Path]], post_count: int) -> None:
        """
        Show download method selection menu.
        
        Args:
            items: List of (url, destination_path) tuples
            post_count: Number of posts being downloaded
        """
        file_count = len(items)
        
        # If JDownloader is not enabled, just start the download directly
        if not self._jdownloader_enabled():
            self._confirm_and_start_download(items, post_count)
            return
        
        # Show menu to choose download method
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #2d2d30;
                border: 1px solid #3f3f46;
                padding: 4px;
            }
            QMenu::item {
                padding: 6px 20px;
                color: #cccccc;
            }
            QMenu::item:selected {
                background-color: #094771;
            }
        """)
        
        builtin_action = menu.addAction(f"Download with Built-in ({file_count} files)")
        builtin_action.setToolTip("Use the application's built-in download manager")
        
        jd_action = menu.addAction(f"Export to JDownloader ({file_count} files)")
        jd_action.setToolTip("Export as .crawljob file for JDownloader")
        
        menu.addSeparator()
        cancel_action = menu.addAction("Cancel")
        
        # Show menu at cursor position
        from PyQt6.QtGui import QCursor
        action = menu.exec(QCursor.pos())
        
        if action == builtin_action:
            self._confirm_and_start_download(items, post_count)
        elif action == jd_action:
            # Generate a package name from first post info
            package_name = None
            if hasattr(self, 'current_platform'):
                package_name = f"{self.current_platform}_{post_count}_posts"
            self._export_to_jdownloader(items, package_name)
            self._clear_post_selection()

    def _confirm_and_start_download(self, items: List[Tuple[str, Path]], post_count: int) -> None:
        """
        Show confirmation dialog and start download.
        
        Args:
            items: List of (url, destination_path) tuples
            post_count: Number of posts being downloaded
        """
        download_path = self._get_download_root()
        if not download_path:
            return
        
        reply = QMessageBox.question(
            self,
            "Confirm Download",
            f"Download {len(items)} file(s) from {post_count} post(s)?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            urls = [url for url, _ in items]
            self._start_download_worker(
                urls,
                download_path,
                items=items,
            )
            self._clear_post_selection()

    def _download_selected_posts(self) -> None:
        """Download all files from selected posts in grid view."""
        selected_posts = self.grid_view.get_selected_posts()
        if not selected_posts:
            QMessageBox.information(self, "No Selection", "Please select posts to download")
            return

        download_path = self._get_download_root()
        if not download_path:
            return

        structured = self._structured_downloads_enabled()

        # Collect all file URLs from selected posts
        urls = []
        items = []
        for post in selected_posts:
            if structured:
                post_items = self._build_post_download_items(post, download_path)
                if post_items:
                    items.extend(post_items)
                    urls.extend([url for url, _ in post_items])
            else:
                file_url = file_to_url(self.current_platform, post.file)
                if file_url:
                    urls.append(file_url)
                for attachment in post.attachments:
                    file_url = file_to_url(self.current_platform, attachment)
                    if file_url:
                        urls.append(file_url)

        has_files = bool(items) if structured else bool(urls)
        if not has_files:
            QMessageBox.information(self, "No Files", "No files found in selected posts")
            return

        # Build items list for non-structured mode
        if not structured and urls:
            items = [(url, download_path / Path(url).name) for url in urls]

        # Show download method menu (or confirm dialog if JDownloader disabled)
        self._show_download_menu(items, len(selected_posts))
