# SPDX-License-Identifier: GPL-3.0-or-later
"""덧씌우기 — 원본을 안 건드리고 사전 + 짧은 후크를 얹는 길 (백로그 6).

**왜 이 길이 있어야 하는가.** 지금 기본으로 쓰는 길(A)은 게임 파일 안의
글자를 직접 바꿔 씁니다. 정확하지만, 파일 구조를 조금이라도 잘못 읽으면
그 파일이 통째로 못 쓰게 됩니다. 네 엔진 중 사고가 하나도 없었던 것은
Ren'Py 뿐인데, Ren'Py 만 이 방식입니다.

**어떻게 다른가.**

    A 방법   data/Map001.json 안의 일본어를 한국어로 **바꿔 씁니다**
    B 방법   원본은 그대로 두고, 게임이 그 글자를 화면에 그리려는 순간
             사전에서 찾아 바꿔치기합니다

되돌리기가 '넣은 파일을 지우는 것' 으로 끝나고, 사전에 없는 문장은 그냥
원문이 나옵니다. **A 를 대체하지 않습니다** — A 가 실패할 때의 두 번째
길입니다. 글자를 한 자씩 뿌리거나 이름을 끼워 넣는 게임은 이 길로도 안
됩니다.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

# 넣는 파일 이름. 배포 압축이 풀릴 때 깨지지 않게 **아스키만** 씁니다.
RM_PLUGIN = "GamelocKo.js"
RM_PLUGINS_JS = "js/plugins.js"
TY_HOOK = "gameloc_ko.js"

_MARK = "GamelocKo"          # 이미 넣었는지 알아보는 표시


# ---------------------------------------------------------------- 사전

def _dict_js(mapping: dict[str, str], var: str) -> str:
    """사전을 자바스크립트 한 줄로. 따로 불러오지 않아도 되게 **파일 안에**
    같이 넣습니다 — 게임에 따라 파일 읽기가 막혀 있어서, 불러오게 만들면
    조용히 실패합니다."""
    return f"var {var} = {json.dumps(mapping, ensure_ascii=False)};\n"


# ---------------------------------------------------------------- RPG Maker

_RM_HOOK = r"""
/*:
 * @target MZ
 * @plugindesc Gameloc 한국어 덧씌우기 (원본을 고치지 않습니다)
 * @help 이 파일은 Gameloc 이 넣었습니다. 지우면 원래 언어로 돌아갑니다.
 */
