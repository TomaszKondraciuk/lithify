//! lithify-agent: the on-speaker half of Lithify (Spotify Connect via librespot on Lithe Audio
//! speakers built on the Libre LS9 module: Marvell Berlin SoC, Cast 1.52 firmware).
//!
//! Subcommands:
//!   exec-file FILE PROG    exec PROG (librespot) with the arguments listed in FILE, after
//!                          restoring default signal handling (form used in the Cast process list)
//!   exec [--args-file FILE] -- PROG [ARGS..]   the same with explicit arguments
//!   onevent                librespot `--onevent` hook; with `--emit-sink-events` it runs right
//!                          before librespot opens the ALSA device and frees it from Libre sources
//!   run                    watchdog loop (official Spotify spin/respawn, source conflicts,
//!                          silent-start detection, fail-fast routes, librespot health, settings
//!                          on trial) and the web page
//!   status                 one-shot diagnostics
//!   luci MBID PAYLOAD      send one LUCI SET command and print what comes back
//!
//! Every file lives next to the binary: agent.conf, librespot.args, VERSIONS, alsa/, cache/, prev/.
//!
//! Everything runs as the Cast process_manager user (uid 1000). The few actions that need root
//! (restarting the official Spotify daemon, Wi-Fi driver tuning) go through the speaker's own
//! passwordless root console on 127.0.0.1:23, one `exec mksh <script>` line per action: that
//! console runs lines through an ash that spins forever after any forked command, while mksh
//! reaps its children correctly.

use std::collections::HashMap;
use std::env;
use std::fs::{self, OpenOptions};
use std::io::{self, BufRead, BufReader, Read, Write};
use std::net::{Ipv4Addr, SocketAddr, SocketAddrV4, TcpStream, ToSocketAddrs};
use std::os::unix::fs::{MetadataExt, OpenOptionsExt};
use std::os::unix::process::{CommandExt, ExitStatusExt};
use std::path::{Path, PathBuf};
use std::process::{self, Child, Command, Stdio};
use std::sync::atomic::{AtomicU32, AtomicUsize, Ordering};
use std::sync::{mpsc, Arc, Mutex, MutexGuard, OnceLock};
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

mod settings;
mod web;

/// Install directory: where this binary lives (/lsync/lithify on LS9). Looked up once.
fn base_dir() -> &'static Path {
    static BASE: OnceLock<PathBuf> = OnceLock::new();
    BASE.get_or_init(|| {
        env::current_exe()
            .ok()
            .and_then(|p| p.parent().map(Path::to_path_buf))
            .unwrap_or_else(|| PathBuf::from("/lsync/lithify"))
    })
}
/// Identifies this build (and the web page embedded in it); set by `lithify build`.
const BUILD: &str = match option_env!("LITHIFY_BUILD") {
    Some(b) => b,
    None => "dev",
};
const LOG_PATH: &str = "/tmp/lithify-agent.log";
const LOG_MAX_BYTES: u64 = 256 * 1024;
const PCM_STATUS: &str = "/proc/asound/card0/pcm0p/sub0/status";
const PCM_NODE: &str = "/dev/snd/pcmC0D0p";

const LUCI_ADDR: SocketAddr = SocketAddr::V4(SocketAddrV4::new(Ipv4Addr::LOCALHOST, 7777));
const ROOT_CONSOLE_ADDR: SocketAddr = SocketAddr::V4(SocketAddrV4::new(Ipv4Addr::LOCALHOST, 23));
const CMD_GET: u8 = 1;
const CMD_SET: u8 = 2;
const MB_REGISTER: u16 = 3;
const MB_TRANSPORT: u16 = 40;
const MB_SOURCE: u16 = 50;
const MB_PLAY_STATE: u16 = 51;

const SPOTIFY_RESTART: &str = "killall -9 spotifyhifi\n\
sleep 1\n\
cd /system/bin\n\
LD_PRELOAD=/system/lib/libm.so ./spotifyhifi </dev/null >/dev/null 2>&1 &\n\
sleep 2\n\
pidof spotifyhifi\n";

// Stopping it for good: it gets a moment to sign off (it leaves the device lists sooner), and
// nothing on the speaker starts it again.
const SPOTIFY_STOP: &str = "killall spotifyhifi\n\
sleep 2\n\
killall -9 spotifyhifi\n\
sleep 1\n\
pidof spotifyhifi\n";

/// Present while the agent keeps the official client stopped (hide_official_spotify), so it
/// comes back when that ends, also after the agent restarted (gone with a reboot, like the hiding).
const HIDDEN_FILE: &str = "/tmp/lithify-official.hidden";

// Same launch line LibreManager uses; it never respawns the daemon on its own.
const SPOTIFY_LAUNCH: &str = "pidof spotifyhifi && exit 0\n\
cd /system/bin\n\
LD_PRELOAD=/system/lib/libm.so ./spotifyhifi </dev/null >/dev/null 2>&1 &\n\
sleep 2\n\
pidof spotifyhifi\n";

/// Restarting or launching official Spotify, one at a time (the watchdog and the web page).
fn official_spotify(script: &str, wait: Duration) -> io::Result<String> {
    static ONE: Mutex<()> = Mutex::new(());
    let _one = ONE.lock().unwrap_or_else(|e| e.into_inner());
    root_run(script, wait)
}

extern "C" {
    fn sysconf(name: i32) -> std::ffi::c_long;
    fn signal(signum: i32, handler: usize) -> usize;
    fn sigprocmask(how: i32, set: *const SigSet, oldset: *mut SigSet) -> i32;
    fn kill(pid: i32, sig: i32) -> i32;
    fn prctl(option: i32, ...) -> i32;
    fn getppid() -> i32;
    fn ioctl(fd: i32, request: std::ffi::c_ulong, ...) -> i32;
    fn nice(inc: i32) -> i32;
    fn flock(fd: i32, operation: i32) -> i32;
    fn fcntl(fd: i32, cmd: i32, ...) -> i32;
}
const F_GETFD: i32 = 1;
const F_SETFD: i32 = 2;
const FD_CLOEXEC: i32 = 1;
const SC_CLK_TCK: i32 = 2;
const SIG_DFL: usize = 0;
const SIG_SETMASK: i32 = 2;
const SIGKILL: i32 = 9;
const SIGPIPE: i32 = 13;
const SIGTERM: i32 = 15;
const SIGCHLD: i32 = 17;
const PR_SET_PDEATHSIG: i32 = 1;
const ESRCH: i32 = 3;
const EWOULDBLOCK: i32 = 11;
const LOCK_EX: i32 = 2;
const LOCK_NB: i32 = 4;

/// A sigset_t as the C library lays it out (128 bytes in musl and glibc), aligned like the
/// `unsigned long`s it is made of.
#[repr(C, align(8))]
struct SigSet([u8; 128]);

/// Send `sig` to `pid`. Never to pid 0, 1 or a negative one (this process group, init, every
/// process): a pid that went wrong somewhere must not become one of those.
fn send_signal(pid: i32, sig: i32) -> io::Result<()> {
    if pid <= 1 {
        return Err(io::Error::new(io::ErrorKind::InvalidInput, format!("refusing to signal pid {pid}")));
    }
    // SAFETY: kill(2) takes two integers and touches no memory of this process.
    if unsafe { kill(pid, sig) } == 0 {
        Ok(())
    } else {
        Err(io::Error::last_os_error())
    }
}

/// `send_signal`, logging a failure other than "already gone".
fn signal_or_log(pid: i32, sig: i32) {
    if let Err(e) = send_signal(pid, sig) {
        if e.raw_os_error() != Some(ESRCH) {
            log(&format!("cannot signal pid {pid} ({sig}): {e}"));
        }
    }
}

/// Start a long-running thread. A thread that cannot start is logged instead of taking the
/// whole agent down (`thread::spawn` panics, and panics abort).
fn spawn_named(name: &str, f: impl FnOnce() + Send + 'static) -> bool {
    match thread::Builder::new().name(name.to_string()).stack_size(256 * 1024).spawn(f) {
        Ok(_) => true,
        Err(e) => {
            log(&format!("cannot start the {name} thread: {e}"));
            false
        }
    }
}

// ── logging ──────────────────────────────────────────────────────────────

fn civil_from_days(z: i64) -> (i64, u32, u32) {
    let z = z + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let doe = (z - era * 146_097) as u64;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = (doy - (153 * mp + 2) / 5 + 1) as u32;
    let m = if mp < 10 { mp + 3 } else { mp - 9 } as u32;
    let y = yoe as i64 + era * 400;
    (if m <= 2 { y + 1 } else { y }, m, d)
}

fn utc_now() -> String {
    let secs = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0);
    let (y, m, d) = civil_from_days(secs.div_euclid(86_400));
    let sod = secs.rem_euclid(86_400);
    format!(
        "{y:04}-{m:02}-{d:02} {:02}:{:02}:{:02}Z",
        sod / 3600,
        (sod % 3600) / 60,
        sod % 60
    )
}

/// The time as HTTP gives it ("Wed, 07 Oct 2026 13:45:00 GMT").
fn http_date() -> String {
    http_date_of(SystemTime::now().duration_since(UNIX_EPOCH).map_or(0, |d| d.as_secs() as i64))
}

fn http_date_of(secs: i64) -> String {
    const DAYS: [&str; 7] = ["Thu", "Fri", "Sat", "Sun", "Mon", "Tue", "Wed"]; // 1970-01-01 was a Thursday
    const MONTHS: [&str; 12] = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    let days = secs.div_euclid(86_400);
    let (y, m, d) = civil_from_days(days);
    let sod = secs.rem_euclid(86_400);
    format!(
        "{}, {d:02} {} {y:04} {:02}:{:02}:{:02} GMT",
        DAYS[days.rem_euclid(7) as usize],
        MONTHS.get((m as usize).saturating_sub(1)).unwrap_or(&"Jan"),
        sod / 3600,
        (sod % 3600) / 60,
        sod % 60
    )
}

// In `run`, one thread writes the log: a slow flash, a full stderr pipe or a rotation must never
// hold up the watchdog or the page. The short-lived subcommands write it themselves.

/// Lines waiting for the writer; more are dropped (and counted) rather than waited for.
const LOG_QUEUE: usize = 256;

enum LogMsg {
    Line(String),
    /// Answered once everything queued before it is written.
    Flush(mpsc::SyncSender<()>),
}

static LOG_TX: OnceLock<mpsc::SyncSender<LogMsg>> = OnceLock::new();
static LOG_DROPPED: AtomicU32 = AtomicU32::new(0);

fn log(msg: &str) {
    let line = format!("{} lithify-agent[{}] {}\n", utc_now(), process::id(), msg);
    let Some(tx) = LOG_TX.get() else { return write_log_now(&line) };
    match tx.try_send(LogMsg::Line(line)) {
        Ok(()) => {}
        Err(mpsc::TrySendError::Full(_)) => {
            LOG_DROPPED.fetch_add(1, Ordering::Relaxed);
        }
        Err(mpsc::TrySendError::Disconnected(LogMsg::Line(line))) => write_log_now(&line),
        Err(mpsc::TrySendError::Disconnected(_)) => {}
    }
}

/// One line, written by the caller (subcommands other than `run`).
fn write_log_now(line: &str) {
    let _ = io::stderr().write_all(line.as_bytes());
    if fs::metadata(LOG_PATH).map(|m| m.len() > LOG_MAX_BYTES).unwrap_or(false) {
        let _ = fs::rename(LOG_PATH, format!("{LOG_PATH}.1"));
    }
    if let Ok(mut f) = OpenOptions::new().create(true).append(true).open(LOG_PATH) {
        let _ = f.write_all(line.as_bytes());
    }
}

/// Start the writer thread; until it runs (or when it cannot start) callers write themselves.
fn start_log_writer() {
    let (tx, rx) = mpsc::sync_channel(LOG_QUEUE);
    let started = thread::Builder::new().name("log".into()).stack_size(64 * 1024).spawn(move || {
        let mut file = None;
        for msg in rx {
            let line = match msg {
                LogMsg::Line(line) => line,
                LogMsg::Flush(done) => {
                    let _ = done.try_send(());
                    continue;
                }
            };
            let dropped = LOG_DROPPED.swap(0, Ordering::Relaxed);
            let text = if dropped > 0 {
                format!("{} lithify-agent[{}] ({dropped} log lines dropped: too many at once)\n{line}", utc_now(), process::id())
            } else {
                line
            };
            // The file first: a stderr pipe that is not read any more may block this thread
            // (callers only lose lines then), but what reached the file stays.
            append_log(&mut file, LOG_PATH, LOG_MAX_BYTES, text.as_bytes());
            let _ = io::stderr().write_all(text.as_bytes());
        }
    });
    if started.is_ok() {
        let _ = LOG_TX.set(tx);
    }
}

/// Append to the log file kept open in `file` (with its inode), rotating it past `max` bytes.
/// Other processes (the exec wrapper, the onevent hook) append and rotate too: the file is
/// opened again when the name no longer leads to the open one.
fn append_log(file: &mut Option<(fs::File, u64)>, path: &str, max: u64, bytes: &[u8]) {
    let now = fs::metadata(path).ok();
    if now.as_ref().is_some_and(|m| m.len() > max) {
        let _ = fs::rename(path, format!("{path}.1"));
        *file = None;
    } else if file.as_ref().is_some_and(|(_, ino)| now.as_ref().map(|m| m.ino()) != Some(*ino)) {
        *file = None;
    }
    if file.is_none() {
        *file = OpenOptions::new()
            .create(true)
            .append(true)
            .open(path)
            .ok()
            .and_then(|f| f.metadata().ok().map(|m| (f, m.ino())));
    }
    if let Some((f, _)) = file {
        let _ = f.write_all(bytes);
    }
}

/// Wait a moment for the writer to write what is queued: before the agent exits.
fn log_flush() {
    let Some(tx) = LOG_TX.get() else { return };
    let deadline = Instant::now() + Duration::from_millis(500);
    let (done_tx, done_rx) = mpsc::sync_channel(1);
    let mut msg = LogMsg::Flush(done_tx);
    loop {
        match tx.try_send(msg) {
            Ok(()) => break,
            Err(mpsc::TrySendError::Full(m)) if Instant::now() < deadline => {
                msg = m;
                thread::sleep(Duration::from_millis(10));
            }
            Err(_) => return,
        }
    }
    let _ = done_rx.recv_timeout(deadline.saturating_duration_since(Instant::now()));
}

// ── state shared with the web page ───────────────────────────────────────

/// What the watchdog threads have seen and done; shown on the web page.
struct Shared {
    libre_source: String,
    libre_state: String,
    spotify_restarts: u32,
    spotify_launches: u32,
    librespot_stops: u32,
    silent_starts: u32,
    shells_stopped: u32,
    /// The official client is kept stopped while librespot works (hide_official_spotify).
    official_hidden: bool,
    events: Vec<String>,
}

static SHARED: Mutex<Shared> = Mutex::new(Shared {
    libre_source: String::new(),
    libre_state: String::new(),
    spotify_restarts: 0,
    spotify_launches: 0,
    librespot_stops: 0,
    silent_starts: 0,
    shells_stopped: 0,
    official_hidden: false,
    events: Vec::new(),
});

fn shared() -> MutexGuard<'static, Shared> {
    SHARED.lock().unwrap_or_else(|e| e.into_inner())
}

/// Log a watchdog action and keep the last few for the web page.
fn event(msg: &str) {
    log(msg);
    let mut s = shared();
    if s.events.len() >= 20 {
        s.events.remove(0);
    }
    s.events.push(format!("{} {msg}", utc_now()));
}

// ── config ───────────────────────────────────────────────────────────────

/// agent.conf, rendered by `lithify` from config.toml (key=value, one per line).
#[derive(Clone)]
struct Config {
    spin_threshold: f64,
    spin_seconds: u64,
    spin_cooldown: u64,
    respawn_missing: bool,
    respawn_grace: u64,
    hide_official: bool,
    silent_seconds: u64,
    silent_action: String,
    conflict_stop_librespot: bool,
    wifi_scancfg: String,
    fastfail_hosts: Vec<String>,
    fastfail_routes: Vec<String>,
    ui: bool,
    ui_port: u16,
    ui_pin: String,
    companion_url: String,
    speaker_id: String,
}

impl Config {
    #[cfg(test)]
    fn default_for_tests(pin: &str) -> Config {
        let mut c = Config::parse("");
        c.ui_pin = pin.to_string();
        c
    }

    /// From the speaker's settings, or from agent.conf on installs made before them.
    fn load() -> Config {
        if let Some((s, install)) = settings::load() {
            return Config::from_settings(&s, &install);
        }
        Config::parse(&fs::read_to_string(base_dir().join("agent.conf")).unwrap_or_default())
    }

    fn from_settings(s: &settings::Settings, install: &settings::Install) -> Config {
        let mut text = String::new();
        for o in settings::schema().iter().filter(|o| o.section == "agent") {
            let v = s.get(o.key);
            let v = match (o.kind, v) {
                (settings::Kind::Bool, "true") => "1",
                (settings::Kind::Bool, _) => "0",
                _ => v,
            };
            text.push_str(&format!("{}={v}\n", o.key));
        }
        text.push_str(&format!("companion_url={}\nspeaker_id={}\n", install.companion_url, install.speaker_id));
        Config::parse(&text)
    }

    fn parse(text: &str) -> Config {
        let mut c = Config {
            spin_threshold: 0.85,
            spin_seconds: 60,
            spin_cooldown: 600,
            respawn_missing: true,
            respawn_grace: 120,
            hide_official: false,
            silent_seconds: 8,
            silent_action: "log".into(),
            conflict_stop_librespot: true,
            wifi_scancfg: String::new(),
            fastfail_hosts: Vec::new(),
            fastfail_routes: Vec::new(),
            ui: true,
            ui_port: 8090,
            ui_pin: String::new(),
            companion_url: String::new(),
            speaker_id: String::new(),
        };
        // agent.conf of installs made before the settings is not checked by them: every number is
        // brought into the range settings.tsv gives it (a NaN or a 0 must not disable a watch).
        let range = |key: &str, lo: i64, hi: i64| {
            let (a, b) = settings::opt(key).and_then(|o| o.range()).unwrap_or((lo, hi));
            (a.min(b), a.max(b))
        };
        let int = |key: &str, v: &str, current: u64, lo: i64, hi: i64| -> u64 {
            let (lo, hi) = range(key, lo, hi);
            v.parse::<i64>().map_or(current, |n| n.clamp(lo.max(0), hi.max(0)) as u64)
        };
        for raw in text.lines() {
            let line = raw.trim();
            if line.is_empty() || line.starts_with('#') {
                continue;
            }
            let Some((k, v)) = line.split_once('=') else { continue };
            let (k, v) = (k.trim(), v.trim());
            match k {
                "spin_threshold_pct" => {
                    if let Some(x) = v.parse::<f64>().ok().filter(|x| x.is_finite()) {
                        let (lo, hi) = range(k, 30, 100);
                        c.spin_threshold = x.clamp(lo as f64, hi as f64) / 100.0;
                    }
                }
                "spin_seconds" => c.spin_seconds = int(k, v, c.spin_seconds, 20, 3600),
                "spin_cooldown_seconds" => c.spin_cooldown = int(k, v, c.spin_cooldown, 0, 86_400),
                "respawn_official_spotify" => c.respawn_missing = v == "1",
                "respawn_grace_seconds" => c.respawn_grace = int(k, v, c.respawn_grace, 30, 3600),
                "hide_official_spotify" => c.hide_official = v == "1",
                "silent_start_seconds" => c.silent_seconds = int(k, v, c.silent_seconds, 3, 600),
                "silent_start_action" => {
                    if settings::opt(k).is_none_or(|o| o.choices().contains(&v)) {
                        c.silent_action = v.to_string();
                    }
                }
                "conflict_stop_librespot" => c.conflict_stop_librespot = v == "1",
                "wifi_scancfg" => c.wifi_scancfg = v.to_string(),
                "fastfail_hosts" => c.fastfail_hosts = v.split_whitespace().map(str::to_string).collect(),
                "fastfail_routes" => c.fastfail_routes = v.split_whitespace().map(str::to_string).collect(),
                "ui" => c.ui = v == "1",
                // A port outside the range is refused rather than moved (the page stays where it was).
                "ui_port" => {
                    let (lo, hi) = range(k, 1024, 65_535);
                    c.ui_port = v.parse().ok().filter(|p| (lo..=hi).contains(&i64::from(*p))).unwrap_or(c.ui_port)
                }
                "ui_pin" => c.ui_pin = v.to_string(),
                "companion_url" => c.companion_url = v.trim_end_matches('/').to_string(),
                "speaker_id" => c.speaker_id = v.to_string(),
                _ => log(&format!("config: unknown key {k:?}")),
            }
        }
        c
    }
}

