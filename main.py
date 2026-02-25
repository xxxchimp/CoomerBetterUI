"""
Main application entry point for Coomer BetterUI
"""
import os
import sys

# Disable Qt's automatic DPI scaling for consistent pixel sizes across displays
os.environ["QT_SCALE_FACTOR"] = "1"

import logging
from logging.handlers import TimedRotatingFileHandler
import asyncio
from pathlib import Path
from PyQt6.QtWidgets import QApplication, QSplashScreen
from PyQt6.QtCore import Qt, QtMsgType, qInstallMessageHandler
from PyQt6.QtGui import QPixmap, QPainter, QColor, QFont
import qasync

from src.utils.file_utils import get_resource_path


def create_splash_screen(app: QApplication) -> QSplashScreen:
    """Create a splash screen for app startup."""
    # Create a pixmap for the splash (400x250 dark background)
    width, height = 400, 250
    pixmap = QPixmap(width, height)
    pixmap.fill(QColor("#1a1a1a"))

    # Paint content onto pixmap
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    # Draw accent line at top
    painter.fillRect(0, 0, width, 4, QColor("#ff6b35"))

    # App name
    painter.setPen(QColor("#e6e6e6"))
    title_font = QFont("Segoe UI", 24, QFont.Weight.Bold)
    painter.setFont(title_font)
    painter.drawText(0, 80, width, 40, Qt.AlignmentFlag.AlignCenter, "Coomer BetterUI")

    # Version
    painter.setPen(QColor("#9ca3af"))
    version_font = QFont("Segoe UI", 12)
    painter.setFont(version_font)
    painter.drawText(0, 120, width, 30, Qt.AlignmentFlag.AlignCenter, f"v{app.applicationVersion()}")

    # Loading text
    painter.setPen(QColor("#6b7280"))
    loading_font = QFont("Segoe UI", 10)
    painter.setFont(loading_font)
    painter.drawText(0, height - 50, width, 30, Qt.AlignmentFlag.AlignCenter, "Loading...")

    painter.end()

    # Create splash screen
    splash = QSplashScreen(pixmap)
    splash.setWindowFlags(Qt.WindowType.SplashScreen | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)

    return splash


# Qt message handler to suppress specific warnings
def qt_message_handler(mode, context, message):
    """Custom Qt message handler to filter out known harmless warnings."""
    # Suppress QFont::setPointSize warnings (happens during widget initialization with font inheritance)
    if "QFont::setPointSize: Point size <= 0" in message:
        return  # Silently ignore
    
    # Suppress stylesheet parse warning (Qt6 is stricter but styles still work)
    if "Could not parse application stylesheet" in message:
        return  # Silently ignore - non-critical
    
    # For other messages, use default logging
    if mode == QtMsgType.QtDebugMsg:
        logging.debug(f"Qt: {message}")
    elif mode == QtMsgType.QtInfoMsg:
        logging.info(f"Qt: {message}")
    elif mode == QtMsgType.QtWarningMsg:
        logging.warning(f"Qt: {message}")
    elif mode == QtMsgType.QtCriticalMsg:
        logging.error(f"Qt: {message}")
    elif mode == QtMsgType.QtFatalMsg:
        logging.critical(f"Qt: {message}")


# Setup logging
def setup_logging(db_manager=None):
    """Configure application logging"""
    from src.utils.logging_config import setup_logging as setup_categorized_logging
    
    # Setup categorized logging with database config
    logging_manager = setup_categorized_logging(db_manager)
    
    # Install Qt message handler to filter warnings
    qInstallMessageHandler(qt_message_handler)
    
    logger = logging.getLogger(__name__)
    logger.info("="*50)
    logger.info("Coomer BetterUI Starting")
    logger.info("="*50)
    
    return logging_manager

