// Portable launcher based on Locale Emulator LEProc/LoaderWrapper.cs (LGPL-3.0).
using System;
using System.IO;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Windows.Forms;

class PortableLocaleLauncher {
    [StructLayout(LayoutKind.Sequential)] struct TimeFields { public short Year,Month,Day,Hour,Minute,Second,Milliseconds,Weekday; }
    [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)] struct TimeZone {
        public int Bias;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst=32)] public string StandardName;
        public TimeFields StandardDate; public int StandardBias;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst=32)] public string DaylightName;
        public TimeFields DaylightDate; public int DaylightBias;
    }
    [StructLayout(LayoutKind.Sequential)] struct Leb {
        public uint AnsiCodePage,OemCodePage,LocaleID,DefaultCharset,HookUILanguageAPI;
        [MarshalAs(UnmanagedType.ByValArray, SizeConst=64)] public byte[] DefaultFaceName;
        public TimeZone Timezone;
        public ulong RegistryCount;
    }
    [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)] struct StartupInfo {
        public int cb; public string reserved,desktop,title; public uint x,y,xsize,ysize,xchars,ychars,fill,flags;
        public short show,reserved2; public IntPtr pReserved2,stdin,stdout,stderr;
    }
    [StructLayout(LayoutKind.Sequential)] struct ProcessInfo { public IntPtr process,thread; public uint processId,threadId; }
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)] static extern IntPtr LoadLibrary(string name);
    [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr handle);
    [DllImport("LoaderDll.dll", CharSet=CharSet.Unicode)] static extern uint LeCreateProcess(IntPtr leb, string application, string command, string directory, uint flags, ref StartupInfo startup, out ProcessInfo process, IntPtr pa, IntPtr ta, IntPtr env, IntPtr token);
    static void Log(string root, string item) {
        try { File.AppendAllText(Path.Combine(root,"locale-launch.log"), DateTime.UtcNow.ToString("o")+" "+item+Environment.NewLine, System.Text.Encoding.UTF8); } catch { }
    }
    static void StartBridge(string root, string exe, uint pid) {
        string map=Path.Combine(root,"ui-native-menus.json"), bridge=Path.Combine(root,"MenuBridge.exe");
        if (!File.Exists(map)) return;
        if (!File.Exists(bridge)) { Log(root,"menu_bridge_missing"); return; }
        try {
            var info=new ProcessStartInfo(bridge, pid.ToString()+" \""+map+"\" \""+exe+"\"");
            info.UseShellExecute=false; info.WorkingDirectory=root; info.CreateNoWindow=true;
            Process.Start(info); Log(root,"menu_bridge_started");
        } catch(Exception e) { Log(root,"menu_bridge_error_type="+e.GetType().Name); }
    }
    [STAThread] static int Main(string[] args) {
        bool probe = args.Length == 3 && args[0] == "--probe";
        string root=AppDomain.CurrentDomain.BaseDirectory;
        try {
            string exe;
            if (probe) {
                exe=Path.GetFullPath(args[1]);
                if (!File.Exists(exe)) throw new Exception("Probe executable was not found.");
            } else {
                string target=File.ReadAllText(Path.Combine(root,"locale-target.txt"), System.Text.Encoding.UTF8).Trim();
                if (String.IsNullOrEmpty(target) || Path.IsPathRooted(target)) throw new Exception("Invalid target path.");
                exe=Path.GetFullPath(Path.Combine(root,target));
                if (!exe.StartsWith(Path.GetFullPath(root), StringComparison.OrdinalIgnoreCase) || !File.Exists(exe)) throw new Exception("Game executable was not found inside this folder.");
                Log(root, "target="+target.Replace("\r", " ").Replace("\n", " "));
            }
            string mode="ja-JP", modeFile=Path.Combine(root,"locale-mode.txt");
            if (!probe && File.Exists(modeFile)) mode=File.ReadAllText(modeFile,System.Text.Encoding.UTF8).Trim();
            if (mode!="native" && mode!="ja-JP") throw new Exception("Invalid locale mode.");
            if (mode=="native") {
                var info=new ProcessStartInfo(exe); info.WorkingDirectory=Path.GetDirectoryName(exe); info.UseShellExecute=false;
                Process child=Process.Start(info); if (child==null) throw new Exception("Could not start game.");
                uint childId=(uint)child.Id; Log(root,"child_pid="+childId.ToString()); StartBridge(root,exe,childId); child.Dispose(); return 0;
            }
            string dll=Path.Combine(root,"LoaderDll.dll");
            if (LoadLibrary(dll)==IntPtr.Zero) throw new Exception("Could not load the bundled locale runtime.");
            var leb=new Leb { AnsiCodePage=932, OemCodePage=932, LocaleID=0x411, DefaultCharset=128, HookUILanguageAPI=0, DefaultFaceName=new byte[64], Timezone=new TimeZone { Bias=-540, StandardName="Tokyo Standard Time", DaylightName="Tokyo Standard Time" }, RegistryCount=0 };
            IntPtr ptr=Marshal.AllocHGlobal(Marshal.SizeOf(typeof(Leb)));
            try {
                Marshal.StructureToPtr(leb,ptr,false);
                var startup=new StartupInfo(); startup.cb=Marshal.SizeOf(typeof(StartupInfo)); ProcessInfo pi;
                uint status=LeCreateProcess(ptr,exe,"\""+exe+"\"",Path.GetDirectoryName(exe),0,ref startup,out pi,IntPtr.Zero,IntPtr.Zero,IntPtr.Zero,IntPtr.Zero);
                Log(root,"create_status=0x"+status.ToString("X8"));
                if (status!=0) throw new Exception("Locale process creation failed: 0x"+status.ToString("X8"));
                Log(root,"child_pid="+pi.processId.ToString());
                if (probe) File.WriteAllText(args[2], pi.processId.ToString(), System.Text.Encoding.ASCII);
                else StartBridge(root,exe,pi.processId);
                CloseHandle(pi.thread); CloseHandle(pi.process); return 0;
            } finally { Marshal.FreeHGlobal(ptr); }
        } catch(Exception ex) { Log(root,"error_type="+ex.GetType().Name); if (probe) { try { File.WriteAllText(args[2]+".error",ex.Message); } catch { } } else MessageBox.Show(ex.Message,"Portable locale launcher",MessageBoxButtons.OK,MessageBoxIcon.Error); return 1; }
    }
}
