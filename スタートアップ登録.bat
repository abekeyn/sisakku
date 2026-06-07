@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 予約出力エージェントをPCのスタートアップに登録します...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([IO.Path]::Combine($env:APPDATA,'Microsoft\Windows\Start Menu\Programs\Startup','abe-rice-agent.lnk'));" ^
  "$s.TargetPath=(Join-Path '%~dp0' '.venv\Scripts\pythonw.exe');" ^
  "$s.Arguments=(Join-Path '%~dp0' 'agent.py');" ^
  "$s.WorkingDirectory='%~dp0';" ^
  "$s.Description='阿部農園 予約出力エージェント';" ^
  "$s.Save()"
echo.
echo 登録しました。次回のPC起動から、予約された出力が自動で
echo デスクトップの『ヤマト出荷CSV』に書き出されます。
echo （解除したいときは「スタートアップ解除.bat」を実行してください）
pause
