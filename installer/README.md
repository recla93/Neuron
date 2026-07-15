# Neuron Installer bootstrapper

`NeuronInstaller.exe` is the first-run Windows bootstrapper. It is deliberately
small and depends only on the .NET Framework already present on supported Windows
systems; it does not require Python, pip, Tkinter or a terminal window.

The executable must be distributed next to the Neuron project files, including
`install.ps1` and `vendor/`. If it cannot find `install.ps1`, it opens a folder
picker so the user can select the project directory.

Build from the repository root:

```powershell
powershell -ExecutionPolicy Bypass -File installer\build-installer.ps1
```

Output: `NeuronInstaller.exe` in the repository root. Double-click it to start the
first installation; it must remain next to `install.ps1` and `vendor/`.