// ── /proc helpers ────────────────────────────────────────────────────────

fn clk_tck() -> f64 {
    // SAFETY: sysconf(3) only reads a system constant.
    let t = unsafe { sysconf(SC_CLK_TCK) };
    if t > 0 { t as f64 } else { 100.0 }
}

fn comm_of(pid: i32) -> Option<String> {
    fs::read_to_string(format!("/proc/{pid}/comm")).ok().map(|c| c.trim_end().to_string())
}

/// Every process named `comm`, lowest pid first (a full /proc scan: one file per process).
fn find_pids(comm: &str) -> Vec<i32> {
    let mut out = Vec::new();
    if let Ok(rd) = fs::read_dir("/proc") {
        for e in rd.flatten() {
            let name = e.file_name();
            let Some(pid) = name.to_str().and_then(|s| s.parse::<i32>().ok()) else { continue };
            if comm_of(pid).as_deref() == Some(comm) {
                out.push(pid);
            }
        }
    }
    out.sort_unstable();
    out
}

/// "Not running" is believed this long: each look for a missing process scans all of /proc.
const NOT_RUNNING_TTL: Duration = Duration::from_secs(3);

/// The process named `comm`. The one found last time is checked first, so the watchdog's
/// polling reads one file instead of scanning /proc.
fn find_pid(comm: &str) -> Option<i32> {
    static LAST: Mutex<Vec<(String, Option<i32>, Instant)>> = Mutex::new(Vec::new());
    let known = {
        let last = LAST.lock().unwrap_or_else(|e| e.into_inner());
        last.iter().find(|(c, _, _)| c == comm).map(|(_, pid, at)| (*pid, *at))
    };
    match known {
        Some((Some(pid), _)) if comm_of(pid).as_deref() == Some(comm) && alive(pid) => return Some(pid),
        Some((None, at)) if at.elapsed() < NOT_RUNNING_TTL => return None,
        _ => {}
    }
    // Scanned without the lock: other threads' lookups must not wait for this one.
    let pid = find_pids(comm).into_iter().find(|p| alive(*p));
    let mut last = LAST.lock().unwrap_or_else(|e| e.into_inner());
    last.retain(|(c, _, _)| c != comm);
    last.push((comm.to_string(), pid, Instant::now()));
    pid
}

/// The fields of a /proc stat file that the agent uses.
struct Stat {
    /// R, S, D, Z ...
    state: char,
    ppid: i32,
    /// utime + stime, in clock ticks
    ticks: u64,
    /// start time after boot, in clock ticks
    start: u64,
}

fn proc_stat(path: &str) -> Option<Stat> {
    parse_stat(&fs::read_to_string(path).ok()?)
}

fn parse_stat(stat: &str) -> Option<Stat> {
    // The command name may contain spaces and parentheses: the fields start after the last ')'.
    // Counted from the state (0): ppid 1, utime 11, stime 12, starttime 19.
    let mut f = stat.get(stat.rfind(')')? + 1..)?.split_whitespace();
    let state = f.next()?.chars().next()?;
    let ppid = f.next()?.parse().ok()?;
    let utime: u64 = f.nth(9)?.parse().ok()?;
    let stime: u64 = f.next()?.parse().ok()?;
    let start = f.nth(6)?.parse().ok()?;
    Some(Stat { state, ppid, ticks: utime.saturating_add(stime), start })
}

/// Running, not just a zombie waiting for its parent (a zombie keeps its /proc entry).
fn alive(pid: i32) -> bool {
    proc_stat(&format!("/proc/{pid}/stat")).is_some_and(|s| s.state != 'Z')
}

/// utime+stime ticks of every thread of `pid`.
fn thread_ticks(pid: i32) -> HashMap<i32, u64> {
    let mut out = HashMap::new();
    let Ok(rd) = fs::read_dir(format!("/proc/{pid}/task")) else { return out };
    for e in rd.flatten() {
        let Some(tid) = e.file_name().to_str().and_then(|s| s.parse::<i32>().ok()) else { continue };
        if let Some(st) = proc_stat(&format!("/proc/{pid}/task/{tid}/stat")) {
            out.insert(tid, st.ticks);
        }
    }
    out
}

fn system_uptime() -> Option<f64> {
    fs::read_to_string("/proc/uptime").ok()?.split_whitespace().next()?.parse().ok()
}

/// Seconds since `pid` started.
fn proc_uptime_s(pid: i32) -> Option<u64> {
    let st = proc_stat(&format!("/proc/{pid}/stat"))?;
    Some((system_uptime()? - st.start as f64 / clk_tck()).max(0.0) as u64)
}

/// The arguments of a running process (without argv[0]).
fn proc_args(pid: i32) -> Vec<String> {
    let raw = fs::read(format!("/proc/{pid}/cmdline")).unwrap_or_default();
    let raw = raw.strip_suffix(&[0]).unwrap_or(&raw);
    if raw.is_empty() {
        return Vec::new();
    }
    raw.split(|b| *b == 0).skip(1).map(|a| String::from_utf8_lossy(a).into_owned()).collect()
}

/// Does `pid` have the PCM open? For what the page shows: no fd scan while the sampler saw
/// the PCM closed moments ago (nobody holds it then). Decisions use `holds_pcm_now`.
fn holds_pcm(pid: i32) -> bool {
    pcm_busy_seen() != Some(false) && holds_pcm_now(pid)
}

fn holds_pcm_now(pid: i32) -> bool {
    let Ok(rd) = fs::read_dir(format!("/proc/{pid}/fd")) else { return false };
    rd.flatten()
        .any(|e| fs::read_link(e.path()).map(|p| p.as_os_str() == PCM_NODE).unwrap_or(false))
}

/// Is the PCM open? Read here and now, for the onevent hook (it has no sampler). A read that
/// does not come back in time counts as free: librespot must not wait for a stuck driver.
fn pcm_busy_now() -> bool {
    let got = read_within(PCM_STATUS, Duration::from_secs(1));
    got.fresh && got.value.is_some_and(|s| s.trim() != "closed")
}

// ── calls that may hang ──────────────────────────────────────────────────
// A tool or a read that ends in a stuck driver (ALSA, Wi-Fi) may never return. Such a call runs
// on a helper thread and its caller waits a bounded time. One call of a kind at a time: while
// one is stuck, callers get the last result at once instead of stacking up more stuck threads.
// And only a few helpers in all.

const MAX_HELPERS: usize = 4;
static HELPERS: AtomicUsize = AtomicUsize::new(0);
/// A result this old is not handed out for a call that is stuck.
const LAST_RESULT_MAX_AGE: Duration = Duration::from_secs(600);
/// Most of a tool's output that is kept (its end).
const MAX_OUTPUT: usize = 512 * 1024;

/// Take one of `max` places counted in `n`; false when all are taken.
fn take_place(n: &AtomicUsize, max: usize) -> bool {
    let mut now = n.load(Ordering::Acquire);
    while now < max {
        match n.compare_exchange_weak(now, now + 1, Ordering::AcqRel, Ordering::Acquire) {
            Ok(_) => return true,
            Err(seen) => now = seen,
        }
    }
    false
}

/// A helper thread's place among the `MAX_HELPERS`, given back when the thread ends.
struct HelperSlot;

impl HelperSlot {
    fn take() -> Option<HelperSlot> {
        // Made only when a place was taken: dropping a slot gives one back.
        take_place(&HELPERS, MAX_HELPERS).then(|| HelperSlot)
    }
}

impl Drop for HelperSlot {
    fn drop(&mut self) {
        HELPERS.fetch_sub(1, Ordering::AcqRel);
    }
}

struct FlightState {
    key: String,
    busy: bool,
    /// The last result that came back, and when.
    last: Option<(Instant, Option<String>)>,
    /// When "took too long" was last logged for this call.
    slow_logged: Option<Instant>,
}

static FLIGHTS: Mutex<Vec<FlightState>> = Mutex::new(Vec::new());

fn flights() -> MutexGuard<'static, Vec<FlightState>> {
    FLIGHTS.lock().unwrap_or_else(|e| e.into_inner())
}

/// What a guarded call gave: its own result when `fresh`, else the last one an earlier call of
/// the same kind left (None: none, or too old).
struct Got {
    value: Option<String>,
    fresh: bool,
}

/// The right to make call `key`. The helper thread owns it, so it is given back only when the
/// call has really returned (not when its caller stopped waiting).
struct Flight {
    key: String,
    landed: bool,
}

impl Flight {
    /// Start call `key`, unless one is still under way: then Err with the last result.
    fn take_off(key: &str) -> Result<Flight, Option<String>> {
        let mut all = flights();
        match all.iter_mut().find(|f| f.key == key) {
            Some(f) if f.busy => return Err(last_of(f)),
            Some(f) => f.busy = true,
            None => {
                if all.len() >= 64 {
                    all.retain(|f| f.busy); // keys are a fixed handful; this never happens
                }
                all.push(FlightState { key: key.to_string(), busy: true, last: None, slow_logged: None });
            }
        }
        Ok(Flight { key: key.to_string(), landed: false })
    }

    /// The call returned `v`: keep it as the last result and let the next call start.
    fn land(mut self, v: &Option<String>) {
        self.landed = true;
        if let Some(f) = flights().iter_mut().find(|f| f.key == self.key) {
            f.busy = false;
            f.last = Some((Instant::now(), v.clone()));
        }
    }
}

impl Drop for Flight {
    fn drop(&mut self) {
        if !self.landed {
            if let Some(f) = flights().iter_mut().find(|f| f.key == self.key) {
                f.busy = false;
            }
        }
    }
}

fn last_of(f: &FlightState) -> Option<String> {
    f.last.as_ref().filter(|(at, _)| at.elapsed() < LAST_RESULT_MAX_AGE).and_then(|(_, v)| v.clone())
}

fn last_result(key: &str) -> Option<String> {
    flights().iter().find(|f| f.key == key).and_then(last_of)
}

/// A call did not come back in time: say so, at most once a minute per kind.
fn too_slow(key: &str, timeout: Duration) {
    let say = {
        let mut all = flights();
        match all.iter_mut().find(|f| f.key == key) {
            Some(f) if f.slow_logged.is_none_or(|t| t.elapsed() >= Duration::from_secs(60)) => {
                f.slow_logged = Some(Instant::now());
                true
            }
            _ => false,
        }
    };
    if say {
        log(&format!("{key}: no answer within {} ms (a stuck driver?); not asked again until it returns", timeout.as_millis()));
    }
}

/// `work()` on a helper thread, waited for at most `timeout` (see above).
fn guarded(key: &str, timeout: Duration, work: impl FnOnce() -> Option<String> + Send + 'static) -> Got {
    let flight = match Flight::take_off(key) {
        Ok(f) => f,
        Err(last) => return Got { value: last, fresh: false },
    };
    let Some(slot) = HelperSlot::take() else {
        drop(flight);
        return Got { value: last_result(key), fresh: false };
    };
    let (tx, rx) = mpsc::sync_channel(1);
    let spawned = thread::Builder::new().name("helper".into()).stack_size(64 * 1024).spawn(move || {
        let _slot = slot;
        let v = work();
        flight.land(&v);
        let _ = tx.send(v);
    });
    if spawned.is_err() {
        return Got { value: last_result(key), fresh: false }; // the flight went with the closure
    }
    match rx.recv_timeout(timeout) {
        Ok(v) => Got { value: v, fresh: true },
        Err(_) => {
            too_slow(key, timeout);
            Got { value: last_result(key), fresh: false }
        }
    }
}

/// A (kernel) file's text, read on a helper thread.
fn read_within(path: &'static str, timeout: Duration) -> Got {
    guarded(path, timeout, move || fs::read_to_string(path).ok())
}

/// stdout of a program, or None when it cannot start or takes longer than `timeout` (it is then
/// killed): a tool stuck in a driver (amixer, iwconfig) must not hang the agent.
fn output_within(prog: &str, args: &[&str], timeout: Duration) -> Option<String> {
    output_guarded(prog, args, timeout).value
}

fn output_guarded(prog: &str, args: &[&str], timeout: Duration) -> Got {
    let key = std::iter::once(prog).chain(args.iter().copied()).collect::<Vec<_>>().join(" ");
    let flight = match Flight::take_off(&key) {
        Ok(f) => f,
        Err(last) => return Got { value: last, fresh: false },
    };
    let Some(slot) = HelperSlot::take() else {
        drop(flight);
        return Got { value: last_result(&key), fresh: false };
    };
    let spawned = Command::new(prog).args(args).stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::null()).spawn();
    let mut child = match spawned {
        Ok(c) => c,
        Err(_) => {
            flight.land(&None);
            return Got { value: None, fresh: true };
        }
    };
    let stdout = child.stdout.take();
    // The helper reaps the child only while holding this lock and empties it then: a kill after
    // a timeout can never reach a pid that has been reused meanwhile (pids wrap fast here).
    let child = Arc::new(Mutex::new(Some(child)));
    let reaper = Arc::clone(&child);
    let (tx, rx) = mpsc::sync_channel(1);
    let reader = thread::Builder::new().name("helper".into()).stack_size(64 * 1024).spawn(move || {
        let _slot = slot;
        let out = stdout.map(read_capped).unwrap_or_default();
        // Killed (by the timeout below, or anyone): what it printed is not the whole answer.
        let v = reap(&reaper).filter(|st| st.signal().is_none()).map(|_| String::from_utf8_lossy(&out).into_owned());
        flight.land(&v);
        let _ = tx.send(v);
    });
    if reader.is_err() {
        // No thread to read and reap it: stop it here, or it stays a zombie. Waited for only a
        // moment: one stuck in a driver does not die before the driver lets it go.
        let taken = child.lock().unwrap_or_else(|e| e.into_inner()).take();
        if let Some(mut c) = taken {
            let _ = c.kill();
            for _ in 0..50 {
                if !matches!(c.try_wait(), Ok(None)) {
                    break;
                }
                thread::sleep(Duration::from_millis(20));
            }
        }
        return Got { value: last_result(&key), fresh: false };
    }
    match rx.recv_timeout(timeout) {
        Ok(v) => Got { value: v, fresh: true },
        Err(_) => {
            if let Some(c) = child.lock().unwrap_or_else(|e| e.into_inner()).as_mut() {
                let _ = c.kill();
            }
            too_slow(&key, timeout);
            Got { value: last_result(&key), fresh: false }
        }
    }
}

/// Everything a pipe gives until it closes, keeping at most the last `MAX_OUTPUT` bytes.
fn read_capped(mut pipe: impl Read) -> Vec<u8> {
    let mut out = Vec::new();
    let mut buf = [0u8; 8192];
    loop {
        match pipe.read(&mut buf) {
            Ok(0) => break,
            Ok(n) => {
                out.extend_from_slice(&buf[..n]);
                if out.len() > 2 * MAX_OUTPUT {
                    out.drain(..out.len() - MAX_OUTPUT);
                }
            }
            Err(e) if e.kind() == io::ErrorKind::Interrupted => {}
            Err(_) => break,
        }
    }
    if out.len() > MAX_OUTPUT {
        out.drain(..out.len() - MAX_OUTPUT);
    }
    out
}

/// Wait for the child, polling under its lock (see `output_guarded`); its exit status, None
/// when it cannot be waited for.
fn reap(child: &Mutex<Option<Child>>) -> Option<process::ExitStatus> {
    let mut pause = Duration::from_millis(2);
    loop {
        {
            let mut c = child.lock().unwrap_or_else(|e| e.into_inner());
            match c.as_mut().map(Child::try_wait) {
                Some(Ok(None)) => {}
                Some(Ok(Some(status))) => {
                    *c = None;
                    return Some(status);
                }
                Some(Err(_)) | None => {
                    *c = None;
                    return None;
                }
            }
        }
        thread::sleep(pause);
        pause = (pause * 2).min(Duration::from_secs(1));
    }
}

// ── sampler ──────────────────────────────────────────────────────────────
// Reads that end in a driver (the PCM status, Wi-Fi statistics) can hang when it is stuck. One
// thread makes them (through the helpers above) and keeps the results; the watchdog loop and the
// web page only look at those.

/// How often the sampler reads the PCM status (while someone needs it).
const PCM_EVERY: Duration = Duration::from_secs(2);
/// An older PCM status is not trusted.
const PCM_FRESH: Duration = Duration::from_secs(3);
/// Wi-Fi figures, for the page only: this often while it is open.
const WIFI_EVERY: Duration = Duration::from_secs(60);

struct Snapshot {
    /// The PCM status file's text (None: no such file), and when it was read.
    pcm: Option<(Instant, Option<String>)>,
    wifi: Option<web::Wifi>,
}

static SNAPSHOT: Mutex<Snapshot> = Mutex::new(Snapshot { pcm: None, wifi: None });

fn snapshot() -> MutexGuard<'static, Snapshot> {
    SNAPSHOT.lock().unwrap_or_else(|e| e.into_inner())
}

/// Is the PCM open, as the sampler read it within the last few seconds? None: not known.
fn pcm_busy_seen() -> Option<bool> {
    let s = snapshot();
    let (at, text) = s.pcm.as_ref()?;
    (at.elapsed() <= PCM_FRESH).then(|| text.as_deref().is_some_and(|t| t.trim() != "closed"))
}

/// The PCM status as last read, however old ("" when unknown): for the page.
fn pcm_status_seen() -> String {
    snapshot().pcm.as_ref().and_then(|(_, t)| t.clone()).unwrap_or_default()
}

fn wifi_seen() -> Option<web::Wifi> {
    snapshot().wifi.clone()
}

/// The PCM status every 2 s while the watchdog needs it (hiding the official client) or the
/// page is open; Wi-Fi figures every minute while the page is open.
fn sampler(hide_official: bool, ui: bool) {
    let mut wifi_at: Option<Instant> = None;
    loop {
        let started = Instant::now();
        let page = ui && web::page_active();
        if hide_official || page {
            let got = read_within(PCM_STATUS, Duration::from_secs(1));
            if got.fresh {
                snapshot().pcm = Some((Instant::now(), got.value));
            }
        }
        if page && wifi_at.is_none_or(|t| t.elapsed() >= WIFI_EVERY) {
            wifi_at = Some(Instant::now());
            let w = web::sample_wifi();
            snapshot().wifi = Some(w);
        }
        thread::sleep(PCM_EVERY.saturating_sub(started.elapsed()).max(Duration::from_millis(500)));
    }
}

// ── ALSA mixer: one ioctl instead of an amixer process ───────────────────
// The kernel's snd_ctl_elem_* structures (sound/asound.h), laid out for this target: `long` is
// 4 bytes on the speaker (ARMv7) and 8 on a 64-bit test host; the ioctl numbers carry the size.

