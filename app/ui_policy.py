"""Menu policy independent of game titles. Never overwrite the translation cache."""
import ast
from script_literals import literal_eval
import hashlib
import json
import re
from engine import save_json

STANDARD = dict([
('Replay','다시보기'),('Achievements','업적'),('Gallery','갤러리'),
('gallery','갤러리'),('GALLERY','갤러리'),
('Start','시작'),('New Game','새 게임'),('Continue','계속하기'),('Load','불러오기'),
('Save','저장'),('Q.Save','빠른 저장'),('Q.Load','빠른 불러오기'),('Quick Save','빠른 저장'),
('Quick Load','빠른 불러오기'),('History','대화 기록'),('Back','뒤로'),('Return','돌아가기'),
('Hist','기록'),('Q.S','빠른 저장'),('Q.L','빠른 불러오기'),
('Skip','건너뛰기'),('Auto','자동'),('Prefs','설정'),('Preferences','설정'),('Settings','설정'),
('Main Menu','메인 메뉴'),('Quit','종료'),('End Replay','다시보기 종료'),('About','정보'),
('Help','도움말'),('Display','화면 모드'),('Window','창 모드'),('Fullscreen','전체 화면'),
('Unseen Text','읽지 않은 대사'),('After Choices','선택 후 계속'),('Transitions','화면 전환 효과'),
('Text Speed','텍스트 속도'),('Auto-Forward Time','자동 진행 대기 시간'),
('Music Volume','음악 음량'),('Sound Volume','효과음 음량'),('Voice Volume','음성 음량'),
('Mute All','모두 음소거'),('Test','시험 재생'),('Yes','예'),('No','아니요'),('OK','확인'),
('Cancel','취소'),('On','켜기'),('Off','끄기'),('Automatic saves','자동 저장'),
('Quick saves','빠른 저장'),('empty slot','빈 슬롯'),('Page {}','{} 페이지'),
('The dialogue history is empty.','대화 기록이 없습니다.'),('Keyboard','키보드'),
('Mouse','마우스'),('Gamepad','게임패드'),('Calibrate','보정'),('Left Click','왼쪽 클릭'),
('Right Click','오른쪽 클릭'),('Middle Click','가운데 클릭'),('Mouse Wheel Up','휠 위로'),
('Mouse Wheel Down','휠 아래로'),('Space','스페이스'),('Enter','엔터'),('Escape','Esc'),
('Ctrl','Ctrl'),('Control','Ctrl'),('Shift','Shift'),('Tab','Tab'),
('Are you sure?','진행하시겠습니까?'),('Are you sure you want to quit?','종료하시겠습니까?'),
('Are you sure you want to delete this save?','이 저장 파일을 삭제하시겠습니까?'),
('Are you sure you want to overwrite your save?','저장 파일을 덮어쓰시겠습니까?'),
('Loading will lose unsaved progress.\nAre you sure you want to do this?','불러오면 저장하지 않은 진행 상황을 잃습니다.\n불러오시겠습니까?'),
('Are you sure you want to return to the main menu?\nThis will lose unsaved progress.','메인 메뉴로 돌아가시겠습니까?\n저장하지 않은 진행 상황을 잃습니다.'),
('Are you sure you want to end the replay?','다시보기를 종료하시겠습니까?'),
('Are you sure you want to begin skipping?','건너뛰기를 시작하시겠습니까?'),
('Skipping','건너뛰는 중'),('Screenshot saved as %s.','스크린샷 저장: %s'),
('Quick save complete.','빠른 저장을 완료했습니다.'),
('Advances dialogue and activates the interface.','대사를 진행하거나 항목을 선택합니다.'),
('Advances dialogue without selecting choices.','선택지를 선택하지 않고 대사를 진행합니다.'),
('Accesses the game menu.','게임 메뉴를 엽니다.'),('Skips dialogue while held down.','누르는 동안 대사를 건너뜁니다.'),
('Toggles dialogue skipping.','대사 건너뛰기를 켜거나 끕니다.'),('Rolls back to earlier dialogue.','이전 대사로 돌아갑니다.'),
('Rolls forward to later dialogue.','다음 대사로 진행합니다.'),('Hides the user interface.','인터페이스를 숨깁니다.'),
('Takes a screenshot.','스크린샷을 저장합니다.'),
])
ROOTS={'navigation','quick_menu','main_menu','game_menu','save','load','file_slots','preferences',
       'confirm','help','about','history','notify','skip_indicator'}
