# Experiment Script — Verify LastWave Personal Addon Behavior
# Uses PowerShell Invoke-WebRequest (no Python/Node required)
# SECURITY: Never logs the full addon URL

$ErrorActionPreference = "Continue"

# Load .env
$envFile = Join-Path $PSScriptRoot "..\.env"
if (-not (Test-Path $envFile)) {
    Write-Host "ERROR: .env not found at $envFile"
    exit 1
}
Get-Content $envFile | ForEach-Object {
    $line = $_.Trim()
    if ($line -and -not $line.StartsWith("#") -and $line.Contains("=")) {
        $key, $val = $line -split "=", 2
        [Environment]::SetEnvironmentVariable($key.Trim(), $val.Trim(), "Process")
    }
}

$ADDON_URL = [Environment]::GetEnvironmentVariable("LASTWAVE_PERSONAL_ADDON_URL", "Process").TrimEnd("/")
if (-not $ADDON_URL) {
    Write-Host "ERROR: LASTWAVE_PERSONAL_ADDON_URL not set"
    exit 1
}

# Extract token for redaction
$uri = [System.Uri]$ADDON_URL
$pathParts = $uri.AbsolutePath.Trim("/").Split("/")
$token = if ($pathParts.Length -ge 2 -and $pathParts[0] -eq "a") { $pathParts[1] } else { "" }
$REDACTED = "$($uri.Scheme)://$($uri.Host)/a/***REDACTED***"

Write-Host "`n[CONFIG] Addon URL (redacted): $REDACTED"
Write-Host "[CONFIG] Token length: $($token.Length)"
Write-Host ""

function Invoke-SafeRequest {
    param(
        [string]$Url,
        [string]$Method = "GET",
        [hashtable]$Headers = @{},
        [int]$TimeoutSec = 15
    )
    
    # Redact for logging
    $logUrl = $Url -replace "/a/[a-f0-9]+/", "/a/***REDACTED***/"
    
    $Headers["User-Agent"] = "LosslessBridge-Experiment/1.0"
    
    try {
        $params = @{
            Uri = $Url
            Method = $Method
            Headers = $Headers
            TimeoutSec = $TimeoutSec
            UseBasicParsing = $true
        }
        
        $resp = Invoke-WebRequest @params
        return @{
            Url = $logUrl
            Status = $resp.StatusCode
            Headers = $resp.Headers
            Content = $resp.Content
            RawContent = $resp.RawContentStream
            ContentBytes = $null
            Error = $null
        }
    }
    catch {
        $statusCode = $null
        $content = $null
        $headers = @{}
        if ($_.Exception.Response) {
            $statusCode = [int]$_.Exception.Response.StatusCode
            try {
                $reader = New-Object System.IO.StreamReader($_.Exception.Response.GetResponseStream())
                $content = $reader.ReadToEnd()
                $reader.Close()
            } catch {}
            try {
                foreach ($h in $_.Exception.Response.Headers) {
                    $headers[$h] = $_.Exception.Response.Headers[$h]
                }
            } catch {}
        }
        return @{
            Url = $logUrl
            Status = $statusCode
            Headers = $headers
            Content = $content
            Error = $_.Exception.Message
        }
    }
}

function Write-Section($title) {
    Write-Host ""
    Write-Host ("=" * 70)
    Write-Host "  $title"
    Write-Host ("=" * 70)
}

# ─── EXPERIMENT 1: /manifest.json ──────────────────────────────────────────
Write-Section "EXPERIMENT 1: GET /manifest.json (no auth)"
$r1 = Invoke-SafeRequest -Url "$ADDON_URL/manifest.json"
Write-Host "  Status: $($r1.Status)"
Write-Host "  Error:  $($r1.Error)"
if ($r1.Content) {
    $manifest = $r1.Content
    # Redact any token references
    $manifest = $manifest -replace $token, "***REDACTED***"
    Write-Host "  Body:`n$manifest"
}

