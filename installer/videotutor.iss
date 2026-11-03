; Inno Setup script -- compiled by tools/build.py, which passes every product
; value with /D from product.toml. Nothing product-specific is written here.
;
; One script, two packages:
;   full   (default)      complete install / upgrade / repair of any version
;   delta  (/DDelta)      only the files that changed since FromVersion;
;                         refuses to run on any other installed version
;
; Upgrade:   same AppId -> installs over the existing copy, data untouched.
; Downgrade: refused unless /ALLOWDOWNGRADE is passed (data from a newer
;            version may not be readable by an older one).
; Uninstall: removes the program; asks before deleting learning data.
; Failure:   Inno rolls back a failed install; rerunning the full package
;            always repairs, whatever state the program folder is in.

#ifndef AppVersion
  #error Compile through tools/build.py (AppVersion is not defined)
#endif

[Setup]
AppId={{{#AppGuid}}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppDisplayName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL=https://github.com/{#AppRepository}
AppSupportURL=https://github.com/{#AppRepository}/issues
AppUpdatesURL=https://github.com/{#AppRepository}/releases
VersionInfoVersion={#AppVersion}
DefaultDirName={autopf}\{#AppName}
DisableProgramGroupPage=yes
UsePreviousAppDir=yes
; Per-user install: no administrator rights, no UAC prompt.
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; The app holds this mutex while running (product.toml single_instance_mutex).
AppMutex={#AppMutex}
SetupMutex={#AppName}-setup
CloseApplications=no
SetupIconFile={#IconFile}
UninstallDisplayIcon={app}\{#AppName}.exe
UninstallDisplayName={#AppDisplayName}
WizardStyle=modern
ShowLanguageDialog=no
Compression=lzma2/max
SolidCompression=yes
OutputDir={#OutputDir}
#ifdef Delta
OutputBaseFilename={#AppName}-Update-{#FromVersion}-to-{#AppVersion}
DisableDirPage=yes
DisableReadyPage=yes
#else
OutputBaseFilename={#AppName}-Setup-{#AppVersion}
#endif

[Languages]
Name: "chinesesimp"; MessagesFile: "lang\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
chinesesimp.Autostart=开机时自动在后台启动
chinesesimp.DesktopIcon=创建桌面快捷方式
chinesesimp.LaunchNow=立即启动 %1
chinesesimp.DowngradeBlocked=已安装更新的版本 %1，不能直接降级到 %2。%n%n新版本保存的学习数据可能无法被旧版本读取。如确需降级，请先卸载（学习数据默认保留），或使用 /ALLOWDOWNGRADE 参数运行安装程序。
chinesesimp.DeltaWrongBase=这个增量更新包只适用于 %1 版本，当前安装的版本是：%2。%n%n请下载完整安装包。
chinesesimp.NotInstalled=（未安装）
chinesesimp.DeleteData=是否同时删除学习数据？%n%n%1%n%n包括学习记录、下载的模型和日志。选择“否”会保留这些数据，以后重新安装时自动恢复。
english.Autostart=Start automatically in the background when I sign in
english.DesktopIcon=Create a desktop shortcut
english.LaunchNow=Launch %1 now
english.DowngradeBlocked=A newer version (%1) is already installed; it cannot be downgraded to %2.%n%nData saved by the newer version may not be readable by an older one. Uninstall first (your data is kept by default), or run this installer with /ALLOWDOWNGRADE.
english.DeltaWrongBase=This update only applies to version %1, but the installed version is: %2.%n%nPlease download the full installer.
english.NotInstalled=(not installed)
english.DeleteData=Also delete your learning data?%n%n%1%n%nThis includes learning records, downloaded models and logs. Choose No to keep them; a reinstall picks them up again.

#ifdef Delta
#include DeltaInclude
#else
[Tasks]
Name: "autostart"; Description: "{cm:Autostart}"
Name: "desktopicon"; Description: "{cm:DesktopIcon}"; Flags: unchecked

[InstallDelete]
; A full install is a clean install of the program folder: nothing left over
; from an older version can shadow the new files.
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}\extension"

[Files]
Source: "{#DistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppName}.exe"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppName}.exe"; Tasks: desktopicon
Name: "{userstartup}\{#AppName}"; Filename: "{app}\{#AppName}.exe"; Parameters: "--no-browser"; Tasks: autostart
#endif

[Run]
Filename: "{app}\{#AppName}.exe"; Description: "{cm:LaunchNow,{#AppName}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Code]
const
  UninstallKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{' + '{#AppGuid}' + '}_is1';

function NextVersionPart(var S: String): Integer;
var
  P: Integer;
begin
  P := Pos('.', S);
  if P = 0 then
  begin
    Result := StrToIntDef(S, 0);
    S := '';
  end
  else
  begin
    Result := StrToIntDef(Copy(S, 1, P - 1), 0);
    Delete(S, 1, P);
  end;
end;

{ -1 if A < B, 0 if equal, 1 if A > B; compares X.Y.Z numerically. }
function CompareVersions(A, B: String): Integer;
var
  I, X, Y: Integer;
begin
  Result := 0;
  for I := 1 to 3 do
  begin
    X := NextVersionPart(A);
    Y := NextVersionPart(B);
    if X < Y then begin Result := -1; Exit; end;
    if X > Y then begin Result := 1; Exit; end;
  end;
end;

function InstalledVersion(): String;
begin
  if not RegQueryStringValue(HKCU, UninstallKey, 'DisplayVersion', Result) then
    Result := '';
end;

function HasParam(Name: String): Boolean;
var
  I: Integer;
begin
  Result := False;
  for I := 1 to ParamCount do
    if CompareText(ParamStr(I), Name) = 0 then
    begin
      Result := True;
      Exit;
    end;
end;

function InitializeSetup(): Boolean;
var
  Installed, Shown: String;
begin
  Result := True;
  Installed := InstalledVersion();
#ifdef Delta
  if Installed <> '{#FromVersion}' then
  begin
    Shown := Installed;
    if Shown = '' then Shown := CustomMessage('NotInstalled');
    SuppressibleMsgBox(FmtMessage(CustomMessage('DeltaWrongBase'), ['{#FromVersion}', Shown]),
      mbError, MB_OK, IDOK);
    Result := False;
  end;
#else
  if (Installed <> '') and (CompareVersions(Installed, '{#AppVersion}') > 0)
     and not HasParam('/ALLOWDOWNGRADE') then
  begin
    SuppressibleMsgBox(FmtMessage(CustomMessage('DowngradeBlocked'), [Installed, '{#AppVersion}']),
      mbError, MB_OK, IDOK);
    Result := False;
  end;
#endif
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep <> usPostUninstall then Exit;
  { Must match infra/paths.py home_dir(): %LOCALAPPDATA%\<product name>. }
  DataDir := ExpandConstant('{localappdata}\{#AppName}');
  if not DirExists(DataDir) then Exit;
  { Silent uninstalls never delete data: no one was asked. }
  if UninstallSilent() then Exit;
  if MsgBox(FmtMessage(CustomMessage('DeleteData'), [DataDir]), mbConfirmation,
            MB_YESNO or MB_DEFBUTTON2) = IDYES then
    DelTree(DataDir, True, True, True);
end;
