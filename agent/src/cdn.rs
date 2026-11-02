//! Spotify's audio CDNs, measured from the speaker, and which of them to skip.
//!
//! For every track Spotify hands both clients a list of addresses, one per CDN, and they try them
//! in its order, each for up to 10 s before they move on. A CDN that this network reaches badly
//! costs that wait on every track, and one that answers much more slowly than the others a slower
//! start. So the agent connects to each CDN a few times every 30 minutes and skips, with fail-fast
//! routes, the ones that fail or are much slower than the best. The best one is never skipped,
//! and a change waits for a second round that agrees. A CDN in use is measured at any time; a
//! skipped one only while nothing plays, because its routes have to go for those seconds.

use std::net::{Ipv4Addr, SocketAddr, SocketAddrV4, TcpStream, ToSocketAddrs};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

/// The CDNs and the host names Spotify gives their addresses under. A name that resolves to
/// nothing (Google's, on 2026-11-02) leaves its CDN out of a round.
const CDNS: &[(&str, &[&str])] = &[
    (
        "Akamai",
        &[
            "audio-ak-spotify-com.akamaized.net",
            "audio4-ak-spotify-com.akamaized.net",
            "audio-ak.spotifycdn.com",
            "audio4-ak.spotifycdn.com",
        ],
    ),
    ("Cloudflare", &["audio-cf.spotifycdn.com", "audio4-cf.spotifycdn.com"]),
    ("Fastly", &["audio-fa.scdn.co", "audio4-fa.scdn.co", "heads-fa.scdn.co", "heads4-fa.scdn.co"]),
    ("Google", &["audio-gm-off.spotifycdn.com", "audio4-gm-fb.spotifycdn.com"]),
];

/// A round this often...
pub(crate) const EVERY: Duration = Duration::from_secs(1800);
/// ...and this soon after one that saw a change coming (the second round confirms it).
pub(crate) const SOON: Duration = Duration::from_secs(120);
/// Again this soon when no CDN answered well: the network may be just coming up.
pub(crate) const RETRY: Duration = Duration::from_secs(60);

/// How many of a CDN's addresses are measured, and how many connections to each.
const MAX_ADDRS: usize = 8;
const TRIES: u32 = 3;
const CONNECT_TIMEOUT: Duration = Duration::from_secs(3);
/// A CDN fails when fewer than 9 of 10 connections to it work...
const HEALTHY_PER_10: u32 = 9;
/// ...and is slower when its usual connection takes over 3 times as long as the best CDN's, and
/// 50 ms more.
const SLOWER_TIMES: u32 = 3;
const SLOWER_BY_MS: u32 = 50;
/// Rounds in a row that must agree before a CDN is skipped or used again.
const ROUNDS_TO_CHANGE: u32 = 2;
/// A skipped CDN keeps every address its names resolved to since (DNS answers with a few of
/// many, and differently each time), up to this many: the latest.
const MAX_KEPT: usize = 32;

/// A CDN in one round.
#[derive(Clone, Debug, PartialEq)]
pub(crate) struct Measured {
    pub(crate) name: &'static str,
    /// Every address its host names resolved to: all of them are skipped when it is.
    pub(crate) addrs: Vec<Ipv4Addr>,
    /// Connections tried and those that worked (none tried: not measured this round).
    pub(crate) tried: u32,
    pub(crate) ok: u32,
    /// The usual time of those that worked, in milliseconds (their median).
    pub(crate) ms: Option<u32>,
    /// The settings' fail-fast lists name it: skipped either way, so not measured.
    pub(crate) listed: bool,
}

/// What a round says about a CDN.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum Verdict {
    Use,
    Failing,
    Slower,
}

impl Verdict {
    /// For the page and the log.
    pub(crate) fn as_str(self) -> &'static str {
        match self {
            Verdict::Use => "use",
            Verdict::Failing => "failing",
            Verdict::Slower => "slower",
        }
    }
}

