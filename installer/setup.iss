; Inno Setup Script for Coomer BetterUI
; Requires Inno Setup 6.0 or later
; https://jrsoftware.org/isinfo.php

#define AppName "Coomer BetterUI"
#define AppVersion "1.0.0"
#define AppPublisher "Coomer BetterUI"
#define AppURL "https://github.com/your-repo/coomer-betterui"
#define AppExeName "CoomerBetterUI.exe"

[Setup]
AppId={{C8B9D2E1-4A3C-4F2E-9B7A-1D5E8F2C6A9B}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
AppUpdatesURL={#AppURL}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
AllowNoIcons=yes
LicenseFile=LICENSE.txt
OutputDir=installer\output
OutputBaseFilename=CoomerBetterUI-{#AppVersion}-Setup
SetupIconFile=resources\icon.ico
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64
MinVersion=10.0

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "quicklaunchicon"; Description: "{cm:CreateQuickLaunchIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked; OnlyBelowVersion: 6.1; Check: not IsAdminInstallMode

[Files]
Source: "dist\{#AppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "resources\*"; DestDir: "{app}\resources"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "LICENSE.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "README.md"; DestDir: "{app}"; Flags: ignoreversion isreadme
; Visual C++ Redistributable - place vc_redist.x64.exe in installer folder
; Download from: https://aka.ms/vs/17/release/vc_redist.x64.exe
Source: "installer\vc_redist.x64.exe"; DestDir: "{tmp}"; Flags: ignoreversion deleteafterinstall; Check: VCRedistNeedsInstall

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon
Name: "{userappdata}\Microsoft\Internet Explorer\Quick Launch\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: quicklaunchicon

[Run]
; Install VC++ Redistributable silently if needed (before launching app)
Filename: "{tmp}\vc_redist.x64.exe"; Parameters: "/install /quiet /norestart"; StatusMsg: "Installing Visual C++ Runtime..."; Flags: waituntilterminated; Check: VCRedistNeedsInstall
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(AppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{localappdata}\CoomerBetterUI"

[Code]
// Check if Visual C++ Redistributable 2015-2022 is installed
function VCRedistNeedsInstall: Boolean;
var
  Version: String;
begin
  // Check for VC++ 2015-2022 Redistributable (x64)
  // Registry key exists if installed
  Result := True;
  if RegQueryStringValue(HKLM, 'SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64', 'Version', Version) then
  begin
    // Version should be v14.x or higher
    if (CompareStr(Version, 'v14.29') >= 0) then
      Result := False;
  end;
  
  // Also check the newer registry path
  if Result and RegQueryStringValue(HKLM, 'SOFTWARE\WOW6432Node\Microsoft\VisualStudio\14.0\VC\Runtimes\x64', 'Version', Version) then
  begin
    if (CompareStr(Version, 'v14.29') >= 0) then
      Result := False;
  end;
end;

// Custom code for update detection
var
  UpdatePage: TInputOptionWizardPage;

function InitializeSetup(): Boolean;
var
  UninstallString: String;
  ErrorCode: Integer;
begin
  Result := True;
  
  // Check for previous installation
  if RegQueryStringValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{#SetupSetting("AppId")}_is1',
     'UninstallString', UninstallString) then
  begin
    if MsgBox('A previous version of {#AppName} is installed. Do you want to uninstall it first?', 
        mbConfirmation, MB_YESNO) = IDYES then
    begin
      Exec(RemoveQuotes(UninstallString), '/SILENT', '', SW_HIDE, ewWaitUntilTerminated, ErrorCode);
    end;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    // Additional post-install tasks can be added here
  end;
end;