# ─── EXPERIMENT 2: /search for Skyfall ────────────────────────────────────
Write-Section "EXPERIMENT 2: GET /search?q=Skyfall+Adele (no auth)"
$r2 = Invoke-SafeRequest -Url "$ADDON_URL/search?q=Skyfall+Adele&quality=27"
Write-Host "  Status: $($r2.Status)"
Write-Host "  Error:  $($r2.Error)"
if ($r2.Content) {
    $searchBody = $r2.Content
    $searchBody = $searchBody -replace $token, "***REDACTED***"
    # Truncate if very long
    if ($searchBody.Length -gt 3000) { $searchBody = $searchBody.Substring(0, 3000) + "...[TRUNCATED]" }
    Write-Host "  Body:`n$searchBody"
}

# ─── EXPERIMENT 3: /search for The Hanging Tree ──────────────────────────
Write-Section "EXPERIMENT 3: GET /search?q=The+Hanging+Tree (no auth)"
$r3 = Invoke-SafeRequest -Url "$ADDON_URL/search?q=The+Hanging+Tree&quality=6"
Write-Host "  Status: $($r3.Status)"
Write-Host "  Error:  $($r3.Error)"
if ($r3.Content) {
    $body3 = $r3.Content -replace $token, "***REDACTED***"
    if ($body3.Length -gt 3000) { $body3 = $body3.Substring(0, 3000) + "...[TRUNCATED]" }
    Write-Host "  Body:`n$body3"
}

# ─── EXPERIMENT 4: /stream/34439418 (Skyfall) ────────────────────────────
Write-Section "EXPERIMENT 4: GET /stream/34439418 (Skyfall, quality=27, no auth)"
$r4 = Invoke-SafeRequest -Url "$ADDON_URL/stream/34439418?quality=27"
Write-Host "  Status: $($r4.Status)"
Write-Host "  Error:  $($r4.Error)"

$skyfallStreamUrl = $null
if ($r4.Content) {
    $body4 = $r4.Content -replace $token, "***REDACTED***"
    Write-Host "  Body:`n$body4"
    
    try {
        $streamData = ConvertFrom-Json $r4.Content
        $skyfallStreamUrl = $streamData.url
        Write-Host "`n  --- Stream Metadata Analysis ---"
        Write-Host "  Claimed format:     $($streamData.format)"
        Write-Host "  Claimed codec:      $($streamData.codec)"
        Write-Host "  Claimed quality:    $($streamData.quality)"
        Write-Host "  Claimed bitrate:    $($streamData.bitrate)"
        Write-Host "  Claimed sampleRate: $($streamData.sampleRate)"
        Write-Host "  Claimed bitDepth:   $($streamData.bitDepth)"
        
        if ($skyfallStreamUrl) {
            $isPrank = $skyfallStreamUrl -match "pranks-cdn|definatelynagato|gemi2"
            Write-Host "  Is prank URL:       $isPrank"
            if ($isPrank) {
                Write-Host "  WARNING: PRANK URL DETECTED: $skyfallStreamUrl"
            } else {
                $streamUri = [System.Uri]$skyfallStreamUrl
                Write-Host "  Stream domain:      $($streamUri.Host)"
                $pathPrefix = if ($streamUri.AbsolutePath.Length -gt 40) { $streamUri.AbsolutePath.Substring(0,40) + "..." } else { $streamUri.AbsolutePath }
                Write-Host "  Stream path prefix: $pathPrefix"
            }
        }
    } catch {
        Write-Host "  Could not parse stream response as JSON"
    }
}

# ─── EXPERIMENT 5: /stream/37977811 (The Hanging Tree) ───────────────────
Write-Section "EXPERIMENT 5: GET /stream/37977811 (The Hanging Tree, quality=6, no auth)"
$r5 = Invoke-SafeRequest -Url "$ADDON_URL/stream/37977811?quality=6"
Write-Host "  Status: $($r5.Status)"
Write-Host "  Error:  $($r5.Error)"

$hangingTreeStreamUrl = $null
if ($r5.Content) {
    $body5 = $r5.Content -replace $token, "***REDACTED***"
    Write-Host "  Body:`n$body5"
    try {
        $streamData5 = ConvertFrom-Json $r5.Content
        $hangingTreeStreamUrl = $streamData5.url
        if ($hangingTreeStreamUrl) {
            $isPrank5 = $hangingTreeStreamUrl -match "pranks-cdn|definatelynagato|gemi2"
            Write-Host "`n  Is prank URL: $isPrank5"
        }
    } catch {}
}

