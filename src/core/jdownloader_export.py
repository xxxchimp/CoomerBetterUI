"""
JDownloader2 Crawljob Export

Generates .crawljob files for JDownloader2's Directory Watch extension.
This allows bulk downloads to be handled by JDownloader instead of the built-in downloader.

Crawljob format supports both properties format and JSON array format.
"""
import json
import logging
from pathlib import Path
from typing import List, Optional, Tuple
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum

logger = logging.getLogger(__name__)


class BooleanStatus(str, Enum):
    """JDownloader boolean status values."""
    TRUE = "TRUE"
    FALSE = "FALSE"
    UNSET = "UNSET"


class Priority(str, Enum):
    """JDownloader priority values."""
    HIGHEST = "HIGHEST"
    HIGHER = "HIGHER"
    HIGH = "HIGH"
    DEFAULT = "DEFAULT"
    LOW = "LOW"
    LOWER = "LOWER"
    LOWEST = "LOWEST"


class JobType(str, Enum):
    """JDownloader job type values."""
    NORMAL = "NORMAL"
    FORCED = "FORCED"


@dataclass
class CrawljobEntry:
    """
    Represents a single entry in a crawljob file.
    
    All fields are optional except 'text' which contains the URL(s).
    """
    text: str  # URL or URLs (required)
    filename: Optional[str] = None  # Override filename (only if text is single URL)
    downloadFolder: Optional[str] = None  # Download destination folder
    packageName: Optional[str] = None  # Package name to group downloads
    enabled: Optional[BooleanStatus] = None
    autoStart: BooleanStatus = BooleanStatus.TRUE
    autoConfirm: BooleanStatus = BooleanStatus.TRUE
    forcedStart: BooleanStatus = BooleanStatus.UNSET
    priority: Priority = Priority.DEFAULT
    chunks: int = 0  # 0 = default/auto
    comment: Optional[str] = None
    downloadPassword: Optional[str] = None
    extractPasswords: Optional[List[str]] = None
    extractAfterDownload: BooleanStatus = BooleanStatus.UNSET
    deepAnalyseEnabled: bool = False
    addOfflineLink: bool = True
    overwritePackagizerEnabled: bool = False
    setBeforePackagizerEnabled: bool = True
    type: JobType = JobType.NORMAL

    def to_properties_format(self) -> str:
        """Convert entry to JDownloader properties format."""
        lines = ["->NEW ENTRY<-"]
        
        # Required field
        lines.append(f"   text={self.text}")
        
        # Optional fields - only include if set
        if self.filename:
            lines.append(f"   filename={self.filename}")
        if self.downloadFolder:
            # Escape backslashes for Windows paths
            folder = self.downloadFolder.replace("\\", "\\\\")
            lines.append(f"   downloadFolder={folder}")
        if self.packageName:
            lines.append(f"   packageName={self.packageName}")
        if self.comment:
            lines.append(f"   comment={self.comment}")
        if self.downloadPassword:
            lines.append(f"   downloadPassword={self.downloadPassword}")
        if self.extractPasswords:
            lines.append(f"   extractPasswords={json.dumps(self.extractPasswords)}")
        
        # Boolean status fields
        lines.append(f"   autoStart={self.autoStart.value}")
        lines.append(f"   autoConfirm={self.autoConfirm.value}")
        
        if self.enabled is not None:
            lines.append(f"   enabled={self.enabled.value}")
        if self.forcedStart != BooleanStatus.UNSET:
            lines.append(f"   forcedStart={self.forcedStart.value}")
        if self.extractAfterDownload != BooleanStatus.UNSET:
            lines.append(f"   extractAfterDownload={self.extractAfterDownload.value}")
        
        # Other fields
        if self.priority != Priority.DEFAULT:
            lines.append(f"   priority={self.priority.value}")
        if self.chunks != 0:
            lines.append(f"   chunks={self.chunks}")
        
        lines.append(f"   deepAnalyseEnabled={str(self.deepAnalyseEnabled).lower()}")
        lines.append(f"   addOfflineLink={str(self.addOfflineLink).lower()}")
        
        return "\n".join(lines)

    def to_dict(self) -> dict:
        """Convert entry to dictionary for JSON format."""
        result = {"text": self.text}
        
        if self.filename:
            result["filename"] = self.filename
        if self.downloadFolder:
            result["downloadFolder"] = self.downloadFolder
        if self.packageName:
            result["packageName"] = self.packageName
        if self.comment:
            result["comment"] = self.comment
        if self.downloadPassword:
            result["downloadPassword"] = self.downloadPassword
        if self.extractPasswords:
            result["extractPasswords"] = self.extractPasswords
        if self.enabled is not None:
            result["enabled"] = self.enabled.value
        
        result["autoStart"] = self.autoStart.value
        result["autoConfirm"] = self.autoConfirm.value
        result["forcedStart"] = self.forcedStart.value
        result["extractAfterDownload"] = self.extractAfterDownload.value
        result["priority"] = self.priority.value
        result["chunks"] = self.chunks
        result["deepAnalyseEnabled"] = self.deepAnalyseEnabled
        result["addOfflineLink"] = self.addOfflineLink
        result["overwritePackagizerEnabled"] = self.overwritePackagizerEnabled
        result["setBeforePackagizerEnabled"] = self.setBeforePackagizerEnabled
        result["type"] = self.type.value
        
        return result


