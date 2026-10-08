//! The speaker's web page and its JSON API: http://<speaker>:<ui_port>/
//!
//! Plain HTTP on the LAN with no dependencies. GET requests only read. Every POST needs the
//! header `X-Lithify: 1`: a page from another site cannot add it without a CORS preflight, which
//! this server never grants (CSRF). Actions that change something also need `X-Lithify-Pin` when
//! `ui_pin` is set. The Host header must be an IP address or a local name (DNS rebinding).
//! Updates come only from the companion URL in settings/install.conf, never from a URL a request names.

use std::collections::VecDeque;
use std::fs;
use std::io::{self, Read, Write};
use std::net::{IpAddr, Ipv4Addr, Ipv6Addr, Shutdown, SocketAddr, TcpListener, TcpStream, ToSocketAddrs, UdpSocket};
use std::process;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Condvar, Mutex, MutexGuard, Once, OnceLock};
use std::thread;
use std::time::{Duration, Instant};

use crate::settings;
use crate::{
    base_dir, BUILD, event, find_bytes as find, find_pid, holds_pcm, http_date, in_route, librespot_args, log,
    log_flush, mixer_of, official_spotify, opt_value, output_within, pcm_status_seen, proc_uptime_s, read_volume,
    read_within, restart_librespot_now, root_run_with, shared, spawn_named, system_uptime, take_place, trial_start,
    unreachable_routes, utc_now, wifi_seen, write_state, Config, INSTALLED_FILE, LIBRESPOT_STATE_FILE, LOG_PATH,
    SPOTIFY_RESTART,
};

const INDEX_HTML: &str = include_str!("../ui/index.html");
const APP_JS: &str = include_str!("../ui/app.js");
// Runs before the page is drawn: the theme chosen on the page (CSP allows no inline script).
const BOOT_JS: &str = include_str!("../ui/boot.js");
// The page's look (CSP allows no inline style either).
const STYLE_CSS: &str = include_str!("../ui/style.css");
// The logo and the icons: browser tabs, bookmarks, phone home screens (the manifest).
const LOGO_SVG: &str = include_str!("../ui/logo.svg");
const ICON_SVG: &str = include_str!("../ui/icon.svg");
const FAVICON_ICO: &[u8] = include_bytes!("../ui/favicon.ico");
const APPLE_TOUCH_ICON: &[u8] = include_bytes!("../ui/apple-touch-icon.png");
const ICON_192: &[u8] = include_bytes!("../ui/icon-192.png");
const ICON_512: &[u8] = include_bytes!("../ui/icon-512.png");
const MANIFEST: &str = include_str!("../ui/manifest.webmanifest");
const INSTALL_SH: &str = include_str!("../../device/ls9/install.sh");
const ROLLBACK_SH: &str = include_str!("../../device/ls9/rollback.sh");

/// A script for the speaker's shell with LF line endings, also when it was built from a checkout
/// that has CRLF ones (Windows): mksh would read each CR as part of a command.
fn lf(script: &str) -> String {
    script.replace("\r\n", "\n")
}

/// The page's port when the configured one cannot be used (and the default setting).
const FALLBACK_PORT: u16 = 8090;
const MAX_CONNECTIONS: usize = 24;
/// A browser keeps up to six connections to one site, some of them idle spares.
const MAX_PER_PEER: usize = 8;
/// Time a client gets to take the whole reply.
const REPLY_DEADLINE: Duration = Duration::from_secs(30);
/// Time a client gets to send its whole request (one slow client must not hold a slot).
const REQUEST_DEADLINE: Duration = Duration::from_secs(5);
const MAX_HEAD: usize = 8 * 1024;
/// The settings form at its largest: 32 host names of 253 characters and 32 librespot arguments,
/// all URL-encoded, and the rest.
const MAX_BODY: usize = 32 * 1024;
const JOB_FILE: &str = "/tmp/lithify-last-job.json";
const NO_COMPANION: &str = "no companion configured: run `lithify serve --install-service` on your computer, then `lithify install` once";

/// Hosts the network test connects to (host, port, what it is).
const NET_TARGETS: &[(&str, u16, &str)] = &[
    ("apresolve.spotify.com", 443, "access point list"),
    ("ap.spotify.com", 4070, "access point (port 4070)"),
    ("ap.spotify.com", 443, "access point (port 443)"),
    ("spclient.wg.spotify.com", 443, "Spotify API"),
    ("dealer.spotify.com", 443, "Spotify Connect channel"),
    ("audio-ak.spotifycdn.com", 443, "audio CDN (Akamai)"),
    ("audio-fa.scdn.co", 443, "audio CDN (Fastly)"),
    ("i.scdn.co", 443, "cover art"),
];

static ACTIVE: AtomicUsize = AtomicUsize::new(0);
static PEERS: Mutex<Vec<(IpAddr, usize)>> = Mutex::new(Vec::new());
static STARTED: OnceLock<Instant> = OnceLock::new();

/// Count a connection from `ip`; false when that address already has too many open.
fn peer_enter(ip: IpAddr) -> bool {
    let mut peers = PEERS.lock().unwrap_or_else(|e| e.into_inner());
    match peers.iter_mut().find(|(a, _)| *a == ip) {
        Some((_, n)) if *n >= MAX_PER_PEER => false,
        Some((_, n)) => {
            *n += 1;
            true
        }
        None => {
            peers.push((ip, 1));
            true
        }
    }
}

fn peer_leave(ip: IpAddr) {
    let mut peers = PEERS.lock().unwrap_or_else(|e| e.into_inner());
    if let Some(i) = peers.iter().position(|(a, _)| *a == ip) {
        peers[i].1 -= 1;
        if peers[i].1 == 0 {
            peers.swap_remove(i);
        }
    }
}

/// Listen on the configured port; when that stays taken (another service, or a port set on this
/// page that cannot work), fall back to the default one, so the page that can fix it stays up.
fn listen(port: u16) -> TcpListener {
    let mut port = port;
    let mut failures = 0u32;
    loop {
        match TcpListener::bind(("0.0.0.0", port)) {
            Ok(l) => {
                log(&format!("web page on port {port}"));
                return l;
            }
            Err(e) => {
                failures += 1;
                if port != FALLBACK_PORT && failures >= 3 {
                    event(&format!("web page: port {port} is not available ({e}): using {FALLBACK_PORT} instead"));
                    port = FALLBACK_PORT;
                    failures = 0;
                    continue;
                }
                let wait = if failures < 3 { 2 } else { 30 };
                log(&format!("web page: cannot listen on port {port}: {e}; retrying in {wait} s"));
                thread::sleep(Duration::from_secs(wait));
            }
        }
    }
}

pub fn serve(cfg: Config) {
    STARTED.get_or_init(Instant::now);
    // getprop may be slow: asked once, in the background, never by a request.
    spawn_named("model", || {
        let _ = MODEL.set(read_device_model());
    });
    let cfg = Arc::new(cfg);
    let listener = listen(cfg.ui_port);
    for conn in listener.incoming() {
        let stream = match conn {
            Ok(s) => s,
            Err(_) => {
                // Out of file descriptors or memory: accept fails again at once, so pause.
                thread::sleep(Duration::from_millis(200));
                continue;
            }
        };
        let Ok(peer) = stream.peer_addr().map(|a| a.ip()) else { continue };
        // Dropping the stream closes it: too many connections in all, or from one address.
        if ACTIVE.fetch_add(1, Ordering::SeqCst) >= MAX_CONNECTIONS {
            ACTIVE.fetch_sub(1, Ordering::SeqCst);
            continue;
        }
        if !peer_enter(peer) {
            ACTIVE.fetch_sub(1, Ordering::SeqCst);
            continue;
        }
        let cfg = Arc::clone(&cfg);
        let spawned = thread::Builder::new().name("web".into()).stack_size(256 * 1024).spawn(move || {
            handle(stream, &cfg, peer);
            peer_leave(peer);
            ACTIVE.fetch_sub(1, Ordering::SeqCst);
        });
        if spawned.is_err() {
            peer_leave(peer);
            ACTIVE.fetch_sub(1, Ordering::SeqCst);
        }
    }
}

// ── HTTP ─────────────────────────────────────────────────────────────────

struct Request {
    peer: IpAddr,
    method: String,
    path: String,
    query: Vec<(String, String)>,
    headers: Vec<(String, String)>,
    body: Vec<u8>,
}

impl Request {
    fn header(&self, name: &str) -> &str {
        self.headers.iter().find(|(k, _)| k == name).map(|(_, v)| v.as_str()).unwrap_or("")
    }

    fn param(&self, name: &str) -> Option<&str> {
        self.query.iter().find(|(k, _)| k == name).map(|(_, v)| v.as_str())
    }
}

/// A reply's body: made for this request, one of the files built into the binary (never
/// copied), or one a cache keeps for several requests.
enum Body {
    Owned(Vec<u8>),
    Static(&'static [u8]),
    Shared(Arc<[u8]>),
}

impl Body {
    fn bytes(&self) -> &[u8] {
        match self {
            Body::Owned(v) => v,
            Body::Static(b) => b,
            Body::Shared(b) => b,
        }
    }
}

struct Response {
    code: u16,
    ctype: &'static str,
    body: Body,
    csp: bool,
    /// Cache-Control; the API's answers change all the time: no-store.
    cache: &'static str,
    etag: Option<&'static str>,
}

impl Response {
    fn new(code: u16, ctype: &'static str, body: impl Into<Vec<u8>>) -> Response {
        Response { code, ctype, body: Body::Owned(body.into()), csp: false, cache: "no-store", etag: None }
    }

    fn embedded(ctype: &'static str, body: &'static [u8], cache: &'static str) -> Response {
        Response { code: 200, ctype, body: Body::Static(body), csp: false, cache, etag: None }
    }

    fn json(code: u16, body: String) -> Response {
        Response::new(code, "application/json; charset=utf-8", body)
    }

    fn text(code: u16, body: &str) -> Response {
        Response::new(code, "text/plain; charset=utf-8", body)
    }

    fn error(code: u16, msg: &str) -> Response {
        Response::json(code, Obj::new().str("error", msg).end())
    }

    fn done(msg: &str) -> Response {
        Response::json(200, Obj::new().bool("ok", true).str("message", msg).end())
    }
}

/// A request that gets no route: one that sent nothing (closed quietly), or an error reply,
/// with how much of an announced body is still unread.
enum Rejected {
    Idle,
    Reply(u16, String, usize),
}

fn bad(code: u16, msg: &str) -> Rejected {
    Rejected::Reply(code, msg.to_string(), 0)
}

fn handle(mut s: TcpStream, cfg: &Config, peer: IpAddr) {
    let (resp, head_only, unread) = match read_request(&mut s, peer) {
        Ok(req) => (route(&req, cfg), req.method == "HEAD", 0),
        // A connection that sent nothing: browsers open spare ones ahead of time. It is closed
        // without a reply, so a browser that uses it late simply retries on a new one.
        Err(Rejected::Idle) => return,
        Err(Rejected::Reply(code, msg, unread)) => (Response::error(code, &msg), false, unread),
    };
    let _ = write_response(&mut s, &resp, head_only);
    let _ = s.shutdown(Shutdown::Write);
    if unread > 0 {
        discard(&mut s, unread);
    }
}

/// Read and drop (a bounded part of) a body that was refused: closing with unread data makes
/// the kernel reset the connection, and the client may lose the reply that says why.
fn discard(s: &mut TcpStream, unread: usize) {
    let deadline = Instant::now() + Duration::from_secs(2);
    let mut left = unread.min(256 * 1024);
    let mut chunk = [0u8; 4096];
    while left > 0 {
        match read_by(s, &mut chunk, deadline) {
            Ok(0) | Err(_) => break,
            Ok(n) => left = left.saturating_sub(n),
        }
    }
}

/// One read that must finish before `deadline`.
fn read_by(s: &mut TcpStream, buf: &mut [u8], deadline: Instant) -> io::Result<usize> {
    let left = deadline.saturating_duration_since(Instant::now());
    if left.is_zero() {
        return Err(io::Error::new(io::ErrorKind::TimedOut, "request took too long"));
    }
    s.set_read_timeout(Some(left))?;
    s.read(buf)
}

fn read_request(s: &mut TcpStream, peer: IpAddr) -> Result<Request, Rejected> {
    let deadline = Instant::now() + REQUEST_DEADLINE;
    let mut buf = Vec::with_capacity(1024);
    let mut chunk = [0u8; 2048];
    let head_end = loop {
        if let Some(i) = find(&buf, b"\r\n\r\n") {
            break i;
        }
        if buf.len() > MAX_HEAD {
            // The rest of the headers may still be on their way: read a little of it after the reply.
            return Err(Rejected::Reply(431, "request headers too large".into(), MAX_HEAD));
        }
        let n = match read_by(s, &mut chunk, deadline) {
            Ok(n) => n,
            Err(_) if buf.is_empty() => return Err(Rejected::Idle),
            Err(_) => return Err(bad(408, "the request took too long")),
        };
        if n == 0 {
            return Err(if buf.is_empty() { Rejected::Idle } else { bad(400, "connection closed") });
        }
        buf.extend_from_slice(&chunk[..n]);
    };
    if head_end > MAX_HEAD {
        return Err(bad(431, "request headers too large"));
    }
    let head = std::str::from_utf8(&buf[..head_end]).map_err(|_| bad(400, "headers are not UTF-8"))?;
    let mut lines = head.split("\r\n");
    let mut first = lines.next().unwrap_or("").split(' ');
    let (Some(method), Some(target), Some(version)) = (first.next(), first.next(), first.next()) else {
        return Err(bad(400, "malformed request line"));
    };
    if !version.starts_with("HTTP/1.") {
        return Err(bad(400, "HTTP/1.x only"));
    }
    let headers: Vec<(String, String)> = lines
        .filter_map(|l| l.split_once(':'))
        .map(|(k, v)| (k.trim().to_ascii_lowercase(), v.trim().to_string()))
        .collect();
    if headers.iter().any(|(k, _)| k == "transfer-encoding") {
        return Err(bad(411, "request bodies must have a Content-Length"));
    }
    let len: usize = match headers.iter().find(|(k, _)| k == "content-length") {
        Some((_, v)) => v.parse().map_err(|_| bad(400, "malformed Content-Length"))?,
        None => 0,
    };
    let mut body = buf[head_end + 4..].to_vec();
    if len > MAX_BODY {
        let msg = format!("request body too large (at most {} KB)", MAX_BODY / 1024);
        return Err(Rejected::Reply(413, msg, len.saturating_sub(body.len())));
    }
    while body.len() < len {
        let n = read_by(s, &mut chunk, deadline).map_err(|_| bad(408, "the request took too long"))?;
        if n == 0 {
            break;
        }
        body.extend_from_slice(&chunk[..n]);
    }
    body.truncate(len);
    let (path, query) = target.split_once('?').unwrap_or((target, ""));
    Ok(Request { peer, method: method.to_string(), path: path.to_string(), query: query_pairs(query), headers, body })
}

fn query_pairs(query: &str) -> Vec<(String, String)> {
    query.split('&').filter_map(|kv| kv.split_once('=')).map(|(k, v)| (k.to_string(), v.to_string())).collect()
}

fn reason(code: u16) -> &'static str {
    match code {
        200 => "OK",
        202 => "Accepted",
        204 => "No Content",
        304 => "Not Modified",
        400 => "Bad Request",
        401 => "Unauthorized",
        403 => "Forbidden",
        404 => "Not Found",
        405 => "Method Not Allowed",
        408 => "Request Timeout",
        409 => "Conflict",
        411 => "Length Required",
        413 => "Content Too Large",
        421 => "Misdirected Request",
        429 => "Too Many Requests",
        431 => "Request Header Fields Too Large",
        501 => "Not Implemented",
        502 => "Bad Gateway",
        503 => "Service Unavailable",
        _ => "Internal Server Error",
    }
}

/// The page's policy: its own files only, no inline script or style, and no DOM sink that
/// takes a string as HTML or script (Trusted Types with no policy at all).
const CSP: &str = "Content-Security-Policy: default-src 'none'; script-src 'self'; style-src 'self'; \
                   connect-src 'self'; img-src 'self'; manifest-src 'self'; base-uri 'none'; form-action 'none'; \
                   frame-ancestors 'none'; require-trusted-types-for 'script'; trusted-types 'none'\r\n";

fn response_head(r: &Response) -> String {
    let mut head = format!("HTTP/1.1 {} {}\r\nDate: {}\r\n", r.code, reason(r.code), http_date());
    // A 304 has no body, and its headers must not describe one.
    if r.code != 304 {
        head.push_str(&format!("Content-Type: {}\r\nContent-Length: {}\r\n", r.ctype, r.body.bytes().len()));
    }
    head.push_str(&format!("Connection: close\r\nCache-Control: {}\r\n", r.cache));
    if let Some(tag) = r.etag {
        head.push_str(&format!("ETag: \"{tag}\"\r\n"));
    }
    head.push_str("X-Content-Type-Options: nosniff\r\nReferrer-Policy: no-referrer\r\nX-Frame-Options: DENY\r\n");
    if r.csp {
        head.push_str(CSP);
    }
    head.push_str("\r\n");
    head
}

