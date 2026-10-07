//! The speaker's settings.
//!
//! agent/settings.tsv describes every option and agent/rules.tsv every check (with its message);
//! the lithify command reads the same two files, so the speaker and the computer accept, refuse
//! and default alike (both test suites run agent/settings-cases.tsv). The values live on the
//! speaker in `<base>/settings/settings.conf`
//! (key=value) and belong to the speaker: its web page changes them, and an update from the
//! computer keeps them unless it sends a patch on purpose. `<base>/settings/install.conf` holds
//! what the computer sets at install time (its own address, the speaker's id). librespot's
//! arguments and the agent's configuration are derived from both.

use std::env;
use std::fs;
use std::io::{self, Write};
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::process;
use std::sync::atomic::{AtomicU32, Ordering};
use std::sync::{Mutex, MutexGuard, OnceLock};

use crate::{base_dir, canonical_route, log, net_of, overlaps, utc_now};

const SCHEMA: &str = include_str!("../settings.tsv");
const RULES: &str = include_str!("../rules.tsv");

/// Owner of the settings files: the Cast process manager's user, which runs the agent.
const SERVICE_UID: u32 = 1000;
/// librespot refuses an empty name; this one is used if the settings have lost theirs.
const FALLBACK_NAME: &str = "Lithify";
/// Where librespot keeps the tracks it is downloading (the current and the next one): a tmpfs
/// of its own, mounted by the exec wrapper, so a long track cannot fill the speaker's small /tmp
/// and what a killed librespot leaves behind is wiped at its next start.
pub const LIBRESPOT_TMP: &str = "/tmp/lithify-librespot";

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Kind {
    Text,
    Int,
    Bool,
    Choice,
    Volume,
    List,
}

impl Kind {
    pub fn name(self) -> &'static str {
        match self {
            Kind::Text => "text",
            Kind::Int => "int",
            Kind::Bool => "bool",
            Kind::Choice => "choice",
            Kind::Volume => "volume",
            Kind::List => "list",
        }
    }
}

#[derive(Debug)]
pub struct Opt {
    pub key: &'static str,
    pub section: &'static str,
    pub kind: Kind,
    pub default: &'static str,
    pub rule: &'static str,
    pub group: &'static str,
    pub restart: &'static str,
}

impl Opt {
    /// The choices of a `choice` option ("" for the empty one).
    pub fn choices(&self) -> Vec<&'static str> {
        if self.kind != Kind::Choice {
            return Vec::new();
        }
        self.rule.split('|').map(|c| if c == "-" { "" } else { c }).collect()
    }

    /// MIN-MAX of an `int` or `volume` option.
    pub fn range(&self) -> Option<(i64, i64)> {
        if !matches!(self.kind, Kind::Int | Kind::Volume) {
            return None;
        }
        let (a, b) = self.rule.split_once('-')?;
        Some((a.parse().ok()?, b.parse().ok()?))
    }
}

pub fn schema() -> &'static [Opt] {
    static PARSED: OnceLock<Vec<Opt>> = OnceLock::new();
    PARSED.get_or_init(|| {
        let dash = |s: &'static str| if s == "-" { "" } else { s };
        SCHEMA
            .lines()
            .map(str::trim)
            .filter(|l| !l.is_empty() && !l.starts_with('#'))
            .filter_map(|l| {
                let f: Vec<&'static str> = l.split_whitespace().collect();
                let kind = match *f.get(2)? {
                    "text" => Kind::Text,
                    "int" => Kind::Int,
                    "bool" => Kind::Bool,
                    "choice" => Kind::Choice,
                    "volume" => Kind::Volume,
                    "list" => Kind::List,
                    _ => return None,
                };
                (f.len() == 7).then(|| Opt {
                    key: f[0],
                    section: f[1],
                    kind,
                    default: dash(f[3]),
                    rule: f[4],
                    group: f[5],
                    restart: f[6],
                })
            })
            .collect()
    })
}

pub fn opt(key: &str) -> Option<&'static Opt> {
    schema().iter().find(|o| o.key == key)
}

// ── checks (agent/rules.tsv) ─────────────────────────────────────────────

struct TextRule {
    name: &'static str,
    min: usize,
    max: usize,
    /// The allowed characters as ranges; None: anything but a control character.
    chars: Option<Vec<(char, char)>>,
    flags: &'static str,
    message: &'static str,
}

