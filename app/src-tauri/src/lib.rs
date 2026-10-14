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

/// Where the engine lives. An installed app carries it in its resources (runtime/python + engine/, assembled by
/// scripts/package/assemble.py); a dev build uses the source tree and its .venv. VYUM_ROOT overrides the dev path.
struct EngineLayout {
    python: PathBuf,
    cwd: PathBuf,
    resources: Option<PathBuf>,
}

fn engine_layout(app: &tauri::App) -> EngineLayout {
    if let Ok(res) = app.path().resource_dir() {
        let python = if cfg!(windows) { res.join("runtime/python/python.exe") } else { res.join("runtime/python/bin/python3") };
        if python.exists() {
            return EngineLayout { python, cwd: res.clone(), resources: Some(res) };
        }
    }
    let root = std::env::var("VYUM_ROOT")
        .map(PathBuf::from)
        .unwrap_or_else(|_| PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../.."));
    let python = if cfg!(windows) { root.join(".venv/Scripts/python.exe") } else { root.join(".venv/bin/python") };
    EngineLayout { python, cwd: root, resources: None }
}

fn start_engine(state: EngineState, layout: EngineLayout) {
    let python = layout.python.clone();
    let mut cmd = Command::new(&python);
    cmd.args(["-m", "engine.main", "--port", "0"])
        .current_dir(&layout.cwd)
        .env("VYUM_TOKEN", &state.token)
        // UTF-8 everywhere (Windows would otherwise read and write files in its legacy code page)
        .env("PYTHONUTF8", "1")
        .env("PYTHONNOUSERSITE", "1")
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit());
    if let Some(res) = &layout.resources {
        cmd.env("VYUM_RESOURCES", res).env("PYTHONDONTWRITEBYTECODE", "1");
    }
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(0x0800_0000); // CREATE_NO_WINDOW: no console window behind the app
    }
    let spawned = cmd.spawn();
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

/// POST /shutdown to the engine, waiting up to a few seconds (plain HTTP over localhost: no extra dependency).
fn shutdown_request(port: u16, token: &str) {
    use std::io::{Read, Write};
    use std::time::Duration;
    let Ok(mut s) = std::net::TcpStream::connect_timeout(&([127, 0, 0, 1], port).into(), Duration::from_secs(2)) else {
        return;
    };
    let _ = s.set_read_timeout(Some(Duration::from_secs(6)));
    let req = format!(
        "POST /shutdown HTTP/1.1\r\nHost: 127.0.0.1\r\nx-vyum-token: {token}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
    );
    if s.write_all(req.as_bytes()).is_ok() {
        let mut buf = [0u8; 256];
        let _ = s.read(&mut buf);
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let state = EngineState { inner: Arc::default(), token: uuid::Uuid::new_v4().simple().to_string() };
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .manage(state.clone())
        .invoke_handler(tauri::generate_handler![engine_info])
        .setup(move |app| {
            let layout = engine_layout(app);
            start_engine(state.clone(), layout);
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building Vyum");

    app.run(|handle, event| {
        if let RunEvent::Exit = event {
            let state = handle.state::<EngineState>();
            let mut e = state.inner.lock().unwrap();
            if let Some(port) = e.port {
                shutdown_request(port, &state.token); // the engine stops Ollama and the PDF editor host first
            }
            if let Some(mut child) = e.child.take() {
                let _ = child.kill();
                let _ = child.wait();
            }
        }
    });
}