fn write_response(s: &mut TcpStream, r: &Response, head_only: bool) -> io::Result<()> {
    let head = response_head(r);
    // One deadline for the whole reply: a client that reads a byte at a time must not keep the
    // connection (and its slot) for long.
    let deadline = Instant::now() + REPLY_DEADLINE;
    let body: &[u8] = if head_only || r.code == 304 { &[] } else { r.body.bytes() };
    for part in [head.as_bytes(), body] {
        for chunk in part.chunks(16 * 1024) {
            let left = deadline.saturating_duration_since(Instant::now());
            if left.is_zero() {
                return Err(io::Error::new(io::ErrorKind::TimedOut, "reply took too long"));
            }
            s.set_write_timeout(Some(left))?;
            s.write_all(chunk)?;
        }
    }
    s.flush()
}

// ── routing and guards ───────────────────────────────────────────────────

const JS: &str = "text/javascript; charset=utf-8";
/// Kept by the browser for good: asked for under the current version of the page.
const IMMUTABLE: &str = "public, max-age=31536000, immutable";
/// Icons and the manifest change rarely: a day.
const A_DAY: &str = "public, max-age=86400";

/// FNV-1a (64 bit): enough to tell versions of the page's files apart, and std has no stable hash.
fn fnv1a64(parts: &[&[u8]]) -> u64 {
    const PRIME: u64 = 0x0000_0100_0000_01b3;
    let mut h: u64 = 0xcbf2_9ce4_8422_2325;
    for p in parts {
        for b in *p {
            h = (h ^ u64::from(*b)).wrapping_mul(PRIME);
        }
        h = (h ^ 0xff).wrapping_mul(PRIME); // where one file ends
    }
    h
}

/// The version of the page: a hash of its files, made once.
fn ui_version() -> &'static str {
    static V: OnceLock<String> = OnceLock::new();
    V.get_or_init(|| {
        let files = [INDEX_HTML.as_bytes(), APP_JS.as_bytes(), BOOT_JS.as_bytes(), STYLE_CSS.as_bytes()];
        format!("{:016x}", fnv1a64(&files))
    })
}

/// index.html with its scripts and style sheet named by version, so a browser keeps those
/// until the page changes (made once).
fn index_html() -> &'static str {
    static PAGE: OnceLock<String> = OnceLock::new();
    PAGE.get_or_init(|| {
        let v = ui_version();
        let mut page = INDEX_HTML.to_string();
        for file in ["/app.js", "/boot.js", "/style.css"] {
            for quote in ['"', '\''] {
                page = page.replace(&format!("{quote}{file}{quote}"), &format!("{quote}{file}?v={v}{quote}"));
            }
        }
        page
    })
}

/// Does an If-None-Match header name `tag`?
fn etag_matches(header: &str, tag: &str) -> bool {
    header.split(',').map(str::trim).any(|t| t == "*" || t.trim_start_matches("W/").trim_matches('"') == tag)
}

/// One of the page's own files. Asked for under the current version, the browser may keep it for
/// good; otherwise (the page itself, an old or no version) it asks again each time and gets a
/// 304 while nothing changed.
fn page_file(req: &Request, ctype: &'static str, body: &'static [u8], versioned: bool) -> Response {
    let v = ui_version();
    if versioned && req.param("v") == Some(v) {
        return Response::embedded(ctype, body, IMMUTABLE);
    }
    let mut r = Response::embedded(ctype, body, "no-cache");
    r.etag = Some(v);
    if etag_matches(req.header("if-none-match"), v) {
        r.code = 304;
        r.body = Body::Static(&[]);
    }
    r
}

fn route(req: &Request, cfg: &Config) -> Response {
    let host = req.header("host");
    if !host_ok(host) {
        return Response::text(421, "Open this page by the speaker's IP address (or its .local / .lan name).\n");
    }
    let get = req.method == "GET" || req.method == "HEAD";
    match req.path.as_str() {
        "/" | "/index.html" if get => {
            Response { csp: true, ..page_file(req, "text/html; charset=utf-8", index_html().as_bytes(), false) }
        }
        "/app.js" if get => page_file(req, JS, APP_JS.as_bytes(), true),
        "/boot.js" if get => page_file(req, JS, BOOT_JS.as_bytes(), true),
        "/style.css" if get => page_file(req, "text/css; charset=utf-8", STYLE_CSS.as_bytes(), true),
        "/favicon.ico" if get => Response::embedded("image/x-icon", FAVICON_ICO, A_DAY),
        "/logo.svg" if get => Response::embedded("image/svg+xml", LOGO_SVG.as_bytes(), A_DAY),
        "/icon.svg" if get => Response::embedded("image/svg+xml", ICON_SVG.as_bytes(), A_DAY),
        "/apple-touch-icon.png" if get => Response::embedded("image/png", APPLE_TOUCH_ICON, A_DAY),
        "/icon-192.png" if get => Response::embedded("image/png", ICON_192, A_DAY),
        "/icon-512.png" if get => Response::embedded("image/png", ICON_512, A_DAY),
        "/manifest.webmanifest" if get => Response::embedded("application/manifest+json", MANIFEST.as_bytes(), A_DAY),
        "/api/status" if get => status_reply(cfg),
        "/api/logs" if get => {
            // A few kinds only: asking for others must not push the other kept answers out.
            let key = format!("logs {} {}", log_source(req), log_lines_asked(req));
            heavy(&key, None, Duration::from_secs(10), || keep_for(Duration::from_secs(5), logs(req)))
        }
        "/api/job" if get => Response::json(200, job_json()),
        "/api/settings" if get => Response::json(200, settings_json()),
        "/api/update/build-status" if get => heavy("build-status", None, Duration::from_secs(20), || {
            keep_for(Duration::from_secs(5), companion(cfg, "GET", "/api/build/status", Duration::from_secs(15)))
        }),
        path if req.method == "POST" => {
            let r = post(req, cfg, host, path);
            status_changed(); // what the page shows next reflects what it just did
            r
        }
        _ if get => Response::error(404, "not found"),
        _ => Response::error(405, "method not allowed"),
    }
}

fn post(req: &Request, cfg: &Config, host: &str, path: &str) -> Response {
    if req.header("x-lithify") != "1" {
        return Response::error(403, "missing X-Lithify header");
    }
    let origin = req.header("origin");
    if !origin.is_empty() && origin.strip_prefix("http://") != Some(host) {
        return Response::error(403, "cross-site request refused");
    }
    // Checks that only read need no PIN. The network test starts a thread per host: one run at a
    // time, its result kept for 10 s.
    match path {
        "/api/test/network" => {
            return heavy("network-test", None, Duration::from_secs(45), || {
                keep_for(Duration::from_secs(10), Response::json(200, test_network(cfg)))
            })
        }
        "/api/test/librespot" => return Response::json(200, test_librespot()),
        _ => {}
    }
    if let Err(r) = check_pin(cfg, req) {
        return r;
    }
    match path {
        "/api/pin" => Response::done("PIN accepted"),
        "/api/restart/librespot" => restart_librespot(),
        "/api/restart/official" => restart_official(),
        "/api/restart/agent" => {
            if let Some(r) = busy() {
                return r;
            }
            exit_soon(Duration::from_millis(800), "restart requested from the web page");
            Response::done("the agent restarts; the page is back in a few seconds")
        }
        // The check can take minutes: one at a time, its result kept for a minute; a fresh one
        // (the page's button) skips that, but runs at most every 20 s.
        "/api/update/check" => {
            let fresh = req.param("fresh") == Some("1");
            heavy(UPDATE_CHECK, fresh.then_some(Duration::from_secs(20)), Duration::from_secs(330), || update_check(cfg, fresh))
        }
        "/api/update/build" => companion(cfg, "POST", "/api/build", Duration::from_secs(20)),
        "/api/settings" => save_settings(req),
        "/api/update/all" => start_update_all(cfg),
        "/api/update/install" => start_install(cfg),
        "/api/update/rollback" => start_rollback(),
        _ => Response::error(404, "not found"),
    }
}

/// DNS-rebinding guard: accept IP literals and local names only.
fn host_ok(host: &str) -> bool {
    let h = host.trim();
    if let Some(rest) = h.strip_prefix('[') {
        return rest.split_once(']').is_some_and(|(ip, _)| ip.parse::<Ipv6Addr>().is_ok());
    }
    let name = match h.rsplit_once(':') {
        Some((n, port)) if port.chars().all(|c| c.is_ascii_digit()) => n,
        _ => h,
    };
    if name.parse::<Ipv4Addr>().is_ok() {
        return true;
    }
    let name = name.trim_end_matches('.').to_ascii_lowercase();
    if name.is_empty() || !name.chars().all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '.') {
        return false;
    }
    !name.contains('.')
        || [".local", ".lan", ".home", ".home.arpa", ".internal", ".localdomain", ".localhost"]
            .iter()
            .any(|suffix| name.ends_with(suffix))
}

/// Wrong PINs per client address: five misses lock that address out for a minute, twice as long
/// after each further five (at most an hour). Other addresses are not affected.
struct PinMisses {
    ip: IpAddr,
    misses: u32,
    strikes: u32,
    until: Option<Instant>,
}

static PIN_MISSES: Mutex<Vec<PinMisses>> = Mutex::new(Vec::new());

fn lockout(strikes: u32) -> Duration {
    Duration::from_secs(60 * (1u64 << strikes.saturating_sub(1).min(6)).min(60))
}

fn check_pin(cfg: &Config, req: &Request) -> Result<(), Response> {
    if cfg.ui_pin.is_empty() {
        return Ok(());
    }
    let now = Instant::now();
    let mut all = PIN_MISSES.lock().unwrap_or_else(|e| e.into_inner());
    let i = match all.iter().position(|m| m.ip == req.peer) {
        Some(i) => i,
        None => {
            if all.len() >= 64 {
                all.remove(0);
            }
            all.push(PinMisses { ip: req.peer, misses: 0, strikes: 0, until: None });
            all.len() - 1
        }
    };
    if let Some(until) = all[i].until {
        if now < until {
            return Err(Response::error(429, "too many wrong PINs from this device: wait a while"));
        }
        all[i].until = None;
    }
    let given = req.header("x-lithify-pin");
    if given.is_empty() {
        return Err(Response::error(401, "PIN required"));
    }
    if same(given.as_bytes(), cfg.ui_pin.as_bytes()) {
        all.remove(i);
        return Ok(());
    }
    let m = &mut all[i];
    m.misses += 1;
    if m.misses >= 5 {
        m.misses = 0;
        m.strikes += 1;
        m.until = Some(now + lockout(m.strikes));
    }
    Err(Response::error(401, "wrong PIN"))
}

/// Comparison whose time does not depend on where the inputs differ.
fn same(a: &[u8], b: &[u8]) -> bool {
    a.len() == b.len() && a.iter().zip(b).fold(0u8, |acc, (x, y)| acc | (x ^ y)) == 0
}

// ── JSON ─────────────────────────────────────────────────────────────────

fn q(s: &str) -> String {
    let mut o = String::with_capacity(s.len() + 2);
    o.push('"');
    for c in s.chars() {
        match c {
            '"' => o.push_str("\\\""),
            '\\' => o.push_str("\\\\"),
            '\n' => o.push_str("\\n"),
            '\r' => o.push_str("\\r"),
            '\t' => o.push_str("\\t"),
            '<' => o.push_str("\\u003c"),
            c if (c as u32) < 0x20 || c == '\u{2028}' || c == '\u{2029}' => o.push_str(&format!("\\u{:04x}", c as u32)),
            c => o.push(c),
        }
    }
    o.push('"');
    o
}

/// Minimal JSON object writer (the agent has no dependencies).
struct Obj(Vec<String>);

impl Obj {
    fn new() -> Obj {
        Obj(Vec::new())
    }

    fn raw(mut self, k: &str, v: impl AsRef<str>) -> Obj {
        self.0.push(format!("{}:{}", q(k), v.as_ref()));
        self
    }

    fn str(self, k: &str, v: &str) -> Obj {
        let v = q(v);
        self.raw(k, v)
    }

    fn opt(self, k: &str, v: Option<&str>) -> Obj {
        match v {
            Some(v) => self.str(k, v),
            None => self.raw(k, "null"),
        }
    }

    fn num(self, k: &str, v: impl std::fmt::Display) -> Obj {
        self.raw(k, v.to_string())
    }

    fn opt_num(self, k: &str, v: Option<impl std::fmt::Display>) -> Obj {
        match v {
            Some(v) => self.num(k, v),
            None => self.raw(k, "null"),
        }
    }

    fn bool(self, k: &str, v: bool) -> Obj {
        self.raw(k, if v { "true" } else { "false" })
    }

    fn end(self) -> String {
        format!("{{{}}}", self.0.join(","))
    }
}

fn arr(items: impl IntoIterator<Item = String>) -> String {
    format!("[{}]", items.into_iter().collect::<Vec<_>>().join(","))
}

fn kv_json(pairs: &[(String, String)]) -> String {
    pairs.iter().fold(Obj::new(), |o, (k, v)| o.str(k, v)).end()
}

/// Value of a string, number or boolean field in a JSON text (first match of the key). Enough
/// for the flat replies of the zeroconf and Cast endpoints and the companion.
fn json_field(text: &str, key: &str) -> Option<String> {
    let pat = format!("\"{key}\"");
    let mut from = 0;
    while let Some(i) = text[from..].find(&pat) {
        let after = from + i + pat.len();
        from = after;
        let Some(rest) = text[after..].trim_start().strip_prefix(':') else { continue };
        let rest = rest.trim_start();
        let Some(body) = rest.strip_prefix('"') else {
            let end = rest.find([',', '}', ']', '\n']).unwrap_or(rest.len());
            return Some(rest[..end].trim().to_string());
        };
        let mut out = String::new();
        let mut chars = body.chars();
        while let Some(c) = chars.next() {
            match c {
                '"' => return Some(out),
                '\\' => match chars.next()? {
                    'n' => out.push('\n'),
                    't' => out.push('\t'),
                    'r' => out.push('\r'),
                    'u' => {
                        let hex: String = chars.by_ref().take(4).collect();
                        if let Some(ch) = u32::from_str_radix(&hex, 16).ok().and_then(char::from_u32) {
                            out.push(ch);
                        }
                    }
                    other => out.push(other),
                },
                c => out.push(c),
            }
        }
        return None;
    }
    None
}

fn read_kv(path: &std::path::Path) -> Vec<(String, String)> {
    fs::read_to_string(path)
        .unwrap_or_default()
        .lines()
        .filter_map(|l| l.split_once('='))
        .map(|(k, v)| (k.trim().to_string(), v.trim().to_string()))
        .collect()
}

/// The last `max` bytes of `s`, cut at a character boundary.
fn tail_chars(s: &str, max: usize) -> &str {
    if s.len() <= max {
        return s;
    }
    let mut start = s.len() - max;
    while !s.is_char_boundary(start) {
        start += 1;
    }
    &s[start..]
}

// ── small helpers ────────────────────────────────────────────────────────

fn invalid(msg: &str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, msg.to_string())
}

/// Minimal HTTP/1.1 client (one request per connection) for the companion (LAN) and the speaker's
/// own services (loopback). The Cast server ignores HTTP/1.0 requests. `timeout` is for the whole
/// exchange: a server that trickles its reply does not get longer.
pub(crate) fn http_call(method: &str, url: &str, timeout: Duration) -> io::Result<(u16, String)> {
    let deadline = Instant::now() + timeout;
    let left = || {
        let l = deadline.saturating_duration_since(Instant::now());
        if l.is_zero() { Err(io::Error::new(io::ErrorKind::TimedOut, "no complete reply in time")) } else { Ok(l) }
    };
    let rest = url.strip_prefix("http://").ok_or_else(|| invalid("only http:// URLs"))?;
    let (hostport, path) = match rest.find('/') {
        Some(i) => (&rest[..i], &rest[i..]),
        None => (rest, "/"),
    };
    let target = if hostport.contains(':') { hostport.to_string() } else { format!("{hostport}:80") };
    let addr = target.to_socket_addrs()?.next().ok_or_else(|| invalid("no address"))?;
    let mut s = TcpStream::connect_timeout(&addr, left()?.min(Duration::from_secs(3)))?;
    s.set_write_timeout(Some(left()?.min(Duration::from_secs(5))))?;
    write!(
        s,
        "{method} {path} HTTP/1.1\r\nHost: {hostport}\r\nUser-Agent: lithify-agent/{}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n",
        env!("CARGO_PKG_VERSION")
    )?;
    let mut raw = Vec::new();
    let mut buf = [0u8; 8192];
    loop {
        let wait = match left() {
            Ok(w) => w,
            Err(_) if complete(&raw) => break,
            Err(e) => return Err(e),
        };
        s.set_read_timeout(Some(wait))?;
        match s.read(&mut buf) {
            Ok(0) => break,
            Ok(n) => {
                raw.extend_from_slice(&buf[..n]);
                if raw.len() > 4 * 1024 * 1024 {
                    return Err(invalid("reply too large"));
                }
                if complete(&raw) {
                    break;
                }
            }
            Err(e) if e.kind() == io::ErrorKind::Interrupted => {}
            Err(e) if matches!(e.kind(), io::ErrorKind::WouldBlock | io::ErrorKind::TimedOut) && complete(&raw) => break,
            Err(e) => return Err(e),
        }
    }
    let end = find(&raw, b"\r\n\r\n").ok_or_else(|| invalid("malformed reply"))?;
    let head = String::from_utf8_lossy(&raw[..end]).to_ascii_lowercase();
    let code = head
        .split_whitespace()
        .nth(1)
        .and_then(|c| c.parse().ok())
        .ok_or_else(|| invalid("malformed status line"))?;
    let body = &raw[end + 4..];
    let body = if is_chunked(&head) { dechunk(body).ok_or_else(|| invalid("malformed chunked reply"))? } else { body.to_vec() };
    Ok((code, String::from_utf8_lossy(&body).into_owned()))
}

