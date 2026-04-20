# PowerShell HTTP server — no Python or Node required
param([int]$Port = 8080)

$root = Split-Path -Parent $MyInvocation.MyCommand.Definition
$url  = "http://localhost:$Port/"

$listener = [System.Net.HttpListener]::new()
$listener.Prefixes.Add($url)

try {
    $listener.Start()
} catch {
    Write-Host "ERROR: Could not start server on port $Port. Is something already using it?" -ForegroundColor Red
    Write-Host "Try: serve.bat 8081" -ForegroundColor Yellow
    Read-Host "Press Enter to exit"
    exit 1
}

Write-Host ""
Write-Host "  Earnings Bot Dashboard running at:" -ForegroundColor Green
Write-Host "  http://localhost:$Port/dashboard.html" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Press Ctrl+C to stop." -ForegroundColor Gray
Write-Host ""

Start-Process "http://localhost:$Port/dashboard.html"

$mimeTypes = @{
    ".html" = "text/html; charset=utf-8"
    ".json" = "application/json"
    ".js"   = "application/javascript"
    ".css"  = "text/css"
    ".ico"  = "image/x-icon"
    ".png"  = "image/png"
}

try {
    while ($listener.IsListening) {
        $ctx  = $listener.GetContext()
        $req  = $ctx.Request
        $resp = $ctx.Response

        $localPath = $req.Url.LocalPath.TrimStart("/")
        if ($localPath -eq "") { $localPath = "dashboard.html" }

        $filePath = Join-Path $root $localPath

        if (Test-Path $filePath -PathType Leaf) {
            $bytes = [System.IO.File]::ReadAllBytes($filePath)
            $ext   = [System.IO.Path]::GetExtension($filePath).ToLower()
            $resp.ContentType   = if ($mimeTypes[$ext]) { $mimeTypes[$ext] } else { "application/octet-stream" }
            $resp.ContentLength64 = $bytes.Length
            $resp.StatusCode    = 200
            $resp.OutputStream.Write($bytes, 0, $bytes.Length)
        } else {
            $msg   = [System.Text.Encoding]::UTF8.GetBytes("404 Not Found: $localPath")
            $resp.StatusCode    = 404
            $resp.ContentLength64 = $msg.Length
            $resp.OutputStream.Write($msg, 0, $msg.Length)
        }

        $resp.OutputStream.Flush()
        $resp.Close()
    }
} finally {
    $listener.Stop()
}
