// Read-only MSAA inspection of one explicitly owned TMessageForm.
using System;
using System.Text;
using System.Runtime.InteropServices;
using Accessibility;
class MsaaProbe {
    [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern int GetClassNameW(IntPtr h, StringBuilder text, int size);
    [DllImport("oleacc.dll")] static extern int AccessibleObjectFromWindow(IntPtr hwnd, uint objectId, ref Guid iid, out IntPtr result);
    [DllImport("oleacc.dll")]
    static extern int AccessibleChildren([MarshalAs(UnmanagedType.Interface)] IAccessible container, int start, int count,
        [Out, MarshalAs(UnmanagedType.LPArray, SizeParamIndex=2, ArraySubType=UnmanagedType.Struct)] object[] children, out int obtained);
    static int remaining = 128;
    static void Text(IAccessible obj, object child, int role) {
        if (role != 0x29 && role != 0x2A) return; // static text / text
        try { var s=obj.get_accName(child); Write(s); } catch { }
        try { var s=obj.get_accValue(child); Write(s); } catch { }
    }
    static void Write(string text) {
        if (String.IsNullOrWhiteSpace(text)) return;
        if (text.Length>1024) text=text.Substring(0,1024);
        Console.WriteLine(Convert.ToBase64String(Encoding.UTF8.GetBytes(text)));
    }
    static void Walk(IAccessible obj, int depth) {
        if (depth>6 || remaining--<=0) return;
        try { Text(obj,0,Convert.ToInt32(obj.get_accRole(0))); } catch { }
        int count=0; try { count=Math.Min(obj.accChildCount,128); } catch { return; }
        if (count <= 0) return;
        object[] children=new object[count]; int got=0;
        try { if (AccessibleChildren(obj,0,count,children,out got)<0) return; } catch { return; }
        for (int i=0; i<got && remaining>0; i++) {
            object child=children[i];
            var sub=child as IAccessible;
            if (sub != null) { Walk(sub,depth+1); continue; }
            remaining--;
            try { Text(obj,child,Convert.ToInt32(obj.get_accRole(child))); } catch { }
        }
    }
    [STAThread] static int Main(string[] args) {
        if (args.Length != 2) return 2;
        long raw; uint expected;
        if (!Int64.TryParse(args[0], out raw) || !UInt32.TryParse(args[1], out expected)) return 2;
        var hwnd=new IntPtr(raw); uint owner; GetWindowThreadProcessId(hwnd,out owner);
        var cls=new StringBuilder(128); GetClassNameW(hwnd,cls,cls.Capacity);
        if (owner != expected || cls.ToString() != "TMessageForm") return 3;
        var iid=new Guid("618736e0-3c3d-11cf-810c-00aa00389b71"); IntPtr p;
        int result=AccessibleObjectFromWindow(hwnd,0xFFFFFFFC,ref iid,out p); // OBJID_CLIENT
        if (result<0 || p==IntPtr.Zero) return 4;
        try { var obj=Marshal.GetObjectForIUnknown(p) as IAccessible; if (obj==null) return 4; Walk(obj,0); return 0; }
        catch { return 4; }
        finally { Marshal.Release(p); }
    }
}