# ─── EXPERIMENT 6: Validate actual stream media ─────────────────────────
Write-Section "EXPERIMENT 6: Validate actual stream media (first 8KB)"

function Test-StreamMedia {
    param([string]$Label, [string]$Url)
    
    if (-not $Url) {
        Write-Host "`n  [$Label] No stream URL available - skipping"
        return
    }
    
    Write-Host "`n  [$Label] Downloading first 8KB to validate container..."
    
    try {
        $webClient = New-Object System.Net.WebClient
        $webClient.Headers.Add("User-Agent", "LosslessBridge-Experiment/1.0")
        
        # Use HttpWebRequest for more control
        $req = [System.Net.HttpWebRequest]::Create($Url)
        $req.Method = "GET"
        $req.UserAgent = "LosslessBridge-Experiment/1.0"
        $req.Timeout = 15000
        $req.AddRange(0, 8191)
        
        $resp = $req.GetResponse()
        Write-Host "  Status:        $([int]$resp.StatusCode)"
        Write-Host "  Content-Type:  $($resp.ContentType)"
        Write-Host "  Content-Length: $($resp.ContentLength)"
        
        # Check headers
        $ar = $resp.Headers["Accept-Ranges"]
        $cr = $resp.Headers["Content-Range"]
        Write-Host "  Accept-Ranges: $(if($ar){$ar}else{'not present'})"
        Write-Host "  Content-Range: $(if($cr){$cr}else{'not present'})"
        
        $stream = $resp.GetResponseStream()
        $buffer = New-Object byte[] 8192
        $totalRead = 0
        $allBytes = New-Object System.Collections.Generic.List[byte]
        
        while ($totalRead -lt 8192) {
            $bytesRead = $stream.Read($buffer, 0, [Math]::Min(8192 - $totalRead, $buffer.Length))
            if ($bytesRead -eq 0) { break }
            for ($i = 0; $i -lt $bytesRead; $i++) {
                $allBytes.Add($buffer[$i])
            }
            $totalRead += $bytesRead
        }
        $stream.Close()
        $resp.Close()
        
        $bytes = $allBytes.ToArray()
        Write-Host "  Bytes read:    $($bytes.Length)"
        
        # Hex dump first 32 bytes
        $hexStr = ($bytes[0..([Math]::Min(31, $bytes.Length-1))] | ForEach-Object { $_.ToString("x2") }) -join " "
        Write-Host "  First 32 bytes: $hexStr"
        
        # Check magic bytes
        if ($bytes.Length -ge 4) {
            $magic = [System.Text.Encoding]::ASCII.GetString($bytes[0..3])
            
            if ($magic -eq "fLaC") {
                Write-Host "  MAGIC: fLaC - THIS IS GENUINE FLAC!"
                
                # Parse STREAMINFO
                if ($bytes.Length -ge 42) {
                    $blockType = $bytes[4] -band 0x7F
                    if ($blockType -eq 0) {
                        # Parse sample rate (20 bits), channels (3 bits), bit depth (5 bits), total samples (36 bits)
                        # Bytes 18-25 of the file (10-17 of STREAMINFO)
                        $b = $bytes[18..25]
                        $sampleRate = ($b[0] -shl 12) -bor ($b[1] -shl 4) -bor ($b[2] -shr 4)
                        $channels = (($b[2] -shr 1) -band 0x07) + 1
                        $bitDepth = (($b[2] -band 0x01) -shl 4) -bor ($b[3] -shr 4) 
                        $bitDepth = $bitDepth + 1
                        
                        $totalSamples = [uint64](($b[3] -band 0x0F))
                        $totalSamples = ($totalSamples -shl 8) -bor $b[4]
                        $totalSamples = ($totalSamples -shl 8) -bor $b[5]
                        $totalSamples = ($totalSamples -shl 8) -bor $b[6]
                        $totalSamples = ($totalSamples -shl 8) -bor $b[7]
                        
                        $durationSec = if ($sampleRate -gt 0) { [math]::Round($totalSamples / $sampleRate, 3) } else { 0 }
                        $durationMs = [math]::Round($durationSec * 1000, 1)
                        
                        Write-Host "  STREAMINFO:"
                        Write-Host "    Sample rate:    $sampleRate Hz"
                        Write-Host "    Bit depth:      $bitDepth bits"
                        Write-Host "    Channels:       $channels"
                        Write-Host "    Total samples:  $totalSamples"
                        Write-Host "    Duration:       ${durationSec}s (${durationMs}ms)"
                    } else {
                        Write-Host "  WARNING: First metadata block is type $blockType, expected 0 (STREAMINFO)"
                    }
                }
            }
            elseif ($magic.Substring(0,3) -eq "ID3") {
                Write-Host "  MAGIC: ID3 - THIS IS MP3 (NOT FLAC!)"
            }
            elseif ($bytes[0] -eq 0xFF -and ($bytes[1] -band 0xE0) -eq 0xE0) {
                Write-Host "  MAGIC: MP3 sync word - THIS IS MP3 (NOT FLAC!)"
            }
            elseif ($magic -eq "OggS") {
                Write-Host "  MAGIC: OggS - THIS IS OGG (NOT FLAC!)"
            }
            elseif ($magic -eq "RIFF") {
                Write-Host "  MAGIC: RIFF - THIS IS WAV (NOT FLAC!)"
            }
            else {
                Write-Host "  MAGIC: Unknown ($magic / $hexStr)"
            }
        }
    }
    catch {
        Write-Host "  ERROR: $($_.Exception.Message)"
    }
}