(function () {
  "use strict";
  var D = $gamelocKo || {};
  function tr(s) {
    return (typeof s === "string" && D[s] !== undefined) ? D[s] : s;
  }
  // 불러온 데이터를 훑어 글자만 바꿔치기합니다. 깊이를 막아 두는 것은
  // 서로를 가리키는 데이터에서 끝없이 도는 것을 막기 위해서입니다.
  function walk(o, depth) {
    if (!o || depth > 12 || typeof o !== "object") return;
    if (Array.isArray(o)) {
      for (var i = 0; i < o.length; i++) {
        if (typeof o[i] === "string") o[i] = tr(o[i]);
        else walk(o[i], depth + 1);
      }
      return;
    }
    for (var k in o) {
      if (!Object.prototype.hasOwnProperty.call(o, k)) continue;
      if (typeof o[k] === "string") o[k] = tr(o[k]);
      else walk(o[k], depth + 1);
    }
  }
  var onLoad = DataManager.onLoad;
  DataManager.onLoad = function (object) {
    onLoad.call(this, object);
    try { walk(object, 0); } catch (e) {}
  };
  // 데이터에 없이 실행 중에 만들어지는 글자를 받는 그물입니다.
  var conv = Window_Base.prototype.convertEscapeCharacters;
  Window_Base.prototype.convertEscapeCharacters = function (text) {
    return conv.call(this, tr(text));
  };
})();
"""


def rm_plugin_source(mapping: dict[str, str]) -> str:
    return _dict_js(mapping, "$gamelocKo") + _RM_HOOK


def rm_register(text: str, name: str = _MARK) -> str:
    """``js/plugins.js`` 의 목록 **맨 뒤**에 우리 것을 답니다.

    맨 뒤여야 합니다 — 다른 플러그인이 데이터를 손본 뒤에 우리가 바꿔야
    그 결과가 화면에 나옵니다. 이미 있으면 그대로 둡니다.

    ``name`` 에는 **확장자를 붙이지 않습니다.** RPG Maker 가 여기에 ``.js``
    를 붙여 ``js/plugins/<name>.js`` 를 불러오기 때문입니다. 붙여 두면
    ``GamelocKo.js.js`` 를 찾다가 조용히 실패합니다.
    """
    if _MARK in text:
        return text
    entry = ('{"name":"%s","status":true,"description":'
             '"Gameloc 한국어","parameters":{}}' % name)
    # 마지막 ']' 앞에 끼워 넣습니다. 뒤에 ';' 나 빈 줄이 있어도 됩니다.
    at = text.rfind("]")
    if at < 0:
        raise ValueError("plugins.js 에서 플러그인 목록을 찾지 못했습니다")
    head = text[:at].rstrip()
    comma = "" if head.endswith("[") else ","
    return f"{head}{comma}\n{entry}\n{text[at:]}"


def rm_unregister(text: str) -> str:
    """되돌리기용. 우리가 단 줄만 뺍니다."""
    out = [ln for ln in text.splitlines() if _MARK not in ln]
    # 우리 줄을 빼면서 생긴 ',]' 를 다듬습니다.
    body = "\n".join(out)
    return re.sub(r",(\s*\])", r"\1", body)


# ---------------------------------------------------------------- 티라노

_TY_HOOK = r"""
(function () {
  "use strict";
  var D = $gamelocKo || {};
  function tr(s) {
    return (typeof s === "string" && D[s] !== undefined) ? D[s] : s;
  }
  // 티라노는 화면에 무엇을 그리든 결국 태그 하나로 지나갑니다. 그 길목
  // 하나만 잡으면 대사·이름·선택지가 함께 걸립니다.
  var KEYS = ["val", "text", "name", "title", "body"];
  function hook() {
    if (!window.TYRANO || !TYRANO.kag || !TYRANO.kag.ftag) return false;
    var f = TYRANO.kag.ftag;
    if (f.__gameloc) return true;
    var start = f.startTag;
    f.startTag = function (name, pm) {
      try {
        if (pm) {
          for (var i = 0; i < KEYS.length; i++) {
            var k = KEYS[i];
            if (typeof pm[k] === "string") pm[k] = tr(pm[k]);
          }
        }
      } catch (e) {}
      return start.call(this, name, pm);
    };
    f.__gameloc = true;
    return true;
  }
  // 티라노가 준비되기 전에 우리가 먼저 실행될 수 있습니다. 1분까지 기다립니다.
  if (!hook()) {
    var n = 0;
    var t = setInterval(function () {
      if (hook() || ++n > 600) clearInterval(t);
    }, 100);
  }
})();
"""


def ty_hook_source(mapping: dict[str, str]) -> str:
    return _dict_js(mapping, "$gamelocKo") + _TY_HOOK


def ty_register(html: str, src: str = TY_HOOK) -> str:
    """``index.html`` 의 ``</body>`` 바로 앞에 우리 것을 답니다.

    맨 뒤여야 티라노가 이미 올라온 뒤에 우리가 걸립니다.
    """
    if _MARK in html or src in html:
        return html
    tag = f'<script src="./{src}"></script><!-- {_MARK} -->\n'
    low = html.lower()
    at = low.rfind("</body>")
    if at < 0:
        at = low.rfind("</html>")
    if at < 0:
        return html + "\n" + tag
    return html[:at] + tag + html[at:]


def ty_unregister(html: str) -> str:
    return "\n".join(ln for ln in html.splitlines() if _MARK not in ln)


# ---------------------------------------------------------------- 어디에 넣나

def rm_targets(root: Path) -> tuple[Path, Path]:
    """``(플러그인 파일, plugins.js)``. 게임 폴더 모양을 보고 찾습니다."""
    for base in (root, root / "www"):
        js = base / "js"
        if (js / "plugins.js").is_file():
            return js / "plugins" / RM_PLUGIN, js / "plugins.js"
    raise FileNotFoundError("js/plugins.js 를 찾지 못했습니다")


def ty_targets(root: Path) -> tuple[Path, Path]:
    """``(후크 파일, index.html)``."""
    for base in (root, root / "www", root / "resources" / "app"):
        page = base / "index.html"
        if page.is_file():
            return base / TY_HOOK, page
    raise FileNotFoundError("index.html 을 찾지 못했습니다")
