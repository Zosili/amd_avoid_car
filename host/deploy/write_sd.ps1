# write_sd.ps1 -- 把 PYNQ 镜像裸写进 SD 卡（需管理员）
# 确认与结果全部用图形弹窗，不依赖控制台输入（Read-Host 在非交互启动时会立即 EOF）
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName Microsoft.VisualBasic | Out-Null

# 镜像路径：默认在脚本所在目录找同名 img；也可用 -Image 指定\nparam([string]$Image = "ZYNQ-7020-2.7.0.img")\n$SRC = if ([System.IO.Path]::IsPathRooted($Image)) { $Image } else { Join-Path $PSScriptRoot $Image }
$EXPECT_BYTES = 8090591232
$EXPECT_GB    = 14.84
$TITLE        = 'PYNQ 镜像写入 SD 卡'

function ShowBox([string]$msg, [string]$title, [int]$icon) {
    [void][Microsoft.VisualBasic.Interaction]::MsgBox($msg, $icon, $title)
}
function AskYesNo([string]$msg, [string]$title) {
    $style = [Microsoft.VisualBasic.MsgBoxStyle]::YesNo -bor [Microsoft.VisualBasic.MsgBoxStyle]::Warning
    $r = [Microsoft.VisualBasic.Interaction]::MsgBox($msg, $style, $title)
    return ($r -eq [Microsoft.VisualBasic.MsgBoxResult]::Yes)
}
function Fail([string]$msg) {
    ShowBox $msg $TITLE 16
    exit 1
}

# ---------- 0. 权限 ----------
$id = [Security.Principal.WindowsIdentity]::GetCurrent()
$pr = New-Object Security.Principal.WindowsPrincipal($id)
if (-not $pr.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Fail "需要管理员权限。`n`n请关闭本窗口，然后右键 write_sd.bat，选择「以管理员身份运行」。`n`n写 SD 卡要直接访问物理磁盘扇区，普通权限会被 Windows 拒绝。"
}
Write-Host "  [OK] 管理员权限" -ForegroundColor Green

# ---------- 1. 镜像 ----------
if (-not (Test-Path -LiteralPath $SRC)) { Fail "镜像不存在：`n$SRC" }
$imgLen = (Get-Item -LiteralPath $SRC).Length
if ($imgLen -ne $EXPECT_BYTES) {
    Fail ("镜像大小异常：`n实际 $imgLen 字节`n预期 $EXPECT_BYTES 字节`n`n文件可能被改动或没解压完整。")
}
Write-Host ("  [OK] 镜像完整：{0:N0} 字节 = {1:N2} GB" -f $imgLen, ($imgLen/1GB)) -ForegroundColor Green

# ---------- 2. 目标盘 ----------
$usb = @(Get-Disk | Where-Object { $_.BusType -eq 'USB' })
if ($usb.Count -eq 0) { Fail "没有找到 USB 磁盘。`n`n读卡器插好了吗？" }
if ($usb.Count -gt 1) {
    $list = ($usb | ForEach-Object { "  Disk {0}: {1}  {2} GB" -f $_.Number, $_.FriendlyName, [math]::Round($_.Size/1GB,2) }) -join "`n"
    Fail "发现 $($usb.Count) 个 USB 磁盘：`n`n$list`n`n请拔掉其它 U 盘 / 移动硬盘，只留读卡器，然后重试。"
}

$disk   = $usb[0]
$sizeGB = [math]::Round($disk.Size/1GB, 2)
if ([math]::Abs($sizeGB - $EXPECT_GB) -gt 0.5) {
    Fail "USB 磁盘容量 $sizeGB GB，与 SD 卡预期 $EXPECT_GB GB 不符。`n`n插的可能不是这张卡。"
}
if ($disk.IsSystem -or $disk.IsBoot) { Fail "目标被识别为系统盘，拒绝操作。" }

$letters = @()
try {
    $letters = @(Get-Partition -DiskNumber $disk.Number -ErrorAction SilentlyContinue |
                 ForEach-Object { $_.DriveLetter } | Where-Object { $_ })
} catch {}
$letterTxt = if ($letters.Count -gt 0) { ($letters -join ', ') + ':' } else { '(当前无盘符)' }
Write-Host ("  [OK] 目标：Disk {0}  {1}  {2} GB  {3}" -f $disk.Number, $disk.FriendlyName, $sizeGB, $letterTxt) -ForegroundColor Green

# ---------- 3. 确认 ----------
$msg = @"
即将把 PYNQ 镜像写入 SD 卡。

镜像
  $SRC
  $([math]::Round($imgLen/1GB,2)) GB   (PYNQ 2.7.0 for ZYNQ-7020)

目标
  Disk $($disk.Number)   $($disk.FriendlyName)
  $sizeGB GB   盘符 $letterTxt

操作
  清空分区表 -> 裸写镜像 -> 重新扫描
  预计 5~25 分钟（取决于卡速）

警告：目标盘上的全部数据将被永久覆盖，不可恢复！

确定继续吗？
"@
if (-not (AskYesNo $msg $TITLE)) {
    Write-Host "  已取消，什么都没做。" -ForegroundColor Yellow
    exit 0
}

