"""Run the validation probe against invented games and already-present SDKs only."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
from engine import interpreter_args, select_windows_runtime, find_launcher, save_json
from patch_validation import _install_probe, _parse_events


SOURCE = '''init python:
    config.name = "Validation fixture"
    config.screen_width = 800
    config.screen_height = 600
    config.save_directory = "rpt-validation-fixture"
    config.window_title = "Validation fixture"

screen main_menu():
    vbox:
        text "Validation fixture"
        textbutton "Credits" action ShowMenu("credits")

screen say(who, what):
    window:
        text what id "what"

screen input(prompt):
    vbox:
        text prompt
        input id "input"

screen choice(items):
    vbox:
        for i in items:
            textbutton (i.caption if hasattr(i, 'caption') else i[0]) action (i.action if hasattr(i, 'action') else i[1])

label start:
    "This label must never be entered by the probe."
    return
'''


def main():
    versions = sys.argv[1:] or ['6.99.0', '7.3.5', '8.0.3', '8.5.3']
    summaries = []
    root = ROOT/'build/patch-validation-fixtures'/time.strftime('%Y%m%d-%H%M%S')
    for version in versions:
        sdk = ROOT/'build/renpy-validation'/('renpy-'+version+'-sdk')
        workspace = root/version
        game = workspace/'staging/game'
        game.mkdir(parents=True)
        (workspace/'data').mkdir()
        (game/'script.rpy').write_text(SOURCE, encoding='utf-8')
        rows = [dict(id='fixture', file='invented.rpy', line=1, kind='dialogue',
                     source='Text.', text='Text.\n{size=18}Reference.{/size}')]
        _install_probe(workspace, rows, [dict(target='credits', file='script.rpy', line=11)], None)
        env = dict(os.environ, RENPY_PATH_TO_SAVES=str(workspace/'data/saves'),
                   RENPY_MULTIPERSISTENT=str(workspace/'data/multipersistent'),
                   RENPY_SKIP_SPLASHSCREEN='1', RENPY_DISABLE_FULLSCREEN='1', PYTHONDONTWRITEBYTECODE='1',
                   RPT_VALIDATION_INPUT=str(workspace/'data/probe-input.json'),
                   RPT_VALIDATION_EVENTS=str(workspace/'data/probe-events.jsonl'))
        interpreter = select_windows_runtime(sdk)
        launcher = find_launcher(sdk)
        outcomes = []
        for operation in ('compile', 'lint', 'render'):
            events = workspace/'data/probe-events.jsonl'
            if events.exists():
                events.unlink()
            log = workspace/'data'/(operation+'.log')
            command = [*interpreter_args(interpreter), str(launcher), str(workspace/'staging')]
            if operation != 'render':
                command.append(operation)
            started = time.monotonic()
            try:
                with log.open('w', encoding='utf-8') as stream:
                    process = subprocess.run(command, cwd=workspace/'staging', env=env, stdout=stream,
                                             stderr=subprocess.STDOUT, timeout=60,
                                             creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                record = dict(operation=operation, code=process.returncode, events=_parse_events(events))
            except subprocess.TimeoutExpired:
                record = dict(operation=operation, code='timeout', events=_parse_events(events))
            record['seconds'] = round(time.monotonic()-started,3)
            outcomes.append(record)
            if record['code'] != 0:
                break
        summaries.append(dict(version=version, workspace=str(workspace), outcomes=outcomes))
        print(json.dumps(summaries[-1],ensure_ascii=False))
    save_json(root/'summary.json', summaries)
    return 0 if all(len(s['outcomes'])==3 and s['outcomes'][-1]['code']==0 and
                    any(e.get('check')=='complete' for e in s['outcomes'][-1]['events']) and
                    all(e.get('status')!='failed' or e.get('check')=='menu_target' for e in s['outcomes'][-1]['events'])
                    for s in summaries) else 1


if __name__ == '__main__':
    raise SystemExit(main())
