from __future__ import annotations

import asyncio
from pathlib import Path
from typing import List, Optional, Tuple, Dict, Any

from PyQt6.QtCore import QThread, pyqtSignal

from src.core.download_manager import DownloadManager


class DownloadWorker(QThread):
    """
    Core download worker that wraps DownloadManager in a Qt thread.
    
    Signals:
        progress_update: (overall_progress, current_file_index, total_files)
        file_progress: (file_index, progress, downloaded_bytes, total_bytes, speed_str)
        file_started: (file_index, url, filename)
        file_completed: (file_index, success, error_message)
        completed: (results_list)
        failed: (error_string)
    """

    progress_update = pyqtSignal(float, int, int)  # progress, current, total
    file_progress = pyqtSignal(int, float, int, int, str)  # index, progress, downloaded, total, speed
    file_started = pyqtSignal(int, str, str)  # index, url, filename
    file_completed = pyqtSignal(int, bool, str)  # index, success, error
    completed = pyqtSignal(list)  # results
    failed = pyqtSignal(str)

    def __init__(
        self,
        download_manager: DownloadManager,
        urls: List[str],
        destination: Path,
        *,
        items: Optional[List[Tuple[str, Path]]] = None,
        create_zip: bool = False,
        zip_filename: Optional[str] = None,
    ):
        super().__init__()
        self._dm = download_manager
        self._urls = urls
        self._destination = destination
        self._items = items
        self._create_zip = create_zip
        self._zip_filename = zip_filename
        self._cancelled = False
        self._paused_items: set = set()  # Indices of paused items
        self._cancelled_items: set = set()  # Indices of cancelled items
        self._last_progress = 0.0
        self._last_current = 0
        self._file_progress: Dict[int, float] = {}  # index -> progress

    def cancel(self) -> None:
        """Cancel all downloads."""
        self._cancelled = True

    def cancel_item(self, index: int) -> None:
        """Cancel a specific download by index."""
        self._cancelled_items.add(index)

    def pause_item(self, index: int) -> None:
        """Pause a specific download by index."""
        self._paused_items.add(index)

    def resume_item(self, index: int) -> None:
        """Resume a specific download by index."""
        self._paused_items.discard(index)

    def is_item_paused(self, index: int) -> bool:
        """Check if a specific item is paused."""
        return index in self._paused_items

    def is_item_cancelled(self, index: int) -> bool:
        """Check if a specific item is cancelled."""
        return index in self._cancelled_items

    def run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            # Determine items to download
            if self._items is not None:
                download_items = self._items
            else:
                # Create items from urls
                download_items = [
                    (url, self._destination / Path(url).name)
                    for url in self._urls
                ]
            
            total = len(download_items)
            results = []
            
            for index, (url, dest) in enumerate(download_items):
                if self._cancelled:
                    # Mark remaining as cancelled
                    results.extend([False] * (total - index))
                    break
                
                if index in self._cancelled_items:
                    results.append(False)
                    self.file_completed.emit(index, False, "Cancelled")
                    continue
                
                # Emit file started
                filename = dest.name
                self.file_started.emit(index, url, filename)
                
                # Wait while paused
                while index in self._paused_items and not self._cancelled:
                    loop.run_until_complete(asyncio.sleep(0.5))
                
                if self._cancelled or index in self._cancelled_items:
                    results.append(False)
                    self.file_completed.emit(index, False, "Cancelled")
                    continue
                
                # Create per-file progress callback
                def make_file_progress_callback(file_index: int):
                    last_emit_time = [0.0]
                    import time
                    
                    def callback(downloaded: int, total_size: int):
                        if self._cancelled or file_index in self._cancelled_items:
                            return
                        
                        # Throttle updates to ~10 per second
                        current_time = time.time()
                        if current_time - last_emit_time[0] < 0.1:
                            return
                        last_emit_time[0] = current_time
                        
                        progress = (downloaded / total_size * 100) if total_size > 0 else 0
                        self._file_progress[file_index] = progress
                        
                        # Calculate speed (simplified)
                        speed_str = ""
                        
                        self.file_progress.emit(file_index, progress, downloaded, total_size, speed_str)
                        
                        # Update overall progress
                        overall = sum(self._file_progress.values()) / total
                        current = sum(1 for p in self._file_progress.values() if p >= 100)
                        
                        if overall > self._last_progress:
                            self._last_progress = overall
                            self._last_current = max(current, self._last_current)
                            self.progress_update.emit(overall, self._last_current, total)
                    
                    return callback
                
                # Download the file
                try:
                    success = loop.run_until_complete(
                        self._dm.download_file(
                            url,
                            dest,
                            progress_callback=make_file_progress_callback(index),
                        )
                    )
                    results.append(success)
                    self._file_progress[index] = 100.0 if success else self._file_progress.get(index, 0)
                    self.file_completed.emit(index, success, "" if success else "Download failed")
                except Exception as e:
                    results.append(False)
                    self.file_completed.emit(index, False, str(e))
                
                # Update overall progress
                completed_count = sum(1 for r in results if r is not None)
                overall = (completed_count / total) * 100
                self.progress_update.emit(overall, completed_count, total)
            
            if not self._cancelled:
                # Handle zip creation if requested
                if self._create_zip and results:
                    successful_paths = [
                        dest for (url, dest), success in zip(download_items, results)
                        if success and dest.exists()
                    ]
                    if successful_paths:
                        zip_name = self._zip_filename or "download.zip"
                        zip_path = self._destination / zip_name
                        loop.run_until_complete(
                            self._dm._create_zip(successful_paths, zip_path)
                        )
                
                self.completed.emit(results)
        except Exception as e:
            if not self._cancelled:
                self.failed.emit(str(e))
        finally:
            loop.close()
