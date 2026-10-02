param(
    [string]$Source,
    [string]$Project,
    [ValidateSet('run','retranslate','analyze','rebuild','repair','status','review','verify-source','prepare','catalog','sample','translate','render','preview','support','seal-stage','validate','package','tasks','answers','routes','language')][string]$Command,
    [string]$Model,
    [ValidateSet('all','font','names','failed','display','layout')][string]$Fix,
    [ValidateSet('run','retranslate','font','names','failed','display','layout','answers','routes','language')][string[]]$Tasks,
    [string]$Output,
    [string]$Suffix,
    [string]$TextboxScale,
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

Commands:
  run           Prepare, translate and package the selected game
  retranslate   Translate afresh in a separate project; keep the previous result
  prepare       Prepare only; no translation
  repair        Repair an existing project (requires -Fix)
  answers       In-game answer hints patch (no model/game execution)
  routes        Choice effects + scene hints (local model for missing summaries only)
  language      Add/update language panel only (-Project); no model/game execution
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
  names   Names, forms of address, input prompts/defaults and affected text
  failed  Indexed failures only; saved output first, local model if needed
  display Original-language reference + layout; no model
  layout  Textbox height only; -TextboxScale default or e.g. 1.2
  all     All fixes (may use a local model)

Other options: -Model <name>, -Sample <count> (analyze only), -Help
Tasks: run OR retranslate, answers, routes, language, and repair scopes.
Retranslate requires a prepared project; interrupted runs resume with the same model.
Advanced: -Output <folder>, -Suffix <suffix>, -TextboxScale <default|number>
Language panel: -LanguageCorner <left|right>, -LanguageMargin <0..300>
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
if ($Fix) { $cliArgs += @('--fix', $Fix) }
if ($Tasks) { $cliArgs += @('--tasks') + $Tasks }
if ($Sample) { $cliArgs += @('--sample', $Sample) }
if ($PSBoundParameters.ContainsKey('Output')) { $cliArgs += @('--output', $Output) }
if ($PSBoundParameters.ContainsKey('Suffix')) { $cliArgs += @('--suffix=' + $Suffix) }
if ($PSBoundParameters.ContainsKey('TextboxScale')) { $cliArgs += @('--textbox-scale', $TextboxScale) }
if ($LanguageCorner) { $cliArgs += @('--language-corner', $LanguageCorner) }
if ($PSBoundParameters.ContainsKey('LanguageMargin')) { $cliArgs += @('--language-margin', "$LanguageMargin") }
if ((Test-Path -LiteralPath $entrypoint) -and (Test-Path -LiteralPath $interpreter)) { & $interpreter -B -X utf8 $entrypoint @cliArgs }
elseif (Test-Path -LiteralPath $portable) { & $portable @cliArgs }
else { throw 'CLI runtime not found. Source users: run .\setup.ps1 first. Portable users: extract the entire ZIP.' }
exit $LASTEXITCODE
