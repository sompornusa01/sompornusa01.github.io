# Compare step.py in both projects
$hash1 = (Get-FileHash "D:\2.PASSIVE INCOME\2.Timemachine\Claude Code\step.py").Hash
$hash2 = (Get-FileHash "D:\2.PASSIVE INCOME\2.Timemachine\Claude CodexFootball Step\step.py").Hash
Write-Host "boom-portfolio step.py  hash: $hash1  date: $((Get-Item 'D:\2.PASSIVE INCOME\2.Timemachine\Claude Code\step.py').LastWriteTime)"
Write-Host "football-step  step.py  hash: $hash2  date: $((Get-Item 'D:\2.PASSIVE INCOME\2.Timemachine\Claude CodexFootball Step\step.py').LastWriteTime)"
Write-Host "IDENTICAL: $($hash1 -eq $hash2)"

# Compare step_result.html
$r1 = "D:\2.PASSIVE INCOME\2.Timemachine\Claude Code\docs\step_result.html"
$r2 = "D:\2.PASSIVE INCOME\2.Timemachine\Claude CodexFootball Step\step_result.html"
if ((Test-Path $r1) -and (Test-Path $r2)) {
    $rh1 = (Get-FileHash $r1).Hash
    $rh2 = (Get-FileHash $r2).Hash
    Write-Host "step_result.html (boom/docs):  $rh1  date: $((Get-Item $r1).LastWriteTime)"
    Write-Host "step_result.html (football):   $rh2  date: $((Get-Item $r2).LastWriteTime)"
    Write-Host "IDENTICAL: $($rh1 -eq $rh2)"
} else {
    Write-Host "step_result.html: one or both paths missing"
}
