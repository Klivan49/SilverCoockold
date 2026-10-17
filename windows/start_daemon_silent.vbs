Option Explicit

Dim fso, shell, root, exe
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

root = fso.GetParentFolderName(fso.GetParentFolderName(WScript.ScriptFullName))
exe = fso.BuildPath(root, ".venv\Scripts\pythonw.exe")

If Not fso.FileExists(exe) Then
    MsgBox "Run windows\install.bat first.", vbExclamation, "SilverCoockold"
    WScript.Quit 1
End If

shell.CurrentDirectory = root
shell.Run """" & exe & """ -m app --daemon", 0, False