const LONGS: usize = 128 / std::mem::size_of::<std::ffi::c_long>();

#[repr(C)]
#[derive(Clone, Copy)]
struct CtlElemId {
    numid: u32,
    iface: i32,
    device: u32,
    subdevice: u32,
    name: [u8; 44],
    index: u32,
}

#[repr(C)]
struct CtlElemInfo {
    id: CtlElemId,
    kind: i32,
    access: u32,
    count: u32,
    owner: i32,
    value: [std::ffi::c_long; LONGS], // integer: min, max, step
    dimen: [u16; 4],
    reserved: [u8; 56],
}

#[repr(C)]
struct CtlElemValue {
    id: CtlElemId,
    indirect: u32,
    pad: u32, // the value union is 8-byte aligned
    value: [std::ffi::c_long; 128],
    tstamp: [std::ffi::c_long; 2],
    reserved: [u8; 128 - 2 * std::mem::size_of::<std::ffi::c_long>()],
}

const fn iowr(nr: u32, size: usize) -> std::ffi::c_ulong {
    ((3u32 << 30) | ((size as u32) << 16) | ((b'U' as u32) << 8) | nr) as std::ffi::c_ulong
}
const CTL_ELEM_INFO: std::ffi::c_ulong = iowr(0x11, std::mem::size_of::<CtlElemInfo>());
const CTL_ELEM_READ: std::ffi::c_ulong = iowr(0x12, std::mem::size_of::<CtlElemValue>());
const CTL_IFACE_MIXER: i32 = 2;
const CTL_TYPE_INTEGER: i32 = 2;
const _: () = assert!(std::mem::size_of::<CtlElemInfo>() == 272);
const _: () = assert!(std::mem::size_of::<CtlElemValue>() == if LONGS == 32 { 712 } else { 1224 });

/// The volume of mixer control `control` on card `card` in percent, the way amixer shows it:
/// "<control> Playback Volume", or "<control> Volume" (this speaker's Master has no direction).
fn alsa_volume(card: &str, control: &str) -> Option<u8> {
    use std::os::fd::AsRawFd;
    if card.is_empty() || !card.bytes().all(|b| b.is_ascii_digit()) {
        return None;
    }
    let dev = fs::File::open(format!("/dev/snd/controlC{card}")).ok()?;
    // SAFETY: plain C structures, zero is a valid value for every field; the kernel writes only
    // within the sizes encoded in the ioctl numbers.
    let mut info: CtlElemInfo = unsafe { std::mem::zeroed() };
    let found = [format!("{control} Playback Volume"), format!("{control} Volume")].iter().any(|name| {
        if name.len() >= 44 {
            return false;
        }
        // SAFETY: as above.
        info = unsafe { std::mem::zeroed() };
        info.id = CtlElemId { numid: 0, iface: CTL_IFACE_MIXER, device: 0, subdevice: 0, name: [0; 44], index: 0 };
        info.id.name[..name.len()].copy_from_slice(name.as_bytes());
        // SAFETY: `info` is a live, exclusively borrowed CtlElemInfo of the size the request
        // number names; the descriptor is open for as long as `dev` lives.
        unsafe { ioctl(dev.as_raw_fd(), CTL_ELEM_INFO, &mut info as *mut CtlElemInfo) == 0 }
    });
    if !found || info.kind != CTL_TYPE_INTEGER || info.count == 0 {
        return None;
    }
    let (min, max) = (info.value[0] as i64, info.value[1] as i64);
    // SAFETY: as above.
    let mut v: CtlElemValue = unsafe { std::mem::zeroed() };
    v.id = info.id;
    // SAFETY: as for CTL_ELEM_INFO; ELEM_READ only reads the control (it never sets a level).
    if max <= min || unsafe { ioctl(dev.as_raw_fd(), CTL_ELEM_READ, &mut v as *mut CtlElemValue) } != 0 {
        return None;
    }
    let pct = ((v.value[0] as i64 - min) as f64 * 100.0 / (max - min) as f64).round_ties_even();
    Some(pct.clamp(0.0, 100.0) as u8)
}

/// What amixer says the level of a control is (0..100), when it answers within 2 s.
fn amixer_volume(card: &str, control: &str) -> Option<u8> {
    let text = output_within("/system/bin/amixer", &["-c", card, "sget", control], Duration::from_secs(2))?;
    text.split('[').find_map(|s| s.split_once("%]").and_then(|(n, _)| n.parse::<u8>().ok()))
}

// ── LUCI (Libre control protocol, TCP 7777) ──────────────────────────────
// TX: little-endian header, remote id 0xAAAA, payload + NUL.
// RX on LS9: big-endian header, remote id 0x0000, payload without NUL, packets back to back.

fn luci_packet(cmd: u8, mbid: u16, payload: &str) -> Vec<u8> {
    let data = payload.as_bytes();
    let mut v = Vec::with_capacity(11 + data.len());
    v.extend_from_slice(&0xAAAAu16.to_le_bytes());
    v.push(cmd);
    v.extend_from_slice(&mbid.to_le_bytes());
    v.push(0);
    v.extend_from_slice(&0u16.to_le_bytes());
    v.extend_from_slice(&(data.len() as u16).to_le_bytes());
    v.extend_from_slice(data);
    v.push(0);
    v
}

fn luci_parse(rx: &mut Vec<u8>) -> Vec<(u16, String)> {
    let mut out = Vec::new();
    while rx.len() >= 10 {
        let rid = u16::from_be_bytes([rx[0], rx[1]]);
        let len = u16::from_be_bytes([rx[8], rx[9]]) as usize;
        if rid != 0 || len > 16 * 1024 {
            rx.remove(0); // resync
            continue;
        }
        if rx.len() < 10 + len {
            break;
        }
        let mbid = u16::from_be_bytes([rx[3], rx[4]]);
        let payload = String::from_utf8_lossy(&rx[10..10 + len])
            .trim_end_matches('\0')
            .to_string();
        rx.drain(..10 + len);
        out.push((mbid, payload));
    }
    out
}

struct Luci {
    s: TcpStream,
    rx: Vec<u8>,
}

impl Luci {
    fn connect() -> io::Result<Luci> {
        let s = TcpStream::connect_timeout(&LUCI_ADDR, Duration::from_secs(3))?;
        let _ = s.set_nodelay(true);
        let mut l = Luci { s, rx: Vec::new() };
        l.send(CMD_SET, MB_REGISTER, "127.0.0.1")?;
        thread::sleep(Duration::from_millis(400));
        Ok(l)
    }

    fn send(&mut self, cmd: u8, mbid: u16, payload: &str) -> io::Result<()> {
        self.s.write_all(&luci_packet(cmd, mbid, payload))
    }

    fn poll(&mut self, timeout: Duration) -> io::Result<Vec<(u16, String)>> {
        self.s.set_read_timeout(Some(timeout.max(Duration::from_millis(10))))?;
        let mut buf = [0u8; 4096];
        match self.s.read(&mut buf) {
            Ok(0) => return Err(io::Error::new(io::ErrorKind::UnexpectedEof, "LUCI closed")),
            Ok(n) => self.rx.extend_from_slice(&buf[..n]),
            Err(e) if matches!(e.kind(), io::ErrorKind::WouldBlock | io::ErrorKind::TimedOut) => {}
            Err(e) => return Err(e),
        }
        Ok(luci_parse(&mut self.rx))
    }
}

fn luci_set_once(mbid: u16, payload: &str) -> io::Result<()> {
    let mut l = Luci::connect()?;
    l.send(CMD_SET, mbid, payload)?;
    let _ = l.poll(Duration::from_millis(300));
    Ok(())
}

// ── root console ─────────────────────────────────────────────────────────
// Each script ends by printing CONSOLE_DONE, so its reply is read exactly to its end: closing
// the connection ends the console session (and what still runs in it), and a reader that waits
// for silence instead either cuts a slow script short or waits for nothing.

const CONSOLE_DONE: &str = "LITHIFY_DONE";
/// The console greets a new connection with "CONNECTED!!" once it takes a line.
const CONSOLE_BANNER: &[u8] = b"CONNECTED";
const CONSOLE_GREET_WITHIN: Duration = Duration::from_secs(8);
/// Most of a reply that is kept (its end; an update prints a few KB).
const CONSOLE_KEEP: usize = 64 * 1024;
const ROOT_SCRIPT_PREFIX: &str = "lithify-root-";

fn find_bytes(hay: &[u8], needle: &[u8]) -> Option<usize> {
    if needle.is_empty() {
        return Some(0);
    }
    hay.windows(needle.len()).position(|w| w == needle)
}

/// A console reply as it arrives: up to the end marker, which is never part of it.
struct ConsoleReply {
    out: Vec<u8>,
    /// How much of `out` has gone to the progress callback.
    shown: usize,
    marker: &'static [u8],
    done: bool,
}

impl ConsoleReply {
    fn new(marker: &'static str) -> ConsoleReply {
        ConsoleReply { out: Vec::new(), shown: 0, marker: marker.as_bytes(), done: false }
    }

    /// Add what arrived (nothing after the marker counts); true once the marker is there.
    fn push(&mut self, data: &[u8], progress: &mut dyn FnMut(&str)) -> bool {
        if self.done {
            return true;
        }
        let hold = self.marker.len().saturating_sub(1);
        // Only where a marker can newly appear: across the old end, or in the new bytes.
        let from = self.out.len().saturating_sub(hold);
        self.out.extend_from_slice(data);
        if let Some(i) = find_bytes(&self.out[from..], self.marker) {
            self.out.truncate(from + i);
            self.done = true;
        }
        // Show what can no longer be the start of the marker, ending on a whole character.
        let upto = if self.done { self.out.len() } else { utf8_whole(&self.out, self.out.len().saturating_sub(hold)) };
        if upto > self.shown {
            progress(&String::from_utf8_lossy(&self.out[self.shown..upto]));
            self.shown = upto;
        }
        if self.out.len() > 2 * CONSOLE_KEEP {
            let cut = (self.out.len() - CONSOLE_KEEP).min(self.shown);
            self.out.drain(..cut);
            self.shown -= cut;
        }
        self.done
    }

    /// The reply (without the marker); what was held back goes to `progress` now.
    fn finish(self, progress: &mut dyn FnMut(&str)) -> String {
        if self.shown < self.out.len() {
            progress(&String::from_utf8_lossy(&self.out[self.shown..]));
        }
        String::from_utf8_lossy(&self.out).into_owned()
    }
}

/// The largest `n <= upto` at which `b[..n]` does not end inside a UTF-8 character.
fn utf8_whole(b: &[u8], upto: usize) -> usize {
    let upto = upto.min(b.len());
    // The last lead byte among the final three: does its character fit before `upto`?
    for i in (upto.saturating_sub(3)..upto).rev() {
        let lead = b[i];
        if lead & 0xC0 == 0x80 {
            continue; // a continuation byte
        }
        let len = match lead {
            0x00..=0x7F => 1,
            0xC0..=0xDF => 2,
            0xE0..=0xEF => 3,
            _ => 4,
        };
        return if i + len > upto { i } else { upto };
    }
    upto
}

fn root_run(script: &str, max_wait: Duration) -> io::Result<String> {
    root_run_with(script, Duration::from_secs(6), max_wait, &mut |_| {})
}

/// Run `script` as root through the console, handing every piece of output to `progress`. The
/// reply is read until the script's end marker; nothing arriving for `idle` (a long update
/// needs a generous one) or `max_wait` in all ends it too.
fn root_run_with(script: &str, idle: Duration, max_wait: Duration, progress: &mut dyn FnMut(&str)) -> io::Result<String> {
    static N: AtomicU32 = AtomicU32::new(0);
    // Unpredictable name: other uid-1000 processes must not be able to prepare a file there.
    let mut rnd = [0u8; 8];
    if let Ok(mut f) = fs::File::open("/dev/urandom") {
        let _ = f.read_exact(&mut rnd);
    }
    let path = format!(
        "/tmp/{ROOT_SCRIPT_PREFIX}{}-{}-{}.sh",
        process::id(),
        N.fetch_add(1, Ordering::Relaxed),
        rnd.iter().map(|b| format!("{b:02x}")).collect::<String>()
    );
    // A file that is there already is not ours: it is neither used nor removed.
    let mut f = OpenOptions::new().write(true).create_new(true).mode(0o600).open(&path)?;
    let written = f.write_all(console_script(script).as_bytes());
    drop(f);
    let result = written.and_then(|_| console_session(ROOT_CONSOLE_ADDR, &path, idle, max_wait, progress));
    let _ = fs::remove_file(&path);
    result.map(|o| o.trim_start_matches('>').trim().to_string())
}

/// The script as the console runs it: in a subshell, so that an `exit` in it (or a trap of its
/// own) still lets the end marker through. The `:` keeps an empty script valid shell.
fn console_script(script: &str) -> String {
    let nl = if script.is_empty() || script.ends_with('\n') { "" } else { "\n" };
    format!("(\n{script}{nl}:\n)\necho {CONSOLE_DONE}\n")
}

fn console_session(
    console: SocketAddr,
    path: &str,
    idle: Duration,
    max_wait: Duration,
    progress: &mut dyn FnMut(&str),
) -> io::Result<String> {
    let start = Instant::now();
    let left = || max_wait.saturating_sub(start.elapsed()).max(Duration::from_millis(10));
    let mut s = TcpStream::connect_timeout(&console, left().min(Duration::from_secs(3)))?;
    wait_for_banner(&mut s, left().min(CONSOLE_GREET_WITHIN))?;
    // The console drops the last two bytes of every line: it expects CRLF. One `exec` line:
    // the console's ash spins forever after anything it forks itself.
    s.write_all(format!("exec mksh {path}\r\n").as_bytes())?;
    let mut reply = ConsoleReply::new(CONSOLE_DONE);
    let mut buf = [0u8; 4096];
    while !reply.done {
        let left = max_wait.saturating_sub(start.elapsed());
        if left.is_zero() {
            break;
        }
        s.set_read_timeout(Some(idle.min(left).max(Duration::from_millis(10))))?;
        match s.read(&mut buf) {
            Ok(0) => break,
            Ok(n) => {
                reply.push(&buf[..n], progress);
            }
            Err(e) if e.kind() == io::ErrorKind::Interrupted => {}
            Err(_) => break, // silent for `idle`
        }
    }
    Ok(reply.finish(progress))
}

/// Read the console's greeting (for at most `within`); it takes a line only after that. The
/// rest of the greeting's line ("!!\r\n") is read too, so it does not open the script's reply.
fn wait_for_banner(s: &mut TcpStream, within: Duration) -> io::Result<()> {
    let start = Instant::now();
    let mut seen = Vec::new();
    let mut buf = [0u8; 256];
    // Once the banner is there, the end of its line is waited for only briefly.
    let mut line_end_by: Option<Instant> = None;
    let not_greeted = || io::Error::new(io::ErrorKind::TimedOut, "the root console did not greet");
    loop {
        let left = match line_end_by {
            Some(t) => t.saturating_duration_since(Instant::now()),
            None => within.saturating_sub(start.elapsed()),
        };
        if left.is_zero() {
            return if line_end_by.is_some() { Ok(()) } else { Err(not_greeted()) };
        }
        s.set_read_timeout(Some(left.max(Duration::from_millis(10))))?;
        match s.read(&mut buf) {
            Ok(0) => return Err(io::Error::new(io::ErrorKind::UnexpectedEof, "the root console closed the connection")),
            Ok(n) => {
                seen.extend_from_slice(&buf[..n]);
                if let Some(i) = find_bytes(&seen, CONSOLE_BANNER) {
                    if seen[i..].contains(&b'\n') {
                        return Ok(());
                    }
                    line_end_by.get_or_insert_with(|| Instant::now() + Duration::from_millis(300));
                }
                if seen.len() > 4096 {
                    seen.drain(..seen.len() - CONSOLE_BANNER.len());
                }
            }
            Err(e) if e.kind() == io::ErrorKind::Interrupted => {}
            Err(e) if matches!(e.kind(), io::ErrorKind::WouldBlock | io::ErrorKind::TimedOut) => {
                return if line_end_by.is_some() { Ok(()) } else { Err(not_greeted()) };
            }
            Err(e) => return Err(e),
        }
    }
}

/// Root scripts left in /tmp by an agent that stopped during a console session.
fn sweep_root_scripts() {
    let Ok(rd) = fs::read_dir("/tmp") else { return };
    for e in rd.flatten() {
        let name = e.file_name();
        let Some(name) = name.to_str() else { continue };
        if !(name.starts_with(ROOT_SCRIPT_PREFIX) && name.ends_with(".sh")) {
            continue;
        }
        let age = e.metadata().and_then(|m| m.modified()).ok().and_then(|t| t.elapsed().ok());
        if age.is_some_and(|a| a > Duration::from_secs(60)) {
            let _ = fs::remove_file(e.path());
        }
    }
}

// ── subcommand: exec ─────────────────────────────────────────────────────

fn cmd_exec(args: &[String]) -> ! {
    // exec [--args-file FILE] -- PROG [ARGS..]; FILE holds extra arguments, one per line, so
    // librespot options can change without touching the Cast process list (no reboot).
    let mut args = args;
    let mut file_args: Vec<String> = Vec::new();
    // First: the settings are read after the wait, so a start after a restore gets the new ones.
    slow_down_restarts();
    if args.first().map(String::as_str) == Some("--args-file") {
        let Some(path) = args.get(1) else {
            eprintln!("usage: lithify-agent exec [--args-file FILE] -- PROG [ARGS..]");
            process::exit(2);
        };
        // With settings on the speaker, librespot's arguments come from them; the file named in
        // the Cast process list is what installs made before them use.
        let base = base_dir().display().to_string();
        if let Some((s, _)) = settings::load() {
            file_args = settings::librespot_args(&s, &base);
        } else {
            match fs::read_to_string(path) {
                Ok(text) => file_args = text.lines().filter(|l| !l.trim().is_empty()).map(str::to_string).collect(),
                Err(e) => {
                    log(&format!("cannot read {path}: {e}; starting with the default settings"));
                    file_args = settings::librespot_args(&settings::Settings::defaults(), &base);
                }
            }
        }
        args = &args[2..];
    }
    let args = match args.first().map(String::as_str) {
        Some("--") => &args[1..],
        _ => args,
    };
    let Some(prog) = args.first() else {
        eprintln!("usage: lithify-agent exec [--args-file FILE] -- PROG [ARGS..]");
        process::exit(2);
    };
    // A parent that ignores SIGCHLD makes every waitpid() in the child fail with ECHILD;
    // librespot waits for its --onevent hook, so give it default dispositions and an empty mask.
    let empty = SigSet([0; 128]);
    // SAFETY: signal(2) with SIG_DFL installs no handler of ours; sigprocmask(2) reads a valid,
    // aligned, zeroed sigset_t (no signal blocked) and is given no old set to write.
    unsafe {
        signal(SIGCHLD, SIG_DFL);
        signal(SIGPIPE, SIG_DFL);
        sigprocmask(SIG_SETMASK, &empty, std::ptr::null_mut());
    }
    let mut all: Vec<String> = args[1..].iter().cloned().chain(file_args).collect();
    if Path::new(prog).file_name().is_some_and(|n| n == "librespot") {
        let named = tmp_dir_of(&all).map(str::to_string);
        match named.as_deref() {
            Some(settings::LIBRESPOT_TMP) => {
                if !prepare_librespot_tmp(settings::LIBRESPOT_TMP) {
                    drop_tmp_option(&mut all);
                }
            }
            Some(_) => {} // a dir of its own (librespot.args of an install made before the settings)
            None => {
                if prepare_librespot_tmp(settings::LIBRESPOT_TMP) {
                    all.extend(["--tmp".to_string(), settings::LIBRESPOT_TMP.to_string()]);
                }
            }
        }
    }
    // Last: the level is read right before librespot starts with it.
    pin_initial_volume(&mut all);
    close_inherited_fds();
    let mut cmd = Command::new(prog);
    cmd.args(&all);
    // The bundled static alsa-lib reads its configuration from the install directory.
    let alsa = base_dir().join("alsa");
    if env::var_os("ALSA_CONFIG_DIR").is_none() && alsa.join("alsa.conf").is_file() {
        cmd.env("ALSA_CONFIG_DIR", &alsa);
    }
    let err = cmd.exec();
    log(&format!("exec {prog} failed: {err}"));
    process::exit(127);
}

