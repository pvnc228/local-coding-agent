use std::path::PathBuf;
use std::sync::Mutex;

#[cfg(target_os = "windows")]
use std::os::windows::process::CommandExt;
#[cfg(target_os = "windows")]
use std::process::Command;

use tauri::{AppHandle, Manager, WebviewWindow};
use tauri_plugin_dialog::DialogExt;
use tauri_plugin_shell::{
    process::{CommandChild, CommandEvent},
    ShellExt,
};

struct SidecarState(Mutex<Option<CommandChild>>);

#[cfg(target_os = "windows")]
fn stop_sidecar_tree(child: CommandChild) {
    const CREATE_NO_WINDOW: u32 = 0x0800_0000;
    let pid = child.pid().to_string();
    let stopped = Command::new("taskkill")
        .args(["/PID", &pid, "/T", "/F"])
        .creation_flags(CREATE_NO_WINDOW)
        .status()
        .is_ok_and(|status| status.success());
    if !stopped {
        let _ = child.kill();
    }
}

#[cfg(not(target_os = "windows"))]
fn stop_sidecar_tree(child: CommandChild) {
    let _ = child.kill();
}

fn set_status(window: &WebviewWindow, message: &str) {
    if let Ok(encoded) = serde_json::to_string(message) {
        let _ = window.eval(&format!("window.setStatus({encoded})"));
    }
}

/// One JSON record per stdout line, but tauri-plugin-shell may split a line
/// across several Stdout events, so bytes accumulate until a newline arrives
/// (P1-7: a split record previously never registered as ready).
#[derive(Default)]
struct ReadinessBuffer {
    text: String,
}

impl ReadinessBuffer {
    fn push(&mut self, bytes: &[u8]) -> Option<String> {
        self.text.push_str(&String::from_utf8_lossy(bytes));
        let mut ready_url = None;
        while let Some(position) = self.text.find('\n') {
            let line: String = self.text.drain(..=position).collect();
            let line = line.trim();
            if line.is_empty() {
                continue;
            }
            let value: serde_json::Value = match serde_json::from_str(line) {
                Ok(value) => value,
                Err(_) => continue,
            };
            if value.get("status").and_then(|v| v.as_str()) == Some("ready") {
                ready_url = value.get("url").and_then(|v| v.as_str()).map(str::to_owned);
                break;
            }
        }
        // Never let a hostile/verbose child grow the buffer unbounded.
        if self.text.len() > 64 * 1024 {
            self.text.clear();
        }
        ready_url
    }
}

fn start_sidecar(app: AppHandle, workspace: PathBuf) -> Result<(), String> {
    let workspace_arg = workspace.to_string_lossy().into_owned();
    let command = app
        .shell()
        .sidecar("local-agent-sidecar")
        .map_err(|error| error.to_string())?
        .args([
            "desktop",
            "--headless",
            "--host",
            "127.0.0.1",
            "--port",
            "0",
            "--workspace",
            workspace_arg.as_str(),
        ]);
    let (mut events, child) = command.spawn().map_err(|error| error.to_string())?;
    *app.state::<SidecarState>()
        .0
        .lock()
        .map_err(|_| "sidecar state lock poisoned")? = Some(child);

    let event_app = app.clone();
    tauri::async_runtime::spawn(async move {
        let mut readiness = ReadinessBuffer::default();
        while let Some(event) = events.recv().await {
            match event {
                CommandEvent::Stdout(bytes) => {
                    let Some(ready_url) = readiness.push(&bytes) else {
                        continue;
                    };
                    if let Some(window) = event_app.get_webview_window("main") {
                        match ready_url.parse::<tauri::Url>() {
                            Ok(parsed) => {
                                let _ = window.navigate(parsed);
                            }
                            Err(error) => {
                                set_status(&window, &format!("Invalid sidecar URL: {error}"))
                            }
                        }
                    }
                }
                CommandEvent::Terminated(payload) => {
                    if let Ok(mut state) = event_app.state::<SidecarState>().0.lock() {
                        state.take();
                    }
                    if let Some(window) = event_app.get_webview_window("main") {
                        set_status(
                            &window,
                            &format!("Local backend stopped: {:?}", payload.code),
                        );
                    }
                    break;
                }
                _ => {}
            }
        }
    });
    Ok(())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .manage(SidecarState(Mutex::new(None)))
        .setup(|app| {
            let handle = app.handle().clone();
            let window = app
                .get_webview_window("main")
                .ok_or("main window is missing")?;
            set_status(
                &window,
                "Select the workspace folder you want the local agent to access.",
            );
            let callback_window = window.clone();
            app.dialog().file().pick_folder(move |selection| {
                // The dialog callback can fire after the user closed the main
                // window; spawning the sidecar then would orphan a backend no
                // window will ever navigate to (P1-6).
                if callback_window
                    .app_handle()
                    .get_webview_window("main")
                    .is_none()
                {
                    return;
                }
                let Some(workspace) = selection.and_then(|path| path.into_path().ok()) else {
                    set_status(
                        &callback_window,
                        "Workspace selection cancelled. Restart the app to choose a folder.",
                    );
                    return;
                };
                set_status(&callback_window, "Starting the local controller...");
                if let Err(error) = start_sidecar(handle.clone(), workspace) {
                    set_status(
                        &callback_window,
                        &format!("Could not start the local controller: {error}"),
                    );
                }
            });
            Ok(())
        })
        .on_window_event(|window, event| {
            if matches!(event, tauri::WindowEvent::Destroyed) {
                if let Ok(mut state) = window.app_handle().state::<SidecarState>().0.lock() {
                    if let Some(child) = state.take() {
                        stop_sidecar_tree(child);
                    }
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running Local AI Coding Harness");
}
