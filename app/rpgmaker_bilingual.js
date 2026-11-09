/* Default RPG Maker MV/MZ message presentation. No model or network in-game. */
(function () {
    'use strict';
    var options = RPT_BilingualOptions;
    var mz = Utils.RPGMAKER_NAME === 'MZ';
    var font = 'RPT Korean';
    if (mz && typeof FontManager !== 'undefined') FontManager.load(font, 'RPT-NanumSquareNeo.ttf');
    else if (Graphics.loadFont) Graphics.loadFont(font, 'fonts/RPT-NanumSquareNeo.ttf');
    if (!mz && typeof Scene_Boot !== 'undefined' && Graphics.isFontLoaded) {
        var bootReady = Scene_Boot.prototype.isReady;
        Scene_Boot.prototype.isReady = function () {
            return bootReady.apply(this, arguments) && Graphics.isFontLoaded(font);
        };
    }
    if (mz && Game_System.prototype.mainFontFace) {
        var mainFont = Game_System.prototype.mainFontFace;
        Game_System.prototype.mainFontFace = function () { return font + ', ' + mainFont.apply(this, arguments); };
    } else {
        var standardFont = Window_Base.prototype.standardFontFace;
        Window_Base.prototype.standardFontFace = function () { return font + ', ' + standardFont.apply(this, arguments); };
    }
    var clear = Game_Message.prototype.clear;
    Game_Message.prototype.clear = function () {
        clear.apply(this, arguments);
        this._rptOriginal = null;
        this._rptChoiceOriginals = null;
    };
    var command101 = Game_Interpreter.prototype.command101;
    Game_Interpreter.prototype.command101 = function () {
        if (!$gameMessage.isBusy()) {
            var next = this._list && this._list[this._index + 1];
            $gameMessage._rptOriginal = next && next.code === 401 ? next._rpt_original || null : null;
        }
        return command101.apply(this, arguments);
    };
    var command105 = Game_Interpreter.prototype.command105;
    if (command105) Game_Interpreter.prototype.command105 = function () {
        if (!$gameMessage.isBusy()) {
            var next = this._list && this._list[this._index + 1];
            $gameMessage._rptOriginal = next && next.code === 405 ? next._rpt_original || null : null;
        }
        return command105.apply(this, arguments);
    };
    var setupChoices = Game_Interpreter.prototype.setupChoices;
    if (setupChoices) Game_Interpreter.prototype.setupChoices = function () {
        var command = this._list && this._list[this._index];
        setupChoices.apply(this, arguments);
        $gameMessage._rptChoiceOriginals = command && command.code === 102 ? command._rpt_choice_originals || null : null;
    };
    // Recover a missing layout newline by its position-code occurrence, not text length.
    function repairPositionBreaks(original, translated) {
        var pattern = /\\(?:PX|PY)\[-?\d+\]/gi;
        var a = [], b = [], match;
        while ((match = pattern.exec(original))) a.push({code:match[0].toUpperCase(),index:match.index});
        pattern.lastIndex = 0;
        while ((match = pattern.exec(translated))) b.push({code:match[0].toUpperCase(),index:match.index});
        if (a.length !== b.length || a.some(function (item,i) { return item.code !== b[i].code; })) return translated;
        for (var i = a.length - 1; i >= 0; i--) {
            var boundary = /\r?\n[ \t]*((?:\\(?:(?:FS|C)\[-?\d+\]|[.!|^><${}]))*)$/i.exec(original.slice(0,a[i].index));
            if (!boundary) continue;
            var prefix=boundary[1],index=b[i].index-prefix.length;
            if (index>=0 && translated.slice(index,b[i].index).toUpperCase()===prefix.toUpperCase() &&
                !/\r?\n[ \t]*$/.test(translated.slice(0,index))) {
                translated = translated.slice(0,index) + '\n' + translated.slice(index);
            }
        }
        return translated;
    }
    // Font and horizontal coordinates affect wrapping; escapes are kept intact.
    function wrap(window, text, width, indent) {
        indent = indent || 0;
        var tokens = String(text).match(/\x1b[A-Za-z]+\[[^\]]*\]|\x1b[A-Za-z]+|\x1b[.!|^><${}\\]|\n|[^]/g) || [];
        var lines = [''], x = indent, fontSize = window.contents.fontSize;
        tokens.forEach(function (token) {
            if (token === '\n') { lines.push(''); x = indent; return; }
            var coordinate = /^\x1bPX\[(-?\d+)\]/i.exec(token);
            if (coordinate) x = Number(coordinate[1]);
            var size = /^\x1bFS\[(\d+)\]/i.exec(token);
            if (size) window.contents.fontSize = Number(size[1]);
            if (token === '\x1b{' && window.makeFontBigger) window.makeFontBigger();
            if (token === '\x1b}' && window.makeFontSmaller) window.makeFontSmaller();
            var cost = token.charAt(0) === '\x1b' ? (/^\x1bI\[/i.test(token) ? 36 : 0) : window.textWidth(token);
            if (cost && x > indent && x + cost > indent + width) { lines.push(''); x = indent; }
            lines[lines.length - 1] += token;
            x += cost;
        });
        window.contents.fontSize = fontSize;
        return lines;
    }
    function displayedBottom(window, escaped, indent) {
        var size = window.contents.fontSize;
        // MZ's Window_Base measurement handles PX/PY/FS/icons without message waits.
        if (window.createTextState && window.processAllText && window.flushTextState) {
            var state = window.createTextState('',indent,0,0);
            state.text = escaped; state.height = window.calcTextHeight(state); state.drawing = false;
            var flush = window.flushTextState, own = Object.prototype.hasOwnProperty.call(window,'flushTextState');
            var bottom = 0;
            window.flushTextState = function (s) {
                if (s.buffer) bottom = Math.max(bottom,s.y+s.height);
                return flush.call(this,s);
            };
            try { window.processAllText(state); }
            finally {
                if (own) window.flushTextState = flush;
                else delete window.flushTextState;
                window.contents.fontSize = size;
            }
            return Math.max(window.lineHeight(),bottom);
        }
        // MV has no processAllText API. Track the same standard layout properties.
        var y = 0, bottomMV = 0, height = window.lineHeight();
        var tokens = String(escaped).match(/\x1b[A-Za-z]+\[[^\]]*\]|\x1b[A-Za-z]+|\x1b[.!|^><${}\\]|\n|[^]/g) || [];
        tokens.forEach(function (token) {
            if (token === '\n') { y += height; height = window.lineHeight(); return; }
            var py = /^\x1bPY\[(-?\d+)\]/i.exec(token);
            if (py) y = Number(py[1]);
            var fs = /^\x1bFS\[(\d+)\]/i.exec(token);
            if (fs) window.contents.fontSize = Number(fs[1]);
            if (token === '\x1b{' && window.makeFontBigger) window.makeFontBigger();
            if (token === '\x1b}' && window.makeFontSmaller) window.makeFontSmaller();
            height = Math.max(height,window.contents.fontSize+8);
            if (token.charAt(0) !== '\x1b' || /^\x1bI\[/i.test(token)) bottomMV = Math.max(bottomMV,y+height);
        });
        window.contents.fontSize = size;
        return Math.max(window.lineHeight(),bottomMV);
    }
    function originalText(window, text) {
        // A Korean actor label must not leak into the original reference.
        text = text.replace(/\\N\[(\d+)\]/gi, function (m, id) { return options.originalActors[id] || m; });
        text = text.replace(/\\P\[(\d+)\]/gi, function (m, id) {
            var actor = $gameParty.members()[Number(id) - 1];
            return actor && options.originalActors[actor.actorId()] || m;
        });
        return window.convertEscapeCharacters(text)
            .replace(/\x1b[A-Za-z]+\[[^\]]*\]|\x1b[A-Za-z]+|\x1b[.!|^><${}\\]/g, '');
    }
    function pane(owner) {
        if (!owner._rptPane) {
            owner._rptPane = mz ? new Window_Base(new Rectangle(0, 0, 100, 100)) : new Window_Base(0, 0, 100, 100);
            owner._rptPane.opacity = 0;
            owner._rptPane.backOpacity = 0;
            owner._rptPane.padding = 0;
            owner.addChild(owner._rptPane);
        }
        return owner._rptPane;
    }
    var contentsHeight = Window_Message.prototype.contentsHeight;
    Window_Message.prototype.contentsHeight = function () {
        return Math.max(1, contentsHeight.apply(this, arguments) - (this._rptReferenceHeight || 0));
    };
    var startMessage = Window_Message.prototype.startMessage;
    Window_Message.prototype.startMessage = function () {
        if (this._rptBaseHeight === undefined) this._rptBaseHeight = this.height;
        var original = $gameMessage._rptOriginal;
        var ref = pane(this);
        ref.visible = false;
        this._rptReferenceHeight = 0;
        this.height = this._rptBaseHeight;
        this.createContents();
        this.resetFontSettings();
        if (original) {
            var koreanSize = this.contents.fontSize;
            var referenceSize = Math.max(1, Math.round(koreanSize * options.referenceRatio));
            // MZ reads textState.rtl; MV ignores the optional argument.
            var measureState = mz && this.createTextState ?
                this.createTextState($gameMessage.allText(), 0, 0, 0) : {rtl: false};
            var indent = this.newLineX ? this.newLineX(measureState) : 0;
            var width = Math.max(80, this.contents.width - indent);
            var korean = this.convertEscapeCharacters(repairPositionBreaks(original,$gameMessage.allText()));
            var koreanLines = wrap(this, korean, width, indent);
            var koreanLineHeight = this.lineHeight();
            var maxBody = Math.max(koreanLineHeight, Math.floor(Graphics.boxHeight * 0.85) - 2 * this.padding);
            ref.contents.fontFace = this.contents.fontFace;
            ref.contents.fontSize = koreanSize;
            ref.lineHeight = this.lineHeight.bind(this);
            var koreanHeight = Math.min(displayedBottom(ref,koreanLines.join('\n'),indent)+6, Math.floor(maxBody * 0.7));
            ref.contents.fontSize = referenceSize;
            var english = originalText(this, original);
            var englishLines = wrap(ref, english, width);
            var referenceLineHeight = Math.ceil(referenceSize * 1.25);
            ref.lineHeight = function () { return referenceLineHeight; };
            var referenceHeight = Math.min(englishLines.length * referenceLineHeight + 8, maxBody - koreanHeight);
            this._rptReferenceHeight = referenceHeight;
            var baseHeight = Math.round((this._rptBaseHeight - 2*this.padding) * options.textboxScale) + 2*this.padding;
            this.height = Math.max(Math.min(baseHeight,maxBody+2*this.padding),koreanHeight + referenceHeight + 2 * this.padding);
            this.createContents();
            ref.move(this.padding + indent, this.padding + koreanHeight, width, referenceHeight);
            ref.createContents();
            ref.resetFontSettings();
            ref.contents.fontSize = referenceSize;
            ref.contents.textColor = '#c7cbd3';
            var count = Math.max(1, Math.floor((referenceHeight - 8) / referenceLineHeight));
            englishLines.slice(0, count).forEach(function (line, i) {
                if (i === count - 1 && englishLines.length > count) {
                    while (line.length && ref.textWidth(line + '…') > width) line = line.slice(0, -1);
                    line += '…';
                }
                ref.drawText(line, 0, 4 + i * referenceLineHeight, width, 'left');
            });
            ref.visible = true;
        }
        startMessage.apply(this, arguments);
        if (original && this._textState) {
            this._textState.text = koreanLines.join('\n');
            this._textState.height = this.calcTextHeight(this._textState, false);
        }
    };
    var update = Window_Message.prototype.update;
    Window_Message.prototype.update = function () {
        update.apply(this, arguments);
        if (this._rptPane) this._rptPane.visible = !!$gameMessage._rptOriginal && this.openness === 255;
    };
    var terminate = Window_Message.prototype.terminateMessage;
    Window_Message.prototype.terminateMessage = function () {
        if (this._rptPane) this._rptPane.visible = false;
        terminate.apply(this, arguments);
    };
    if (typeof Window_ChoiceList !== 'undefined') {
        var choiceLineHeight = Window_ChoiceList.prototype.lineHeight;
        var choiceItemHeight = Window_ChoiceList.prototype.itemHeight;
        var choiceRows = Window_ChoiceList.prototype.numVisibleRows;
        var drawChoice = Window_ChoiceList.prototype.drawItem;
        Window_ChoiceList.prototype.lineHeight = function () {
            var base = choiceLineHeight.apply(this, arguments);
            var size = this.standardFontSize ? this.standardFontSize() :
                (typeof $gameSystem !== 'undefined' && $gameSystem.mainFontSize ? $gameSystem.mainFontSize() : 26);
            return $gameMessage._rptChoiceOriginals ? base + Math.ceil(size * options.referenceRatio * 1.25) + 4 : base;
        };
        Window_ChoiceList.prototype.itemHeight = function () {
            return $gameMessage._rptChoiceOriginals ? this.lineHeight() : choiceItemHeight.apply(this, arguments);
        };
        Window_ChoiceList.prototype.numVisibleRows = function () {
            var rows = choiceRows.apply(this, arguments);
            return $gameMessage._rptChoiceOriginals ? Math.max(1, Math.min(rows, Math.floor(Graphics.boxHeight * 0.45 / this.itemHeight()))) : rows;
        };
        Window_ChoiceList.prototype.drawItem = function (index) {
            drawChoice.apply(this, arguments);
            var originals = $gameMessage._rptChoiceOriginals;
            if (!originals || !originals[index]) return;
            var rect = this.itemRectForText ? this.itemRectForText(index) : this.itemLineRect(index);
            var size = this.contents.fontSize, color = this.contents.textColor;
            var referenceSize = Math.max(1, Math.round(size * options.referenceRatio));
            this.contents.fontSize = referenceSize; this.contents.textColor = '#c7cbd3';
            var text = originalText(this, originals[index]).replace(/\n/g, ' ');
            var full = text;
            while (text.length && this.textWidth(text + (text === full ? '' : '…')) > rect.width) text = text.slice(0, -1);
            this.contents.drawText(text + (text === full ? '' : '…'), rect.x, rect.y + choiceLineHeight.call(this) + 2,
                rect.width, Math.ceil(referenceSize * 1.25), 'left');
            this.contents.fontSize = size; this.contents.textColor = color;
        };
    }
    if (typeof Window_ScrollText !== 'undefined') {
        var scrollStart = Window_ScrollText.prototype.startMessage;
        Window_ScrollText.prototype.startMessage = function () {
            scrollStart.apply(this, arguments);
            if (!$gameMessage._rptOriginal) return;
            var old = this.contents;
            var referenceSize = Math.max(1, Math.round(old.fontSize * options.referenceRatio));
            var savedSize = old.fontSize;
            old.fontSize = referenceSize;
            var lines = wrap(this, originalText(this, $gameMessage._rptOriginal), Math.max(80, old.width - 8));
            old.fontSize = savedSize;
            var lineHeight = Math.ceil(referenceSize * 1.25);
            var bitmap = new Bitmap(old.width, old.height + lines.length * lineHeight + 12);
            bitmap.blt(old, 0, 0, old.width, old.height, 0, 0);
            bitmap.fontFace = old.fontFace; bitmap.fontSize = referenceSize; bitmap.textColor = '#c7cbd3';
            lines.forEach(function (line, i) { bitmap.drawText(line, 4, old.height + 8 + i * lineHeight, old.width - 8, lineHeight, 'left'); });
            this.contents = bitmap;
            if (typeof this._textHeight === 'number') this._textHeight = bitmap.height;
            if (typeof this._allTextHeight === 'number') this._allTextHeight = bitmap.height;
        };
    }
    // Metadata remains on commands; choices, switches and event indices are unchanged.
})();