impl TextRule {
    fn has(&self, flag: &str) -> bool {
        self.flags.split(',').any(|f| f == flag)
    }
}

#[derive(Default)]
struct Rules {
    messages: Vec<(&'static str, &'static str)>,
    list_max: usize,
    text: Vec<TextRule>,
    network: Vec<(&'static str, u32)>,
    never: Vec<(u32, u32)>,
    reserved: Vec<(&'static str, &'static str)>,
    ports: Vec<u16>,
    /// Lines that did not parse (a test keeps this empty).
    bad: Vec<&'static str>,
}

fn rules() -> &'static Rules {
    static PARSED: OnceLock<Rules> = OnceLock::new();
    PARSED.get_or_init(|| {
        let mut r = Rules::default();
        for line in RULES.lines().map(|l| l.trim_end_matches('\r')) {
            if line.is_empty() || line.starts_with('#') {
                continue;
            }
            let f: Vec<&'static str> = line.split('\t').collect();
            let num = |i: usize| f.get(i).and_then(|v| v.parse::<usize>().ok());
            let ok = match (f[0], f.len()) {
                ("message", 3) => {
                    r.messages.push((f[1], f[2]));
                    true
                }
                ("list", 2) => num(1).map(|n| r.list_max = n).is_some(),
                ("text", 7) => match (num(2), num(3)) {
                    (Some(min), Some(max)) => {
                        r.text.push(TextRule { name: f[1], min, max, chars: char_class(f[4]), flags: f[5], message: f[6] });
                        true
                    }
                    _ => false,
                },
                ("network", 3) => num(2).map(|p| r.network.push((f[1], p as u32))).is_some(),
                ("never", 2) => canonical_route(f[1]).as_deref().and_then(net_of).map(|n| r.never.push(n)).is_some(),
                ("reserved", 3) => {
                    r.reserved.push((f[1], f[2]));
                    true
                }
                ("port", 3) => f[1].parse().map(|p| r.ports.push(p)).is_ok(),
                _ => false,
            };
            if !ok {
                r.bad.push(line);
            }
        }
        r
    })
}

/// A class like "A-Za-z0-9_-" as ranges (\s is a space, \x the character x); "*": None.
fn char_class(spec: &str) -> Option<Vec<(char, char)>> {
    if spec == "*" {
        return None;
    }
    let mut tokens: Vec<(char, bool)> = Vec::new(); // (character, escaped)
    let mut it = spec.chars();
    while let Some(c) = it.next() {
        match c {
            '\\' => match it.next() {
                Some('s') => tokens.push((' ', true)),
                Some(x) => tokens.push((x, true)),
                None => {}
            },
            c => tokens.push((c, false)),
        }
    }
    let mut out = Vec::new();
    let mut i = 0;
    while i < tokens.len() {
        if i + 2 < tokens.len() && tokens[i + 1] == ('-', false) {
            out.push((tokens[i].0, tokens[i + 2].0));
            i += 3;
        } else {
            out.push((tokens[i].0, tokens[i].0));
            i += 1;
        }
    }
    Some(out)
}

/// Message `name` of rules.tsv with its {placeholders} filled in.
pub fn message(name: &str, fill: &[(&str, &str)]) -> String {
    let mut text = rules().messages.iter().find(|(n, _)| *n == name).map_or(name, |(_, t)| *t).to_string();
    for (k, v) in fill {
        text = text.replace(&format!("{{{k}}}"), v);
    }
    text
}

