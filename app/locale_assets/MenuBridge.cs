// Read-only mapping input; changes only the strings of matching Win32 menu items.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.RegularExpressions;
using System.Web.Script.Serialization;
using System.Windows.Forms;

internal static class MenuBridge {
    const uint MIIM_STRING=0x40, MF_BYPOSITION=0x400, WM_MN_GETHMENU=0x01E1;
    const uint EVENT_SYSTEM_MENUSTART=4, EVENT_SYSTEM_MENUPOPUPSTART=6, EVENT_OBJECT_CREATE=0x8000, EVENT_OBJECT_NAMECHANGE=0x800C;
    const uint WINEVENT_OUTOFCONTEXT=0, PROCESS_QUERY_LIMITED_INFORMATION=0x1000;
    [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)] struct MENUITEMINFO {
        public uint cbSize, fMask, fType, fState, wID;
        public IntPtr hSubMenu, hbmpChecked, hbmpUnchecked, dwItemData, dwTypeData;
        public uint cch; public IntPtr hbmpItem;
    }
    delegate bool EnumProc(IntPtr hwnd, IntPtr lParam);
    delegate void WinEventProc(IntPtr hook,uint evt,IntPtr hwnd,int objectId,int childId,uint thread,uint time);
    [DllImport("user32.dll")] static extern bool EnumWindows(EnumProc callback, IntPtr lParam);
    [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr hwnd,out uint pid);
    [DllImport("user32.dll",CharSet=CharSet.Unicode)] static extern int GetClassNameW(IntPtr hwnd,StringBuilder text,int max);
    [DllImport("user32.dll")] static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] static extern IntPtr GetAncestor(IntPtr hwnd,uint flags);
    [DllImport("user32.dll")] static extern bool IsWindow(IntPtr hwnd);
    [DllImport("user32.dll")] static extern IntPtr GetMenu(IntPtr hwnd);
    [DllImport("user32.dll")] static extern IntPtr GetSystemMenu(IntPtr hwnd,bool revert);
    [DllImport("user32.dll")] static extern int GetMenuItemCount(IntPtr menu);
    [DllImport("user32.dll")] static extern IntPtr GetSubMenu(IntPtr menu,int index);
    [DllImport("user32.dll",CharSet=CharSet.Unicode)] static extern int GetMenuStringW(IntPtr menu,uint item,StringBuilder text,int max,uint flags);
    [DllImport("user32.dll",CharSet=CharSet.Unicode)] static extern bool SetMenuItemInfoW(IntPtr menu,uint item,bool byPosition,ref MENUITEMINFO info);
    [DllImport("user32.dll")] static extern bool DrawMenuBar(IntPtr hwnd);
    [DllImport("user32.dll")] static extern IntPtr SetWinEventHook(uint min,uint max,IntPtr dll,WinEventProc callback,uint process,uint thread,uint flags);
    [DllImport("user32.dll")] static extern bool UnhookWinEvent(IntPtr hook);
    [DllImport("user32.dll",CharSet=CharSet.Unicode)] static extern IntPtr SendMessageTimeoutW(IntPtr hwnd,uint msg,IntPtr wparam,IntPtr lparam,uint flags,uint timeout,out IntPtr result);
    [DllImport("kernel32.dll",SetLastError=true)] static extern IntPtr OpenProcess(uint access,bool inherit,uint pid);
    [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)] static extern bool QueryFullProcessImageNameW(IntPtr process,uint flags,StringBuilder path,ref int length);
    [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr handle);
    static uint pid; static Dictionary<string,object> captions; static HashSet<string> translated; static Dictionary<string,string> uniqueLabels; static HashSet<string> translatedLabels; static int captionCapacity; static bool busy; static Process target; static IntPtr mainWindow;
    static readonly List<IntPtr> hooks=new List<IntPtr>(); static WinEventProc callback;
    static bool Owned(IntPtr h) { uint owner; return h!=IntPtr.Zero && GetWindowThreadProcessId(h,out owner)!=0 && owner==pid; }
    sealed class NumberTemplate {
        internal Regex Pattern;
        internal string Replacement;
        internal Dictionary<string,string> Groups;
        internal string Prefix;
    }
    static readonly Regex tokenPattern=new Regex(@"\$\{([A-Za-z_][A-Za-z0-9_]*)\}",RegexOptions.CultureInvariant,TimeSpan.FromMilliseconds(5));
    static List<NumberTemplate> numberTemplates;
    static HashSet<string> TokenNames(string text) {
        var names=new HashSet<string>(StringComparer.Ordinal); int count=0;
        foreach(Match token in tokenPattern.Matches(text)) { names.Add(token.Groups[1].Value); if(++count>16) return null; }
        if(tokenPattern.Replace(text,"").Contains("${") || names.Count>8) return null;
        return names;
    }
    static void IndexNumberTemplates() {
        numberTemplates=new List<NumberTemplate>();
        int candidates=0;
        foreach(var pair in captions) {
            if(!pair.Key.Contains("${")) continue;
            if(++candidates>64) { numberTemplates.Clear(); return; }
            if(!(pair.Value is string) || pair.Key.Length>1024 || ((string)pair.Value).Length>1024) continue;
            string replacement=(string)pair.Value;
            var left=TokenNames(pair.Key); var right=TokenNames(replacement);
            if(left==null || right==null || left.Count==0 || !left.SetEquals(right)) continue;
            var groups=new Dictionary<string,string>(StringComparer.Ordinal); var pattern=new StringBuilder(@"\A"); int position=0;
            foreach(Match token in tokenPattern.Matches(pair.Key)) {
                pattern.Append(Regex.Escape(pair.Key.Substring(position,token.Index-position)));
                string name=token.Groups[1].Value, group;
                if(!groups.TryGetValue(name,out group)) {
                    group="v"+groups.Count; groups.Add(name,group); pattern.Append("(?<"+group+">[0-9]{1,10})");
                } else pattern.Append(@"\k<"+group+">");
                position=token.Index+token.Length;
            }
            pattern.Append(Regex.Escape(pair.Key.Substring(position))); pattern.Append(@"\z");
            var first=tokenPattern.Match(pair.Key);
            numberTemplates.Add(new NumberTemplate { Pattern=new Regex(pattern.ToString(),RegexOptions.CultureInvariant,TimeSpan.FromMilliseconds(5)),
                Replacement=replacement, Groups=groups, Prefix=pair.Key.Substring(0,first.Index) });
        }
    }
    static string ApplyNumberTemplate(string source) {
        if(source.Length>1024) return null;
        int matches=0; string result=null;
        foreach(var template in numberTemplates) {
            if(!source.StartsWith(template.Prefix,StringComparison.Ordinal)) continue;
            try {
                Match match=template.Pattern.Match(source); if(!match.Success) continue;
                if(++matches>1) return null;
                result=tokenPattern.Replace(template.Replacement,delegate(Match token) { return match.Groups[template.Groups[token.Groups[1].Value]].Value; });
            } catch(RegexMatchTimeoutException) { return null; }
        }
        return result;
    }
    static readonly Regex keySuffix=new Regex(@"^(.*)(\((?:&[A-Za-z0-9]|F(?:[1-9]|1[0-9]|2[0-4]))\))$",RegexOptions.CultureInvariant);
    static bool SplitSuffix(string caption,out string label,out string key,out string tab) {
        int index=caption.IndexOf('\t'); tab=index<0 ? "" : caption.Substring(index);
        string main=index<0 ? caption : caption.Substring(0,index);
        Match match=keySuffix.Match(main);
        if(!match.Success || match.Groups[1].Value.Length==0) { label=main; key=""; return false; }
        label=match.Groups[1].Value; key=match.Groups[2].Value; return true;
    }
    static void IndexCaptions() {
        uniqueLabels=new Dictionary<string,string>(StringComparer.Ordinal);
        translatedLabels=new HashSet<string>(StringComparer.Ordinal);
        int longest=0;
        foreach(var pair in captions) {
            longest=Math.Max(longest,pair.Key.Length);
            if(!(pair.Value is string)) continue;
            string label,key,tab,targetLabel,targetKey,targetTab;
            if(!SplitSuffix(pair.Key,out label,out key,out tab)) continue;
            string target=(string)pair.Value;
            SplitSuffix(target,out targetLabel,out targetKey,out targetTab);
            if(targetLabel.Length==0) continue;
            translatedLabels.Add(targetLabel);
            string previous;
            if(!uniqueLabels.TryGetValue(label,out previous)) uniqueLabels.Add(label,targetLabel);
            else if(previous!=targetLabel) uniqueLabels[label]=null;
        }
        captionCapacity=Math.Min(32768,Math.Max(1024,longest+257));
    }
    static bool Apply(IntPtr menu, int depth, HashSet<IntPtr> visited, ref int budget) {
        if(menu==IntPtr.Zero || depth>12 || budget<=0 || !visited.Add(menu)) return false;
        bool changed=false; int count=GetMenuItemCount(menu); if(count<0 || count>4096) return false;
        var buf=new StringBuilder(captionCapacity);
        for(int i=0;i<count && budget>0;i++) {
            budget--; buf.Length=0;
            int length=GetMenuStringW(menu,(uint)i,buf,buf.Capacity,MF_BYPOSITION);
            if(length>=buf.Capacity-1) { if(Apply(GetSubMenu(menu,i),depth+1,visited,ref budget)) changed=true; continue; }
            string source=buf.ToString(); object mapped; string value=null;
            if(source.Length>0 && !translated.Contains(source)) {
                // An exact full-caption mapping takes precedence over fallback.
                if(captions.TryGetValue(source,out mapped)) {
                    if(mapped is string) value=(string)mapped;
                } else {
                    int tab=source.IndexOf('\t');
                    if(tab>0) {
                        string prefix=source.Substring(0,tab);
                        if(!translated.Contains(prefix) && captions.TryGetValue(prefix,out mapped) && mapped is string && ((string)mapped).IndexOf('\t')<0)
                            value=(string)mapped+source.Substring(tab);
                    }
                    if(value==null) {
                        string label,key,shortcut,mappedLabel;
                        if(SplitSuffix(source,out label,out key,out shortcut) && !translatedLabels.Contains(label)
                           && uniqueLabels.TryGetValue(label,out mappedLabel) && mappedLabel!=null)
                            value=mappedLabel+key+shortcut;
                    }
                    if(value==null) value=ApplyNumberTemplate(source);
                }
            }
            if(value!=null && value!=source && value.Length>0 && value.Length<32767 && value.IndexOf('\0')<0) {
                IntPtr p=Marshal.StringToHGlobalUni(value);
                try { var info=new MENUITEMINFO(); info.cbSize=(uint)Marshal.SizeOf(typeof(MENUITEMINFO)); info.fMask=MIIM_STRING; info.dwTypeData=p; info.cch=(uint)value.Length;
                    if(SetMenuItemInfoW(menu,(uint)i,true,ref info)) changed=true;
                } finally { Marshal.FreeHGlobal(p); }
            }
            if(Apply(GetSubMenu(menu,i),depth+1,visited,ref budget)) changed=true;
        }
        return changed;
    }
    static bool Inspect(IntPtr h) {
        if(!Owned(h)) return false;
        var visited=new HashSet<IntPtr>(); int budget=1024;
        IntPtr rootMenu=GetMenu(h); if(rootMenu!=IntPtr.Zero) mainWindow=h;
        bool changed=Apply(rootMenu,0,visited,ref budget);
        if(Apply(GetSystemMenu(h,false),0,visited,ref budget)) changed=true;
        var cls=new StringBuilder(128); GetClassNameW(h,cls,cls.Capacity);
        if(cls.ToString()=="#32768") {
            IntPtr menu; if(SendMessageTimeoutW(h,WM_MN_GETHMENU,IntPtr.Zero,IntPtr.Zero,2,150,out menu)!=IntPtr.Zero)
                if(Apply(menu,0,visited,ref budget)) changed=true;
        }
        if(changed) DrawMenuBar(h);
        return changed;
    }
    static bool Scan() {
        bool found=false;
        EnumWindows(delegate(IntPtr hwnd,IntPtr unused) { if(Owned(hwnd) && Inspect(hwnd)) found=true; return true; },IntPtr.Zero);
        return found;
    }
    static void Event(IntPtr hook,uint evt,IntPtr hwnd,int objectId,int childId,uint thread,uint when) {
        if(busy || !Owned(hwnd)) return;
        // Ignore unrelated content/name changes in the game window.
        if(evt==EVENT_OBJECT_NAMECHANGE && objectId!=-3 && objectId!=-1) return;
        if(evt==EVENT_OBJECT_CREATE && objectId!=-3 && objectId!=-1 && objectId!=0) return;
        busy=true; try { Inspect(hwnd); } catch { } finally { busy=false; }
    }
    static bool SameImage(uint id,string expected) {
        IntPtr h=OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION,false,id); if(h==IntPtr.Zero) return false;
        try { var path=new StringBuilder(32768); int n=path.Capacity;
            return QueryFullProcessImageNameW(h,0,path,ref n) && String.Equals(Path.GetFullPath(path.ToString()),Path.GetFullPath(expected),StringComparison.OrdinalIgnoreCase);
        } finally { CloseHandle(h); }
    }
    [STAThread] static int Main(string[] args) {
        if(args.Length!=3 || !UInt32.TryParse(args[0],out pid) || !File.Exists(args[1]) || !File.Exists(args[2]) || !SameImage(pid,args[2])) return 2;
        try {
            var serializer=new JavaScriptSerializer(); serializer.MaxJsonLength=4*1024*1024;
            var root=serializer.DeserializeObject(File.ReadAllText(args[1],Encoding.UTF8)) as Dictionary<string,object>;
            if(root==null || !root.ContainsKey("version") || Convert.ToInt32(root["version"])!=1 || !root.ContainsKey("captions")) return 3;
            captions=root["captions"] as Dictionary<string,object>; if(captions==null || captions.Count==0) return 3;
            translated=new HashSet<string>(StringComparer.Ordinal);
            foreach(object value in captions.Values) if(value is string) translated.Add((string)value);
            IndexCaptions(); IndexNumberTemplates();
            target=Process.GetProcessById((int)pid); callback=Event;
            foreach(uint evt in new uint[]{EVENT_SYSTEM_MENUSTART,EVENT_SYSTEM_MENUPOPUPSTART,EVENT_OBJECT_CREATE,EVENT_OBJECT_NAMECHANGE}) {
                IntPtr hook=SetWinEventHook(evt,evt,IntPtr.Zero,callback,pid,0,WINEVENT_OUTOFCONTEXT); if(hook!=IntPtr.Zero) hooks.Add(hook);
            }
            var context=new ApplicationContext(); int attempts=0;
            var startup=new Timer(); startup.Interval=250;
            startup.Tick+=delegate { attempts++; bool found=false; try { found=Scan(); } catch { } if(found || attempts>=80) startup.Stop(); };
            var lifetime=new Timer(); lifetime.Interval=1000;
            lifetime.Tick+=delegate {
                try {
                    if(target.HasExited) { context.ExitThread(); return; }
                    if(busy) return;
                    IntPtr foreground=GetForegroundWindow();
                    IntPtr rootWindow=foreground==IntPtr.Zero ? IntPtr.Zero : GetAncestor(foreground,2);
                    IntPtr candidate=Owned(rootWindow) && GetMenu(rootWindow)!=IntPtr.Zero ? rootWindow : mainWindow;
                    if(candidate!=IntPtr.Zero && IsWindow(candidate) && Owned(candidate)) {
                        busy=true; try { Inspect(candidate); } finally { busy=false; }
                    }
                } catch { context.ExitThread(); }
            };
            try { if(!Scan()) startup.Start(); lifetime.Start(); Application.Run(context); }
            finally { startup.Stop(); lifetime.Stop(); startup.Dispose(); lifetime.Dispose(); foreach(IntPtr h in hooks) UnhookWinEvent(h); }
            return 0;
        } catch { foreach(IntPtr h in hooks) UnhookWinEvent(h); return 1; }
    }
}
