//! Tauri shell: launches the Python engine on 127.0.0.1 with a per-launch token and stops it on exit.
use std::io::{BufRead, BufReader};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};

use serde::Serialize;
use tauri::{Manager, RunEvent};

#[derive(Default)]
struct Engine {
    child: Option<Child>,
    port: Option<u16>,
    error: Option<String>,
}

#[derive(Clone)]
struct EngineState {
    inner: Arc<Mutex<Engine>>,
    token: String,
}

#[derive(Serialize)]
struct EngineInfo {
    ready: bool,
    port: Option<u16>,
    token: String,
    error: Option<String>,
}

#[tauri::command]
fn engine_info(state: tauri::State<EngineState>) -> EngineInfo {
    let e = state.inner.lock().unwrap();
    EngineInfo { ready: e.port.is_some(), port: e.port, token: state.token.clone(), error: e.error.clone() }
}

/// Repo root holding engine/ and .venv/. Dev builds use the source tree; VYUM_ROOT overrides it.
fn engine_root() -> PathBuf {
    std::env::var("VYUM_ROOT")
        .map(PathBuf::from)
        .unwrap_or_else(|_| PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../.."))
}

fn start_engine(state: EngineState) {
    let root = engine_root();
    let python = root.join(".venv/bin/python");
    let spawned = Command::new(&python)
        .args(["-m", "engine.main", "--port", "0"])
        .current_dir(&root)
        .env("VYUM_TOKEN", &state.token)
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit())
        .spawn();
    let mut child = match spawned {
        Ok(c) => c,
        Err(err) => {
            state.inner.lock().unwrap().error = Some(format!("could not start engine at {}: {err}", python.display()));
            return;
        }
    };
    let stdout = child.stdout.take().expect("piped stdout");
    state.inner.lock().unwrap().child = Some(child);
    std::thread::spawn(move || {
        for line in BufReader::new(stdout).lines().map_while(Result::ok) {
            if let Some(port) = line.strip_prefix("VYUM_READY ").and_then(|p| p.trim().parse().ok()) {
                state.inner.lock().unwrap().port = Some(port);
            }
        }
        let mut e = state.inner.lock().unwrap();
        if e.port.is_none() {
            e.error = Some("engine exited before it was ready".into());
        }
    });
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let state = EngineState { inner: Arc::default(), token: uuid::Uuid::new_v4().simple().to_string() };
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .manage(state.clone())
        .invoke_handler(tauri::generate_handler![engine_info])
        .setup(move |_| {
            start_engine(state.clone());
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building Vyum");

    app.run(|handle, event| {
        if let RunEvent::Exit = event {
            let state = handle.state::<EngineState>();
            let mut e = state.inner.lock().unwrap();
            if let Some(mut child) = e.child.take() {
                let _ = child.kill();
                let _ = child.wait();
            }
        }
    });
}
