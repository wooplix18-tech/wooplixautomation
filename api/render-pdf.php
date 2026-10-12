<?php
// Shared Dompdf renderer: accepts a saved artifact JSON body and returns PDF bytes.
$autoload = dirname(__DIR__) . '/vendor/autoload.php';
if (!is_file($autoload)) {
    http_response_code(503);
    if (PHP_SAPI !== 'cli') header('Content-Type: text/plain; charset=utf-8');
    fwrite(STDERR, "Dompdf dependencies are missing. Run composer install.\n");
    exit(1);
}
require_once $autoload;

use Dompdf\Dompdf;
use Dompdf\Options;

$raw = file_get_contents(PHP_SAPI === 'cli' ? 'php://stdin' : 'php://input');
$artifact = json_decode($raw ?: '', true);
if (!is_array($artifact) || !is_array($artifact['content'] ?? null)) {
    http_response_code(400);
    if (PHP_SAPI !== 'cli') header('Content-Type: text/plain; charset=utf-8');
    fwrite(STDERR, "Expected a saved document artifact.\n");
    exit(1);
}

function h($value): string {
    return htmlspecialchars((string)($value ?? ''), ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
}
function title_for(string $key): string {
    static $names = [
        'executive_summary' => 'Summary', 'in_scope' => 'What’s included',
        'out_of_scope' => 'Not included', 'delivery_approach' => 'How the work will happen',
        'commercials' => 'Separate costs', 'training_plan' => 'Training plan',
        'open_decisions' => 'Questions to confirm', 'acceptance_criteria' => 'How we’ll know it works',
        'functional_requirements' => 'What the system must do',
        'non_functional_requirements' => 'Quality and operating needs',
        'personas' => 'People using the system', 'business_rules' => 'Business rules',
        'assumptions' => 'Assumptions', 'risks' => 'Risks', 'support' => 'Support after launch',
        'objectives' => 'Goals', 'deliverables' => 'Documents and outputs',
        'timeline' => 'Timeline', 'requirements' => 'Requirements', 'next_step' => 'Next step'
    ];
    return $names[$key] ?? ucfirst(str_replace('_', ' ', $key));
}
function render_value($value, int $depth = 0): string {
    $hidden = ['id','generated_by','generated_at','integration','shared_requirement_links','agent_audit','source_trace','trace','requirement_register','context_record_ids','status','planning_target','traditional_hours','ai_assisted_hours','human_involvement_hours'];
    if ($value === null || $value === '') return '<p class="muted">To be confirmed</p>';
    if (is_scalar($value)) return '<p>' . nl2br(h($value)) . '</p>';
    if (is_array($value) && array_is_list($value)) {
        if (!$value) return '<p class="muted">None listed</p>';
        $html = '<ul>';
        foreach ($value as $item) $html .= '<li>' . (is_array($item) ? render_value($item, $depth + 1) : h($item)) . '</li>';
        return $html . '</ul>';
    }
    if (!is_array($value)) return '<p>' . h($value) . '</p>';
    if (isset($value['items']) && is_array($value['items'])) {
        $html = '<table><thead><tr><th>Item</th><th>Amount</th><th>Notes</th></tr></thead><tbody>';
        foreach ($value['items'] as $row) {
            if (!is_array($row)) continue;
            $html .= '<tr><td>' . h($row['item'] ?? '') . '</td><td>' . h($row['amount'] ?? 'To be quoted') . '</td><td>' . h($row['basis'] ?? '') . '</td></tr>';
        }
        $html .= '</tbody></table>';
        foreach ($value as $key => $child) if ($key !== 'items' && $key !== 'total' && !in_array($key, $hidden, true)) $html .= '<h3>' . h(title_for((string)$key)) . '</h3>' . render_value($child, $depth + 1);
        return $html;
    }
    $html = '';
    foreach ($value as $key => $child) {
        if (in_array((string)$key, $hidden, true)) continue;
        $html .= '<div class="field"><strong>' . h(title_for((string)$key)) . '</strong>' . render_value($child, $depth + 1) . '</div>';
    }
    return $html;
}

$content = $artifact['content'];
$sections = '';
$internal = ['id','generated_by','generated_at','integration','shared_requirement_links','agent_audit','source_trace','trace','requirement_register','context_record_ids','status'];
foreach ($content as $key => $value) {
    if (in_array((string)$key, $internal, true)) continue;
    $sections .= '<section><h2>' . h(title_for((string)$key)) . '</h2>' . render_value($value) . '</section>';
}
$title = h($artifact['title'] ?? 'Project document');
$version = (int)($artifact['version'] ?? 1);
$prepared = h(substr((string)($artifact['generated_at'] ?? date(DATE_ATOM)), 0, 10));
$html = '<!doctype html><html lang="en"><head><meta charset="utf-8"><style>
@page{size:A4;margin:22mm 17mm 20mm}body{font-family:"DejaVu Sans",sans-serif;color:#243447;font-size:9pt;line-height:1.55}h1{font-size:22pt;line-height:1.25;color:#10243a;text-align:center;margin:8mm 0}h2{font-size:13pt;color:#fff;background:#10243a;padding:7px 10px;margin:12mm 0 4mm;page-break-after:avoid}h3{font-size:10pt;color:#087f68;margin:6mm 0 2mm;page-break-after:avoid}p{margin:0 0 3mm}li{margin:0 0 2mm}.muted{color:#64748b;font-style:italic}.brand{text-align:center;color:#087f68;font-size:10pt;font-weight:bold;letter-spacing:1px}.meta{text-align:center;color:#64748b;font-size:8pt;margin-bottom:10mm}.field{padding:3mm 0;border-bottom:1px solid #e2e8f0;page-break-inside:avoid}.field>strong{display:block;color:#334155;margin-bottom:1mm}table{width:100%;border-collapse:collapse;margin:4mm 0}th{background:#10243a;color:white;text-align:left}th,td{padding:7px;border:1px solid #cbd5e1;vertical-align:top}tr:nth-child(even){background:#f1f6f6}footer{position:fixed;bottom:-13mm;left:0;right:0;border-top:1px solid #d8e1ea;padding-top:3mm;font-size:8pt;color:#64748b}footer .page:after{content:counter(page)}
</style></head><body><div class="brand">WOOPLIX TECHNOLOGIES PRIVATE LIMITED</div><h1>' . $title . '</h1><div class="meta">Draft for review · Version ' . $version . ' · Prepared ' . $prepared . '</div>' . $sections . '<footer>Wooplix · Draft for review <span style="float:right">Page <span class="page"></span></span></footer></body></html>';

$options = new Options();
$options->set('isRemoteEnabled', false);
$options->set('defaultFont', 'DejaVu Sans');
$options->set('isHtml5ParserEnabled', true);
$pdf = new Dompdf($options);
$pdf->loadHtml($html, 'UTF-8');
$pdf->setPaper('A4', 'portrait');
$pdf->render();
$bytes = $pdf->output();
if (PHP_SAPI === 'cli') {
    echo $bytes;
} else {
    $kind = preg_replace('/[^a-z0-9_-]/i', '', (string)($artifact['kind'] ?? 'document'));
    header('Content-Type: application/pdf');
    header('Content-Disposition: attachment; filename="wooplix-' . $kind . '-v' . $version . '.pdf"');
    header('Content-Length: ' . strlen($bytes));
    echo $bytes;
}
