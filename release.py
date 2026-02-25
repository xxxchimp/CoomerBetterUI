"""
Build and release automation script for Coomer BetterUI
Handles building, packaging, and preparing releases
"""
import sys
import subprocess
import hashlib
import json
from pathlib import Path
from datetime import datetime
import shutil

class ReleaseBuilder:
    """Automates the build and release process"""
    
    def __init__(self, version: str, update_type: str = 'patch'):
        """
        Initialize release builder
        
        Args:
            version: Version string (e.g., "1.0.1")
            update_type: 'patch' or 'full'
        """
        self.version = version
        self.update_type = update_type
        self.project_root = Path(__file__).parent
        self.dist_dir = self.project_root / "dist"
        self.output_dir = self.project_root / "installer" / "output"
        
    def calculate_checksum(self, filepath: Path) -> str:
        """
        Calculate SHA256 checksum of file
        
        Args:
            filepath: Path to file
            
        Returns:
            Checksum in format "sha256:hash"
        """
        sha256 = hashlib.sha256()
        with open(filepath, 'rb') as f:
            for chunk in iter(lambda: f.read(8192), b''):
                sha256.update(chunk)
        return f"sha256:{sha256.hexdigest()}"
    
    def build_executable(self) -> bool:
        """
        Build executable using PyInstaller
        
        Returns:
            True if successful
        """
        print("=" * 60)
        print("STEP 1: Building Executable")
        print("=" * 60)
        
        try:
            # Run build script
            subprocess.run([sys.executable, "build.py"], check=True)
            
            exe_path = self.dist_dir / "CoomerBetterUI.exe"
            if exe_path.exists():
                print(f"✓ Executable built: {exe_path}")
                print(f"  Size: {exe_path.stat().st_size:,} bytes")
                return True
            else:
                print("✗ Executable not found")
                return False
                
        except subprocess.CalledProcessError as e:
            print(f"✗ Build failed: {e}")
            return False
    
    def build_installer(self) -> Path:
        """
        Build installer using Inno Setup
        
        Returns:
            Path to created installer
        """
        print("\n" + "=" * 60)
        print("STEP 2: Building Installer")
        print("=" * 60)
        
        # Update setup.iss with version
        setup_file = self.project_root / "installer" / "setup.iss"
        
        # Read setup file
        with open(setup_file, 'r') as f:
            content = f.read()
        
        # Update version
        content = content.replace(
            '#define AppVersion "1.0.0"',
            f'#define AppVersion "{self.version}"'
        )
        
        # Update output filename
        if self.update_type == 'patch':
            content = content.replace(
                'OutputBaseFilename=CoomerBetterUI-Setup',
                f'OutputBaseFilename=CoomerBetterUI-{self.version}-Patch'
            )
        else:
            content = content.replace(
                'OutputBaseFilename=CoomerBetterUI-Setup',
                f'OutputBaseFilename=CoomerBetterUI-{self.version}-Setup'
            )
        
        # Write updated setup file
        with open(setup_file, 'w') as f:
            f.write(content)
        
        print(f"Updated setup.iss with version {self.version}")
        
        # Compile with Inno Setup (if available)
        iscc_paths = [
            r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
            r"C:\Program Files\Inno Setup 6\ISCC.exe"
        ]
        
        iscc_path = None
        for path in iscc_paths:
            if Path(path).exists():
                iscc_path = path
                break
        
        if iscc_path:
            try:
                subprocess.run([iscc_path, str(setup_file)], check=True)
                print("✓ Installer compiled successfully")
            except subprocess.CalledProcessError:
                print("✗ Installer compilation failed")
                print("  Please compile manually using Inno Setup")
        else:
            print("! Inno Setup not found")
            print("  Please compile manually: installer/setup.iss")
        
        # Find created installer
        if self.update_type == 'patch':
            installer_name = f"CoomerBetterUI-{self.version}-Patch.exe"
        else:
            installer_name = f"CoomerBetterUI-{self.version}-Setup.exe"
        
        installer_path = self.output_dir / installer_name
        
        if installer_path.exists():
            print(f"✓ Installer created: {installer_path}")
            print(f"  Size: {installer_path.stat().st_size:,} bytes")
            return installer_path
        else:
            print(f"✗ Installer not found: {installer_path}")
            return None
    
    def generate_manifest_entry(self, installer_path: Path) -> dict:
        """
        Generate update manifest entry
        
        Args:
            installer_path: Path to installer
            
        Returns:
            Manifest entry dictionary
        """
        print("\n" + "=" * 60)
        print("STEP 3: Generating Manifest Entry")
        print("=" * 60)
        
        checksum = self.calculate_checksum(installer_path)
        size = installer_path.stat().st_size
        
        entry = {
            "url": f"https://github.com/your-repo/coomer-betterui/releases/download/v{self.version}/{installer_path.name}",
            "checksum": checksum,
            "size": size,
            "changelog": f"## Version {self.version}\n\n### Changes\n- TODO: Add changelog\n",
            "release_date": datetime.now().strftime("%Y-%m-%d")
        }
        
        if self.update_type == 'patch':
            # Derive previous version for patches
            parts = self.version.split('.')
            parts[-1] = str(int(parts[-1]) - 1)
            entry["from_version"] = '.'.join(parts)
        
        print("✓ Manifest entry generated:")
        print(json.dumps(entry, indent=2))
        
        return entry
    
    def create_release_notes(self, installer_path: Path, manifest_entry: dict):
        """
        Create release notes file
        
        Args:
            installer_path: Path to installer
            manifest_entry: Manifest entry dictionary
        """
        print("\n" + "=" * 60)
        print("STEP 4: Creating Release Notes")
        print("=" * 60)
        
        notes = f"""# Coomer BetterUI v{self.version}

## Release Type: {self.update_type.upper()}

## Installation

### New Users
Download and run `{installer_path.name}`

### Existing Users
- **Automatic**: Application will notify you of the update
- **Manual**: Download and run the installer

## File Information

**Filename**: {installer_path.name}
**Size**: {installer_path.stat().st_size:,} bytes
**Checksum**: {manifest_entry['checksum']}

## Changelog

TODO: Add detailed changelog here

## Manifest Entry

Add to your `update_manifest.json`:

```json
{json.dumps({self.version: manifest_entry}, indent=2)}
```

## Verification

Verify download integrity:

```powershell
Get-FileHash -Algorithm SHA256 "{installer_path.name}"
```

Expected: `{manifest_entry['checksum'].split(':')[1]}`

## Links

- [Download]({manifest_entry['url']})
- [Documentation](https://github.com/your-repo/coomer-betterui)
- [Report Issues](https://github.com/your-repo/coomer-betterui/issues)
"""
        
        notes_file = self.output_dir / f"RELEASE_NOTES_v{self.version}.md"
        with open(notes_file, 'w') as f:
            f.write(notes)
        
        print(f"✓ Release notes created: {notes_file}")
    
    def run(self):
        """Execute full build and release process"""
        print("\n" + "=" * 70)
        print(f"  Coomer BetterUI Release Builder v{self.version}")
        print(f"  Type: {self.update_type.upper()}")
        print("=" * 70)
        
        # Step 1: Build executable
        if not self.build_executable():
            print("\n✗ Build process failed at Step 1")
            return False
        
        # Step 2: Build installer
        installer_path = self.build_installer()
        if not installer_path:
            print("\n✗ Build process failed at Step 2")
            return False
        
        # Step 3: Generate manifest entry
        manifest_entry = self.generate_manifest_entry(installer_path)
        
        # Step 4: Create release notes
        self.create_release_notes(installer_path, manifest_entry)
        
        # Summary
        print("\n" + "=" * 70)
        print("BUILD COMPLETE")
        print("=" * 70)
        print(f"\n✓ Installer: {installer_path}")
        print(f"✓ Release Notes: {self.output_dir / f'RELEASE_NOTES_v{self.version}.md'}")
        print(f"\nNext Steps:")
        print(f"1. Review release notes and add detailed changelog")
        print(f"2. Test installer on clean machine")
        print(f"3. Create git tag: git tag -a v{self.version} -m 'Version {self.version}'")
        print(f"4. Push tag: git push origin v{self.version}")
        print(f"5. Create GitHub release and upload installer")
        print(f"6. Update update_manifest.json with generated entry")
        
        return True

def main():
    """Main entry point"""
    if len(sys.argv) < 2:
        print("Usage: python release.py <version> [type]")
        print("Example: python release.py 1.0.1 patch")
        print("Example: python release.py 1.1.0 full")
        sys.exit(1)
    
    version = sys.argv[1]
    update_type = sys.argv[2] if len(sys.argv) > 2 else 'patch'
    
    if update_type not in ['patch', 'full']:
        print(f"Error: Invalid update type '{update_type}'. Must be 'patch' or 'full'")
        sys.exit(1)
    
    builder = ReleaseBuilder(version, update_type)
    success = builder.run()
    
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    main()
