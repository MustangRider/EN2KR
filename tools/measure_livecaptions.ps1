# Measure the load of Windows Live Captions (or any process) on a Windows 11 PC.
#
#   1. Turn on Live Captions (Win + Ctrl + L) and play a video with English speech.
#   2. Run:  powershell -ExecutionPolicy Bypass -File measure_livecaptions.ps1
#      (other process: -ProcessName chrome   duration: -Seconds 30)
#
# Uses WMI performance classes, whose names are not localized, so it also works
# on Korean Windows (Get-Counter paths like "\GPU Engine(*)" are translated there).
param(
    [string]$ProcessName = "LiveCaptions",
    [int]$Seconds = 60
)

$procs = Get-Process -Name $ProcessName -ErrorAction SilentlyContinue
if (-not $procs) {
    Write-Host "$ProcessName 이(가) 실행 중이 아닙니다. 실시간 자막(Win + Ctrl + L)을 켜고 영상을 재생한 뒤 다시 실행하세요."
    exit 1
}
$ids = @($procs | ForEach-Object { $_.Id })
$cores = (Get-CimInstance Win32_ComputerSystem).NumberOfLogicalProcessors
$gpuName = (Get-CimInstance Win32_VideoController | Select-Object -First 1).Name
Write-Host "대상: $ProcessName (PID $($ids -join ', ')) / CPU 논리코어 $cores / GPU $gpuName / $Seconds 초 측정"

function Get-PidPattern { "pid_(" + ($ids -join "|") + ")_" }

$rows = @()
$prevCpu = ($ids | ForEach-Object { (Get-Process -Id $_ -ErrorAction SilentlyContinue).TotalProcessorTime.TotalSeconds } | Measure-Object -Sum).Sum
$prevTime = Get-Date
for ($i = 1; $i -le $Seconds; $i++) {
    Start-Sleep -Seconds 1
    $live = @($ids | ForEach-Object { Get-Process -Id $_ -ErrorAction SilentlyContinue } | Where-Object { $_ })
    if (-not $live) { Write-Host "프로세스가 종료되었습니다."; break }
    $now = Get-Date
    $cpuNow = ($live | ForEach-Object { $_.TotalProcessorTime.TotalSeconds } | Measure-Object -Sum).Sum
    $cpuPct = ($cpuNow - $prevCpu) / ($now - $prevTime).TotalSeconds / $cores * 100
    $prevCpu = $cpuNow; $prevTime = $now
    $ramMb = ($live | Measure-Object -Property WorkingSet64 -Sum).Sum / 1MB

    $pattern = Get-PidPattern
    $gpuPct = (Get-CimInstance Win32_PerfFormattedData_GPUPerformanceCounters_GPUEngine -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match $pattern } | Measure-Object -Property UtilizationPercentage -Sum).Sum
    $vramMb = (Get-CimInstance Win32_PerfFormattedData_GPUPerformanceCounters_GPUProcessMemory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match $pattern } | Measure-Object -Property DedicatedUsage -Sum).Sum / 1MB

    $row = [pscustomobject]@{ Sec = $i; CPU = [math]::Round($cpuPct, 1); RAM_MB = [math]::Round($ramMb);
                              GPU = [math]::Round([double]$gpuPct, 1); VRAM_MB = [math]::Round([double]$vramMb) }
    $rows += $row
    Write-Host ("{0,3}s  CPU {1,5}%  RAM {2,6} MB  GPU {3,5}%  VRAM {4,6} MB" -f $row.Sec, $row.CPU, $row.RAM_MB, $row.GPU, $row.VRAM_MB)
}

if ($rows) {
    $s = { param($p) $m = $rows | Measure-Object -Property $p -Average -Maximum; "{0:N1} / {1:N1}" -f $m.Average, $m.Maximum }
    Write-Host ""
    Write-Host "== 요약 (평균 / 최대) =="
    Write-Host ("CPU %   : " + (& $s "CPU"))
    Write-Host ("RAM MB  : " + (& $s "RAM_MB"))
    Write-Host ("GPU %   : " + (& $s "GPU"))
    Write-Host ("VRAM MB : " + (& $s "VRAM_MB"))
}