fn is_chunked(head: &str) -> bool {
    head.lines().any(|l| l.starts_with("transfer-encoding:") && l.contains("chunked"))
}

/// Body of a `Transfer-Encoding: chunked` reply.
fn dechunk(mut data: &[u8]) -> Option<Vec<u8>> {
    let mut out = Vec::new();
    loop {
        let line_end = find(data, b"\r\n")?;
        let size_text = std::str::from_utf8(&data[..line_end]).ok()?;
        let size = usize::from_str_radix(size_text.split(';').next()?.trim(), 16).ok()?;
        data = &data[line_end + 2..];
        if size == 0 {
            return Some(out);
        }
        out.extend_from_slice(data.get(..size)?);
        data = data.get(size + 2..)?;
    }
}

/// A reply is complete once its Content-Length bytes, or its last chunk, have arrived.
fn complete(raw: &[u8]) -> bool {
    let Some(end) = find(raw, b"\r\n\r\n") else { return false };
    let head = String::from_utf8_lossy(&raw[..end]).to_ascii_lowercase();
    if is_chunked(&head) {
        return raw.ends_with(b"\r\n0\r\n\r\n") || raw[end + 4..].starts_with(b"0\r\n\r\n");
    }
    head.lines()
        .find_map(|l| l.strip_prefix("content-length:").and_then(|v| v.trim().parse::<usize>().ok()))
        .is_some_and(|len| raw.len() >= (end + 4).saturating_add(len))
}

/// Absolute path of a speaker tool (the agent's PATH is not guaranteed).
fn sys_bin(name: &str) -> String {
    for dir in ["/system/bin", "/system/xbin", "/bin", "/usr/bin"] {
        let p = format!("{dir}/{name}");
        if fs::metadata(&p).is_ok() {
            return p;
        }
    }
    name.to_string()
}

/// A speaker tool's output, within 3 s (a tool stuck in a driver is killed).
fn run(prog: &str, args: &[&str]) -> Option<String> {
    run_within(prog, args, Duration::from_secs(3))
}

fn run_within(prog: &str, args: &[&str], limit: Duration) -> Option<String> {
    output_within(&sys_bin(prog), args, limit)
}

struct Cached {
    key: &'static str,
    /// When `value` was made (None: never).
    at: Option<Instant>,
    value: Option<String>,
    /// Someone is making a new value.
    busy: bool,
}

static CACHE: Mutex<Vec<Cached>> = Mutex::new(Vec::new());
/// A failure is remembered this long (at most `ttl`): a service that is down must not cost a
/// timeout on every request.
const FAILURE_TTL: Duration = Duration::from_secs(10);

fn cache() -> MutexGuard<'static, Vec<Cached>> {
    CACHE.lock().unwrap_or_else(|e| e.into_inner())
}

/// What the cache says for `key`: Ok(value) to use as it is, Err(value) when it is due again and
/// this caller is now the one to make it (the busy mark is set).
fn cache_check(key: &'static str, ttl: Duration) -> Result<Option<String>, Option<String>> {
    let mut c = cache();
    let e = match c.iter().position(|e| e.key == key) {
        Some(i) => &mut c[i],
        None => {
            c.push(Cached { key, at: None, value: None, busy: false });
            let last = c.len() - 1;
            &mut c[last]
        }
    };
    let ttl = if e.value.is_some() { ttl } else { ttl.min(FAILURE_TTL) };
    if e.at.is_some_and(|at| at.elapsed() < ttl) || e.busy {
        return Ok(e.value.clone()); // fresh, or being made: the last value
    }
    e.busy = true;
    Err(e.value.clone())
}

/// Keeps a new value for `key` (and clears its busy mark, also when dropped without one).
struct CacheFill(&'static str);

impl CacheFill {
    fn put(self, v: Option<String>) {
        if let Some(e) = cache().iter_mut().find(|e| e.key == self.0) {
            (e.at, e.value) = (Some(Instant::now()), v);
        }
    }
}

impl Drop for CacheFill {
    fn drop(&mut self) {
        if let Some(e) = cache().iter_mut().find(|e| e.key == self.0) {
            e.busy = false;
        }
    }
}

/// `make()`'s result, made again at most every `ttl`, by one caller at a time: while one makes
/// it, the others get the last value at once.
fn cached(key: &'static str, ttl: Duration, make: impl FnOnce() -> Option<String>) -> Option<String> {
    if let Ok(v) = cache_check(key, ttl) {
        return v;
    }
    let fill = CacheFill(key);
    let v = make();
    fill.put(v.clone());
    v
}

/// Like `cached`, for the slow parts of the page's status: once there is a value, a due one is
/// made on a thread of its own and the request gets the previous one at once. So it is made
/// only while a page asks, never while a request waits (except for the very first).
fn cached_swr(key: &'static str, ttl: Duration, make: impl FnOnce() -> Option<String> + Send + 'static) -> Option<String> {
    let old = match cache_check(key, ttl) {
        Ok(v) => return v,
        Err(old) => old,
    };
    let fill = CacheFill(key);
    if cache().iter().any(|e| e.key == key && e.at.is_some()) {
        // The thread owns `fill`: the busy mark stays until the new value is there. When no
        // thread can start, `fill` is dropped with it and the next request tries again.
        let _ = thread::Builder::new().name("refresh".into()).stack_size(128 * 1024).spawn(move || fill.put(make()));
        return old;
    }
    let v = make();
    fill.put(v.clone());
    v
}

// ── costly requests ──────────────────────────────────────────────────────
// Some answers need no PIN but cost a lot to make: a logcat run, a call to the companion, a
// dozen connections. Each is kept a few seconds and made by one request at a time (the others
// wait for it), and only two are made at once: a page left open in many tabs, or a client
// that asks in a loop, must not load the speaker.

const MAX_HEAVY: usize = 2;
static HEAVY_NOW: AtomicUsize = AtomicUsize::new(0);
/// An error answer is kept at most this long.
const HEAVY_ERROR_TTL: Duration = Duration::from_secs(5);
const UPDATE_CHECK: &str = "update-check";

#[derive(Clone)]
struct Kept {
    code: u16,
    ctype: &'static str,
    body: Arc<[u8]>,
}

impl Kept {
    fn reply(&self) -> Response {
        Response { body: Body::Shared(Arc::clone(&self.body)), ..Response::new(self.code, self.ctype, Vec::new()) }
    }
}

struct Heavy {
    key: String,
    /// The kept answer, when it was made, and how long it is good for.
    reply: Option<(Kept, Instant, Duration)>,
    /// When the last fresh one (that does not take a kept answer) was started.
    fresh: Option<Instant>,
    busy: bool,
}

static HEAVY: Mutex<Vec<Heavy>> = Mutex::new(Vec::new());
static HEAVY_MADE: Condvar = Condvar::new();

fn heavy_list() -> MutexGuard<'static, Vec<Heavy>> {
    HEAVY.lock().unwrap_or_else(|e| e.into_inner())
}

/// One of the `MAX_HEAVY` places, given back when dropped.
struct HeavySlot;

impl HeavySlot {
    fn take() -> Option<HeavySlot> {
        // Made only when a place was taken: dropping a slot gives one back.
        take_place(&HEAVY_NOW, MAX_HEAVY).then(|| HeavySlot)
    }
}

impl Drop for HeavySlot {
    fn drop(&mut self) {
        HEAVY_NOW.fetch_sub(1, Ordering::AcqRel);
    }
}

/// Clears the busy mark of `key` (and wakes those who wait), also when no answer was made.
struct HeavyMaking<'a>(&'a str);

impl Drop for HeavyMaking<'_> {
    fn drop(&mut self) {
        if let Some(h) = heavy_list().iter_mut().find(|h| h.key == self.0) {
            h.busy = false;
        }
        HEAVY_MADE.notify_all();
    }
}

/// How long to keep an answer: `ttl`, an error only a few seconds.
fn keep_for(ttl: Duration, r: Response) -> (Response, Duration) {
    let keep = if r.code >= 400 { ttl.min(HEAVY_ERROR_TTL) } else { ttl };
    (r, keep)
}

/// The kept answer for `key`, or a new one from `make()` (which says how long it is good for).
/// A request that comes while one is being made waits for it (up to `wait`). With `fresh_gap`
/// a kept answer counts only when it comes from a fresh run started within that gap.
fn heavy(key: &str, fresh_gap: Option<Duration>, wait: Duration, make: impl FnOnce() -> (Response, Duration)) -> Response {
    let deadline = Instant::now() + wait;
    let mut list = heavy_list();
    loop {
        let i = match list.iter().position(|h| h.key == key) {
            Some(i) => i,
            None => {
                if list.len() >= 16 {
                    if let Some(j) = list.iter().position(|h| !h.busy) {
                        list.remove(j);
                    }
                }
                list.push(Heavy { key: key.to_string(), reply: None, fresh: None, busy: false });
                list.len() - 1
            }
        };
        let h = &list[i];
        if !h.busy {
            // Within its time (an error's is short), and for a fresh one also from a fresh run
            // that ended within the gap: one waited for counts, however long it ran.
            let usable = h.reply.as_ref().is_some_and(|(_, at, keep)| at.elapsed() < *keep)
                && fresh_gap.is_none_or(|gap| h.fresh.is_some_and(|t| t.elapsed() < gap));
            match (&h.reply, usable) {
                (Some((kept, _, _)), true) => return kept.reply(),
                _ => break,
            }
        }
        let left = deadline.saturating_duration_since(Instant::now());
        if left.is_zero() {
            return Response::error(503, "still working on the last request: try again in a moment");
        }
        list = HEAVY_MADE.wait_timeout(list, left).unwrap_or_else(|e| e.into_inner()).0;
    }
    let Some(slot) = HeavySlot::take() else {
        return Response::error(503, "the speaker is busy with other requests: try again in a moment");
    };
    if let Some(h) = list.iter_mut().find(|h| h.key == key) {
        h.busy = true;
    }
    drop(list);
    let making = HeavyMaking(key);
    let (r, keep) = make();
    let kept = Kept { code: r.code, ctype: r.ctype, body: Arc::from(r.body.bytes()) };
    if let Some(h) = heavy_list().iter_mut().find(|h| h.key == key) {
        h.reply = Some((kept.clone(), Instant::now(), keep));
        if fresh_gap.is_some() {
            h.fresh = Some(Instant::now()); // the gap counts from the end of the run
        }
    }
    drop(making);
    drop(slot);
    kept.reply()
}

/// Drop the kept answer for `key` (what it said is out of date).
fn heavy_forget(key: &str) {
    if let Some(h) = heavy_list().iter_mut().find(|h| h.key == key) {
        (h.reply, h.fresh) = (None, None);
    }
}

/// The Cast server's self-description (name, Cast version, Wi-Fi), when it answers. It changes
/// rarely and the Cast server answers slowly, so it is asked at most once a minute.
fn eureka_info() -> Option<String> {
    cached_swr("eureka", Duration::from_secs(60), || {
        match http_call("GET", "http://127.0.0.1:8008/setup/eureka_info", Duration::from_secs(3)) {
            Ok((200, body)) if body.trim_start().starts_with('{') => Some(body),
            _ => None,
        }
    })
}

fn zeroconf_port(args: &[String]) -> u16 {
    opt_value(args, "--zeroconf-port").and_then(|p| p.parse().ok()).unwrap_or(4070)
}

/// librespot's zeroconf reply, when it answers.
pub(crate) fn librespot_info(args: &[String]) -> Option<String> {
    let url = format!("http://127.0.0.1:{}/?action=getInfo", zeroconf_port(args));
    match http_call("GET", &url, Duration::from_secs(2)) {
        Ok((200, body)) => Some(body),
        _ => None,
    }
}

fn credentials_saved() -> bool {
    fs::metadata(base_dir().join("cache").join("credentials.json")).is_ok()
}

/// The level the page shows; only ever read (Cast mirrors a level set on the control as a mute).
fn speaker_volume(args: &[String]) -> Option<u8> {
    let (card, control) = mixer_of(args, "Master");
    cached("volume", Duration::from_secs(4), || read_volume(&card, &control).map(|v| v.to_string()))
        .and_then(|v| v.parse().ok())
}

/// A 409 while an update or rollback runs: restarting the agent then would lose it (or leave a
/// half-installed version).
fn busy() -> Option<Response> {
    let j = job();
    j.running.then(|| Response::error(409, &format!("{} is running: try again when it has finished", j.kind)))
}

fn exit_soon(delay: Duration, why: &'static str) {
    let exit = move || {
        log(&format!("exiting: {why} (the Cast process manager starts the agent again)"));
        log_flush();
        process::exit(0);
    };
    if !spawn_named("exit", move || {
        thread::sleep(delay);
        exit();
    }) {
        exit(); // the reply is lost, but the restart that was asked for happens
    }
}

// ── status ───────────────────────────────────────────────────────────────
// The page asks every 5 s, from every open tab. One status is made at a time and serves every
// request for a moment; its slow parts (the Cast server, the official client, librespot's
// zeroconf server, df) are kept and renewed in the background, the kernel's (PCM, Wi-Fi) come
// from the sampler. So a request never waits for a service or a driver.

/// A status is reused this long (and made again at once after any change from the page).
const STATUS_TTL: Duration = Duration::from_millis(1500);
/// Events in the status (the newest); the agent keeps a few more.
const STATUS_EVENTS: usize = 6;
/// The page counts as open this long after it last asked for its status.
const PAGE_ACTIVE: Duration = Duration::from_secs(30);

struct StatusCache {
    at: Option<Instant>,
    json: Option<Arc<[u8]>>,
    busy: bool,
    /// Counts changes from the page: a status begun before the last one is not fresh.
    changes: u64,
}

static STATUS: Mutex<StatusCache> = Mutex::new(StatusCache { at: None, json: None, busy: false, changes: 0 });
static STATUS_MADE: Condvar = Condvar::new();
static LAST_PAGE: Mutex<Option<Instant>> = Mutex::new(None);

fn status_cache() -> MutexGuard<'static, StatusCache> {
    STATUS.lock().unwrap_or_else(|e| e.into_inner())
}

/// Has a page asked for its status lately? Work done only for the page waits for that.
pub(crate) fn page_active() -> bool {
    LAST_PAGE.lock().unwrap_or_else(|e| e.into_inner()).is_some_and(|t| t.elapsed() < PAGE_ACTIVE)
}

/// The next status is made anew: the page changed something.
fn status_changed() {
    let mut c = status_cache();
    c.at = None;
    c.changes = c.changes.wrapping_add(1);
}

/// Clears the busy mark (and wakes those who wait) also when the status is not made.
struct StatusMaking;

impl Drop for StatusMaking {
    fn drop(&mut self) {
        status_cache().busy = false;
        STATUS_MADE.notify_all();
    }
}

fn status_reply(cfg: &Config) -> Response {
    *LAST_PAGE.lock().unwrap_or_else(|e| e.into_inner()) = Some(Instant::now());
    let shared_json = |json: Arc<[u8]>| Response { body: Body::Shared(json), ..Response::json(200, String::new()) };
    let mut c = status_cache();
    loop {
        if let (Some(at), Some(json)) = (c.at, &c.json) {
            // Recent, or another request is making the next one: this one is good enough.
            if at.elapsed() < STATUS_TTL || c.busy {
                return shared_json(Arc::clone(json));
            }
        }
        if !c.busy {
            break;
        }
        // Only the very first status (or the first after a change) is waited for.
        let (g, waited) = STATUS_MADE.wait_timeout(c, Duration::from_secs(15)).unwrap_or_else(|e| e.into_inner());
        c = g;
        if waited.timed_out() && c.busy {
            match &c.json {
                Some(json) => return shared_json(Arc::clone(json)),
                None => return Response::error(503, "the status is not ready yet"),
            }
        }
    }
    c.busy = true;
    let changes = c.changes;
    drop(c);
    let making = StatusMaking;
    let json: Arc<[u8]> = status_json(cfg).into_bytes().into();
    keep_status(&mut status_cache(), Arc::clone(&json), changes);
    drop(making);
    shared_json(json)
}

/// Keep a status made since change `begun`: one begun before the page's last change is good for
/// the request that made it, not for the next ones.
fn keep_status(c: &mut StatusCache, json: Arc<[u8]>, begun: u64) {
    c.at = (c.changes == begun).then(Instant::now);
    c.json = Some(json);
}

