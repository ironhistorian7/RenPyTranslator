// MV/MZ public API contracts in memory. No game, network, GPU or graphics window.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const plugin = fs.readFileSync(__dirname + '/rpgmaker_bilingual.js', 'utf8');
const argument = key => { const i=process.argv.indexOf(key);return i<0?null:process.argv[i+1]; };
const engineWindows=argument('--engine-windows');

for (const variant of ['MV', 'MZ']) {
    class Bitmap {
        constructor(width, height) { Object.assign(this, {width, height, fontSize:28, lines:[]}); }
        drawText(text,x,y,width,height) { this.lines.push({text,x,y,width,height,size:this.fontSize}); }
        blt(old) { this.lines = old.lines.slice(); }
    }
    class WindowBase {
        constructor(x,y,width,height) {
            if (typeof x === 'object') ({x,y,width,height}=x);
            Object.assign(this,{x,y,width,height,padding:18,openness:255});this.createContents();
        }
        contentsHeight() { return this.height-2*this.padding; }
        createContents() { this.contents=new Bitmap(this.width-2*this.padding,this.contentsHeight()); }
        resetFontSettings() { this.contents.fontSize=28;this.contents.fontFace=this.standardFontFace(); }
        standardFontFace() { return 'GameFont'; }
        standardFontSize() { return 28; }
        lineHeight() { return 36; }
        textWidth(text) { return text.length*this.contents.fontSize*.5; }
        drawText(text,x,y,width) { this.contents.drawText(text,x,y,width,this.lineHeight()); }
        move(x,y,width,height) { Object.assign(this,{x,y,width,height}); }
        convertEscapeCharacters(text) {
            return text.replace(/\\/g,'\x1b').replace(/\x1bV\[2\]/gi,'100').replace(/\x1bN\[1\]/gi,'앨리스');
        }
        addChild(child) { (this.children ||= []).push(child); }
        calcTextHeight() { return 36; }
        changeTextColor(color) { this.contents.textColor=color; }
        drawIcon() {}
    }
    class GameMessage {
        constructor() { this.clear(); }
        clear() { this._texts=[];this._choices=[]; }
        isBusy() { return this._texts.length>0; }
        allText() { return this._texts.join('\n'); }
    }
    const message = new GameMessage();
    class Interpreter {
        command101() {
            if(message.isBusy())return false;
            while(this._list[this._index+1]?.code===401)message._texts.push(this._list[++this._index].parameters[0]);
            if(this._list[this._index+1]?.code===102)this.setupChoices(this._list[++this._index].parameters);
            return false;
        }
        command105() {
            if(message.isBusy())return false;
            while(this._list[this._index+1]?.code===405)message._texts.push(this._list[++this._index].parameters[0]);
            return false;
        }
        setupChoices(params) { message._choices=params[0].slice(); }
    }
    class WindowMessage extends WindowBase {
        startMessage() {
            this._textState=this.createTextState ? this.createTextState(message.allText(),0,0,0) :
                {text:this.convertEscapeCharacters(message.allText()),index:0,x:0,y:0};
            this.updatePlacement();
        }
        updatePlacement() { this.y=624-this.height; }
        // Match MZ's required textState argument. The old no-argument mock
        // masked a real crash in the production presentation wrapper.
        newLineX(textState) { return variant === 'MZ' ? (textState.rtl ? this.contents.width - 4 : 4) : 0; }
        createTextState(text) { return {text:this.convertEscapeCharacters(text),rtl:false,index:0}; }
        update() {}
        terminateMessage() { message.clear(); }
    }
    class WindowScroll extends WindowBase {
        startMessage() { this.createContents();this._allTextHeight=this.contents.height; }
    }
    class WindowChoice extends WindowBase {
        itemHeight() { return 36; }
        numVisibleRows() { return 8; }
        itemRectForText(index) { return {x:0,y:index*this.itemHeight(),width:120,height:this.itemHeight()}; }
        itemLineRect(index) { return {x:0,y:index*this.itemHeight(),width:120,height:this.itemHeight()}; }
        drawItem(index) { this.drawText(message._choices[index],0,index*this.itemHeight(),120); }
    }
    if(variant==='MZ') { WindowChoice.prototype.itemRectForText=null;WindowChoice.prototype.standardFontSize=null; }
    class GameSystem { mainFontFace() { return 'GameFont'; } mainFontSize() { return 28; } }
    class SceneBoot { isReady() { return true; } }
    const loaded=[];
    const context=vm.createContext({Utils:{RPGMAKER_NAME:variant,containsArabic:()=>false},Graphics:{boxHeight:624,loadFont:(...a)=>loaded.push(a),isFontLoaded:()=>true},
        FontManager:{load:(...a)=>loaded.push(a)},Game_System:GameSystem,Scene_Boot:SceneBoot,Window_Base:WindowBase,
        Game_Message:GameMessage,Game_Interpreter:Interpreter,Window_Message:WindowMessage,Window_ScrollText:WindowScroll,Window_ChoiceList:WindowChoice,
        Rectangle:class {constructor(x,y,width,height){Object.assign(this,{x,y,width,height});}},Bitmap,
        $gameMessage:message,$gameParty:{members:()=>[{actorId:()=>1,name:()=> '앨리스'}]},
        $gameSystem:new GameSystem(),$gameVariables:{value:()=>100},$gameActors:{actor:()=>({name:()=> '앨리스'})},
        TextManager:{currencyUnit:'G'},ImageManager:{iconWidth:32},ColorManager:{textColor:()=> '#fff'},
        RPT_BilingualOptions:{originalActors:{1:'Alice'},referenceRatio:.55,textboxScale:1}});
    if (variant==='MZ' && engineWindows) {
        // Read only renderer methods, not execute the engine boot or any game events.
        const source=fs.readFileSync(engineWindows,'utf8');
        const methods=['createTextState','processAllText','flushTextState','createTextBuffer','processCharacter',
            'processControlCharacter','processNewLine','obtainEscapeCode','obtainEscapeParam','processEscapeCharacter',
            'processColorChange','processDrawIcon','makeFontBigger','makeFontSmaller','calcTextHeight','maxFontSizeInLine'];
        for (const name of methods) {
            const code=source.match(new RegExp('^Window_Base\\.prototype\\.'+name+' = function[\\s\\S]*?^};','m'));
            assert(code,'Native Window_Base method missing: '+name);vm.runInContext(code[0],context);
        }
        delete WindowMessage.prototype.createTextState;
    }
    vm.runInContext(plugin,context);
    assert.equal(loaded[0][0],'RPT Korean');
    const commands=[{code:101,parameters:[]},{code:401,parameters:['안녕하세요, \\N[1]! \\!'],_rpt_original:'Hello, \\N[1]! We have \\V[2] coins.'},
        {code:102,parameters:[['돌아가기','머무르기']],_rpt_choice_originals:['Go home','Stay']}];
    const interpreter=new Interpreter();interpreter._list=commands;interpreter._index=0;interpreter.command101();
    assert.equal(message._rptOriginal,commands[1]._rpt_original);assert.equal(interpreter._index,2);
    const unchanged=message.allText();const window=new WindowMessage(0,444,816,180);window.startMessage();
    assert.equal(message.allText(),unchanged);assert.deepEqual(message._choices,['돌아가기','머무르기']);
    assert(window._textState.text.includes('\x1b!'));assert(window._rptPane.contents.lines[0].text.includes('Alice'));
    assert(!window._rptPane.contents.lines.some(line=>line.text.includes('앨리스')));
    assert.equal(window._rptPane.contents.fontSize,Math.round(28*.55));
    assert.equal(window.y,624-window.height);assert(window.contents.height>=36);
    assert(window._rptPane.y+window._rptPane.height<=window.height);
    const choices=new WindowChoice(0,0,200,220);choices.drawItem(0);
    assert.equal(choices.contents.lines.at(-1).text,'Go home');assert.equal(choices.contents.lines.at(-1).size,Math.round(28*.55));
    assert(choices.itemHeight()>36);assert(choices.numVisibleRows()*choices.itemHeight()<=624*.45);
    window.terminateMessage();assert.equal(message._rptOriginal,null);assert.equal(window._rptPane.visible,false);
    message._texts=['원문만'];window.startMessage();assert.equal(window.height,180);assert.equal(window._rptPane.visible,false);
    message.clear();message._texts=['아주 긴 문장 '.repeat(100)];message._rptOriginal='Long original '.repeat(100);window.startMessage();
    assert(window.height<=Math.floor(624*.85));assert(window._textState.text.includes('\n'));
    assert(window._rptPane.contents.lines.at(-1).text.endsWith('…'));
    // Old translated data with a lost newline must render on separate rows.
    message.clear();message._texts=['\\PY[30]\\PX[60]첫 문장\\.\\PX[60]다음 문장'];
    message._rptOriginal='\\PY[30]\\PX[60]First sentence\\.\n\\PX[60]Next sentence';
    window.startMessage();
    assert(window._textState.text.includes('\n\x1bPX[60]다음 문장'));
    assert.equal(window._rptPane.y-window.padding,30+2*36+6);
    assert.equal(message.allText(),'\\PY[30]\\PX[60]첫 문장\\.\\PX[60]다음 문장');
    message.clear();message._texts=['짧은 대사'];message._rptOriginal='Short line';window.startMessage();
    assert.equal(window._rptPane.y-window.padding,36+6,'Reference must follow real text, not the four-line default');
    message.clear();message._texts=['\\PY[20]\\FS[48]큰 글자\n다음 줄'];message._rptOriginal='Big type\nSecond line';window.startMessage();
    assert(window._rptPane.y-window.padding>=20+2*56,'Font size and vertical coordinates affect the reference position');
    if (variant==='MZ' && engineWindows && argument('--scene-file')) {
        const data=JSON.parse(fs.readFileSync(argument('--scene-file'),'utf8'));
        const command=argument('--scene-path').split('.').reduce((value,key)=>value[key],data);
        message.clear();message._texts=command.parameters.slice();message._rptOriginal=command._rpt_original;
        window.startMessage();
        WindowBase.prototype.processAllText.call(window,window._textState);
        const painted=window.contents.lines.filter(line=>line.text.trim());
        assert(painted.length>=2,'Expected two independently positioned text runs');
        for(let i=1;i<painted.length;i++)assert(painted[i].y>=painted[i-1].y+painted[i-1].height,'Early-scene text runs overlap');
        const bottom=Math.max(...painted.map(line=>line.y+line.height));
        assert.equal(window._rptPane.y-window.padding-bottom,6,'Native renderer reference gap');
        console.log('Specified early scene: native MZ text positions do not overlap; reference gap is 6px');
    }
    message.clear();interpreter._list=[{code:105},{code:405,parameters:['스크롤'],_rpt_original:'Scroll reference'}];interpreter._index=0;interpreter.command105();
    const scroll=new WindowScroll(0,0,816,180);scroll.startMessage();assert(scroll.contents.lines.some(line=>line.text==='Scroll reference'));
    console.log(variant+': bilingual size, controls, choices, original actor reference, placement, pagination and scroll API checks passed');
}