# ---------- 4. 清空分区表 ----------
Write-Host ""
Write-Host "  [1/4] 清空分区表并卸载卷 ..." -ForegroundColor Cyan
$dpIn = "select disk $($disk.Number)`r`nclean`r`nexit"
$dpIn | diskpart | Out-Null
Start-Sleep -Seconds 2
Write-Host "        完成" -ForegroundColor Green

# ---------- 5. 裸写 ----------
Write-Host "  [2/4] 写入镜像（7.5 GB，5~25 分钟）..." -ForegroundColor Cyan
$devPath   = "\\.\PhysicalDrive$($disk.Number)"
$outStream = $null
$inStream  = $null
$sw = [Diagnostics.Stopwatch]::StartNew()
try {
    $outStream = [System.IO.File]::Open($devPath,
                    [System.IO.FileMode]::Open,
                    [System.IO.FileAccess]::Write,
                    [System.IO.FileShare]::ReadWrite)
    $inStream = [System.IO.File]::OpenRead($SRC)
    $bufSize = 8MB
    $buf = New-Object byte[] $bufSize
    $total = $inStream.Length
    $done = 0L
    while (($n = $inStream.Read($buf, 0, $bufSize)) -gt 0) {
        $outStream.Write($buf, 0, $n)
        $done += $n
        $pct = [int]($done * 100 / $total)
        $sec = $sw.Elapsed.TotalSeconds
        $mbps = if ($sec -gt 0) { ($done / 1MB) / $sec } else { 0 }
        $left = if ($mbps -gt 0.5) { [int](($total - $done) / 1MB / $mbps) } else { 0 }
        Write-Progress -Activity "写入 SD 卡" -PercentComplete $pct `
            -Status ("{0}%   {1:N0}/{2:N0} MB   {3:N1} MB/s   剩余约 {4} 分" -f `
                     $pct, ($done/1MB), ($total/1MB), $mbps, [math]::Round($left/60,0))
    }
    $outStream.Flush()
    Write-Progress -Activity "写入 SD 卡" -Completed
    Write-Host ("        完成，{0:N0} 秒，平均 {1:N1} MB/s" -f $sw.Elapsed.TotalSeconds, (($total/1MB)/$sw.Elapsed.TotalSeconds)) -ForegroundColor Green
} catch {
    Write-Progress -Activity "写入 SD 卡" -Completed
    if ($inStream)  { $inStream.Close() }
    if ($outStream) { $outStream.Close() }
    Fail ("写入失败：`n" + $_.Exception.Message + "`n`n卡可能处于半写入状态。`n重跑一次本脚本即可（会重新清空分区表）。")
} finally {
    if ($inStream)  { try { $inStream.Close()  } catch {} }
    if ($outStream) { try { $outStream.Close() } catch {} }
}

# ---------- 6. 刷新 ----------
Write-Host "  [3/4] 刷新磁盘缓存 ..." -ForegroundColor Cyan
"rescan`r`nexit" | diskpart | Out-Null
Start-Sleep -Seconds 3
Write-Host "        完成" -ForegroundColor Green

# ---------- 7. 验证 ----------
Write-Host "  [4/4] 验证 ..." -ForegroundColor Cyan
$ok = $true
$detail = @()
$parts = @(Get-Partition -DiskNumber $disk.Number -ErrorAction SilentlyContinue)
if ($parts.Count -ne 2) {
    $detail += "分区数 $($parts.Count)，预期 2"
    $ok = $false
} else {
    $p1mb = [math]::Round($parts[0].Size/1MB, 0)
    $p2gb = [math]::Round($parts[1].Size/1GB, 2)
    $detail += "分区1  $p1mb MB  (预期 130)"
    $detail += "分区2  $p2gb GB  (预期 7.41)"
    if ([math]::Abs($p1mb - 130) -gt 5 -or [math]::Abs($p2gb - 7.41) -gt 0.1) { $ok = $false }
}
$vol = $null
try {
    $vol = Get-Partition -DiskNumber $disk.Number -ErrorAction SilentlyContinue |
           Get-Volume -ErrorAction SilentlyContinue |
           Where-Object { $_.FileSystemLabel -eq 'PYNQ' }
} catch {}
if ($vol) {
    $detail += ("BOOT 分区卷标 PYNQ  {0} MB  [OK]" -f [math]::Round($vol.Size/1MB,0))
    Write-Host ("        [OK] 卷标 PYNQ，{0} MB" -f [math]::Round($vol.Size/1MB,0)) -ForegroundColor Green
} else {
    $detail += "没找到卷标 PYNQ 的分区"
    $ok = $false
}
$detail | ForEach-Object { Write-Host ("        " + $_) }

# ---------- 8. 结果 ----------
$body = $detail -join "`n"
if ($ok) {
    ShowBox @"
写卡成功。

$body

接下来：
1. 卡在 Windows 里会显示约 130 MB，这是正常的
2. 安全弹出读卡器
3. 卡插【板子背面】卡槽
4. 拨码【两个开关都拨到下面 OFF】
5. UART 线插【左侧 USB_UART】口
6. 上电，串口 115200，登录 xilinx / xilinx
"@ $TITLE 64
} else {
    ShowBox @"
写完了，但验证有异常：

$body

如果卡能正常启动就不用管；不能启动就重跑一次本脚本。
"@ $TITLE 48
}