/// The Cast process manager leaves descriptors open in what it starts (its pid file for writing,
/// a flash device): they are not librespot's, so they close when it starts. The Android property
/// area (ANDROID_PROPERTY_WORKSPACE) stays, for programs that read system properties.
fn close_inherited_fds() {
    let keep = env::var("ANDROID_PROPERTY_WORKSPACE").ok().and_then(|w| w.split(',').next()?.parse::<i32>().ok());
    let Ok(dir) = fs::read_dir("/proc/self/fd") else { return };
    let fds: Vec<i32> = dir.flatten().filter_map(|e| e.file_name().to_str()?.parse().ok()).collect();
    for fd in fds.into_iter().filter(|&fd| fd > 2 && Some(fd) != keep) {
        // SAFETY: fcntl(2) with F_GETFD/F_SETFD reads and sets one descriptor's flags and touches
        // no memory; a descriptor closed meanwhile (the listing's own) just fails with EBADF.
        unsafe {
            let flags = fcntl(fd, F_GETFD);
            if flags >= 0 {
                fcntl(fd, F_SETFD, flags | FD_CLOEXEC);
            }
        }
    }
}

// ── librespot's downloads ────────────────────────────────────────────────
// librespot writes every track it plays (and the next one) whole into a temporary file in its
// --tmp dir, which it deletes only when it ends cleanly. The speaker's /tmp is a 32 MB tmpfs: a
// long track fills it (the track breaks, and with it every other /tmp user: logs, Cast), and
// each librespot that is stopped (SIGTERM: it handles only SIGINT) or crashes leaves its files
// there, in RAM, until a reboot. So the dir is a tmpfs of its own, emptied at every start.

/// The tmpfs: room for an 80-minute track at 320 kbit/s (a long podcast at 160 kbit/s: 2.7 h) and
/// the start of the next one. It takes memory only for what it holds; the speaker has 462 MB,
/// about 380 MB of it free while librespot plays.
const LIBRESPOT_TMP_SIZE_KB: u64 = 192 * 1024;
const LIBRESPOT_TMP_OPTIONS: &str = "size=192m,mode=0700,uid=1000,gid=1000";

/// The dir librespot's arguments name for its downloads (`--tmp D`, `--tmp=D`, `-t D`).
fn tmp_dir_of(args: &[String]) -> Option<&str> {
    args.iter().enumerate().find_map(|(i, a)| match a.as_str() {
        "--tmp" | "-t" => args.get(i + 1).map(String::as_str),
        a => a.strip_prefix("--tmp="),
    })
}

/// Is `dir` a mount point in `mounts` (/proc/mounts: device, mount point, type, ...)?
fn is_mount_point(mounts: &str, dir: &str) -> bool {
    mounts.lines().any(|l| l.split_whitespace().nth(1) == Some(dir))
}

/// The size of `dir`'s mount in /proc/mounts, in KB (its options say "size=131072k").
fn mount_size_kb(mounts: &str, dir: &str) -> Option<u64> {
    let options = mounts.lines().map(|l| l.split_whitespace().collect::<Vec<_>>()).find(|f| f.get(1) == Some(&dir))?.get(3)?.to_string();
    options.split(',').find_map(|o| o.strip_prefix("size="))?.strip_suffix('k')?.parse().ok()
}

/// Remove everything inside `dir`, not `dir` itself: how many entries, and the bytes of the files.
fn wipe_dir(dir: &Path) -> io::Result<(usize, u64)> {
    let mut removed = (0, 0);
    for e in fs::read_dir(dir)?.flatten() {
        // The entry's own type: a link is removed, never followed.
        let Ok(kind) = e.file_type() else { continue };
        let bytes = if kind.is_file() { e.metadata().map_or(0, |m| m.len()) } else { 0 };
        let gone = if kind.is_dir() { fs::remove_dir_all(e.path()) } else { fs::remove_file(e.path()) };
        if gone.is_ok() {
            removed = (removed.0 + 1, removed.1 + bytes);
        }
    }
    Ok(removed)
}

fn wipe_logged(dir: &str) {
    match wipe_dir(Path::new(dir)) {
        Ok((0, _)) => {}
        Ok((n, bytes)) => {
            log(&format!("removed {n} files left in {dir} by a librespot that did not end cleanly ({} KB)", bytes / 1024))
        }
        Err(e) if e.kind() == io::ErrorKind::NotFound => {} // librespot creates it
        Err(e) => log(&format!("cannot empty {dir}: {e}")),
    }
}

/// Before librespot starts (no other process uses the dir, the last librespot has ended): its
/// download dir is a tmpfs of its own, mounted through the root console when it is not yet, and
/// empty. Without the console it stays a plain dir in /tmp, emptied all the same. False when
/// librespot could not write into it at all: it is then left to its default, /tmp itself.
fn prepare_librespot_tmp(dir: &str) -> bool {
    let mounts = || fs::read_to_string("/proc/mounts").unwrap_or_default();
    let mounted = || is_mount_point(&mounts(), dir);
    let size = mount_size_kb(&mounts(), dir);
    if mounted() && size.is_some_and(|kb| kb != LIBRESPOT_TMP_SIZE_KB) {
        // Mounted by an earlier version with another size: resized in place (growing loses nothing).
        let script = format!("mount -o remount,size={LIBRESPOT_TMP_SIZE_KB}k {dir}\n");
        match root_run(&script, Duration::from_secs(5)) {
            Ok(_) if mount_size_kb(&mounts(), dir) == Some(LIBRESPOT_TMP_SIZE_KB) => {
                log(&format!("librespot's download tmpfs resized to {} MB", LIBRESPOT_TMP_SIZE_KB / 1024))
            }
            Ok(out) => log(&format!("could not resize the tmpfs on {dir}: {}", out.trim())),
            Err(e) => log(&format!("could not resize the tmpfs on {dir} (root console: {e})")),
        }
    }
    if !mounted() {
        // Made by this user, so that it stays writable for librespot if the mount fails (one
        // the console made would be root's, and its umask is 077).
        let _ = fs::create_dir(dir);
        // What is in the plain dir would stay under the mount, hidden but still filling /tmp.
        wipe_logged(dir);
        let script = format!(
            "d={dir}\n[ -L $d ] && {{ echo \"$d is a symbolic link\"; exit 1; }}\n\
             mkdir -p $d && mount -t tmpfs -o {LIBRESPOT_TMP_OPTIONS} lithify-librespot $d\n"
        );
        let plain = "librespot downloads into a plain dir in /tmp";
        match root_run(&script, Duration::from_secs(5)) {
            Ok(_) if mounted() => log(&format!("librespot downloads into a tmpfs of its own ({dir}, {LIBRESPOT_TMP_OPTIONS})")),
            Ok(out) => log(&format!("no tmpfs on {dir} ({}): {plain}", out.trim())),
            Err(e) => log(&format!("no tmpfs on {dir} (root console: {e}): {plain}")),
        }
    }
    wipe_logged(dir);
    let usable = writable(Path::new(dir));
    if !usable {
        log(&format!("librespot cannot write into {dir}: it downloads into /tmp itself"));
    }
    usable
}

/// Can this user make files in `dir`? (Tried, not worked out from modes and owners.)
fn writable(dir: &Path) -> bool {
    let probe = dir.join(format!(".lithify-probe-{}", process::id()));
    let made = OpenOptions::new().write(true).create_new(true).open(&probe).is_ok();
    if made {
        let _ = fs::remove_file(&probe);
    }
    made
}

/// librespot's arguments without their download dir (`--tmp D`, `--tmp=D`, `-t D`).
fn drop_tmp_option(args: &mut Vec<String>) {
    let mut i = 0;
    while i < args.len() {
        match args[i].as_str() {
            "--tmp" | "-t" => {
                args.drain(i..(i + 2).min(args.len()));
            }
            a if a.starts_with("--tmp=") => {
                args.remove(i);
            }
            _ => i += 1,
        }
    }
}

/// Every librespot start is noted (system uptime, the last ten), so the agent sees a crash loop.
const STARTS_FILE: &str = "/tmp/lithify-librespot.starts";

/// ... and every stop the agent asks for itself (a setting, a button, an update): the starts that
/// follow those are no sign of trouble.
const STOPS_FILE: &str = "/tmp/lithify-librespot.stops";

fn times(path: &str) -> Vec<f64> {
    fs::read_to_string(path).unwrap_or_default().split_whitespace().filter_map(|v| v.parse().ok()).collect()
}

fn librespot_starts() -> Vec<f64> {
    times(STARTS_FILE)
}

fn note_time(path: &str) {
    let Some(now) = system_uptime() else { return };
    let mut t: Vec<f64> = times(path).into_iter().filter(|x| *x <= now).collect();
    t.push(now);
    let keep = t.len().saturating_sub(10);
    write_state(path, &t[keep..].iter().map(|x| format!("{x:.0}\n")).collect::<String>());
}

/// Replace a small state file in /tmp whole: readers (the agent, the exec wrapper, the hook)
/// never see half of one.
fn write_state(path: &str, text: &str) {
    if let Err(e) = settings::write_atomic(Path::new(path), text) {
        log(&format!("cannot write {path}: {e}"));
    }
}

/// Stop librespot on purpose (the Cast process manager starts it again).
fn restart_librespot_now() -> Vec<i32> {
    note_time(STOPS_FILE);
    let pids = find_pids("librespot");
    for p in &pids {
        signal_or_log(*p, SIGTERM);
    }
    pids
}

/// A librespot that exits at once would be restarted as fast as the process manager can, each
/// time printing its usage text into the log: wait longer the more often it started lately.
fn slow_down_restarts() {
    let Some(now) = system_uptime() else { return };
    let recent = unexplained_starts(now, 60.0);
    note_time(STARTS_FILE);
    if recent >= 2 {
        let wait = (5 * (recent as u64 - 1)).min(30);
        log(&format!("librespot started {recent} times in the last minute: waiting {wait} s first"));
        thread::sleep(Duration::from_secs(wait));
    }
}

/// librespot starts within the last `window` seconds that no stop by the agent explains.
fn unexplained_starts(now: f64, window: f64) -> usize {
    let stops = times(STOPS_FILE);
    librespot_starts()
        .into_iter()
        .filter(|t| now - t < window && now >= *t)
        .filter(|t| !stops.iter().any(|s| t >= s && t - s < 15.0))
        .count()
}

fn opt_value<'a>(args: &'a [String], name: &str) -> Option<&'a str> {
    args.iter().position(|a| a == name).and_then(|i| args.get(i + 1)).map(String::as_str)
}

/// With `--mixer alsa` librespot drives a hardware control directly, but at every start it sets
/// that control to its initial volume (50% unless `--initial-volume` is given, despite the help
/// text). The control is the speaker's master volume, shared with Cast/AirPlay, so pin the
/// initial volume to the control's current value: a librespot (re)start then changes nothing.
fn pin_initial_volume(args: &mut Vec<String>) {
    if opt_value(args, "--mixer") != Some("alsa") || args.iter().any(|a| a == "--initial-volume") {
        return;
    }
    let (card, control) = mixer_of(args, "PCM");
    // Right after boot the mixer may not answer yet; a failed read would mean librespot's 50%.
    let level = (0..3).find_map(|i| {
        if i > 0 {
            thread::sleep(Duration::from_millis(300));
        }
        read_volume(&card, &control)
    });
    match level {
        Some(pct) => {
            log(&format!("librespot initial volume pinned to the current {control} level: {pct}%"));
            args.push("--initial-volume".into());
            args.push(pct.to_string());
        }
        None => log(&format!("could not read {control} on card {card}; librespot will use its default")),
    }
}

/// Card and control of librespot's hardware mixer (`--alsa-mixer-device hw:N`, `--alsa-mixer-control`).
fn mixer_of(args: &[String], default_control: &str) -> (String, String) {
    let control = opt_value(args, "--alsa-mixer-control").unwrap_or(default_control).to_string();
    let card = opt_value(args, "--alsa-mixer-device")
        .and_then(|d| d.strip_prefix("hw:"))
        .map(|c| c.split(',').next().unwrap_or("0").to_string())
        .unwrap_or_else(|| "0".to_string());
    (card, control)
}

/// Current level (0..100) of an ALSA control: read from the kernel directly (no process), else
/// asked from amixer; never waits more than about 3 s, even when the audio driver is stuck (then
/// the last level read, while that read has not returned).
fn read_volume(card: &str, control: &str) -> Option<u8> {
    let (c, k) = (card.to_string(), control.to_string());
    // "" when the ioctl finds no such control (amixer may know it under another form)
    let got = guarded(&format!("ALSA {control} level (card {card})"), Duration::from_secs(1), move || {
        Some(alsa_volume(&c, &k).map(|v| v.to_string()).unwrap_or_default())
    });
    match got.value {
        Some(v) if v.is_empty() && got.fresh => amixer_volume(card, control),
        v => v.and_then(|v| v.parse().ok()),
    }
}

/// librespot's arguments: those of the running librespot, else what its next start uses (the
/// settings, or librespot.args on installs made before them).
fn librespot_args() -> Vec<String> {
    if let Some(pid) = find_pid("librespot") {
        let args = proc_args(pid);
        if !args.is_empty() {
            return args;
        }
    }
    if let Some((s, _)) = settings::load() {
        return settings::librespot_args(&s, &base_dir().display().to_string());
    }
    fs::read_to_string(base_dir().join("librespot.args"))
        .unwrap_or_default()
        .lines()
        .filter(|l| !l.trim().is_empty())
        .map(str::to_string)
        .collect()
}

// ── subcommand: onevent ──────────────────────────────────────────────────

fn wait_pcm_free(max: Duration) -> bool {
    let start = Instant::now();
    while start.elapsed() < max {
        if !pcm_busy_now() {
            return true;
        }
        thread::sleep(Duration::from_millis(100));
    }
    !pcm_busy_now()
}

/// librespot's last player event ("<event> <sink status>"), for the page.
const LIBRESPOT_STATE_FILE: &str = "/tmp/lithify-librespot.state";

fn cmd_onevent() -> i32 {
    let event = env::var("PLAYER_EVENT").unwrap_or_default();
    let sink = env::var("SINK_STATUS").unwrap_or_default();
    write_state(LIBRESPOT_STATE_FILE, &format!("{event} {sink}\n"));
    // Volume needs nothing here: librespot drives the hardware Master control itself.
    if event == "sink" && sink == "running" && pcm_busy_now() {
        free_pcm();
    }
    0
}

/// librespot is about to open the PCM and another source still holds it.
fn free_pcm() {
    let started = Instant::now();
    log("librespot wants the PCM while it is busy: pausing the active Libre source");
    if let Err(e) = luci_set_once(MB_TRANSPORT, "PAUSE") {
        log(&format!("LUCI PAUSE failed: {e}"));
    }
    // AudioFlinger keeps the device open for a few seconds after a pause (standby delay).
    if !wait_pcm_free(Duration::from_millis(4500)) {
        log("PCM still busy after PAUSE: sending STOP");
        let _ = luci_set_once(MB_TRANSPORT, "STOP");
        wait_pcm_free(Duration::from_secs(2));
    }
    log(&format!(
        "PCM {} after {} ms",
        if pcm_busy_now() { "STILL BUSY" } else { "free" },
        started.elapsed().as_millis()
    ));
}

// ── subcommand: run ──────────────────────────────────────────────────────

struct SpinWatch {
    pid: Option<i32>,
    prev: HashMap<i32, u64>,
    prev_total: Option<u64>,
    prev_at: Instant,
    hot_since: Option<Instant>,
    last_restart: Option<Instant>,
    missing_since: Option<Instant>,
    max_rate: f64,
    /// Since when librespot has not been working (hide_official_spotify).
    librespot_bad_since: Option<Instant>,
    last_hide: Option<Instant>,
}

impl SpinWatch {
    fn new() -> SpinWatch {
        SpinWatch {
            pid: None,
            prev: HashMap::new(),
            prev_total: None,
            prev_at: Instant::now(),
            hot_since: None,
            last_restart: None,
            missing_since: None,
            max_rate: 0.0,
            librespot_bad_since: None,
            last_hide: None,
        }
    }

    /// With hide_official_spotify the official client stays stopped while librespot works, so
    /// Spotify lists only one device for this speaker. When librespot has not worked for the
    /// grace period (or the option is off again) it comes back at once. True while it is hidden:
    /// there is nothing else to watch then.
    fn hide_tick(&mut self, cfg: &Config) -> bool {
        // Working: up for half a minute at least (a crash loop does not count).
        let works = find_pid("librespot").and_then(proc_uptime_s).is_some_and(|s| s >= 30);
        let bad_for = if works {
            self.librespot_bad_since = None;
            Duration::ZERO
        } else {
            self.librespot_bad_since.get_or_insert_with(Instant::now).elapsed()
        };
        let hide = cfg.hide_official && bad_for < Duration::from_secs(cfg.respawn_grace);
        shared().official_hidden = hide;
        let official = find_pid("spotifyhifi");
        if hide {
            // Never in the middle of a song: as the fallback it may be the one playing. A PCM
            // status the sampler has not read in the last few seconds counts as playing.
            let other_plays = pcm_busy_seen().unwrap_or(true) && !find_pid("librespot").is_some_and(holds_pcm_now);
            let due = self.last_hide.is_none_or(|t| t.elapsed() >= Duration::from_secs(60));
            if let (Some(pid), false, true) = (official, other_plays, due) {
                self.last_hide = Some(Instant::now());
                let first = fs::metadata(HIDDEN_FILE).is_err();
                write_state(HIDDEN_FILE, "1\n");
                let msg = format!("hiding the official Spotify (pid {pid}) while librespot works");
                if first { event(&msg) } else { log(&msg) }
                match official_spotify(SPOTIFY_STOP, Duration::from_secs(10)) {
                    Ok(o) if o.trim().is_empty() => {}
                    Ok(o) => log(&format!("official Spotify still running after the stop: {o:?}")),
                    Err(e) => log(&format!("stopping official Spotify via the root console failed: {e}")),
                }
            }
            self.pid = None;
            self.missing_since = None;
            self.hot_since = None;
            return true;
        }
        if fs::metadata(HIDDEN_FILE).is_ok() {
            let _ = fs::remove_file(HIDDEN_FILE);
            if official.is_none() && find_pid("LibreManager").is_some() {
                event(if cfg.hide_official {
                    "librespot has not worked for a while: the official Spotify is back"
                } else {
                    "showing the official Spotify again"
                });
                match official_spotify(SPOTIFY_LAUNCH, Duration::from_secs(10)) {
                    Ok(o) => log(&format!("launch done, pidof: {o:?}")),
                    Err(e) => log(&format!("launch via root console failed: {e}")),
                }
                self.missing_since = None;
            }
        }
        false
    }

    fn cooled_down(&self, cfg: &Config) -> bool {
        self.last_restart
            .map(|t| t.elapsed() >= Duration::from_secs(cfg.spin_cooldown))
            .unwrap_or(true)
    }

