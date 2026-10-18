"""Spike 6: Windows UI Automation via the `uiautomation` package.

Opens Explorer and Settings, lists their interactive elements by name, clicks named
elements (Explorer: navigate to Downloads via the nav pane; Settings: open "Bluetooth & devices"
then read the Bluetooth toggle state without changing it), and times each step.
Also saves a screenshot of the Settings window (used by the Qwen3-VL test in spike 2).

Run: python uia_spike.py
"""
import os, sys, time, json, subprocess
import uiautomation as auto

HERE = os.path.dirname(os.path.abspath(__file__))
T = {}
def tick(label, t0): T[label] = round(time.perf_counter() - t0, 3); print(f"  [{T[label]*1000:6.0f} ms] {label}")

def list_interactive(win, max_depth=12, limit=60):
    """Walk the control tree and return (depth, ControlType, Name, AutomationId) for named interactive controls."""
    out = []
    types = {"ButtonControl", "ListItemControl", "TreeItemControl", "TabItemControl", "HyperlinkControl",
             "CheckBoxControl", "RadioButtonControl", "ComboBoxControl", "EditControl", "MenuItemControl", "SplitButtonControl", "ToggleButtonControl"}
    for c, depth in auto.WalkControl(win, includeTop=False, maxDepth=max_depth):
        if c.ControlTypeName in types and c.Name:
            out.append((depth, c.ControlTypeName, c.Name, c.AutomationId))
            if len(out) >= limit: break
    return out

def explorer():
    print("\n== Explorer ==")
    t0 = time.perf_counter()
    subprocess.Popen(["explorer.exe", os.path.expanduser("~")])
    win = auto.WindowControl(searchDepth=1, ClassName="CabinetWClass")
    assert win.Exists(10, 0.2), "Explorer window not found"
    tick("explorer open + window found", t0)
    t0 = time.perf_counter(); items = list_interactive(win); tick(f"enumerate {len(items)} interactive elements", t0)
    for d, ty, n, aid in items[:25]: print(f"     {'  '*d}{ty:18} {n!r}")
    # click "Downloads" in the navigation pane by name
    t0 = time.perf_counter()
    # Note: pinned items are named 'Downloads (pinned)', so match by substring.
    dl = win.TreeItemControl(SubName="Downloads", searchDepth=20)
    found = dl.Exists(5, 0.2)
    tick("find TreeItem 'Downloads*'", t0)
    clicked = False
    if found:
        print("   matched:", repr(dl.Name))
        t0 = time.perf_counter()
        try:
            dl.GetSelectionItemPattern().Select()   # programmatic, no mouse movement
        except Exception as e:
            print("   Select() failed, using mouse click:", e); dl.Click()
        time.sleep(1.0)
        clicked = "Downloads" in (win.Name or "")
        if not clicked:  # some shells ignore Select(); real click as fallback
            dl.Click(); time.sleep(1.0); clicked = "Downloads" in (win.Name or "")
            how = "mouse click"
        else:
            how = "SelectionItemPattern"
        tick(f"navigate to Downloads via {how} + settle", t0)
        print("   window title now:", win.Name)
    # programmatic activation instead of a mouse click: 'Back' button via InvokePattern
    t0 = time.perf_counter()
    back = win.ButtonControl(Name="Back", searchDepth=12)
    inv_ok = False
    if back.Exists(2, 0.2) and back.IsEnabled:
        try:
            back.GetInvokePattern().Invoke(); time.sleep(0.6); inv_ok = True
        except Exception as e:
            print("   Invoke failed, falling back to Click:", e); back.Click(simulateMove=False); time.sleep(0.6); inv_ok = "click-fallback"
    tick("InvokePattern on 'Back' button", t0)
    win.GetWindowPattern().Close()
    return {"found_window": True, "n_elements": len(items), "downloads_found": found, "downloads_click_navigated": clicked, "invoke_back_ok": inv_ok}

def settings():
    print("\n== Settings ==")
    t0 = time.perf_counter()
    subprocess.Popen(["cmd", "/c", "start", "ms-settings:"], shell=False)
    win = auto.WindowControl(searchDepth=1, Name="Settings")
    assert win.Exists(15, 0.2), "Settings window not found"
    time.sleep(1.5)  # WinUI content loads async
    tick("settings open + window found", t0)
    win.SetActive(); win.Maximize() if False else None
    t0 = time.perf_counter(); items = list_interactive(win, max_depth=20, limit=80); tick(f"enumerate {len(items)} interactive elements", t0)
    for d, ty, n, aid in items[:30]: print(f"     {'  '*min(d,6)}{ty:18} {n!r}  id={aid!r}")
    # click "Bluetooth & devices" nav item by name
    t0 = time.perf_counter()
    bt = win.ListItemControl(Name="Bluetooth & devices", searchDepth=20)
    found = bt.Exists(5, 0.2)
    tick("find ListItem 'Bluetooth & devices'", t0)
    toggle_state = None; page_ok = False
    if found:
        t0 = time.perf_counter(); bt.Click(simulateMove=False); time.sleep(1.5); tick("click + settle", t0)
        # Bluetooth toggle: read state via TogglePattern WITHOUT flipping it
        t0 = time.perf_counter()
        # WinUI pages raise transient COM errors while still loading; retry the lookup.
        for attempt in range(6):
            try:
                tog = win.ButtonControl(searchDepth=25, Name="Bluetooth")
                if tog.Exists(1, 0.2):
                    toggle_state = tog.GetTogglePattern().ToggleState; page_ok = True; break
            except Exception as e:
                print(f"   attempt {attempt}: {type(e).__name__} {e}"); time.sleep(0.5)
        tick("find 'Bluetooth' toggle + read state (incl. retries)", t0)
        print("   Bluetooth toggle state:", toggle_state, "(1=on, 0=off)")
    # screenshot of the Settings window for the VL test
    try:
        from PIL import ImageGrab
        r = win.BoundingRectangle
        img = ImageGrab.grab(bbox=(r.left, r.top, r.right, r.bottom), all_screens=True)
        p = os.path.join(HERE, "settings_window.png"); img.save(p); print("   screenshot:", p, img.size)
    except Exception as e:
        print("   screenshot failed:", e)
    win.GetWindowPattern().Close()
    return {"found_window": True, "n_elements": len(items), "bluetooth_nav_found": found, "bluetooth_page_ok": page_ok, "bluetooth_toggle_state": str(toggle_state)}

if __name__ == "__main__":
    auto.uiautomation.SetGlobalSearchTimeout(10)
    res = {"explorer": explorer(), "settings": settings(), "timings_s": T}
    json.dump(res, open(os.path.join(HERE, "results.json"), "w"), indent=2)
    print(json.dumps(res, indent=1))
