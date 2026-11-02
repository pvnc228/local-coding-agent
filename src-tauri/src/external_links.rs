//! Links that open a new window (`target="_blank"`, `window.open`), such as the ones in Help.
//!
//! They never open inside the app window. An allowed link goes to the system browser, anything
//! else is dropped. The page needs no permission for this: the request is intercepted here.

use tauri::{webview::NewWindowResponse, AppHandle, Runtime, Url};
use tauri_plugin_shell::ShellExt;

/// Only https links into the project's own GitHub account.
fn is_allowed(url: &Url) -> bool {
    url.scheme() == "https"
        && url.host_str() == Some("github.com")
        && url.username().is_empty()
        && url.password().is_none()
        && url.port().is_none()
        && url
            .path_segments()
            .and_then(|mut segments| segments.next())
            .is_some_and(|owner| owner.eq_ignore_ascii_case("pvnc228"))
}

pub fn on_new_window<R: Runtime>(app: &AppHandle<R>, url: Url) -> NewWindowResponse<R> {
    if is_allowed(&url) {
        // The scope check of the deprecated API only applies to calls from the page; this is Rust.
        #[allow(deprecated)]
        let _ = app.shell().open(url.as_str(), None);
    }
    NewWindowResponse::Deny
}

#[cfg(test)]
mod tests {
    use super::*;

    fn allowed(url: &str) -> bool {
        is_allowed(&url.parse().unwrap())
    }

    #[test]
    fn project_github_links_are_allowed() {
        assert!(allowed("https://github.com/pvnc228/local-coding-agent"));
        assert!(allowed("https://github.com/pvnc228/local-coding-agent/issues?q=a&b=c#top"));
        assert!(allowed("https://github.com/PVNC228/local-coding-agent/blob/main/CHANGELOG.md"));
        assert!(allowed("https://GITHUB.com/pvnc228"));
    }

    #[test]
    fn everything_else_is_refused() {
        for url in [
            "http://github.com/pvnc228/local-coding-agent",
            "https://github.com/ggml-org/llama.cpp",
            "https://github.com/pvnc2280/x",
            "https://github.com/",
            "https://github.com/pvnc228evil/x",
            "https://github.com.evil.example/pvnc228/x",
            "https://evil.example/github.com/pvnc228",
            "https://github.com@evil.example/pvnc228",
            "https://pvnc228@github.com/pvnc228/x",
            "https://github.com:8443/pvnc228/x",
            "https://github.com./pvnc228/x",
            "https://docs.ollama.com/faq",
            "https://ollama.ai",
            "http://127.0.0.1:11434",
            "javascript:alert(1)",
            "file:///C:/Windows/System32/calc.exe",
            "ms-msdt:/id",
            "about:blank",
        ] {
            assert!(!allowed(url), "{url}");
        }
    }

    #[test]
    fn path_tricks_do_not_leave_the_account() {
        assert!(!allowed("https://github.com/pvnc228/../evil/x"));
        assert!(!allowed("https://github.com/%2e%2e/evil/x"));
        assert!(!allowed("https://github.com/pvnc228%2f..%2fevil"));
    }
}
