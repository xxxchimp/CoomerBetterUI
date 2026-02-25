"""
Build script for creating executable using PyInstaller

Also provides a development launcher that suppresses Qt window artifacts.
"""
import PyInstaller.__main__
from pathlib import Path
import sys
import os

# Qt environment variables to suppress transient window artifacts on Windows
QT_ENV_VARS = {
    # Disable Qt's automatic high-DPI scaling which can cause window flashes
    'QT_AUTO_SCREEN_SCALE_FACTOR': '0',
    # Use software OpenGL to avoid driver-related window artifacts
    'QT_QUICK_BACKEND': 'software',
    # Disable QML disk cache which can cause startup artifacts
    'QML_DISABLE_DISK_CACHE': '1',
    # Windows-specific: reduce DWM (Desktop Window Manager) composition artifacts
    'QT_QPA_PLATFORM': 'windows:darkmode=1,nodrawtext=0',
}


def create_runtime_hook(project_root: Path) -> Path:
    """Create a PyInstaller runtime hook to set Qt environment variables."""
    hooks_dir = project_root / "build_hooks"
    hooks_dir.mkdir(exist_ok=True)

    hook_path = hooks_dir / "hook-qt-env.py"
    hook_content = '''"""
PyInstaller runtime hook to configure Qt environment for Windows.
Suppresses transient window artifacts.
"""
import os
import sys

# Set Qt environment variables before any Qt imports
os.environ.setdefault('QT_AUTO_SCREEN_SCALE_FACTOR', '0')
os.environ.setdefault('QML_DISABLE_DISK_CACHE', '1')

# Windows-specific optimizations
if sys.platform == 'win32':
    # Force dark title bar even if QT_QPA_PLATFORM is set externally
    os.environ['QT_QPA_PLATFORM'] = 'windows:darkmode=1,nodrawtext=0'

    # NOTE: We no longer patch subprocess.Popen globally here.
    # The global CREATE_NO_WINDOW patch was causing BEX64 crashes in Qt6Core.dll
    # because Qt uses subprocess internally for various operations.
    # 
    # Instead, ffmpeg/ffprobe console hiding is handled locally in the code
    # that calls those tools (e.g., in media/processor.py or video_player.py).
'''
    hook_path.write_text(hook_content)
    return hook_path


def setup_dev_environment():
    """Set up environment variables for development mode to suppress Qt window artifacts."""
    if sys.platform == 'win32':
        for key, value in QT_ENV_VARS.items():
            os.environ.setdefault(key, value)
        # NOTE: No longer patching subprocess globally - handled locally in ffmpeg calls


def run_dev():
    """Run the application in development mode with Qt optimizations."""
    print("Starting in development mode with Qt window artifact suppression...")

    # Apply environment setup BEFORE any other imports
    setup_dev_environment()
    print("  ✓ Qt environment variables configured")

    # Import and run main after environment is configured
    project_root = Path(__file__).parent
    sys.path.insert(0, str(project_root))

    # Now import and run
    import main
    main.main()