Test-StreamMedia -Label "Skyfall" -Url $skyfallStreamUrl
Test-StreamMedia -Label "Hanging Tree" -Url $hangingTreeStreamUrl

# ─── EXPERIMENT 7: Range request support ─────────────────────────────────
Write-Section "EXPERIMENT 7: Range request support (detailed)"

$testUrl = if ($skyfallStreamUrl) { $skyfallStreamUrl } else { $hangingTreeStreamUrl }
if ($testUrl) {
    # HEAD request
    Write-Host "`n  [HEAD request]"
    try {
        $headReq = [System.Net.HttpWebRequest]::Create($testUrl)
        $headReq.Method = "HEAD"
        $headReq.UserAgent = "LosslessBridge-Experiment/1.0"
        $headReq.Timeout = 15000
        $headResp = $headReq.GetResponse()
        Write-Host "  Status:         $([int]$headResp.StatusCode)"
        Write-Host "  Content-Length: $($headResp.ContentLength)"
        Write-Host "  Accept-Ranges:  $($headResp.Headers['Accept-Ranges'])"
        Write-Host "  Content-Type:   $($headResp.ContentType)"
        $headResp.Close()
    }
    catch {
        Write-Host "  HEAD Error: $($_.Exception.Message)"
    }
    
    # Range: bytes=0-65535
    Write-Host "`n  [Range: bytes=0-65535]"
    try {
        $rangeReq = [System.Net.HttpWebRequest]::Create($testUrl)
        $rangeReq.Method = "GET"
        $rangeReq.UserAgent = "LosslessBridge-Experiment/1.0"
        $rangeReq.Timeout = 15000
        $rangeReq.AddRange(0, 65535)
        $rangeResp = $rangeReq.GetResponse()
        Write-Host "  Status:         $([int]$rangeResp.StatusCode)"
        Write-Host "  Content-Length: $($rangeResp.ContentLength)"
        Write-Host "  Content-Range:  $($rangeResp.Headers['Content-Range'])"
        Write-Host "  Accept-Ranges:  $($rangeResp.Headers['Accept-Ranges'])"
        $rangeResp.Close()
    }
    catch {
        Write-Host "  Range Error: $($_.Exception.Message)"
    }
    
    # Range: bytes=65536-131071
    Write-Host "`n  [Range: bytes=65536-131071]"
    try {
        $rangeReq2 = [System.Net.HttpWebRequest]::Create($testUrl)
        $rangeReq2.Method = "GET"
        $rangeReq2.UserAgent = "LosslessBridge-Experiment/1.0"
        $rangeReq2.Timeout = 15000
        $rangeReq2.AddRange(65536, 131071)
        $rangeResp2 = $rangeReq2.GetResponse()
        Write-Host "  Status:         $([int]$rangeResp2.StatusCode)"
        Write-Host "  Content-Length: $($rangeResp2.ContentLength)"
        Write-Host "  Content-Range:  $($rangeResp2.Headers['Content-Range'])"
        $rangeResp2.Close()
    }
    catch {
        Write-Host "  Range Error: $($_.Exception.Message)"
    }
} else {
    Write-Host "  No stream URL available for Range testing"
}