    fn tick(&mut self, cfg: &Config, tck: f64) {
        if self.hide_tick(cfg) {
            return;
        }
        let pid = find_pid("spotifyhifi");
        if pid != self.pid {
            self.pid = pid;
            self.prev.clear();
            self.prev_total = None;
            self.hot_since = None;
        }
        let Some(pid) = pid else {
            let since = *self.missing_since.get_or_insert_with(Instant::now);
            if cfg.respawn_missing
                && since.elapsed() >= Duration::from_secs(cfg.respawn_grace)
                && self.cooled_down(cfg)
                && find_pid("LibreManager").is_some()
            {
                event("official Spotify (spotifyhifi) has been gone for a while: launching it again");
                shared().spotify_launches += 1;
                self.last_restart = Some(Instant::now());
                self.missing_since = None;
                match official_spotify(SPOTIFY_LAUNCH, Duration::from_secs(10)) {
                    Ok(o) => log(&format!("launch done, pidof: {o:?}")),
                    Err(e) => log(&format!("launch via root console failed: {e}")),
                }
            }
            return;
        };
        self.missing_since = None;
        let dt = self.prev_at.elapsed().as_secs_f64();
        self.prev_at = Instant::now();
        // One thread can only spin when the whole process uses at least that much CPU, so the
        // per-thread counters (one file per thread) are read only then.
        let total = proc_stat(&format!("/proc/{pid}/stat")).map(|s| s.ticks);
        let process_rate = match (total, self.prev_total) {
            (Some(t), Some(p)) if dt > 0.5 => Some(t.saturating_sub(p) as f64 / tck / dt),
            _ => None,
        };
        self.prev_total = total;
        if let Some(rate) = process_rate.filter(|r| *r < cfg.spin_threshold) {
            self.max_rate = self.max_rate.max(rate);
            self.prev.clear();
            self.hot_since = None;
            return;
        }
        let now_ticks = thread_ticks(pid);
        let mut hottest = (0, 0.0f64);
        if dt > 0.5 {
            for (tid, t) in &now_ticks {
                if let Some(p) = self.prev.get(tid) {
                    let rate = t.saturating_sub(*p) as f64 / tck / dt;
                    if rate > hottest.1 {
                        hottest = (*tid, rate);
                    }
                }
            }
        }
        self.prev = now_ticks;
        self.max_rate = self.max_rate.max(hottest.1);
        if hottest.1 < cfg.spin_threshold {
            self.hot_since = None;
            return;
        }
        let since = *self.hot_since.get_or_insert_with(Instant::now);
        if since.elapsed() < Duration::from_secs(cfg.spin_seconds) || !self.cooled_down(cfg) {
            return;
        }
        event(&format!(
            "official Spotify thread {} has used {:.0}% of a core for {}s: restarting spotifyhifi",
            hottest.0,
            hottest.1 * 100.0,
            since.elapsed().as_secs()
        ));
        shared().spotify_restarts += 1;
        self.last_restart = Some(Instant::now());
        self.hot_since = None;
        match official_spotify(SPOTIFY_RESTART, Duration::from_secs(12)) {
            Ok(o) => log(&format!("restart done, pidof: {o:?}")),
            Err(e) => log(&format!("restart via root console failed: {e}")),
        }
    }
}

fn stop_librespot(pid: i32, why: &str) {
    event(&format!("{why}: stopping librespot (pid {pid}) so the Libre source can open the PCM"));
    shared().librespot_stops += 1;
    note_time(STOPS_FILE);
    signal_or_log(pid, SIGTERM);
    for _ in 0..30 {
        if !alive(pid) {
            return;
        }
        thread::sleep(Duration::from_millis(100));
    }
    // Still the same librespot? Pids wrap fast here.
    if comm_of(pid).as_deref() == Some("librespot") {
        signal_or_log(pid, SIGKILL);
    }
}

fn playing_state(s: &str) -> bool {
    matches!(s, "0" | "3" | "4" | "5")
}

/// Watches Libre's own source/state pushes; a Libre source starting to play while librespot
/// holds the single PCM substream means that source cannot get the device.
fn luci_watch(cfg_conflict: bool) {
    let mut backoff = 2;
    loop {
        let started = Instant::now();
        match Luci::connect() {
            Ok(l) => luci_session(l, cfg_conflict),
            Err(e) => log(&format!("LUCI connect failed: {e}")),
        }
        // A session that ends at once (a service that accepts and closes) must not spin.
        backoff = if started.elapsed() > Duration::from_secs(60) { 2 } else { (backoff * 2).min(60) };
        thread::sleep(Duration::from_secs(backoff));
    }
}

fn luci_session(mut l: Luci, cfg_conflict: bool) {
    let _ = l.send(CMD_GET, MB_SOURCE, "");
    let _ = l.send(CMD_GET, MB_PLAY_STATE, "");
    let mut source: Option<String> = None;
    let mut state: Option<String> = None;
    let mut pending_check: Option<Instant> = None;
    let mut last_rx = Instant::now();
    let mut probe: Option<Instant> = None;
    loop {
        // Sleep until data arrives or the next thing is due: a pending check, the quiet-link probe
        // (once it is sent, only its answer or its timeout is due, not the long-past quiet mark).
        let quiet = probe.is_none().then(|| last_rx + Duration::from_secs(120));
        let due = [pending_check, probe.map(|t| t + Duration::from_secs(10)), quiet]
            .into_iter()
            .flatten()
            .min()
            .unwrap_or_else(Instant::now);
        let wait = due.saturating_duration_since(Instant::now()).clamp(Duration::from_millis(50), Duration::from_secs(30));
        let packets = match l.poll(wait) {
            Ok(p) => p,
            Err(e) => {
                log(&format!("LUCI connection lost: {e}"));
                return;
            }
        };
        if !packets.is_empty() {
            last_rx = Instant::now();
            probe = None;
        }
        let mut conflict: Option<String> = None;
        for (mbid, payload) in packets {
            match mbid {
                MB_SOURCE if source.as_deref() != Some(payload.as_str()) => {
                    if source.is_some() {
                        log(&format!("Libre source {:?} -> {payload:?}", source.as_deref().unwrap_or("?")));
                        pending_check = Some(Instant::now() + Duration::from_millis(1500));
                    }
                    shared().libre_source = payload.clone();
                    source = Some(payload);
                }
                MB_PLAY_STATE if state.as_deref() != Some(payload.as_str()) => {
                    shared().libre_state = payload.clone();
                    let was = state.replace(payload.clone());
                    if was.is_some() && playing_state(&payload) {
                        conflict = Some(format!("Libre state {:?} -> {payload:?}", was.unwrap_or_default()));
                    }
                }
                _ => {}
            }
        }
        if let Some(t) = pending_check {
            if Instant::now() >= t {
                pending_check = None;
                if state.as_deref().map(playing_state).unwrap_or(false) {
                    conflict = Some(format!("Libre source changed to {:?}", source.as_deref().unwrap_or("?")));
                }
            }
        }
        if let (Some(why), true) = (conflict, cfg_conflict) {
            if let Some(pid) = find_pid("librespot") {
                if holds_pcm_now(pid) {
                    stop_librespot(pid, &why);
                }
            }
        }
        // The speaker pushes MB#51 about every 30 s. After two quiet minutes ask for it, and give
        // up on a connection that does not answer even that (open, but dead).
        if probe.is_some_and(|t| t.elapsed() > Duration::from_secs(10)) {
            log("LUCI does not answer: reconnecting");
            return;
        }
        if probe.is_none() && last_rx.elapsed() > Duration::from_secs(120) {
            if l.send(CMD_GET, MB_PLAY_STATE, "").is_err() {
                return;
            }
            probe = Some(Instant::now());
        }
    }
}

// ── logcat ───────────────────────────────────────────────────────────────
// The Cast process manager logs librespot's output and the agent's stderr under the tag
// "lithify-agent"; official Spotify logs as SPOTIFY and SP_RING. One logcat reads them all:
// every logcat receives the speaker's whole (busy) log from logd and filters it itself.

const LOGCAT_FILTERS: [&str; 3] = ["lithify-agent:*", "SPOTIFY:E", "SP_RING:E"];

/// A reader must not outlive the agent: an orphaned logcat keeps receiving the whole log until
/// it next has a line to write, which for a rare tag can be never.
fn die_with_agent(cmd: &mut Command) {
    let agent = process::id() as i32;
    // SAFETY: runs between fork and exec and makes only async-signal-safe calls (no allocation).
    unsafe {
        cmd.pre_exec(move || {
            prctl(PR_SET_PDEATHSIG, SIGKILL as std::ffi::c_ulong);
            if getppid() != agent {
                return Err(io::Error::from_raw_os_error(ESRCH)); // the agent is already gone
            }
            Ok(())
        });
    }
}

/// logcat readers left behind by an agent that is gone (earlier versions did not tie them to it).
fn kill_stale_readers() {
    let mut killed = Vec::new();
    for pid in find_pids("logcat") {
        let cmdline = String::from_utf8_lossy(&fs::read(format!("/proc/{pid}/cmdline")).unwrap_or_default()).into_owned();
        // Exactly the agent's readers (`-v time ... -s <its tags>`), not someone's own logcat.
        let ours = cmdline.contains("\0-v\0time\0")
            && (cmdline.contains("\0lithify-agent:*") || cmdline.contains("\0SPOTIFY:E"));
        // Orphans only: a running agent's reader has that agent as its parent.
        if !ours || proc_stat(&format!("/proc/{pid}/stat")).is_none_or(|st| st.ppid != 1) {
            continue;
        }
        if send_signal(pid, SIGKILL).is_ok() {
            killed.push(pid);
        }
    }
    if !killed.is_empty() {
        log(&format!("stopped {} logcat readers left behind by an earlier agent: {killed:?}", killed.len()));
    }
}

/// The tag of a `logcat -v time` line ("10-06 19:11:45.697 E/SPOTIFY ( 1383): text").
fn logcat_tag(line: &str) -> Option<&str> {
    let rest = line.splitn(3, ' ').nth(2)?;
    let (_, tag) = rest.split_once('/')?;
    Some(tag.split('(').next()?.trim_end())
}

/// Lines for the silent-start watch that may wait for it; more are dropped (it keeps up easily).
const OFFICIAL_QUEUE: usize = 512;

/// Hands librespot's and the agent's lines to the web page (`keep_lines`) and official Spotify's
/// errors to the silent-start watch. A logcat that cannot start, or ends soon, is tried again
/// later and later (up to every few minutes), never given up on.
fn logcat_reader(official: mpsc::SyncSender<String>, keep_lines: bool) {
    let mut tail = Some("2000"); // what logcat still has from before the agent started
    let mut pause = Duration::from_secs(5);
    loop {
        let mut cmd = Command::new("/system/bin/logcat");
        cmd.args(["-v", "time"]);
        if let Some(t) = tail {
            cmd.args(["-T", t]);
        }
        cmd.arg("-s").args(LOGCAT_FILTERS).stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::null());
        die_with_agent(&mut cmd);
        let spawned = Instant::now();
        let mut child = match cmd.spawn() {
            Ok(c) => c,
            Err(e) => {
                log(&format!(
                    "cannot run logcat ({e}): no librespot lines on the web page, no silent-start detection; again in {} s",
                    pause.as_secs()
                ));
                thread::sleep(pause);
                pause = (pause * 2).min(Duration::from_secs(300));
                continue;
            }
        };
        if let Some(out) = child.stdout.take() {
            let mut reader = BufReader::new(out);
            let mut raw = Vec::new();
            loop {
                raw.clear();
                // Lines are bytes: one that is not UTF-8 must not end the reader.
                match reader.read_until(b'\n', &mut raw) {
                    Ok(0) | Err(_) => break,
                    Ok(_) => {}
                }
                let line = String::from_utf8_lossy(&raw).trim_end_matches(['\n', '\r']).to_string();
                // logcat starts with the lines it already had; the watches want new ones only.
                let new = spawned.elapsed() >= Duration::from_secs(2);
                match logcat_tag(&line) {
                    Some("lithify-agent") => {
                        if new {
                            note_link(&line);
                        }
                        if keep_lines {
                            web::remember(line);
                        }
                    }
                    Some("SPOTIFY" | "SP_RING") if new => {
                        let _ = official.try_send(line);
                    }
                    _ => {}
                }
            }
        }
        let _ = child.kill();
        let _ = child.wait();
        let ran = spawned.elapsed();
        tail = match tail {
            Some(_) if ran < Duration::from_secs(3) => None, // this logcat has no -T
            Some(_) => Some("1"),                            // older lines are kept already
            None => None,
        };
        // One that ran a while is restarted soon; one that keeps ending at once, less and less often.
        let steady = ran >= Duration::from_secs(60);
        if steady {
            pause = Duration::from_secs(5);
        }
        thread::sleep(pause);
        if !steady {
            pause = (pause * 2).min(Duration::from_secs(300));
        }
    }
}

/// Official Spotify (eSDK 3.194) sometimes starts a track and never feeds the ring buffer:
/// "PlaybackStarted", then "dequeue  no data condition" with no "came out of no data condition".
fn silent_start_watch(seconds: u64, action: String, rx: mpsc::Receiver<String>) {
    let mut armed: Option<Instant> = None;
    let mut nodata_since: Option<Instant> = None;
    loop {
        // Wake up only when a line arrives or a running no-data period is due.
        let wait = nodata_since
            .map(|t| (t + Duration::from_secs(seconds)).saturating_duration_since(Instant::now()))
            .unwrap_or(Duration::from_secs(60))
            .max(Duration::from_millis(50));
        match rx.recv_timeout(wait) {
            Ok(line) => {
                if line.contains("PLAYBACKEVENT = PlaybackStarted") {
                    armed = Some(Instant::now());
                    nodata_since = None;
                } else if line.contains("dequeue  no data condition") || line.contains("dequeue no data condition") {
                    if armed.map(|t| t.elapsed() < Duration::from_secs(15)).unwrap_or(false) && nodata_since.is_none() {
                        nodata_since = Some(Instant::now());
                    }
                } else if line.contains("came out of no data condition") {
                    nodata_since = None;
                    armed = None;
                }
            }
            Err(mpsc::RecvTimeoutError::Timeout) => {}
            Err(mpsc::RecvTimeoutError::Disconnected) => return,
        }
        if let Some(t) = nodata_since {
            if t.elapsed() >= Duration::from_secs(seconds) {
                nodata_since = None;
                armed = None;
                event(&format!("official Spotify started a track but has fed no audio for {seconds}s (action: {action})"));
                shared().silent_starts += 1;
                let r = match action.as_str() {
                    "pause_resume" => luci_set_once(MB_TRANSPORT, "PAUSE").and_then(|_| {
                        thread::sleep(Duration::from_millis(1500));
                        luci_set_once(MB_TRANSPORT, "RESUME")
                    }),
                    "next" => luci_set_once(MB_TRANSPORT, "NEXT"),
                    "seek0" => luci_set_once(MB_TRANSPORT, "SEEK:0"),
                    _ => Ok(()),
                };
                if let Err(e) = r {
                    log(&format!("silent-start action failed: {e}"));
                }
            }
        }
    }
}

// ── fail-fast routes ─────────────────────────────────────────────────────
// From some ISPs (behind carrier-grade NAT) most new TCP connections to Fastly hang until timeout
// while Akamai answers in ~10 ms. An `unreachable` route makes such connects fail at once, so both
// Spotify clients move on to their next CDN URL immediately instead of stalling for 10 s or more.

/// "a.b.c.d/len" with the host bits cleared, as the kernel keeps a route; None when `s` is not
/// an IPv4 address or network.
fn canonical_route(s: &str) -> Option<String> {
    let (ip, bits) = s.split_once('/').unwrap_or((s, "32"));
    let ip: Ipv4Addr = ip.parse().ok()?;
    if bits.is_empty() || !bits.bytes().all(|b| b.is_ascii_digit()) {
        return None;
    }
    let len: u32 = bits.parse().ok().filter(|l| *l <= 32)?;
    let mask = if len == 0 { 0 } else { u32::MAX << (32 - len) };
    Some(format!("{}/{len}", Ipv4Addr::from(u32::from(ip) & mask)))
}

/// (network, prefix length) of a canonical route.
fn net_of(route: &str) -> Option<(u32, u32)> {
    let (ip, len) = route.split_once('/')?;
    Some((u32::from(ip.parse::<Ipv4Addr>().ok()?), len.parse().ok().filter(|l| *l <= 32)?))
}

/// Do two networks share an address (one contains the other)?
fn overlaps(a: (u32, u32), b: (u32, u32)) -> bool {
    let len = a.1.min(b.1);
    let mask = if len == 0 { 0 } else { u32::MAX << (32 - len) };
    a.0 & mask == b.0 & mask
}

fn fastfail_targets(hosts: &[String], routes: &[String]) -> (Vec<String>, bool) {
    let mut out: Vec<String> = routes.iter().filter_map(|r| canonical_route(r)).collect();
    let mut all_resolved = true;
    for h in hosts {
        if h.is_empty() || !h.chars().all(|c| c.is_ascii_alphanumeric() || c == '.' || c == '-') {
            continue;
        }
        match (h.as_str(), 443).to_socket_addrs() {
            Ok(addrs) => out.extend(addrs.filter_map(|a| match a {
                SocketAddr::V4(v4) => Some(format!("{}/32", v4.ip())),
                SocketAddr::V6(_) => None, // no IPv6 route on this network anyway
            })),
            Err(_) => all_resolved = false,
        }
    }
    // A host name that resolves into a protected range (a local DNS answer) is left alone.
    out.retain(|t| settings::check_network(t).is_ok());
    out.sort();
    out.dedup();
    (out, all_resolved)
}

/// One line of /proc/net/route.
struct KernelRoute {
    net: Ipv4Addr,
    len: u32,
    gateway: Ipv4Addr,
    reject: bool,
}

fn kernel_routes(text: &str) -> Vec<KernelRoute> {
    const RTF_REJECT: u32 = 0x0200;
    text.lines()
        .skip(1)
        .filter_map(|l| {
            let f: Vec<&str> = l.split_whitespace().collect();
            let hex = |i: usize| f.get(i).and_then(|v| u32::from_str_radix(v, 16).ok());
            // Addresses are printed as the native-endian value of the network-order bytes.
            Some(KernelRoute {
                net: Ipv4Addr::from(hex(1)?.to_ne_bytes()),
                len: u32::from_be_bytes(hex(7)?.to_ne_bytes()).count_ones(),
                gateway: Ipv4Addr::from(hex(2)?.to_ne_bytes()),
                reject: hex(3)? & RTF_REJECT != 0,
            })
        })
        .collect()
}

/// The `unreachable` (and `prohibit`) routes the kernel has, as "a.b.c.d/len". Read from
/// /proc/net/route, so no `ip` process is needed.
fn unreachable_routes() -> Vec<String> {
    parse_reject_routes(&fs::read_to_string("/proc/net/route").unwrap_or_default())
}

fn parse_reject_routes(text: &str) -> Vec<String> {
    kernel_routes(text).into_iter().filter(|r| r.reject).map(|r| format!("{}/{}", r.net, r.len)).collect()
}

/// The networks the speaker reaches directly and its gateway: a fail-fast route over any of them
/// would cut it off its own network.
fn own_networks(text: &str) -> Vec<(u32, u32)> {
    kernel_routes(text)
        .into_iter()
        .filter(|r| !r.reject)
        .map(|r| if r.len == 0 { (u32::from(r.gateway), 32) } else { (u32::from(r.net), r.len) })
        .collect()
}

/// Is `ip` inside `route` ("a.b.c.d" or "a.b.c.d/len")?
fn in_route(ip: Ipv4Addr, route: &str) -> bool {
    let (net, len) = route.split_once('/').unwrap_or((route, "32"));
    let (Ok(net), Ok(len)) = (net.parse::<Ipv4Addr>(), len.parse::<u32>()) else { return false };
    if len > 32 {
        return false;
    }
    let mask = if len == 0 { 0 } else { u32::MAX << (32 - len) };
    u32::from(ip) & mask == u32::from(net) & mask
}

