@echo off
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$dir = '%~dp0'; " ^
  "$html = Get-Content '$dir\dashboard.html' -Raw -Encoding UTF8; " ^
  "$recs = Get-Content '$dir\data\recommendations.json' -Raw -Encoding UTF8; " ^
  "$trades = Get-Content '$dir\data\trades_history.json' -Raw -Encoding UTF8; " ^
  "$pos = Get-Content '$dir\data\positions.json' -Raw -Encoding UTF8; " ^
  "$budget = Get-Content '$dir\data\budget_tracker.json' -Raw -Encoding UTF8; " ^
  "$now = (Get-Date -Format 'yyyy-MM-ddTHH:mm:ss'); " ^
  "$meta = '{\"embedded_at\":\"' + $now + '\",\"source\":\"update_dashboard.bat\"}'; " ^
  "$html = $html -replace '(?s)(<script id=\"__RECOMMENDATIONS__\"[^>]*>).*?(</script>)', ('${1}' + $recs.Trim() + '${2}'); " ^
  "$html = $html -replace '(?s)(<script id=\"__TRADES__\"[^>]*>).*?(</script>)', ('${1}' + $trades.Trim() + '${2}'); " ^
  "$html = $html -replace '(?s)(<script id=\"__POSITIONS__\"[^>]*>).*?(</script>)', ('${1}' + $pos.Trim() + '${2}'); " ^
  "$html = $html -replace '(?s)(<script id=\"__BUDGET__\"[^>]*>).*?(</script>)', ('${1}' + $budget.Trim() + '${2}'); " ^
  "$html = $html -replace '(?s)(<script id=\"__META__\"[^>]*>).*?(</script>)', ('${1}' + $meta + '${2}'); " ^
  "[System.IO.File]::WriteAllText('$dir\dashboard.html', $html, [System.Text.Encoding]::UTF8); " ^
  "Write-Host 'Dashboard updated successfully.' -ForegroundColor Green"
echo.
echo Done! Open dashboard.html in your browser.
