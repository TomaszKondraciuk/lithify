// Lithify web page: status, tests, settings and updates for one speaker. No dependencies; every
// value from the speaker is inserted with textContent, never as HTML.
'use strict';
(() => {
  // This browser's own choices on the page (language, theme); without storage, this visit only.
  const stored = (key) => { try { return localStorage.getItem(key) || ''; } catch (_) { return ''; } };
  const store = (key, value) => {
    try { if (value) localStorage.setItem(key, value); else localStorage.removeItem(key); } catch (_) { /* not kept */ }
  };
  const chosenLang = stored('lithify-lang');
  const LANG = chosenLang === 'pl' || chosenLang === 'en' ? chosenLang : /^pl\b/i.test(navigator.language || '') ? 'pl' : 'en';

  const T = {
    en: {
      pin: 'PIN', connecting: 'connecting…', offline: 'offline', restarting: 'restarting…',
      lang_group: 'Language', theme_group: 'Theme', theme_auto: 'Theme as on this device', theme_light: 'Light theme',
      theme_dark: 'Dark theme', speaker: 'Speaker',
      speaker_help: 'The speaker’s name, as in the Lithe and Google Home apps (it is changed there)',
      all_ok: 'all good', attention: 'needs attention',
      p_lr_down: 'Spotify Connect (librespot) is not running.',
      p_account: 'Spotify account not linked yet: pick the speaker once in the Spotify app.',
      p_signal: 'Weak Wi-Fi signal: music may stutter.', p_official: 'The official Spotify is not running.',
      p_sys_busy: 'High CPU load on the speaker.', p_sys_low_mem: 'The speaker is low on memory.',
      p_sys_low_disk: 'Too little storage for the next update.',
      p_same_name: 'Another device on the network is also called “{0}”: {1}. Rename one of them in the Google Home app so they are not mixed up.',
      lr_title: 'Spotify Connect (librespot)', off_title: 'Official Spotify', net_title: 'Network',
      upd_title: 'Updates', sys_title: 'System', logs_title: 'Logs', set_title: 'Settings',
      running: 'running', starting: 'starting', stopped: 'stopped',
      test: 'Run a check', restart: 'Restart', restart_lr: 'Restart librespot', restart_off: 'Restart official Spotify',
      test_net: 'Check connections', check: 'Check for updates',
      update_all: 'Update everything', upd_more: 'More options', install: "Install the computer's build",
      build: 'Build again (without installing)', rollback: 'Roll back to the previous version',
      rollback_to: 'Roll back to the previous version ({0})', rollback_lr: 'Roll back to librespot {0}',
      build_of: 'build of {0}', restart_agent: 'Restart agent', load_logs: 'Show logs', log_source: 'Log source',
      log_lithify: 'librespot + agent', log_agent: 'agent only', log_official: 'official Spotify',
      footer: 'Lithify runs on the speaker; new versions are built on your computer.',
      k_seen_as: 'In Spotify as', k_version: 'Version', k_account: 'Spotify account', k_uptime: 'Running for',
      k_now: 'Now', now_lr: 'playing', now_paused: 'paused', now_other: 'another source is playing', now_idle: 'nothing playing',
      account_saved: 'saved – connects by itself', account_none: 'not linked yet – pick the speaker once in the Spotify app',
      off_note: 'Built into the speaker firmware (old, cannot be updated). Lithify restarts it when it hangs.',
      off_hidden: 'hidden', off_note_hidden: 'Hidden in the Spotify app while Lithify works (setting “Hide the official Spotify”). It comes back by itself when librespot stops working for more than {0}.',
      k_restarts: 'Restarts by Lithify',
      k_volume: 'Speaker volume', k_libre_source: 'Libre source', k_pcm: 'Audio output (PCM)',
      k_libre_state: 'Libre state', st0: 'playing', st1: 'stopped', st2: 'paused', st3: 'connecting', st4: 'receiving', st5: 'buffering',
      k_wifi: 'Wi-Fi', k_band: 'Band', k_signal: 'Signal', k_rate: 'Link rate', k_ip: 'IP address', k_failfast: 'Skipped CDN servers',
      band_24: '2.4 GHz (channel {0})', band_5: '5 GHz (channel {0})',
      sig_excellent: 'excellent', sig_good: 'good', sig_fair: 'fair', sig_weak: 'weak', sig_pill: '{0} signal',
      ff_some: '{0} ({1} addresses blocked)',
      hint_24: 'The speaker uses 2.4 GHz. If your router offers 5 GHz, moving the speaker there usually makes streaming steadier.',
      testing: 'testing…', testing_net: 'connecting to the Spotify services…',
      th_host: 'Host', th_what: 'Service', th_result: 'Result', th_time: 'Time',
      v_ok: 'OK', v_slow: 'slow', v_failfast: 'skipped on purpose (fail-fast)', v_failing: 'no connection', v_dns: 'name not resolved',
      net_ok: 'All Spotify services answer.', net_bad: '{0} of {1} services do not answer.',
      net_legend: '“Skipped on purpose” are CDN addresses your settings mark unreachable, so Spotify moves on to another one at once.',
      lr_ok: 'librespot answers ({0} ms) and is visible in Spotify Connect.', lr_not_running: 'librespot is not running.',
      lr_no_zeroconf: 'librespot runs, but its discovery service does not answer yet.',
      lr_log: 'Since {0}: {1} buffer underruns, {2} warnings, {3} errors.', lr_log_expected: '(not counting {0} expected messages)',
      log_summary: 'Errors: {0}, warnings: {1}, expected messages (dimmed): {2}. Expected are the CDN servers fail-fast skips, librespot’s known, harmless notes and the moment of silence when a track is switched.',
      log_official_note: 'The official Spotify marks every message “E” (error), ordinary playback events too – they are not failures.',
      confirm_restart_lr: 'Restart librespot? Playback through it stops for a few seconds.',
      confirm_restart_off: 'Restart the speaker’s official Spotify service?',
      confirm_restart_agent: 'Restart the agent? This page reconnects by itself.',
      ok_restart_lr: 'librespot is restarting.', ok_restart_off: 'Official Spotify restarted.', ok_restart_agent: 'The agent is restarting.',
      working: 'working…', error: 'Error', need_pin: 'This action needs the PIN – enter it at the top of the page.',
      e_timeout: 'the speaker did not answer in time', e_network: 'no connection to the speaker',
      e_reply: 'the speaker’s reply was cut off', online_again: 'connected again',
      pin_ok: 'PIN OK', pin_bad: 'wrong PIN', pin_locked: 'too many attempts – wait a while',
      th_component: 'Component', th_installed: 'Current', th_latest: 'Newest', th_status: 'Status',
      comp_librespot: 'librespot (Spotify Connect)', comp_rust: 'Rust (compiler)', comp_alsa_lib: 'alsa-lib (audio)',
      comp_deps: 'Libraries', comp_lithify: 'Lithify (agent and this page)', comp_firmware: 'Speaker firmware',
      deps_installed: 'as of {0}', deps_current: 'all current', deps_newer: '{0} newer',
      lithify_behind: '{0} newer changes on GitHub', changes_newer: '{0} newer changes',
      fw_none: 'nothing newer (Google Cast updater)', fw_app: 'newer available', comp_build: 'Build',
      s_current: 'current', s_outdated: 'update available', s_unknown: 'could not check', s_local: 'current (local copy)',
      s_ready: 'ready to install', s_fw_pending: 'the speaker installs it',
      upd_source: 'New versions are built by Lithify on your computer ({0}); the speaker gets them only from there, never straight from the internet.',
      upd_local_copy: 'Lithify on the computer is a local copy, not a clone from GitHub, so it cannot check for newer versions of itself.',
      upd_no_companion: 'Updates need Lithify running on your computer: `lithify serve --install-service`, then `lithify update` once.',
      upd_available: 'updates available', upd_current: 'up to date', upd_checking: 'checking…',
      upd_checked: 'Checked {0}.', upd_not_checked: 'Not checked yet.', just_now: 'just now',
      upd_result_current: 'Everything is up to date.',
      upd_result_available: 'Updates are available: “Update everything” installs them.',
      checking: 'checking for updates (up to a minute)…', upd_check_failed: 'Could not check for updates: {0}',
      bad_reply: 'the computer sent an unreadable reply',
      confirm_update: 'Update everything now? The computer builds the newest versions (a few minutes, up to 20 the first time), then the speaker installs them; Spotify Connect pauses for about a minute.',
      confirm_build: 'Build again on your computer now (without installing)?',
      build_starting: 'starting the build…', build_running: 'building since {0}…', build_ok: 'Build finished.',
      build_failed: 'The build failed.',
      confirm_install: "Install the computer's build now? Spotify Connect pauses for about a minute.",
      confirm_rollback: 'Go back to the previous version ({0})?',
      job_starting: 'starting…', job_running_install: 'installing – about a minute…', job_running_rollback: 'rolling back…',
      job_running_update: 'updating: building on the computer, then installing…',
      job_ok_install: 'Installed: librespot and the agent run the new version.',
      job_failed_install: 'Install failed – the previous version keeps running.',
      job_ok_rollback: 'Rolled back to the previous version.', job_failed_rollback: 'Rollback failed.',
      job_ok_update: 'Updated: the speaker runs the newest versions.',
      job_nothing_update: 'Everything was already up to date – nothing to install.',
      job_failed_update: 'The update failed – the previous version keeps running.',
      k_agent_uptime: 'Agent running for', k_load: 'CPU load', load_low: 'low', load_medium: 'moderate', load_high: 'high',
      k_mem: 'Memory', mem_fmt: '{0} free of {1}',
      k_shells: 'Stuck firmware shells stopped',
      k_disk: 'Free storage', k_firmware: 'Firmware build', k_install: 'Installed in',
      k_model: 'Model', k_cast_id: 'Google Cast ID', k_mac: 'MAC address',
      events: 'Recent events', loading: 'loading…', sys_more: 'Technical details',
      sys_ok: 'normal', sys_busy: 'high load', sys_low_mem: 'low memory', sys_low_disk: 'low storage',
      yesterday: 'yesterday',
      set_save: 'Save changes', set_undo: 'Undo changes', saving: 'saving…',
      set_unavailable: 'This speaker gets its settings from the computer until its next update (`lithify update`).',
      set_by: 'Last changed {1}, {0}.', by_page: 'on this page', by_computer: 'from the computer',
      by_agent: 'automatically: the last settings that worked were restored',
      saved: 'Saved: {0}.', nothing_changed: 'Nothing to save.',
      restart_lr_note: 'librespot restarts with the new settings (a few seconds).',
      restart_agent_note: 'The agent restarts with the new settings; this page reconnects.',
      port_moving: 'The page moves to port {0}…', set_invalid: 'Some values are not valid – see the marked fields.',
      grp_basic: 'Basics', grp_network: 'Network', grp_page: 'This page', grp_advanced: 'Advanced',
      grp_librespot: 'librespot', grp_watchdog: 'Official Spotify watchdog',
      keep_volume: 'keep the speaker’s current volume (recommended)', pin_clear: 'turn the PIN off',
      pin_unchanged: '(unchanged)', pin_none: '(none)', none: '(none)',
      s_name: 'Name in Spotify', h_name: 'How the speaker is listed in the Spotify app.',
      s_bitrate: 'Audio quality', c_bitrate_96: '96 kbit/s (saves data)', c_bitrate_160: '160 kbit/s (normal)',
      c_bitrate_320: '320 kbit/s (very high)',
      s_mixer: 'Volume control', c_mixer_alsa: 'Spotify slider = speaker volume (recommended)',
      c_mixer_softvol: 'librespot only (software)',
      s_volume_ctrl: 'Volume curve', c_volume_ctrl_linear: 'linear', c_volume_ctrl_log: 'logarithmic',
      c_volume_ctrl_cubic: 'cubic', c_volume_ctrl_fixed: 'fixed (no control)',
      s_initial_volume: 'Volume at start', s_normalisation: 'Even out loudness between songs',
      s_autoplay: 'Autoplay similar songs', c_autoplay_none: 'as set in the Spotify app', c_autoplay_on: 'always',
      c_autoplay_off: 'never',
      c_format_S16: '16 bit (recommended: the longest buffer)', c_format_S24: '24 bit (half the buffer)',
      c_format_S24_3: '24 bit packed (converted)', c_format_S32: '32 bit (for software volume; half the buffer)',
      c_format_F32: '32 bit float (converted)',
      c_format_F64: '64 bit float (converted)',
      s_device_type: 'Icon in Spotify', h_device_type: 'Roughly how the icon looks in the Spotify app. Types that make Spotify hide the device altogether (such as Cast audio) are left out.',
      icon_risky: 'With this type Spotify may not show the speaker at all.', c_device_type_speaker: 'speaker', c_device_type_avr: 'AV receiver',
      c_device_type_tv: 'TV', c_device_type_computer: 'computer', c_device_type_audiodongle: 'audio dongle',
      c_device_type_stb: 'set-top box', c_device_type_castaudio: 'Cast audio', c_device_type_castvideo: 'Cast video',
      c_device_type_tablet: 'tablet', c_device_type_smartphone: 'smartphone', c_device_type_gameconsole: 'game console',
      c_device_type_automobile: 'car', c_device_type_smartwatch: 'smartwatch', c_device_type_chromebook: 'Chromebook',
      c_device_type_carthing: 'Car Thing', s_backend: 'Audio backend', s_device: 'ALSA output device', s_format: 'Sample format',
      s_mixer_device: 'ALSA mixer card', s_mixer_control: 'ALSA volume control', s_zeroconf_port: 'Discovery port (zeroconf)',
      s_extra_args: 'More librespot options', h_extra_args: 'One per line, e.g. --enable-volume-normalisation.',
      s_ui_port: 'Port of this page', h_ui_port: 'After a change the page moves to the new address.',
      s_ui_pin: 'PIN for actions on this page', h_ui_pin: '4 to 12 digits; 6 or more are harder to guess. Status and tests stay open.',
      s_fastfail_hosts: 'CDN servers to skip (fail-fast)',
      h_fastfail_hosts: 'Servers your network cannot reach; Spotify switches to another one at once. One per line.',
      s_fastfail_routes: 'Addresses to skip (fail-fast)', h_fastfail_routes: 'IPv4 addresses or networks, e.g. 199.232.0.0/16.',
      s_spin_threshold_pct: 'Restart official Spotify at CPU use of', s_spin_seconds: '…lasting longer than',
      s_spin_cooldown_seconds: 'At most one such restart every', s_respawn_official_spotify: 'Start official Spotify again after a crash',
      s_respawn_grace_seconds: 'Wait before starting it again', s_silent_start_seconds: 'Official Spotify silent at track start for',
      s_silent_start_action: '…then', c_silent_start_action_log: 'only log it', c_silent_start_action_pause_resume: 'pause and resume',
      c_silent_start_action_next: 'next track', c_silent_start_action_seek0: 'restart the track',
      s_conflict_stop_librespot: 'Stop librespot when another source plays (Cast, AirPlay…)',
      s_hide_official_spotify: 'Hide the official Spotify while Lithify works',
      h_hide_official_spotify: 'Spotify then lists only the Lithify device of this speaker. The official one comes back by itself when librespot stops working.',
      s_wifi_scancfg: 'Wi-Fi scan tuning (experts)',
    },
    pl: {
      pin: 'PIN', connecting: 'łączenie…', offline: 'brak połączenia', restarting: 'restart…',
      lang_group: 'Język', theme_group: 'Motyw', theme_auto: 'Motyw jak na tym urządzeniu', theme_light: 'Jasny motyw',
      theme_dark: 'Ciemny motyw', speaker: 'Głośnik',
      speaker_help: 'Nazwa głośnika, taka jak w aplikacjach Lithe i Google Home (tam się ją zmienia)',
      all_ok: 'wszystko działa', attention: 'wymaga uwagi',
      p_lr_down: 'Spotify Connect (librespot) nie działa.',
      p_account: 'Konto Spotify jeszcze nie połączone: wybierz raz ten głośnik w aplikacji Spotify.',
      p_signal: 'Słaby sygnał Wi-Fi: muzyka może się zacinać.', p_official: 'Oficjalny Spotify nie działa.',
      p_sys_busy: 'Wysokie obciążenie procesora głośnika.', p_sys_low_mem: 'Głośnikowi brakuje pamięci.',
      p_sys_low_disk: 'Za mało miejsca na następną aktualizację.',
      p_same_name: 'W sieci jest inne urządzenie o tej samej nazwie „{0}”: {1}. Zmień nazwę jednego z nich w aplikacji Google Home, żeby ich nie mylić.',
      lr_title: 'Spotify Connect (librespot)', off_title: 'Oficjalny Spotify', net_title: 'Sieć',
      upd_title: 'Aktualizacje', sys_title: 'System', logs_title: 'Logi', set_title: 'Ustawienia',
      running: 'działa', starting: 'uruchamia się', stopped: 'zatrzymany',
      test: 'Sprawdź działanie', restart: 'Uruchom ponownie', restart_lr: 'Uruchom ponownie librespot',
      restart_off: 'Uruchom ponownie oficjalny Spotify', test_net: 'Sprawdź połączenia', check: 'Sprawdź aktualizacje',
      update_all: 'Zaktualizuj wszystko', upd_more: 'Więcej opcji', install: 'Zainstaluj kompilację z komputera',
      build: 'Zbuduj ponownie (bez instalacji)', rollback: 'Przywróć poprzednią wersję',
      rollback_to: 'Przywróć poprzednią wersję ({0})', rollback_lr: 'Przywróć librespot {0}',
      build_of: 'kompilacja z {0}', restart_agent: 'Uruchom ponownie agenta', load_logs: 'Pokaż logi', log_source: 'Źródło logów',
      log_lithify: 'librespot + agent', log_agent: 'tylko agent', log_official: 'oficjalny Spotify',
      footer: 'Lithify działa na głośniku; nowe wersje buduje Twój komputer.',
      k_seen_as: 'W Spotify jako', k_version: 'Wersja', k_account: 'Konto Spotify', k_uptime: 'Czas działania',
      k_now: 'Teraz', now_lr: 'gra', now_paused: 'wstrzymane', now_other: 'gra inne źródło', now_idle: 'nic nie gra',
      account_saved: 'zapisane – łączy się samo', account_none: 'jeszcze nie połączone – wybierz raz ten głośnik w aplikacji Spotify',
      off_note: 'Wbudowany w firmware głośnika (stary, nie da się go zaktualizować). Lithify uruchamia go ponownie, gdy się zawiesi.',
      off_hidden: 'ukryty', off_note_hidden: 'Ukryty w aplikacji Spotify, bo działa Lithify (ustawienie „Ukryj oficjalny Spotify”). Wróci sam, gdy librespot przestanie działać na dłużej niż {0}.',
      k_restarts: 'Restarty przez Lithify',
      k_volume: 'Głośność głośnika', k_libre_source: 'Źródło Libre', k_pcm: 'Wyjście audio (PCM)',
      k_libre_state: 'Stan Libre', st0: 'odtwarzanie', st1: 'zatrzymany', st2: 'pauza', st3: 'łączenie', st4: 'odbieranie', st5: 'buforowanie',
      k_wifi: 'Wi-Fi', k_band: 'Pasmo', k_signal: 'Sygnał', k_rate: 'Prędkość łącza', k_ip: 'Adres IP', k_failfast: 'Pomijane serwery CDN',
      band_24: '2,4 GHz (kanał {0})', band_5: '5 GHz (kanał {0})',
      sig_excellent: 'doskonały', sig_good: 'dobry', sig_fair: 'średni', sig_weak: 'słaby', sig_pill: '{0} sygnał',
      ff_some: '{0} (zablokowanych adresów: {1})',
      hint_24: 'Głośnik działa w paśmie 2,4 GHz. Jeśli router ma 5 GHz, przeniesienie tam głośnika zwykle stabilizuje odtwarzanie.',
      testing: 'testowanie…', testing_net: 'łączenie z usługami Spotify…',
      th_host: 'Host', th_what: 'Usługa', th_result: 'Wynik', th_time: 'Czas',
      v_ok: 'OK', v_slow: 'wolno', v_failfast: 'celowo pomijany (fail-fast)', v_failing: 'brak połączenia', v_dns: 'nazwa nierozwiązana',
      net_ok: 'Wszystkie usługi Spotify odpowiadają.', net_bad: 'Nie odpowiada usług: {0} z {1}.',
      net_legend: '„Celowo pomijany” to adresy CDN, które ustawienia oznaczają jako nieosiągalne – Spotify od razu przechodzi do innego.',
      lr_ok: 'librespot odpowiada ({0} ms) i jest widoczny w Spotify Connect.', lr_not_running: 'librespot nie działa.',
      lr_no_zeroconf: 'librespot działa, ale jego usługa wykrywania jeszcze nie odpowiada.',
      lr_log: 'Od {0}: niedobory bufora: {1}, ostrzeżenia: {2}, błędy: {3}.', lr_log_expected: '(pominięte oczekiwane komunikaty: {0})',
      log_summary: 'Błędy: {0}, ostrzeżenia: {1}, oczekiwane komunikaty (wyszarzone): {2}. Oczekiwane to serwery CDN pominięte przez fail-fast, znane, niegroźne komunikaty librespot i chwila ciszy przy przełączaniu utworu.',
      log_official_note: 'Oficjalny Spotify oznacza każdy komunikat literą „E” (błąd), także zwykłe zdarzenia odtwarzania – to nie są awarie.',
      confirm_restart_lr: 'Uruchomić ponownie librespot? Odtwarzanie przez niego zatrzyma się na kilka sekund.',
      confirm_restart_off: 'Uruchomić ponownie oficjalną usługę Spotify głośnika?',
      confirm_restart_agent: 'Uruchomić ponownie agenta? Strona połączy się sama.',
      ok_restart_lr: 'librespot uruchamia się ponownie.', ok_restart_off: 'Oficjalny Spotify uruchomiony ponownie.',
      ok_restart_agent: 'Agent uruchamia się ponownie.',
      working: 'pracuję…', error: 'Błąd', need_pin: 'Ta akcja wymaga PIN-u – wpisz go u góry strony.',
      e_timeout: 'głośnik nie odpowiedział na czas', e_network: 'brak połączenia z głośnikiem',
      e_reply: 'odpowiedź głośnika została ucięta', online_again: 'połączono ponownie',
      pin_ok: 'PIN poprawny', pin_bad: 'zły PIN', pin_locked: 'za dużo prób – odczekaj chwilę',
      th_component: 'Składnik', th_installed: 'Obecna', th_latest: 'Najnowsza', th_status: 'Stan',
      comp_librespot: 'librespot (Spotify Connect)', comp_rust: 'Rust (kompilator)', comp_alsa_lib: 'alsa-lib (dźwięk)',
      comp_deps: 'Biblioteki', comp_lithify: 'Lithify (agent i ta strona)', comp_firmware: 'Firmware głośnika',
      deps_installed: 'stan z {0}', deps_current: 'wszystkie aktualne', deps_newer: 'nowszych: {0}',
      lithify_behind: 'nowszych zmian na GitHubie: {0}', changes_newer: 'nowszych zmian: {0}',
      fw_none: 'brak nowszego (wg Google Cast)', fw_app: 'jest nowszy', comp_build: 'Kompilacja',
      s_current: 'aktualne', s_outdated: 'jest aktualizacja', s_unknown: 'nie sprawdzono', s_local: 'aktualne (kopia lokalna)',
      s_ready: 'gotowa do instalacji', s_fw_pending: 'głośnik zainstaluje sam',
      upd_source: 'Nowe wersje buduje Lithify na Twoim komputerze ({0}); głośnik pobiera je tylko stamtąd, nigdy bezpośrednio z internetu.',
      upd_local_copy: 'Lithify na komputerze to kopia lokalna, a nie klon z GitHuba, więc nie może sprawdzić, czy są jego nowsze wersje.',
      upd_no_companion: 'Aktualizacje wymagają Lithify uruchomionego na komputerze: `lithify serve --install-service`, a potem raz `lithify update`.',
      upd_available: 'są aktualizacje', upd_current: 'aktualne', upd_checking: 'sprawdzanie…',
      upd_checked: 'Sprawdzono {0}.', upd_not_checked: 'Jeszcze nie sprawdzono.', just_now: 'przed chwilą',
      upd_result_current: 'Wszystko jest aktualne.',
      upd_result_available: 'Są aktualizacje: „Zaktualizuj wszystko” je zainstaluje.',
      checking: 'sprawdzanie aktualizacji (do minuty)…', upd_check_failed: 'Nie udało się sprawdzić aktualizacji: {0}',
      bad_reply: 'komputer odesłał nieczytelną odpowiedź',
      confirm_update: 'Zaktualizować teraz wszystko? Komputer zbuduje najnowsze wersje (kilka minut, za pierwszym razem do 20), potem głośnik je zainstaluje; Spotify Connect przerwie na około minutę.',
      confirm_build: 'Zbudować teraz ponownie na komputerze (bez instalacji)?',
      build_starting: 'uruchamianie kompilacji…', build_running: 'kompilacja trwa od {0}…', build_ok: 'Kompilacja zakończona.',
      build_failed: 'Kompilacja nie powiodła się.',
      confirm_install: 'Zainstalować teraz kompilację z komputera? Spotify Connect przerwie na około minutę.',
      confirm_rollback: 'Wrócić do poprzedniej wersji ({0})?',
      job_starting: 'uruchamianie…', job_running_install: 'instalacja – około minuty…', job_running_rollback: 'przywracanie…',
      job_running_update: 'aktualizacja: budowanie na komputerze, potem instalacja…',
      job_ok_install: 'Zainstalowano: librespot i agent działają w nowej wersji.',
      job_failed_install: 'Instalacja nie powiodła się – działa poprzednia wersja.',
      job_ok_rollback: 'Przywrócono poprzednią wersję.', job_failed_rollback: 'Przywracanie nie powiodło się.',
      job_ok_update: 'Zaktualizowano: głośnik działa w najnowszych wersjach.',
      job_nothing_update: 'Wszystko było aktualne – nie trzeba było niczego instalować.',
      job_failed_update: 'Aktualizacja nie powiodła się – działa poprzednia wersja.',
      k_agent_uptime: 'Czas działania agenta', k_load: 'Obciążenie CPU', load_low: 'niskie', load_medium: 'średnie',
      load_high: 'wysokie', k_mem: 'Pamięć',
      k_shells: 'Zatrzymane zawieszone powłoki firmware',
      mem_fmt: 'wolne {0} z {1}', k_disk: 'Wolne miejsce', k_firmware: 'Kompilacja firmware', k_install: 'Katalog instalacji',
      k_model: 'Model', k_cast_id: 'Identyfikator Google Cast', k_mac: 'Adres MAC',
      events: 'Ostatnie zdarzenia', loading: 'wczytywanie…', sys_more: 'Szczegóły techniczne',
      sys_ok: 'w normie', sys_busy: 'wysokie obciążenie', sys_low_mem: 'mało pamięci', sys_low_disk: 'mało miejsca',
      yesterday: 'wczoraj',
      set_save: 'Zapisz zmiany', set_undo: 'Cofnij zmiany', saving: 'zapisywanie…',
      set_unavailable: 'Ten głośnik dostaje ustawienia z komputera do najbliższej aktualizacji (`lithify update`).',
      set_by: 'Ostatnia zmiana: {1}, {0}.', by_page: 'na tej stronie', by_computer: 'z komputera',
      by_agent: 'automatycznie: przywrócono ostatnie działające ustawienia',
      saved: 'Zapisano: {0}.', nothing_changed: 'Nie ma nic do zapisania.',
      restart_lr_note: 'librespot uruchamia się ponownie z nowymi ustawieniami (kilka sekund).',
      restart_agent_note: 'Agent uruchamia się ponownie z nowymi ustawieniami; strona połączy się sama.',
      port_moving: 'Strona przenosi się na port {0}…', set_invalid: 'Niektóre wartości są niepoprawne – zobacz zaznaczone pola.',
      grp_basic: 'Podstawowe', grp_network: 'Sieć', grp_page: 'Ta strona', grp_advanced: 'Zaawansowane',
      grp_librespot: 'librespot', grp_watchdog: 'Pilnowanie oficjalnego Spotify',
      keep_volume: 'zachowaj obecną głośność głośnika (zalecane)', pin_clear: 'wyłącz PIN',
      pin_unchanged: '(bez zmian)', pin_none: '(brak)', none: '(brak)',
      s_name: 'Nazwa w Spotify', h_name: 'Tak głośnik widać w aplikacji Spotify.',
      s_bitrate: 'Jakość dźwięku', c_bitrate_96: '96 kbit/s (oszczędna)', c_bitrate_160: '160 kbit/s (normalna)',
      c_bitrate_320: '320 kbit/s (bardzo wysoka)',
      s_mixer: 'Regulacja głośności', c_mixer_alsa: 'suwak w Spotify = głośność głośnika (zalecane)',
      c_mixer_softvol: 'tylko w librespot (programowa)',
      s_volume_ctrl: 'Krzywa głośności', c_volume_ctrl_linear: 'liniowa', c_volume_ctrl_log: 'logarytmiczna',
      c_volume_ctrl_cubic: 'sześcienna', c_volume_ctrl_fixed: 'stała (bez regulacji)',
      s_initial_volume: 'Głośność po starcie', s_normalisation: 'Wyrównuj głośność utworów',
      s_autoplay: 'Autoodtwarzanie podobnych utworów', c_autoplay_none: 'jak w aplikacji Spotify', c_autoplay_on: 'zawsze',
      c_autoplay_off: 'nigdy',
      c_format_S16: '16 bitów (zalecane: najdłuższy bufor)', c_format_S24: '24 bity (połowa bufora)',
      c_format_S24_3: '24 bity spakowane (przeliczane)', c_format_S32: '32 bity (przy głośności programowej; połowa bufora)',
      c_format_F32: '32 bity zmiennoprzecinkowe (przeliczane)',
      c_format_F64: '64 bity zmiennoprzecinkowe (przeliczane)',
      s_device_type: 'Ikona w Spotify', h_device_type: 'Tak w przybliżeniu wygląda ikona w aplikacji Spotify. Pominięte są typy, przy których Spotify w ogóle nie pokazuje urządzenia (np. Cast audio).',
      icon_risky: 'Przy tym typie Spotify może w ogóle nie pokazywać głośnika.', c_device_type_speaker: 'głośnik', c_device_type_avr: 'amplituner AV',
      c_device_type_tv: 'telewizor', c_device_type_computer: 'komputer', c_device_type_audiodongle: 'przystawka audio',
      c_device_type_stb: 'dekoder TV', c_device_type_castaudio: 'Cast audio', c_device_type_castvideo: 'Cast wideo',
      c_device_type_tablet: 'tablet', c_device_type_smartphone: 'smartfon', c_device_type_gameconsole: 'konsola do gier',
      c_device_type_automobile: 'samochód', c_device_type_smartwatch: 'smartwatch', c_device_type_chromebook: 'Chromebook',
      c_device_type_carthing: 'Car Thing', s_backend: 'Sterownik dźwięku', s_device: 'Urządzenie wyjściowe ALSA', s_format: 'Format próbek',
      s_mixer_device: 'Karta miksera ALSA', s_mixer_control: 'Regulator głośności ALSA', s_zeroconf_port: 'Port wykrywania (zeroconf)',
      s_extra_args: 'Dodatkowe opcje librespot', h_extra_args: 'Po jednej w linii, np. --enable-volume-normalisation.',
      s_ui_port: 'Port tej strony', h_ui_port: 'Po zmianie strona przeniesie się pod nowy adres.',
      s_ui_pin: 'PIN do akcji na tej stronie', h_ui_pin: 'Od 4 do 12 cyfr; 6 i więcej trudniej odgadnąć. Stan i testy zostają dostępne.',
      s_fastfail_hosts: 'Pomijane serwery CDN (fail-fast)',
      h_fastfail_hosts: 'Serwery, z którymi Twoja sieć nie może się połączyć; Spotify od razu użyje innego. Po jednym w linii.',
      s_fastfail_routes: 'Pomijane adresy (fail-fast)', h_fastfail_routes: 'Adresy IPv4 lub sieci, np. 199.232.0.0/16.',
      s_spin_threshold_pct: 'Restart oficjalnego Spotify przy zużyciu CPU', s_spin_seconds: '…trwającym dłużej niż',
      s_spin_cooldown_seconds: 'Najwyżej jeden taki restart co', s_respawn_official_spotify: 'Uruchamiaj ponownie oficjalny Spotify po awarii',
      s_respawn_grace_seconds: 'Odczekaj przed ponownym uruchomieniem', s_silent_start_seconds: 'Oficjalny Spotify milczy na starcie utworu przez',
      s_silent_start_action: '…wtedy', c_silent_start_action_log: 'tylko zapisz w logu', c_silent_start_action_pause_resume: 'pauza i wznowienie',
      c_silent_start_action_next: 'następny utwór', c_silent_start_action_seek0: 'utwór od początku',
      s_conflict_stop_librespot: 'Zatrzymaj librespot, gdy gra inne źródło (Cast, AirPlay…)',
      s_hide_official_spotify: 'Ukryj oficjalny Spotify, gdy działa Lithify',
      h_hide_official_spotify: 'W aplikacji Spotify zostanie tylko urządzenie Lithify tego głośnika. Oficjalne wróci samo, gdy librespot przestanie działać.',
      s_wifi_scancfg: 'Strojenie skanowania Wi-Fi (dla ekspertów)',
    },
  };

  // Service names reported by the agent's network test.
  const PL_LABELS = {
    'access point list': 'lista punktów dostępowych',
    'access point (port 4070)': 'punkt dostępowy (port 4070)',
    'access point (port 443)': 'punkt dostępowy (port 443)',
    'Spotify API': 'API Spotify',
    'Spotify Connect channel': 'kanał Spotify Connect',
    'audio CDN (Akamai)': 'CDN audio (Akamai)',
    'audio CDN (Fastly)': 'CDN audio (Fastly)',
    'cover art': 'okładki',
    'fail-fast host (config)': 'host fail-fast (ustawienia)',
  };

  const has = (key) => key in T[LANG] || key in T.en;
  const t = (key, ...args) =>
    String(T[LANG][key] ?? T.en[key] ?? key).replace(/\{(\d)\}/g, (_, i) => String(args[Number(i)] ?? ''));
  const $ = (id) => document.getElementById(id);

  function el(tag, props, ...kids) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(props || {})) {
      if (v == null || v === false) continue;
      if (k === 'class') e.className = v;
      else if (k === 'text') e.textContent = v;
      else e.setAttribute(k, v === true ? '' : v);
    }
    for (const kid of kids) if (kid != null) e.append(kid);
    return e;
  }

  // The PIN is kept for this tab once the speaker has accepted it. A wrong one is never kept: every
  // request carrying it would count as one more miss and lock this device out for longer.
  let pin = '';
  try { pin = sessionStorage.getItem('lithify-pin') || ''; } catch (_) { /* storage blocked: ask again */ }
  let pinOk = Boolean(pin);
  function keepPin(value) {
    pin = value;
    pinOk = Boolean(value);
    try {
      if (value) sessionStorage.setItem('lithify-pin', value);
      else sessionStorage.removeItem('lithify-pin');
    } catch (_) { /* this page only */ }
  }
  let st = null; // last /api/status
  let check = null; // last update check
  let settings = null; // last /api/settings
  // When an install/rollback/update started here (or was last seen running) whose result is not
  // shown yet; given up after half an hour (the result stays in the job's log).
  let jobSince = 0;
  const watchingJob = () => jobSince > 0 && Date.now() - jobSince < 30 * 60000;
  let timer = 0;
  let failures = 0; // status requests in a row without an answer
  let seq = 0; // numbers the status requests: an answer that comes late must not undo a newer one
  let restartingUntil = 0; // until then no answer means the agent restarts on purpose, not "offline"
  let pageBuild = null; // the agent build this page came from (the page is part of the agent)
  let shownJob = ''; // `finished` of the job result on screen…
  let shownAt = 0; // …and when it appeared
  let autoChecked = false;
  let checking = null; // the update check under way (its promise), whoever started it
  let recheckAfter = null; // the agent (pid) whose restart, after an install, calls for a new check
  const inflight = new Set(); // actions still running (their buttons stay disabled)

  // How long a request may take before the page gives up on it: a speaker that does not answer must
  // not leave a button spinning. (The update check waits for the computer, up to five minutes.)
  const TIMEOUT = { status: 8000, read: 20000, action: 30000, nettest: 60000, check: 330000 };
  function deadline(ms) {
    if (AbortSignal.timeout) return AbortSignal.timeout(ms);
    const c = new AbortController();
    setTimeout(() => c.abort(new DOMException('timeout', 'TimeoutError')), ms);
    return c.signal;
  }

  async function api(path, method = 'GET', form = null, ms = method === 'POST' ? TIMEOUT.action : TIMEOUT.read) {
    const headers = {};
    if (method === 'POST') {
      headers['X-Lithify'] = '1';
      if (pin) headers['X-Lithify-Pin'] = pin;
    }
    const opts = { method, headers, cache: 'no-store', signal: deadline(ms) };
    if (form) opts.body = form; // URLSearchParams: sent as a form
    let r = null;
    let body;
    try {
      r = await fetch(path, opts);
      body = (r.headers.get('content-type') || '').includes('json') ? await r.json() : await r.text();
    } catch (e) {
      // No answer in time, no connection, or an answer cut off (say, by the agent restarting).
      const name = e && e.name;
      const err = new Error(t(name === 'TimeoutError' || name === 'AbortError' ? 'e_timeout' : r ? 'e_reply' : 'e_network'));
      err.status = r && !r.ok ? r.status : 0;
      throw err;
    }
    if (!r.ok) {
      const e = new Error((body && body.error) || (typeof body === 'string' && body.trim()) || `HTTP ${r.status}`);
      e.status = r.status;
      e.body = body;
      throw e;
    }
    return body;
  }

  // ── small renderers ──
  // Poll after poll only what differs is changed: a selection, a scroll position or an open tooltip
  // survives, and screen readers are not told the same thing again.
  const setText = (node, text) => {
    if (node.textContent !== text) node.textContent = text;
  };
  const setProp = (node, prop, value) => {
    if (node[prop] !== value) node[prop] = value;
  };
  function show(id, ...nodes) {
    const box = $(id);
    box.replaceChildren(...nodes.filter(Boolean));
    delete box.dataset.shown;
  }
  // A message the polls repeat, drawn again only when its key changes.
  function showOnce(id, key, ...nodes) {
    if ($(id).dataset.shown === key) return;
    show(id, ...nodes);
    $(id).dataset.shown = key;
  }
  // A log under a box's message (a build, an install): one <pre> per box whose text grows in place,
  // stays scrolled to the end unless the reader scrolled up, and is not read out line by line.
  const tails = {};
  function tail(id, text) {
    const box = $(id);
    const pre = tails[id] || (tails[id] = el('pre', { 'aria-live': 'off' }));
    if (!text) {
      pre.remove();
      return;
    }
    if (pre.parentNode !== box) {
      box.append(pre);
      pre.textContent = text;
      pre.scrollTop = pre.scrollHeight;
      return;
    }
    if (pre.textContent === text) return;
    const atEnd = pre.scrollHeight - pre.scrollTop - pre.clientHeight < 24;
    pre.textContent = text;
    if (atEnd) pre.scrollTop = pre.scrollHeight;
  }
  // What changes by itself (the verdict, the connection, the PIN), said once to screen readers.
  function announce(text) {
    const box = $('announce');
    box.textContent = '';
    if (text) setTimeout(() => { box.textContent = text; }, 60); // (so the same words again are said again)
  }
  const msg = (text, kind = '') => el('div', { class: `msg ${kind}`, text });
  const busy = (text) => el('div', { class: 'msg spin', text });
  const note = (text) => (text ? el('p', { class: 'hint', text }) : null);

  function failed(e) {
    if (e.status === 401) {
      keepPin(''); // wrong, or changed meanwhile: not sent again
      $('pin').value = '';
      askPin();
      return msg(t('need_pin'), 'bad');
    }
    if (e.status === 429) return msg(t('pin_locked'), 'bad');
    return msg(`${t('error')}: ${e.message}`, 'bad');
  }

  // A theme chosen in the header (boot.js has set it before the page was drawn), or the system's
  // ("auto"): the colours, the pressed button, and the colour of the browser's own bars.
  const BAR = { light: '#ffffff', dark: '#171c23' };
  function applyTheme(choice) {
    const fixed = choice === 'light' || choice === 'dark';
    if (fixed) document.documentElement.dataset.theme = choice;
    else delete document.documentElement.dataset.theme;
    for (const m of document.querySelectorAll('meta[name="theme-color"]')) {
      m.content = BAR[fixed ? choice : m.media.includes('dark') ? 'dark' : 'light'];
    }
    for (const b of document.querySelectorAll('[data-theme-set]')) {
      b.setAttribute('aria-pressed', String(b.dataset.themeSet === (fixed ? choice : 'auto')));
    }
  }

  function pill(id, text, kind) {
    const p = $(id);
    setText(p, text || '');
    const cls = `pill ${kind || ''}`;
    if (p.className !== cls) p.className = cls;
    setProp(p, 'hidden', !text);
  }

  // The rows of a <dl>, in the elements already there.
  function fill(id, rows) {
    const d = $(id);
    let i = 0;
    for (const [k, v, cls] of rows) {
      if (v == null || v === '') continue;
      if (!d.children[i + 1]) d.append(el('dt'), el('dd'));
      setText(d.children[i], k);
      const dd = d.children[i + 1];
      setText(dd, String(v));
      if (dd.className !== (cls || '')) dd.className = cls || '';
      i += 2;
    }
    while (d.children.length > i) d.lastElementChild.remove();
  }

  function table(headKeys, rows) {
    return el('div', { class: 'scroll' }, el('table', null,
      el('thead', null, el('tr', null, ...headKeys.map((k) => el('th', { scope: 'col', text: t(k) })))),
      el('tbody', null, ...rows)));
  }

  function dur(s) {
    if (s == null) return '';
    s = Math.floor(s);
    const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
    if (d) return `${d} d ${h} h`;
    if (h) return `${h} h ${m} min`;
    return m ? `${m} min` : `${s} s`;
  }

  function size(kb) {
    if (!Number.isFinite(kb)) return '';
    if (kb >= 1048576) return `${num((kb / 1048576).toFixed(1))} GB`;
    return kb >= 1024 ? `${Math.round(kb / 1024)} MB` : `${kb} kB`;
  }

  // Times arrive in UTC ("2026-10-07T09:26:07Z", "2026-10-06 22:39:25Z"); people read them in their
  // own: today's as the time alone, older ones with the day.
  const LOCALE = LANG === 'pl' ? 'pl-PL' : undefined;
  function when(stamp) {
    const d = stamp ? new Date(String(stamp).trim().replace(' ', 'T')) : null;
    if (!d || Number.isNaN(d.getTime())) return stamp || '?';
    const time = d.toLocaleTimeString(LOCALE, { hour: '2-digit', minute: '2-digit' });
    const today = new Date();
    const days = Math.round((new Date(today).setHours(0, 0, 0, 0) - new Date(d).setHours(0, 0, 0, 0)) / 86400000);
    if (days === 0) return time;
    if (days === 1) return `${t('yesterday')}, ${time}`;
    const year = d.getFullYear() === today.getFullYear() ? undefined : 'numeric';
    return `${d.toLocaleDateString(LOCALE, { day: 'numeric', month: 'short', year })}, ${time}`;
  }

  // A calendar date ("2026-10-07") the way the page's language writes it.
  const dateOnly = (ymd) => new Date(`${ymd}T12:00:00Z`).toLocaleDateString(LOCALE, { day: 'numeric', month: 'short', year: 'numeric' });
  const num = (x) => Number(x).toLocaleString(LOCALE, { maximumFractionDigits: 2 });
  // The agent's events start with their UTC time.
  const eventText = (e) => e.replace(/^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\dZ) /, (_, stamp) => `${when(stamp)} – `);

  function band(mhz) {
    if (!mhz) return '';
    return mhz < 3000 ? t('band_24', Math.round((mhz - 2407) / 5)) : t('band_5', Math.round((mhz - 5000) / 5));
  }

  // About -67 dBm is the weakest signal that still streams reliably.
  function signalKind(dbm) {
    if (dbm == null) return '';
    return dbm >= -67 ? 'ok' : dbm >= -75 ? 'warn' : 'bad';
  }

  function signalQuality(dbm) {
    if (dbm == null) return '';
    return dbm >= -55 ? 'sig_excellent' : dbm >= -67 ? 'sig_good' : dbm >= -75 ? 'sig_fair' : 'sig_weak';
  }

  // How fresh something is ("3 minutes ago"); the exact time goes in the tooltip.
  function ago(stamp) {
    const s = (Date.now() - new Date(String(stamp || '').trim().replace(' ', 'T')).getTime()) / 1000;
    if (!Number.isFinite(s)) return when(stamp);
    if (s < 60) return t('just_now');
    const rtf = new Intl.RelativeTimeFormat(LANG, { numeric: 'auto' });
    if (s < 3600) return rtf.format(-Math.round(s / 60), 'minute');
    if (s < 86400) return rtf.format(-Math.round(s / 3600), 'hour');
    return rtf.format(-Math.round(s / 86400), 'day');
  }

  // ── status ──
  function render(s) {
    // After an update the agent serves a new page: load it, then show the result again.
    if (pageBuild !== null && s.agent.build !== pageBuild) {
      // Carry the result over only when that job caused this restart (it just finished).
      const fresh = shownJob && Date.now() - shownAt < 120000;
      try { if (fresh) sessionStorage.setItem('lithify-show-job', shownJob); } catch (_) { /* no storage */ }
      location.reload();
      return;
    }
    pageBuild = s.agent.build;
    st = s;
    const sp = s.speaker || {};
    const lr = s.librespot, of = s.official, a = s.audio, n = s.network, sy = s.system, ag = s.agent, w = s.watchdog;
    setText($('sp-name'), sp.name || lr.name || 'Lithe Audio');
    const title = sp.name ? `Lithify – ${sp.name}` : 'Lithify';
    if (document.title !== title) document.title = title;
    if (s.ui.pin_required) setProp($('pin-box'), 'hidden', false);

    // Spotify Connect: is it there (the pill), and what the speaker does now. Versions are in the
    // update table and the configured values in the settings, so neither is repeated here.
    const lrState = !lr.running ? ['stopped', 'bad'] : lr.zeroconf_ok ? ['running', 'ok'] : ['starting', 'warn'];
    pill('lr-pill', t(lrState[0]), lrState[1]);
    const pcmOpen = Boolean(a.pcm) && a.pcm !== 'closed';
    const now = lr.holds_pcm ? 'now_lr' : pcmOpen ? 'now_other'
      : lr.running && /^paused\b/.test(lr.last_event || '') ? 'now_paused' : 'now_idle';
    fill('lr-dl', [
      [t('k_seen_as'), lr.name],
      [t('k_account'), lr.credentials_saved ? t('account_saved') : t('account_none'), lr.credentials_saved ? '' : 'warn'],
      [t('k_now'), t(now), now === 'now_lr' ? 'ok' : ''],
      [t('k_volume'), a.volume != null ? `${a.volume}%` : ''],
      [t('k_uptime'), dur(lr.uptime_s)],
    ]);

    // Hidden on purpose (hide_official_spotify) is not a problem: say what it means instead.
    const offHidden = Boolean(of.hidden) && !of.running;
    pill('off-pill', t(offHidden ? 'off_hidden' : of.running ? 'running' : 'stopped'), offHidden ? '' : of.running ? 'ok' : 'warn');
    setText($('off-note'), offHidden
      ? t('off_note_hidden', dur(Number(settings && settings.values.respawn_grace_seconds) || 120)) : t('off_note'));
    setProp(document.querySelector('[data-act="restart-official"]'), 'hidden', Boolean(of.hidden));
    fill('off-dl', [
      [t('k_seen_as'), of.name],
      [t('k_version'), of.version && `eSDK ${of.version}`],
      [t('k_uptime'), dur(of.uptime_s)],
      [t('k_restarts'), String(w.spotify_restarts + w.spotify_launches)],
    ]);

    // The pill says how good the signal is, the row gives the number.
    const sig = signalKind(n.signal_dbm);
    const quality = signalQuality(n.signal_dbm);
    pill('net-pill', quality && t('sig_pill', t(quality)), sig);
    fill('net-dl', [
      [t('k_wifi'), n.ssid],
      [t('k_band'), band(n.freq_mhz)],
      [t('k_signal'), n.signal_dbm != null ? `${n.signal_dbm} dBm` : '', sig],
      [t('k_rate'), n.bitrate_mbps != null ? `${n.bitrate_mbps} Mbit/s` : ''],
      [t('k_ip'), n.ip],
      [t('k_failfast'), n.fastfail_hosts.length ? t('ff_some', n.fastfail_hosts.length, n.fastfail_routes.length) : ''],
    ]);
    const hint = $('net-hint');
    setText(hint, t('hint_24'));
    setProp(hint, 'hidden', !(n.freq_mhz && n.freq_mhz < 3000));

    renderUpdates();

    // System: the pill sums up the health; the numbers behind it follow.
    const load1 = parseFloat(String(sy.load || '').split(' ')[0]);
    const perCore = load1 / (sy.cpus || 1);
    const loadKey = !Number.isFinite(load1) ? '' : perCore >= 0.8 ? 'load_high' : perCore >= 0.4 ? 'load_medium' : 'load_low';
    const memLow = sy.mem_total_kb > 0 && sy.mem_available_kb / sy.mem_total_kb < 0.1;
    const diskKb = sy.disk_free_kb === '' ? NaN : Number(sy.disk_free_kb);
    const diskLow = diskKb < 32768; // an update needs about 16 MB next to the installed files
    const problem = loadKey === 'load_high' ? 'sys_busy' : memLow ? 'sys_low_mem' : diskLow ? 'sys_low_disk' : '';
    pill('sys-pill', t(problem || 'sys_ok'), problem ? 'warn' : 'ok');
    fill('sys-dl', [
      [t('k_uptime'), dur(sy.uptime_s)],
      [t('k_load'), loadKey && `${t(loadKey)} (${num(load1)})`, loadKey === 'load_high' ? 'warn' : ''],
      [t('k_mem'), sy.mem_total_kb ? t('mem_fmt', size(sy.mem_available_kb), size(sy.mem_total_kb)) : '', memLow ? 'warn' : ''],
      [t('k_disk'), size(diskKb), diskLow ? 'warn' : ''],
      [t('k_shells'), w.shells_stopped ? String(w.shells_stopped) : ''],
    ]);
    const state = a.libre.state;
    fill('sys-more', [
      // what tells this speaker from another one with the same name
      [t('k_model'), sp.model],
      [t('k_cast_id'), sp.cast_id],
      [t('k_mac'), sp.mac],
      [t('k_firmware'), sp.build],
      [t('k_agent_uptime'), dur(ag.uptime_s)],
      [t('k_install'), ag.base],
      [t('k_pcm'), a.pcm],
      [t('k_libre_source'), a.libre.source !== '' ? `#${a.libre.source}` : ''],
      [t('k_libre_state'), state === '' ? '' : has(`st${state}`) ? t(`st${state}`) : `#${state}`],
    ]);
    const events = w.events.slice(0, 6).map(eventText);
    const box = $('events');
    if (box.dataset.shown !== events.join('\n')) {
      box.dataset.shown = events.join('\n');
      box.replaceChildren(...(events.length
        ? [note(t('events')), el('ul', { class: 'events' }, ...events.map((e) => el('li', { text: e })))] : []));
    }

    // One verdict first (the pill at the top), and what needs attention, linked to its card. Not
    // while an update restarts things on purpose; a problem counts once two readings agree.
    const issues = [];
    if (!(s.job && s.job.running)) {
      if (!lr.running && !pcmOpen) issues.push(['p_lr_down', 'bad', 'card-lr']);
      if (!lr.credentials_saved) issues.push(['p_account', 'warn', 'card-lr']);
      if (sig === 'bad') issues.push(['p_signal', 'warn', 'card-net']);
      if (!of.running && !of.hidden) issues.push(['p_official', 'warn', 'card-off']);
      if (problem) issues.push([`p_${problem}`, 'warn', 'card-sys']);
      if (s.same_name && s.same_name.length) {
        issues.push(['p_same_name', 'warn', 'card-sys', sp.name || '?', s.same_name.join(', ')]);
      }
    }
    renderVerdict(issues);

    renderJob(s.job);
    if (!autoChecked && s.ui.companion && !(s.ui.pin_required && !pinOk)) {
      autoChecked = true;
      runCheck(false, true);
    }
    // After an install the agent restarts: what is installed changed, so check again once the new
    // one answers (a check sent before would be cut off by the restart).
    if (recheckAfter !== null && s.agent.pid !== recheckAfter) {
      recheckAfter = null;
      runCheck(false, true);
    }
  }

  let lastIssues = null; // the previous reading's issues (none yet: trust the first)…
  let shownIssues = null; // …and the ones on screen
  let connHold = 0; // what the PIN check said stays in the top pill until then
  function renderVerdict(issues) {
    const now = issues.map(([k]) => k).join(' ');
    const confirmed = lastIssues === null ? issues : issues.filter(([k]) => lastIssues.split(' ').includes(k));
    lastIssues = now;
    const worst = confirmed.some(([, kind]) => kind === 'bad') ? 'bad' : confirmed.length ? 'warn' : 'ok';
    if (Date.now() >= connHold) pill('conn', t(confirmed.length ? 'attention' : 'all_ok'), worst);
    const key = confirmed.map(([k, , , ...args]) => [k, ...args].join('|')).join(' ');
    if (key === shownIssues) return; // unchanged: a screen reader hears it once
    // Said when it changes; when the page opens, the banner is read with the page.
    if (shownIssues !== null) {
      announce(confirmed.length ? `${t('attention')}: ${confirmed.map(([k, , , ...args]) => t(k, ...args)).join(' ')}` : t('all_ok'));
    }
    shownIssues = key;
    const box = $('alerts');
    box.className = `alerts wide ${worst}`;
    box.replaceChildren(...(confirmed.length
      ? [el('ul', null, ...confirmed.map(([k, , card, ...args]) => el('li', null, el('a', { href: `#${card}`, text: t(k, ...args) }))))]
      : []));
    box.hidden = !confirmed.length;
  }

  // ── updates ──
  const rowOf = (name) => (check && check.check && check.check.ok ? (check.check.data.rows || []) : [])
    .find((r) => r.component === name);

  function updatesAvailable() {
    if (!check) return null;
    const rows = check.check && check.check.ok ? check.check.data.rows || [] : [];
    return Boolean(check.bundle_new) || rows.some((r) => r.status === 'outdated');
  }

  function renderUpdates() {
    if (!st) return;
    const v = st.versions, sp = st.speaker || {};
    // "Newest" is, first, what the computer has built and the speaker does not run yet ("ready to
    // install"); otherwise the newest upstream release the next build would take.
    const built = (check && check.latest && check.latest.ok && check.latest.data && check.latest.data.versions) || null;
    const cls = { current: 'ok', ready: 'warn', outdated: 'warn', unknown: 'muted', local: 'ok', fw_pending: 'warn' };
    const line = (key, installed, newest, status) => el('tr', { class: status === 'outdated' || status === 'ready' ? 'diff' : null },
      el('td', { text: t(`comp_${key}`) }),
      el('td', { text: installed || '–' }),
      el('td', { text: newest == null ? '–' : newest }),
      el('td', { class: cls[status] || 'muted', text: status ? t(`s_${status}`) : '–' }));
    let ready = false;
    const row = (key, installed, field, upstream, label = (x) => x) => {
      if (built && built[field] && built[field] !== v[field]) {
        ready = true;
        return line(key, installed, label(built[field], built), 'ready');
      }
      return line(key, installed, upstream ? upstream.newest : null, upstream && upstream.status);
    };
    const up = (name) => {
      const r = rowOf(name);
      // a branch followed (librespot's dev) also says how far it is ahead of our build
      return r && { newest: r.behind ? `${r.latest || '?'} · ${t('changes_newer', r.behind)}` : r.latest || '?', status: r.status };
    };
    const day = (deps) => ((deps || '').match(/\d{4}-\d\d-\d\d/) || [])[0];
    const depsLabel = (deps) => (day(deps) ? t('deps_installed', dateOnly(day(deps))) : deps);
    const deps = rowOf('dependencies');
    const me = rowOf('lithify');
    const lithifyLabel = (_, b) => `${b.lithify || '?'}${b.agent_build ? ` (${b.agent_build.slice(0, 7)})` : ''}`;
    const fwUpdate = sp.has_update === 'true';
    const rows = [
      row('librespot', lrVersion(v), 'librespot', up('librespot'), (_, b) => lrVersion(b)),
      row('rust', v.rust, 'rust', up('rust')),
      row('alsa_lib', v.alsa_lib, 'alsa_lib', up('alsa-lib')),
      row('deps', depsLabel(v.deps), 'deps', deps && {
        newest: deps.status === 'current' ? t('deps_current') : deps.status === 'outdated' ? t('deps_newer', (deps.items || []).length) : '?',
        status: deps.status,
      }, depsLabel),
      // Without an upstream (a local copy, not a clone from GitHub) Lithify cannot know of newer
      // versions of itself: the newest for this speaker is the computer's.
      row('lithify', lithifyLabel(null, v), 'agent_build', me && (me.latest == null
        ? { newest: built ? lithifyLabel(null, built) : null, status: 'local' }
        : { newest: me.status === 'outdated' ? t('lithify_behind', parseInt(me.latest, 10) || me.latest) : me.latest,
          status: me.status }), lithifyLabel),
    ];
    // The computer's files differ in a way none of the versions shows (the same versions built
    // again): say so instead of leaving the table at odds with "updates available".
    if (check && check.bundle_new && !ready && built) {
      rows.push(line('build', when(v.built), when(built.built), 'ready'));
    }
    // The firmware is Lithe's: the speaker's own Google Cast updater checks for it and installs it.
    rows.push(line('firmware', sp.cast ? `Cast ${sp.cast}` : '', t(fwUpdate ? 'fw_app' : 'fw_none'), fwUpdate ? 'fw_pending' : 'current'));
    const tableBox = $('ver-table');
    const sig = rows.map((r) => [r.className, ...[...r.cells].map((c) => `${c.className}=${c.textContent}`)].join('\t')).join('\n');
    if (tableBox.dataset.shown !== sig) {
      tableBox.dataset.shown = sig;
      tableBox.replaceChildren(
        el('thead', null, el('tr', null, ...['th_component', 'th_installed', 'th_latest', 'th_status'].map((k) => el('th', { scope: 'col', text: t(k) })))),
        el('tbody', null, ...rows));
    }

    const running = Boolean(st.job && st.job.running);
    const avail = updatesAvailable();
    for (const act of ['check', 'build', 'install', 'update-all']) {
      setProp(document.querySelector(`[data-act="${act}"]`), 'disabled', !st.ui.companion || running || inflight.has(act)
        || (act === 'check' && checking !== null));
    }
    // The main action stands out only when there is something to install.
    document.querySelector('[data-act="update-all"]').classList.toggle('primary', avail === true);
    // Rolling back makes sense only to a version that differs from the one running.
    const prev = st.prev_versions;
    const rb = $('btn-rollback');
    setProp(rb, 'hidden', !prev || samePrev(prev));
    setProp(rb, 'disabled', running || inflight.has('rollback'));
    if (prev) {
      setText(rb, prev.librespot && prev.librespot !== st.versions.librespot
        ? t('rollback_lr', lrVersion(prev)) : t('rollback_to', when(prev.built)));
    }
    const checked = check && check.check && check.check.ok && check.check.data.checked;
    const hint = $('upd-hint');
    setText(hint, st.ui.companion ? (checked ? t('upd_checked', ago(checked)) : t('upd_not_checked')) : t('upd_no_companion'));
    setProp(hint, 'title', checked ? when(checked) : '');
    // Where updates come from, in words: the computer (by its address; the port is technical).
    let computer = st.ui.companion_url;
    try { computer = new URL(st.ui.companion_url).hostname; } catch (_) { /* shown as it is */ }
    setText($('upd-source'), st.ui.companion ? [t('upd_source', computer),
      me && me.latest == null ? t('upd_local_copy') : ''].filter(Boolean).join(' ') : '');
    if (checking) pill('upd-pill', t('upd_checking'), '');
    else if (avail != null) pill('upd-pill', t(avail ? 'upd_available' : 'upd_current'), avail ? 'warn' : 'ok');
  }

  // librespot as built: a release ("v0.8.0"), or the branch it follows and the commit ("dev e023adb").
  const lrVersion = (v) => (v.librespot_commit && !/^v?\d/.test(v.librespot_ref || '')
    ? `${v.librespot_ref} ${v.librespot_commit.slice(0, 7)}` : v.librespot_ref || v.librespot);

  // What the rollback brings back: the librespot release when it differs, and the build time.
  const prevLabel = (prev) => [prev.librespot && prev.librespot !== st.versions.librespot
    ? `librespot ${lrVersion(prev)}` : '', t('build_of', when(prev.built))].filter(Boolean).join(', ');
  const samePrev = (prev) => ['built', 'agent_build', 'librespot'].every((k) => prev[k] === st.versions[k]);

  function renderJob(job) {
    if (!job || !job.kind) return;
    if (job.running) {
      jobSince = Date.now();
      showOnce('out-upd', `job ${job.kind} ${job.started}`, busy(t(`job_running_${job.kind}`)));
      tail('out-upd', job.output);
      return;
    }
    let pending = '';
    try { pending = sessionStorage.getItem('lithify-show-job') || ''; } catch (_) { /* no storage */ }
    if (watchingJob() || (pending && pending === job.finished)) {
      jobSince = 0;
      shownJob = job.finished;
      shownAt = Date.now();
      try { sessionStorage.removeItem('lithify-show-job'); } catch (_) { /* no storage */ }
      const key = job.ok ? (job.kind === 'update' && !job.installed ? 'job_nothing_update' : `job_ok_${job.kind}`)
        : `job_failed_${job.kind}`;
      show('out-upd', msg(t(key), job.ok ? 'ok' : 'bad'));
      // The status carries a job's output only while it runs; the whole log comes once, here.
      const finished = job.finished;
      api('/api/job').then((j) => { if (j && shownJob === finished) tail('out-upd', j.output); })
        .catch(() => { /* the message says what matters */ });
      if (job.ok && job.installed) recheckAfter = st ? st.agent.pid : null;
      else if (job.ok) runCheck(false, true);
    }
  }

  // One check at a time; a second request (the button during an automatic check) joins it.
  function runCheck(fresh, quiet) {
    if (checking) {
      if (!quiet) show('out-upd', busy(t('checking')));
      return checking.then(() => { if (!quiet) showCheck(); });
    }
    checking = doCheck(fresh, quiet).finally(() => {
      checking = null;
      renderUpdates();
    });
    renderUpdates();
    return checking;
  }

  function showCheck() {
    if (!check) return;
    const avail = updatesAvailable();
    show('out-upd',
      check.check.ok ? msg(t(avail ? 'upd_result_available' : 'upd_result_current'), avail ? 'warn' : 'ok')
        : msg(t('upd_check_failed', check.check.error), 'bad'),
      ...depsList());
  }

  async function doCheck(fresh, quiet) {
    if (!quiet) show('out-upd', busy(t('checking')));
    try {
      const c = await api(`/api/update/check${fresh ? '?fresh=1' : ''}`, 'POST', null, TIMEOUT.check);
      // The agent passes the companion's replies on as text; a broken one is just an error.
      for (const part of [c.latest, c.check]) {
        if (!part.ok) continue;
        try {
          part.data = JSON.parse(part.data);
        } catch (_) {
          Object.assign(part, { ok: false, error: t('bad_reply') });
        }
      }
      check = c;
      if (!quiet) showCheck();
    } catch (e) {
      if (!quiet) show('out-upd', failed(e));
    }
  }

  function depsList() {
    const deps = rowOf('dependencies');
    if (!deps || !(deps.items || []).length) return [];
    return [el('details', null, el('summary', { text: t('deps_newer', deps.items.length) }),
      el('pre', { text: deps.items.join('\n') }))];
  }

  async function refresh() {
    clearTimeout(timer);
    const mine = ++seq;
    let s;
    try {
      s = await api('/api/status', 'GET', null, TIMEOUT.status);
    } catch (_) {
      if (mine !== seq) return; // a newer reading is under way
      failures += 1;
      const restarting = watchingJob() || Date.now() < restartingUntil;
      if (restarting) {
        pill('conn', t('restarting'), 'warn');
      } else if (failures >= 2) {
        // One missed reading is a Wi-Fi hiccup; from the second the page says so (and greys out
        // the values it still shows).
        pill('conn', t('offline'), 'bad');
        if (!document.body.classList.contains('stale')) announce(t('offline'));
        document.body.classList.add('stale');
      }
      schedule(restarting ? 2000 : retryIn());
      return;
    }
    if (mine !== seq) return;
    failures = 0;
    if (document.body.classList.contains('stale')) announce(t('online_again'));
    document.body.classList.remove('stale');
    try {
      render(s);
    } catch (e) {
      console.error(e); // a fault of this page, not the speaker's: keep reading
    }
    schedule(watchingJob() ? 2000 : 5000);
  }

  // 4, 8, then every 15 s while the speaker does not answer (a restarted speaker is back on the page
  // soon), give or take 15 %, so pages open on several devices do not all knock at the same moment.
  const retryIn = () => Math.min(15000, 4000 * 2 ** Math.min(2, failures - 1)) * (0.85 + Math.random() * 0.3);

  function schedule(ms) {
    clearTimeout(timer);
    if (!document.hidden) timer = setTimeout(refresh, ms);
  }

  // ── settings ──
  // In sight: the everyday options and this page's own. The rest waits under "Advanced", in parts:
  // the network workarounds, librespot's audio options, the official Spotify watchdog.
  const OPEN_GROUPS = ['basic', 'page'];
  const ADVANCED_PARTS = ['network', 'librespot', 'watchdog'];
  const partOf = (o) => (o.group === 'network' || o.key === 'wifi_scancfg' ? 'network'
    : /^(spin_|respawn_|silent_start_)/.test(o.key) ? 'watchdog' : 'librespot');
  // Units stand next to the number, not in the label.
  const UNITS = { spin_threshold_pct: '%', spin_seconds: 's', spin_cooldown_seconds: 's', respawn_grace_seconds: 's',
    silent_start_seconds: 's' };
  // A few choices are easier to pick when all of them are in sight (radio buttons); longer lists
  // stay drop-downs.
  const RADIO_MAX = 4;
  const label = (key) => t(`s_${key}`);
  const choiceLabel = (key, c) => {
    const k = `c_${key}_${c || 'none'}`;
    return has(k) ? t(k) : c || t('none');
  };

  // Tried again after a failure (the agent restarting, a Wi-Fi hiccup): in 2 s, 4 s, … then every
  // 30 s, until the settings come.
  async function loadSettings(tries = 0) {
    try {
      settings = await api('/api/settings');
    } catch (e) {
      show('out-set', failed(e));
      setTimeout(() => loadSettings(tries + 1), Math.min(30000, 2000 * 2 ** tries));
      return;
    }
    if (tries) show('out-set'); // the error shown before is over
    renderSettings();
  }

  // The settings as the speaker keeps them, a moment after a save (values it normalised, who
  // changed them), tried again while the agent restarts. The form is drawn again only when nothing
  // is being edited in it.
  async function reloadSettings(delay, tries = 0) {
    await sleep(delay);
    let fresh;
    try {
      fresh = await api('/api/settings');
    } catch (_) {
      if (tries < 4) reloadSettings(1000 * 2 ** tries, tries + 1); // in 1, 2, 4 and 8 s
      return;
    }
    const editing = changes().length > 0;
    settings = fresh;
    if (editing) {
      settingsHint();
      updateDirty();
    } else {
      renderSettings();
    }
  }

  // The icon Spotify shows for each device type, drawn after the app's (24×24 outlines). Only the
  // types Spotify lists: with audiodongle, gameconsole, castaudio or castvideo it hides the
  // speaker from Spotify Connect altogether; stb gets the speaker's icon.
  const SPEAKER_ICON = ['M7 2h10a2 2 0 0 1 2 2v16a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2z',
    'M12 10a4 4 0 1 0 0 8a4 4 0 1 0 0-8z', 'M12 5.3a1.2 1.2 0 1 0 0 2.4a1.2 1.2 0 1 0 0-2.4z'];
  const ICONS = {
    speaker: SPEAKER_ICON,
    avr: ['M3.5 7h17A1.5 1.5 0 0 1 22 8.5v7a1.5 1.5 0 0 1-1.5 1.5h-17A1.5 1.5 0 0 1 2 15.5v-7A1.5 1.5 0 0 1 3.5 7z',
      'M17 9.5a2.5 2.5 0 1 0 0 5a2.5 2.5 0 1 0 0-5z', 'M5 10.5h6M5 13.5h4', 'M5 17v1.5M19 17v1.5'],
    tv: ['M3.5 4h17A1.5 1.5 0 0 1 22 5.5v10a1.5 1.5 0 0 1-1.5 1.5h-17A1.5 1.5 0 0 1 2 15.5v-10A1.5 1.5 0 0 1 3.5 4z',
      'M8 21h8M12 17v4'],
    stb: SPEAKER_ICON,
    computer: ['M5 4h14a1 1 0 0 1 1 1v10H4V5a1 1 0 0 1 1-1z', 'M2 18.5h20'],
    tablet: ['M6 2h12a2 2 0 0 1 2 2v16a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2z', 'M11 18.5h2'],
    smartphone: ['M9 2h6a2 2 0 0 1 2 2v16a2 2 0 0 1-2 2H9a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2z', 'M11 18.5h2'],
  };
  const UNKNOWN_ICON = ['M12 3a9 9 0 1 0 0 18a9 9 0 1 0 0-18z', 'M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .9-1 1.6v.6',
    'M12 17.2v.1'];

  function icon(paths) {
    const ns = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(ns, 'svg');
    for (const [k, v] of Object.entries({ viewBox: '0 0 24 24', 'aria-hidden': 'true', fill: 'none', stroke: 'currentColor',
      'stroke-width': '1.6', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' })) svg.setAttribute(k, v);
    for (const d of paths) {
      const p = document.createElementNS(ns, 'path');
      p.setAttribute('d', d);
      svg.append(p);
    }
    return svg;
  }

  // The device type as a row of icons to pick from; a type set elsewhere that Spotify may hide
  // stays visible here, marked.
  function iconPicker(o, id, value, help) {
    const set = el('fieldset', { class: 'field', 'data-key': o.key, role: 'radiogroup' }, el('legend', { text: label(o.key) }));
    const types = Object.keys(ICONS).filter((k) => o.choices.includes(k));
    if (value && !types.includes(value)) types.push(value);
    const tiles = el('div', { class: 'tiles' });
    for (const k of types) {
      const risky = !ICONS[k];
      const r = el('input', { type: 'radio', name: id, id: `${id}-${k}`, value: k, checked: k === value });
      tiles.append(el('label', { class: risky ? 'tile risky' : 'tile', for: r.id, title: risky ? t('icon_risky') : null },
        r, icon(ICONS[k] || UNKNOWN_ICON), choiceLabel(o.key, k)));
    }
    set.append(tiles);
    if (help) set.append(help);
    return set;
  }

  // The control a field's hint and errors describe: the input, or the group of radio buttons.
  const controlOf = (field) => (field.matches('fieldset') ? field : field.querySelector(`#set-${field.dataset.key}`));
  const describe = (control, id, add) => {
    const ids = (control.getAttribute('aria-describedby') || '').split(' ').filter((x) => x && x !== id);
    if (add) ids.push(id);
    if (ids.length) control.setAttribute('aria-describedby', ids.join(' '));
    else control.removeAttribute('aria-describedby');
  };

  function fieldFor(o) {
    const field = buildField(o);
    const help = field && field.querySelector(':scope > .help');
    const control = field && controlOf(field);
    if (help && control) {
      help.id = `set-${o.key}-help`;
      describe(control, help.id, true);
    }
    return field;
  }

  function buildField(o) {
    const id = `set-${o.key}`;
    const value = settings.values[o.key] ?? '';
    const wrap = el('div', { class: 'field', 'data-key': o.key });
    const add = (...nodes) => wrap.append(...nodes.filter(Boolean)); // (append(null) would show "null")
    const help = has(`h_${o.key}`) ? el('div', { class: 'help', text: t(`h_${o.key}`) }) : null;
    if (o.type === 'bool') {
      const box = el('input', { type: 'checkbox', id, checked: value === 'true' });
      add(el('label', { class: 'inline', for: id }, box, label(o.key)), help);
      return wrap;
    }
    if (o.type === 'choice' && o.choices.length < 2) return null; // nothing to choose
    if (o.key === 'device_type') return iconPicker(o, id, value, help);
    if (o.type === 'choice' && o.choices.length <= RADIO_MAX) {
      const set = el('fieldset', { class: 'field', 'data-key': o.key, role: 'radiogroup' }, el('legend', { text: label(o.key) }));
      for (const c of o.choices) {
        const r = el('input', { type: 'radio', name: id, id: `${id}-${c}`, value: c, checked: c === value });
        set.append(el('label', { class: 'inline', for: r.id }, r, choiceLabel(o.key, c)));
      }
      if (help) set.append(help);
      return set;
    }
    let input;
    if (o.type === 'volume') {
      // Keep the speaker's volume, or one of the usual levels (and the one set, when unusual).
      const levels = Array.from({ length: 20 }, (_, i) => String((i + 1) * 5));
      if (value && value !== 'current' && !levels.includes(value)) levels.push(value);
      levels.sort((a, b) => a - b);
      input = el('select', { id }, el('option', { value: 'current', text: t('keep_volume'), selected: value === 'current' }),
        ...levels.map((l) => el('option', { value: l, text: `${l}%`, selected: l === value })));
    } else if (o.type === 'choice') {
      input = el('select', { id }, ...o.choices.map((c) => el('option', { value: c, text: choiceLabel(o.key, c), selected: c === value })));
    } else if (o.type === 'list') {
      input = el('textarea', { id, rows: 3, spellcheck: 'false' });
      input.value = value.split(' ').filter(Boolean).join('\n');
    } else if (o.type === 'int') {
      input = el('input', { type: 'number', id, min: o.min, max: o.max, step: 1, value });
      if (UNITS[o.key]) input = el('span', { class: 'unit' }, input, UNITS[o.key]);
    } else if (o.key === 'ui_pin') {
      input = el('input', { type: 'password', id, inputmode: 'numeric', autocomplete: 'new-password',
        placeholder: t(settings.pin_set ? 'pin_unchanged' : 'pin_none') });
      add(el('label', { for: id, text: label(o.key) }), input);
      if (settings.pin_set) {
        const clear = el('input', { type: 'checkbox', id: `${id}-clear` });
        add(el('label', { class: 'inline', for: `${id}-clear` }, clear, t('pin_clear')));
      }
      add(help);
      return wrap;
    } else {
      input = el('input', { type: 'text', id, value, spellcheck: 'false' });
    }
    add(el('label', { for: id, text: label(o.key) }), input, help);
    return wrap;
  }

  function renderSettings() {
    const form = $('set-form');
    const hint = $('set-hint');
    const advancedOpen = Boolean(form.querySelector('details[open]')); // kept open across a save
    form.replaceChildren();
    if (!settings || !settings.available) {
      form.append(note(t('set_unavailable')));
      hint.hidden = true;
      updateDirty();
      return;
    }
    const fields = (opts) => el('div', { class: 'fields' }, ...opts.map(fieldFor));
    const group = (key, opts) => el('fieldset', null, el('legend', { text: t(`grp_${key}`) }), fields(opts));
    for (const g of OPEN_GROUPS) {
      const opts = settings.schema.filter((o) => o.group === g);
      if (opts.length) form.append(group(g, opts));
    }
    const advanced = settings.schema.filter((o) => o.group === 'network' || o.group === 'advanced');
    const parts = ADVANCED_PARTS.map((p) => [p, advanced.filter((o) => partOf(o) === p)]).filter(([, opts]) => opts.length);
    if (parts.length) {
      form.append(el('details', { open: advancedOpen }, el('summary', { text: t('grp_advanced') }),
        ...parts.map(([p, opts]) => group(p, opts))));
    }
    settingsHint();
    updateDirty();
  }

  function settingsHint() {
    const hint = $('set-hint');
    setText(hint, settings.updated ? t('set_by', t(`by_${settings.by || 'computer'}`), when(settings.updated)) : '');
    hint.hidden = !hint.textContent;
  }

  function fieldValue(o) {
    const picked = document.querySelector(`#set-form input[name="set-${o.key}"]:checked`);
    if (picked) return picked.value;
    const e = $(`set-${o.key}`);
    if (!e) return settings.values[o.key];
    if (o.type === 'bool') return e.checked ? 'true' : 'false';
    if (o.type === 'list') return e.value.split(/[\s,]+/).filter(Boolean).join(' ');
    return e.value.trim();
  }

  function changes() {
    if (!settings || !settings.available) return [];
    const out = [];
    for (const o of settings.schema) {
      if (o.group === 'hidden') continue;
      if (o.key === 'ui_pin') {
        const clear = $('set-ui_pin-clear');
        const v = $('set-ui_pin').value.trim();
        if (clear && clear.checked) out.push(['ui_pin', '']);
        else if (v) out.push(['ui_pin', v]);
        continue;
      }
      const v = fieldValue(o);
      if (v !== settings.values[o.key]) out.push([o.key, v]);
    }
    return out;
  }

  function updateDirty() {
    const ch = changes();
    const keys = new Set(ch.map(([k]) => k));
    for (const f of document.querySelectorAll('#set-form .field')) f.classList.toggle('changed', keys.has(f.dataset.key));
    const idle = !inflight.has('settings-save');
    setProp(document.querySelector('[data-act="settings-save"]'), 'disabled', !ch.length || !idle);
    setProp(document.querySelector('[data-act="settings-undo"]'), 'disabled', !ch.length || !idle);
  }

  function markErrors(errors) {
    for (const e of document.querySelectorAll('#set-form .err')) {
      const control = controlOf(e.closest('.field'));
      if (control) {
        describe(control, e.id, false);
        control.removeAttribute('aria-invalid');
      }
      e.remove();
    }
    let first = null;
    for (const [key, text] of Object.entries(errors || {})) {
      const f = document.querySelector(`#set-form .field[data-key="${key}"]`);
      if (!f) continue;
      const err = el('div', { class: 'err', id: `set-${key}-err`, text });
      f.append(err);
      const control = controlOf(f);
      if (control) {
        control.setAttribute('aria-invalid', 'true');
        describe(control, err.id, true);
      }
      const fold = f.closest('details'); // an error must not stay hidden in a closed section
      if (fold) fold.open = true;
      first = first || f;
    }
    // Take the reader to the first field to fix.
    if (first) (first.querySelector('input, select, textarea') || first).focus();
  }

  async function saveSettings() {
    const ch = changes();
    if (!ch.length) return show('out-set', msg(t('nothing_changed')));
    show('out-set', busy(t('saving')));
    markErrors({});
    let r;
    try {
      r = await api('/api/settings', 'POST', new URLSearchParams(ch));
    } catch (e) {
      if (e.status === 400 && e.body && e.body.errors) {
        markErrors(e.body.errors);
        show('out-set', msg(t('set_invalid'), 'bad'));
      } else {
        show('out-set', failed(e));
      }
      return;
    }
    const newPin = ch.find(([k]) => k === 'ui_pin');
    if (newPin) {
      keepPin(newPin[1]);
      $('pin').value = pin;
    }
    // What was saved is the speaker's now. The form is not drawn again under the reader's hands
    // (something may have been edited meanwhile): only the "changed" marks go.
    for (const [k, v] of ch) {
      if (k === 'ui_pin') settings.pin_set = v !== '';
      else settings.values[k] = v;
    }
    const pinField = $('set-ui_pin');
    if (pinField) pinField.value = '';
    if (changes().length) updateDirty();
    else renderSettings();
    const restarts = r.restart || [];
    if (restarts.includes('agent')) restartingUntil = Date.now() + 30000;
    show('out-set', msg(t('saved', (r.changed || []).map(label).join(', ')), 'ok'),
      restarts.includes('agent') ? note(t('restart_agent_note')) : restarts.includes('librespot') ? note(t('restart_lr_note')) : null);
    const port = ch.find(([k]) => k === 'ui_port');
    if (port) {
      const url = `${location.protocol}//${location.hostname}:${port[1]}/`;
      $('out-set').append(el('p', { class: 'hint' }, t('port_moving', port[1]), ' ', el('a', { href: url, text: url })));
      setTimeout(() => { location.href = url; }, 5000);
      return;
    }
    reloadSettings(restarts.includes('agent') ? 3000 : 1000);
    setTimeout(refresh, restarts.length ? 4000 : 1000);
  }

  // ── actions ──
  async function simple(out, path, okKey, busyKey) {
    show(out, busy(t(busyKey || 'working')));
    try {
      const r = await api(path, 'POST');
      show(out, msg(t(okKey), 'ok'), note(r.message));
      setTimeout(refresh, 1500);
      return true;
    } catch (e) {
      show(out, failed(e));
      return false;
    }
  }

  function netResults(r) {
    const results = r.results || [];
    const bad = results.filter((x) => x.verdict === 'failing' || x.verdict === 'dns');
    const cls = { ok: 'ok', slow: 'warn', failfast: 'muted', failing: 'bad', dns: 'bad' };
    const rows = results.map((x) => {
      const detail = x.verdict === 'failing' || x.verdict === 'dns'
        ? x.detail || x.addrs.map((ad) => `${ad.ip}: ${ad.detail || ad.result}`).join(', ')
        : '';
      return el('tr', null,
        el('td', { text: `${x.host}:${x.port}` }),
        el('td', { text: (LANG === 'pl' && PL_LABELS[x.label]) || x.label }),
        el('td', { class: cls[x.verdict], text: t(`v_${x.verdict}`) + (detail ? ` – ${detail}` : '') }),
        el('td', { text: x.best_ms != null ? `${x.best_ms} ms` : '' }));
    });
    return [
      msg(bad.length ? t('net_bad', bad.length, results.length) : t('net_ok'), bad.length ? 'bad' : 'ok'),
      table(['th_host', 'th_what', 'th_result', 'th_time'], rows),
      note(t('net_legend')),
    ];
  }

  const sleep = (ms) => new Promise((ok) => setTimeout(ok, ms));

  // What a log line means: a problem ('bad'), worth a look ('warn'), or expected here ('muted':
  // CDN servers the fail-fast routes skip, librespot 0.8's notes around playback; the agent's
  // expected_warning knows the same). The official client marks every line "E/", so nothing.
  const EXPECTED = [/librespot_audio::fetch.*NetworkUnreachable/, /context is not available/,
    /failed filling up next_track during stopping/, /Invalid start position of/];
  // Expected as well, as the agent counts them: the device running dry as the next track starts
  // (within 1.5 s of "loaded"), and the dealer of the session a new login replaced closing
  // (within 10 s of "Authenticated as").
  const lineMs = (l) => {
    const m = /^\d\d-\d\d (\d\d):(\d\d):(\d\d)\.(\d{3}) /.exec(l);
    return m ? ((Number(m[1]) * 60 + Number(m[2])) * 60 + Number(m[3])) * 1000 + Number(m[4]) : null;
  };
  function muteSwitches(lines, kinds) {
    let loaded = null;
    let login = null;
    const within = (at, now, ms) => at !== null && now !== null && now >= at && now - at <= ms;
    lines.forEach((l, i) => {
      if (/librespot_playback::player\] .* loaded$/.test(l)) loaded = lineMs(l);
      else if (/librespot_core::session\] Authenticated as/.test(l)) login = lineMs(l);
      else if (kinds[i] === 'warn') {
        const now = lineMs(l);
        if ((/underrun|Broken pipe/.test(l) && within(loaded, now, 1500))
          || (/librespot_core::dealer\] Websocket connection failed/.test(l) && within(login, now, 10000))) kinds[i] = 'muted';
      }
    });
    return kinds;
  }

  function logKind(line, source) {
    if (source === 'official') return '';
    if (/underrun|Broken pipe/.test(line)) return 'warn';
    if (/ ERROR |\bpanicked\b/.test(line)) return 'bad';
    if (/ WARN /.test(line)) return EXPECTED.some((re) => re.test(line)) ? 'muted' : 'warn';
    return '';
  }

  // The computer's build, followed while it runs: one loop whoever started it, paused while the page
  // is hidden, given up after three readings in a row without an answer.
  let buildLoop = null;
  const pollBuild = () => buildLoop || (buildLoop = followBuild().finally(() => { buildLoop = null; }));
  const visible = () => new Promise((ok) => {
    const on = () => {
      if (document.hidden) return;
      document.removeEventListener('visibilitychange', on);
      ok();
    };
    document.addEventListener('visibilitychange', on);
  });

  async function followBuild() {
    let misses = 0;
    for (;;) {
      if (document.hidden) await visible();
      let b;
      try {
        b = await api('/api/update/build-status');
        misses = 0;
      } catch (e) {
        if (++misses < 3) {
          await sleep(4000);
          continue;
        }
        show('out-upd', failed(e));
        return;
      }
      if (b.running) {
        showOnce('out-upd', `build ${b.started}`, busy(t('build_running', when(b.started))));
        tail('out-upd', b.log_tail);
        await sleep(4000);
        continue;
      }
      if (b.ok) {
        show('out-upd', msg(t('build_ok'), 'ok'));
        runCheck(true, true);
      } else if (b.ok === false) {
        show('out-upd', msg(t('build_failed'), 'bad'), el('pre', { text: `${b.error || ''}\n${b.log_tail || ''}`.trim() }));
      }
      return;
    }
  }

  async function startJob(path) {
    show('out-upd', busy(t('job_starting')));
    try {
      await api(path, 'POST');
      jobSince = Date.now();
      refresh();
    } catch (e) {
      show('out-upd', failed(e));
    }
  }

  const ACTIONS = {
    'test-librespot': async () => {
      show('out-lr', busy(t('testing')));
      try {
        const r = await api('/api/test/librespot', 'POST');
        const z = r.zeroconf, lg = r.log;
        const good = r.running && z.ok;
        show('out-lr',
          msg(good ? t('lr_ok', z.ms) : t(r.running ? 'lr_no_zeroconf' : 'lr_not_running'), good ? 'ok' : 'bad'),
          msg(t('lr_log', lg.since || '?', lg.underruns, lg.warnings, lg.errors)
            + (lg.expected ? ` ${t('lr_log_expected', lg.expected)}` : ''), lg.errors || lg.underruns > 3 ? 'warn' : ''),
          r.credentials_saved ? null : msg(t('account_none'), 'warn'),
          lg.recent.length ? el('pre', { text: lg.recent.join('\n') }) : null);
      } catch (e) {
        show('out-lr', failed(e));
      }
    },
    'restart-librespot': async () => {
      if (confirm(t('confirm_restart_lr'))) await simple('out-lr', '/api/restart/librespot', 'ok_restart_lr');
    },
    'restart-official': async () => {
      if (confirm(t('confirm_restart_off'))) await simple('out-off', '/api/restart/official', 'ok_restart_off');
    },
    'test-network': async () => {
      show('out-net', busy(t('testing_net')));
      try {
        show('out-net', ...netResults(await api('/api/test/network', 'POST', null, TIMEOUT.nettest)));
      } catch (e) {
        show('out-net', failed(e));
      }
    },
    check: () => runCheck(true, false),
    'update-all': async () => {
      if (confirm(t('confirm_update'))) await startJob('/api/update/all');
    },
    build: async () => {
      if (!confirm(t('confirm_build'))) return;
      show('out-upd', busy(t('build_starting')));
      try {
        await api('/api/update/build', 'POST');
        await pollBuild();
      } catch (e) {
        show('out-upd', failed(e));
      }
    },
    install: async () => {
      if (confirm(t('confirm_install'))) await startJob('/api/update/install');
    },
    rollback: async () => {
      const prev = st && st.prev_versions;
      if (prev && confirm(t('confirm_rollback', prevLabel(prev)))) await startJob('/api/update/rollback');
    },
    'restart-agent': async () => {
      if (!confirm(t('confirm_restart_agent'))) return;
      if (await simple('out-sys', '/api/restart/agent', 'ok_restart_agent')) restartingUntil = Date.now() + 30000;
    },
    'settings-save': saveSettings,
    'settings-undo': async () => {
      renderSettings();
      show('out-set');
    },
    logs: async () => {
      const source = $('log-source').value;
      show('out-logs', busy(t('loading')));
      try {
        const text = await api(`/api/logs?source=${encodeURIComponent(source)}&n=400`);
        const lines = String(text).split('\n').filter((l) => l.trim());
        const kinds = muteSwitches(lines, lines.map((l) => logKind(l, source)));
        const count = (k) => kinds.filter((x) => x === k).length;
        const pre = el('pre', null, ...lines.map((l, i) => el('span', { class: kinds[i] || null, text: `${l}\n` })));
        show('out-logs', source === 'official' ? note(t('log_official_note'))
          : msg(t('log_summary', count('bad'), count('warn'), count('muted')), count('bad') ? 'bad' : count('warn') ? 'warn' : 'ok'), pre);
        pre.scrollTop = pre.scrollHeight;
      } catch (e) {
        show('out-logs', failed(e));
      }
    },
  };

  function askPin() {
    $('pin-box').hidden = false;
    $('pin').focus();
  }

  // One run of an action at a time; its button stays disabled until it is done.
  async function run(name, btn) {
    const act = ACTIONS[name];
    if (!act || !btn || btn.disabled || inflight.has(name)) return;
    inflight.add(name);
    btn.disabled = true;
    try {
      await act();
    } finally {
      inflight.delete(name);
      btn.disabled = false;
      renderUpdates(); // re-apply the buttons' state
      updateDirty();
    }
  }

  document.addEventListener('click', (ev) => {
    const btn = ev.target.closest('button[data-act]');
    // Save submits the settings form; the submit event runs it (Enter in a field does the same).
    if (btn && btn.type !== 'submit') run(btn.dataset.act, btn);
  });

  $('set-form').addEventListener('input', updateDirty);
  $('set-form').addEventListener('change', updateDirty);
  $('set-form').addEventListener('submit', (ev) => {
    ev.preventDefault();
    run('settings-save', ev.submitter || document.querySelector('[data-act="settings-save"]'));
  });

  // A PIN typed at the top is checked first and kept only when the speaker accepts it.
  $('pin').value = pin;
  $('pin').addEventListener('change', async (ev) => {
    const entered = ev.target.value.trim();
    keepPin('');
    if (!entered) return;
    pin = entered; // sent with the check
    try {
      await api('/api/pin', 'POST');
    } catch (e) {
      pin = '';
      ev.target.value = '';
      const text = t(e.status === 429 ? 'pin_locked' : e.status === 401 ? 'pin_bad' : 'offline');
      connHold = Date.now() + 6000;
      pill('conn', text, 'bad');
      announce(text);
      return;
    }
    keepPin(entered);
    connHold = Date.now() + 3000;
    pill('conn', t('pin_ok'), 'ok');
    announce(t('pin_ok'));
    refresh(); // (and the update check the missing PIN held back)
  });

  document.addEventListener('visibilitychange', () => (document.hidden ? clearTimeout(timer) : refresh()));
  window.addEventListener('online', () => refresh());

  // The sticky header's height (two rows on a phone) for scroll-padding: what focus or a link
  // scrolls to is not left under it.
  const header = document.querySelector('header.top');
  const headerHeight = () => document.documentElement.style.setProperty('--header-h', `${header.offsetHeight}px`);
  if (window.ResizeObserver) new ResizeObserver(headerHeight).observe(header);
  else headerHeight();

  document.documentElement.lang = LANG;
  for (const node of document.querySelectorAll('[data-t]')) node.textContent = t(node.dataset.t);
  // Names for assistive technology where the visible label needs its card around it ("Restart").
  for (const node of document.querySelectorAll('[data-t-aria]')) node.setAttribute('aria-label', t(node.dataset.tAria));

  $('sp-name').parentElement.title = t('speaker_help');

  // Language and theme, in the header: this browser's choice, or its language and the system's theme.
  for (const b of document.querySelectorAll('[data-lang]')) {
    b.setAttribute('aria-pressed', String(b.dataset.lang === LANG));
    b.addEventListener('click', () => {
      if (b.dataset.lang === LANG) return;
      store('lithify-lang', b.dataset.lang);
      location.reload(); // every text again, in the other language (the PIN stays for this tab)
    });
  }
  for (const b of document.querySelectorAll('[data-theme-set]')) {
    b.title = b.getAttribute('aria-label');
    b.addEventListener('click', () => {
      store('lithify-theme', b.dataset.themeSet === 'auto' ? '' : b.dataset.themeSet);
      applyTheme(b.dataset.themeSet);
    });
  }
  applyTheme(stored('lithify-theme') || 'auto');
  pill('conn', t('connecting'), '');
  refresh();
  loadSettings();
  // A build started elsewhere (the CLI, another browser) is shown here too.
  api('/api/update/build-status').then((b) => { if (b && b.running) pollBuild(); }).catch(() => {});
})();
