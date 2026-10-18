"""Launch the overlay, sample FPS from stdout, verify click-through toggles via WS_EX_TRANSPARENT,
and grab screenshots. Run with the spike venv python (needs pillow)."""
import subprocess, time, ctypes, ctypes.wintypes as wt, sys, os, json
HERE = os.path.dirname(os.path.abspath(__file__))
user32 = ctypes.windll.user32
GWL_EXSTYLE, WS_EX_TRANSPARENT, WS_EX_LAYERED, WS_EX_TOPMOST = -20, 0x20, 0x80000, 0x8
p = subprocess.Popen([os.path.join(HERE, 'node_modules', 'electron', 'dist', 'electron.exe'), HERE],
                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
fps = []
def pump(seconds):
    end = time.time() + seconds
    import threading
    while time.time() < end:
        line = p.stdout.readline()
        if not line: break
        line = line.strip()
        if line.startswith('fps='): fps.append(float(line[4:]))
        else: print('  [electron]', line)
# find our window
def find_hwnd():
    found = []
    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def cb(h, _):
        pid = wt.DWORD(); user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
        if pid.value == p.pid and user32.IsWindowVisible(h):
            buf = ctypes.create_unicode_buffer(256); user32.GetWindowTextW(h, buf, 256)
            if 'overlay spike' in buf.value: found.append(h)
        return True
    user32.EnumWindows(cb, 0)
    return found[0] if found else None
pump(6)
h = find_hwnd()
print('hwnd', h)
r = wt.RECT(); user32.GetWindowRect(h, ctypes.byref(r))
print('rect', r.left, r.top, r.right, r.bottom)
ex = user32.GetWindowLongW(h, GWL_EXSTYLE)
print('exstyle topmost=%s layered=%s transparent=%s' % (bool(ex & WS_EX_TOPMOST), bool(ex & WS_EX_LAYERED), bool(ex & WS_EX_TRANSPARENT)))
def probe(x, y, label):
    user32.SetCursorPos(x, y); time.sleep(0.05); user32.SetCursorPos(x+1, y+1); time.sleep(0.4)
    pump(0.3)
    ex = user32.GetWindowLongW(h, GWL_EXSTYLE)
    print(f'cursor at {label} ({x},{y}): WS_EX_TRANSPARENT(click-through)={bool(ex & WS_EX_TRANSPARENT)}')
    return bool(ex & WS_EX_TRANSPARENT)
cx, cy = (r.left + r.right)//2, (r.top + r.bottom)//2
res = {}
res['corner_click_through'] = probe(r.left + 10, r.top + 60, 'top-left corner (no avatar)')
res['center_click_through'] = probe(cx, cy - 40, 'window center (avatar torso)')
res['corner_again_click_through'] = probe(r.right - 10, r.top + 60, 'top-right corner (no avatar)')
# screenshot the region
from PIL import ImageGrab
img = ImageGrab.grab(bbox=(r.left, r.top, r.right, r.bottom), all_screens=True)
img.save(os.path.join(HERE, 'screenshot.png'))
pump(8)
p.kill()
res['fps_samples'] = fps
res['fps_avg'] = sum(fps)/len(fps) if fps else None
res['fps_min'] = min(fps) if fps else None
print(json.dumps(res, indent=1))
json.dump(res, open(os.path.join(HERE, 'results.json'), 'w'), indent=1)