/// The canonical form of `raw` for option `o`, or why it is not acceptable.
pub fn normalize(o: &Opt, raw: &str) -> Result<String, String> {
    let v = raw.trim();
    match o.kind {
        Kind::Bool => match v.to_ascii_lowercase().as_str() {
            "1" | "true" | "yes" | "on" => Ok("true".into()),
            "0" | "false" | "no" | "off" | "" => Ok("false".into()),
            _ => Err(message("bool", &[])),
        },
        Kind::Int | Kind::Volume => {
            if o.kind == Kind::Volume && (v.is_empty() || v.eq_ignore_ascii_case("current")) {
                return Ok("current".into());
            }
            let n: i64 = v.parse().map_err(|_| message("number", &[]))?;
            let (lo, hi) = o.range().unwrap_or((i64::MIN, i64::MAX));
            if n < lo || n > hi {
                return Err(message("range", &[("min", &lo.to_string()), ("max", &hi.to_string())]));
            }
            Ok(n.to_string())
        }
        Kind::Choice => {
            let choices = o.choices();
            choices.iter().find(|c| c.eq_ignore_ascii_case(v)).map(|c| c.to_string()).ok_or_else(|| {
                let none = message("none", &[]);
                let shown: Vec<&str> = choices.iter().map(|c| if c.is_empty() { none.as_str() } else { c }).collect();
                message("choice", &[("choices", &shown.join(", "))])
            })
        }
        Kind::Text => check(o.rule, v, false).map(|_| v.to_string()),
        Kind::List => {
            let items: Vec<&str> = v.split(|c: char| c.is_whitespace() || c == ',').filter(|s| !s.is_empty()).collect();
            if items.len() > rules().list_max {
                return Err(message("entries", &[("max", &rules().list_max.to_string())]));
            }
            for item in &items {
                check(o.rule, item, true)?;
            }
            Ok(items.join(" "))
        }
    }
}

/// `v` against rule `rule` (a text value, or one entry of a list).
fn check(rule: &str, v: &str, entry: bool) -> Result<(), String> {
    if rules().network.iter().any(|(n, _)| *n == rule) {
        return check_network(v);
    }
    let Some(r) = rules().text.iter().find(|r| r.name == rule) else { return Err(message("unknown", &[])) };
    if v.is_empty() && r.has("empty-ok") {
        return Ok(());
    }
    let allowed = |c: char| match &r.chars {
        None => !c.is_control(),
        Some(set) => set.iter().any(|(a, b)| (*a..=*b).contains(&c)),
    };
    let ok = (r.min..=r.max).contains(&v.chars().count())
        && v.chars().all(allowed)
        && !(r.has("no-space") && v.chars().any(char::is_whitespace))
        && !(r.has("no-leading-dash") && v.starts_with('-'));
    if !ok {
        return Err(if entry { message("entry", &[("value", v), ("message", r.message)]) } else { r.message.to_string() });
    }
    if r.has("not-reserved") {
        if let Some(long) = reserved_arg(v) {
            return Err(message("reserved", &[("value", v), ("option", long)]));
        }
    }
    Ok(())
}

/// May `v` (an IPv4 address or network) get a fail-fast route? Also used for the addresses
/// host names resolve to.
pub fn check_network(v: &str) -> Result<(), String> {
    let min = rules().network.first().map_or(8, |(_, p)| *p);
    let Some((net, len)) = canonical_route(v).as_deref().and_then(net_of) else {
        return Err(message("network", &[("value", v)]));
    };
    if len < min {
        return Err(message("prefix", &[("value", v), ("min", &min.to_string())]));
    }
    if rules().never.iter().any(|n| overlaps((net, len), *n)) {
        return Err(message("never", &[("value", v)]));
    }
    Ok(())
}

/// The reserved librespot option (long name) an extra argument would give, if any: `--name`,
/// `--name=x`, `-n`, `-nx`, or a group of short flags that contains one.
fn reserved_arg(v: &str) -> Option<&'static str> {
    let reserved = &rules().reserved;
    if let Some(rest) = v.strip_prefix("--") {
        let name = rest.split('=').next().unwrap_or(rest);
        return reserved.iter().find(|(long, _)| *long == name).map(|(long, _)| *long);
    }
    let group = v.strip_prefix('-')?;
    let letters: String = group.chars().take_while(|c| c.is_ascii_alphabetic()).collect();
    reserved.iter().find(|(_, short)| !short.is_empty() && letters.contains(*short)).map(|(long, _)| *long)
}

/// Values for every option, in schema order.
#[derive(Clone, Debug, PartialEq)]
pub struct Settings {
    values: Vec<(&'static str, String)>,
    pub updated: String,
    pub by: String,
}

impl Settings {
    pub fn defaults() -> Settings {
        Settings {
            values: schema().iter().map(|o| (o.key, o.default.to_string())).collect(),
            updated: String::new(),
            by: String::new(),
        }
    }