async def async_main(splash: QSplashScreen = None):
    """Async main function with Qt event loop integration"""
    logger = logging.getLogger(__name__)

    try:
        # Import modules
        from src.core import CoreContext
        from src.ui.browser.browser_window import BrowserWindow
        from PyQt6.QtGui import QFontDatabase

        # Update splash message
        if splash:
            splash.showMessage("Loading fonts...", Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter, QColor("#6b7280"))

        # Create Qt application
        app = QApplication.instance()
        
        # TEMPORARY: Disable all tooltips globally to prevent white flash windows
        app.setAttribute(Qt.ApplicationAttribute.AA_DisableSessionManager, True)
        app.setEffectEnabled(Qt.UIEffect.UI_FadeTooltip, False)
        app.setEffectEnabled(Qt.UIEffect.UI_AnimateTooltip, False)

        # Load embedded fonts
        fonts_dir = get_resource_path('resources', 'fonts')
        if fonts_dir.exists():
            font_count = 0
            for font_file in fonts_dir.glob("*.ttf"):
                font_id = QFontDatabase.addApplicationFont(str(font_file))
                if font_id != -1:
                    families = QFontDatabase.applicationFontFamilies(font_id)
                    logger.info(f"Loaded font: {font_file.name} -> {families}")
                    font_count += 1
                else:
                    logger.warning(f"Failed to load font: {font_file.name}")

            for font_file in fonts_dir.glob("*.otf"):
                font_id = QFontDatabase.addApplicationFont(str(font_file))
                if font_id != -1:
                    families = QFontDatabase.applicationFontFamilies(font_id)
                    logger.info(f"Loaded font: {font_file.name} -> {families}")
                    font_count += 1
                else:
                    logger.warning(f"Failed to load font: {font_file.name}")

            if font_count > 0:
                logger.info(f"Successfully loaded {font_count} font file(s)")
            else:
                logger.info("No font files found in resources/fonts directory")
        else:
            logger.info("Fonts directory not found - using system fonts")

        # Update splash message
        if splash:
            splash.showMessage("Loading theme...", Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter, QColor("#6b7280"))

        # Load and apply dark theme stylesheet
        style_path = get_resource_path('resources', 'styles', 'dark_theme_pro.qss')
        if style_path.exists():
            try:
                with open(style_path, 'r') as f:
                    app.setStyleSheet(f.read())
                logger.info("Dark theme applied to application")
            except Exception as e:
                logger.error(f"Error loading application stylesheet: {e}")

        # Set dark palette for system widgets
        from PyQt6.QtGui import QPalette
        palette = QPalette()
        palette.setColor(QPalette.ColorRole.Window, QColor("#1e1e1e"))
        palette.setColor(QPalette.ColorRole.WindowText, QColor("#e0e0e0"))
        palette.setColor(QPalette.ColorRole.Base, QColor("#252525"))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#2a2a2a"))
        palette.setColor(QPalette.ColorRole.ToolTipBase, QColor("#2a2a2a"))
        palette.setColor(QPalette.ColorRole.ToolTipText, QColor("#e0e0e0"))
        palette.setColor(QPalette.ColorRole.Text, QColor("#e0e0e0"))
        palette.setColor(QPalette.ColorRole.Button, QColor("#353535"))
        palette.setColor(QPalette.ColorRole.ButtonText, QColor("#e0e0e0"))
        palette.setColor(QPalette.ColorRole.Link, QColor("#ff6b35"))
        palette.setColor(QPalette.ColorRole.Highlight, QColor("#ff6b35"))
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
        app.setPalette(palette)

        # Set application icon (taskbar/title bar)
        icon_path = get_resource_path('resources', 'icon.ico')
        if icon_path.exists():
            from PyQt6.QtGui import QIcon
            app.setWindowIcon(QIcon(str(icon_path)))
            logger.info(f"Application icon loaded: {icon_path}")
        else:
            logger.info("No icon.ico found in resources/ - using default icon")

        # Update splash message
        if splash:
            splash.showMessage("Initializing...", Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter, QColor("#6b7280"))

        # Initialize core context
        logger.info("Initializing core context...")
        core = CoreContext()

        # Update splash message
        if splash:
            splash.showMessage("Creating UI...", Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter, QColor("#6b7280"))

        # Create and show main UI window
        logger.info("Creating browser window...")
        main_window = BrowserWindow(core)

        # Force Windows dark mode for title bar (Windows 10 1809+ / Windows 11)
        from src.utils.file_utils import apply_windows_dark_mode
        apply_windows_dark_mode(main_window)
        logger.info("Windows dark mode enabled for title bar")

        # Close splash and show main window
        if splash:
            splash.finish(main_window)

        main_window.show()

        logger.info("Application started successfully - main UI")

        # Keep reference to prevent garbage collection
        app._main_window = main_window
        app._core_context = core

    except Exception as e:
        logger.exception(f"Fatal error during startup: {e}")
        sys.exit(1)

def main():
    """Main application entry point"""
    # Setup logging (will load DB config after DB init)
    logging_manager = setup_logging()
    logger = logging.getLogger(__name__)

    try:
        # Create Qt application
        app = QApplication(sys.argv)
        app.setApplicationName("Coomer BetterUI")
        app.setApplicationVersion("2.2.1")
        app.setOrganizationName("CoomerBetterUI")

        # Show splash screen immediately
        splash = create_splash_screen(app)
        splash.show()
        app.processEvents()  # Ensure splash is painted

        # Create event loop with Qt integration
        loop = qasync.QEventLoop(app)
        asyncio.set_event_loop(loop)

        logger.info("Starting application with asyncio event loop integration")

        # Run async main with splash
        with loop:
            loop.run_until_complete(async_main(splash))
            loop.run_forever()

    except KeyboardInterrupt:
        logger.info("Application interrupted by user")
    except Exception as e:
        logger.exception(f"Fatal error: {e}")
        sys.exit(1)
    finally:
        logger.info("Shutting down...")
        if 'app' in locals():
            if hasattr(app, '_core_context'):
                app._core_context.close()
            try:
                from src.core.thumbnails import get_thumbnail_manager
                get_thumbnail_manager().shutdown()
            except Exception:
                pass
        logger.info("Application closed")

if __name__ == "__main__":
    main()