def build():
    """Build executable with PyInstaller"""

    # Project root
    project_root = Path(__file__).parent

    # Create resources directory if needed
    resources_dir = project_root / "resources"
    resources_dir.mkdir(exist_ok=True)

    # Create runtime hook for Qt environment
    hook_path = create_runtime_hook(project_root)
    print(f"Created runtime hook: {hook_path}")

    # Check for icon, create placeholder if missing
    icon_path = resources_dir / "icon.ico"
    if not icon_path.exists():
        print("Warning: icon.ico not found, using default")
        icon_arg = []
    else:
        icon_arg = [f'--icon={icon_path}']

    # Find mpv DLL
    mpv_dll = project_root / "venv" / "Scripts" / "libmpv-2.dll"
    if not mpv_dll.exists():
        print(f"Warning: libmpv-2.dll not found at {mpv_dll}")
        mpv_arg = []
    else:
        # Bundle mpv DLL into the mpv folder so video_player.py can find it
        mpv_arg = [f'--add-binary={mpv_dll};mpv']

    # PyInstaller options - using onedir mode for better compatibility
    # with native DLLs (mpv) and predictable file paths
    options = [
        'main.py',  # Entry point
        '--name=CoomerBetterUI',
        '--windowed',  # No console window
        '--onedir',  # Directory mode - avoids temp MEI extraction issues
        *icon_arg,  # Application icon (if available)
        *mpv_arg,  # mpv DLL for video playback
        '--add-data=resources;resources',  # Include resources
        f'--runtime-hook={hook_path}',  # Qt environment hook
        '--hidden-import=PyQt6',
        '--hidden-import=PyQt6.QtWebEngineWidgets',
        '--hidden-import=PyQt6.QtWebChannel',
        '--hidden-import=ffmpeg',
        '--collect-all=PyQt6',
        '--clean',  # Clean build cache
        f'--distpath={project_root / "dist"}',
        f'--workpath={project_root / "build"}',
        f'--specpath={project_root}',
    ]

    # Run PyInstaller
    print("Building executable (onedir mode)...")
    PyInstaller.__main__.run(options)

    print("Build complete!")
    print(f"Output folder: {project_root / 'dist' / 'CoomerBetterUI'}")
    print(f"Executable: {project_root / 'dist' / 'CoomerBetterUI' / 'CoomerBetterUI.exe'}")


def build_no_hook():
    """Build executable without runtime hook (for debugging)."""
    project_root = Path(__file__).parent
    resources_dir = project_root / "resources"
    resources_dir.mkdir(exist_ok=True)

    icon_path = resources_dir / "icon.ico"
    icon_arg = [f'--icon={icon_path}'] if icon_path.exists() else []

    # Find mpv DLL
    mpv_dll = project_root / "venv" / "Scripts" / "libmpv-2.dll"
    mpv_arg = [f'--add-binary={mpv_dll};mpv'] if mpv_dll.exists() else []

    options = [
        'main.py',
        '--name=CoomerBetterUI',
        '--windowed',
        '--onedir',  # Directory mode
        *icon_arg,
        *mpv_arg,
        '--add-data=resources;resources',
        '--hidden-import=PyQt6',
        '--hidden-import=PyQt6.QtWebEngineWidgets',
        '--hidden-import=PyQt6.QtWebChannel',
        '--hidden-import=ffmpeg',
        '--collect-all=PyQt6',
        '--clean',
        f'--distpath={project_root / "dist"}',
        f'--workpath={project_root / "build"}',
        f'--specpath={project_root}',
    ]

    print("Building executable WITHOUT runtime hook (onedir mode)...")
    PyInstaller.__main__.run(options)
    print("Build complete!")
    print(f"Output folder: {project_root / 'dist' / 'CoomerBetterUI'}")
    print(f"Executable: {project_root / 'dist' / 'CoomerBetterUI' / 'CoomerBetterUI.exe'}")


def print_usage():
    """Print usage information."""
    print("""
Coomer BetterUI Build Script
============================

Usage:
    python build.py              - Build the executable with runtime hook (onedir)
    python build.py --no-hook    - Build without runtime hook (for debugging)
    python build.py --dev        - Run in development mode
    python build.py --help       - Show this help message

Build Output:
    The build uses onedir mode, creating a folder at dist/CoomerBetterUI/
    containing the executable and all dependencies. This avoids temp MEI
    extraction issues with native DLLs like mpv.

Development Mode:
    Running with --dev sets Qt environment variables to suppress transient
    window artifacts on Windows. Console hiding for ffmpeg/ffprobe is handled
    locally in the code that calls those tools.
""")


if __name__ == '__main__':
    if len(sys.argv) > 1:
        arg = sys.argv[1].lower()
        if arg in ('--dev', '-d', 'dev'):
            run_dev()
        elif arg in ('--no-hook', '--nohook'):
            build_no_hook()
        elif arg in ('--help', '-h', 'help'):
            print_usage()
        else:
            print(f"Unknown argument: {arg}")
            print_usage()
            sys.exit(1)
    else:
        build()