EXCLUDE={'choice','say','nvl','nvl_choice','input','bubble'}
LITERAL=re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'')

def uncomment(line):
    masked=LITERAL.sub(lambda m:' '*len(m[0]),line)
    index=masked.find('#')
    return line if index<0 else line[:index]

def action_caption(line):
    """Recognize built-in button actions without evaluating game expressions."""
    masked=LITERAL.sub(lambda m:' '*len(m[0]),line)
    found=re.search(r'\baction\s+',masked)
    if not found:return None
    expression=line[found.end():].strip()
    masked=LITERAL.sub(lambda m:' '*len(m[0]),expression)
    depth=0;end=None
    for i,c in enumerate(masked):
        if c in '([':depth+=1
        elif c in ')]':
            depth-=1
            if depth==0:end=i+1;break
    if end is None:return None
    try:node=ast.parse(expression[:end],mode='eval').body
    except SyntaxError:return None
    def resolve(n):
        if isinstance(n,ast.Constant) and n.value is None:return ''
        if not isinstance(n,ast.Call) or not isinstance(n.func,ast.Name):return None
        name=n.func.id
        captions={'Rollback':'뒤로','Skip':'건너뛰기','QuickSave':'빠른 저장',
                  'QuickLoad':'빠른 불러오기','HideInterface':'숨기기'}
        if name in captions:return captions[name]
        args=[a.value if isinstance(a,ast.Constant) else None for a in n.args]
        if name=='ShowMenu' and args:
            return {'history':'기록','save':'저장','load':'불러오기','preferences':'설정'}.get(args[0])
        if name=='Preference' and len(args)>=2 and args[0]=='auto-forward':
            return {'enable':'자동','disable':'자동 끄기','toggle':'자동'}.get(args[1])
        if name=='If' and len(n.args)>=2:
            branches=[resolve(a) for a in n.args[1:]]
            labels={s for s in branches if s}
            if None not in branches and len(labels)==1:return labels.pop()
        return None
    return resolve(node) or None

def menu_translations(result):
    menu={}
    for source,uses in result['uses'].items():
        target=standard(source)
        # Only compact controls with confirmed built-in actions become text.
        # Never interpret a game's descriptive/custom caption from its action alone.
        compact=key(source) in ('Hist','Q.S','Q.L') or re.fullmatch(r'[ Aa▶◀■▼▲▷◁▸▹◂◃►◄|+\-<>]+',source)
        actions={use.get('action_text') for use in uses}
        if compact and len(actions)==1 and None not in actions:
            target=''.join(re.findall(r'\{#[^{}]*\}',source))+actions.pop()
        menu[source]=source if target is None else target
    return menu

def key(source):
    return re.sub(r'\{#[^{}]*\}', '', source)

def standard(source):
    clean=key(source)
    if clean in STANDARD:
        # Disambiguation tags carry no visible text, but preserve them for validators.
        return ''.join(re.findall(r'\{#[^{}]*\}',source))+STANDARD[clean]
    return None

