"""Explicit source-language policies; Korean is the only output language."""
import re

LANGUAGES = {'english': '영어', 'japanese': '일본어'}
ALIASES = {'en': 'english', 'ja': 'japanese'}


def normalize(value=None):
    value = ALIASES.get(value, value) or 'english'
    if value not in LANGUAGES:
        raise ValueError('원문 언어는 영어 또는 일본어를 선택하세요.')
    return value


def letters(text):
    return any(c.isalpha() for c in text)


def instruction(cfg):
    language = normalize(cfg.get('source_language'))
    return ('원문 언어: '+LANGUAGES[language]+'. 출력 언어: 한국어. '
            '원문에 다른 언어가 섞여 있어도 함께 한국어로 번역하세요.\n')


def apply(project,cfg,value=None):
    from engine import save_json
    result=dict(cfg,source_language=normalize(value if value is not None else cfg.get('source_language')))
    if result!=cfg:save_json(project/'project.json',result)
    return result


def source_units(text):
    # Preserve the established English calculation; unspaced scripts also count.
    non_latin = sum(c.isalpha() and ord(c) > 0x2ff for c in text)
    return max(len(text.split()), (non_latin + 1)//2)


def occurrences(text, term):
    # Japanese particles attach without whitespace. Latin names still need bounds.
    if any(c.isalpha() and ord(c) > 0x2ff for c in term):
        return list(re.finditer(re.escape(term), text))
    return list(re.finditer(r'(?<![\w])'+re.escape(term)+r'(?![A-Za-z])', text))


JAPANESE_MENUS = {
    'スタート':'시작', '開始':'시작', 'ゲーム開始':'새 게임', 'ニューゲーム':'새 게임',
    '新しいゲーム':'새 게임', 'チャプター選択':'챕터 선택', '章選択':'챕터 선택',
    '閉じる':'닫기', 'クローズ':'닫기', 'ロード':'불러오기', 'セーブ':'저장',
    'クイックセーブ':'빠른 저장', 'クイックロード':'빠른 불러오기',
    '履歴':'대화 기록', 'バックログ':'대화 기록', '戻る':'돌아가기',
    'スキップ':'건너뛰기', 'オート':'자동', '設定':'설정', '環境設定':'설정',
    'メインメニュー':'메인 메뉴', 'タイトルへ戻る':'메인 메뉴', '終了':'종료',
    'ギャラリー':'갤러리', 'リプレイ':'다시보기', '実績':'업적',
    'はい':'예', 'いいえ':'아니요', 'キャンセル':'취소', '確認':'확인',
    'ヘルプ':'도움말', 'ウィンドウ':'창 모드', 'フルスクリーン':'전체 화면',
    '文字速度':'텍스트 속도', 'テキスト速度':'텍스트 속도',
    '音楽音量':'음악 음량', '効果音音量':'효과음 음량', 'ボイス音量':'음성 음량',
}
