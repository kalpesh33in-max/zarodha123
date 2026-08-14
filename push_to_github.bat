@echo off
echo ========================================
echo Pushing Zarodha Scripts to GitHub...
echo ========================================

git add .
git commit -m "Manual update from script"
git push

echo ========================================
echo Push Complete!
echo ========================================
pause
