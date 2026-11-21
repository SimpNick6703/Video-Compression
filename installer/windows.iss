; Windows installer: adds a "Video-Compression" submenu to the right-click menu of video files.
;
; Build (from the repository root, after `python build.py`):
;   iscc /DAppVersion=1.3.0 /DSourceExe=..\dist\100mb-hevc-win64-v1.3.0.exe installer\windows.iss
;   (relative paths resolve from this folder)
;
; Installs per user (no admin prompt). The menu lives in the classic menu
; (Shift + right-click, or "Show more options" on Windows 11); the modern
; Windows 11 menu needs a signed package and is not used.
;
; Codec: asked on a wizard page; silent installs (WinGet) use HEVC unless
; `/CODEC=h264` is passed. Reinstall to change it.

#ifndef AppVersion
  #error Pass /DAppVersion=<version>
#endif
#ifndef SourceExe
  #error Pass /DSourceExe=<path to the 100mb-hevc Windows executable>
#endif

#ifndef NumericVersion
  ; File version resources only accept numbers; branch builds have none.
  #define NumericVersion "0.0.0"
#endif

#define AppName "Video-Compression"
#define AppUrl "https://github.com/SimpNick6703/Video-Compression"
; Submenu entries live in one shared key; each extension's menu entry points at it.
#define MenuKey "Software\Classes\VideoCompression.Menu"
; Explorer ignores the perceived-type key (SystemFileAssociations\video) when another
; app (e.g. VLC) owns the extension, so the menu is registered per extension.
#dim Exts[14] {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".wmv", ".flv", ".ts", ".mts", ".m2ts", ".3gp", ".mpg", ".mpeg"}
#define i 0

[Setup]
AppId={{004B68D8-64A4-46FA-8982-4B69DF87D66F}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=SimpNick6703
AppCopyright=Copyright (c) 2026 SimpNick6703. Licensed under GPL-3.0-or-later.
VersionInfoVersion={#NumericVersion}
AppPublisherURL={#AppUrl}
AppSupportURL={#AppUrl}/issues
AppUpdatesURL={#AppUrl}/releases
DefaultDirName={autopf}\{#AppName}
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile=..\LICENSE
OutputDir=..\dist
OutputBaseFilename=VideoCompression-Setup-win64-{#AppVersion}
UninstallDisplayIcon={app}\videocompress.exe
Compression=lzma2/max
SolidCompression=yes
ChangesAssociations=yes

[Files]
Source: "{#SourceExe}"; DestDir: "{app}"; DestName: "videocompress.exe"; Flags: ignoreversion
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\THIRD_PARTY_NOTICES-win64.txt"; DestDir: "{app}"; Flags: ignoreversion

[Registry]
#sub ExtEntry
  #define ExtKey "Software\Classes\SystemFileAssociations\" + Exts[i]
Root: HKA; Subkey: "{#ExtKey}"; Flags: uninsdeletekeyifempty
Root: HKA; Subkey: "{#ExtKey}\shell"; Flags: uninsdeletekeyifempty
Root: HKA; Subkey: "{#ExtKey}\shell\VideoCompression"; ValueType: string; ValueName: "MUIVerb"; ValueData: "{#AppName}"; Flags: uninsdeletekey
Root: HKA; Subkey: "{#ExtKey}\shell\VideoCompression"; ValueType: string; ValueName: "Icon"; ValueData: "{app}\videocompress.exe,0"
Root: HKA; Subkey: "{#ExtKey}\shell\VideoCompression"; ValueType: string; ValueName: "ExtendedSubCommandsKey"; ValueData: "VideoCompression.Menu"
#endsub
#for {i = 0; i < DimOf(Exts); i++} ExtEntry
Root: HKA; Subkey: "{#MenuKey}"; Flags: uninsdeletekey
; Entries are listed in key-name order, hence the number prefixes.
Root: HKA; Subkey: "{#MenuKey}\shell\1_20mb"; ValueType: string; ValueName: "MUIVerb"; ValueData: "Compress to 20 MB"
Root: HKA; Subkey: "{#MenuKey}\shell\1_20mb\command"; ValueType: string; ValueData: """{app}\videocompress.exe"" ""%1"" 20 {code:GetCodec}"
Root: HKA; Subkey: "{#MenuKey}\shell\2_50mb"; ValueType: string; ValueName: "MUIVerb"; ValueData: "Compress to 50 MB"
Root: HKA; Subkey: "{#MenuKey}\shell\2_50mb\command"; ValueType: string; ValueData: """{app}\videocompress.exe"" ""%1"" 50 {code:GetCodec}"
Root: HKA; Subkey: "{#MenuKey}\shell\3_100mb"; ValueType: string; ValueName: "MUIVerb"; ValueData: "Compress to 100 MB"
Root: HKA; Subkey: "{#MenuKey}\shell\3_100mb\command"; ValueType: string; ValueData: """{app}\videocompress.exe"" ""%1"" 100 {code:GetCodec}"
Root: HKA; Subkey: "{#MenuKey}\shell\4_1gb"; ValueType: string; ValueName: "MUIVerb"; ValueData: "Compress to 1 GB"
Root: HKA; Subkey: "{#MenuKey}\shell\4_1gb\command"; ValueType: string; ValueData: """{app}\videocompress.exe"" ""%1"" 1024 {code:GetCodec}"
Root: HKA; Subkey: "{#MenuKey}\shell\5_custom"; ValueType: string; ValueName: "MUIVerb"; ValueData: "Compress to Custom..."
Root: HKA; Subkey: "{#MenuKey}\shell\5_custom\command"; ValueType: string; ValueData: """{app}\videocompress.exe"" ""%1"" --custom {code:GetCodec}"

[Code]
var
  CodecPage: TInputOptionWizardPage;

procedure InitializeWizard;
begin
  CodecPage := CreateInputOptionPage(wpLicense,
    'Video codec', 'Which codec should the right-click menu use?',
    'HEVC gives better quality at the same size. Choose H.264 if the videos must play on older devices or browsers. Reinstall to change this later.',
    True, False);
  CodecPage.Add('HEVC (H.265), recommended');
  CodecPage.Add('H.264');
  if CompareText(ExpandConstant('{param:CODEC|hevc}'), 'h264') = 0 then
    CodecPage.SelectedValueIndex := 1
  else
    CodecPage.SelectedValueIndex := 0;
end;

function GetCodec(Param: string): string;
begin
  if CodecPage.SelectedValueIndex = 1 then
    Result := 'h264'
  else
    Result := 'hevc';
end;