    /// settings.conf; a bad or unknown line is reported and that option keeps its default.
    pub fn parse(text: &str) -> (Settings, Vec<String>) {
        let mut s = Settings::defaults();
        let mut warnings = Vec::new();
        for (k, v) in pairs(text) {
            match k.as_str() {
                "_updated" => s.updated = v,
                "_by" => s.by = v,
                _ => {
                    if let Err(e) = s.set(&k, &v) {
                        warnings.push(format!("{k}: {e} (default used)"));
                    }
                }
            }
        }
        (s, warnings)
    }

    pub fn get(&self, key: &str) -> &str {
        self.values.iter().find(|(k, _)| *k == key).map(|(_, v)| v.as_str()).unwrap_or("")
    }

    pub fn bool(&self, key: &str) -> bool {
        self.get(key) == "true"
    }

    pub fn list(&self, key: &str) -> Vec<String> {
        self.get(key).split_whitespace().map(str::to_string).collect()
    }

    pub fn values(&self) -> &[(&'static str, String)] {
        &self.values
    }

    /// Validate and store one value; true when it changed.
    pub fn set(&mut self, key: &str, raw: &str) -> Result<bool, String> {
        let o = opt(key).ok_or_else(|| message("unknown", &[]))?;
        let v = normalize(o, raw)?;
        let Some(slot) = self.values.iter_mut().find(|(k, _)| *k == key) else {
            return Err(message("unknown", &[]));
        };
        let changed = slot.1 != v;
        slot.1 = v;
        Ok(changed)
    }

    /// Values that are fine one by one but not together: ports the speaker's own services use,
    /// or the page and librespot on the same port.
    pub fn conflicts(&self) -> Vec<(&'static str, String)> {
        let mut out = Vec::new();
        let port = |k: &str| self.get(k).parse::<u16>().unwrap_or(0);
        for k in ["ui_port", "zeroconf_port"] {
            if rules().ports.contains(&port(k)) {
                out.push((opt(k).map_or("", |o| o.key), message("port", &[("port", &port(k).to_string())])));
            }
        }
        if port("ui_port") == port("zeroconf_port") {
            out.push(("zeroconf_port", message("same-port", &[])));
        }
        out
    }

    pub fn render(&self) -> String {
        let mut out = String::from(
            "# Settings of this speaker. Change them on its web page or with `lithify settings`;\n\
             # updates from the computer keep them.\n",
        );
        out.push_str(&format!("_updated={}\n_by={}\n", self.updated, self.by));
        for (k, v) in &self.values {
            out.push_str(&format!("{k}={v}\n"));
        }
        out
    }
}

/// key=value lines (comments and blank lines skipped).
pub fn pairs(text: &str) -> Vec<(String, String)> {
    text.lines()
        .map(str::trim)
        .filter(|l| !l.is_empty() && !l.starts_with('#'))
        .filter_map(|l| l.split_once('='))
        .map(|(k, v)| (k.trim().to_string(), v.trim().to_string()))
        .collect()
}

/// What the computer writes at install time.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Install {
    pub companion_url: String,
    pub speaker_id: String,
    pub platform: String,
}

impl Install {
    pub fn parse(text: &str) -> Install {
        let mut i = Install::default();
        for (k, v) in pairs(text) {
            match k.as_str() {
                "companion_url" => i.companion_url = v.trim_end_matches('/').to_string(),
                "speaker_id" => i.speaker_id = v,
                "platform" => i.platform = v,
                _ => {}
            }
        }
        i
    }
}

/// librespot's command line from the settings (one argument per element).
pub fn librespot_args(s: &Settings, base: &str) -> Vec<String> {
    let mut a: Vec<String> = Vec::new();
    let mut kv = |name: &str, value: &str| {
        a.push(name.to_string());
        a.push(value.to_string());
    };
    kv("--name", if s.get("name").is_empty() { FALLBACK_NAME } else { s.get("name") });
    kv("--device-type", s.get("device_type"));
    kv("--backend", s.get("backend"));
    kv("--device", s.get("device"));
    kv("--format", s.get("format"));
    kv("--bitrate", s.get("bitrate"));
    kv("--mixer", s.get("mixer"));
    kv("--volume-ctrl", s.get("volume_ctrl"));
    if s.get("mixer") == "alsa" {
        kv("--alsa-mixer-device", s.get("mixer_device"));
        kv("--alsa-mixer-control", s.get("mixer_control"));
        // Otherwise librespot takes the index from the device's ",N" and finds no such control.
        kv("--alsa-mixer-index", "0");
    }
    if s.get("initial_volume") != "current" {
        kv("--initial-volume", s.get("initial_volume"));
    }
    if !s.get("autoplay").is_empty() {
        kv("--autoplay", s.get("autoplay"));
    }
    kv("--cache", &format!("{base}/cache"));
    kv("--tmp", LIBRESPOT_TMP);
    kv("--zeroconf-port", s.get("zeroconf_port"));
    kv("--onevent", &format!("{base}/lithify-agent onevent"));
    if s.bool("normalisation") {
        a.push("--enable-volume-normalisation".into());
    }
    a.push("--disable-audio-cache".into());
    a.push("--emit-sink-events".into());
    a.extend(s.list("extra_args"));
    a
}

pub fn dir() -> PathBuf {
    base_dir().join("settings")
}

/// The speaker's settings and install data, when this install has them (older ones use
/// agent.conf and librespot.args). Settings that have lost the speaker's name (a damaged file)
/// give way to the previous ones when those are intact.
pub fn load() -> Option<(Settings, Install)> {
    let text = fs::read_to_string(dir().join("settings.conf")).ok()?;
    let (mut s, mut warnings) = Settings::parse(&text);
    if s.get("name").is_empty() {
        let good = fs::read_to_string(dir().join(GOOD)).map(|t| Settings::parse(&t).0);
        match good {
            Ok(g) if !g.get("name").is_empty() => {
                warnings.push("no speaker name: the last settings that worked are used".into());
                s = g;
            }
            _ => warnings.push(format!("no speaker name: librespot uses {FALLBACK_NAME:?}")),
        }
    }
    warn_once(&warnings);
    let install = Install::parse(&fs::read_to_string(dir().join("install.conf")).unwrap_or_default());
    Some((s, install))
}

/// The settings are read often (the page's status): report the same problems only once.
fn warn_once(warnings: &[String]) {
    static LAST: Mutex<Vec<String>> = Mutex::new(Vec::new());
    let mut last = LAST.lock().unwrap_or_else(|e| e.into_inner());
    if *last != warnings {
        for w in warnings {
            log(&format!("settings.conf: {w}"));
        }
        *last = warnings.to_vec();
    }
}

/// The last settings librespot ran well with (see `remember_good`).
const GOOD: &str = "settings.good.conf";

/// One change of the settings at a time: two page tabs, or the page and the agent's own restore,
/// must not mix their files.
pub fn lock() -> MutexGuard<'static, ()> {
    static LOCK: Mutex<()> = Mutex::new(());
    LOCK.lock().unwrap_or_else(|e| e.into_inner())
}