/// A host's address stays blocked this long after it was last resolved (CDN addresses move).
const FASTFAIL_KEEP: Duration = Duration::from_secs(24 * 3600);

/// Keeps an `unreachable` route for every configured network and every address the configured
/// hosts resolve to, and removes the ones that are no longer wanted.
fn fastfail_watch(hosts: Vec<String>, routes: Vec<String>) {
    let configured: Vec<String> = routes.iter().filter_map(|r| canonical_route(r)).collect();
    // Routes from an earlier agent: a host address may still be in use and expires like a fresh
    // one; anything the settings no longer ask for goes at once.
    let start = Instant::now();
    let mut expires: HashMap<String, Instant> = unreachable_routes()
        .into_iter()
        .map(|r| {
            let keep = configured.contains(&r) || (r.ends_with("/32") && !hosts.is_empty());
            (r, if keep { start + FASTFAIL_KEEP } else { start })
        })
        .collect();
    let mut skipped: Vec<String> = Vec::new();
    loop {
        let (mut targets, all_resolved) = fastfail_targets(&hosts, &configured);
        let table = fs::read_to_string("/proc/net/route").unwrap_or_default();
        let own = own_networks(&table);
        let (keep, clash): (Vec<String>, Vec<String>) =
            targets.into_iter().partition(|t| net_of(t).is_some_and(|n| !own.iter().any(|o| overlaps(n, *o))));
        targets = keep;
        if !clash.is_empty() && clash != skipped {
            event(&format!("fail-fast: not blocking {} (the speaker's own network or gateway)", clash.join(" ")));
            skipped = clash;
        }
        let now = Instant::now();
        for t in &targets {
            expires.insert(t.clone(), now + FASTFAIL_KEEP);
        }
        let installed = parse_reject_routes(&table);
        let add: Vec<&String> = targets.iter().filter(|t| !installed.contains(t)).collect();
        let del: Vec<&String> = installed.iter().filter(|r| expires.get(*r).is_some_and(|e| *e <= now)).collect();
        let mut failed = false;
        if !add.is_empty() || !del.is_empty() {
            let script: String = add
                .iter()
                .map(|t| format!("ip route replace unreachable {t}\n"))
                .chain(del.iter().map(|r| format!("ip route del unreachable {r}\n")))
                .collect();
            match root_run(&script, Duration::from_secs(8)) {
                Ok(_) => {
                    let list = |v: &[&String]| v.iter().map(|s| s.as_str()).collect::<Vec<_>>().join(" ");
                    if !add.is_empty() {
                        event(&format!("fail-fast routes added: {}", list(&add)));
                    }
                    if !del.is_empty() {
                        event(&format!("fail-fast routes removed: {}", list(&del)));
                    }
                    // The console answering says nothing about the commands: check the kernel.
                    let now_installed = unreachable_routes();
                    let missing = add.iter().any(|t| !now_installed.contains(*t));
                    let left = del.iter().any(|r| now_installed.contains(*r));
                    if missing || left {
                        log("fail-fast routes: the kernel does not show every change yet; trying again in a minute");
                        failed = true;
                    }
                }
                Err(e) => {
                    log(&format!("fail-fast routes via root console failed: {e}"));
                    failed = true;
                }
            }
        }
        // Forget what is gone, unless its route still has to be removed.
        expires.retain(|r, e| *e > now || (failed && installed.contains(r)));
        if hosts.is_empty() && configured.is_empty() && expires.is_empty() {
            return; // nothing to keep up
        }
        // Re-resolve every 30 min (every minute until DNS works or the console answers).
        thread::sleep(Duration::from_secs(if all_resolved && !failed { 1800 } else { 60 }));
    }
}

/// Held by the running watchdog for as long as it lives (the kernel drops it with the process).
const LOCK_PATH: &str = "/tmp/lithify-agent.lock";

/// One watchdog per speaker: an agent started by hand next to the managed one, or one left over,
/// would fight it over librespot and the routes. False when another one holds the lock; a lock
/// that cannot be taken for another reason does not stop this one.
fn take_instance_lock() -> bool {
    use std::os::fd::AsRawFd;
    static HELD: OnceLock<fs::File> = OnceLock::new();
    // Not truncated on open: the pid in it belongs to whoever holds the lock.
    let opened = OpenOptions::new().read(true).write(true).create(true).truncate(false).mode(0o644).open(LOCK_PATH);
    let mut file = match opened.or_else(|_| fs::File::open(LOCK_PATH)) {
        Ok(f) => f,
        Err(e) => {
            log(&format!("cannot open {LOCK_PATH} ({e}): running without the single-instance lock"));
            return true;
        }
    };
    // SAFETY: flock(2) on a descriptor owned by `file`, which is open during the call.
    if unsafe { flock(file.as_raw_fd(), LOCK_EX | LOCK_NB) } != 0 {
        let e = io::Error::last_os_error();
        if e.raw_os_error() == Some(EWOULDBLOCK) {
            let holder = fs::read_to_string(LOCK_PATH).unwrap_or_default();
            log(&format!("another lithify-agent (pid {}) is running already: this one exits", holder.trim()));
            return false;
        }
        log(&format!("cannot lock {LOCK_PATH} ({e}): running without the single-instance lock"));
        return true;
    }
    // Who holds it, for the message above.
    let _ = file.set_len(0).and_then(|_| file.write_all(format!("{}\n", process::id()).as_bytes()));
    let _ = HELD.set(file);
    true
}

fn cmd_run() -> i32 {
    // Audio first: the watchdog and the page give way to librespot and the speaker's services on
    // its two cores (threads and children started from here inherit this).
    // SAFETY: nice(2) only lowers this thread's priority.
    unsafe { nice(10) };
    if !take_instance_lock() {
        // A process manager that starts it again at once must not make that a busy loop.
        thread::sleep(Duration::from_secs(5));
        return 0;
    }
    start_log_writer();
    sweep_root_scripts();
    let cfg = Config::load();
    log(&format!(
        "watchdog start ({}): spin>={:.0}% for {}s (cooldown {}s), respawn_missing={}, hide_official={}, silent_start={}s/{}, conflict_stop_librespot={}, fastfail_hosts={:?}, fastfail_routes={:?}, ui={} port {} pin={}",
        base_dir().display(),
        cfg.spin_threshold * 100.0,
        cfg.spin_seconds,
        cfg.spin_cooldown,
        cfg.respawn_missing,
        cfg.hide_official,
        cfg.silent_seconds,
        cfg.silent_action,
        cfg.conflict_stop_librespot,
        cfg.fastfail_hosts,
        cfg.fastfail_routes,
        cfg.ui,
        cfg.ui_port,
        if cfg.ui_pin.is_empty() { "off" } else { "on" }
    ));
    if !cfg.wifi_scancfg.is_empty() {
        if cfg.wifi_scancfg.chars().all(|c| c.is_ascii_digit() || c == ' ') {
            match root_run(&format!("mlanutl wlan0 scancfg {}\n", cfg.wifi_scancfg), Duration::from_secs(6)) {
                Ok(o) => log(&format!("wifi scancfg {} applied: {o:?}", cfg.wifi_scancfg)),
                Err(e) => log(&format!("wifi scancfg failed: {e}")),
            }
        } else {
            log("config: wifi_scancfg must be digits and spaces only; ignored");
        }
    }
    kill_stale_readers();
    // Always started: with no hosts and networks set it removes the routes left from before.
    let (hosts, routes) = (cfg.fastfail_hosts.clone(), cfg.fastfail_routes.clone());
    spawn_named("fastfail", move || fastfail_watch(hosts, routes));
    let conflict = cfg.conflict_stop_librespot;
    spawn_named("luci", move || luci_watch(conflict));
    let (hide, ui) = (cfg.hide_official, cfg.ui);
    spawn_named("sampler", move || sampler(hide, ui));
    let (tx, rx) = mpsc::sync_channel(OFFICIAL_QUEUE);
    let keep_lines = cfg.ui;
    spawn_named("logcat", move || logcat_reader(tx, keep_lines));
    let (secs, action) = (cfg.silent_seconds, cfg.silent_action.clone());
    spawn_named("silent-start", move || silent_start_watch(secs, action, rx));
    if cfg.ui {
        let web_cfg = cfg.clone();
        spawn_named("web", move || web::serve(web_cfg));
        spawn_named("names", web::name_watch); // the page warns of another device with this name
    }

    let tck = clk_tck();
    let mut spin = SpinWatch::new();
    let mut zeroconf = ZeroconfWatch::new();
    let mut crashes = CrashWatch::new();
    let mut shells = ShellWatch::new();
    let mut link = LinkWatch { last_restart: None };
    let mut last_beat = Instant::now();
    loop {
        spin.tick(&cfg, tck);
        shells.tick(tck);
        zeroconf.tick();
        link.tick();
        trial_tick();
        crashes.tick();
        if last_beat.elapsed() >= Duration::from_secs(1800) {
            last_beat = Instant::now();
            log(&format!(
                "heartbeat: spotifyhifi={:?} max_cpu_30min={:.0}% librespot={:?}",
                spin.pid,
                spin.max_rate * 100.0,
                find_pid("librespot"),
            ));
            spin.max_rate = 0.0;
        }
        thread::sleep(Duration::from_secs(5));
    }
}

// ── spinning firmware shells ─────────────────────────────────────────────
// The firmware runs its event hooks through `sh -c` (luci_service: `sh -c LUCI_local 494
// StationConnected` on a Wi-Fi event). That ash inherits SIGCHLD=SIG_IGN, so once its command
// has finished it spins forever: a whole core, until the next reboot. An orphaned `sh -c` with
// no child left that keeps a core busy for 30 s is stopped (its command is long done).

struct ShellWatch {
    next_scan: Instant,
    /// pid -> (CPU ticks at the last look, when, looks in a row at a full core)
    watched: HashMap<i32, (u64, Instant, u32)>,
}

impl ShellWatch {
    fn new() -> ShellWatch {
        ShellWatch { next_scan: Instant::now(), watched: HashMap::new() }
    }

    fn tick(&mut self, tck: f64) {
        let now = Instant::now();
        if now >= self.next_scan {
            self.next_scan = now + Duration::from_secs(60);
            for pid in find_pids("sh") {
                if let (Some(st), false) = (proc_stat(&format!("/proc/{pid}/stat")), self.watched.contains_key(&pid)) {
                    if st.ppid == 1 && proc_args(pid).first().map(String::as_str) == Some("-c") {
                        self.watched.insert(pid, (st.ticks, now, 0));
                    }
                }
            }
        }
        let mut stuck = Vec::new();
        self.watched.retain(|pid, (ticks, at, hot)| {
            let Some(st) = proc_stat(&format!("/proc/{pid}/stat")).filter(|st| st.ppid == 1) else { return false };
            let dt = at.elapsed().as_secs_f64();
            if dt < 1.0 {
                return true;
            }
            let rate = st.ticks.saturating_sub(*ticks) as f64 / tck / dt;
            (*ticks, *at) = (st.ticks, Instant::now());
            if rate < 0.8 {
                return false; // busy for a moment only: the next scan looks again
            }
            *hot += 1;
            if *hot >= 6 {
                stuck.push(*pid);
                return false;
            }
            true
        });
        for pid in stuck {
            if has_children(pid) {
                continue;
            }
            let what = proc_args(pid).join(" ");
            // The root script checks again: pid numbers wrap quickly here (pid_max is 4096).
            let script = format!(
                "c=$(busybox tr \"\\0\" \" \" < /proc/{pid}/cmdline)\n\
                 case \"$c\" in \"sh -c \"*) kill -9 {pid} && echo stopped ;; esac\n"
            );
            match root_run(&script, Duration::from_secs(6)) {
                Ok(out) if out.contains("stopped") => {
                    event(&format!("stopped a firmware shell that kept a whole core busy (pid {pid}: sh {what})"));
                    shared().shells_stopped += 1;
                }
                Ok(_) => {}
                Err(e) => log(&format!("could not stop the spinning shell {pid}: {e}")),
            }
        }
    }
}

/// Has any process `pid` as its parent?
fn has_children(pid: i32) -> bool {
    fs::read_dir("/proc").map_or(true, |rd| {
        rd.flatten().any(|e| {
            e.file_name()
                .to_str()
                .and_then(|n| n.parse::<i32>().ok())
                .and_then(|p| proc_stat(&format!("/proc/{p}/stat")))
                .is_some_and(|st| st.ppid == pid)
        })
    })
}

// ── librespot health ─────────────────────────────────────────────────────

// librespot can be left on a dead session: when the connection to Spotify's access point
// closes, it shuts the session down, but spirc checks for that only when something else wakes
// it. Idle, it stays parked; the next command from an app then fails (no audio key) and starts
// it over with a blank state. A dropped dealer websocket can likewise leave it on stale
// channels for hours. The logcat reader notes such a loss, a new login clears it; a loss left
// standing for a while gets librespot restarted (never while it plays: then it notices itself).

/// What a line of librespot's log says about its connection to Spotify.
#[derive(Clone, Copy, Debug, PartialEq)]
enum LinkNote {
    Lost,
    Back,
}

/// librespot's own lines only ("[time LEVEL librespot_x::y] message"), not the agent's.
fn link_note(line: &str) -> Option<LinkNote> {
    let (_, rest) = line.split_once(" librespot_")?;
    let (module, msg) = rest.split_once("] ")?;
    if module == "core::session" {
        if msg.starts_with("Authenticated as") {
            return Some(LinkNote::Back);
        }
        if msg.starts_with("Connection to server closed.") {
            return Some(LinkNote::Lost);
        }
    }
    // A dealer whose websocket stops answering can leave spirc on stale channels. One that fails
    // to connect tries again by itself, and the dealer of a session that a new login replaces
    // says the same when it closes: that line is no loss.
    let lost = msg.contains("session lost connection to server") || msg.starts_with("Websocket peer does not respond.");
    lost.then_some(LinkNote::Lost)
}

/// Since when librespot has been without its connection (the first loss since its last login).
static LINK_LOST: Mutex<Option<Instant>> = Mutex::new(None);

fn link_lost() -> MutexGuard<'static, Option<Instant>> {
    LINK_LOST.lock().unwrap_or_else(|e| e.into_inner())
}

/// When librespot last signed in.
static LINK_LOGIN: Mutex<Option<Instant>> = Mutex::new(None);

/// A loss reported this soon after a login is the old session's, closed by the login.
const LINK_SWITCH: Duration = Duration::from_secs(10);

/// Does a loss count, librespot having signed in `since_login` ago (None: not since the agent
/// started)?
fn loss_counts(since_login: Option<Duration>) -> bool {
    since_login.is_none_or(|d| d >= LINK_SWITCH)
}

fn note_link(line: &str) {
    let mut login = LINK_LOGIN.lock().unwrap_or_else(|e| e.into_inner());
    match link_note(line) {
        Some(LinkNote::Lost) if loss_counts(login.map(|t| t.elapsed())) => {
            link_lost().get_or_insert_with(Instant::now);
        }
        Some(LinkNote::Lost) | None => {}
        Some(LinkNote::Back) => {
            *link_lost() = None;
            *login = Some(Instant::now());
        }
    }
}

/// librespot gets this long to reconnect by itself.
const LINK_GRACE: Duration = Duration::from_secs(20);
/// At most one restart this often: while the network itself is down, restarts do not help.
const LINK_RESTART_GAP: Duration = Duration::from_secs(600);

/// Restart librespot for a loss noted `lost_for` ago, the last such restart `since_last` ago?
/// `playing` is asked last (it looks at librespot's files).
fn link_restart_due(lost_for: Duration, since_last: Option<Duration>, playing: impl FnOnce() -> bool) -> bool {
    lost_for >= LINK_GRACE && since_last.is_none_or(|d| d >= LINK_RESTART_GAP) && !playing()
}

struct LinkWatch {
    last_restart: Option<Instant>,
}

impl LinkWatch {
    fn tick(&mut self) {
        let Some(lost) = *link_lost() else { return };
        let Some(pid) = find_pid("librespot") else { return };
        if !link_restart_due(lost.elapsed(), self.last_restart.map(|t| t.elapsed()), || holds_pcm_now(pid)) {
            return;
        }
        event("librespot lost its connection to Spotify and did not reconnect: restarting it");
        self.last_restart = Some(Instant::now());
        *link_lost() = None;
        restart_librespot_now(); // a stop of the agent's own: no sign of a crash loop
    }
}

/// A librespot that runs but no longer answers on its zeroconf port cannot be found by Spotify
/// apps on the LAN. It is restarted after three failed checks a minute apart, never while it
/// plays, and only when discovery is on.
struct ZeroconfWatch {
    next: Instant,
    failures: u32,
    restarts: usize,
}

/// Minutes between restarts while the problem stays: a cause that persists (its port taken when
/// it started) must not drop a paused session every few minutes.
const ZEROCONF_BACKOFF_MIN: [u64; 4] = [3, 10, 30, 60];

impl ZeroconfWatch {
    fn new() -> ZeroconfWatch {
        ZeroconfWatch { next: Instant::now() + Duration::from_secs(60), failures: 0, restarts: 0 }
    }

    fn tick(&mut self) {
        if Instant::now() < self.next {
            return;
        }
        self.next = Instant::now() + Duration::from_secs(60);
        let Some(pid) = find_pid("librespot") else {
            self.failures = 0;
            return;
        };
        let args = proc_args(pid);
        if args.iter().any(|a| a == "--disable-discovery" || a == "-O") || proc_uptime_s(pid).unwrap_or(0) < 90 {
            self.failures = 0;
            return;
        }
        if web::librespot_info(&args).is_some() {
            self.failures = 0;
            self.restarts = 0;
            return;
        }
        self.failures += 1;
        log(&format!("librespot (pid {pid}) did not answer its zeroconf request ({} in a row)", self.failures));
        if self.failures >= 3 && !holds_pcm_now(pid) {
            event(&format!("librespot (pid {pid}) has stopped answering on its zeroconf port: restarting it"));
            note_time(STOPS_FILE);
            signal_or_log(pid, SIGTERM);
            self.failures = 0;
            let wait = ZEROCONF_BACKOFF_MIN[self.restarts.min(ZEROCONF_BACKOFF_MIN.len() - 1)];
            self.restarts += 1;
            self.next = Instant::now() + Duration::from_secs(60 * wait);
        }
    }
}

// ── settings on trial ────────────────────────────────────────────────────
// librespot options changed on the web page must let librespot keep running. Otherwise the
// previous settings come back by themselves: a bad value must not leave the speaker without
// Spotify until someone fixes it by hand.

const TRIAL_FILE: &str = "/tmp/lithify-settings-trial";
/// librespot counts as working once it has run this long with the new settings.
const TRIAL_OK_SECONDS: u64 = 20;
/// ... and gets this long to get there.
const TRIAL_SECONDS: f64 = 90.0;

/// Called by the web page after it saved librespot options, before it restarts librespot. The
/// trial is a file, so it survives the agent's own restart (agent options saved at the same time).
fn trial_start(keys: &str) {
    let old = find_pid("librespot").unwrap_or(0);
    write_state(TRIAL_FILE, &format!("{:.0} {old} {keys}\n", system_uptime().unwrap_or(0.0)));
}

