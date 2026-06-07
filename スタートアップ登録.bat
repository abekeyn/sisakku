@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 予約出力エージェント（常駐・監視モード）をPCのスタートアップに登録します...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([IO.Path]::Combine($env:APPDATA,'Microsoft\Windows\Start Menu\Programs\Startup','abe-rice-agent.lnk'));" ^
  "$s.TargetPath=(Join-Path '%~dp0' '.venv\Scripts\pythonw.exe');" ^
  "$s.Arguments='\"'+(Join-Path '%~dp0' 'agent.py')+'\" --watch';" ^
  "$s.WorkingDirectory='%~dp0';" ^
  "$s.Description='阿部農園 予約出力エージェント（常駐）';" ^
  "$s.Save()"
echo.
echo 登録しました。今すぐ常駐を開始します...
start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0agent.py" --watch
echo.
echo これで、クラウド（スマホ等）で「ヤマトCSVを作成」すると、
echo 数秒後にデスクトップの『ヤマト出荷CSV』へ自動保存されます。
echo （PC起動のたびに自動で常駐します。解除は「スタートアップ解除.bat」）
pause