fn read(name: &str) -> io::Result<Option<String>> {
    match fs::read_to_string(dir().join(name)) {
        Ok(t) => Ok(Some(t)),
        Err(e) if e.kind() == io::ErrorKind::NotFound => Ok(None),
        Err(e) => Err(e),
    }
}

fn same_librespot_options(a: &Settings, b: &Settings) -> bool {
    schema().iter().filter(|o| o.restart == "librespot").all(|o| a.get(o.key) == b.get(o.key))
}

/// Replace a file in one step: write a new one, flush it to the flash, rename it over the old one
/// and flush the directory, so a power cut leaves either the old or the new file, never a part
/// (and a reader never sees half of one). Each call has its own temporary file: two threads that
/// replace the same file at once must not write into one.
pub(crate) fn write_atomic(path: &Path, text: &str) -> io::Result<()> {
    static N: AtomicU32 = AtomicU32::new(0);
    let tmp = PathBuf::from(format!("{}.tmp{}.{}", path.display(), process::id(), N.fetch_add(1, Ordering::Relaxed)));
    let result = (|| {
        let mut f = fs::File::create(&tmp)?;
        f.write_all(text.as_bytes())?;
        f.set_permissions(fs::Permissions::from_mode(0o644))?;
        f.sync_all()?;
        fs::rename(&tmp, path)
    })();
    if result.is_err() {
        let _ = fs::remove_file(&tmp);
    }
    result?;
    if let Some(d) = path.parent().and_then(|d| fs::File::open(d).ok()) {
        let _ = d.sync_all(); // not every file system syncs directories; the rename is done anyway
    }
    Ok(())
}

pub fn save(s: &Settings) -> io::Result<()> {
    write_atomic(&dir().join("settings.conf"), &s.render())
}

