# Step 1: build the sample document with Word COM (hidden), export to PDF.
# ASCII-only on purpose (PowerShell 5.1); Japanese text comes from sample_text.txt (UTF-8).
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$lines = Get-Content -Encoding UTF8 (Join-Path $here 'sample_text.txt')
$outDir = Join-Path $here 'out'
$pdf = Join-Path $outDir 'sample_word.pdf'
$docx = Join-Path $outDir 'sample_word.docx'
$word = $null; $doc = $null
function AddPara($text, $font, $size, $align) {
    $r = $doc.Paragraphs.Last.Range
    $r.Text = $text
    $r.Font.Name = $font; $r.Font.NameFarEast = $font; $r.Font.NameAscii = $font
    $r.Font.Size = $size; $r.Font.Bold = 0; $r.Font.Color = -16777216
    $r.ParagraphFormat.Alignment = $align
    $r.ParagraphFormat.Shading.BackgroundPatternColor = -16777216
    $r.ParagraphFormat.PageBreakBefore = 0
    $r.ListFormat.RemoveNumbers()
    $r.InsertParagraphAfter()
    return $doc.Paragraphs.Item($doc.Paragraphs.Count - 1).Range
}
function Mark($range, $what, $bold, $color) {
    $f = $range.Duplicate
    if ($f.Find.Execute($what)) {
        if ($bold) { $f.Font.Bold = 1 }
        if ($color -ne $null) { $f.Font.Color = $color }
    } else { Write-Output "not found: run $what" }
}
try {
    $word = New-Object -ComObject Word.Application
    $word.Visible = $false
    $word.DisplayAlerts = 0
    $doc = $word.Documents.Add()
    $t = AddPara $lines[0] 'Yu Gothic' 14 1; $t.Font.Bold = 1
    $p1 = AddPara $lines[1] 'Yu Mincho' 10.5 3
    Mark $p1 $lines[12] $true $null
    Mark $p1 $lines[13] $false 255
    $p2 = AddPara $lines[2] 'Yu Gothic' 10.5 3
    $p2.ParagraphFormat.Shading.BackgroundPatternColor = 14277081
    foreach ($i in 3..5) { $b = AddPara $lines[$i] 'Meiryo' 10.5 0; $b.ListFormat.ApplyBulletDefault() }
    $p3 = AddPara $lines[6] 'MS Mincho' 10.5 3
    # table after P3
    $r = $doc.Paragraphs.Last.Range
    $tbl = $doc.Tables.Add($r, 3, 3)
    $tbl.Borders.Enable = 1
    foreach ($row in 1..3) {
        $cells = $lines[8 + $row] -split "`t"
        foreach ($col in 1..3) {
            $c = $tbl.Cell($row, $col).Range
            $c.Text = $cells[$col - 1]
            $c.Font.Name = 'MS Mincho'; $c.Font.NameFarEast = 'MS Mincho'; $c.Font.Size = 10.5
        }
    }
    $p4 = AddPara $lines[7] 'Meiryo' 10.5 0
    $p4.ParagraphFormat.PageBreakBefore = 1
    Mark $p4 $lines[14] $false 16711680
    $p5 = AddPara $lines[8] 'Yu Mincho' 10.5 3
    $doc.SaveAs2($docx, 16)
    $doc.ExportAsFixedFormat($pdf, 17)
    Write-Output "OK pages=$($doc.ComputeStatistics(2)) word=$($word.Version)"
}
catch {
    Write-Output "WORD FAILED: $($_.Exception.Message)"
}
finally {
    if ($doc -ne $null) { try { $doc.Close(0) } catch {} }
    if ($word -ne $null) { try { $word.Quit(0) } catch {}; [void][Runtime.InteropServices.Marshal]::ReleaseComObject($word) }
}