fn status_json(cfg: &Config) -> String {
    let base = base_dir();
    let args = librespot_args();
    let lr_pid = find_pid("librespot");
    let lr_info = lr_pid.and_then(|_| {
        let args = args.clone();
        cached_swr("librespot", Duration::from_secs(10), move || librespot_info(&args))
    });
    let librespot = Obj::new()
        .bool("running", lr_pid.is_some())
        .opt_num("pid", lr_pid)
        .opt_num("uptime_s", lr_pid.and_then(proc_uptime_s))
        .bool("holds_pcm", lr_pid.is_some_and(holds_pcm))
        .opt("name", opt_value(&args, "--name"))
        .opt("bitrate", opt_value(&args, "--bitrate"))
        .opt("mixer", opt_value(&args, "--mixer"))
        .num("zeroconf_port", zeroconf_port(&args))
        .bool("zeroconf_ok", lr_info.is_some())
        .opt("version", lr_info.as_deref().and_then(|b| json_field(b, "libraryVersion")).as_deref())
        .bool("credentials_saved", credentials_saved())
        .str("last_event", fs::read_to_string(LIBRESPOT_STATE_FILE).unwrap_or_default().trim())
        .end();

    let off_pid = find_pid("spotifyhifi");
    let off_info = cached_swr("esdk", Duration::from_secs(60), || {
        match http_call("GET", "http://127.0.0.1:9095/zc?action=getInfo", Duration::from_secs(2)) {
            Ok((200, body)) => Some(body),
            _ => None,
        }
    })
    .unwrap_or_default();
    let hidden = shared().official_hidden; // the lock is not held over the /proc reads below
    let official = Obj::new()
        .bool("running", off_pid.is_some())
        .bool("hidden", hidden)
        .opt_num("pid", off_pid)
        .opt_num("uptime_s", off_pid.and_then(proc_uptime_s))
        .opt("version", json_field(&off_info, "libraryVersion").as_deref())
        .opt("name", json_field(&off_info, "remoteName").as_deref())
        .end();

    let (watchdog, libre) = {
        let s = shared();
        let watchdog = Obj::new()
            .num("spotify_restarts", s.spotify_restarts)
            .num("spotify_launches", s.spotify_launches)
            .num("librespot_stops", s.librespot_stops)
            .num("silent_starts", s.silent_starts)
            .num("shells_stopped", s.shells_stopped)
            .raw("events", arr(s.events.iter().rev().take(STATUS_EVENTS).map(|e| q(e))))
            .end();
        let libre = Obj::new().str("source", &s.libre_source).str("state", &s.libre_state).end();
        (watchdog, libre)
    };

    let pcm = pcm_status_seen();
    let pcm_state = pcm.lines().next().unwrap_or("").trim_start_matches("state:").trim().to_string();
    let audio = Obj::new()
        .str("pcm", &pcm_state)
        .opt_num("volume", speaker_volume(&args))
        .raw("libre", libre)
        .end();

    let versions = kv_json(&read_kv(&base.join("VERSIONS")));
    let prev = read_kv(&base.join("prev").join("VERSIONS"));
    let prev = if prev.is_empty() { "null".to_string() } else { kv_json(&prev) };

    let agent = Obj::new()
        .str("version", env!("CARGO_PKG_VERSION"))
        .str("build", BUILD)
        .num("pid", process::id())
        .num("uptime_s", STARTED.get().map_or(0, |t| t.elapsed().as_secs()))
        .str("base", &base.display().to_string())
        .end();
    let ui = Obj::new()
        .bool("pin_required", !cfg.ui_pin.is_empty())
        .bool("companion", companion_base(cfg).is_some())
        .str("companion_url", &cfg.companion_url)
        .str("speaker_id", &cfg.speaker_id)
        .end();
    let same_name = arr(SAME_NAME.lock().unwrap_or_else(|e| e.into_inner()).iter().map(|s| q(s)));

    Obj::new()
        .raw("agent", agent)
        .raw("speaker", speaker_json().unwrap_or_else(speaker_fallback))
        .raw("same_name", same_name)
        .raw("librespot", librespot)
        .raw("official", official)
        .raw("audio", audio)
        .raw("network", network_json(cfg))
        .raw("system", system_json())
        .raw("watchdog", watchdog)
        .raw("versions", versions)
        .raw("prev_versions", prev)
        .raw("ui", ui)
        .raw("job", job_status_json())
        .end()
}

fn firmware_build() -> Option<String> {
    static BUILD_ID: OnceLock<Option<String>> = OnceLock::new();
    BUILD_ID
        .get_or_init(|| {
            fs::read_to_string("/system/build.prop")
                .unwrap_or_default()
                .lines()
                .find_map(|l| l.strip_prefix("ro.build.display.id=").map(str::to_string))
        })
        .clone()
}

/// Maker and model, as the firmware sets them at boot (they are not in build.prop); asked once
/// when the page starts (`serve`).
static MODEL: OnceLock<Option<String>> = OnceLock::new();

fn read_device_model() -> Option<String> {
    let prop = |key| run("getprop", &[key]).map(|v| v.trim().to_string()).filter(|v| !v.is_empty());
    match (prop("ro.product.manufacturer"), prop("ro.product.model")) {
        (Some(maker), Some(model)) => Some(format!("{maker} {model}")),
        (maker, model) => model.or(maker),
    }
}

fn device_model() -> Option<String> {
    MODEL.get().cloned().flatten()
}

/// Name and versions from the Cast server; None while it is not up yet. The Cast id and the
/// MAC tell two speakers apart even when they have the same name.
fn speaker_json() -> Option<String> {
    let eureka = eureka_info()?;
    Some(
        Obj::new()
            .opt("name", json_field(&eureka, "name").as_deref())
            .opt("cast", json_field(&eureka, "cast_build_revision").as_deref())
            .opt("locale", json_field(&eureka, "locale").as_deref())
            .opt("build", firmware_build().as_deref())
            .opt("has_update", json_field(&eureka, "has_update").as_deref())
            .opt("model", device_model().as_deref())
            .opt("cast_id", json_field(&eureka, "ssdp_udn").as_deref())
            .opt("mac", json_field(&eureka, "mac_address").as_deref())
            .end(),
    )
}

// ── other devices with this speaker's name ───────────────────────────────

/// Devices elsewhere on the network that announce this speaker's name ("192.168.1.150 (Google
/// Cast)"), from the last look.
static SAME_NAME: Mutex<Vec<String>> = Mutex::new(Vec::new());

/// Every 10 minutes: does another device announce this speaker's name (Google Cast or AirPlay)?
/// Two of them would be easy to mix up in the apps.
pub fn name_watch() {
    thread::sleep(Duration::from_secs(30)); // the Cast server and the network first
    loop {
        let own = eureka_info().and_then(|e| json_field(&e, "name")).map(|n| n.trim().to_lowercase());
        let ip = lan_ip().and_then(|s| s.parse::<Ipv4Addr>().ok());
        if let Some(own) = own.filter(|n| !n.is_empty()) {
            let mut found: Vec<String> = lan_names(Duration::from_secs(3))
                .into_iter()
                .filter(|(addr, _, name)| Some(*addr) != ip && name.trim().to_lowercase() == own)
                .map(|(addr, kind, _)| format!("{addr} ({kind})"))
                .collect();
            found.sort();
            found.dedup();
            *SAME_NAME.lock().unwrap_or_else(|e| e.into_inner()) = found;
        }
        thread::sleep(Duration::from_secs(600));
    }
}

/// The names devices on the network announce: Google Cast friendly names and AirPlay names. One
/// mDNS query as "legacy unicast" (RFC 6762 6.7): sent from an ordinary port, it is answered
/// straight to that port, so port 5353 stays with mdnsd and librespot.
fn lan_names(wait: Duration) -> Vec<(Ipv4Addr, &'static str, String)> {
    let Ok(sock) = UdpSocket::bind("0.0.0.0:0") else { return Vec::new() };
    let _ = sock.set_read_timeout(Some(Duration::from_millis(250)));
    let id = (process::id() as u16) | 1;
    let query = mdns_query(id, &["_googlecast._tcp.local", "_raop._tcp.local", "_airplay._tcp.local"]);
    if sock.send_to(&query, "224.0.0.251:5353").is_err() {
        return Vec::new();
    }
    let deadline = Instant::now() + wait;
    let mut out = Vec::new();
    let mut buf = [0u8; 9000];
    while Instant::now() < deadline {
        if let Ok((n, SocketAddr::V4(from))) = sock.recv_from(&mut buf) {
            if n > 12 && buf[..2] == id.to_be_bytes() {
                out.extend(mdns_names(&buf[..n]).into_iter().map(|(kind, name)| (*from.ip(), kind, name)));
            }
        }
    }
    out
}

/// A DNS query for the PTR records of `services`.
fn mdns_query(id: u16, services: &[&str]) -> Vec<u8> {
    let mut q = Vec::with_capacity(96);
    q.extend_from_slice(&id.to_be_bytes());
    q.extend_from_slice(&[0, 0]);
    q.extend_from_slice(&(services.len() as u16).to_be_bytes());
    q.extend_from_slice(&[0; 6]);
    for s in services {
        for label in s.split('.') {
            q.push(label.len() as u8);
            q.extend_from_slice(label.as_bytes());
        }
        q.extend_from_slice(&[0, 0, 12, 0, 1]); // end of name, type PTR, class IN
    }
    q
}

/// The DNS name at `off` (compression pointers followed, a few levels at most) and the offset
/// right after it.
fn dns_name(p: &[u8], mut off: usize, depth: u8) -> Option<(String, usize)> {
    let mut labels: Vec<String> = Vec::new();
    loop {
        let n = *p.get(off)? as usize;
        if n == 0 {
            return Some((labels.join("."), off + 1));
        }
        if n & 0xC0 == 0xC0 {
            if depth > 8 {
                return None;
            }
            let ptr = ((n & 0x3F) << 8) | *p.get(off + 1)? as usize;
            labels.push(dns_name(p, ptr, depth + 1)?.0);
            return Some((labels.join("."), off + 2));
        }
        labels.push(String::from_utf8_lossy(p.get(off + 1..off + 1 + n)?).into_owned());
        off += 1 + n;
    }
}

/// The friendly names in one mDNS answer: Google Cast ("fn=" in its TXT record) and AirPlay (the
/// instance names, "MAC@Name" for _raop).
fn mdns_names(p: &[u8]) -> Vec<(&'static str, String)> {
    let mut out = Vec::new();
    let word = |i: usize| p.get(i..i + 2).map(|b| u16::from_be_bytes([b[0], b[1]]) as usize);
    let (Some(qd), Some(an), Some(ns), Some(ar)) = (word(4), word(6), word(8), word(10)) else { return out };
    let mut off = 12;
    for _ in 0..qd {
        let Some((_, o)) = dns_name(p, off, 0) else { return out };
        off = o + 4;
    }
    for _ in 0..an + ns + ar {
        let Some((name, o)) = dns_name(p, off, 0) else { break };
        let (Some(rtype), Some(len)) = (word(o), word(o + 8)) else { break };
        let data = o + 10;
        let Some(rdata) = p.get(data..data + len) else { break };
        if rtype == 12 {
            if let Some((target, _)) = dns_name(p, data, 0) {
                let instance = target.split("._").next().unwrap_or_default();
                if name.starts_with("_raop.") {
                    if let Some((_, n)) = instance.split_once('@') {
                        out.push(("AirPlay", n.to_string()));
                    }
                } else if name.starts_with("_airplay.") {
                    out.push(("AirPlay", instance.to_string()));
                }
            }
        } else if rtype == 16 && name.contains("._googlecast.") {
            let mut i = 0;
            while i < rdata.len() {
                let l = rdata[i] as usize;
                if let Some(v) = rdata.get(i + 1..i + 1 + l).and_then(|kv| kv.strip_prefix(b"fn=")) {
                    out.push(("Google Cast", String::from_utf8_lossy(v).into_owned()));
                }
                i += 1 + l;
            }
        }
        off = data + len;
    }
    out
}

fn speaker_fallback() -> String {
    Obj::new().raw("name", "null").raw("cast", "null").raw("locale", "null").opt("build", firmware_build().as_deref()).end()
}

/// The speaker's address on its network: the source address the kernel picks for the default
/// route (connecting a UDP socket sends nothing).
fn lan_ip() -> Option<String> {
    let s = UdpSocket::bind("0.0.0.0:0").ok()?;
    s.connect("192.0.2.1:9").ok()?; // TEST-NET-1, never contacted
    let ip = s.local_addr().ok()?.ip();
    (!ip.is_unspecified()).then(|| ip.to_string())
}

/// The Wi-Fi link as the sampler last read it (once a minute while the page is open).
#[derive(Clone, Debug, Default, PartialEq)]
pub(crate) struct Wifi {
    ssid: Option<String>,
    freq_mhz: Option<u32>,
    bitrate_mbps: Option<f64>,
    signal_dbm: Option<i32>,
    noise_dbm: Option<i32>,
}

/// For the sampler: `iwconfig wlan0` and /proc/net/wireless (both end in the Wi-Fi driver), each
/// with a timeout.
pub(crate) fn sample_wifi() -> Wifi {
    // The firmware scans for Wi-Fi networks for 3.5 s every 63 s and holds the driver meanwhile:
    // both wait for it, which is no stuck driver.
    let iw = run_within("iwconfig", &["wlan0"], Duration::from_secs(6)).unwrap_or_default();
    let (ssid, freq_mhz, bitrate_mbps) = parse_iwconfig(&iw);
    let wireless = read_within("/proc/net/wireless", Duration::from_secs(5)).value.unwrap_or_default();
    let (signal_dbm, noise_dbm) = parse_wireless(&wireless, "wlan0").unzip();
    Wifi { ssid, freq_mhz, bitrate_mbps, signal_dbm, noise_dbm }
}

fn network_json(cfg: &Config) -> String {
    let eureka = eureka_info().unwrap_or_default();
    let wifi = wifi_seen().unwrap_or_default();
    let ip = lan_ip();
    let ssid = json_field(&eureka, "ssid").or(wifi.ssid);
    Obj::new()
        .opt("ssid", ssid.as_deref())
        .opt_num("freq_mhz", wifi.freq_mhz)
        .opt_num("bitrate_mbps", wifi.bitrate_mbps)
        .opt_num("signal_dbm", wifi.signal_dbm)
        .opt_num("noise_dbm", wifi.noise_dbm)
        .opt("ip", ip.as_deref().or(json_field(&eureka, "ip_address").as_deref()))
        .raw("fastfail_routes", arr(unreachable_routes().iter().map(|r| q(r))))
        .raw("fastfail_hosts", arr(cfg.fastfail_hosts.iter().map(|h| q(h))))
        .end()
}

/// (ESSID, frequency in MHz, bit rate in Mb/s) from `iwconfig wlan0`.
fn parse_iwconfig(text: &str) -> (Option<String>, Option<u32>, Option<f64>) {
    let ssid = text
        .split_once("ESSID:\"")
        .and_then(|(_, r)| r.split_once('"'))
        .map(|(s, _)| s.to_string());
    let number_after = |key: &str| -> Option<(f64, String)> {
        let (_, r) = text.split_once(key)?;
        let r = r.trim_start_matches([':', '=']).trim_start();
        let end = r.find(|c: char| !(c.is_ascii_digit() || c == '.')).unwrap_or(r.len());
        let unit = r[end..].split_whitespace().next().unwrap_or("").to_string();
        Some((r[..end].parse().ok()?, unit))
    };
    let freq = number_after("Frequency").map(|(v, unit)| if unit.starts_with("GHz") { (v * 1000.0).round() as u32 } else { v as u32 });
    let bitrate = number_after("Bit Rate").map(|(v, _)| v);
    (ssid, freq, bitrate)
}

/// (signal dBm, noise dBm) of `iface` from /proc/net/wireless.
fn parse_wireless(text: &str, iface: &str) -> Option<(i32, i32)> {
    let line = text.lines().find(|l| l.trim_start().starts_with(&format!("{iface}:")))?;
    let f: Vec<&str> = line.split_whitespace().collect();
    let num = |s: &str| s.trim_end_matches('.').parse::<i32>().ok();
    Some((num(f.get(3)?)?, num(f.get(4)?)?))
}

fn system_json() -> String {
    let meminfo = fs::read_to_string("/proc/meminfo").unwrap_or_default();
    let mem = |key: &str| -> u64 {
        meminfo
            .lines()
            .find_map(|l| l.strip_prefix(key).and_then(|r| r.split_whitespace().next()).and_then(|v| v.parse().ok()))
            .unwrap_or(0)
    };
    let available = mem("MemFree:") + mem("Buffers:") + mem("Cached:");
    let load = fs::read_to_string("/proc/loadavg").unwrap_or_default();
    let load: Vec<&str> = load.split_whitespace().take(3).collect();
    let disk = cached_swr("disk", Duration::from_secs(60), || {
        let mount = base_dir().parent().map(|p| p.display().to_string()).unwrap_or_else(|| "/".into());
        run("df", &[mount.as_str()]).and_then(|t| t.lines().nth(1)?.split_whitespace().nth(3).map(str::to_string))
    })
    .unwrap_or_default();
    // The load average means something only next to the number of cores.
    static CPUS: OnceLock<usize> = OnceLock::new();
    let cpus = *CPUS.get_or_init(|| {
        let info = fs::read_to_string("/proc/cpuinfo").unwrap_or_default();
        info.lines().filter(|l| l.starts_with("processor")).count().max(1)
    });
    Obj::new()
        .opt_num("uptime_s", system_uptime().map(|u| u as u64))
        .str("load", &load.join(" "))
        .num("cpus", cpus as u64)
        .num("mem_total_kb", mem("MemTotal:"))
        .num("mem_available_kb", available)
        .str("disk_free_kb", &disk)
        .end()
}

// ── logs ─────────────────────────────────────────────────────────────────
// On a busy speaker the shared logcat buffer holds only minutes, so the agent's logcat reader
// keeps the recent lines of librespot and the agent here.

const RING_LINES: usize = 2000;
static RING: Mutex<VecDeque<String>> = Mutex::new(VecDeque::new());

fn ring() -> MutexGuard<'static, VecDeque<String>> {
    RING.lock().unwrap_or_else(|e| e.into_inner())
}

/// At most this much text in all (a line can be 4 KB).
const RING_BYTES: usize = 512 * 1024;
static RING_SIZE: AtomicUsize = AtomicUsize::new(0);