/// Remember the current settings as ones librespot runs well with (called once it has run with
/// them for a while). Written only when librespot's options differ from the copy.
pub fn remember_good() -> io::Result<bool> {
    let _lock = lock();
    let Some(text) = read("settings.conf")? else { return Ok(false) };
    let current = Settings::parse(&text).0;
    if current.get("name").is_empty() {
        return Ok(false);
    }
    if let Some(good) = read(GOOD)? {
        if same_librespot_options(&Settings::parse(&good).0, &current) {
            return Ok(false);
        }
    }
    write_atomic(&dir().join(GOOD), &text)?;
    Ok(true)
}

/// Put back librespot's options from the last settings that worked; the agent's own (PIN, port,
/// routes) stay as they are. False when there is nothing to restore.
pub fn restore_good() -> io::Result<bool> {
    let _lock = lock();
    let (Some(text), Some(good)) = (read("settings.conf")?, read(GOOD)?) else { return Ok(false) };
    let (mut current, good) = (Settings::parse(&text).0, Settings::parse(&good).0);
    if same_librespot_options(&current, &good) {
        return Ok(false);
    }
    for o in schema().iter().filter(|o| o.restart == "librespot") {
        let _ = current.set(o.key, good.get(o.key));
    }
    current.updated = utc_now();
    current.by = "agent".into();
    save(&current)?;
    Ok(true)
}