/// One round: each CDN's addresses, and a few connections to each, all at once (a round takes
/// about a second, and at most `TRIES` × `CONNECT_TIMEOUT`). The CDNs named in `leave_out` are
/// resolved but not connected to (skipped ones, while something plays), and so are those that
/// `listed` (given a CDN's host names and addresses) says the settings' fail-fast lists name.
pub(crate) fn measure(leave_out: &[&str], listed: impl Fn(&[&str], &[Ipv4Addr]) -> bool) -> Vec<Measured> {
    let resolved: Vec<(&'static str, &[&str], Vec<Ipv4Addr>)> =
        CDNS.iter().map(|(name, hosts)| (*name, *hosts, resolve(hosts))).collect();
    thread::scope(|s| {
        let running: Vec<_> = resolved
            .iter()
            .map(|(name, hosts, addrs)| {
                let on_list = listed(hosts, addrs);
                let probes: Vec<_> = if leave_out.contains(name) || on_list {
                    Vec::new()
                } else {
                    addrs
                        .iter()
                        .take(MAX_ADDRS)
                        .map(|ip| {
                            let ip = *ip;
                            thread::Builder::new().stack_size(64 * 1024).spawn_scoped(s, move || connect_times(ip))
                        })
                        .collect()
                };
                (*name, addrs.clone(), on_list, probes)
            })
            .collect();
        running
            .into_iter()
            .map(|(name, addrs, listed, probes)| {
                let (mut tried, mut times) = (0, Vec::new());
                for times_of_one in probes.into_iter().filter_map(|p| p.ok()?.join().ok()) {
                    tried += times_of_one.len() as u32;
                    times.extend(times_of_one.into_iter().flatten());
                }
                let ok = times.len() as u32;
                Measured { name, addrs, tried, ok, ms: median(&mut times), listed }
            })
            .collect()
    })
}

fn resolve(hosts: &[&str]) -> Vec<Ipv4Addr> {
    let mut out = Vec::new();
    for host in hosts {
        for addr in (*host, 443).to_socket_addrs().into_iter().flatten() {
            if let SocketAddr::V4(v4) = addr {
                if !out.contains(v4.ip()) {
                    out.push(*v4.ip());
                }
            }
        }
    }
    out
}

/// `TRIES` connections to `ip`, one after the other: how long each took, `None` for a failed one.
fn connect_times(ip: Ipv4Addr) -> Vec<Option<u32>> {
    let addr = SocketAddr::V4(SocketAddrV4::new(ip, 443));
    (0..TRIES)
        .map(|_| {
            let t = Instant::now();
            TcpStream::connect_timeout(&addr, CONNECT_TIMEOUT)
                .ok()
                .map(|_| t.elapsed().as_millis().min(u128::from(u32::MAX)) as u32)
        })
        .collect()
}

fn median(times: &mut [u32]) -> Option<u32> {
    times.sort_unstable();
    times.get(times.len().saturating_sub(1) / 2).copied()
}

fn healthy(m: &Measured) -> bool {
    m.tried >= TRIES && m.ok * 10 >= m.tried * HEALTHY_PER_10
}

/// Each measured CDN's verdict from one round. `None` when no CDN answered well: then it is the
/// network, not the CDNs, and nothing changes.
pub(crate) fn verdicts(round: &[Measured]) -> Option<Vec<(&'static str, Verdict)>> {
    let best = round.iter().filter(|m| healthy(m)).filter_map(|m| m.ms).min()?;
    Some(
        round
            .iter()
            .filter(|m| m.tried > 0)
            .map(|m| {
                let verdict = match m.ms {
                    _ if !healthy(m) => Verdict::Failing,
                    Some(ms) if ms > best.saturating_mul(SLOWER_TIMES) && ms > best + SLOWER_BY_MS => Verdict::Slower,
                    _ => Verdict::Use,
                };
                (m.name, verdict)
            })
            .collect(),
    )
}

/// A CDN skipped now: why, and every address seen for it since (up to `MAX_KEPT`).
#[derive(Clone, Debug, PartialEq)]
pub(crate) struct Skipped {
    pub(crate) name: &'static str,
    pub(crate) verdict: Verdict,
    pub(crate) addrs: Vec<Ipv4Addr>,
}

/// What a round changed, for the log.
#[derive(Clone, Debug, PartialEq)]
pub(crate) struct Change {
    pub(crate) name: &'static str,
    /// `Use` when the CDN is used again.
    pub(crate) verdict: Verdict,
    pub(crate) text: String,
}

/// Which CDNs are skipped, from the rounds so far.
#[derive(Default)]
pub(crate) struct Choice {
    skipped: Vec<Skipped>,
    /// A verdict that differs from what is done with a CDN now, and in how many rounds in a row.
    pending: Vec<(&'static str, Verdict, u32)>,
    /// Whether a round has decided yet: the first one that can decides at once.
    decided: bool,
}

impl Choice {
    pub(crate) fn skipped(&self) -> &[Skipped] {
        &self.skipped
    }

    /// Whether a later round is waiting to confirm a change.
    pub(crate) fn changing(&self) -> bool {
        !self.pending.is_empty()
    }

    /// Whether any round has decided yet (until then every CDN is measured, so the first round
    /// waits for silence).
    pub(crate) fn decided(&self) -> bool {
        self.decided
    }

    /// The fail-fast routes ("a.b.c.d/32") of the skipped CDNs.
    pub(crate) fn routes(&self) -> Vec<String> {
        let mut out: Vec<String> =
            self.skipped.iter().flat_map(|s| s.addrs.iter().map(|ip| format!("{ip}/32"))).collect();
        out.sort();
        out.dedup();
        out
    }

    /// Take a round in, and say what changed. A CDN is skipped, or used again, once
    /// `ROUNDS_TO_CHANGE` rounds in a row say so; the first round that decides anything does at
    /// once. A round that did not measure a CDN changes nothing about it.
    pub(crate) fn update(&mut self, round: &[Measured]) -> Vec<Change> {
        for m in round.iter().filter(|m| !m.addrs.is_empty()) {
            if let Some(s) = self.skipped.iter_mut().find(|s| s.name == m.name) {
                for ip in &m.addrs {
                    if !s.addrs.contains(ip) {
                        s.addrs.push(*ip);
                    }
                }
                let extra = s.addrs.len().saturating_sub(MAX_KEPT);
                s.addrs.drain(..extra);
            }
        }
        let Some(verdicts) = verdicts(round) else { return Vec::new() };
        let first = !self.decided;
        self.decided = true;
        let best = round.iter().filter(|m| healthy(m)).min_by_key(|m| m.ms.unwrap_or(u32::MAX));
        let (mut changes, mut pending) = (Vec::new(), Vec::new());
        for (name, verdict) in verdicts {
            let now = self.skipped.iter().position(|s| s.name == name);
            match (now, verdict) {
                (None, Verdict::Use) => continue,
                // still skipped, maybe for the other reason now
                (Some(i), Verdict::Failing | Verdict::Slower) => {
                    self.skipped[i].verdict = verdict;
                    continue;
                }
                _ => {}
            }
            let seen = self.pending.iter().find(|(n, v, _)| *n == name && *v == verdict).map_or(0, |p| p.2) + 1;
            if !first && seen < ROUNDS_TO_CHANGE {
                pending.push((name, verdict, seen));
                continue;
            }
            let m = round.iter().find(|m| m.name == name).expect("a verdict comes from a measured CDN");
            match now {
                Some(i) => {
                    self.skipped.remove(i);
                }
                None => self.skipped.push(Skipped { name, verdict, addrs: m.addrs.clone() }),
            }
            changes.push(Change { name, verdict, text: describe(m, verdict, best) });
        }
        self.pending = pending;
        changes
    }
}

/// A line for the log: "audio CDN Fastly skipped: 3 of 12 connections worked".
fn describe(m: &Measured, verdict: Verdict, best: Option<&Measured>) -> String {
    let ms = |m: &Measured| m.ms.map_or("-".to_string(), |ms| format!("{ms} ms"));
    match verdict {
        Verdict::Failing => format!("audio CDN {} skipped: {} of {} connections worked", m.name, m.ok, m.tried),
        Verdict::Slower => match best {
            Some(b) => format!("audio CDN {} skipped: {}, {} {}", m.name, ms(m), b.name, ms(b)),
            None => format!("audio CDN {} skipped: {}", m.name, ms(m)),
        },
        Verdict::Use => format!("audio CDN {} used again: {} of {} connections, {}", m.name, m.ok, m.tried, ms(m)),
    }
}

// ── what the page shows ──────────────────────────────────────────────────

/// The last round, as the page shows it.
#[derive(Clone)]
pub(crate) struct Snapshot {
    /// When it ran (Unix seconds; 0 before the clock was set).
    pub(crate) at: u64,
    pub(crate) cdns: Vec<Measured>,
    pub(crate) skipped: Vec<Skipped>,
}

static LAST: Mutex<Option<Snapshot>> = Mutex::new(None);

pub(crate) fn remember(round: &[Measured], choice: &Choice) {
    let at = SystemTime::now().duration_since(UNIX_EPOCH).map_or(0, |d| d.as_secs());
    let at = if at < 1_600_000_000 { 0 } else { at };
    let snapshot = Snapshot { at, cdns: round.to_vec(), skipped: choice.skipped().to_vec() };
    *LAST.lock().unwrap_or_else(|e| e.into_inner()) = Some(snapshot);
}

pub(crate) fn last() -> Option<Snapshot> {
    LAST.lock().unwrap_or_else(|e| e.into_inner()).clone()
}

// ── librespot's own trouble ──────────────────────────────────────────────

static TROUBLE: AtomicBool = AtomicBool::new(false);

/// librespot moving on from a CDN that did not answer (not one a fail-fast route skips) asks for
/// a round now, rather than at the next half hour.
pub(crate) fn note_line(line: &str) {
    if line.contains("librespot_audio::fetch] Fetching ")
        && line.contains("trying next")
        && !line.contains("NetworkUnreachable")
    {
        TROUBLE.store(true, Ordering::Relaxed);
    }
}

/// Whether librespot had such trouble since the last call.
pub(crate) fn take_trouble() -> bool {
    TROUBLE.swap(false, Ordering::Relaxed)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn m(name: &'static str, ok: u32, tried: u32, ms: Option<u32>) -> Measured {
        Measured { name, addrs: vec![Ipv4Addr::new(192, 0, 2, name.len() as u8)], tried, ok, ms, listed: false }
    }

    fn round(fastly: Measured) -> Vec<Measured> {
        vec![m("Akamai", 12, 12, Some(10)), m("Cloudflare", 6, 6, Some(12)), fastly]
    }

    #[test]
    fn failing_and_much_slower_cdns_get_their_verdict() {
        let v = verdicts(&round(m("Fastly", 3, 12, Some(900)))).unwrap();
        assert_eq!(v, vec![("Akamai", Verdict::Use), ("Cloudflare", Verdict::Use), ("Fastly", Verdict::Failing)]);
        let v = verdicts(&round(m("Fastly", 12, 12, Some(80)))).unwrap();
        assert_eq!(v[2], ("Fastly", Verdict::Slower));
        // a little slower is still used: 3 times as long, but not 50 ms more
        let v = verdicts(&round(m("Fastly", 12, 12, Some(40)))).unwrap();
        assert_eq!(v[2], ("Fastly", Verdict::Use));
        // 11 of 12 is fine, 10 of 12 is not
        assert_eq!(verdicts(&round(m("Fastly", 11, 12, Some(17)))).unwrap()[2].1, Verdict::Use);
        assert_eq!(verdicts(&round(m("Fastly", 10, 12, Some(17)))).unwrap()[2].1, Verdict::Failing);
    }

    #[test]
    fn with_no_cdn_answering_well_nothing_is_decided() {
        let down = vec![m("Akamai", 0, 12, None), m("Fastly", 1, 6, Some(30))];
        assert_eq!(verdicts(&down), None);
        let mut c = Choice::default();
        assert!(c.update(&down).is_empty());
        assert!(c.skipped().is_empty());
    }

    #[test]
    fn a_cdn_not_measured_or_with_no_address_gets_no_verdict() {
        let r = vec![
            m("Akamai", 12, 12, Some(10)),
            m("Fastly", 0, 0, None),
            Measured { addrs: vec![], ..m("Google", 0, 0, None) },
        ];
        assert_eq!(verdicts(&r).unwrap(), vec![("Akamai", Verdict::Use)]);
    }

    #[test]
    fn the_first_round_decides_at_once_later_ones_need_two_in_a_row() {
        let mut c = Choice::default();
        let changes = c.update(&round(m("Fastly", 2, 12, Some(17))));
        assert_eq!(changes.len(), 1);
        assert_eq!(changes[0].text, "audio CDN Fastly skipped: 2 of 12 connections worked");
        assert_eq!(c.routes(), vec!["192.0.2.6/32".to_string()]);

        // healthy again: used again only after the second round that says so
        let good = round(m("Fastly", 12, 12, Some(17)));
        assert!(c.update(&good).is_empty());
        assert!(c.changing());
        let changes = c.update(&good);
        assert_eq!(changes[0].text, "audio CDN Fastly used again: 12 of 12 connections, 17 ms");
        assert!(c.skipped().is_empty() && !c.changing());

        // one bad round, then a good one: nothing happens
        assert!(c.update(&round(m("Fastly", 0, 12, None))).is_empty());
        assert!(c.update(&good).is_empty());
        assert!(c.skipped().is_empty() && !c.changing());

        // slower twice in a row: skipped, and the line names the best one
        let slow = round(m("Fastly", 12, 12, Some(200)));
        assert!(c.update(&slow).is_empty());
        assert_eq!(c.update(&slow)[0].text, "audio CDN Fastly skipped: 200 ms, Akamai 10 ms");
    }

    #[test]
    fn a_skipped_cdn_keeps_its_routes_when_not_measured_or_not_resolved() {
        let mut c = Choice::default();
        c.update(&round(m("Fastly", 0, 12, None)));
        let routes = c.routes();
        // something plays: the skipped CDN is not measured, so it stays skipped
        assert!(c.update(&round(m("Fastly", 0, 0, None))).is_empty());
        // its names resolved to nothing this time: the addresses from before stay
        let unresolved = Measured { addrs: vec![], ..m("Fastly", 0, 0, None) };
        assert!(c.update(&round(unresolved)).is_empty());
        assert_eq!(c.routes(), routes);
        // other addresses in a later answer are added to the ones it has
        let mut other = m("Fastly", 0, 12, None);
        other.addrs = vec![Ipv4Addr::new(198, 51, 100, 1)];
        c.update(&round(other));
        assert_eq!(c.routes(), vec!["192.0.2.6/32".to_string(), "198.51.100.1/32".to_string()]);
        // a skipped one that is now slower rather than failing stays skipped, without a change
        assert!(c.update(&round(m("Fastly", 12, 12, Some(500)))).is_empty());
        assert_eq!(c.skipped()[0].verdict, Verdict::Slower);
    }

    #[test]
    fn librespot_moving_on_from_a_cdn_asks_for_a_round() {
        let _ = take_trouble();
        note_line("[2026-11-02T09:55:45Z WARN  librespot_audio::fetch] Fetching https://[hidden] failed with error Error { kind: Unavailable, error: hyper_util::client::legacy::Error(Connect, ConnectError(\"tcp connect error\", 199.232.214.248:443, Os { code: 101, kind: NetworkUnreachable })) }, trying next");
        assert!(!take_trouble(), "a fail-fast route at work is no trouble");
        note_line("[2026-11-02T09:55:45Z WARN  librespot_audio::fetch] Fetching https://[hidden] failed with error Error { kind: DeadlineExceeded, error: WaitTimeout }, trying next");
        assert!(take_trouble());
        assert!(!take_trouble());
    }

    #[test]
    fn the_median_is_the_lower_middle() {
        assert_eq!(median(&mut []), None);
        assert_eq!(median(&mut [30, 10, 20]), Some(20));
        assert_eq!(median(&mut [40, 10, 30, 20]), Some(20));
    }
}
