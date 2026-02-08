$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $MyInvocation.MyCommand.Path
$Git = "C:\Program Files\Git\cmd\git.exe"
$Branch = "main"
$Remote = "origin"
$CommitPrefix = "Auto"  # 커밋 메시지 접두어
$DebounceMs = 1500       # 저장 연속 호출 방지

if (-not (Test-Path $Git)) {
  Write-Host "Git 실행 파일을 찾을 수 없습니다: $Git" -ForegroundColor Red
  exit 1
}

Set-Location $Repo

$global:lastEvent = Get-Date "1970-01-01"

$fsw = New-Object System.IO.FileSystemWatcher
$fsw.Path = $Repo
$fsw.IncludeSubdirectories = $true
$fsw.EnableRaisingEvents = $true
$fsw.Filter = "*.*"

$action = {
  $now = Get-Date
  if (($now - $global:lastEvent).TotalMilliseconds -lt $DebounceMs) { return }
  $global:lastEvent = $now

  # 스테이지/커밋/푸시
  & $using:Git add -A | Out-Null
  $status = & $using:Git status --porcelain
  if ($status) {
    $msg = "$using:CommitPrefix update: $([DateTime]::Now.ToString('yyyy-MM-dd HH:mm:ss'))"
    & $using:Git commit -m $msg | Out-Null
    & $using:Git push $using:Remote $using:Branch | Out-Null
    Write-Host "[AUTO] pushed at $([DateTime]::Now.ToString('HH:mm:ss'))"
  }
}

$created = Register-ObjectEvent $fsw Created -Action $action
$changed = Register-ObjectEvent $fsw Changed -Action $action
$renamed = Register-ObjectEvent $fsw Renamed -Action $action
$deleted = Register-ObjectEvent $fsw Deleted -Action $action

Write-Host "자동 업로드 실행 중... (종료하려면 창을 닫으세요)" -ForegroundColor Cyan

try {
  while ($true) { Start-Sleep -Milliseconds 500 }
}
finally {
  Unregister-Event -SourceIdentifier $created.Name
  Unregister-Event -SourceIdentifier $changed.Name
  Unregister-Event -SourceIdentifier $renamed.Name
  Unregister-Event -SourceIdentifier $deleted.Name
  $fsw.Dispose()
}
