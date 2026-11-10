// Synthetic MV/MZ APIs only: no game, external engine, network or GUI is loaded.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(__dirname + '/rpgmaker_bilingual.js', 'utf8');
function harness(variant, {nativeWrap=false, spacing=0, characterSpacing=0, dynamicFont=false, nativeTranslation=false}={}) {
    let characterEffects=0;
    class Bitmap {
        constructor(width,height) { Object.assign(this,{width,height,fontFace:'GameFont',fontSize:28,lines:[]}); }
        _makeFontNameText() { return `${this.fontFace} ${this.fontSize}px`; }
        measureTextWidth(text) {
            this._makeFontNameText();
            return Array.from(String(text)).reduce((n,c)=>n+(c===' '? .3 : this.fontFace==='DynamicWide'? .72 : .5)*this.fontSize,0);
        }
        drawText(text,x,y,width,height) {
            const old=this.fontFace;if(dynamicFont)this.fontFace='DynamicWide';this._makeFontNameText();
            const e={text:String(text),x,y,width,height,actualWidth:this.measureTextWidth(text),size:this.fontSize,face:this.fontFace};
            this.lines.push(e);if(this.owner)this.owner.paints.push({...e,page:this.owner.page});this.fontFace=old;
        }
        blt(old) { this.lines=old.lines.slice(); }
        clear() { this.lines=[]; }
    }
    class Base {
        constructor(x,y,width,height) {
            if(typeof x==='object')({x,y,width,height}=x);
            Object.assign(this,{x,y,width,height,padding:18,openness:255,_contentsSprite:{},paints:[],page:0});this.createContents();
        }
        get contents(){return this._contentsSprite.bitmap;}
        set contents(bitmap){this._contentsSprite.bitmap=bitmap;bitmap.owner=this;}
        contentsHeight(){return this.height-2*this.padding;}
        createContents(){this.contents=new Bitmap(this.width-2*this.padding,this.contentsHeight());}
        resetFontSettings(){this.contents.fontSize=28;this.contents.fontFace=this.standardFontFace();}
        standardFontFace(){return 'GameFont';}
        standardFontSize(){return 28;}
        lineHeight(){return 36;}
        textWidth(text){return this.contents.measureTextWidth(text);}
        drawText(text,x,y,width){this.contents.drawText(text,x,y,width,this.lineHeight());}
        move(x,y,width,height){Object.assign(this,{x,y,width,height});}
        addChild(child){(this.children ||= []).push(child);}
        convertEscapeCharacters(text){return text.replace(/\\/g,'\x1b').replace(/\x1bN\[1\]/gi,'앨리스').replace(/\x1bV\[2\]/gi,'100');}
        calcTextHeight(state){
            let size=this.contents.fontSize;
            for(const m of state.text.slice(state.index).split('\n')[0].matchAll(/\x1bFS\[(\d+)\]/gi))size=Math.max(size,Number(m[1]));
            return size+8;
        }
        processNewLine(state){state.x=state.startX;state.y+=state.height;state.height=this.calcTextHeight(state);}
        processControlCharacter(state,c){
            if(c==='\n')this.processNewLine(state);
            if(c==='\f'){state.y=this.contents.height;this.startPause();}
            if(c==='\x1b'){
                const code=state.text.slice(state.index).match(/^[A-Za-z]+|^[.!|^><{}]/)[0];state.index+=code.length;
                const number=/^\[(-?\d+)\]/.exec(state.text.slice(state.index));if(number)state.index+=number[0].length;
                this.escapes.push(code);
                if(code==='FS')this.contents.fontSize=Number(number[1]);if(code==='PX')state.x=Number(number[1]);if(code==='PY')state.y=Number(number[1]);
                if(code==='I')this.drawIcon(Number(number[1]),state.x,state.y);
                if(code==='.'||code==='|')this.startWait(code==='.'?15:60);if(code==='!')this.startPause();
            }
        }
        drawIcon(_id,x,y){this.icons.push({x,y});}
    }
    if(variant==='MZ'){
        Base.prototype.createTextState=function(text,x=0,y=0,width=0){return {text:this.convertEscapeCharacters(text),index:0,x,y,startX:x,startY:y,width,height:36,buffer:'',drawing:true,rtl:false,outputWidth:0,outputHeight:0};};
        Base.prototype.processCharacter=function(s){characterEffects++;const c=s.text[s.index++];if(c.charCodeAt(0)<32){this.flushTextState(s);this.processControlCharacter(s,c);}else s.buffer+=c;};
        Base.prototype.flushTextState=function(s){const text=s.buffer,width=this.textWidth(text);if(s.drawing)this.contents.drawText(text,s.x,s.y,width,s.height);s.x+=width;s.buffer='';};
    }else{
        Base.prototype.processCharacter=function(s){characterEffects++;const c=s.text[s.index++];if(c.charCodeAt(0)<32)this.processControlCharacter(s,c);else{const width=this.textWidth(c);this.contents.drawText(c,s.x,s.y,width*2,s.height);s.x+=width+characterSpacing;}};
    }
    class GameMessage {
        constructor(){this.clear();}clear(){this._texts=[];this._choices=[];}
        add(text){this._texts.push(nativeTranslation?'NATIVE '+text:text);}
        isBusy(){return this._texts.length>0;}allText(){return this._texts.join('\n');}
    }
    const message=new GameMessage();
    class Interpreter {
        command101(){
            if(message.isBusy())return false;this.sideEffects=(this.sideEffects||0)+1;
            while(this._list[this._index+1]?.code===401)message.add(this._list[++this._index].parameters[0]);
            if(this._list[this._index+1]?.code===102)this.setupChoices(this._list[++this._index].parameters);return false;
        }
        command105(){while(this._list[this._index+1]?.code===405)message.add(this._list[++this._index].parameters[0]);return false;}
        setupChoices(p){message._choices=p[0].map(s=>nativeTranslation?'NATIVE '+s:s);message.defaultChoice=p[1];message.cancelChoice=p[2];}
    }
    class Message extends Base {
        startMessage(){const text=message.allText();this._textState=this.createTextState?this.createTextState(text,4,0,0):{text:this.convertEscapeCharacters(text),index:0,x:0,y:0,startX:0,startY:0,height:36,rtl:false};this.newPage(this._textState);this.updatePlacement();}
        newLineX(state){assert(state);return variant==='MZ'?4:0;}
        newPage(s){this.page++;this.contents.clear();this.resetFontSettings();s.x=s.startX;s.y=0;s.height=this.calcTextHeight(s);}
        processNewLine(s){super.processNewLine(s);if(this.needsNewPage(s))this.startPause();}
        needsNewPage(s){return s.index<s.text.length&&s.y+s.height>this.contents.height;}
        startPause(){this.pause=true;this.pauseCalls++;}startWait(){this.waitCalls++;}
        updatePlacement(){this.y=624-this.height;}update(){}terminateMessage(){message.clear();}
    }
    if(nativeWrap){
        assert.equal(variant,'MZ');
        Message.prototype.flushTextState=function(s){
            const text=s.buffer,width=this.textWidth(text);
            if(s.drawing){
                if(width>this.contents.width-s.x&&s.x>0){this.processNewLine(s);s.index--;s.buffer='';return;}
                this.contents.drawText(text,s.x,s.y,width*2,s.height);s.x+=width;if(text!==' ')s.x+=spacing;
            }s.buffer='';
        };
        Message.prototype.processCharacter=function(s){characterEffects++;const c=s.text[s.index++];if(c.charCodeAt(0)<32){this.flushTextState(s);this.processControlCharacter(s,c);}else{s.buffer+=c;if(c===' '||c==='　')this.flushTextState(s);}};
    }
    class Choice extends Base {
        itemHeight(){return 36;}numVisibleRows(){return 8;}
        itemRectForText(index){return {x:0,y:index*this.itemHeight(),width:150,height:this.itemHeight()};}
        itemLineRect(index){return this.itemRectForText(index);}drawItem(index){this.drawText(message._choices[index],0,index*this.itemHeight(),150);}
    }
    class Scroll extends Base{startMessage(){this.createContents();this._allTextHeight=this.contents.height;}}
    class System{mainFontFace(){return 'GameFont';}mainFontSize(){return 28;}}
    class Boot{isReady(){return true;}}
    const loaded=[];
    const context=vm.createContext({Utils:{RPGMAKER_NAME:variant},Graphics:{boxHeight:624,loadFont:(...a)=>loaded.push(a),isFontLoaded:()=>true},FontManager:{load:(...a)=>loaded.push(a)},
        Window_Base:Base,Window_Message:Message,Window_ChoiceList:Choice,Window_ScrollText:Scroll,Game_Message:GameMessage,Game_Interpreter:Interpreter,Game_System:System,Scene_Boot:Boot,Bitmap,
        Rectangle:class{constructor(x,y,width,height){Object.assign(this,{x,y,width,height});}},$gameMessage:message,$gameSystem:new System(),$gameParty:{members:()=>[{actorId:()=>1}]},ImageManager:{iconHeight:32},
        RPT_BilingualOptions:{originalActors:{1:'Alice'},referenceRatio:.55,textboxScale:1}});
    vm.runInContext(source,context);
    function start(text,original='An invented original reference.',width=260){
        message.clear();message._texts=[text];message._rptTranslation=text;message._rptOriginal=original;
        const w=new Message(0,444,width,180);Object.assign(w,{escapes:[],icons:[],waitCalls:0,pauseCalls:0});w.startMessage();return w;
    }
    function finish(w,fast=false){
        const s=w._textState;let frames=0;
        while(s.index<s.text.length){
            assert(++frames<30000,'Message layout stalled');w.pause=false;if(w.needsNewPage(s))w.newPage(s);
            let steps=0;do{w.processCharacter(s);steps++;}while(fast&&!w.pause&&s.index<s.text.length&&steps<1000);
            if(w.flushTextState)w.flushTextState(s);
            const painted=w.paints.filter(p=>p.page===w.page&&p.text.trim());
            if(w._rptLayout&&painted.length){
                const bottom=Math.max(...painted.map(p=>p.y+p.height));assert(bottom<=w.contents.height+.001,'Body overran its pagination boundary');
                if(w._rptLayout.referenceHeight)assert(w._rptPane.y-w.padding>=bottom,'Reference covered live body text');
                assert(w._rptPane.y+w._rptPane.height<=w.height+.001,'Reference escaped the message frame');
            }
        }return w.paints.filter(p=>p.text.trim()).map(p=>p.text).join('');
    }
    function lines(w){const found=new Map();for(const p of w.paints){const k=p.page+':'+p.y;found.set(k,(found.get(k)||'')+p.text);}return [...found.values()].map(s=>s.trim()).filter(Boolean);}
    return {start,finish,lines,message,Interpreter,Choice,Scroll,loaded,effects:()=>characterEffects};
}
let scenarios=0;
for(const [variant,settings] of [['MV',{}],['MV',{characterSpacing:2}],['MZ',{}],['MZ',{nativeWrap:true,spacing:3}],['MZ',{nativeWrap:true,spacing:3,dynamicFont:true,nativeTranslation:true}]]){
    const h=harness(variant,settings);scenarios++;assert.equal(h.loaded[0][0],'RPT Korean');
    for(const text of ['차분하게 재료를 모으면서 숨겨진 답을 찾아보세요.','Gather supplies and carefully explore another island.']){
        const before=h.effects(),w=h.start(text),initial=w._textState.text,painted=h.finish(w);
        assert.equal(h.effects()-before,text.length,'Measuring replayed a character sound/voice callback');
        assert.equal(painted.replace(/\s/g,''),text.replace(/\s/g,''),'Native wrapping lost or duplicated text');
        assert.equal(w._textState.text,initial,'Layout inserted persistent newlines');
        const lines=h.lines(w);for(const word of text.split(' '))assert(lines.some(s=>s.includes(word)),'A fitting word was split: '+word);
        assert.equal(w._rptPane.contents.fontSize,Math.round(28*.55));
    }
    const beforeFast=h.effects(),fast=h.start('비밀스러운 작은 섬에서 천천히 새로운 길을 찾아보세요.');h.finish(fast,true);
    assert.equal(h.effects()-beforeFast,fast._textState.text.length,'Instant text replayed character callbacks');
    assert.equal(h.lines(fast).join('').replace(/\s/g,''),'비밀스러운 작은 섬에서 천천히 새로운 길을 찾아보세요.'.replace(/\s/g,''));
    const long=h.start('가나다라마바사아자차카타파하'.repeat(10),'Long original '.repeat(80),180);h.finish(long);
    assert(long.page>1,'Long unbroken text must use native pagination');assert(long._rptPane.contents.lines.at(-1).text.endsWith('…'));
    const c=h.start('\\PY[20]\\FS[48]큰 글자\\FS[28]\n다음 줄\\.\\!','Two source lines',420);h.finish(c);
    assert.deepEqual(c.escapes,['PY','FS','FS','.','!']);assert.equal(c.waitCalls,1);assert.equal(c.pauseCalls,1);
    assert(c._rptPane.y-c.padding>=Math.max(...c.paints.map(p=>p.y+p.height)));
    const old=h.start('\\PX[20]첫 말\\.\\PX[20]다음 말','\\PX[20]First\\.\n\\PX[20]Next',420);
    assert(old._textState.text.includes('\n\x1bPX[20]'));h.finish(old);
    const actor=h.start('\\N[1] 안녕','Hello \\N[1], \\V[2] coins.');h.finish(actor);
    assert(actor._rptPane.contents.lines.some(p=>p.text.includes('Alice')));assert(!actor._rptPane.contents.lines.some(p=>p.text.includes('앨리스')));
    h.message.clear();
    const commands=[{code:101},{code:401,parameters:['base source'],_rpt_translation:'확정한 번역문',_rpt_original:'Selected source'},
        {code:401,parameters:['second physical command']},{code:102,parameters:[['base choice'],0,-1],_rpt_choice_translations:['확정 선택지'],_rpt_choice_originals:['Selected choice']}];
    const i=new h.Interpreter();i._list=commands;i._index=0;i.command101();
    assert.equal(h.message.allText(),'확정한 번역문');assert.equal(i._index,3);assert.equal(i.sideEffects,1);
    assert.deepEqual(Array.from(h.message._choices),['확정 선택지']);assert.equal(h.message.defaultChoice,0);assert.equal(h.message.cancelChoice,-1);
    const choices=new h.Choice(0,0,220,220);choices.drawItem(0);assert.equal(choices.contents.lines.at(-1).text,'Selected choice');assert.equal(choices.contents.lines.at(-1).size,Math.round(28*.55));
    h.message.clear();i._list=[{code:101},{code:401,parameters:['base source'],_rpt_translation:'원문 표시 없는 번역'}];i._index=0;i.command101();
    assert.equal(h.message.allText(),'원문 표시 없는 번역');assert.equal(h.message._rptOriginal,null);
    const off=h.start('원문 표시 없이도 자연스럽게 이어지는 한국어 문장입니다.',null);h.finish(off);assert.equal(off._rptPane.visible,false);
    off.terminateMessage();assert.equal(h.message._rptTranslation,null);assert.equal(off._rptPane.visible,false);
    h.message.clear();i._list=[{code:105},{code:405,parameters:['base scroll'],_rpt_translation:'스크롤 번역',_rpt_original:'Scroll reference'}];i._index=0;i.command105();
    assert.equal(h.message.allText(),'스크롤 번역');const scroll=new h.Scroll(0,0,420,180);scroll.startMessage();assert(scroll.contents.lines.some(p=>p.text==='Scroll reference'));
    console.log(variant+' '+JSON.stringify(settings)+': live wrapping, fonts, bounds, waits, pages, references and final metadata passed');
}
console.log(scenarios+' synthetic renderer scenarios passed; no game or external engine code loaded.');
