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
        this._rptTranslation = null;
        this._rptChoiceOriginals = null;
    };
    var command101 = Game_Interpreter.prototype.command101;
    Game_Interpreter.prototype.command101 = function () {
        var next = !$gameMessage.isBusy() && this._list && this._list[this._index + 1];
        var translated = next && next.code === 401 && next._rpt_translation;
        var original = next && next.code === 401 && next._rpt_original;
        if (!$gameMessage.isBusy()) {
            $gameMessage._rptOriginal = original || null;
        }
        var result = command101.apply(this, arguments);
        if (typeof translated === 'string') {
            // Native add() hooks have already run. Do not translate our final text again.
            $gameMessage._texts = [translated];
            $gameMessage._rptTranslation = translated;
            $gameMessage._rptOriginal = original || null;
        }
        return result;
    };
    var command105 = Game_Interpreter.prototype.command105;
    if (command105) Game_Interpreter.prototype.command105 = function () {
        var next = !$gameMessage.isBusy() && this._list && this._list[this._index + 1];
        var translated = next && next.code === 405 && next._rpt_translation;
        var original = next && next.code === 405 && next._rpt_original;
        if (!$gameMessage.isBusy()) {
            $gameMessage._rptOriginal = original || null;
        }
        var result = command105.apply(this, arguments);
        if (typeof translated === 'string') {
            $gameMessage._texts = [translated];
            $gameMessage._rptTranslation = translated;
            $gameMessage._rptOriginal = original || null;
        }
        return result;
    };
    var setupChoices = Game_Interpreter.prototype.setupChoices;
    if (setupChoices) Game_Interpreter.prototype.setupChoices = function () {
        var command = this._list && this._list[this._index];
        setupChoices.apply(this, arguments);
        var translated = command && command._rpt_choice_translations;
        if (Array.isArray(translated) && translated.length === $gameMessage._choices.length &&
            translated.every(function (text) { return typeof text === 'string'; })) {
            $gameMessage._choices = translated.slice();
        }
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
    var fontProperties = ['fontFace','fontSize','fontBold','fontItalic','textColor','outlineColor','outlineWidth'];
    function copyFont(from, to) {
        fontProperties.forEach(function (name) { if (from[name] !== undefined) to[name] = from[name]; });
    }
    function fontKey(bitmap) {
        return fontProperties.map(function (name) { return String(bitmap[name]); }).join('|');
    }
    var baseTextWidth = Window_Base.prototype.textWidth;
    // Some font managers select a face inside Bitmap.drawText, after textWidth.
    // Probe only that plain-text drawing path; never execute message escape codes.
    function drawWidth(window, text) {
        var probe = window._rptFontProbe || (window._rptFontProbe = new Bitmap(1, 1));
        copyFont(window.contents, probe);
        var used = {}, make = probe._makeFontNameText;
        var own = Object.prototype.hasOwnProperty.call(probe, '_makeFontNameText');
        if (make && probe.measureTextWidth) {
            probe._makeFontNameText = function () { copyFont(this, used); return make.apply(this, arguments); };
            try { probe.drawText(text, 0, 0, 1048576, Math.max(1, probe.fontSize * 2)); }
            finally { if (own) probe._makeFontNameText = make; else delete probe._makeFontNameText; }
            copyFont(used, probe);
            return probe.measureTextWidth(text);
        }
        return baseTextWidth.call(window, text);
    }
    // Reference/scroll text has no message waits or native message auto-wrap.
    function wrap(window, text, width, indent) {
        indent = indent || 0;
        var tokens = String(text).match(/\n|[^\S\n]+|[^\s]+/g) || [];
        var lines = [''];
        tokens.forEach(function (token) {
            if (token === '\n') { lines.push(''); return; }
            var last = lines.length - 1;
            if (/^\s+$/.test(token)) { if (lines[last]) lines[last] += token; return; }
            if (lines[last] && drawWidth(window, lines[last] + token) > width) {
                lines[last] = lines[last].replace(/\s+$/, ''); lines.push(''); last++;
            }
            if (drawWidth(window, token) <= width) { lines[last] += token; return; }
            Array.from(token).forEach(function (character) {
                var index = lines.length - 1;
                if (lines[index] && drawWidth(window, lines[index] + character) > width) lines.push('');
                lines[lines.length - 1] += character;
            });
        });
        return lines;
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
        return this._rptLayout ? this._rptLayout.bodyLimit : contentsHeight.apply(this, arguments);
    };
    function syncReference(window) {
        var layout = window._rptLayout;
        if (!layout) return;
        var top = Math.min(layout.bodyLimit, Math.max(window.lineHeight(), layout.bottom)) + 6;
        var height = Math.min(layout.maxHeight, Math.max(layout.baseHeight, top + layout.referenceHeight + 2 * window.padding));
        if (window.height !== height) {
            window.height = height;
            if (window.updatePlacement) window.updatePlacement();
            if (window._nameBoxWindow && window._nameBoxWindow.updatePlacement) window._nameBoxWindow.updatePlacement();
        }
        if (layout.referenceHeight) {
            window._rptPane.y = window.padding + top;
            window._rptPane.visible = window.openness === 255;
        }
    }
    function painted(window, y, height) {
        if (window._rptLayout) {
            window._rptLayout.bottom = Math.max(window._rptLayout.bottom, y + height);
            syncReference(window);
        }
    }
    function bodyBitmap(window) {
        var old = window.contents, limit = window._rptLayout.bodyLimit;
        if (old.height !== limit) {
            var bitmap = new Bitmap(old.width, limit);
            copyFont(old, bitmap);
            bitmap.blt(old, 0, 0, old.width, Math.min(old.height, limit), 0, 0);
            window.contents = bitmap;
            if (old.destroy) old.destroy();
        }
        var contents = window.contents;
        if (contents._rptOwner === window) return;
        var draw = contents.drawText;
        contents._rptOwner = window;
        contents.drawText = function (text, x, y, width, height) {
            var result = draw.apply(this, arguments);
            if (this === window.contents && String(text).trim()) painted(window, y, height);
            return result;
        };
    }
    var messageTextWidth = Window_Message.prototype.textWidth;
    Window_Message.prototype.textWidth = function (text) {
        return this._rptLayout || this._rptMeasuring ? drawWidth(this, text) : messageTextWidth.apply(this, arguments);
    };
    var character = Window_Message.prototype.processCharacter;
    var flush = Window_Message.prototype.flushTextState;
    function runWidth(window, text, rtl) {
        // MV draws characters directly. Observe its real extra advance rather
        // than replaying character callbacks (which can play sounds or voices).
        if (!flush) return drawWidth(window, text) + (window._rptAdvanceExtra || 0) * Array.from(text).length;
        var key = fontKey(window.contents) + '|' + window.contents.width + '|' + !!rtl + '|' + text;
        var cache = window._rptMeasureCache;
        if (cache && cache[key] !== undefined) return cache[key];
        var bitmap = window._rptLayoutProbe;
        if (!bitmap || bitmap.width !== window.contents.width) {
            if (bitmap && bitmap.destroy) bitmap.destroy();
            bitmap = window._rptLayoutProbe = new Bitmap(window.contents.width, 1);
        }
        var widest = 0;
        // Flush both per-character and per-word: native instant/reveal modes can
        // use different spacing. Reserve enough width for either live path.
        [true, false].forEach(function (each) {
            copyFont(window.contents, bitmap);
            var probe = Object.create(window);
            // Window.contents is an accessor backed by a sprite. Shadow it so
            // a plain-text probe can never replace the live window's bitmap.
            Object.defineProperty(probe, 'contents', {value:bitmap,writable:true,configurable:true});
            probe._rptLayout = null; probe._rptMeasuring = true;
            probe.processNewLine = function (state) {
                state.x = state.startX; state.y += state.height;
                state.height = this.calcTextHeight(state, false);
            };
            probe.startPause = function () {};
            probe.startWait = function () {};
            var origin = rtl ? bitmap.width : 0;
            var state = {text:text,index:0,x:origin,y:0,startX:origin,startY:0,width:bitmap.width,
                         height:window.lineHeight(),buffer:'',drawing:true,rtl:!!rtl,outputWidth:0,outputHeight:0};
            if (each) {
                Array.from(text).some(function (unit) {
                    state.index += unit.length;
                    state.buffer += unit;
                    flush.call(probe, state);
                    return !!state.y;
                });
            } else {
                state.index = text.length;
                state.buffer = text;
                flush.call(probe, state);
            }
            widest = Math.max(widest, state.y ? Infinity : Math.abs(state.x - origin));
        });
        if (!cache || Object.keys(cache).length > 256) cache = window._rptMeasureCache = Object.create(null);
        cache[key] = widest;
        return widest;
    }
    function prepareCharacter(window, state) {
        var c = state.text[state.index];
        if (!c || c.charCodeAt(0) < 0x20 || /\s/.test(c)) return true;
        var code = c.charCodeAt(0);
        if (code >= 0xdc00 && code <= 0xdfff && state.index && /[\ud800-\udbff]/.test(state.text[state.index - 1])) return true;
        var first = state._rptWordEnd === undefined || state.index >= state._rptWordEnd;
        if (flush && state.buffer && (first || state._rptLongWord)) flush.call(window, state);
        var start = state.startX === undefined ? (window.newLineX ? window.newLineX(state) : 0) : state.startX;
        var remaining = state.rtl ? state.x : window.contents.width - state.x;
        var available = state.rtl ? start : window.contents.width - start;
        var wrapHere = false;
        if (first) {
            var word = /^[^\s\x00-\x1f]+/.exec(state.text.slice(state.index))[0];
            var width = runWidth(window, word, state.rtl);
            state._rptWordEnd = state.index + word.length;
            state._rptLongWord = width > available;
            wrapHere = !state._rptLongWord && width > remaining;
        }
        if (state._rptLongWord) {
            var unit = code >= 0xd800 && code <= 0xdbff ? state.text.slice(state.index, state.index + 2) : c;
            wrapHere = runWidth(window, unit, state.rtl) > remaining;
        }
        if (wrapHere && (state.rtl ? state.x < start : state.x > start)) {
            window.processNewLine(state);
            return !window.needsNewPage || !window.needsNewPage(state);
        }
        return true;
    }
    if (character) Window_Message.prototype.processCharacter = function (state) {
        if (this._rptLayout && !prepareCharacter(this, state)) return;
        var observe = this._rptLayout && !flush && state.text[state.index] && state.text.charCodeAt(state.index) >= 0x20;
        var x = state.x, y = state.y, index = state.index;
        var result = character.apply(this, arguments);
        if (observe && state.y === y && state.index > index) {
            var width = drawWidth(this, state.text.slice(index, state.index));
            this._rptAdvanceExtra = Math.max(this._rptAdvanceExtra || 0, Math.abs(state.x - x) - width);
        }
        return result;
    };
    var drawIcon = Window_Message.prototype.drawIcon;
    if (drawIcon) Window_Message.prototype.drawIcon = function (index, x, y) {
        var result = drawIcon.apply(this, arguments);
        painted(this, y, ImageManager.iconHeight || Window_Base._iconHeight || 32);
        return result;
    };
    var newPage = Window_Message.prototype.newPage;
    if (newPage) Window_Message.prototype.newPage = function () {
        if (this._rptLayout) this._rptLayout.bottom = 0;
        var result = newPage.apply(this, arguments);
        if (this._rptLayout) { bodyBitmap(this); syncReference(this); }
        return result;
    };
    var startMessage = Window_Message.prototype.startMessage;
    Window_Message.prototype.startMessage = function () {
        if (this._rptBaseHeight === undefined) this._rptBaseHeight = this.height;
        var original = $gameMessage._rptOriginal;
        var ref = pane(this);
        ref.visible = false;
        this._rptLayout = null;
        this._rptMeasureCache = null;
        this.height = this._rptBaseHeight;
        this.createContents();
        startMessage.apply(this, arguments);
        if (!this._textState || (!original && typeof $gameMessage._rptTranslation !== 'string')) return;
        var state = this._textState;
        var repaired = original && repairPositionBreaks(original, $gameMessage.allText());
        if (repaired && repaired !== $gameMessage.allText()) state.text = this.convertEscapeCharacters(repaired);
        var referenceSize = Math.max(1, Math.round(this.contents.fontSize * options.referenceRatio));
        var indent = state.startX === undefined ? (this.newLineX ? this.newLineX(state) : 0) : state.startX;
        var width = Math.max(80, this.contents.width - indent);
        var maxHeight = Math.max(this.lineHeight() + 2 * this.padding, Math.floor(Graphics.boxHeight * .85));
        var maxBody = maxHeight - 2 * this.padding;
        var referenceHeight = 0;
        if (original) {
            ref.move(this.padding + indent, this.padding, width, Math.ceil(maxBody * .3));
            ref.createContents();
            ref.resetFontSettings();
            ref.contents.fontSize = referenceSize;
            ref.contents.textColor = '#c7cbd3';
            var referenceLineHeight = Math.ceil(referenceSize * 1.25);
            ref.lineHeight = function () { return referenceLineHeight; };
            var lines = wrap(ref, originalText(this, original), width);
            referenceHeight = Math.min(lines.length * referenceLineHeight + 8, Math.floor(maxBody * .3));
            ref.move(this.padding + indent, this.padding, width, referenceHeight);
            ref.createContents(); ref.resetFontSettings();
            ref.contents.fontSize = referenceSize; ref.contents.textColor = '#c7cbd3';
            var count = Math.max(1, Math.floor((referenceHeight - 8) / referenceLineHeight));
            lines.slice(0, count).forEach(function (line, i) {
                if (i === count - 1 && lines.length > count) {
                    while (line.length && drawWidth(ref, line + '…') > width) line = line.slice(0, -1);
                    line += '…';
                }
                ref.drawText(line, 0, 4 + i * referenceLineHeight, width, 'left');
            });
        }
        this._rptLayout = {bottom:0,referenceHeight:referenceHeight,maxHeight:maxHeight,
            bodyLimit:Math.max(this.lineHeight(), maxBody - referenceHeight - 6),
            baseHeight:Math.min(maxHeight, Math.round((this._rptBaseHeight - 2*this.padding) * options.textboxScale) + 2*this.padding)};
        bodyBitmap(this);
        syncReference(this);
    };
    var update = Window_Message.prototype.update;
    Window_Message.prototype.update = function () {
        update.apply(this, arguments);
        if (this._rptPane) this._rptPane.visible = !!(this._rptLayout && this._rptLayout.referenceHeight) && this.openness === 255;
    };
    var terminate = Window_Message.prototype.terminateMessage;
    Window_Message.prototype.terminateMessage = function () {
        if (this._rptPane) this._rptPane.visible = false;
        this._rptLayout = null;
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