/// `settings-apply BASE`, run as root by install.sh in the directory of a new bundle: the
/// speaker gets settings from settings.default when it has none yet, values in settings.patch
/// (sent on purpose, e.g. `lithify update --settings`) override its own, install.conf is stored.
/// Nothing is written unless everything is valid.
pub fn cmd_apply(args: &[String]) -> i32 {
    let Some(base) = args.first().map(PathBuf::from) else {
        eprintln!("usage: lithify-agent settings-apply BASE (run in the new bundle's directory)");
        return 2;
    };
    let here = env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
    let target = base.join("settings");
    // The speaker's own settings, unless they are damaged (no name): then the computer's.
    let existing = fs::read_to_string(target.join("settings.conf")).ok().filter(|text| {
        let named = !Settings::parse(text).0.get("name").is_empty();
        if !named {
            println!("warning: the speaker's settings have no name: starting again from the computer's");
        }
        named
    });
    let source = match &existing {
        Some(text) => text.clone(),
        None => fs::read_to_string(here.join("settings.default")).unwrap_or_default(),
    };
    let (mut s, warnings) = Settings::parse(&source);
    for w in &warnings {
        println!("warning: {w}");
    }
    let mut changed = Vec::new();
    if let Ok(patch) = fs::read_to_string(here.join("settings.patch")) {
        for (k, v) in pairs(&patch) {
            match s.set(&k, &v) {
                Ok(true) => changed.push(k),
                Ok(false) => {}
                Err(e) => {
                    println!("FAIL setting {k}: {e}");
                    return 1;
                }
            }
        }
    }
    if s.get("name").is_empty() {
        println!("FAIL settings: the speaker has no name");
        return 1;
    }
    let write_settings = existing.is_none() || !changed.is_empty();
    if write_settings {
        s.updated = utc_now();
        s.by = "computer".into();
    }
    let result = (|| -> io::Result<()> {
        fs::create_dir_all(&target)?;
        // Only what changed is written (flash wear, and an update must not touch the rest).
        if write_settings {
            write_atomic(&target.join("settings.conf"), &s.render())?;
        }
        if let Ok(install) = fs::read_to_string(here.join("install.conf")) {
            if fs::read_to_string(target.join("install.conf")).ok().as_deref() != Some(install.as_str()) {
                write_atomic(&target.join("install.conf"), &install)?;
            }
        }
        // Owned by the services' user, so the web page can change the settings.
        for p in [target.clone(), target.join("settings.conf"), target.join("install.conf")] {
            if p.exists() {
                let _ = std::os::unix::fs::chown(&p, Some(SERVICE_UID), Some(SERVICE_UID));
            }
        }
        fs::set_permissions(&target, fs::Permissions::from_mode(0o755))
    })();
    if let Err(e) = result {
        println!("FAIL settings: {e}");
        return 1;
    }
    match (existing.is_none(), changed.is_empty()) {
        (true, _) => println!("SETTINGS_OK created"),
        (false, true) => println!("SETTINGS_OK kept the speaker's settings"),
        (false, false) => println!("SETTINGS_OK changed {}", changed.join(" ")),
    }
    0
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn schema_has_every_line() {
        let lines = SCHEMA.lines().map(str::trim).filter(|l| !l.is_empty() && !l.starts_with('#')).count();
        assert_eq!(schema().len(), lines, "a line of settings.tsv did not parse");
        assert_eq!(opt("bitrate").unwrap().choices(), vec!["96", "160", "320"]);
        assert_eq!(opt("autoplay").unwrap().choices(), vec!["", "on", "off"]);
        assert_eq!(opt("ui_port").unwrap().range(), Some((1024, 65535)));
        for o in schema() {
            assert!(normalize(o, o.default).is_ok() || o.key == "name", "default of {} is invalid", o.key);
            assert!(["librespot", "agent"].contains(&o.restart), "{}", o.key);
        }
    }

    #[test]
    fn rules_file_parses_completely() {
        assert!(rules().bad.is_empty(), "lines of rules.tsv that did not parse: {:?}", rules().bad);
        assert!(rules().list_max > 0 && !rules().never.is_empty() && !rules().ports.is_empty());
        // Every check settings.tsv names exists, and every message the code asks for.
        for o in schema().iter().filter(|o| matches!(o.kind, Kind::Text | Kind::List)) {
            let known = rules().text.iter().any(|r| r.name == o.rule) || rules().network.iter().any(|(n, _)| *n == o.rule);
            assert!(known, "{}: no rule {:?} in rules.tsv", o.key, o.rule);
        }
        for m in ["bool", "number", "range", "choice", "none", "unknown", "entries", "entry", "reserved", "network", "prefix",
                  "never", "port", "same-port"] {
            assert!(rules().messages.iter().any(|(n, _)| *n == m), "no message {m:?} in rules.tsv");
        }
        assert_eq!(char_class("A-Za-z0-9\\s_-"), Some(vec![('A', 'Z'), ('a', 'z'), ('0', '9'), (' ', ' '), ('_', '_'), ('-', '-')]));
        assert_eq!(char_class("*"), None);
    }

    /// The cases the lithify command's tests run too (tests/test_config.py).
    #[test]
    fn shared_cases_hold() {
        let unescape = |v: &str| -> String {
            if v == "\"\"" {
                return String::new();
            }
            let (mut out, mut it) = (String::new(), v.chars());
            while let Some(c) = it.next() {
                match (c, c == '\\') {
                    (_, true) => match it.next() {
                        Some('s') => out.push(' '),
                        Some('n') => out.push('\n'),
                        Some(x) => out.push(x),
                        None => {}
                    },
                    (c, false) => out.push(c),
                }
            }
            out
        };
        let mut n = 0;
        for line in include_str!("../settings-cases.tsv").lines().filter(|l| !l.is_empty() && !l.starts_with('#')) {
            let f: Vec<&str> = line.split('\t').collect();
            match f[..] {
                ["value", key, input, expected] => {
                    let got = normalize(opt(key).unwrap_or_else(|| panic!("no setting {key}")), &unescape(input));
                    match expected {
                        "<refused>" => assert!(got.is_err(), "{key}={input:?} must be refused, got {got:?}"),
                        e => assert_eq!(got.as_deref(), Ok(unescape(e).as_str()), "{key}={input:?}"),
                    }
                }
                ["conflict", settings, keys] => {
                    let text: String = unescape(settings).split(' ').map(|kv| format!("{kv}\n")).collect();
                    let (s, _) = Settings::parse(&format!("name=x\n{text}"));
                    let mut got: Vec<&str> = s.conflicts().iter().map(|(k, _)| *k).collect();
                    got.sort_unstable();
                    let want = unescape(keys);
                    let mut want: Vec<&str> = want.split(' ').filter(|k| !k.is_empty()).collect();
                    want.sort_unstable();
                    assert_eq!(got, want, "conflicts of {settings}");
                }
                _ => panic!("settings-cases.tsv: bad line {line:?}"),
            }
            n += 1;
        }
        assert!(n > 50, "only {n} cases");
    }

    #[test]
    fn settings_round_trip_and_report_bad_lines() {
        let (mut s, w) = Settings::parse("name=Łazienka\nbitrate=160\nbogus=1\nui_port=99999\n_by=page\n");
        assert_eq!(w.len(), 2);
        assert_eq!((s.get("name"), s.get("bitrate"), s.get("ui_port"), s.by.as_str()), ("Łazienka", "160", "8090", "page"));
        assert_eq!(s.set("bitrate", "320"), Ok(true));
        assert_eq!(s.set("bitrate", "320"), Ok(false));
        let (again, w) = Settings::parse(&s.render());
        assert!(w.is_empty());
        assert_eq!(again, s);
    }

    #[test]
    fn librespot_command_line() {
        let (s, _) = Settings::parse("name=Łazienka (librespot)\nbitrate=160\ninitial_volume=40\nextra_args=--ap-port=443\n");
        let a = librespot_args(&s, "/lsync/lithify");
        assert_eq!(a[..2], ["--name", "Łazienka (librespot)"]);
        let val = |k: &str| a.iter().position(|x| x == k).map(|i| a[i + 1].as_str());
        assert_eq!(val("--bitrate"), Some("160"));
        assert_eq!(val("--alsa-mixer-control"), Some("Master"));
        assert_eq!(val("--initial-volume"), Some("40"));
        assert_eq!(val("--onevent"), Some("/lsync/lithify/lithify-agent onevent"));
        assert_eq!(val("--cache"), Some("/lsync/lithify/cache"));
        assert_eq!(val("--tmp"), Some(LIBRESPOT_TMP)); // downloads into a tmpfs of their own
        assert!(!a.contains(&"--enable-volume-normalisation".to_string()));
        assert_eq!(a.last().map(String::as_str), Some("--ap-port=443"));
        // "current" volume is left to the agent; softvol drops the ALSA mixer options
        let (s, _) = Settings::parse("name=x\nmixer=softvol\nautoplay=on\nnormalisation=true\n");
        let a = librespot_args(&s, "/b");
        assert!(!a.iter().any(|x| x == "--initial-volume" || x == "--alsa-mixer-control"));
        assert!(a.windows(2).any(|w| w == ["--autoplay", "on"]));
        assert!(a.contains(&"--enable-volume-normalisation".to_string()));
    }

    #[test]
    fn files_are_replaced_whole() {
        let d = env::temp_dir().join(format!("lithify-settings-test-{}", process::id()));
        fs::create_dir_all(&d).unwrap();
        let f = d.join("settings.conf");
        write_atomic(&f, "name=a\n").unwrap();
        write_atomic(&f, "name=b\n").unwrap();
        assert_eq!(fs::read_to_string(&f).unwrap(), "name=b\n");
        assert_eq!(fs::metadata(&f).unwrap().permissions().mode() & 0o777, 0o644);
        assert_eq!(fs::read_dir(&d).unwrap().count(), 1, "no temporary file is left");
        // threads replacing one file at once: always one whole version, never a mix
        let texts: Vec<String> = (0..8).map(|i| format!("{i}\n").repeat(5000)).collect();
        std::thread::scope(|s| {
            for t in &texts {
                let f = &f;
                s.spawn(move || {
                    for _ in 0..20 {
                        write_atomic(f, t).unwrap();
                    }
                });
            }
        });
        assert!(texts.contains(&fs::read_to_string(&f).unwrap()));
        assert_eq!(fs::read_dir(&d).unwrap().count(), 1, "no temporary file is left");
        fs::remove_dir_all(&d).unwrap();
    }

    #[test]
    fn extra_arguments_do_not_move_the_downloads() {
        let extra = opt("extra_args").unwrap();
        for v in ["--tmp /x", "--tmp=/x", "-t /x", "-qt /x"] {
            assert!(normalize(extra, v).is_err(), "{v:?} must be refused");
        }
        assert!(normalize(extra, "-q -v").is_ok());
    }

    #[test]
    fn a_lost_name_does_not_reach_librespot() {
        let (s, _) = Settings::parse("bitrate=160\n");
        assert_eq!(librespot_args(&s, "/b")[..2], ["--name", FALLBACK_NAME]);
    }

    #[test]
    fn install_data() {
        let i = Install::parse("companion_url=http://192.168.1.10:8095/\nspeaker_id=lazienka\nplatform=ls9\n");
        assert_eq!(i.companion_url, "http://192.168.1.10:8095");
        assert_eq!((i.speaker_id.as_str(), i.platform.as_str()), ("lazienka", "ls9"));
    }
}
