Unicode true
!include "MUI2.nsh"
!include "x64.nsh"
Name "Research Hub"
OutFile "${OUTFILE}"
InstallDir "$LOCALAPPDATA\Programs\ResearchHub"
InstallDirRegKey HKCU "Software\ResearchHub" "InstallPath"
RequestExecutionLevel user
SetCompressor zlib
VIProductVersion "0.1.0.0"
VIAddVersionKey "ProductName" "Research Hub"
VIAddVersionKey "FileDescription" "Research Hub per-user installer"
VIAddVersionKey "FileVersion" "0.1.0"
VIAddVersionKey "LegalCopyright" "Research Hub contributors"

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "${PAYLOAD}\LICENSE"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"

Function .onInit
  ${IfNot} ${RunningX64}
    MessageBox MB_ICONSTOP|MB_OK "Research Hub requires x64 Windows."
    Abort
  ${EndIf}
  SetShellVarContext current
FunctionEnd

Section "Research Hub"
  SetOutPath "$INSTDIR"
  File /r "${PAYLOAD}\*"
  CreateDirectory "$SMPROGRAMS\Research Hub"
  CreateShortcut "$SMPROGRAMS\Research Hub\Research Hub.lnk" "$INSTDIR\ResearchHub.exe"
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  WriteRegStr HKCU "Software\ResearchHub" "InstallPath" "$INSTDIR"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\ResearchHub" "DisplayName" "Research Hub"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\ResearchHub" "DisplayVersion" "0.1.0"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\ResearchHub" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\ResearchHub" "DisplayIcon" "$INSTDIR\ResearchHub.exe"
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\ResearchHub" "NoModify" 1
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\ResearchHub" "NoRepair" 1
SectionEnd

Section "Uninstall"
  SetShellVarContext current
  !include "${REMOVE_LIST}"
  Delete "$INSTDIR\Uninstall.exe"
  Delete "$SMPROGRAMS\Research Hub\Research Hub.lnk"
  RMDir "$SMPROGRAMS\Research Hub"
  RMDir "$INSTDIR"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\ResearchHub"
  DeleteRegKey HKCU "Software\ResearchHub"
SectionEnd
