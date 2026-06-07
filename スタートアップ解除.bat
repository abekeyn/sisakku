@echo off
chcp 65001 >nul
del "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\abe-rice-agent.lnk" 2>nul
echo スタートアップ登録を解除しました。
pause