# ─── EXPERIMENT 8: HMAC Signing ──────────────────────────────────────────
Write-Section "EXPERIMENT 8: HMAC Signing Analysis"

Write-Host "  Token extracted from URL: YES ($($token.Substring(0,8))...$($token.Substring($token.Length-4)))"
Write-Host "  Token length: $($token.Length)"
Write-Host ""

Write-Host "  [Test: Bogus HMAC headers]"
$ts = [Math]::Floor(([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()) / 1000).ToString()
$bogusSign = "0000000000000000000000000000000000000000000000000000000000000000"

$r8 = Invoke-SafeRequest -Url "$ADDON_URL/manifest.json" -Headers @{
    "X-LW-TS" = $ts
    "X-LW-Sign" = $bogusSign
}
Write-Host "  Status with bogus HMAC: $($r8.Status)"
Write-Host "  Status without HMAC:    $($r1.Status)"

if ($r8.Status -eq $r1.Status) {
    Write-Host "  CONCLUSION: Server does NOT validate HMAC - requests work without signing"
} else {
    Write-Host "  CONCLUSION: Server MAY validate HMAC - different status codes"
}

# Also test search with bogus HMAC
Write-Host ""
Write-Host "  [Test: Search with bogus HMAC]"
$r8b = Invoke-SafeRequest -Url "$ADDON_URL/search?q=Skyfall+Adele&quality=27" -Headers @{
    "X-LW-TS" = $ts
    "X-LW-Sign" = $bogusSign
}
Write-Host "  Status with bogus HMAC: $($r8b.Status)"
Write-Host "  Status without HMAC:    $($r2.Status)"

# ─── EXPERIMENT 9: Quality tiers ─────────────────────────────────────────
Write-Section "EXPERIMENT 9: Quality tier comparison (Skyfall)"

@(
    @{Id=27; Name="Hi-Res 192k"},
    @{Id=7; Name="Hi-Res 96k"},
    @{Id=6; Name="CD FLAC"},
    @{Id=5; Name="MP3 320"}
) | ForEach-Object {
    $qid = $_.Id
    $qname = $_.Name
    $rq = Invoke-SafeRequest -Url "$ADDON_URL/stream/34439418?quality=$qid"
    if ($rq.Content) {
        try {
            $sd = ConvertFrom-Json $rq.Content
            $streamUrlQ = $sd.url
            $isPrankQ = if ($streamUrlQ) { $streamUrlQ -match "pranks-cdn|definatelynagato|gemi2" } else { "no_url" }
            $domain = if ($streamUrlQ -and -not $isPrankQ) { ([System.Uri]$streamUrlQ).Host } else { "N/A" }
            Write-Host "  quality=$qid ($qname): format=$($sd.format) sr=$($sd.sampleRate) bd=$($sd.bitDepth) br=$($sd.bitrate) prank=$isPrankQ domain=$domain"
        } catch {
            Write-Host "  quality=$qid ($qname): parse error"
        }
    } else {
        Write-Host "  quality=$qid ($qname): status=$($rq.Status) error=$($rq.Error)"
    }
}

# ─── SUMMARY ─────────────────────────────────────────────────────────────
Write-Section "EXPERIMENT SUMMARY"
Write-Host @"

  Review the output above to determine:
  1. Does /manifest.json work? What resources does it declare?
  2. Does /search return real track candidates?
  3. Does /stream return prank URLs or real Qobuz CDN URLs?
  4. Is HMAC signing required?
  5. Is the actual media FLAC or MP3?
  6. What are the real sample rate, bit depth, duration?
  7. Does the CDN support Range requests?
  8. Do different quality tiers return different streams?
"@