/// Keep one line of librespot's or the agent's output (from the agent's logcat reader).
pub(crate) fn remember(line: String) {
    let mut ring = ring();
    let mut size = RING_SIZE.load(Ordering::Relaxed) + line.len();
    ring.push_back(line);
    while ring.len() > RING_LINES || size > RING_BYTES {
        match ring.pop_front() {
            Some(old) => size -= old.len(),
            None => break,
        }
    }
    RING_SIZE.store(size, Ordering::Relaxed);
}

/// librespot's and the agent's last `n` lines as one text: taken from the reader's ring under
/// its lock (nothing else is copied), or straight from logcat while the ring is empty.
fn our_lines(n: usize) -> String {
    {
        let ring = ring();
        if !ring.is_empty() {
            return last_lines(ring.range(ring.len().saturating_sub(n)..).map(String::as_str));
        }
    }
    logcat(&["lithify-agent:*"], n).unwrap_or_default()
}

fn last_lines<'a>(lines: impl Iterator<Item = &'a str>) -> String {
    let mut out = String::new();
    for l in lines {
        if !out.is_empty() {
            out.push('\n');
        }
        out.push_str(l);
    }
    out
}

/// The last `n` lines of `text`.
fn tail_lines(text: &str, n: usize) -> String {
    let skip = text.lines().count().saturating_sub(n);
    last_lines(text.lines().skip(skip))
}

fn logcat(filters: &[&str], n: usize) -> Option<String> {
    let mut args = vec!["-d", "-v", "time", "-s"];
    args.extend_from_slice(filters);
    let text = run("logcat", &args)?;
    let lines: Vec<&str> = text.lines().filter(|l| !l.starts_with("--------- beginning of")).collect();
    Some(last_lines(lines[lines.len().saturating_sub(n)..].iter().copied()))
}

fn tail_file(path: &str, n: usize) -> String {
    let text = fs::read_to_string(format!("{path}.1")).unwrap_or_default() + &fs::read_to_string(path).unwrap_or_default();
    tail_lines(&text, n)
}

/// The log asked for: the agent's own file, the official client's lines, or librespot's and the
/// agent's (anything else).
fn log_source(req: &Request) -> &'static str {
    match req.param("source") {
        Some("agent") => "agent",
        Some("official") => "official",
        _ => "ours",
    }
}

/// How many lines: rounded up to one of a few sizes (the page asks for 400).
fn log_lines_asked(req: &Request) -> usize {
    let n = req.param("n").and_then(|v| v.parse::<usize>().ok()).unwrap_or(400);
    [400, 1000, 3000].into_iter().find(|size| n <= *size).unwrap_or(3000)
}

fn logs(req: &Request) -> Response {
    let n = log_lines_asked(req);
    let text = match log_source(req) {
        "agent" => Some(tail_file(LOG_PATH, n)),
        "official" => logcat(&["SPOTIFY:*", "SP_RING:*"], n),
        _ => Some(our_lines(n)), // librespot and the agent, via the Cast process manager
    };
    match text {
        Some(t) if !t.trim().is_empty() => Response::text(200, &redact(&t)),
        Some(_) => Response::text(200, "(no lines)"),
        None => Response::text(200, &redact(&tail_file(LOG_PATH, n))),
    }
}

/// The official client writes its Spotify login (the reusable credentials "blob") into the
/// system log, and librespot names the account it signed in with. The page shows logs to the
/// whole network without a PIN, so those are cut off first.
fn redact(text: &str) -> String {
    text.lines().map(redact_line).collect::<Vec<_>>().join("\n")
}

fn redact_line(l: &str) -> String {
    if let Some(i) = l.to_ascii_lowercase().find("blob") {
        return format!("{}blob [hidden]", &l[..i]);
    }
    mask_account(l).unwrap_or_else(|| l.to_string())
}

/// "Authenticated as 'name' !" (librespot) with only the first two characters of the name.
fn mask_account(l: &str) -> Option<String> {
    const KEY: &str = "Authenticated as ";
    let at = l.find(KEY)? + KEY.len();
    let rest = &l[at..];
    let quote = rest.chars().next().filter(|c| matches!(c, '\'' | '"'));
    let name_on = &rest[quote.map_or(0, char::len_utf8)..];
    let end = match quote {
        Some(q) => name_on.find(q).unwrap_or(name_on.len()),
        None => name_on.find(char::is_whitespace).unwrap_or(name_on.len()),
    };
    let kept: String = name_on[..end].chars().take(2).collect();
    let q = quote.map(String::from).unwrap_or_default();
    Some(format!("{}{q}{kept}***{}", &l[..at], &name_on[end..]))
}

// ── tests ────────────────────────────────────────────────────────────────

fn test_network(cfg: &Config) -> String {
    let routes = unreachable_routes();
    let mut targets: Vec<(String, u16, String)> =
        NET_TARGETS.iter().map(|(h, p, l)| (h.to_string(), *p, l.to_string())).collect();
    for h in &cfg.fastfail_hosts {
        if !targets.iter().any(|t| &t.0 == h) {
            targets.push((h.clone(), 443, "fail-fast host (config)".into()));
        }
    }
    let handles: Vec<_> = targets
        .into_iter()
        .map(|(host, port, label)| {
            let routes = routes.clone();
            thread::Builder::new().stack_size(128 * 1024).spawn(move || probe(&host, port, &label, &routes))
        })
        .collect();
    let results: Vec<String> = handles.into_iter().filter_map(|h| h.ok()?.join().ok()).collect();
    Obj::new().raw("results", arr(results)).str("checked", &utc_now()).end()
}

fn probe(host: &str, port: u16, label: &str, routes: &[String]) -> String {
    let t = Instant::now();
    let resolved = (host, port).to_socket_addrs();
    let o = Obj::new().str("host", host).num("port", port).str("label", label).num("dns_ms", t.elapsed().as_millis());
    let addrs: Vec<SocketAddr> = match resolved {
        Ok(a) => a.filter(|a| a.is_ipv4()).take(3).collect(),
        Err(e) => return o.str("verdict", "dns").str("detail", &e.to_string()).raw("addrs", "[]").end(),
    };
    if addrs.is_empty() {
        return o.str("verdict", "dns").str("detail", "no IPv4 address").raw("addrs", "[]").end();
    }
    let (mut best, mut all_ff, mut items) = (None::<u128>, true, Vec::new());
    for a in &addrs {
        let ff = match a.ip() {
            std::net::IpAddr::V4(v4) => routes.iter().any(|r| in_route(v4, r)),
            _ => false,
        };
        all_ff &= ff;
        let t = Instant::now();
        let r = TcpStream::connect_timeout(a, Duration::from_secs(3));
        let ms = t.elapsed().as_millis();
        let (result, detail) = match r {
            Ok(_) => {
                best = Some(best.map_or(ms, |b| b.min(ms)));
                ("ok", String::new())
            }
            Err(e) if ff => ("failfast", e.to_string()),
            Err(e) if matches!(e.kind(), io::ErrorKind::TimedOut | io::ErrorKind::WouldBlock) => ("timeout", e.to_string()),
            Err(e) => ("error", e.to_string()),
        };
        items.push(Obj::new().str("ip", &a.ip().to_string()).num("ms", ms).str("result", result).str("detail", &detail).end());
    }
    let verdict = match best {
        Some(ms) if ms > 300 => "slow",
        Some(_) => "ok",
        None if all_ff => "failfast",
        None => "failing",
    };
    o.str("verdict", verdict).opt_num("best_ms", best).raw("addrs", arr(items)).end()
}

fn test_librespot() -> String {
    let args = librespot_args();
    let pid = find_pid("librespot");
    let t = Instant::now();
    let info = librespot_info(&args);
    let zeroconf = match &info {
        Some(body) => Obj::new()
            .bool("ok", true)
            .num("ms", t.elapsed().as_millis())
            .opt("name", json_field(body, "remoteName").as_deref())
            .opt("version", json_field(body, "libraryVersion").as_deref()),
        None => Obj::new().bool("ok", false).str("detail", "no answer from librespot's zeroconf server"),
    }
    .end();
    // Counted under the ring's lock, line by line (nothing copied but the few recent ones).
    let mut stats = LogStats::default();
    let since = {
        let ring = ring();
        ring.iter().for_each(|l| stats.add(l));
        ring.front().map(|l| l.chars().take(18).collect::<String>())
    };
    let since = since.unwrap_or_else(|| {
        let text = our_lines(RING_LINES); // the ring is empty: straight from logcat
        text.lines().for_each(|l| stats.add(l));
        text.lines().next().map(|l| l.chars().take(18).collect()).unwrap_or_default()
    });
    Obj::new()
        .bool("running", pid.is_some())
        .opt_num("pid", pid)
        .opt_num("uptime_s", pid.and_then(proc_uptime_s))
        .bool("holds_pcm", pid.is_some_and(holds_pcm))
        .bool("credentials_saved", credentials_saved())
        .raw("zeroconf", zeroconf)
        .raw(
            "log",
            Obj::new()
                .str("since", since.trim())
                .num("underruns", stats.underruns)
                .num("warnings", stats.warnings)
                .num("expected", stats.expected)
                .num("errors", stats.errors)
                .num("sessions", stats.sessions)
                .raw("recent", arr(stats.recent.iter().map(|l| q(&redact_line(l)))))
                .end(),
        )
        .end()
}

/// Health counters over librespot's log lines ("[time LEVEL librespot_x] message").
#[derive(Default)]
struct LogStats {
    underruns: u32,
    warnings: u32,
    /// Warnings that are expected here and say nothing is wrong (see `expected_warning`).
    expected: u32,
    errors: u32,
    sessions: u32,
    recent: Vec<String>,
    /// When the last track was loaded and librespot last signed in (milliseconds into the day,
    /// from the logcat time).
    loaded_at: Option<u32>,
    login_at: Option<u32>,
}

/// The dealer of a session that a new login replaced says it lost its websocket this soon after.
const SWITCH_DEALER_MS: u32 = 10_000;

/// A device that ran dry this soon after the next track was loaded is the switch itself: the
/// sound stopped while it loaded.
const SWITCH_UNDERRUN_MS: u32 = 1500;

/// Milliseconds into the day of a logcat line ("10-07 17:53:59.189 I/...").
fn logcat_ms(line: &str) -> Option<u32> {
    let t = line.get(6..18)?;
    let (h, rest) = t.split_once(':')?;
    let (m, rest) = rest.split_once(':')?;
    let (s, ms) = rest.split_once('.')?;
    Some(((h.parse::<u32>().ok()? * 60 + m.parse::<u32>().ok()?) * 60 + s.parse::<u32>().ok()?) * 1000 + ms.parse::<u32>().ok()?)
}

/// librespot warnings that are part of normal playback here: a CDN address the fail-fast
/// routes block on purpose (librespot moves on to the next one at once), and librespot 0.8's
/// notes around starting, transferring and stopping playback.
fn expected_warning(line: &str) -> bool {
    (line.contains("librespot_audio::fetch") && line.contains("NetworkUnreachable"))
        || line.contains("context is not available")
        || line.contains("failed filling up next_track during stopping")
        || line.contains("Invalid start position of")
}

impl LogStats {
    fn add(&mut self, line: &str) {
        let warn = line.contains(" WARN  librespot");
        let error = line.contains(" ERROR librespot");
        if line.contains("Authenticated as") {
            self.sessions += 1;
            self.login_at = logcat_ms(line);
        }
        if line.contains("librespot_playback::player] ") && line.ends_with(" loaded") {
            self.loaded_at = logcat_ms(line);
        }
        if !(warn || error) {
            return;
        }
        let underrun = line.contains("Broken pipe") || line.contains("underrun");
        let within = |since: Option<u32>, ms: u32| match (since, logcat_ms(line)) {
            (Some(at), Some(now)) => now >= at && now - at <= ms,
            _ => false,
        };
        let old_dealer = line.contains("librespot_core::dealer] Websocket connection failed");
        if warn
            && (expected_warning(line)
                || (underrun && within(self.loaded_at, SWITCH_UNDERRUN_MS))
                || (old_dealer && within(self.login_at, SWITCH_DEALER_MS)))
        {
            self.expected += 1;
            return;
        }
        if warn {
            self.warnings += 1;
        } else {
            self.errors += 1;
        }
        if underrun {
            self.underruns += 1;
        }
        if self.recent.len() >= 8 {
            self.recent.remove(0);
        }
        self.recent.push(line.to_string());
    }
}

// ── restarts ─────────────────────────────────────────────────────────────

fn restart_librespot() -> Response {
    if find_pid("librespot").is_none() {
        return Response::error(409, "librespot is not running (the Cast process manager starts it)");
    }
    let pids = restart_librespot_now();
    event(&format!("librespot restarted from the web page (pid {pids:?})"));
    Response::done("librespot restarts; it is back in a few seconds")
}

fn restart_official() -> Response {
    if shared().official_hidden {
        return Response::error(409, "the official Spotify is hidden while librespot works (setting hide_official_spotify)");
    }
    match official_spotify(SPOTIFY_RESTART, Duration::from_secs(12)) {
        Ok(out) => {
            event("official Spotify restarted from the web page");
            Response::done(&format!("official Spotify restarted (pid {})", out.trim()))
        }
        Err(e) => Response::error(502, &format!("root console: {e}")),
    }
}

// ── updates (through the companion) ──────────────────────────────────────

/// Only plain characters may reach the update scripts.
fn safe_url(url: &str) -> bool {
    url.starts_with("http://")
        && url.len() < 200
        && url.chars().all(|c| c.is_ascii_alphanumeric() || ".:/-_[]".contains(c))
}

fn companion_base(cfg: &Config) -> Option<String> {
    (!cfg.companion_url.is_empty() && safe_url(&cfg.companion_url)).then(|| cfg.companion_url.clone())
}

fn companion(cfg: &Config, method: &str, path: &str, timeout: Duration) -> Response {
    let Some(base) = companion_base(cfg) else { return Response::error(503, NO_COMPANION) };
    match http_call(method, &format!("{base}{path}"), timeout) {
        Ok((code, body)) if body.trim_start().starts_with('{') => Response::json(code, body),
        Ok((code, _)) => Response::error(502, &format!("companion answered HTTP {code} without JSON")),
        Err(e) => Response::error(502, &format!("companion {base} not reachable: {e}")),
    }
}

/// The companion's view of updates, and how long it may be kept: a minute, or a few seconds when
/// the companion could not be asked at all (the computer may be just waking up).
fn update_check(cfg: &Config, fresh: bool) -> (Response, Duration) {
    let Some(base) = companion_base(cfg) else { return keep_for(Duration::ZERO, Response::error(503, NO_COMPANION)) };
    let latest_url = format!("{base}/api/latest?speaker={}", cfg.speaker_id);
    let check_url = format!("{base}/api/check{}", if fresh { "?fresh=1" } else { "" });
    // Both at once: the check can take minutes, the staged bundle only seconds.
    let (latest, check) = thread::scope(|sc| {
        let latest = thread::Builder::new()
            .stack_size(128 * 1024)
            .spawn_scoped(sc, || http_call("GET", &latest_url, Duration::from_secs(60)));
        let check = http_call("POST", &check_url, Duration::from_secs(300));
        let latest = match latest {
            Ok(h) => h.join().unwrap_or_else(|_| Err(io::Error::other("thread failed"))),
            Err(_) => http_call("GET", &latest_url, Duration::from_secs(60)),
        };
        (latest, check)
    });
    let part = |r: io::Result<(u16, String)>| -> String {
        match r {
            Ok((200, body)) => Obj::new().bool("ok", true).str("data", body.trim()).end(),
            Ok((code, body)) => Obj::new()
                .bool("ok", false)
                .str("error", &json_field(&body, "error").unwrap_or_else(|| format!("HTTP {code}")))
                .end(),
            Err(e) => Obj::new().bool("ok", false).str("error", &format!("companion {base} not reachable: {e}")).end(),
        }
    };
    let bundle_new = matches!(&latest, Ok((200, body)) if bundle_is_new(body));
    let failed = !matches!(latest, Ok((200, _))) && !matches!(check, Ok((200, _)));
    let latest = part(latest);
    let check = part(check);
    let reply = Response::json(
        200,
        Obj::new().str("companion", &base).bool("bundle_new", bundle_new).raw("latest", latest).raw("check", check).end(),
    );
    (reply, if failed { HEAVY_ERROR_TTL } else { Duration::from_secs(60) })
}

// ── settings (the web page's form) ───────────────────────────────────────

fn settings_json() -> String {
    let Some((s, _)) = settings::load() else {
        return Obj::new().bool("available", false).end();
    };
    // The PIN is never sent back; the page only learns whether one is set.
    let values = s
        .values()
        .iter()
        .fold(Obj::new(), |o, (k, v)| o.str(k, if *k == "ui_pin" { "" } else { v }))
        .end();
    let schema = arr(settings::schema().iter().map(|o| {
        let mut x = Obj::new()
            .str("key", o.key)
            .str("section", o.section)
            .str("type", o.kind.name())
            .str("default", o.default)
            .str("group", o.group)
            .str("restart", o.restart);
        if o.kind == settings::Kind::Choice {
            x = x.raw("choices", arr(o.choices().iter().map(|c| q(c))));
        }
        if let Some((lo, hi)) = o.range() {
            x = x.num("min", lo).num("max", hi);
        }
        x.end()
    }));
    Obj::new()
        .bool("available", true)
        .raw("values", values)
        .raw("schema", schema)
        .bool("pin_set", !s.get("ui_pin").is_empty())
        .str("updated", &s.updated)
        .str("by", &s.by)
        .end()
}