fn trial_tick() {
    let Ok(text) = fs::read_to_string(TRIAL_FILE) else { return };
    let mut f = text.split_whitespace();
    let (Some(since), Some(old)) = (f.next().and_then(|v| v.parse::<f64>().ok()), f.next().and_then(|v| v.parse::<i32>().ok()))
    else {
        let _ = fs::remove_file(TRIAL_FILE);
        return;
    };
    let keys: Vec<&str> = f.collect();
    let new = find_pid("librespot").filter(|p| *p != old);
    if new.and_then(proc_uptime_s).is_some_and(|up| up >= TRIAL_OK_SECONDS) {
        let _ = fs::remove_file(TRIAL_FILE);
        log(&format!("librespot runs with the new settings ({})", keys.join(" ")));
        return;
    }
    let now = system_uptime();
    if now.is_some_and(|now| now - since < TRIAL_SECONDS) {
        return;
    }
    let _ = fs::remove_file(TRIAL_FILE);
    if now.is_some() {
        restore_good(&format!("librespot did not keep running after the web page changed {}", keys.join(" ")));
    }
}

/// Put back librespot's options from the last settings it ran well with, and restart it.
fn restore_good(why: &str) -> bool {
    match settings::restore_good() {
        Ok(true) => {
            let _ = fs::remove_file(TRIAL_FILE); // whatever was on trial is gone now
            event(&format!("{why}: the last settings that worked are back"));
            restart_librespot_now();
            true
        }
        Ok(false) => {
            log(&format!("{why}, and no other settings are known to work"));
            false
        }
        Err(e) => {
            log(&format!("{why}; cannot restore the last settings that worked: {e}"));
            false
        }
    }
}

// ── librespot that keeps exiting ─────────────────────────────────────────

/// Notices librespot starting over and over (the exec wrapper notes each start): first the last
/// settings that worked come back; when the settings are not the cause and a new version was
/// installed just before, the previous version comes back. Also keeps the copy of good settings.
struct CrashWatch {
    handled: f64,
    next_good_check: Instant,
    told: Option<Instant>,
}

/// An install leaves this mark (system uptime) for the new version's first minutes.
const INSTALLED_FILE: &str = "/tmp/lithify-installed";

impl CrashWatch {
    fn new() -> CrashWatch {
        CrashWatch { handled: 0.0, next_good_check: Instant::now() + Duration::from_secs(60), told: None }
    }

    fn tick(&mut self) {
        if Instant::now() >= self.next_good_check {
            self.next_good_check = Instant::now() + Duration::from_secs(60);
            self.remember_good();
        }
        let Some(now) = system_uptime() else { return };
        let newest = librespot_starts().into_iter().fold(0.0, f64::max);
        // Five unexplained starts within five minutes, the newest under a minute ago, librespot
        // not running steadily now, not handled yet.
        let steady = find_pid("librespot").and_then(proc_uptime_s).is_some_and(|up| up >= 30);
        if unexplained_starts(now, 300.0) < 5 || now - newest > 60.0 || newest <= self.handled || steady {
            return;
        }
        self.handled = newest;
        if restore_good("librespot keeps exiting right after it starts") {
            return;
        }
        let installed = fs::read_to_string(INSTALLED_FILE).ok().and_then(|t| t.trim().parse::<f64>().ok());
        if installed.is_some_and(|at| now - at < 900.0) {
            let _ = fs::remove_file(INSTALLED_FILE);
            event("librespot keeps exiting since the new version was installed: rolling back to the previous one");
            web::rollback_now();
            return;
        }
        if self.told.is_none_or(|t| t.elapsed() > Duration::from_secs(3600)) {
            self.told = Some(Instant::now());
            event("librespot keeps exiting right after it starts: see its log on this page");
        }
    }

    /// librespot has run a minute with settings that have not changed since it started: those work.
    fn remember_good(&self) {
        let Some(pid) = find_pid("librespot") else { return };
        let Some(up) = proc_uptime_s(pid).filter(|up| *up >= 60) else { return };
        let changed = fs::metadata(settings::dir().join("settings.conf")).and_then(|m| m.modified());
        let unchanged_since_start =
            changed.ok().and_then(|t| t.elapsed().ok()).is_some_and(|age| age.as_secs() > up);
        if unchanged_since_start {
            match settings::remember_good() {
                Ok(true) => log("these settings work: kept as the last good ones"),
                Ok(false) => {}
                Err(e) => log(&format!("cannot keep the good settings: {e}")),
            }
        }
    }
}

// ── subcommands: status, luci ────────────────────────────────────────────

fn cmd_status() -> i32 {
    let tck = clk_tck();
    match find_pid("spotifyhifi") {
        Some(pid) => {
            let a = thread_ticks(pid);
            thread::sleep(Duration::from_secs(2));
            let b = thread_ticks(pid);
            let mut rates: Vec<(i32, f64)> = b
                .iter()
                .map(|(tid, t)| (*tid, t.saturating_sub(*a.get(tid).unwrap_or(t)) as f64 / tck / 2.0))
                .collect();
            rates.sort_by(|x, y| y.1.total_cmp(&x.1));
            let top: Vec<String> = rates.iter().take(3).map(|(t, r)| format!("{t}:{:.0}%", r * 100.0)).collect();
            println!("spotifyhifi pid {pid}, busiest threads {}", top.join(" "));
        }
        None => println!("spotifyhifi not running"),
    }
    match find_pid("librespot") {
        Some(pid) => println!("librespot pid {pid}, holds PCM: {}", holds_pcm_now(pid)),
        None => println!("librespot not running"),
    }
    let pcm = read_within(PCM_STATUS, Duration::from_secs(2));
    println!("PCM status: {}", if pcm.fresh { pcm.value.unwrap_or_default() } else { "(no answer)".into() }.trim());
    let tmp = settings::LIBRESPOT_TMP;
    let own = fs::read_to_string("/proc/mounts").is_ok_and(|m| is_mount_point(&m, tmp));
    let held: u64 = fs::read_dir(tmp).map_or(0, |rd| rd.flatten().filter_map(|e| e.metadata().ok()).map(|m| m.len()).sum());
    println!("librespot downloads: {tmp} ({}, {} KB now)", if own { "a tmpfs of its own" } else { "a plain dir in /tmp" }, held / 1024);
    let (card, control) = mixer_of(&librespot_args(), "Master");
    println!(
        "volume {control} (card {card}): {:?} read directly, {:?} from amixer",
        alsa_volume(&card, &control),
        amixer_volume(&card, &control)
    );
    let ff = unreachable_routes();
    println!("fail-fast routes: {}", if ff.is_empty() { "none".to_string() } else { ff.join(" ") });
    match Luci::connect() {
        Ok(mut l) => {
            let _ = l.send(CMD_GET, MB_SOURCE, "");
            let _ = l.send(CMD_GET, MB_PLAY_STATE, "");
            let end = Instant::now() + Duration::from_millis(1500);
            while Instant::now() < end {
                for (mbid, p) in l.poll(Duration::from_millis(300)).unwrap_or_default() {
                    if mbid == MB_SOURCE || mbid == MB_PLAY_STATE {
                        println!("LUCI MB#{mbid} = {p:?}");
                    }
                }
            }
        }
        Err(e) => println!("LUCI: {e}"),
    }
    0
}

fn cmd_luci(args: &[String]) -> i32 {
    let (Some(mbid), Some(payload)) = (args.first().and_then(|s| s.parse::<u16>().ok()), args.get(1)) else {
        eprintln!("usage: lithify-agent luci MBID PAYLOAD");
        return 2;
    };
    match Luci::connect() {
        Ok(mut l) => {
            if let Err(e) = l.send(CMD_SET, mbid, payload) {
                eprintln!("send failed: {e}");
                return 1;
            }
            let end = Instant::now() + Duration::from_secs(1);
            while Instant::now() < end {
                for (m, p) in l.poll(Duration::from_millis(300)).unwrap_or_default() {
                    println!("MB#{m}: {p:?}");
                }
            }
            0
        }
        Err(e) => {
            eprintln!("LUCI connect failed: {e}");
            1
        }
    }
}

