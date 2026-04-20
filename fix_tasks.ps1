$py      = 'C:\Users\krish\AppData\Local\Programs\Python\Python313\python.exe'
$srcDir  = 'C:\Users\krish\Documents\Claude Stock Trader\src'
$rootDir = 'C:\Users\krish\Documents\Claude Stock Trader'

$tasks = @(
    @{ name='EarningsBot_Scanner';   script="$srcDir\scanner.py";      workdir=$srcDir  }
    @{ name='EarningsBot_Validator'; script="$srcDir\validator.py";    workdir=$srcDir  }
    @{ name='EarningsBot_Executor';  script="$srcDir\executor.py";     workdir=$srcDir  }
    @{ name='EarningsBot_Monitor';   script="$srcDir\monitor.py";      workdir=$srcDir  }
    @{ name='EarningsBot_Reporter';  script="$srcDir\reporter.py";     workdir=$srcDir  }
    @{ name='EarningsBot_Learner';   script="$srcDir\learner.py";      workdir=$srcDir  }
    @{ name='EarningsBot_Dashboard'; script="$rootDir\update_dashboard.py"; workdir=$rootDir }
)

Write-Host ""
Write-Host "Fixing task arguments and working directories..." -ForegroundColor Cyan
Write-Host ""

foreach ($t in $tasks) {
    try {
        $task   = Get-ScheduledTask -TaskName $t.name -ErrorAction Stop
        $action = $task.Actions[0]

        # Fix: properly quote the script path, set working directory
        $action.Execute         = $py
        $action.Arguments       = "`"$($t.script)`""
        $action.WorkingDirectory = $t.workdir

        Set-ScheduledTask -TaskName $t.name -Action $action | Out-Null
        Write-Host "  FIXED  $($t.name)" -ForegroundColor Green
    } catch {
        Write-Host "  SKIP   $($t.name) — not found, please create it manually" -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "All done. Running a quick test of the validator now..." -ForegroundColor Cyan
Start-ScheduledTask -TaskName 'EarningsBot_Validator'
Start-Sleep -Seconds 5
$info = Get-ScheduledTaskInfo -TaskName 'EarningsBot_Validator'
$code = '0x{0:X}' -f $info.LastTaskResult
if ($info.LastTaskResult -eq 0) {
    Write-Host "Validator test: SUCCESS (exit code 0x0)" -ForegroundColor Green
} else {
    Write-Host "Validator test: exit code $code — check logs folder" -ForegroundColor Red
}
Write-Host ""
Read-Host "Press Enter to close"