fn hex_digit(b: u8) -> Option<u8> {
    match b {
        b'0'..=b'9' => Some(b - b'0'),
        b'a'..=b'f' => Some(b - b'a' + 10),
        b'A'..=b'F' => Some(b - b'A' + 10),
        _ => None,
    }
}

/// application/x-www-form-urlencoded value.
fn pct_decode(s: &str) -> String {
    let b = s.as_bytes();
    let mut out = Vec::with_capacity(b.len());
    let mut i = 0;
    while i < b.len() {
        match b[i] {
            b'+' => {
                out.push(b' ');
                i += 1;
            }
            b'%' => match (b.get(i + 1).copied().and_then(hex_digit), b.get(i + 2).copied().and_then(hex_digit)) {
                (Some(h), Some(l)) => {
                    out.push(h << 4 | l);
                    i += 3;
                }
                _ => {
                    out.push(b'%');
                    i += 1;
                }
            },
            c => {
                out.push(c);
                i += 1;
            }
        }
    }
    String::from_utf8_lossy(&out).into_owned()
}

fn form_pairs(body: &[u8]) -> Vec<(String, String)> {
    String::from_utf8_lossy(body)
        .split('&')
        .filter(|p| !p.is_empty())
        .map(|p| {
            let (k, v) = p.split_once('=').unwrap_or((p, ""));
            (pct_decode(k), pct_decode(v))
        })
        .collect()
}

/// Save settings sent by the page (only the fields that changed are sent), then restart what
/// uses them: librespot for its options, the agent for its own. New librespot options are on
/// trial: when librespot does not keep running with them, the agent puts the last good ones back.
fn save_settings(req: &Request) -> Response {
    let _lock = settings::lock();
    let Some((mut s, _)) = settings::load() else {
        return Response::error(409, "this speaker gets its settings from the computer until its next update (lithify update)");
    };
    let mut changed: Vec<&'static str> = Vec::new();
    let mut errors = Obj::new();
    let mut bad = false;
    for (k, v) in form_pairs(&req.body) {
        let Some(o) = settings::opt(&k) else {
            errors = errors.str(&k, "unknown setting");
            bad = true;
            continue;
        };
        match s.set(o.key, &v) {
            Ok(true) => changed.push(o.key),
            Ok(false) => {}
            Err(e) => {
                errors = errors.str(o.key, &e);
                bad = true;
            }
        }
    }
    for (k, why) in s.conflicts() {
        if changed.contains(&k) {
            errors = errors.str(k, &why);
            bad = true;
        }
    }
    if bad {
        return Response::json(400, Obj::new().str("error", "some values are not valid").raw("errors", errors.end()).end());
    }
    let mut restart: Vec<&'static str> = Vec::new();
    for k in &changed {
        if let Some(o) = settings::opt(k) {
            if !restart.contains(&o.restart) {
                restart.push(o.restart);
            }
        }
    }
    if restart.contains(&"agent") {
        if let Some(r) = busy() {
            return r;
        }
    }
    if !changed.is_empty() {
        s.updated = utc_now();
        s.by = "page".into();
        let librespot = restart.contains(&"librespot");
        if let Err(e) = settings::save(&s) {
            return Response::error(500, &format!("cannot save the settings: {e}"));
        }
        event(&format!("settings changed on the web page: {}", changed.join(" ")));
        if librespot {
            trial_start(&changed.join(" "));
            let stop = || {
                restart_librespot_now();
            };
            if !spawn_named("restart", move || {
                thread::sleep(Duration::from_millis(300)); // the reply goes out first
                stop();
            }) {
                stop();
            }
        }
        if restart.contains(&"agent") {
            exit_soon(Duration::from_millis(1200), "settings changed");
        }
    }
    Response::json(
        200,
        Obj::new()
            .bool("ok", true)
            .raw("changed", arr(changed.iter().map(|k| q(k))))
            .raw("restart", arr(restart.iter().map(|r| q(r))))
            .end(),
    )
}

struct Job {
    kind: String,
    running: bool,
    ok: Option<bool>,
    installed: bool,
    started: String,
    finished: String,
    output: String,
}

static JOB: Mutex<Job> = Mutex::new(Job {
    kind: String::new(),
    running: false,
    ok: None,
    installed: false,
    started: String::new(),
    finished: String::new(),
    output: String::new(),
});

/// A job's output in the status while it runs (its end); /api/job gives all that is kept.
const JOB_OUTPUT_LIVE: usize = 8000;
/// The output kept: past the high mark, only the last part.
const JOB_OUTPUT_HIGH: usize = 32 * 1024;
const JOB_OUTPUT_KEEP: usize = 16 * 1024;

/// The running or last job; after an update restarted the agent, the one it saved before exiting.
fn job() -> MutexGuard<'static, Job> {
    static RESTORE: Once = Once::new();
    RESTORE.call_once(|| {
        if let Some(saved) = fs::read_to_string(JOB_FILE).ok().and_then(|t| job_from_saved(&t)) {
            let mut j = JOB.lock().unwrap_or_else(|e| e.into_inner());
            if j.kind.is_empty() {
                *j = saved;
            }
        }
    });
    JOB.lock().unwrap_or_else(|e| e.into_inner())
}

/// A job as `render_job` saved it.
fn job_from_saved(text: &str) -> Option<Job> {
    if !text.starts_with('{') {
        return None;
    }
    Some(Job {
        kind: json_field(text, "kind").filter(|k| !k.is_empty())?,
        running: false, // saved when it ended
        ok: match json_field(text, "ok").as_deref() {
            Some("true") => Some(true),
            Some("false") => Some(false),
            _ => None,
        },
        installed: json_field(text, "installed").as_deref() == Some("true"),
        started: json_field(text, "started").unwrap_or_default(),
        finished: json_field(text, "finished").unwrap_or_default(),
        output: json_field(text, "output").unwrap_or_default(),
    })
}

/// The job as JSON with at most `output` bytes of its output (its end); none at all with 0.
fn render_job(j: &Job, output: usize) -> String {
    let o = Obj::new()
        .str("kind", &j.kind)
        .bool("running", j.running)
        .raw("ok", j.ok.map_or("null", |ok| if ok { "true" } else { "false" }))
        .bool("installed", j.installed)
        .str("started", &j.started)
        .str("finished", &j.finished);
    if output == 0 { o } else { o.str("output", tail_chars(&j.output, output)) }.end()
}

/// /api/job: the running or last job with all its output that is kept.
fn job_json() -> String {
    let j = job();
    if j.kind.is_empty() {
        return "null".into();
    }
    render_job(&j, JOB_OUTPUT_HIGH)
}

/// The job in the page's status: its output only while it runs (the page asks /api/job for it
/// once the job has finished), so a status repeated every few seconds stays small.
fn job_status_json() -> String {
    let j = job();
    if j.kind.is_empty() {
        return "null".into();
    }
    render_job(&j, if j.running { JOB_OUTPUT_LIVE } else { 0 })
}

fn job_progress(chunk: &str) {
    let mut j = job();
    let mut chunk = chunk.replace('\r', "");
    // The console's prompt precedes the first output of a script.
    if (j.output.is_empty() || j.output.ends_with('\n')) && chunk.starts_with('>') {
        chunk.remove(0);
    }
    j.output.push_str(&chunk);
    if j.output.len() > JOB_OUTPUT_HIGH {
        j.output = tail_chars(&j.output, JOB_OUTPUT_KEEP).to_string();
    }
}

/// Run `work` in the background. When it succeeds and says so, librespot and the agent are
/// restarted to use the new files (the Cast process manager starts both again).
fn start_job(kind: &str, work: impl FnOnce() -> Result<(String, bool), String> + Send + 'static) -> Response {
    {
        let mut j = job();
        if j.running {
            return Response::error(409, &format!("{} is already running", j.kind));
        }
        *j = Job {
            kind: kind.to_string(),
            running: true,
            ok: None,
            installed: false,
            started: utc_now(),
            finished: String::new(),
            output: String::new(),
        };
    }
    let kind_owned = kind.to_string();
    let spawned = spawn_named("job", move || {
        let result = work();
        let ok = result.is_ok();
        let restart = matches!(result, Ok((_, true)));
        let text = {
            let mut j = job();
            j.running = false;
            j.ok = Some(ok);
            j.installed = restart;
            j.finished = utc_now();
            match result {
                Ok((msg, _)) | Err(msg) => j.output.push_str(&msg),
            }
            render_job(&j, JOB_OUTPUT_HIGH)
        };
        // Whole or not at all: the page reads it while the agent restarts.
        write_state(JOB_FILE, &text);
        // A new version gets watched for its first minutes (see the agent's crash watch).
        if ok && restart && kind_owned != "rollback" {
            write_state(INSTALLED_FILE, &format!("{:.0}\n", system_uptime().unwrap_or(0.0)));
        } else if ok && restart {
            let _ = fs::remove_file(INSTALLED_FILE);
        }
        // What the page shows and the last update check are out of date now.
        heavy_forget(UPDATE_CHECK);
        status_changed();
        event(&format!("{kind_owned} {}", if ok { "finished" } else { "FAILED" }));
        if ok && restart {
            thread::sleep(Duration::from_secs(3)); // let the page see the result first
            restart_librespot_now();
            exit_soon(Duration::from_millis(200), "new version installed");
        }
    });
    if !spawned {
        job().running = false;
        return Response::error(500, "could not start a thread");
    }
    Response::json(202, Obj::new().bool("started", true).str("kind", kind).end())
}

fn valid_id(id: &str) -> bool {
    !id.is_empty() && id.chars().all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '-')
}

/// SHA-256 of the installed files, from the bundle's SHA256SUMS kept by install.sh.
fn installed_sums() -> Vec<(String, String)> {
    fs::read_to_string(base_dir().join("SHA256SUMS"))
        .unwrap_or_default()
        .lines()
        .filter_map(|l| l.split_once("  ").map(|(sum, f)| (f.trim().to_string(), sum.trim().to_string())))
        .collect()
}

/// Does the companion's staged bundle (its /api/latest reply) carry other binaries than the
/// installed ones? The companion names the files that make a version ("binaries") and gives
/// their checksums ("sha_<file>"); a reply without them counts as new.
fn bundle_differs(latest: &str, installed: &[(String, String)]) -> bool {
    let Some(names) = json_field(latest, "binaries").filter(|n| !n.trim().is_empty()) else { return true };
    !names.split_whitespace().all(|f| {
        let staged = json_field(latest, &format!("sha_{f}"));
        staged.is_some() && installed.iter().any(|(name, sum)| name == f && Some(sum) == staged.as_ref())
    })
}

fn bundle_is_new(latest: &str) -> bool {
    bundle_differs(latest, &installed_sums())
}

/// Download this speaker's staged files from the companion and install them with install.sh.
fn install_from_companion(base: &str, id: &str) -> Result<String, String> {
    let latest = format!("{base}/api/latest?speaker={id}");
    match http_call("GET", &latest, Duration::from_secs(60)) {
        Ok((200, body)) => job_progress(&format!(
            "companion {base}: lithify {}, librespot {}, built {}\n",
            json_field(&body, "lithify").unwrap_or_default(),
            json_field(&body, "librespot").unwrap_or_default(),
            json_field(&body, "built").unwrap_or_default()
        )),
        Ok((code, body)) => return Err(format!("companion answered {code}: {}", json_field(&body, "error").unwrap_or(body))),
        Err(e) => return Err(format!("companion {base} not reachable: {e}")),
    }
    let script = format!("LITHIFY_BASE={}\nset -- {base}/bundle/{id}\n{}", base_dir().display(), lf(INSTALL_SH));
    // Read to the script's very end, so a failed install has cleaned up before the session closes.
    let out = root_run_with(&script, Duration::from_secs(90), Duration::from_secs(900), &mut |c| job_progress(c))
        .map_err(|e| format!("root console: {e}"))?;
    if out.contains("INSTALL_OK") {
        Ok("\ninstalled: restarting librespot and the agent\n".into())
    } else {
        Err("\ninstall failed: the current version keeps running\n".into())
    }
}

fn start_install(cfg: &Config) -> Response {
    let Some(base) = companion_base(cfg) else { return Response::error(503, NO_COMPANION) };
    let id = cfg.speaker_id.clone();
    if !valid_id(&id) {
        return Response::error(500, "this install has no speaker id: run `lithify update` on the computer once");
    }
    start_job("install", move || install_from_companion(&base, &id).map(|m| (m, true)))
}

/// What "update everything" does first, as the companion's reply says: download the newest
/// Lithify release (versions.toml names one), or build the newest stable versions.
fn update_step(reply: &str) -> (&'static str, &'static str) {
    if json_field(reply, "source").as_deref() == Some("release") {
        ("1/2 downloading the newest Lithify release on the computer\n", "the download failed")
    } else {
        ("1/2 building the newest stable versions on the computer (librespot, Rust, alsa-lib, libraries)\n", "the build failed")
    }
}

/// Get the newest versions on the computer (a release downloaded, or a build), then install them
/// when they differ from what the speaker runs.
fn start_update_all(cfg: &Config) -> Response {
    let Some(base) = companion_base(cfg) else { return Response::error(503, NO_COMPANION) };
    let id = cfg.speaker_id.clone();
    if !valid_id(&id) {
        return Response::error(500, "this install has no speaker id: run `lithify update` on the computer once");
    }
    start_job("update", move || {
        let (step, failed) = match http_call("POST", &format!("{base}/api/build?latest=1"), Duration::from_secs(20)) {
            Ok((200 | 202, body)) => update_step(&body),
            Ok((code, body)) => {
                return Err(format!("the computer refused the update ({code}): {}", json_field(&body, "error").unwrap_or(body)))
            }
            Err(e) => return Err(format!("companion {base} not reachable: {e}")),
        };
        job_progress(step);
        let started = Instant::now();
        let mut last = String::new();
        loop {
            thread::sleep(Duration::from_secs(5));
            if started.elapsed() > Duration::from_secs(90 * 60) {
                return Err("\nthe build took longer than 90 minutes\n".into());
            }
            let Ok((200, body)) = http_call("GET", &format!("{base}/api/build/status"), Duration::from_secs(15)) else {
                continue; // the companion may be restarting after updating itself
            };
            // The build's step ("compiling librespot …"); a companion without steps gives its log.
            let line = json_field(&body, "phase")
                .filter(|p| !p.trim().is_empty())
                .or_else(|| {
                    json_field(&body, "log_tail")
                        .and_then(|t| t.lines().rev().find(|l| !l.trim().is_empty()).map(str::to_string))
                })
                .unwrap_or_default();
            if !line.is_empty() && line != last {
                job_progress(&format!("  {}\n", tail_chars(&line, 160)));
                last = line;
            }
            if json_field(&body, "running").as_deref() == Some("true") {
                continue;
            }
            match json_field(&body, "ok").as_deref() {
                Some("true") => break,
                Some("false") => {
                    return Err(format!("\n{failed}: {}\n", json_field(&body, "error").unwrap_or_default()));
                }
                _ => continue,
            }
        }
        job_progress("2/2 comparing with what the speaker runs\n");
        let latest = match http_call("GET", &format!("{base}/api/latest?speaker={id}"), Duration::from_secs(60)) {
            Ok((200, body)) => body,
            Ok((code, body)) => return Err(format!("companion answered {code}: {}", json_field(&body, "error").unwrap_or(body))),
            Err(e) => return Err(format!("companion {base} not reachable: {e}")),
        };
        if !bundle_is_new(&latest) {
            return Ok(("\neverything is up to date: the speaker already runs this build\n".into(), false));
        }
        install_from_companion(&base, &id).map(|m| (m, true))
    })
}

/// Roll back without a web request (the agent's crash watch).
pub(crate) fn rollback_now() {
    let _ = start_rollback();
}