fn main() {
    let args: Vec<String> = env::args().skip(1).collect();
    // Started from the speaker's console we inherit SIGCHLD=SIG_IGN, which breaks every
    // Command::output()/wait() (ECHILD); restore the default for all subcommands.
    // SAFETY: SIG_DFL installs no handler; no other thread exists yet.
    unsafe { signal(SIGCHLD, SIG_DFL) };
    // The Cast process_manager parses commands with Chromium's base::CommandLine, which moves
    // "--switch" arguments in front of positional ones: `exec --args-file F -- PROG` arrives as
    // `--args-file exec F -- PROG`. Accept that form; process.json itself uses `exec-file`.
    let args = if args.first().map(String::as_str) == Some("--args-file")
        && args.get(1).map(String::as_str) == Some("exec")
    {
        let mut v = vec!["exec".to_string(), "--args-file".to_string()];
        v.extend(args[2..].iter().cloned());
        v
    } else {
        args
    };
    let code = match args.first().map(String::as_str) {
        Some("exec") => cmd_exec(&args[1..]),
        // exec-file FILE PROG [ARGS..]: positional-only form for the Cast process list
        Some("exec-file") if args.len() >= 3 => {
            let mut v = vec!["--args-file".to_string(), args[1].clone(), "--".to_string()];
            v.extend(args[2..].iter().cloned());
            cmd_exec(&v)
        }
        Some("onevent") => cmd_onevent(),
        Some("settings-apply") => settings::cmd_apply(&args[1..]),
        Some("run") => cmd_run(),
        Some("status") => cmd_status(),
        Some("luci") => cmd_luci(&args[1..]),
        Some("version") | Some("--version") => {
            println!("lithify-agent {}", env!("CARGO_PKG_VERSION"));
            0
        }
        _ => {
            eprintln!(
                "usage: lithify-agent exec-file FILE PROG | onevent | run | status | settings-apply BASE | luci MBID PAYLOAD | version"
            );
            2
        }
    };
    process::exit(code);
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn packet_layout_matches_lithe_reference() {
        let p = luci_packet(CMD_SET, MB_TRANSPORT, "PAUSE");
        assert_eq!(&p[..10], &[0xAA, 0xAA, 2, 40, 0, 0, 0, 0, 5, 0]);
        assert_eq!(&p[10..15], b"PAUSE");
        assert_eq!(p[15], 0);
    }

    #[test]
    fn parses_ls9_big_endian_replies_back_to_back() {
        // Captured from the speaker on 2026-10-06: MB#3 ack, then MB#50 "4" and MB#51 "2".
        let mut rx = vec![
            0x00, 0x00, 0x02, 0x00, 0x03, 0x01, 0x27, 0xac, 0x00, 0x00, //
            0x00, 0x00, 0x01, 0x00, 0x32, 0x01, 0xc3, 0x5e, 0x00, 0x01, 0x34, //
            0x00, 0x00, 0x01, 0x00, 0x33, 0x01, 0x09, 0xc9, 0x00, 0x01, 0x32, //
            0x00, 0x00, 0x01, // incomplete tail stays buffered
        ];
        let got = luci_parse(&mut rx);
        assert_eq!(got, vec![(3, String::new()), (50, "4".into()), (51, "2".into())]);
        assert_eq!(rx, vec![0x00, 0x00, 0x01]);
    }

    #[test]
    fn fastfail_targets_are_validated() {
        let ok = |s: &str| settings::check_network(s).is_ok();
        assert!(ok("199.232.0.0/16"));
        assert!(ok("199.232.210.248"));
        assert!(!ok("199.232.0.0/33"));
        assert!(!ok("199.232.0.0/16; reboot"));
        assert!(!ok("2a04:4e42::/32"));
        // Never the speaker's own world: everything, private, loopback, multicast, broadcast.
        for bad in ["0.0.0.0/0", "1.0.0.0/7", "10.1.2.3", "172.20.0.0/16", "192.168.1.1", "192.0.0.0/2",
                    "127.0.0.1", "169.254.1.1", "100.64.0.1", "224.0.0.251", "255.255.255.255"] {
            assert!(!ok(bad), "{bad}");
        }
        let (t, _) = fastfail_targets(&[], &["199.232.0.0/16".into(), "bad".into(), "199.232.0.0/16".into(), "10.0.0.0/8".into()]);
        assert_eq!(t, vec!["199.232.0.0/16".to_string()]);
    }

    #[test]
    fn the_speakers_own_network_is_known() {
        let t = "Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT\n\
                 wlan0\t00000000\t0101A8C0\t0003\t0\t0\t304\t00000000\t0\t0\t0\n\
                 *\tF87A4B92\t00000000\t0205\t0\t0\t0\tFFFFFFFF\t0\t0\t0\n\
                 wlan0\t0001A8C0\t00000000\t0001\t0\t0\t304\t00FFFFFF\t0\t0\t0\n";
        let own = own_networks(t);
        let ip = |s: &str| u32::from(s.parse::<Ipv4Addr>().unwrap());
        assert_eq!(own, vec![(ip("192.168.1.1"), 32), (ip("192.168.1.0"), 24)]);
        assert!(overlaps((ip("192.168.1.10"), 32), own[1]));
        assert!(overlaps((ip("192.0.0.0"), 8), own[0])); // a large network over the gateway
        assert!(!overlaps((ip("199.232.0.0"), 16), own[1]));
    }

    #[test]
    fn option_values_are_found() {
        let a: Vec<String> = ["--mixer", "alsa", "--alsa-mixer-control", "Master"].iter().map(|s| s.to_string()).collect();
        assert_eq!(opt_value(&a, "--mixer"), Some("alsa"));
        assert_eq!(opt_value(&a, "--alsa-mixer-control"), Some("Master"));
        assert_eq!(opt_value(&a, "--alsa-mixer-device"), None);
    }

    #[test]
    fn agent_conf_is_parsed_with_limits() {
        let c = Config::parse(
            "# comment\nui=0\nui_port=80\nui_pin=2468\ncompanion_url=http://192.168.1.10:8095/\n\
             speaker_id=lazienka\nfastfail_hosts=a.example b.example\nspin_threshold_pct=10\nrespawn_official_spotify=0\n\
             hide_official_spotify=1\n",
        );
        assert!(c.hide_official);
        assert!(!Config::parse("").hide_official); // shown unless asked
        assert!(!c.ui);
        assert_eq!(c.ui_port, 8090); // ports below 1024 are refused
        assert_eq!(c.ui_pin, "2468");
        assert_eq!(c.companion_url, "http://192.168.1.10:8095");
        assert_eq!(c.speaker_id, "lazienka");
        assert_eq!(c.fastfail_hosts, vec!["a.example".to_string(), "b.example".to_string()]);
        assert!((c.spin_threshold - 0.3).abs() < 1e-9); // clamped
        assert!(!c.respawn_missing);
    }

    #[test]
    fn routes_are_canonical() {
        assert_eq!(canonical_route("199.232.1.2/16").as_deref(), Some("199.232.0.0/16"));
        assert_eq!(canonical_route("199.232.210.248").as_deref(), Some("199.232.210.248/32"));
        assert_eq!(canonical_route("0.0.0.0/0").as_deref(), Some("0.0.0.0/0"));
        assert_eq!(canonical_route("1.2.3.4/+16"), None);
        assert_eq!(canonical_route("1.2.3.4/"), None);
        assert_eq!(canonical_route("1.2.3.4/16 ; reboot"), None);
    }

    #[test]
    fn kernel_reject_routes_are_read() {
        // /proc/net/route on the speaker (2026-10-06): a default route, the LAN, two unreachable.
        let t = "Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT\n\
                 wlan0\t00000000\t0101A8C0\t0003\t0\t0\t304\t00000000\t0\t0\t0\n\
                 *\tF87A4B92\t00000000\t0205\t0\t0\t0\tFFFFFFFF\t0\t0\t0\n\
                 wlan0\t0001A8C0\t00000000\t0001\t0\t0\t304\t00FFFFFF\t0\t0\t0\n\
                 *\t0000E8C7\t00000000\t0201\t0\t0\t0\t0000FFFF\t0\t0\t0\n";
        assert_eq!(parse_reject_routes(t), vec!["146.75.122.248/32".to_string(), "199.232.0.0/16".to_string()]);
    }

    #[test]
    fn logcat_tags_are_found() {
        assert_eq!(logcat_tag("10-06 19:11:45.697 E/SPOTIFY ( 1383): dequeue  no data condition"), Some("SPOTIFY"));
        assert_eq!(logcat_tag("10-06 19:11:45.697 I/lithify-agent( 3979): [2026 INFO librespot] x/y (z)"), Some("lithify-agent"));
        assert_eq!(logcat_tag("--------- beginning of main"), None);
    }

    #[test]
    fn routes_match_prefixes() {
        let ip: Ipv4Addr = "199.232.210.248".parse().unwrap();
        assert!(in_route(ip, "199.232.210.248"));
        assert!(in_route(ip, "199.232.0.0/16"));
        assert!(!in_route(ip, "199.233.0.0/16"));
        assert!(in_route(ip, "0.0.0.0/0"));
        assert!(!in_route(ip, "default"));
    }

    #[test]
    fn civil_dates() {
        assert_eq!(civil_from_days(0), (1970, 1, 1));
        assert_eq!(civil_from_days(20_732), (2026, 10, 6));
    }

    #[test]
    fn http_dates() {
        assert_eq!(http_date_of(0), "Thu, 01 Jan 1970 00:00:00 GMT");
        assert_eq!(http_date_of(20_733 * 86_400 + 13 * 3600 + 45 * 60 + 7), "Wed, 07 Oct 2026 13:45:07 GMT");
        assert_eq!(http_date().len(), 29);
    }

    /// Tests that use the helper threads (there are only a few) run one at a time.
    static SERIAL: Mutex<()> = Mutex::new(());

    pub(crate) fn serial() -> MutexGuard<'static, ()> {
        SERIAL.lock().unwrap_or_else(|e| e.into_inner())
    }

    #[test]
    fn proc_stat_fields_are_found() {
        // a command name with spaces and parentheses, as the kernel prints it
        let st = parse_stat("1383 (spot (hi) x) S 1 1383 1383 0 -1 4194560 2711 0 0 0 1200 345 0 0 20 0 41 0 5321 1 2").unwrap();
        assert_eq!((st.state, st.ppid, st.ticks, st.start), ('S', 1, 1545, 5321));
        assert!(parse_stat("1 (x) S 1 2 3").is_none());
        assert!(parse_stat("no parenthesis at all").is_none());
    }

    #[test]
    fn agent_conf_numbers_stay_in_their_ranges() {
        let c = Config::parse(
            "spin_threshold_pct=NaN\nspin_seconds=99999\nspin_cooldown_seconds=-5\nrespawn_grace_seconds=1\n\
             silent_start_seconds=100000\nsilent_start_action=reboot\nui_port=65536\n",
        );
        assert!((c.spin_threshold - 0.85).abs() < 1e-9); // NaN is no number: the default stays
        assert_eq!((c.spin_seconds, c.spin_cooldown, c.respawn_grace, c.silent_seconds), (3600, 0, 30, 600));
        assert_eq!(c.silent_action, "log"); // not one of the choices
        assert_eq!(c.ui_port, 8090);
        let c = Config::parse("spin_threshold_pct=inf\nspin_threshold_pct=250\n");
        assert!((c.spin_threshold - 1.0).abs() < 1e-9);
        let c = Config::parse("silent_start_action=next\nui_port=9000\nspin_cooldown_seconds=30\n");
        assert_eq!((c.silent_action.as_str(), c.ui_port, c.spin_cooldown), ("next", 9000, 30));
    }

    #[test]
    fn console_replies_end_at_the_marker() {
        let mut shown = String::new();
        let mut r = ConsoleReply::new(CONSOLE_DONE);
        assert!(!r.push(b">pidof: 12", &mut |s| shown.push_str(s)));
        assert!(!r.push(b"34\nLITHIFY_", &mut |s| shown.push_str(s)));
        assert!(!shown.contains("LITH"), "{shown:?}"); // what may be the marker's start waits
        assert!(r.push(b"DONE\nafter", &mut |s| shown.push_str(s)));
        assert!(r.push(b"more", &mut |s| shown.push_str(s))); // nothing after it counts
        let out = r.finish(&mut |s| shown.push_str(s));
        assert_eq!((out.as_str(), shown.as_str()), (">pidof: 1234\n", ">pidof: 1234\n"));
        // a reply cut short (no marker): all of it, at the end
        let mut shown = String::new();
        let mut r = ConsoleReply::new(CONSOLE_DONE);
        r.push(b"partial LITHIFY_DON", &mut |s| shown.push_str(s));
        assert_eq!(r.finish(&mut |s| shown.push_str(s)), "partial LITHIFY_DON");
        assert_eq!(shown, "partial LITHIFY_DON");
    }

    #[test]
    fn console_output_keeps_whole_characters_and_its_end() {
        let text = "Łazienka: zainstalowano – gotowe ".repeat(5);
        let (mut shown, mut r) = (String::new(), ConsoleReply::new(CONSOLE_DONE));
        for b in text.as_bytes() {
            r.push(&[*b], &mut |s| shown.push_str(s)); // byte by byte: characters split everywhere
        }
        assert_eq!(r.finish(&mut |s| shown.push_str(s)), text);
        assert_eq!(shown, text);
        let (mut n, mut r) = (0, ConsoleReply::new(CONSOLE_DONE));
        for _ in 0..300 {
            r.push(&[b'x'; 1000], &mut |s| n += s.len());
        }
        let out = r.finish(&mut |s| n += s.len());
        assert!(out.len() <= 2 * CONSOLE_KEEP && out.len() >= CONSOLE_KEEP);
        assert_eq!(n, 300_000); // all of it was shown, once
        assert_eq!(utf8_whole("aŁb".as_bytes(), 2), 1);
        assert_eq!(utf8_whole("aŁb".as_bytes(), 3), 3);
        assert_eq!(utf8_whole("aŁb".as_bytes(), 9), 4);
        assert_eq!(utf8_whole("€".as_bytes(), 2), 0);
    }

    /// A console like the speaker's: greets in two pieces, takes one CRLF line, answers, then
    /// keeps the connection open (as the real one does after the script has ended).
    fn fake_console(greeting: &'static [&'static [u8]], reply: &'static [u8]) -> (SocketAddr, mpsc::Receiver<String>) {
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let addr = listener.local_addr().unwrap();
        let (tx, rx) = mpsc::channel();
        thread::spawn(move || {
            let (mut s, _) = listener.accept().unwrap();
            for part in greeting {
                let _ = s.write_all(part);
                thread::sleep(Duration::from_millis(50));
            }
            let mut line = Vec::new();
            let mut b = [0u8; 1];
            while !line.ends_with(b"\r\n") && s.read(&mut b).unwrap_or(0) == 1 {
                line.push(b[0]);
            }
            let _ = s.write_all(reply);
            let _ = tx.send(String::from_utf8_lossy(&line).into_owned());
            thread::sleep(Duration::from_secs(2)); // open, silent: the client must not wait for this
        });
        (addr, rx)
    }

    #[test]
    fn a_console_session_ends_at_its_marker() {
        let (addr, server) = fake_console(&[b"CONNEC", b"TED!!\r\n"], "pidof: 1234\nŁ\nLITHIFY_DONE\n".as_bytes());
        let (started, mut shown) = (Instant::now(), String::new());
        let out = console_session(addr, "/tmp/x.sh", Duration::from_secs(6), Duration::from_secs(10), &mut |s| shown.push_str(s));
        assert_eq!(out.unwrap(), "pidof: 1234\nŁ\n");
        assert_eq!(shown, "pidof: 1234\nŁ\n");
        assert!(started.elapsed() < Duration::from_secs(1), "waited for silence instead of the marker");
        assert_eq!(server.recv().unwrap(), "exec mksh /tmp/x.sh\r\n"); // one line, CRLF
        // a greeting without a line end, and a console that never greets
        let (addr, server) = fake_console(&[b"CONNECTED!!"], b"ok\nLITHIFY_DONE\n");
        let out = console_session(addr, "/tmp/y.sh", Duration::from_secs(6), Duration::from_secs(10), &mut |_| {});
        assert_eq!(out.unwrap(), "ok\n");
        assert_eq!(server.recv().unwrap(), "exec mksh /tmp/y.sh\r\n");
        let (addr, _server) = fake_console(&[b"hello"], b"");
        let out = console_session(addr, "/tmp/z.sh", Duration::from_secs(6), Duration::from_millis(500), &mut |_| {});
        assert_eq!(out.map_err(|e| e.kind()), Err(io::ErrorKind::TimedOut));
    }

    #[test]
    fn console_scripts_always_print_the_marker() {
        assert_eq!(console_script("pidof x\n"), "(\npidof x\n:\n)\necho LITHIFY_DONE\n");
        assert_eq!(console_script("exit 1"), "(\nexit 1\n:\n)\necho LITHIFY_DONE\n");
        assert_eq!(console_script(""), "(\n:\n)\necho LITHIFY_DONE\n");
        assert_eq!(find_bytes(b"abcabd", b"abd"), Some(3));
        assert_eq!(find_bytes(b"ab", b"abc"), None);
    }

    #[test]
    fn a_stuck_call_is_not_joined_by_more() {
        let _one = serial();
        let key = "test stuck call";
        let calls = Arc::new(AtomicUsize::new(0));
        let (go_tx, go_rx) = mpsc::channel::<()>();
        let c = Arc::clone(&calls);
        let got = guarded(key, Duration::from_millis(50), move || {
            c.fetch_add(1, Ordering::SeqCst);
            let _ = go_rx.recv();
            Some("late".into())
        });
        assert!(!got.fresh && got.value.is_none()); // gave up waiting; nothing known before
        // while it hangs, no second call: the last result (none yet) at once
        let c = Arc::clone(&calls);
        let asked = Instant::now();
        let again = guarded(key, Duration::from_secs(5), move || {
            c.fetch_add(1, Ordering::SeqCst);
            Some("again".into())
        });
        assert!(!again.fresh && asked.elapsed() < Duration::from_secs(1));
        assert_eq!(calls.load(Ordering::SeqCst), 1);
        // once it returns, its result is the last one, and the next call runs
        go_tx.send(()).unwrap();
        let deadline = Instant::now() + Duration::from_secs(5);
        while last_result(key).is_none() {
            assert!(Instant::now() < deadline, "the call never landed");
            thread::sleep(Duration::from_millis(10));
        }
        assert_eq!(last_result(key).as_deref(), Some("late"));
        let now = guarded(key, Duration::from_secs(5), || Some("now".into()));
        assert_eq!((now.fresh, now.value.as_deref()), (true, Some("now")));
    }

    #[test]
    fn a_tool_that_takes_too_long_is_killed() {
        let _one = serial();
        let started = Instant::now();
        let got = output_guarded("/bin/sh", &["-c", "exec sleep 5"], Duration::from_millis(200));
        assert!(!got.fresh && got.value.is_none());
        // killed, reaped and given back long before its 5 s
        let key = "/bin/sh -c exec sleep 5";
        loop {
            if let Ok(f) = Flight::take_off(key) {
                drop(f);
                break;
            }
            assert!(started.elapsed() < Duration::from_secs(4), "the tool was not stopped");
            thread::sleep(Duration::from_millis(20));
        }
        assert_eq!(last_result(key), None); // what a killed tool printed is no answer
        let ok = output_guarded("/bin/sh", &["-c", "echo hi"], Duration::from_secs(5));
        assert_eq!((ok.fresh, ok.value.as_deref()), (true, Some("hi\n")));
        let missing = output_guarded("/no/such/tool", &[], Duration::from_secs(1));
        assert_eq!((missing.fresh, missing.value), (true, None));
    }

    #[test]
    fn helpers_are_few() {
        let _one = serial();
        let mut held = Vec::new();
        while let Some(s) = HelperSlot::take() {
            held.push(s);
            assert!(held.len() <= MAX_HELPERS);
        }
        for _ in 0..3 {
            assert!(HelperSlot::take().is_none()); // a refused one gives no place back
        }
        // none left: the call is not made at all
        assert!(!guarded("test no helper", Duration::from_secs(1), || Some("x".into())).fresh);
        drop(held);
        assert!(guarded("test no helper", Duration::from_secs(1), || Some("x".into())).fresh);
        let n = AtomicUsize::new(0);
        assert!(take_place(&n, 2) && take_place(&n, 2) && !take_place(&n, 2));
    }

    #[test]
    fn the_log_rotates_and_follows_renames() {
        let dir = env::temp_dir().join(format!("lithify-log-test-{}", process::id()));
        fs::create_dir_all(&dir).unwrap();
        let path = dir.join("agent.log");
        let p = path.to_str().unwrap();
        let mut file = None;
        append_log(&mut file, p, 10, b"0123456789AB\n");
        append_log(&mut file, p, 10, b"next\n"); // over 10 bytes: rotated first
        assert_eq!(fs::read_to_string(format!("{p}.1")).unwrap(), "0123456789AB\n");
        assert_eq!(fs::read_to_string(p).unwrap(), "next\n");
        // another process rotated it: the open file is not written to any more
        fs::rename(p, dir.join("moved")).unwrap();
        append_log(&mut file, p, 1000, b"after\n");
        assert_eq!(fs::read_to_string(p).unwrap(), "after\n");
        assert_eq!(fs::read_to_string(dir.join("moved")).unwrap(), "next\n");
        fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn signals_never_reach_init_or_a_group() {
        for pid in [-1, 0, 1] {
            assert_eq!(send_signal(pid, 0).map_err(|e| e.kind()), Err(io::ErrorKind::InvalidInput));
        }
        assert!(send_signal(process::id() as i32, 0).is_ok()); // signal 0 only asks whether it is there
    }

    /// A librespot line as the logcat reader gets it.
    fn librespot_line(rest: &str) -> String {
        format!("10-07 15:36:13.944 I/lithify-agent( 3061): [2026-10-07T13:36:13Z {rest}")
    }

    #[test]
    fn librespot_link_lines_are_told_apart() {
        let note = |rest: &str| link_note(&librespot_line(rest));
        assert_eq!(note("ERROR librespot_core::session] Connection to server closed."), Some(LinkNote::Lost));
        assert_eq!(note("ERROR librespot_connect::spirc] session lost connection to server"), Some(LinkNote::Lost));
        assert_eq!(note("WARN  librespot_core::dealer] Websocket peer does not respond."), Some(LinkNote::Lost));
        // a dealer that could not connect tries again by itself (and the replaced session's says so)
        assert_eq!(note("WARN  librespot_core::dealer] Websocket connection failed: IO error: peer closed connection without sending TLS close_notify"), None);
        assert_eq!(note("INFO  librespot_core::session] Authenticated as 'user' !"), Some(LinkNote::Back));
        assert_eq!(note("INFO  librespot_playback::player] Loading <Song> with Spotify URI <spotify:track:x>"), None);
        assert_eq!(note("INFO  librespot_core::session] Connection to server closing soon"), None);
        // the agent's own lines never count, whatever they say
        let own = "10-07 15:36:14.000 I/lithify-agent( 3061): 2026-10-07 13:36:14Z lithify-agent[99] \
                   Connection to server closed. Websocket connection failed, Authenticated as x";
        assert_eq!(link_note(own), None);
        // a loss counts from the first line, until librespot signs in again
        note_link(&librespot_line("ERROR librespot_core::session] Connection to server closed."));
        let first = link_lost().unwrap();
        note_link(&librespot_line("WARN  librespot_core::dealer] Websocket peer does not respond."));
        assert_eq!(*link_lost(), Some(first));
        note_link(&librespot_line("INFO  librespot_core::session] Authenticated as 'user' !"));
        assert!(link_lost().is_none());
        // right after a login, a loss is the replaced session's: not noted
        note_link(&librespot_line("WARN  librespot_core::dealer] Websocket peer does not respond."));
        assert!(link_lost().is_none());
    }

    #[test]
    fn a_loss_right_after_a_login_does_not_count() {
        let s = Duration::from_secs;
        assert!(loss_counts(None));
        assert!(!loss_counts(Some(s(0))));
        assert!(!loss_counts(Some(s(9))));
        assert!(loss_counts(Some(s(10))));
    }

    #[test]
    fn a_dead_session_is_restarted_after_a_grace_and_rarely() {
        let s = Duration::from_secs;
        let idle = || false;
        assert!(!link_restart_due(s(19), None, idle)); // it may still reconnect by itself
        assert!(link_restart_due(s(20), None, idle)); // the first one at once after the grace
        assert!(!link_restart_due(s(60), None, || true)); // playing: never
        assert!(!link_restart_due(s(60), Some(s(300)), idle)); // one per 10 minutes at most
        assert!(link_restart_due(s(60), Some(s(600)), idle));
        let mut looked = false;
        assert!(!link_restart_due(s(5), None, || {
            looked = true;
            false
        }));
        assert!(!looked); // librespot's files are looked at only when all else says yes
    }

    #[test]
    fn librespot_downloads_have_a_mount_of_their_own() {
        // /proc/mounts on the speaker, with the tmpfs the exec wrapper mounts
        let mounts = "rootfs / rootfs ro,relatime 0 0\n\
                      tmpfs /tmp tmpfs rw,relatime,size=32768k 0 0\n\
                      lithify-librespot /tmp/lithify-librespot tmpfs rw,relatime,size=131072k,mode=700,uid=1000,gid=1000 0 0\n";
        assert!(is_mount_point(mounts, settings::LIBRESPOT_TMP));
        assert!(is_mount_point(mounts, "/tmp"));
        assert!(!is_mount_point(mounts, "/tmp/lithify"));
        assert!(!is_mount_point("tmpfs /tmp tmpfs rw,relatime,size=32768k 0 0\n", settings::LIBRESPOT_TMP));
        assert_eq!(mount_size_kb(mounts, settings::LIBRESPOT_TMP), Some(131072)); // an older version's: resized
        assert_eq!(mount_size_kb(mounts, "/tmp"), Some(32768));
        assert_eq!(mount_size_kb(mounts, "/nowhere"), None);
        assert_eq!(LIBRESPOT_TMP_SIZE_KB, 196608); // what the kernel shows for size=192m
        assert!(!is_mount_point("", "/tmp"));
        let a = |v: &[&str]| v.iter().map(|s| s.to_string()).collect::<Vec<_>>();
        assert_eq!(tmp_dir_of(&a(&["--name", "x", "--tmp", "/a"])), Some("/a"));
        assert_eq!(tmp_dir_of(&a(&["--tmp=/b"])), Some("/b"));
        assert_eq!(tmp_dir_of(&a(&["-t", "/c"])), Some("/c"));
        assert_eq!(tmp_dir_of(&a(&["--cache", "/d", "--tmp"])), None);
    }

    #[test]
    fn a_download_dir_librespot_cannot_use_is_left_out() {
        let a = |v: &[&str]| v.iter().map(|s| s.to_string()).collect::<Vec<_>>();
        let mut args = a(&["--name", "x", "--tmp", "/a", "--cache", "/c", "--tmp=/b", "-t", "/d", "--bitrate", "320"]);
        drop_tmp_option(&mut args);
        assert_eq!(args, a(&["--name", "x", "--cache", "/c", "--bitrate", "320"]));
        let mut cut = a(&["--name", "x", "--tmp"]); // a value missing at the end
        drop_tmp_option(&mut cut);
        assert_eq!(cut, a(&["--name", "x"]));
        let dir = env::temp_dir().join(format!("lithify-writable-test-{}", process::id()));
        assert!(!writable(&dir)); // not there
        fs::create_dir_all(&dir).unwrap();
        assert!(writable(&dir));
        assert!(fs::read_dir(&dir).unwrap().next().is_none(), "the probe is not left behind");
        fs::remove_dir(&dir).unwrap();
    }

    #[test]
    fn leftover_downloads_are_wiped_but_not_their_dir() {
        let dir = env::temp_dir().join(format!("lithify-wipe-test-{}", process::id()));
        let outside = env::temp_dir().join(format!("lithify-wipe-outside-{}", process::id()));
        fs::create_dir_all(dir.join("sub/deeper")).unwrap();
        fs::create_dir_all(&outside).unwrap();
        fs::write(outside.join("keep"), b"k").unwrap();
        fs::write(dir.join(".tmpAbC123"), vec![0u8; 3000]).unwrap();
        fs::write(dir.join("sub/deeper/x"), b"x").unwrap();
        std::os::unix::fs::symlink(&outside, dir.join("link")).unwrap(); // removed, never followed
        assert_eq!(wipe_dir(&dir).unwrap(), (3, 3000));
        assert!(dir.is_dir() && fs::read_dir(&dir).unwrap().next().is_none());
        assert!(outside.join("keep").is_file());
        fs::remove_dir(&dir).unwrap();
        fs::remove_dir_all(&outside).unwrap();
        assert_eq!(wipe_dir(&dir).map_err(|e| e.kind()), Err(io::ErrorKind::NotFound));
    }

    /// xorshift64: the same "random" inputs on every run.
    struct Rng(u64);

    impl Rng {
        fn next(&mut self) -> u64 {
            let mut x = self.0;
            x ^= x << 13;
            x ^= x >> 7;
            x ^= x << 17;
            self.0 = x;
            x
        }

        fn below(&mut self, n: usize) -> usize {
            (self.next() % n.max(1) as u64) as usize
        }

        /// `seed` cut short with a few bytes changed, or random bytes.
        fn mutate(&mut self, seed: &[u8]) -> Vec<u8> {
            let mut v: Vec<u8> = if self.below(4) == 0 {
                (0..self.below(300)).map(|_| self.next() as u8).collect()
            } else {
                seed[..self.below(seed.len() + 1)].to_vec()
            };
            for _ in 0..self.below(4) {
                if !v.is_empty() {
                    let i = self.below(v.len());
                    v[i] = self.next() as u8;
                }
            }
            v
        }
    }

    #[test]
    fn parsers_survive_garbage() {
        let mut rng = Rng(0x2545_F491_4F6C_DD1D);
        let seeds: [&[u8]; 6] = [
            b"1383 (spot (hi) x) S 1 1383 1383 0 -1 4194560 2711 0 0 0 1200 345 0 0 20 0 41 0 5321",
            &[0x00, 0x00, 0x01, 0x00, 0x32, 0x01, 0xc3, 0x5e, 0x00, 0x01, 0x34, 0x00, 0x00, 0x02],
            b"Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\n*\t0000E8C7\t00000000\t0201\t0\t0\t0\t0000FFFF",
            b"10-06 19:11:45.697 E/SPOTIFY ( 1383): dequeue  no data condition",
            b"lithify-librespot /tmp/lithify-librespot tmpfs rw 0 0\n",
            "199.232.1.2/16 Łazienka LITHIFY_DONE".as_bytes(),
        ];
        for round in 0..20_000 {
            let input = rng.mutate(seeds[round % seeds.len()]);
            let text = String::from_utf8_lossy(&input);
            let _ = parse_stat(&text);
            let _ = luci_parse(&mut input.clone());
            let _ = parse_reject_routes(&text);
            let _ = own_networks(&text);
            let _ = logcat_tag(&text);
            let _ = canonical_route(&text);
            let _ = in_route(Ipv4Addr::LOCALHOST, &text);
            let _ = is_mount_point(&text, "/tmp/lithify-librespot");
            let _ = utf8_whole(&input, rng.below(input.len() + 4));
            let mut r = ConsoleReply::new(CONSOLE_DONE);
            for part in input.chunks(rng.below(16) + 1) {
                r.push(part, &mut |_| {});
            }
            let _ = r.finish(&mut |_| {});
        }
    }
}
