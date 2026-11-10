param(
    [string]$Source,
    [string]$Project,
    [ValidateSet('run','retranslate','update','check','analyze','rebuild','repair','status','review','verify-source','prepare','catalog','sample','translate','render','preview','support','seal-stage','validate','package','tasks','answers','routes','language')][string]$Command,
    [string]$Model,
    [string]$RuntimeId,
    [ValidateSet('english','japanese','en','ja')][string]$SourceLanguage,
    [ValidateSet('all','font','names','failed','display','layout')][string]$Fix,
    [ValidateSet('run','retranslate','update','check','font','names','failed','display','layout','answers','routes','language')][string[]]$Tasks,
    [string]$Output,
    [string]$Suffix,
    [string]$TextboxScale,
    [ValidateSet('show','hide')][string]$ReferenceDisplay,
    [ValidateSet('left','right')][string]$LanguageCorner,
    [ValidateRange(0,300)][int]$LanguageMargin,
    [ValidateRange(0,1000000)][int]$Sample = 0,
    [switch]$Help
)
$ErrorActionPreference = 'Stop'
if ($Help -or -not $Command -or ($Command -eq 'repair' -and -not $Fix) -or ($Command -eq 'tasks' -and -not $Tasks)) {
    Write-Output @'
RenPyTranslator - explicit commands only; no default action

Usage:
  .\translate.ps1 -Command <command> -Source <game-folder>
  .\translate.ps1 -Command repair -Project <project-folder> -Fix <scope>
  .\translate.ps1 -Command tasks -Project <project-folder> -Tasks names,font
  .\translate.ps1 -Command tasks -Source <game-folder> -Tasks answers,routes
  .\translate.ps1 -Command update -Project <old-project> -Source <new-game-folder>
  .\translate.ps1 -Command check -Project <project-folder>

Commands:
  Supported formats: RenPy and RPG Maker MV/MZ (detected from the selected folder).
  RPG Maker: run, retranslate, update, check, prepare, status, rebuild,
             font/names/failed/display/layout repairs.
  RPG Maker answers, routes and language panel are not supported.
  run           Prepare, translate and package the selected game
  retranslate   Translate afresh in a separate project; keep the previous result
  update        Reuse existing translation for the updated game
  check         RenPy isolated engine checks; RPG Maker static output checks
  prepare       Prepare only; no translation
  repair        Repair an existing project (requires -Fix)
  answers       In-game answer hints (beta)
  routes        Choice effects + scene hints (beta)
  language      Add/update language panel (-Project)
  tasks         Run selected -Tasks only (required; same as GUI checkboxes)
  status        Show translation counts
  rebuild       Rebuild from saved translations
  analyze       Explicit context analysis
  review        Explicit translation review
  verify-source Verify original source hashes
  Advanced: catalog, sample, translate, render, preview, support,
            seal-stage, validate, package

Repair scopes (-Fix):
  font    Fonts, standard menus and display; no model
  names   RenPy names/address/input; RPG Maker actor-name consistency (no model)
  failed  Find unresolved failures; reuse saved output before local translation
  display Original-language reference + layout; no model
  layout  Textbox height only; -TextboxScale default or e.g. 1.2
  all     All fixes (may use a local model)

Other options: -Model <name>, -SourceLanguage <english|japanese>, -Sample <count> (analyze only), -Help
Output is Korean. Omit -SourceLanguage to retain an existing project's language.
Tasks: run OR retranslate OR update, answers, routes, check, language, and repair scopes.
Retranslate requires a prepared project; interrupted runs resume with the same model.
Advanced: -Output <folder>, -Suffix <suffix>, -TextboxScale <default|number>
Original reference: -ReferenceDisplay <show|hide> (both supported engines)
Language panel: -LanguageCorner <left|right>, -LanguageMargin <0..300>
Runtime: -RuntimeId <installed Ollama version ID>
No command, or repair without -Fix: show this help and do nothing.
'@
    exit 0
}
$interpreter = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$portable = Join-Path $PSScriptRoot 'RenPyTranslator-cli.exe'
$entrypoint = Join-Path $PSScriptRoot 'app\portable_cli.py'
$cliArgs = @($Command)
if ($Source) { $cliArgs += @('--source', $Source) }
if ($Project) { $cliArgs += @('--project', $Project) }
if ($Model) { $cliArgs += @('--model', $Model) }
if ($PSBoundParameters.ContainsKey('RuntimeId')) { $cliArgs += @('--runtime-id=' + $RuntimeId) }
if ($SourceLanguage) { $cliArgs += @('--source-language', $SourceLanguage) }
if ($Fix) { $cliArgs += @('--fix', $Fix) }
if ($Tasks) { $cliArgs += @('--tasks') + $Tasks }
if ($Sample) { $cliArgs += @('--sample', $Sample) }
if ($PSBoundParameters.ContainsKey('Output')) { $cliArgs += @('--output', $Output) }
if ($PSBoundParameters.ContainsKey('Suffix')) { $cliArgs += @('--suffix=' + $Suffix) }
if ($PSBoundParameters.ContainsKey('TextboxScale')) { $cliArgs += @('--textbox-scale', $TextboxScale) }
if ($ReferenceDisplay) { $cliArgs += $(if ($ReferenceDisplay -eq 'show') { '--show-reference' } else { '--hide-reference' }) }
if ($LanguageCorner) { $cliArgs += @('--language-corner', $LanguageCorner) }
if ($PSBoundParameters.ContainsKey('LanguageMargin')) { $cliArgs += @('--language-margin', "$LanguageMargin") }
if ((Test-Path -LiteralPath $entrypoint) -and (Test-Path -LiteralPath $interpreter)) { & $interpreter -B -X utf8 $entrypoint @cliArgs }
elseif (Test-Path -LiteralPath $portable) { & $portable @cliArgs }
else { throw 'CLI runtime not found. Source users: run .\setup.ps1 first. Portable users: extract the entire ZIP.' }
exit $LASTEXITCODE
