param(
    [ValidateNotNullOrEmpty()]
    [string]$TopicConfigPath = "config\personal_topics\default.toml",

    [ValidateNotNullOrEmpty()]
    [string]$TaskName = "Daily Research Agent",

    [ValidateNotNullOrEmpty()]
    [string]$At = "09:00",

    [ValidateSet("Daily", "Weekdays")]
    [string]$Frequency = "Daily"
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$Python = if (Test-Path $VenvPython) {
    $VenvPython
} else {
    (Get-Command python).Source
}
$Config = if ([System.IO.Path]::IsPathRooted($TopicConfigPath)) {
    $TopicConfigPath
} else {
    Join-Path $ProjectRoot $TopicConfigPath
}

if (-not (Test-Path $Config)) {
    throw "Config file was not found at $Config."
}

$Config = (Resolve-Path -LiteralPath $Config).Path
$ScheduleTime = [datetime]::ParseExact(
    $At,
    @("H:mm", "HH:mm", "h:mmtt", "h:mm tt"),
    [System.Globalization.CultureInfo]::InvariantCulture,
    [System.Globalization.DateTimeStyles]::None
)

$Action = New-ScheduledTaskAction `
    -Execute $Python `
    -Argument "-m news_agent --config `"$Config`"" `
    -WorkingDirectory $ProjectRoot

$Trigger = if ($Frequency -eq "Weekdays") {
    New-ScheduledTaskTrigger `
        -Weekly `
        -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday `
        -At $ScheduleTime
} else {
    New-ScheduledTaskTrigger -Daily -At $ScheduleTime
}
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Description "Sends a daily topic research digest by email." `
    -Force

Write-Host "Scheduled '$TaskName' to run $($Frequency.ToLowerInvariant()) at $($ScheduleTime.ToString("h:mm tt"))."