class JDownloaderExporter:
    """
    Exports download items to JDownloader2 crawljob files.
    """
    
    def __init__(self, watch_folder: Optional[Path] = None):
        """
        Initialize exporter.
        
        Args:
            watch_folder: JDownloader's folderwatch directory.
                         Typically: ~/AppData/Local/JDownloader 2.0/folderwatch/
        """
        self.watch_folder = watch_folder
        self.entries: List[CrawljobEntry] = []
    
    @staticmethod
    def get_default_watch_folder() -> Path:
        """Get the default JDownloader folderwatch path."""
        import os
        if os.name == 'nt':  # Windows
            return Path.home() / "AppData" / "Local" / "JDownloader 2.0" / "folderwatch"
        elif os.name == 'darwin':  # macOS
            return Path.home() / "Library" / "Application Support" / "JDownloader 2.0" / "folderwatch"
        else:  # Linux
            return Path.home() / ".jdownloader2" / "folderwatch"
    
    @staticmethod
    def find_default_watch_folder() -> Optional[Path]:
        """
        Find the JDownloader folderwatch folder.
        
        Tries several common locations and returns the first one that exists.
        Returns None if not found.
        """
        import os
        
        candidates = []
        
        if os.name == 'nt':  # Windows
            appdata = os.environ.get('APPDATA', '')
            localappdata = os.environ.get('LOCALAPPDATA', '')
            if appdata:
                candidates.append(Path(appdata) / "JDownloader 2.0" / "folderwatch")
            if localappdata:
                candidates.append(Path(localappdata) / "JDownloader 2.0" / "folderwatch")
            candidates.append(Path.home() / "JDownloader 2.0" / "folderwatch")
        elif os.name == 'darwin':  # macOS
            candidates.extend([
                Path.home() / "Library" / "Application Support" / "JDownloader 2.0" / "folderwatch",
                Path.home() / "JDownloader 2.0" / "folderwatch",
            ])
        else:  # Linux
            candidates.extend([
                Path.home() / ".jd2" / "folderwatch",
                Path.home() / ".jdownloader2" / "folderwatch",
                Path.home() / "JDownloader 2.0" / "folderwatch",
            ])
        
        for path in candidates:
            # Check if parent JDownloader folder exists
            if path.parent.exists():
                # Create folderwatch if it doesn't exist
                path.mkdir(parents=True, exist_ok=True)
                return path
        
        return None
    
    def add_entry(
        self,
        url: str,
        *,
        download_folder: Optional[str] = None,
        filename: Optional[str] = None,
        package_name: Optional[str] = None,
        enabled: bool = True,
        auto_start: bool = True,
        force_download: bool = False,
        auto_confirm: str = "TRUE",
        priority: str = "DEFAULT",
        chunks: int = 0,
        comment: Optional[str] = None,
    ) -> None:
        """
        Add a single download entry.
        
        Args:
            url: Download URL
            download_folder: Destination folder
            filename: Override filename (optional)
            package_name: Package name for grouping
            enabled: Whether download is enabled
            auto_start: Auto-start download
            force_download: Force download even if file exists
            auto_confirm: Auto-confirm setting
            priority: Download priority
            chunks: Number of download chunks (0=auto)
            comment: Optional comment
        """
        entry = CrawljobEntry(
            text=url,
            filename=filename,
            downloadFolder=download_folder,
            packageName=package_name,
            enabled=BooleanStatus.TRUE if enabled else BooleanStatus.FALSE,
            autoStart=BooleanStatus.TRUE if auto_start else BooleanStatus.FALSE,
            autoConfirm=BooleanStatus(auto_confirm) if auto_confirm in ["TRUE", "FALSE", "UNSET"] else BooleanStatus.TRUE,
            forcedStart=BooleanStatus.TRUE if force_download else BooleanStatus.UNSET,
            priority=Priority(priority) if priority in [p.value for p in Priority] else Priority.DEFAULT,
            chunks=chunks,
            comment=comment,
        )
        self.entries.append(entry)

    def export_to_file(self, watch_folder: Optional[Path] = None, use_json_format: bool = False) -> Path:
        """
        Export all added entries to a crawljob file.
        
        Args:
            watch_folder: Output folder. If None, uses configured or default watch folder.
            use_json_format: Use JSON format instead of properties format.
            
        Returns:
            Path to the created crawljob file
        """
        if not self.entries:
            raise ValueError("No entries to export")
        
        folder = watch_folder or self.watch_folder or self.find_default_watch_folder()
        if not folder:
            raise ValueError("No watch folder specified and could not find JDownloader installation")
        
        folder.mkdir(parents=True, exist_ok=True)
        
        if use_json_format:
            content = json.dumps([e.to_dict() for e in self.entries], indent=2)
        else:
            content = "\n\n".join(e.to_properties_format() for e in self.entries)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = folder / f"coomer_export_{timestamp}.crawljob"
        
        output_path.write_text(content, encoding="utf-8")
        
        logger.info(f"Exported {len(self.entries)} entries to {output_path}")
        return output_path
    
    def clear(self) -> None:
        """Clear all entries."""
        self.entries.clear()
    
    def create_crawljob(
        self,
        items: List[Tuple[str, Path]],
        *,
        package_name: Optional[str] = None,
        auto_start: bool = True,
        use_json_format: bool = False,
        group_by_folder: bool = True,
    ) -> str:
        """
        Create crawljob content from download items.
        
        Args:
            items: List of (url, destination_path) tuples
            package_name: Optional package name (overrides auto-generated)
            auto_start: Whether to auto-start downloads
            use_json_format: Use JSON array format instead of properties
            group_by_folder: Group items by destination folder into packages
            
        Returns:
            Crawljob file content as string
        """
        entries = []
        
        if group_by_folder:
            # Group items by destination folder
            folder_groups: dict[Path, List[Tuple[str, Path]]] = {}
            for url, dest in items:
                folder = dest.parent
                if folder not in folder_groups:
                    folder_groups[folder] = []
                folder_groups[folder].append((url, dest))
            
            for folder, group_items in folder_groups.items():
                pkg_name = package_name or folder.name
                for url, dest in group_items:
                    entry = CrawljobEntry(
                        text=url,
                        filename=dest.name,
                        downloadFolder=str(folder),
                        packageName=pkg_name,
                        autoStart=BooleanStatus.TRUE if auto_start else BooleanStatus.FALSE,
                        autoConfirm=BooleanStatus.TRUE,
                    )
                    entries.append(entry)
        else:
            # Single flat list
            for url, dest in items:
                entry = CrawljobEntry(
                    text=url,
                    filename=dest.name,
                    downloadFolder=str(dest.parent),
                    packageName=package_name,
                    autoStart=BooleanStatus.TRUE if auto_start else BooleanStatus.FALSE,
                    autoConfirm=BooleanStatus.TRUE,
                )
                entries.append(entry)
        
        if use_json_format:
            return json.dumps([e.to_dict() for e in entries], indent=2)
        else:
            return "\n\n".join(e.to_properties_format() for e in entries)
    
    def export_items_to_file(
        self,
        items: List[Tuple[str, Path]],
        output_path: Optional[Path] = None,
        *,
        package_name: Optional[str] = None,
        auto_start: bool = True,
        use_json_format: bool = False,
    ) -> Path:
        """
        Export download items to a crawljob file.
        
        Args:
            items: List of (url, destination_path) tuples
            output_path: Output file path. If None, uses watch_folder with timestamp name.
            package_name: Optional package name
            auto_start: Whether to auto-start downloads
            use_json_format: Use JSON format instead of properties
            
        Returns:
            Path to the created crawljob file
        """
        if not items:
            raise ValueError("No items to export")
        
        content = self.create_crawljob(
            items,
            package_name=package_name,
            auto_start=auto_start,
            use_json_format=use_json_format,
        )
        
        if output_path is None:
            if self.watch_folder is None:
                self.watch_folder = self.get_default_watch_folder()
            
            self.watch_folder.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = self.watch_folder / f"coomer_export_{timestamp}.crawljob"
        
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(content, encoding="utf-8")
        
        logger.info(f"Exported {len(items)} items to {output_path}")
        return output_path
    
    def export_urls(
        self,
        urls: List[str],
        download_folder: Path,
        *,
        package_name: Optional[str] = None,
        auto_start: bool = True,
    ) -> Path:
        """
        Export a list of URLs to a crawljob file.
        
        Args:
            urls: List of URLs to download
            download_folder: Destination folder for downloads
            package_name: Optional package name
            auto_start: Whether to auto-start downloads
            
        Returns:
            Path to the created crawljob file
        """
        # Create items from URLs (let JDownloader determine filenames)
        items = [(url, download_folder / Path(url).name) for url in urls]
        return self.export_items_to_file(
            items,
            package_name=package_name,
            auto_start=auto_start,
        )


def export_to_jdownloader(
    items: List[Tuple[str, Path]],
    watch_folder: Optional[Path] = None,
    package_name: Optional[str] = None,
    auto_start: bool = True,
) -> Path:
    """
    Convenience function to export items to JDownloader.
    
    Args:
        items: List of (url, destination_path) tuples
        watch_folder: JDownloader watch folder (uses default if None)
        package_name: Optional package name
        auto_start: Whether to auto-start downloads
        
    Returns:
        Path to the created crawljob file
    """
    exporter = JDownloaderExporter(watch_folder)
    return exporter.export_items_to_file(
        items,
        package_name=package_name,
        auto_start=auto_start,
    )
