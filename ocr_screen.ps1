# Reads text from a screen region (or an image file) with the OCR built into Windows.
# Prints one word per output line as "x<TAB>y<TAB>height<TAB>text", UTF-8. Windows returns
# wrapped text as out-of-order fragments, so screen_reader.py rebuilds lines from positions.
#   powershell -NoProfile -ExecutionPolicy Bypass -File ocr_screen.ps1 -X 100 -Y 200 -Width 600 -Height 150
#   powershell -NoProfile -ExecutionPolicy Bypass -File ocr_screen.ps1 -Path shot.png
param(
    [int]$X, [int]$Y, [int]$Width, [int]$Height,
    [string]$Path,
    [int]$Scale = 2   # upscaling helps OCR on small or pixel-style fonts
)
$ErrorActionPreference = 'Stop'

Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Runtime.WindowsRuntime
Add-Type -Namespace KrillionOcr -Name Dpi -MemberDefinition '[DllImport("user32.dll")] public static extern bool SetProcessDPIAware();'
# Physical pixels, matching the coordinates the GUI's region picker records.
[KrillionOcr.Dpi]::SetProcessDPIAware() | Out-Null

if ($Path) {
    $source = [System.Drawing.Bitmap]::FromFile((Resolve-Path $Path))
} else {
    if ($Width -le 0 -or $Height -le 0) { throw 'Give -Path, or -X -Y -Width -Height' }
    $source = New-Object System.Drawing.Bitmap $Width, $Height
    $g = [System.Drawing.Graphics]::FromImage($source)
    $g.CopyFromScreen($X, $Y, 0, 0, $source.Size)
    $g.Dispose()
}

$scaled = New-Object System.Drawing.Bitmap ($source.Width * $Scale), ($source.Height * $Scale)
$g = [System.Drawing.Graphics]::FromImage($scaled)
$g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
$g.DrawImage($source, 0, 0, $scaled.Width, $scaled.Height)
$g.Dispose()
$source.Dispose()
$tmp = Join-Path $env:TEMP ("krillion-ocr-{0}.png" -f $PID)
$scaled.Save($tmp, [System.Drawing.Imaging.ImageFormat]::Png)
$scaled.Dispose()

# WinRT async calls, awaited from Windows PowerShell 5.1.
$asTask = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' } | Select-Object -First 1
function Await($op, [Type]$type) {
    $task = $asTask.MakeGenericMethod($type).Invoke($null, @($op))
    $task.Wait(-1) | Out-Null
    $task.Result
}
[Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime] | Out-Null
[Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics, ContentType = WindowsRuntime] | Out-Null

try {
    $file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($tmp)) ([Windows.Storage.StorageFile])
    $stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
    $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
    $bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
    $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
    if (-not $engine) { throw 'Windows OCR is not available for your display language' }
    $result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
    $stream.Dispose()
    $rows = foreach ($line in $result.Lines) {
        foreach ($word in $line.Words) {
            $r = $word.BoundingRect
            "{0}`t{1}`t{2}`t{3}" -f [int]$r.X, [int]$r.Y, [int]$r.Height, $word.Text
        }
    }
    # Write raw UTF-8 bytes: with no console (pythonw) PowerShell's own encoding garbles non-ASCII.
    $bytes = [System.Text.Encoding]::UTF8.GetBytes((@($rows) -join "`n") + "`n")
    $out = [Console]::OpenStandardOutput()
    $out.Write($bytes, 0, $bytes.Length)
    $out.Flush()
} finally {
    Remove-Item $tmp -ErrorAction SilentlyContinue
}