fn start_rollback() -> Response {
    let base = base_dir();
    if fs::metadata(base.join("prev").join("librespot")).is_err() {
        return Response::error(409, "there is no previous version to roll back to");
    }
    let install_base = base.display().to_string();
    start_job("rollback", move || {
        let script = format!("LITHIFY_BASE={install_base}\nset -- version\n{}", lf(ROLLBACK_SH));
        let out = root_run_with(&script, Duration::from_secs(20), Duration::from_secs(120), &mut |c| job_progress(c))
            .map_err(|e| format!("root console: {e}"))?;
        if out.contains("VERSION_ROLLED_BACK") {
            Ok(("\nrolled back: restarting librespot and the agent\n".into(), true))
        } else {
            Err("\nrollback failed\n".into())
        }
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::mpsc;

    #[test]
    fn host_guard_accepts_ips_and_local_names_only() {
        assert!(host_ok("192.168.1.40:8090"));
        assert!(host_ok("192.168.1.40"));
        assert!(host_ok("[fe80::1]:8090"));
        assert!(host_ok("lazienka:8090"));
        assert!(host_ok("lazienka.local:8090"));
        assert!(host_ok("speaker.home.arpa"));
        assert!(!host_ok("evil.example.com:8090"));
        assert!(!host_ok("192.168.1.40.nip.io:8090"));
        assert!(!host_ok(""));
        assert!(!host_ok("bad host"));
    }

    #[test]
    fn json_strings_are_escaped() {
        assert_eq!(q("a\"b\\c\n<"), "\"a\\\"b\\\\c\\n\\u003c\"");
        assert_eq!(q("Łazienka"), "\"Łazienka\"");
        assert_eq!(Obj::new().str("a", "x").num("b", 2).bool("c", true).opt("d", None).end(), r#"{"a":"x","b":2,"c":true,"d":null}"#);
    }

    #[test]
    fn update_everything_says_whether_it_downloads_or_builds() {
        let (step, failed) = update_step(r#"{"started":true,"latest":true,"source":"release"}"#);
        assert!(step.contains("downloading the newest Lithify release"), "{step}");
        assert_eq!(failed, "the download failed");
        // a companion without releases, or one from before them (no "source")
        for reply in [r#"{"started":true,"latest":true,"source":"build"}"#, r#"{"started":true,"latest":true}"#] {
            let (step, failed) = update_step(reply);
            assert!(step.starts_with("1/2 building the newest stable versions"), "{step}");
            assert_eq!(failed, "the build failed");
        }
    }

    #[test]
    fn json_fields_are_read() {
        let t = r#"{"status":101,"remoteName":"Łazienka (librespot)","activeUser":"","resolverVersion": "0","x":[1]}"#;
        assert_eq!(json_field(t, "remoteName").as_deref(), Some("Łazienka (librespot)"));
        assert_eq!(json_field(t, "activeUser").as_deref(), Some(""));
        assert_eq!(json_field(t, "status").as_deref(), Some("101"));
        assert_eq!(json_field(t, "resolverVersion").as_deref(), Some("0"));
        assert_eq!(json_field(t, "missing"), None);
        assert_eq!(json_field(r#"{"versions":{"librespot":"v0.8.0-5"}}"#, "librespot").as_deref(), Some("v0.8.0-5"));
    }

    #[test]
    fn iwconfig_and_wireless_are_parsed() {
        let iw = "wlan0     IEEE 802.11-DS  ESSID:\"HomeNet\" [2]\n          Mode:Managed  Frequency=2.422 GHz  Access Point: 02:00:5E:10:00:01\n          Bit Rate:72 Mb/s   Tx-Power=18 dBm";
        assert_eq!(parse_iwconfig(iw), (Some("HomeNet".into()), Some(2422), Some(72.0)));
        let w = "Inter-| sta-|   Quality        |\n face | tus | link level noise |\n wlan0: 0002    4.  -53.  -98.       0      0 330547\n wlan1: 0002    0.  -256.  -256.";
        assert_eq!(parse_wireless(w, "wlan0"), Some((-53, -98)));
        assert_eq!(parse_wireless(w, "eth0"), None);
    }

    #[test]
    fn urls_for_scripts_are_plain() {
        assert!(safe_url("http://192.168.1.110:8095/bundle/lazienka"));
        assert!(!safe_url("http://x/$(reboot)"));
        assert!(!safe_url("http://x/a b"));
        assert!(!safe_url("https://example.com/"));
    }

    #[test]
    fn chunked_replies_are_decoded() {
        assert_eq!(dechunk(b"4\r\nWiki\r\n5;x=1\r\npedia\r\n0\r\n\r\n").as_deref(), Some(&b"Wikipedia"[..]));
        assert_eq!(dechunk(b"4\r\nWi"), None);
        assert!(complete(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n2\r\nok\r\n0\r\n\r\n"));
        assert!(!complete(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n2\r\nok\r\n"));
    }

    #[test]
    fn replies_are_complete_by_length() {
        assert!(complete(b"HTTP/1.0 200 OK\r\nContent-Length: 2\r\n\r\nok"));
        assert!(!complete(b"HTTP/1.0 200 OK\r\nContent-Length: 3\r\n\r\nok"));
        assert!(!complete(b"HTTP/1.0 200 OK\r\n\r\nok"));
    }

    #[test]
    fn logs_hide_the_spotify_account() {
        let t = redact(
            "10-07 13:21:10.002 I/lithify-agent( 3061): [2026-10-07T11:21:10Z INFO  librespot_core::session] Authenticated as 'alice.k' !\n\
             x Authenticated as \"Łukasz\" !\n\
             x Authenticated as abcdef\n\
             Authenticated as '' !\n\
             Authenticated as 'unterminated",
        );
        let l: Vec<&str> = t.lines().collect();
        assert!(l[0].ends_with("] Authenticated as 'al***' !"), "{}", l[0]);
        assert_eq!(l[1], "x Authenticated as \"Łu***\" !");
        assert_eq!(l[2], "x Authenticated as ab***");
        assert_eq!(l[3], "Authenticated as '***' !");
        assert_eq!(l[4], "Authenticated as 'un***");
        assert!(!t.contains("alice") && !t.contains("ukasz") && !t.contains("cdef") && !t.contains("terminated"));
    }

    #[test]
    fn logs_hide_the_official_login() {
        let t = redact("10-07 13:21:10.002 E/SPOTIFY ( 1612): USERNAME = 123 :::: BLOB = SECRETvalue1    WE ARE SENDING\n\
                        10-07 13:21:10.867 E/SPOTIFY ( 1612): blob = SECRETvalue2\n\
                        10-07 13:21:10.867 E/SPOTIFY ( 1612): Łazienka STORE THE BLOB SECRETvalue3\n\
                        10-07 13:21:11.871 E/SPOTIFY ( 1612): ---SPOTIFY PLAYBACKEVENT = PlaybackStopped");
        assert!(!t.contains("SECRET"));
        assert_eq!(t.lines().count(), 4);
        assert!(t.ends_with("PlaybackStopped"));
    }

    #[test]
    fn log_stats_count_librespot_problems() {
        let mut s = LogStats::default();
        s.add("10-06 19:11:45.697 I/lithify-agent( 3979): [2026-10-06T17:11:45Z WARN  librespot_playback::audio_backend::alsa] Error writing from AlsaSink buffer to PCM, trying to recover, ALSA function 'snd_pcm_writei' failed with error 'Broken pipe (32)'");
        s.add("10-06 19:11:46.000 I/lithify-agent( 3979): [2026-10-06T17:11:46Z INFO  librespot_core::session] Authenticated as \"user\" !");
        s.add("10-06 19:11:47.000 I/lithify-agent( 3980): 2026-10-06 17:11:47Z lithify-agent[3983] librespot initial volume pinned");
        assert_eq!((s.underruns, s.warnings, s.errors, s.sessions, s.recent.len()), (1, 1, 0, 1, 1));
        // A CDN address the fail-fast routes block, and librespot 0.8's stop note: expected.
        s.add("10-07 11:40:18.101 I/lithify-agent( 3061): [2026-10-07T09:40:18Z WARN  librespot_audio::fetch] Fetching https://XXXX failed with error Error { kind: Unavailable, error: hyper_util::client::legacy::Error(Connect, Custom { kind: Other, error: ConnectError(\"tcp connect error\", 199.232.210.248:443, Os { code: 101, kind: NetworkUnreachable, message: \"Network unreachable\" }) }) }, trying next");
        s.add("10-07 11:55:01.073 I/lithify-agent( 3061): [2026-10-07T09:55:01Z WARN  librespot_connect::spirc] failed filling up next_track during stopping: Invalid state { context is not available. type: Default }");
        assert_eq!((s.warnings, s.expected, s.recent.len()), (1, 2, 1));
        // the device running dry as the next track starts is the switch; seconds later, a problem
        s.add("10-07 17:53:59.184 I/lithify-agent( 1133): [2026-10-07T15:53:59Z INFO  librespot_playback::player] <\"Episode\" | Guest> (4355297 ms) loaded");
        let dry = "Error writing from AlsaSink buffer to PCM, trying to recover, ALSA function 'snd_pcm_writei' failed with error 'Broken pipe (32)'";
        s.add(&format!("10-07 17:53:59.189 I/lithify-agent( 1133): [2026-10-07T15:53:59Z WARN  librespot_playback::audio_backend::alsa] {dry}"));
        assert_eq!((s.underruns, s.expected), (1, 3));
        s.add(&format!("10-07 17:54:09.000 I/lithify-agent( 1133): [2026-10-07T15:54:09Z WARN  librespot_playback::audio_backend::alsa] {dry}"));
        assert_eq!((s.underruns, s.expected), (2, 3));
        assert_eq!(logcat_ms("10-07 17:53:59.189 I/x"), Some(((17 * 60 + 53) * 60 + 59) * 1000 + 189));
        // the replaced session's dealer closing right after a new login: expected; later, a warning
        s.add("10-07 17:37:51.771 I/lithify-agent( 1133): [2026-10-07T15:37:51Z INFO  librespot_core::session] Authenticated as '11***' !");
        let dealer = "WARN  librespot_core::dealer] Websocket connection failed: IO error: peer closed connection without sending TLS close_notify";
        s.add(&format!("10-07 17:37:51.984 I/lithify-agent( 1133): [2026-10-07T15:37:51Z {dealer}"));
        assert_eq!((s.warnings, s.expected), (2, 4));
        s.add(&format!("10-07 18:37:51.984 I/lithify-agent( 1133): [2026-10-07T16:37:51Z {dealer}"));
        assert_eq!((s.warnings, s.expected), (3, 4));
        assert_eq!(logcat_ms("short"), None);
    }

    #[test]
    fn tails_cut_at_char_boundaries() {
        assert_eq!(tail_chars("abc", 10), "abc");
        assert_eq!(tail_chars("aŁb", 2), "b");
        assert_eq!(tail_chars("aŁb", 3), "Łb");
    }

    #[test]
    fn connections_are_capped_per_address() {
        let ip: IpAddr = "192.0.2.7".parse().unwrap();
        for _ in 0..MAX_PER_PEER {
            assert!(peer_enter(ip));
        }
        assert!(!peer_enter(ip));
        peer_leave(ip);
        assert!(peer_enter(ip));
        for _ in 0..MAX_PER_PEER {
            peer_leave(ip);
        }
        assert!(PEERS.lock().unwrap().iter().all(|(a, _)| *a != ip));
    }

    #[test]
    fn mdns_answers_give_their_names() {
        // an answer as devices send it: AirPlay's PTR, then a Cast TXT record whose name points
        // back into the first one (DNS name compression)
        let mut p = vec![0x12, 0x34, 0x84, 0x00, 0, 0, 0, 2, 0, 0, 0, 0];
        let raop = p.len();
        for l in ["_raop", "_tcp", "local"] {
            p.push(l.len() as u8);
            p.extend_from_slice(l.as_bytes());
        }
        p.push(0);
        let inst = "AABBCCDDEEFF@Łazienka".as_bytes();
        p.extend_from_slice(&[0, 12, 0, 1, 0, 0, 0, 120, 0, (inst.len() + 3) as u8, inst.len() as u8]);
        p.extend_from_slice(inst);
        p.extend_from_slice(&[0xC0, raop as u8]);
        let cast = b"Speaker-1234";
        p.push(cast.len() as u8);
        p.extend_from_slice(cast);
        p.push(11);
        p.extend_from_slice(b"_googlecast");
        p.extend_from_slice(&[0xC0, (raop + 6) as u8]); // "_tcp.local" of the first name
        let txt: Vec<u8> = [&b"md=Model"[..], "fn=Łazienka".as_bytes()]
            .iter()
            .flat_map(|kv| std::iter::once(kv.len() as u8).chain(kv.iter().copied()))
            .collect();
        p.extend_from_slice(&[0, 16, 0, 1, 0, 0, 0, 120, 0, txt.len() as u8]);
        p.extend_from_slice(&txt);
        assert_eq!(mdns_names(&p), vec![("AirPlay", "Łazienka".to_string()), ("Google Cast", "Łazienka".to_string())]);
        // a pointer loop ends instead of recursing forever; cut packets give nothing
        assert!(dns_name(&[0xC0, 0x00], 0, 0).is_none());
        assert!(mdns_names(&p[..20]).is_empty());
        let q = mdns_query(7, &["_raop._tcp.local", "_airplay._tcp.local"]);
        assert_eq!((&q[..2], u16::from_be_bytes([q[4], q[5]])), (&[0u8, 7][..], 2));
        assert_eq!(dns_name(&q, 12, 0).map(|(n, _)| n).as_deref(), Some("_raop._tcp.local"));
    }

    /// A request as `read_request` makes it: `target` may carry a query.
    fn request(method: &str, target: &str, headers: &[(&str, &str)]) -> Request {
        let (path, query) = target.split_once('?').unwrap_or((target, ""));
        let mut h = vec![("host".to_string(), "192.168.1.40:8090".to_string())];
        h.extend(headers.iter().map(|(k, v)| (k.to_string(), v.to_string())));
        Request {
            peer: "192.168.1.110".parse().unwrap(),
            method: method.into(),
            path: path.into(),
            query: query_pairs(query),
            headers: h,
            body: Vec::new(),
        }
    }

    fn get(cfg: &Config, target: &str, headers: &[(&str, &str)]) -> Response {
        route(&request("GET", target, headers), cfg)
    }

    fn text_of(r: &Response) -> String {
        String::from_utf8(r.body.bytes().to_vec()).unwrap()
    }

    /// Tests that use the shared caches, the costly-request places or a POST (which renews the
    /// status) run one at a time.
    static SERIAL: Mutex<()> = Mutex::new(());

    fn serial() -> MutexGuard<'static, ()> {
        SERIAL.lock().unwrap_or_else(|e| e.into_inner())
    }

    #[test]
    fn icons_and_manifest_are_served() {
        let cfg = Config::default_for_tests("");
        let ico = get(&cfg, "/favicon.ico", &[]);
        assert_eq!((ico.code, ico.ctype), (200, "image/x-icon"));
        assert_eq!(&ico.body.bytes()[..4], &[0, 0, 1, 0]); // an ICO file
        assert_eq!(&get(&cfg, "/icon-512.png", &[]).body.bytes()[1..4], b"PNG");
        assert!(text_of(&get(&cfg, "/logo.svg", &[])).starts_with("<svg"));
        // every file the page and its manifest name is there (the page's own under their version)
        let page = text_of(&get(&cfg, "/", &[]));
        let manifest = text_of(&get(&cfg, "/manifest.webmanifest", &[]));
        let named: Vec<&str> = page.split("href=\"").skip(1)
            .chain(page.split("<script src=\"").skip(1))
            .chain(manifest.split("\"src\": \"").skip(1))
            .map(|s| s.split('"').next().unwrap_or(""))
            .collect();
        assert!(named.len() >= 8, "{named:?}");
        for path in named {
            assert_eq!(get(&cfg, path, &[]).code, 200, "{path}");
        }
    }

    #[test]
    fn page_files_are_versioned_and_revalidated() {
        let cfg = Config::default_for_tests("");
        let v = ui_version();
        assert_eq!(v.len(), 16);
        let page = get(&cfg, "/", &[]);
        assert_eq!((page.code, page.cache, page.etag, page.csp), (200, "no-cache", Some(v), true));
        let html = text_of(&page);
        for file in ["/app.js", "/boot.js", "/style.css"] {
            assert!(html.contains(&format!("\"{file}?v={v}\"")), "{file} is not versioned");
            assert!(!html.contains(&format!("\"{file}\"")), "{file} is named without its version");
        }
        // asked again: 304 while nothing changed (also in a list, also weak), else the page
        let again = get(&cfg, "/", &[("if-none-match", &format!("\"{v}\""))]);
        assert_eq!((again.code, again.body.bytes().len()), (304, 0));
        assert_eq!(get(&cfg, "/", &[("if-none-match", &format!("W/\"old\", W/\"{v}\""))]).code, 304);
        assert_eq!(get(&cfg, "/", &[("if-none-match", "\"0123456789abcdef\"")]).code, 200);
        // the current version is kept for good; an old one or none is asked again
        let js = get(&cfg, &format!("/app.js?v={v}"), &[]);
        assert_eq!((js.code, js.cache, js.etag), (200, IMMUTABLE, None));
        let old = get(&cfg, "/app.js?v=0123", &[]);
        assert_eq!((old.code, old.cache, old.etag), (200, "no-cache", Some(v)));
        assert_eq!(get(&cfg, "/boot.js", &[("if-none-match", &format!("\"{v}\""))]).code, 304);
        let css = get(&cfg, &format!("/style.css?v={v}"), &[]);
        assert_eq!((css.code, css.ctype, css.cache), (200, "text/css; charset=utf-8", IMMUTABLE));
        assert_eq!(get(&cfg, "/icon-192.png", &[]).cache, A_DAY);
        assert_eq!(get(&cfg, "/manifest.webmanifest", &[]).cache, A_DAY);
        assert_eq!(get(&cfg, "/api/settings", &[]).cache, "no-store");
        // what goes on the wire
        let head = response_head(&again);
        assert!(head.starts_with("HTTP/1.1 304 Not Modified\r\nDate: "), "{head}");
        assert!(head.contains(&format!("Cache-Control: no-cache\r\nETag: \"{v}\"\r\n")), "{head}");
        assert!(!head.contains("\r\nContent-Length:") && !head.contains("\r\nContent-Type:"), "{head}");
        let head = response_head(&page);
        assert!(head.contains(&format!("Content-Length: {}\r\n", html.len())));
        assert!(head.contains(
            "Content-Security-Policy: default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; \
             img-src 'self'; manifest-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'; \
             require-trusted-types-for 'script'; trusted-types 'none'\r\n"
        ));
        assert!(!response_head(&js).contains("Content-Security-Policy"));
    }

    #[test]
    fn hashes_tell_versions_apart() {
        assert_eq!(fnv1a64(&[]), 0xcbf2_9ce4_8422_2325); // FNV-1a's offset basis
        assert_ne!(fnv1a64(&[b"a"]), fnv1a64(&[b"b"]));
        assert_ne!(fnv1a64(&[b"ab", b"c"]), fnv1a64(&[b"a", b"bc"])); // files end where they end
        assert!(etag_matches("*", "x"));
        assert!(!etag_matches("", "x"));
    }

    #[test]
    fn pin_lockout_grows_and_stays_per_address() {
        assert_eq!(lockout(1), Duration::from_secs(60));
        assert_eq!(lockout(2), Duration::from_secs(120));
        assert_eq!(lockout(20), Duration::from_secs(3600));
        let cfg = Config::default_for_tests("2468");
        let req = |ip: &str, pin: &str| Request {
            peer: ip.parse().unwrap(),
            method: "POST".into(),
            path: "/api/pin".into(),
            query: Vec::new(),
            headers: vec![("x-lithify-pin".into(), pin.into())],
            body: Vec::new(),
        };
        for _ in 0..5 {
            assert_eq!(check_pin(&cfg, &req("192.0.2.10", "0000")).err().map(|r| r.code), Some(401));
        }
        assert_eq!(check_pin(&cfg, &req("192.0.2.10", "2468")).err().map(|r| r.code), Some(429));
        assert!(check_pin(&cfg, &req("192.0.2.11", "2468")).is_ok()); // another device still works
    }

    #[test]
    fn forms_are_decoded() {
        assert_eq!(pct_decode("%C5%81azienka+%28librespot%29"), "Łazienka (librespot)");
        assert_eq!(pct_decode("100%"), "100%");
        assert_eq!(pct_decode("%zz%4"), "%zz%4");
        assert_eq!(
            form_pairs(b"name=Kuchnia&bitrate=160&fastfail_hosts=a.example+b.example&&empty="),
            vec![
                ("name".to_string(), "Kuchnia".to_string()),
                ("bitrate".to_string(), "160".to_string()),
                ("fastfail_hosts".to_string(), "a.example b.example".to_string()),
                ("empty".to_string(), String::new()),
            ]
        );
    }

    #[test]
    fn new_binaries_are_detected() {
        let installed = vec![
            ("librespot".to_string(), "aa".to_string()),
            ("lithify-agent".to_string(), "bb".to_string()),
            ("alsa.tar".to_string(), "cc".to_string()),
            ("VERSIONS".to_string(), "dd".to_string()),
        ];
        let same = r#"{"versions":{"built":"x"},"binaries":"librespot lithify-agent alsa.tar","sha_librespot":"aa","sha_lithify-agent":"bb","sha_alsa.tar":"cc"}"#;
        assert!(!bundle_differs(same, &installed));
        assert!(bundle_differs(&same.replace("\"bb\"", "\"b2\""), &installed));
        assert!(bundle_differs(r#"{"versions":{}}"#, &installed)); // an older companion: assume new
    }

    #[test]
    fn pin_comparison() {
        assert!(same(b"1234", b"1234"));
        assert!(!same(b"1234", b"1235"));
        assert!(!same(b"123", b"1234"));
    }

    /// Send `raw` to `handle` over a real connection; the reply as text.
    fn exchange(raw: Vec<u8>) -> String {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let addr = listener.local_addr().unwrap();
        let client = thread::spawn(move || {
            let mut c = TcpStream::connect(addr).unwrap();
            let _ = c.write_all(&raw); // the server may answer before it has read everything
            let _ = c.shutdown(Shutdown::Write);
            let mut reply = String::new();
            let _ = c.read_to_string(&mut reply);
            reply
        });
        let (s, peer) = listener.accept().unwrap();
        handle(s, &Config::default_for_tests(""), peer.ip());
        client.join().unwrap()
    }

    fn post_with_body(len: usize) -> Vec<u8> {
        let mut raw = format!(
            "POST /api/pin HTTP/1.1\r\nHost: 127.0.0.1:8090\r\nX-Lithify: 1\r\nContent-Length: {len}\r\n\r\n"
        )
        .into_bytes();
        raw.resize(raw.len() + len, b'a');
        raw
    }

    #[test]
    fn the_settings_form_fits_and_more_gets_a_json_413() {
        let _one = serial();
        // 32 host names of 253 characters, URL-encoded, are well within the limit
        let hosts = vec!["a".repeat(253); 32].join("+");
        assert!(format!("fastfail_hosts={hosts}").len() < MAX_BODY);
        let ok = exchange(post_with_body(MAX_BODY));
        assert!(ok.starts_with("HTTP/1.1 200 OK\r\n"), "{ok}");
        let refused = exchange(post_with_body(MAX_BODY + 1));
        assert!(refused.starts_with("HTTP/1.1 413 Content Too Large\r\n"), "{refused}");
        assert!(refused.contains("Content-Type: application/json") && refused.contains("{\"error\":\"request body too large"));
        let garbled = exchange(b"POST /api/pin HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: ten\r\n\r\n".to_vec());
        assert!(garbled.starts_with("HTTP/1.1 400 ") && garbled.contains("\"error\""), "{garbled}");
        let mut huge = b"GET / HTTP/1.1\r\nX-Pad: ".to_vec();
        huge.resize(huge.len() + MAX_HEAD + 100, b'p');
        let refused = exchange(huge);
        assert!(refused.starts_with("HTTP/1.1 431 ") && refused.contains("\"error\""), "{refused}");
    }

    #[test]
    fn cached_values_are_made_by_one_caller_at_a_time() {
        let key = "test-single-flight";
        assert_eq!(cached(key, Duration::from_secs(60), || Some("a".into())).as_deref(), Some("a"));
        assert_eq!(cached(key, Duration::from_secs(60), || Some("b".into())).as_deref(), Some("a")); // still fresh
        // due again, and being made by another caller: the last value at once, made only once
        let (started_tx, started_rx) = mpsc::channel();
        let (go_tx, go_rx) = mpsc::channel::<()>();
        let maker = thread::spawn(move || {
            cached(key, Duration::ZERO, move || {
                started_tx.send(()).unwrap();
                go_rx.recv().unwrap();
                Some("c".into())
            })
        });
        started_rx.recv().unwrap();
        let mut asked = false;
        assert_eq!(cached(key, Duration::ZERO, || { asked = true; None }).as_deref(), Some("a"));
        assert!(!asked);
        go_tx.send(()).unwrap();
        assert_eq!(maker.join().unwrap().as_deref(), Some("c"));
        assert_eq!(cached(key, Duration::from_secs(60), || None).as_deref(), Some("c"));
    }

    #[test]
    fn slow_parts_are_renewed_in_the_background() {
        static MADE: AtomicUsize = AtomicUsize::new(0);
        let key = "test-stale-while-renewing";
        // the very first value is made by the request itself
        assert_eq!(cached_swr(key, Duration::from_secs(60), || Some("1".into())).as_deref(), Some("1"));
        let (go_tx, go_rx) = mpsc::channel::<()>();
        // due: the old value at once, the new one on a thread of its own
        let renew = move || {
            MADE.fetch_add(1, Ordering::SeqCst);
            let _ = go_rx.recv();
            Some("2".to_string())
        };
        assert_eq!(cached_swr(key, Duration::ZERO, renew).as_deref(), Some("1"));
        // while that runs nobody starts another one
        let again = || {
            MADE.fetch_add(1, Ordering::SeqCst);
            Some("3".to_string())
        };
        assert_eq!(cached_swr(key, Duration::ZERO, again).as_deref(), Some("1"));
        go_tx.send(()).unwrap();
        let deadline = Instant::now() + Duration::from_secs(5);
        while cached_swr(key, Duration::from_secs(60), || None).as_deref() != Some("2") {
            assert!(Instant::now() < deadline, "the new value never came");
            thread::sleep(Duration::from_millis(10));
        }
        assert_eq!(MADE.load(Ordering::SeqCst), 1);
    }

    fn costly(key: &str, gap: Option<Duration>, made: &'static AtomicUsize, code: u16) -> Response {
        heavy(key, gap, Duration::from_secs(5), || {
            made.fetch_add(1, Ordering::SeqCst);
            thread::sleep(Duration::from_millis(150));
            keep_for(Duration::from_secs(60), Response::json(code, format!("{{\"n\":{}}}", made.load(Ordering::SeqCst))))
        })
    }

    #[test]
    fn costly_answers_are_made_once_and_kept() {
        static MADE: AtomicUsize = AtomicUsize::new(0);
        let _one = serial();
        // two at once: one makes it, the other waits for that one
        let other = thread::spawn(|| text_of(&costly("test-heavy", None, &MADE, 200)));
        let mine = text_of(&costly("test-heavy", None, &MADE, 200));
        assert_eq!((mine.as_str(), other.join().unwrap().as_str()), ("{\"n\":1}", "{\"n\":1}"));
        assert_eq!(text_of(&costly("test-heavy", None, &MADE, 200)), "{\"n\":1}"); // kept
        heavy_forget("test-heavy");
        assert_eq!(text_of(&costly("test-heavy", None, &MADE, 200)), "{\"n\":2}");
        // errors are kept a few seconds only
        assert_eq!(keep_for(Duration::from_secs(60), Response::error(502, "x")).1, HEAVY_ERROR_TTL);
        assert_eq!(keep_for(Duration::from_secs(60), Response::done("x")).1, Duration::from_secs(60));
    }

    #[test]
    fn fresh_checks_run_at_most_once_in_their_gap() {
        static MADE: AtomicUsize = AtomicUsize::new(0);
        let _one = serial();
        let gap = Some(Duration::from_secs(20));
        assert_eq!(text_of(&costly("test-fresh", None, &MADE, 200)), "{\"n\":1}");
        // a fresh one does not take the kept answer of an ordinary one...
        assert_eq!(text_of(&costly("test-fresh", gap, &MADE, 200)), "{\"n\":2}");
        // ...but within its gap it takes its own, and ordinary ones take it too
        assert_eq!(text_of(&costly("test-fresh", gap, &MADE, 200)), "{\"n\":2}");
        assert_eq!(text_of(&costly("test-fresh", None, &MADE, 200)), "{\"n\":2}");
        assert_eq!(MADE.load(Ordering::SeqCst), 2);
    }

    #[test]
    fn a_fresh_check_waited_for_is_not_run_again() {
        static MADE: AtomicUsize = AtomicUsize::new(0);
        let _one = serial();
        let gap = Some(Duration::from_millis(100));
        let check = move || {
            heavy("test-fresh-slow", gap, Duration::from_secs(5), || {
                MADE.fetch_add(1, Ordering::SeqCst);
                thread::sleep(Duration::from_millis(250)); // runs longer than the gap
                keep_for(Duration::from_secs(60), Response::done("checked"))
            })
        };
        let first = thread::spawn(move || check().code);
        thread::sleep(Duration::from_millis(50));
        assert_eq!(check().code, 200); // waited for the first run, and took its answer
        assert_eq!(first.join().unwrap(), 200);
        assert_eq!(MADE.load(Ordering::SeqCst), 1);
        thread::sleep(Duration::from_millis(150)); // the gap has passed since that run ended
        assert_eq!(check().code, 200);
        assert_eq!(MADE.load(Ordering::SeqCst), 2);
    }

    #[test]
    fn a_status_begun_before_a_change_is_not_fresh() {
        let json: Arc<[u8]> = Arc::from(&b"{}"[..]);
        let mut c = StatusCache { at: None, json: None, busy: true, changes: 7 };
        keep_status(&mut c, Arc::clone(&json), 6); // the page changed something meanwhile
        assert!(c.at.is_none() && c.json.is_some());
        keep_status(&mut c, json, 7);
        assert!(c.at.is_some());
    }

    #[test]
    fn log_requests_fall_into_a_few_kinds() {
        let kind = |q: &str| {
            let r = request("GET", &format!("/api/logs?{q}"), &[]);
            (log_source(&r), log_lines_asked(&r))
        };
        assert_eq!(kind("source=agent&n=400"), ("agent", 400));
        assert_eq!(kind("source=official&n=10"), ("official", 400));
        assert_eq!(kind("source=x1&n=401"), ("ours", 1000));
        assert_eq!(kind("n=99999"), ("ours", 3000));
        assert_eq!(kind("n=-5"), ("ours", 400));
    }

    #[test]
    fn at_most_two_costly_answers_are_made_at_once() {
        static MADE: AtomicUsize = AtomicUsize::new(0);
        let _one = serial();
        let (a, b) = (HeavySlot::take().unwrap(), HeavySlot::take().unwrap());
        for _ in 0..3 {
            assert!(HeavySlot::take().is_none()); // a refused one gives no place back
        }
        let r = costly("test-full", None, &MADE, 200);
        assert_eq!(r.code, 503);
        assert!(text_of(&r).starts_with("{\"error\":"));
        drop((a, b));
        assert_eq!(costly("test-full", None, &MADE, 200).code, 200);
        assert_eq!(MADE.load(Ordering::SeqCst), 1);
    }

    #[test]
    fn the_status_is_made_once_for_many_requests() {
        let _one = serial();
        let _helpers = crate::tests::serial(); // the status reads the volume and df through helpers
        let cfg = Config::default_for_tests("");
        let shared_body = |r: &Response| match &r.body {
            Body::Shared(b) => Arc::clone(b),
            _ => panic!("the status is not shared"),
        };
        let (a, b) = (status_reply(&cfg), status_reply(&cfg));
        assert!(Arc::ptr_eq(&shared_body(&a), &shared_body(&b)));
        assert!(page_active());
        status_changed();
        let c = status_reply(&cfg);
        assert!(!Arc::ptr_eq(&shared_body(&a), &shared_body(&c)));
        let text = text_of(&c);
        assert!(text.starts_with("{\"agent\":") && text.contains("\"watchdog\":") && text.contains("\"job\":"));
        assert_eq!(c.ctype, "application/json; charset=utf-8");
    }

    #[test]
    fn jobs_send_their_output_only_while_they_run() {
        let mut j = Job {
            kind: "update".into(),
            running: true,
            ok: None,
            installed: false,
            started: "2026-10-07 13:00:00Z".into(),
            finished: String::new(),
            output: "line <1> \"q\"\n".repeat(2000),
        };
        let live = render_job(&j, JOB_OUTPUT_LIVE);
        // the end of the output only (JSON escapes make it longer than the bytes it holds)
        assert!(live.contains("\"output\":\"") && live.len() < 2 * JOB_OUTPUT_LIVE);
        assert!(!render_job(&j, 0).contains("output"));
        (j.running, j.ok, j.installed, j.finished) = (false, Some(true), true, "2026-10-07 13:02:00Z".into());
        let saved = render_job(&j, JOB_OUTPUT_HIGH);
        let back = job_from_saved(&saved).unwrap();
        assert_eq!((back.kind.as_str(), back.running, back.ok, back.installed), ("update", false, Some(true), true));
        assert_eq!((back.started.as_str(), back.finished.as_str()), (j.started.as_str(), j.finished.as_str()));
        assert_eq!(back.output, j.output); // 26 KB: all of it is kept
        assert!(job_from_saved("null").is_none() && job_from_saved("{\"kind\":\"\"}").is_none());
    }

    #[test]
    fn log_tails_are_taken_without_copying_everything() {
        assert_eq!(tail_lines("a\nb\nc\n", 2), "b\nc");
        assert_eq!(tail_lines("a\nb", 5), "a\nb");
        assert_eq!(tail_lines("", 3), "");
        assert_eq!(last_lines(["x", "y"].into_iter()), "x\ny");
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

        /// `seed` cut short, with a few bytes changed, or random bytes.
        fn mutate(&mut self, seed: &[u8]) -> Vec<u8> {
            let mut v = if seed.is_empty() || self.below(4) == 0 {
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
        let mut rng = Rng(0x9E37_79B9_7F4A_7C15);
        let mut cast = vec![0x12, 0x34, 0x84, 0x00, 0, 0, 0, 2, 0, 0, 0, 0];
        cast.extend_from_slice(&mdns_query(7, &["_googlecast._tcp.local"])[12..]);
        let seeds: Vec<Vec<u8>> = vec![
            cast,
            b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n4\r\nWiki\r\n5;x=1\r\npedia\r\n0\r\n\r\n".to_vec(),
            b"HTTP/1.0 200 OK\r\nContent-Length: 2\r\n\r\nok".to_vec(),
            r#"{"status":101,"remoteName":"Łazienka Ł","x":"a\"b\\c","n":[1]}"#.as_bytes().to_vec(),
            b"wlan0     IEEE 802.11-DS  ESSID:\"Net\"\n Mode:Managed  Frequency=2.422 GHz  Bit Rate:72 Mb/s".to_vec(),
            b" wlan0: 0002    4.  -53.  -98.       0      0 330547".to_vec(),
            b"Authenticated as 'name' ! BLOB = x".to_vec(),
            b"name=K%C5%81+x&bitrate=160&&=".to_vec(),
        ];
        for round in 0..20_000 {
            let input = rng.mutate(&seeds[round % seeds.len()]);
            let text = String::from_utf8_lossy(&input);
            let _ = mdns_names(&input);
            let _ = dns_name(&input, rng.below(input.len() + 2), 0);
            let _ = dechunk(&input);
            let _ = complete(&input);
            let _ = json_field(&text, "remoteName");
            let _ = json_field(&text, "x");
            let _ = parse_iwconfig(&text);
            let _ = parse_wireless(&text, "wlan0");
            let _ = redact(&text);
            let _ = form_pairs(&input);
            let _ = host_ok(&text);
            let _ = etag_matches(&text, "abc");
            let _ = tail_chars(&text, rng.below(64));
            let _ = query_pairs(&text);
        }
    }
}
