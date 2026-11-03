//! Windows 11 Snap Layouts for the frameless main window.
//!
//! WebView2 (windowed hosting, as used by wry) cannot report a maximize button
//! region, and the web content is a child window, so the top-level window never
//! sees `WM_NCHITTEST` for the page's maximize button. A transparent `STATIC`
//! child window of our own process is therefore laid over that button; it answers
//! `HTMAXBUTTON`, which makes Windows 11 show the Snap Layouts flyout, and turns
//! the click into maximize/restore. No code runs in another process and no input
//! is synthesized.
//!
//! Layout contract with the page (CSS px, scaled by the window DPI): the control
//! strip is 44px high, every window button is 46px wide and the maximize button
//! is the second from the right, i.e. x in [W-92, W-46), y in [0, 44).
//!
//! Hover cannot reach the page (the overlay covers the button), so the overlay
//! toggles the `max-hover` class on `<html>` through `eval`, which needs no IPC
//! permission.

use std::{mem::size_of, ptr::null, ptr::null_mut, sync::Mutex};

use tauri::{Manager, WebviewWindow, Window};
use windows_sys::Win32::{
    Foundation::{HWND, LPARAM, LRESULT, RECT, WPARAM},
    UI::{
        HiDpi::GetDpiForWindow,
        Input::KeyboardAndMouse::{TrackMouseEvent, TME_LEAVE, TME_NONCLIENT, TRACKMOUSEEVENT},
        Shell::{DefSubclassProc, RemoveWindowSubclass, SetWindowSubclass},
        WindowsAndMessaging::{
            CreateWindowExW, GetClientRect, IsIconic, IsZoomed, LoadCursorW, SendMessageW,
            SetCursor, SetWindowPos, ShowWindow, HTMAXBUTTON, HWND_TOP, IDC_ARROW, SC_MAXIMIZE,
            SC_RESTORE, SWP_NOACTIVATE, SWP_SHOWWINDOW, SW_HIDE, WM_NCDESTROY, WM_NCHITTEST,
            WM_NCLBUTTONDOWN, WM_NCLBUTTONUP, WM_NCMOUSELEAVE, WM_SETCURSOR, WM_SYSCOMMAND,
            WS_CHILD, WS_VISIBLE,
        },
    },
};

const BUTTON_WIDTH: i32 = 46;
const BUTTON_HEIGHT: i32 = 44;
/// Distance of the maximize button's left edge from the right client edge.
const MAXIMIZE_LEFT_FROM_RIGHT: i32 = 2 * BUTTON_WIDTH;
const SUBCLASS_ID: usize = 0x4c43_4153; // "LCAS"

/// Child HWND of the overlay (0 until installed).
#[derive(Default)]
pub struct Overlay(Mutex<isize>);

struct State {
    window: WebviewWindow,
    parent: HWND,
    hovering: bool,
}

fn scale(css_px: i32, dpi: u32) -> i32 {
    ((css_px as i64 * dpi as i64 + 48) / 96) as i32
}

fn set_hover(window: &WebviewWindow, hover: bool) {
    let _ = window.eval(format!(
        "document.documentElement.classList.toggle('max-hover',{hover})"
    ));
}

/// Creates the overlay. Must run on the main thread (the setup hook does).
pub fn install(window: &WebviewWindow) {
    let (Ok(parent), Some(overlay)) = (window.hwnd(), window.try_state::<Overlay>()) else {
        return;
    };
    let parent = parent.0 as HWND;
    let class: Vec<u16> = "STATIC\0".encode_utf16().collect();
    unsafe {
        let child = CreateWindowExW(
            0,
            class.as_ptr(),
            null(),
            WS_CHILD | WS_VISIBLE,
            0,
            0,
            0,
            0,
            parent,
            null_mut(),
            null_mut(),
            null(),
        );
        if child.is_null() {
            return;
        }
        let state = Box::into_raw(Box::new(State {
            window: window.clone(),
            parent,
            hovering: false,
        }));
        if SetWindowSubclass(child, Some(subclass_proc), SUBCLASS_ID, state as usize) == 0 {
            drop(Box::from_raw(state));
            return;
        }
        if let Ok(mut slot) = overlay.0.lock() {
            *slot = child as isize;
        }
        place(parent, child);
    }
}

/// Re-places the overlay after a resize, maximize/restore or DPI change.
pub fn sync(window: &Window) {
    let (Ok(parent), Some(overlay)) = (window.hwnd(), window.try_state::<Overlay>()) else {
        return;
    };
    let child = overlay.0.lock().map(|slot| *slot).unwrap_or(0);
    if child != 0 {
        unsafe { place(parent.0 as HWND, child as HWND) };
    }
}

unsafe fn place(parent: HWND, child: HWND) {
    let mut client = RECT {
        left: 0,
        top: 0,
        right: 0,
        bottom: 0,
    };
    let dpi = GetDpiForWindow(parent);
    if IsIconic(parent) != 0 || GetClientRect(parent, &mut client) == 0 || dpi == 0 {
        ShowWindow(child, SW_HIDE);
        return;
    }
    let x = client.right - scale(MAXIMIZE_LEFT_FROM_RIGHT, dpi);
    if x < 0 {
        ShowWindow(child, SW_HIDE);
        return;
    }
    // HWND_TOP keeps the overlay above the WebView2 child window.
    SetWindowPos(
        child,
        HWND_TOP,
        x,
        0,
        scale(BUTTON_WIDTH, dpi),
        scale(BUTTON_HEIGHT, dpi),
        SWP_NOACTIVATE | SWP_SHOWWINDOW,
    );
}

unsafe extern "system" fn subclass_proc(
    hwnd: HWND,
    msg: u32,
    wparam: WPARAM,
    lparam: LPARAM,
    subclass_id: usize,
    ref_data: usize,
) -> LRESULT {
    let state = ref_data as *mut State;
    match msg {
        WM_NCDESTROY => {
            RemoveWindowSubclass(hwnd, Some(subclass_proc), subclass_id);
            drop(Box::from_raw(state));
        }
        WM_NCHITTEST => {
            let state = &mut *state;
            if !state.hovering {
                state.hovering = true;
                set_hover(&state.window, true);
                let mut track = TRACKMOUSEEVENT {
                    cbSize: size_of::<TRACKMOUSEEVENT>() as u32,
                    dwFlags: TME_LEAVE | TME_NONCLIENT,
                    hwndTrack: hwnd,
                    dwHoverTime: 0,
                };
                TrackMouseEvent(&mut track);
            }
            return HTMAXBUTTON as LRESULT;
        }
        WM_NCMOUSELEAVE => {
            let state = &mut *state;
            if state.hovering {
                state.hovering = false;
                set_hover(&state.window, false);
            }
        }
        WM_NCLBUTTONDOWN if wparam == HTMAXBUTTON as usize => return 0,
        WM_NCLBUTTONUP if wparam == HTMAXBUTTON as usize => {
            let command = if IsZoomed((*state).parent) != 0 {
                SC_RESTORE
            } else {
                SC_MAXIMIZE
            };
            SendMessageW((*state).parent, WM_SYSCOMMAND, command as WPARAM, 0);
            return 0;
        }
        WM_SETCURSOR => {
            SetCursor(LoadCursorW(null_mut(), IDC_ARROW));
            return 1;
        }
        _ => {}
    }
    DefSubclassProc(hwnd, msg, wparam, lparam)
}
