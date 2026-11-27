' Keeps the existing Windows Startup shortcut pointed at SurMuy.
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
folder = fso.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = folder
app = fso.BuildPath(folder, "SurMuy.exe")
shortcut = fso.BuildPath(folder, "SurMuy.lnk")
If fso.FileExists(shortcut) Then
    Set launcher = shell.CreateShortcut(shortcut)
    If fso.FileExists(launcher.TargetPath) Then app = launcher.TargetPath
End If
If fso.FileExists(app) Then
    shell.Run """" & app & """", 1, False
Else
    shell.Run "pythonw -B """ & fso.BuildPath(folder, "src\screen_chat.py") & """", 0, False
End If
