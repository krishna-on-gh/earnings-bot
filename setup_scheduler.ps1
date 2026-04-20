$py  = 'C:\Users\krish\AppData\Local\Programs\Python\Python313\python.exe'
$dir = 'C:\Users\krish\Documents\Claude Stock Trader'

$tasks = @(
    @{ name='EarningsBot_Scanner';   script='src\scanner.py';      trigger='daily';   days=$null;                  time='23:00'; repeat=$null; duration=$null }
    @{ name='EarningsBot_Validator'; script='src\validator.py';    trigger='weekly';  days=@('Monday','Tuesday','Wednesday','Thursday','Friday'); time='08:00'; repeat=$null; duration=$null }
    @{ name='EarningsBot_Executor';  script='src\executor.py';     trigger='weekly';  days=@('Monday','Tuesday','Wednesday','Thursday','Friday'); time='09:35'; repeat=$null; duration=$null }
    @{ name='EarningsBot_Monitor';   script='src\monitor.py';      trigger='weekly';  days=@('Monday','Tuesday','Wednesday','Thursday','Friday'); time='10:00'; repeat=60;    duration=6 }
    @{ name='EarningsBot_Reporter';  script='src\reporter.py';     trigger='weekly';  days=@('Monday','Tuesday','Wednesday','Thursday','Friday'); time='16:30'; repeat=$null; duration=$null }
    @{ name='EarningsBot_Learner';   script='src\learner.py';      trigger='weekly';  days=@('Sunday');             time='21:00'; repeat=$null; duration=$null }
    @{ name='EarningsBot_Dashboard'; script='update_dashboard.py'; trigger='weekly';  days=@('Monday','Tuesday','Wednesday','Thursday','Friday'); time='09:00'; repeat=30;    duration=8 }
)

Write-Host ""
Write-Host "Setting up Earnings Bot scheduled tasks..." -ForegroundColor Cyan
Write-Host ""

$action_settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Hours 2) -MultipleInstances IgnoreNew

foreach ($t in $tasks) {
    $scriptPath = Join-Path $dir $t.script
    $action = New-ScheduledTaskAction -Execute $py -Argument "`"$scriptPath`"" -WorkingDirectory (Join-Path $dir 'src')

    $at = [datetime]::ParseExact($t.time, 'HH:mm', $null)

    if ($t.trigger -eq 'daily') {
        $trigger = New-ScheduledTaskTrigger -Daily -At $at
    } else {
        $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $t.days -At $at
    }

    if ($t.repeat -ne $null) {
        $trigger.RepetitionInterval = [System.Xml.XmlConvert]::ToString([TimeSpan]::FromMinutes($t.repeat))
        $trigger.RepetitionDuration = [System.Xml.XmlConvert]::ToString([TimeSpan]::FromHours($t.duration))
    }

    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -RunLevel Highest

    Unregister-ScheduledTask -TaskName $t.name -Confirm:$false -ErrorAction SilentlyContinue

    try {
        Register-ScheduledTask -TaskName $t.name -Action $action -Trigger $trigger -Principal $principal -Settings $action_settings -Force | Out-Null
        Write-Host "  OK  $($t.name)" -ForegroundColor Green
    } catch {
        Write-Host "  FAIL $($t.name): $_" -ForegroundColor Red
    }
}

Write-Host ""
Write-Host "All done! Tasks are registered." -ForegroundColor Cyan
Write-Host "Open Task Scheduler and look for EarningsBot_* to confirm." -ForegroundColor Gray
Write-Host ""
Read-Host "Press Enter to close"