def discover(scripts):
    screens={}; choices=set()
    for filename,text in scripts.items():
        screen=None; screen_indent=0; in_menu=False; menu_indent=0
        for number,line in enumerate(text.splitlines(),1):
            line=uncomment(line);stripped=line.strip()
            if not stripped or stripped.startswith('#'):continue
            indent=len(line)-len(line.lstrip())
            if screen and indent<=screen_indent:screen=None
            m=re.match(r'screen\s+(\w+)\s*\(',stripped)
            if m:screen=m[1];screen_indent=indent;in_menu=False
            if screen:
                screens.setdefault(screen,[]).append((filename,number,stripped,indent))
            if re.match(r'menu(?:\s|\(|:)',stripped) and not screen:
                in_menu=True;menu_indent=indent;continue
            if in_menu and indent<=menu_indent:in_menu=False
            if in_menu:
                m=LITERAL.match(stripped)
                if m and re.match(r'(?:\s+if\b.*?)?\s*(?:\(.*\))?\s*:',stripped[m.end():]):
                    choices.add(literal_eval(m.group()))
    menus=set(ROOTS)
    for name,lines in screens.items():
        if re.search(r'menu|prefs|settings',name,re.I):menus.add(name)
        for _,_,line,_ in lines:
            for m in re.finditer(r'ShowMenu\(\s*["\']([^"\']+)',line):menus.add(m[1])
    while True:
        added={m[1] for name in menus for _,_,line,_ in screens.get(name,[])
               if (m:=re.match(r'use\s+(\w+)',line))}-menus
        if not added:break
        menus.update(added)
    uses={}
    for name in sorted(menus-EXCLUDE):
        button=[];button_indent=-1
        for filename,number,line,indent in screens.get(name,[]):
            if indent<=button_indent:button=[]
            if button and re.match(r'action\s+',line):
                caption=action_caption(line)
                if caption:
                    for use in button:use['action_text']=caption
            line_uses=[]
            def add(source,translated):
                use=dict(file=filename,line=number,screen=name,translated=translated)
                uses.setdefault(source,[]).append(use);line_uses.append(use)
            # Also catch captions passed as arguments: game_menu(_("Save")),
            # FilePageNameInputValue(pattern=_("Page {}")), confirmation prompts.
            for translated in re.finditer(r'\b_\(\s*',line):
                literal=LITERAL.match(line,translated.end())
                if literal:
                    source=literal_eval(literal.group())
                    add(source,True)
            m=re.match(r'(?:textbutton|text|label)\s+(?:_\(\s*)?',line)
            if not m:continue
            q=LITERAL.match(line,m.end())
            if not q:continue
            if not re.match(r'(?:textbutton|text|label)\s+_\(',line):
                source=literal_eval(q.group())
                add(source,False)
            if line.startswith('textbutton '):
                button=line_uses;button_indent=indent
                caption=action_caption(line)
                if caption:
                    for use in button:use['action_text']=caption
    return {'uses':uses,'choices':sorted(choices)}

def policy(project):
    from automatic import script_sources
    scripts=script_sources(project)
    digest=hashlib.sha256(json.dumps([scripts,STANDARD],sort_keys=True).encode()).hexdigest()
    path=project/'data/ui-policy.json'
    if path.exists():
        old=json.loads(path.read_text(encoding='utf-8'))
        if old.get('digest')==digest and old.get('version')==4:return old
    result=discover(scripts);result.update(version=4,digest=digest)
    result['menu']=menu_translations(result)
    save_json(path,result)
    return result

def cached_policy(project):
    path=project/'data/ui-policy.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'menu':{},'choices':[]}


def apply_policy(project,rows,known,preserve_choices=False,cached_only=False):
    p=cached_policy(project) if cached_only else policy(project)
    for row in rows:
        if row['kind']!='string':continue
        source=row['source']
        if preserve_choices and source in p['choices']:continue
        target=p['menu'].get(source)
        if target is None and row.get('file','').endswith('common.rpy'):target=standard(source)
        if target is None:continue
        known[row['id']]=dict(known.get(row['id'],{}),id=row['id'],source=source,text=target,
                              status='ui_fixed' if target!=source else 'ui_original')
    return known
