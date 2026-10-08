// Lithify setup assistant (`lithify wizard`): the steps of a first installation in the browser. The
// computer does the work and says where it is (GET /api/state); this page draws it. No
// dependencies; everything from the computer is inserted with textContent, never as HTML.
'use strict';
(() => {
  // This browser's choice of language; without storage, this visit only.
  const stored = (key) => { try { return localStorage.getItem(key) || ''; } catch (_) { return ''; } };
  const store = (key, value) => {
    try { if (value) localStorage.setItem(key, value); else localStorage.removeItem(key); } catch (_) { /* not kept */ }
  };

  // The access key comes in the address the wizard opened. It leaves the address bar at once (no
  // history entry, bookmark or screenshot keeps it) and stays with this tab, so a reload still works.
  const KEY = 'lithify-wizard-token';
  let token = new URLSearchParams(location.search).get('t') || '';
  if (token) {
    try { sessionStorage.setItem(KEY, token); } catch (_) { /* this view only */ }
    history.replaceState(null, '', location.pathname);
  } else {
    try { token = sessionStorage.getItem(KEY) || ''; } catch (_) { token = ''; }
  }

  const T = {
    en: {
      doc_title: 'Lithify setup', app_title: 'Spotify Connect setup', lang_group: 'Language', steps_label: 'Steps',
      step_welcome: 'Welcome', step_computer: 'Computer', step_speaker: 'Speaker',
      step_install: 'Install', step_done: 'Done', step_completed: 'completed', loading: 'Loading…',
      footer: 'This page runs on this computer only; other devices on your network cannot open it.',
      back: 'Back', next: 'Continue', try_again: 'Try again', copy: 'Copy', copied: 'Copied',
      copy_log: 'Copy log', copy_cmd: 'Copy the command', details_title: 'Details',
      copied_log: 'The log is copied.', copied_cmd: 'The command is copied.',
      copy_failed: 'Could not copy: select the text and copy it yourself.',
      e_busy: 'Another step is still running – wait a moment.',
      e_timeout: 'The setup assistant did not answer in time.', e_network: 'No connection to the setup assistant.',
      e_bad_speaker: 'Choose a supported speaker first.',
      e_token: 'This page has lost its access key. Open the address shown in the Lithify window on this computer again.',
      e_other: 'Error: {0}',

      welcome_title: 'Welcome',
      welcome_lead: 'This assistant adds Spotify Connect (librespot) to your Lithe Audio speaker, so it plays reliably from the Spotify app on your phone, tablet or computer.',
      welcome_what: 'What happens',
      w_1: 'This computer checks that it has what it needs and finds your speaker on the home network.',
      w_2: 'It builds the software for the speaker – the first time this takes 10–30 minutes – and installs it over the network.',
      w_3: 'The speaker restarts once and is silent for about a minute.',
      w_time: 'All in all about 15–40 minutes the first time; later updates take a few minutes.',
      w_2_ready: 'It installs the ready-made software on the speaker over the network.',
      w_time_ready: 'All in all a few minutes.',
      welcome_safe: 'Nothing is flashed',
      w_safe: 'The speaker keeps its own firmware, Google Cast and AirPlay. Lithify runs next to them and can be removed again at any time.',
      welcome_need: 'You need',
      n_1: 'the speaker switched on, on the same network as this computer',
      n_premium: 'Spotify Premium (librespot does not work with free accounts)',
      n_2: 'Docker Desktop on this computer (the next step checks it and shows where to get it)',
      n_2_linux: 'Docker on this computer (the next step checks it and shows how to install it)',
      start: 'Start',

      computer_title: 'Checking this computer', computer_lead: 'Lithify needs a few things on this computer.',
      checking: 'Checking this computer…', check_again: 'Check again', check_computer: 'Check this computer',
      computer_ok: 'Everything is ready: continue to find your speaker.',
      computer_bad: 'Fix the marked item, then click “Check again”.',
      c_python: 'Python', c_docker: 'Docker', c_disk: 'Disk space', c_bundle: 'Speaker software',
      st_ok: 'OK', st_warn: 'Note', st_bad: 'Needs fixing',
      python_ok: 'Python {version} is fine.',
      docker_ok: 'Docker {version} is running.',
      docker_not_needed: 'Not needed this time: the speaker software is already built on this computer.',
      docker_published: 'Not needed: the speaker software is downloaded ready-made (Docker would only build it if the download failed).',
      docker_missing: 'Docker is not installed. Lithify builds the speaker software inside Docker.',
      docker_not_running: 'Docker is installed but not running.',
      docker_permission: 'Docker is running, but your user may not use it yet.',
      docker_slow: 'Docker does not answer – it may still be starting.',
      disk_ok: '{free} GB free.',
      disk_low: 'Only {free} GB free. Building the speaker software needs about 6 GB: free some space first.',
      bundle_present: 'Already built ({version}), so this installation takes only a few minutes.',
      bundle_absent: 'It will be built during the installation (10–30 minutes the first time).',
      bundle_published: 'It will be downloaded ready-made during the installation (a minute or two).',
      fix_docker_missing_windows: 'Install Docker Desktop (free for personal use), start it once and wait until it shows “Engine running”. Then click “Check again”.',
      fix_docker_missing_macos: 'Install Docker Desktop (free for personal use), start it from Applications and wait until it is running. Then click “Check again”.',
      fix_docker_missing_linux: 'Install Docker Engine for your Linux distribution with the guide below. Then click “Check again”.',
      fix_docker_not_running_windows: 'Start Docker Desktop and wait until it shows “Engine running” – or let Lithify start it.',
      fix_docker_not_running_macos: 'Start Docker Desktop and wait until it is running – or let Lithify start it.',
      fix_docker_not_running_linux: 'Start the Docker service in a terminal, then click “Check again”:',
      fix_docker_slow_windows: 'Wait until Docker Desktop has started, then click “Check again” – or let Lithify start it.',
      fix_docker_slow_macos: 'Wait until Docker Desktop has started, then click “Check again” – or let Lithify start it.',
      fix_docker_slow_linux: 'Wait a minute, then click “Check again”. If it stays like this, restart the Docker service.',
      fix_docker_permission_linux: 'Add your user to the “docker” group in a terminal, then log out and back in (or restart the computer) and start Lithify again:',
      fix_docker_permission_windows: 'Sign out of Windows and sign in again (Docker Desktop’s access, the “docker-users” group, starts then), then click “Check again”.',
      fix_docker_permission: 'Quit Docker Desktop and start it again, then click “Check again”.',
      link_docker_missing_windows: 'Download Docker Desktop for Windows', link_docker_missing_macos: 'Download Docker Desktop for Mac',
      link_docker_missing_linux: 'How to install Docker Engine', link_docker_permission: 'Docker’s guide: Docker as a regular user',
      start_docker: 'Start Docker Desktop',
      starting_docker: 'Starting Docker Desktop… this can take a minute or two ({0}).',

      speaker_title: 'Choose your speaker',
      searching: 'Searching your network for Lithe Audio speakers…', probing: 'Checking {0}…',
      found_n: 'Speakers found on your network: {0}.',
      none_found: 'No Lithe Audio speaker answered. Check that it is switched on and on the same network as this computer (not a guest network), then search again – or type its address below.',
      search_again: 'Search again', k_model: 'Model', k_address: 'Address', k_lithify: 'Lithify',
      lithify_installed: 'installed (librespot {0})',
      unnamed: 'Lithe Audio speaker', model_unknown: 'unknown model',
      sp_checking: 'checking…', sp_supported: 'supported', sp_unsupported: 'not supported', sp_silent: 'no answer',
      use_speaker: 'Use this speaker', use_aria: 'Use the speaker {0}', chosen_btn: 'Chosen', chosen_aria: 'Chosen: {0}',
      r_no_console: 'Its service console does not answer. Newer models (WiFi Speaker V3, PRO 2, iO1) are not supported yet; if this is a WiFi Speaker V2, WiFi PRO or Micro Subwoofer, restart it and search again.',
      r_not_found: 'Nothing answers at this address. Check the address and that the speaker is switched on.',
      r_unsupported_model: 'This model is not supported yet: Lithify works with the WiFi Speaker V2, WiFi PRO and Micro Subwoofer.',
      r_no_answer: 'It did not answer in time. Restart it and search again.',
      r_probe_failed: 'It could not be checked: {0}',
      manual_label: 'Or type the speaker’s address (an IP address such as 192.168.1.20, or its name)',
      manual_hint: 'You find the IP address in the Lithe Audio or Google Home app (the speaker’s settings, device information) or in your router’s list of devices.',
      add: 'Check this address',
      bad_host: 'That is not an IP address or a network name (for example 192.168.1.20).',

      name_title: 'Name in Spotify',
      name_lead: 'This is how the speaker will be listed in the Spotify app when you tap the devices icon (Spotify Connect) on your phone or computer.',
      name_official: 'The speaker’s built-in Spotify stays in that list as “{0}”; the name below is the new, reliable entry that Lithify adds. You can change it later on the speaker’s page.',
      name_current: 'Lithify already runs on this speaker (librespot {0}), named “{1}” in Spotify. Installing again updates it and keeps its settings.',
      name_label: 'Name in Spotify', name_help: '1 to 64 characters.',
      name_bad: 'Give the speaker a name of 1 to 64 characters.',
      name_summary: 'Speaker: {0}, {1}, at {2}',
      before_install: 'The first installation takes 15–40 minutes. Keep this computer switched on and awake, and leave this page and the Lithify window open.',
      before_install_ready: 'The installation takes a few minutes. Keep this computer switched on and awake, and leave this page and the Lithify window open.',
      hint_windows: 'Windows may ask whether Lithify may make changes (a firewall rule, so the speaker can download its software from this computer): choose “Yes”.',
      hint_macos: 'If macOS asks whether Python may accept incoming network connections, choose “Allow”: the speaker downloads its software from this computer.',
      hint_linux: 'If this computer runs a firewall (ufw, firewalld), the speaker must reach it on TCP ports {0}.',
      install_btn: 'Install',

      install_title: 'Installing on {0}', now: 'Now: {0}', now_step: '{0}: {1}', elapsed: 'Time so far: {0}',
      ph_prepare: 'Preparing', ph_build: 'Building the speaker software', ph_install: 'Installing on the speaker',
      ph_restart: 'Restarting the speaker', ph_check: 'Checking and finishing',
      ph_build_note: 'The first build takes 10–30 minutes; later ones are much faster.',
      ph_download: 'Downloading the speaker software',
      ph_restart_note: 'About a minute without sound.',
      ph_check_note: 'Lithify also sets up its helper on this computer, which keeps the speaker updatable.',
      ps_pending: 'waiting', ps_active: 'in progress', ps_done: 'done', ps_skipped: 'not needed', ps_failed: 'stopped',
      sub_image: 'preparing the build tools', sub_source: 'getting the librespot sources',
      sub_compile: 'compiling librespot – the longest part', sub_reuse: 'librespot is unchanged: reusing the last build',
      sub_agent: 'building the speaker’s agent', sub_bundle: 'packing it all for the speaker',
      sub_download: 'downloading the ready-made software',
      sub_link: 'librespot itself – the last part: several minutes in which the bar does not move',
      sub_firewall: 'Windows asks for permission (a firewall rule, so the speaker can download its software from this computer): choose “Yes”',
      cancel: 'Cancel', stopping: 'Stopping…',
      confirm_cancel: 'Stop the installation? You can start it again later.',
      confirm_cancel_speaker: 'The speaker is being updated right now. If you stop, it keeps working (with the previous version, or it finishes on its own), and you can start the installation again. Stop now?',
      log_title: 'Technical details (log)',

      done_title: 'Done! Spotify Connect is ready', open_page: 'Open the speaker’s page',
      page_help: 'The page shows how the speaker is doing, lets you change its settings and install updates. Its address: {0}',
      page_quiet: 'The speaker’s page did not answer yet. Give it a minute; if it still does not open, restart the speaker.',
      how_title: 'How to play',
      how_1: 'Open the Spotify app on your phone, tablet or computer and start any song.',
      how_2: 'Tap the devices icon (Spotify Connect: a small speaker and screen) at the bottom of the player.',
      how_3: 'Pick “{0}” from the list. The first time, Spotify links the speaker to your account; after that it connects by itself.',
      helper_ok: 'Lithify’s helper now runs on this computer and starts with it. It keeps the speaker updatable: on the speaker’s page, “Updates” → “Update everything”.',
      helper_ok_macos: 'Lithify’s helper now runs on this computer and starts with it (macOS lists it as “python3.x” under Login Items & Extensions – that is this helper). It keeps the speaker updatable: on the speaker’s page, “Updates” → “Update everything”.',
      helper_failed: 'The update helper could not be set up on this computer. The speaker works; for updates from its page, run “lithify serve --install-service” later.',
      helper_no_systemd: 'This computer has no systemd user session, so the update helper cannot start by itself. The speaker works; for updates from its page, start “lithify serve” yourself, for example from your desktop’s autostart.',
      another: 'Set up another speaker', finish: 'Finish',
      closed_title: 'Setup is finished', closed_text: 'You can close this tab now.',
      gone_title: 'The setup assistant has stopped',
      gone_text: 'It no longer answers – its window on this computer was probably closed. Start Lithify again to continue.',

      err_docker_missing: 'Docker is not installed.',
      err_docker_desktop_missing: 'Docker Desktop was not found on this computer.',
      fix_docker_desktop_missing: 'Install Docker Desktop, start it once, then click “Check again”.',
      err_docker_not_running: 'Docker is not running.', err_docker_slow: 'Docker does not answer.',
      err_docker_permission: 'Your user may not use Docker yet.',
      err_docker_start_timeout: 'Docker Desktop did not start within 3 minutes.',
      fix_docker_start_timeout: 'Start Docker Desktop yourself and wait until it is running, then click “Check again”.',
      err_unreachable: 'The speaker does not answer.',
      fix_unreachable: 'Check that it is switched on and on the same network as this computer – not a guest network. Then click “Try again”.',
      err_unsupported: 'This speaker model is not supported yet.',
      fix_unsupported: 'Lithify works with the WiFi Speaker V2, WiFi PRO and Micro Subwoofer. Newer models (WiFi Speaker V3, PRO 2, iO1) are not supported yet.',
      err_firewall: 'The speaker could not download its software from this computer.',
      fix_firewall_windows: 'The Windows firewall probably blocked it. Click “Try again” and choose “Yes” when Windows asks for permission, and set this network to Private (Settings → Network & internet → Properties) – or allow incoming TCP connections on ports {0} from your local network. Also check that this computer is not connected through a VPN, and the speaker not to a guest Wi-Fi.',
      fix_firewall_macos: 'The macOS firewall probably blocked it. Open System Settings → Network → Firewall → Options, allow incoming connections for Python, then click “Try again”. Also check that this computer is not connected through a VPN, and the speaker not to a guest Wi-Fi.',
      fix_firewall_linux: 'A firewall on this computer probably blocked it. Allow the speaker to reach TCP ports {0} – with ufw or with firewalld one of these commands does it – then click “Try again”. Also check that this computer is not connected through a VPN, and the speaker not to a guest Wi-Fi.',
      err_no_space: 'This computer ran out of disk space.',
      fix_no_space: 'Free at least 6 GB (for example empty the trash or delete large downloads), then click “Try again”.',
      err_speaker_space: 'The speaker’s own storage is full.',
      fix_speaker_space: 'Unplug the speaker for 10 seconds, plug it back in and wait a minute, then click “Try again”.',
      err_not_back: 'The speaker did not come back after its restart.',
      fix_not_back: 'Wait a minute. If it still does not respond, unplug it for 10 seconds and plug it back in. Then click “Try again”: the installation continues where it stopped.',
      err_build_busy: 'Another build is already running on this computer.',
      fix_build_busy: 'Perhaps the speaker’s page started one. Wait until it is finished (up to 20 minutes), then click “Try again”.',
      err_build_failed: 'Building the speaker software failed.',
      err_build_memory: 'The computer ran out of memory while building the speaker software.',
      fix_build_memory: 'Building needs about 1.5 GB of free memory at once. Close other programs (and other browser tabs), then click “Try again”.',
      fix_build_memory_windows: 'Close other programs, then click “Try again”. If it fails again, this computer has too little memory for Docker Desktop: building needs about 1.5 GB free (8 GB in the computer is enough).',
      fix_build_memory_macos: 'Close other programs, then click “Try again”. If it fails again, give Docker Desktop more memory: Settings → Resources → Memory, 4 GB or more.',
      fix_build_failed: 'This is often temporary – a download that failed, or a busy computer. Click “Try again”. If it fails again, copy the log and ask for help in the Lithify project. The full build log is in {log}.',
      err_config: 'Lithify’s settings file has a problem.',
      fix_config: 'See the details below. Correct the file, or rename it (Lithify then starts a new one), and click “Try again”.',
      err_cancelled: 'The installation was stopped.', fix_cancelled: 'Click “Try again” to start it again.',
      err_timeout: 'The installation did not finish within 3 hours and was stopped.',
      fix_timeout: 'Restart this computer and the speaker, then try again.',
      err_unknown: 'Something went wrong.',
      fix_unknown: 'Click “Try again”. If it keeps failing, copy the log and ask for help in the Lithify project.',
      other_speaker: 'Choose another speaker',

      ann_phase: 'Now: {0}.', ann_installed: 'Installation finished.', ann_failed: 'The installation stopped: {0}',
      ann_checked: 'Check finished.', ann_found: 'Speakers found: {0}.', ann_probe: 'Checked {0}: {1}.',
    },
    pl: {
      doc_title: 'Instalacja Lithify', app_title: 'Instalacja Spotify Connect', lang_group: 'Język', steps_label: 'Kroki',
      step_welcome: 'Start', step_computer: 'Komputer', step_speaker: 'Głośnik',
      step_install: 'Instalacja', step_done: 'Gotowe', step_completed: 'ukończony', loading: 'Wczytywanie…',
      footer: 'Ta strona działa tylko na tym komputerze; inne urządzenia w sieci nie mają do niej dostępu.',
      back: 'Wstecz', next: 'Dalej', try_again: 'Spróbuj ponownie', copy: 'Kopiuj', copied: 'Skopiowano',
      copy_log: 'Kopiuj log', copy_cmd: 'Kopiuj polecenie', details_title: 'Szczegóły',
      copied_log: 'Log skopiowany.', copied_cmd: 'Polecenie skopiowane.',
      copy_failed: 'Nie udało się skopiować: zaznacz tekst i skopiuj go samodzielnie.',
      e_busy: 'Trwa jeszcze inny krok – poczekaj chwilę.',
      e_timeout: 'Asystent instalacji nie odpowiedział na czas.', e_network: 'Brak połączenia z asystentem instalacji.',
      e_bad_speaker: 'Najpierw wybierz obsługiwany głośnik.',
      e_token: 'Ta strona straciła klucz dostępu. Otwórz ponownie adres widoczny w oknie Lithify na tym komputerze.',
      e_other: 'Błąd: {0}',

      welcome_title: 'Witaj',
      welcome_lead: 'Ten asystent doda do Twojego głośnika Lithe Audio Spotify Connect (librespot), żeby niezawodnie grał z aplikacji Spotify na telefonie, tablecie lub komputerze.',
      welcome_what: 'Co się stanie',
      w_1: 'Komputer sprawdzi, czy ma wszystko, czego potrzeba, i znajdzie głośnik w sieci domowej.',
      w_2: 'Zbuduje oprogramowanie dla głośnika – za pierwszym razem trwa to 10–30 minut – i zainstaluje je przez sieć.',
      w_3: 'Głośnik raz uruchomi się ponownie i przez około minutę nie będzie grał.',
      w_time: 'Łącznie za pierwszym razem około 15–40 minut; późniejsze aktualizacje trwają kilka minut.',
      w_2_ready: 'Zainstaluje przez sieć gotowe oprogramowanie na głośniku.',
      w_time_ready: 'Łącznie kilka minut.',
      welcome_safe: 'Bez wgrywania firmware',
      w_safe: 'Głośnik zachowuje swój firmware, Google Cast i AirPlay. Lithify działa obok nich i w każdej chwili można go usunąć.',
      welcome_need: 'Potrzebne będą',
      n_1: 'włączony głośnik w tej samej sieci co ten komputer',
      n_premium: 'konto Spotify Premium (librespot nie działa z darmowymi kontami)',
      n_2: 'Docker Desktop na tym komputerze (następny krok to sprawdzi i pokaże, skąd go pobrać)',
      n_2_linux: 'Docker na tym komputerze (następny krok to sprawdzi i pokaże, jak go zainstalować)',
      start: 'Zaczynamy',

      computer_title: 'Sprawdzanie komputera', computer_lead: 'Lithify potrzebuje na tym komputerze kilku rzeczy.',
      checking: 'Sprawdzanie komputera…', check_again: 'Sprawdź ponownie', check_computer: 'Sprawdź komputer',
      computer_ok: 'Wszystko gotowe: przejdź dalej, aby znaleźć głośnik.',
      computer_bad: 'Popraw zaznaczony element i kliknij „Sprawdź ponownie”.',
      c_python: 'Python', c_docker: 'Docker', c_disk: 'Miejsce na dysku', c_bundle: 'Oprogramowanie głośnika',
      st_ok: 'OK', st_warn: 'Uwaga', st_bad: 'Do poprawienia',
      python_ok: 'Python {version} – w porządku.',
      docker_ok: 'Docker {version} działa.',
      docker_not_needed: 'Tym razem niepotrzebny: oprogramowanie głośnika jest już zbudowane na tym komputerze.',
      docker_published: 'Niepotrzebny: oprogramowanie głośnika zostanie pobrane gotowe (Docker zbudowałby je tylko, gdyby pobieranie się nie udało).',
      docker_missing: 'Docker nie jest zainstalowany. Lithify buduje w nim oprogramowanie głośnika.',
      docker_not_running: 'Docker jest zainstalowany, ale nie działa.',
      docker_permission: 'Docker działa, ale Twój użytkownik nie ma jeszcze do niego dostępu.',
      docker_slow: 'Docker nie odpowiada – może jeszcze się uruchamia.',
      disk_ok: 'Wolne: {free} GB.',
      disk_low: 'Wolne tylko {free} GB. Budowanie oprogramowania głośnika potrzebuje około 6 GB: najpierw zwolnij trochę miejsca.',
      bundle_present: 'Już zbudowane ({version}), więc ta instalacja potrwa tylko kilka minut.',
      bundle_absent: 'Zostanie zbudowane podczas instalacji (za pierwszym razem 10–30 minut).',
      bundle_published: 'Zostanie pobrane gotowe podczas instalacji (minuta lub dwie).',
      fix_docker_missing_windows: 'Zainstaluj Docker Desktop (bezpłatny do użytku osobistego), uruchom go raz i poczekaj, aż pokaże „Engine running”. Potem kliknij „Sprawdź ponownie”.',
      fix_docker_missing_macos: 'Zainstaluj Docker Desktop (bezpłatny do użytku osobistego), uruchom go z folderu Programy i poczekaj, aż zacznie działać. Potem kliknij „Sprawdź ponownie”.',
      fix_docker_missing_linux: 'Zainstaluj Docker Engine dla swojej dystrybucji Linuksa według poniższej instrukcji. Potem kliknij „Sprawdź ponownie”.',
      fix_docker_not_running_windows: 'Uruchom Docker Desktop i poczekaj, aż pokaże „Engine running” – albo pozwól Lithify go uruchomić.',
      fix_docker_not_running_macos: 'Uruchom Docker Desktop i poczekaj, aż zacznie działać – albo pozwól Lithify go uruchomić.',
      fix_docker_not_running_linux: 'Uruchom usługę Dockera w terminalu, a potem kliknij „Sprawdź ponownie”:',
      fix_docker_slow_windows: 'Poczekaj, aż Docker Desktop się uruchomi, i kliknij „Sprawdź ponownie” – albo pozwól Lithify go uruchomić.',
      fix_docker_slow_macos: 'Poczekaj, aż Docker Desktop się uruchomi, i kliknij „Sprawdź ponownie” – albo pozwól Lithify go uruchomić.',
      fix_docker_slow_linux: 'Poczekaj minutę i kliknij „Sprawdź ponownie”. Jeśli nic się nie zmieni, uruchom ponownie usługę Dockera.',
      fix_docker_permission_linux: 'Dodaj swojego użytkownika do grupy „docker” w terminalu, potem wyloguj się i zaloguj ponownie (albo uruchom ponownie komputer) i jeszcze raz uruchom Lithify:',
      fix_docker_permission_windows: 'Wyloguj się z Windows i zaloguj ponownie (wtedy zaczyna działać dostęp nadany przez Docker Desktop – grupa „docker-users”), a potem kliknij „Sprawdź ponownie”.',
      fix_docker_permission: 'Zamknij Docker Desktop i uruchom go ponownie, a potem kliknij „Sprawdź ponownie”.',
      link_docker_missing_windows: 'Pobierz Docker Desktop dla Windows', link_docker_missing_macos: 'Pobierz Docker Desktop dla Maca',
      link_docker_missing_linux: 'Jak zainstalować Docker Engine', link_docker_permission: 'Instrukcja Dockera: Docker jako zwykły użytkownik',
      start_docker: 'Uruchom Docker Desktop',
      starting_docker: 'Uruchamianie Docker Desktop… to może potrwać minutę lub dwie ({0}).',

      speaker_title: 'Wybierz głośnik',
      searching: 'Szukanie głośników Lithe Audio w sieci…', probing: 'Sprawdzanie {0}…',
      found_n: 'Głośniki znalezione w Twojej sieci: {0}.',
      none_found: 'Żaden głośnik Lithe Audio nie odpowiedział. Sprawdź, czy jest włączony i podłączony do tej samej sieci co ten komputer (nie do sieci dla gości), i poszukaj ponownie – albo wpisz jego adres poniżej.',
      search_again: 'Szukaj ponownie', k_model: 'Model', k_address: 'Adres', k_lithify: 'Lithify',
      lithify_installed: 'zainstalowany (librespot {0})',
      unnamed: 'Głośnik Lithe Audio', model_unknown: 'nieznany model',
      sp_checking: 'sprawdzanie…', sp_supported: 'obsługiwany', sp_unsupported: 'nieobsługiwany', sp_silent: 'brak odpowiedzi',
      use_speaker: 'Wybierz ten głośnik', use_aria: 'Wybierz głośnik {0}', chosen_btn: 'Wybrany', chosen_aria: 'Wybrany: {0}',
      r_no_console: 'Jego konsola serwisowa nie odpowiada. Nowsze modele (WiFi Speaker V3, PRO 2, iO1) nie są jeszcze obsługiwane; jeśli to WiFi Speaker V2, WiFi PRO lub Micro Subwoofer, uruchom go ponownie i poszukaj jeszcze raz.',
      r_not_found: 'Pod tym adresem nic nie odpowiada. Sprawdź adres i czy głośnik jest włączony.',
      r_unsupported_model: 'Ten model nie jest jeszcze obsługiwany: Lithify działa z WiFi Speaker V2, WiFi PRO i Micro Subwoofer.',
      r_no_answer: 'Nie odpowiedział na czas. Uruchom go ponownie i poszukaj jeszcze raz.',
      r_probe_failed: 'Nie udało się go sprawdzić: {0}',
      manual_label: 'Albo wpisz adres głośnika (adres IP, np. 192.168.1.20, albo jego nazwę)',
      manual_hint: 'Adres IP znajdziesz w aplikacji Lithe Audio lub Google Home (ustawienia głośnika, informacje o urządzeniu) albo na liście urządzeń w routerze.',
      add: 'Sprawdź ten adres',
      bad_host: 'To nie jest adres IP ani nazwa w sieci (np. 192.168.1.20).',

      name_title: 'Nazwa w Spotify',
      name_lead: 'Pod tą nazwą głośnik będzie widoczny w aplikacji Spotify po dotknięciu ikony urządzeń (Spotify Connect) na telefonie lub komputerze.',
      name_official: 'Wbudowany Spotify głośnika zostanie na tej liście jako „{0}”; poniższa nazwa to nowa, niezawodna pozycja dodana przez Lithify. Później można ją zmienić na stronie głośnika.',
      name_current: 'Na tym głośniku działa już Lithify (librespot {0}), w Spotify jako „{1}”. Ponowna instalacja go zaktualizuje i zachowa jego ustawienia.',
      name_label: 'Nazwa w Spotify', name_help: 'Od 1 do 64 znaków.',
      name_bad: 'Podaj nazwę głośnika od 1 do 64 znaków.',
      name_summary: 'Głośnik: {0}, {1}, adres {2}',
      before_install: 'Pierwsza instalacja trwa 15–40 minut. Nie wyłączaj ani nie usypiaj komputera i nie zamykaj tej strony ani okna Lithify.',
      before_install_ready: 'Instalacja trwa kilka minut. Nie wyłączaj ani nie usypiaj komputera i nie zamykaj tej strony ani okna Lithify.',
      hint_windows: 'Windows może zapytać, czy Lithify może wprowadzić zmiany (reguła zapory, aby głośnik mógł pobrać oprogramowanie z tego komputera): wybierz „Tak”.',
      hint_macos: 'Jeśli macOS zapyta, czy Python może przyjmować przychodzące połączenia sieciowe, wybierz „Pozwalaj”: głośnik pobiera oprogramowanie z tego komputera.',
      hint_linux: 'Jeśli na tym komputerze działa zapora (ufw, firewalld), głośnik musi mieć do niego dostęp na portach TCP {0}.',
      install_btn: 'Zainstaluj',

      install_title: 'Instalacja na: {0}', now: 'Teraz: {0}', now_step: '{0}: {1}', elapsed: 'Czas: {0}',
      ph_prepare: 'Przygotowanie', ph_build: 'Budowanie oprogramowania głośnika', ph_install: 'Instalacja na głośniku',
      ph_restart: 'Ponowne uruchamianie głośnika', ph_check: 'Sprawdzanie i zakończenie',
      ph_build_note: 'Pierwsza kompilacja trwa 10–30 minut; kolejne są dużo szybsze.',
      ph_download: 'Pobieranie oprogramowania głośnika',
      ph_restart_note: 'Około minuty bez dźwięku.',
      ph_check_note: 'Lithify ustawia też na tym komputerze swojego pomocnika, dzięki któremu głośnik można aktualizować.',
      ps_pending: 'czeka', ps_active: 'w toku', ps_done: 'gotowe', ps_skipped: 'niepotrzebne', ps_failed: 'przerwane',
      sub_image: 'przygotowanie narzędzi', sub_source: 'pobieranie źródeł librespot',
      sub_compile: 'kompilacja librespot – to trwa najdłużej', sub_reuse: 'librespot bez zmian: użyta ostatnia kompilacja',
      sub_agent: 'budowanie agenta głośnika', sub_bundle: 'pakowanie wszystkiego dla głośnika',
      sub_download: 'pobieranie gotowego oprogramowania',
      sub_link: 'sam librespot – ostatnia część: kilka minut, w których pasek stoi w miejscu',
      sub_firewall: 'Windows prosi o zgodę (reguła zapory, żeby głośnik mógł pobrać oprogramowanie z tego komputera): wybierz „Tak”',
      cancel: 'Anuluj', stopping: 'Zatrzymywanie…',
      confirm_cancel: 'Przerwać instalację? Możesz ją później uruchomić ponownie.',
      confirm_cancel_speaker: 'Głośnik jest właśnie aktualizowany. Jeśli przerwiesz, będzie dalej działał (w poprzedniej wersji albo sam dokończy), a instalację można uruchomić ponownie. Przerwać teraz?',
      log_title: 'Szczegóły techniczne (log)',

      done_title: 'Gotowe! Spotify Connect działa', open_page: 'Otwórz stronę głośnika',
      page_help: 'Strona pokazuje stan głośnika, pozwala zmienić ustawienia i instalować aktualizacje. Jej adres: {0}',
      page_quiet: 'Strona głośnika jeszcze nie odpowiada. Daj jej minutę; jeśli dalej się nie otwiera, uruchom głośnik ponownie.',
      how_title: 'Jak słuchać',
      how_1: 'Otwórz aplikację Spotify na telefonie, tablecie lub komputerze i włącz dowolny utwór.',
      how_2: 'Dotknij ikony urządzeń (Spotify Connect: mały głośnik i ekran) na dole odtwarzacza.',
      how_3: 'Wybierz z listy „{0}”. Za pierwszym razem Spotify połączy głośnik z Twoim kontem; potem łączy się sam.',
      helper_ok: 'Pomocnik Lithify działa teraz na tym komputerze i uruchamia się razem z nim. Dzięki niemu głośnik można aktualizować: na stronie głośnika „Aktualizacje” → „Zaktualizuj wszystko”.',
      helper_ok_macos: 'Pomocnik Lithify działa teraz na tym komputerze i uruchamia się razem z nim (macOS pokazuje go jako „python3.x” w Rzeczach otwieranych przy logowaniu – to właśnie ten pomocnik). Dzięki niemu głośnik można aktualizować: na stronie głośnika „Aktualizacje” → „Zaktualizuj wszystko”.',
      helper_failed: 'Nie udało się ustawić pomocnika aktualizacji na tym komputerze. Głośnik działa; aby aktualizować go z jego strony, uruchom później „lithify serve --install-service”.',
      helper_no_systemd: 'Ten komputer nie ma sesji użytkownika systemd, więc pomocnik aktualizacji nie uruchomi się sam. Głośnik działa; aby aktualizować go z jego strony, uruchamiaj „lithify serve” samodzielnie, np. z autostartu pulpitu.',
      another: 'Skonfiguruj kolejny głośnik', finish: 'Zakończ',
      closed_title: 'Instalacja zakończona', closed_text: 'Możesz już zamknąć tę kartę.',
      gone_title: 'Asystent instalacji nie działa',
      gone_text: 'Nie odpowiada – jego okno na tym komputerze zostało pewnie zamknięte. Uruchom Lithify ponownie, aby kontynuować.',

      err_docker_missing: 'Docker nie jest zainstalowany.',
      err_docker_desktop_missing: 'Na tym komputerze nie znaleziono Docker Desktop.',
      fix_docker_desktop_missing: 'Zainstaluj Docker Desktop, uruchom go raz, a potem kliknij „Sprawdź ponownie”.',
      err_docker_not_running: 'Docker nie działa.', err_docker_slow: 'Docker nie odpowiada.',
      err_docker_permission: 'Twój użytkownik nie ma jeszcze dostępu do Dockera.',
      err_docker_start_timeout: 'Docker Desktop nie uruchomił się w ciągu 3 minut.',
      fix_docker_start_timeout: 'Uruchom Docker Desktop samodzielnie i poczekaj, aż zacznie działać, a potem kliknij „Sprawdź ponownie”.',
      err_unreachable: 'Głośnik nie odpowiada.',
      fix_unreachable: 'Sprawdź, czy jest włączony i w tej samej sieci co ten komputer – nie w sieci dla gości. Potem kliknij „Spróbuj ponownie”.',
      err_unsupported: 'Ten model głośnika nie jest jeszcze obsługiwany.',
      fix_unsupported: 'Lithify działa z WiFi Speaker V2, WiFi PRO i Micro Subwoofer. Nowsze modele (WiFi Speaker V3, PRO 2, iO1) nie są jeszcze obsługiwane.',
      err_firewall: 'Głośnik nie mógł pobrać oprogramowania z tego komputera.',
      fix_firewall_windows: 'Zapewne zablokowała go Zapora Windows. Kliknij „Spróbuj ponownie”, wybierz „Tak”, gdy Windows zapyta o zgodę, i ustaw tę sieć jako prywatną (Ustawienia → Sieć i Internet → Właściwości) – albo zezwól na przychodzące połączenia TCP na portach {0} z sieci lokalnej. Sprawdź też, czy komputer nie łączy się przez VPN, a głośnik nie jest w sieci Wi-Fi dla gości.',
      fix_firewall_macos: 'Zapewne zablokowała go zapora macOS. Otwórz Ustawienia systemowe → Sieć → Zapora → Opcje, pozwól na połączenia przychodzące dla Pythona, a potem kliknij „Spróbuj ponownie”. Sprawdź też, czy komputer nie łączy się przez VPN, a głośnik nie jest w sieci Wi-Fi dla gości.',
      fix_firewall_linux: 'Zapewne zablokowała go zapora na tym komputerze. Pozwól głośnikowi łączyć się z portami TCP {0} – zrobi to jedno z tych poleceń (dla ufw albo dla firewalld) – a potem kliknij „Spróbuj ponownie”. Sprawdź też, czy komputer nie łączy się przez VPN, a głośnik nie jest w sieci Wi-Fi dla gości.',
      err_no_space: 'Na tym komputerze zabrakło miejsca na dysku.',
      fix_no_space: 'Zwolnij co najmniej 6 GB (np. opróżnij kosz albo usuń duże pobrane pliki), a potem kliknij „Spróbuj ponownie”.',
      err_speaker_space: 'Zapełniła się pamięć głośnika.',
      fix_speaker_space: 'Odłącz głośnik od prądu na 10 sekund, podłącz go ponownie i odczekaj minutę, a potem kliknij „Spróbuj ponownie”.',
      err_not_back: 'Głośnik nie wrócił po ponownym uruchomieniu.',
      fix_not_back: 'Poczekaj minutę. Jeśli dalej nie odpowiada, odłącz go od prądu na 10 sekund i podłącz ponownie. Potem kliknij „Spróbuj ponownie”: instalacja będzie kontynuowana od miejsca, w którym stanęła.',
      err_build_busy: 'Na tym komputerze trwa już inna kompilacja.',
      fix_build_busy: 'Może uruchomiła ją strona głośnika. Poczekaj, aż się skończy (do 20 minut), a potem kliknij „Spróbuj ponownie”.',
      err_build_failed: 'Budowanie oprogramowania głośnika nie powiodło się.',
      err_build_memory: 'Podczas budowania oprogramowania głośnika zabrakło pamięci.',
      fix_build_memory: 'Budowanie potrzebuje naraz około 1,5 GB wolnej pamięci. Zamknij inne programy (i inne karty przeglądarki), potem kliknij „Spróbuj ponownie”.',
      fix_build_memory_windows: 'Zamknij inne programy, potem kliknij „Spróbuj ponownie”. Jeśli znowu się nie uda, ten komputer ma za mało pamięci dla Docker Desktop: budowanie potrzebuje około 1,5 GB wolnej (wystarczy 8 GB w komputerze).',
      fix_build_memory_macos: 'Zamknij inne programy, potem kliknij „Spróbuj ponownie”. Jeśli znowu się nie uda, daj Docker Desktop więcej pamięci: Settings → Resources → Memory, 4 GB lub więcej.',
      fix_build_failed: 'Często to chwilowy problem – nieudane pobieranie albo zajęty komputer. Kliknij „Spróbuj ponownie”. Jeśli znowu się nie uda, skopiuj log i poproś o pomoc w projekcie Lithify. Pełny log kompilacji jest w {log}.',
      err_config: 'Plik ustawień Lithify zawiera błąd.',
      fix_config: 'Zobacz szczegóły poniżej. Popraw plik albo zmień jego nazwę (Lithify utworzy wtedy nowy) i kliknij „Spróbuj ponownie”.',
      err_cancelled: 'Instalacja została przerwana.', fix_cancelled: 'Kliknij „Spróbuj ponownie”, aby uruchomić ją od nowa.',
      err_timeout: 'Instalacja nie skończyła się w ciągu 3 godzin i została zatrzymana.',
      fix_timeout: 'Uruchom ponownie ten komputer i głośnik, a potem spróbuj jeszcze raz.',
      err_unknown: 'Coś poszło nie tak.',
      fix_unknown: 'Kliknij „Spróbuj ponownie”. Jeśli dalej się nie udaje, skopiuj log i poproś o pomoc w projekcie Lithify.',
      other_speaker: 'Wybierz inny głośnik',

      ann_phase: 'Teraz: {0}.', ann_installed: 'Instalacja zakończona.', ann_failed: 'Instalacja zatrzymana: {0}',
      ann_checked: 'Sprawdzanie zakończone.', ann_found: 'Znalezione głośniki: {0}.', ann_probe: 'Sprawdzono {0}: {1}.',
    },
  };

  // The language: one chosen on this page, else the wizard's --lang, else this browser's.
  const chosen = stored('lithify-lang');
  let picked = chosen === 'pl' || chosen === 'en';
  let LANG = picked ? chosen : /^pl\b/i.test(navigator.language || '') ? 'pl' : 'en';
  const has = (key) => key in T[LANG] || key in T.en;
  // {0}, {1}… or {name}: the arguments, or the one object given.
  const t = (key, ...args) => {
    const p = args.length === 1 && args[0] !== null && typeof args[0] === 'object' ? args[0] : args;
    return String(T[LANG][key] ?? T.en[key] ?? key).replace(/\{(\w+)\}/g, (_, k) => String(p[k] ?? ''));
  };
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
  // Poll after poll only what differs is changed: focus, a selection or a scroll position survive,
  // and screen readers are not told the same thing again.
  const setText = (node, text) => { if (node.textContent !== text) node.textContent = text; };
  const setProp = (node, prop, value) => { if (node[prop] !== value) node[prop] = value; };
  const setClass = (node, cls) => { if (node.className !== cls) node.className = cls; };
  const refs = new WeakMap(); // an item's parts (as expandos, `title` would be the element's own)
  function pill(node, text, kind) {
    setText(node, text);
    setClass(node, `pill ${kind || ''}`.trim());
  }
  function announce(text) {
    const box = $('announce');
    box.textContent = '';
    if (text) setTimeout(() => { box.textContent = text; }, 60); // (the same words again are said again)
  }
  const fmt = (seconds) => {
    const s = Math.max(0, Math.floor(seconds));
    const h = Math.floor(s / 3600);
    const m = String(Math.floor(s / 60) % 60).padStart(h ? 2 : 1, '0');
    return `${h ? `${h}:` : ''}${m}:${String(s % 60).padStart(2, '0')}`;
  };

  // ── talking to the computer ──
  function deadline(ms) {
    if (AbortSignal.timeout) return AbortSignal.timeout(ms);
    const c = new AbortController();
    setTimeout(() => c.abort(new DOMException('timeout', 'TimeoutError')), ms);
    return c.signal;
  }
  async function api(path, body) {
    const opts = { method: body === undefined ? 'GET' : 'POST', headers: { 'X-Lithify-Wizard': token },
      cache: 'no-store', signal: deadline(10000) };
    if (body !== undefined) {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(body);
    }
    let r;
    try {
      r = await fetch(path, opts);
    } catch (e) {
      const name = e && e.name;
      const err = new Error(t(name === 'TimeoutError' || name === 'AbortError' ? 'e_timeout' : 'e_network'));
      err.offline = true;
      throw err;
    }
    let data = null;
    try { data = await r.json(); } catch (_) { data = null; }
    if (!r.ok) {
      const err = new Error((data && data.error) || `HTTP ${r.status}`);
      err.status = r.status;
      err.key = data && data.error_key;
      throw err;
    }
    return data;
  }

  const STEPS = ['welcome', 'computer', 'speaker', 'install', 'done'];
  const PHASES = ['prepare', 'build', 'install', 'restart', 'check'];
  const NOTES = { build: 'ph_build_note', restart: 'ph_restart_note', check: 'ph_check_note' };
  // (a published bundle is downloaded in the build's place, and nothing is compiled)
  const phaseLabel = (p) => t(p === 'build' && st.software === 'download' ? 'ph_download' : `ph_${p}`);
  // Which step shows what went wrong in a task.
  const TASK_STEP = { check: 'computer', 'start-docker': 'computer', discover: 'speaker', probe: 'speaker', install: 'install' };
  let st = null; // the last state from the computer
  const drawn = { rev: -1, lang: '', step: '', error: '', phase: '', task: '', ask: '' };
  let offset = 0; // the computer's clock minus this browser's, in seconds
  let timer = 0;
  let misses = 0; // polls in a row without an answer
  let over = false; // "Finish" was clicked
  let gone = false;
  let nameFor = ''; // the speaker whose name the field was filled with
  let lastHost = '';
  const inflight = new Set();

  const running = () => Boolean(st && st.task && !st.task.finished);
  const ports = () => st.firewall.ports.split(',').join(', ');
  const clock = () => Date.now() / 1000 + offset;

  async function poll() {
    clearTimeout(timer);
    if (over) return;
    try {
      const s = await api('/api/state');
      misses = 0;
      offset = s.now - Date.now() / 1000;
      if (!picked && s.lang && s.lang !== LANG) LANG = s.lang;
      picked = true; // (the wizard's --lang is taken once: after that, this page's switch decides)
      st = s;
      if (gone) leaveGone();
      draw();
    } catch (e) {
      if (e.status === 403) {
        showEnd('gone_title', 'e_token');
        return;
      }
      misses += 1;
      if (misses >= 3 && st) showGone();
    }
    // (stopped: asked now and then only, in case it was merely busy or this computer slept)
    timer = setTimeout(poll, gone ? 10000 : running() ? 1000 : 5000);
  }

  async function act(path, body = {}) {
    if (inflight.has(path)) return;
    inflight.add(path);
    notice('');
    try {
      await api(path, body);
    } catch (e) {
      notice(e.key === 'busy' ? t('e_busy') : e.key === 'bad_speaker' ? t('e_bad_speaker')
        : e.key === 'token' ? t('e_token') : e.offline ? e.message : t('e_other', e.message));
    } finally {
      inflight.delete(path);
    }
    poll();
  }
  function notice(text) {
    setText($('notice'), text);
    setProp($('notice'), 'hidden', !text);
  }

  // ── drawing ──
  function applyStatic() {
    document.documentElement.lang = LANG;
    document.title = t('doc_title');
    for (const node of document.querySelectorAll('[data-t]')) node.textContent = t(node.dataset.t);
    for (const node of document.querySelectorAll('[data-t-aria]')) node.setAttribute('aria-label', t(node.dataset.tAria));
    for (const b of document.querySelectorAll('[data-lang]')) b.setAttribute('aria-pressed', String(b.dataset.lang === LANG));
  }

  function draw(force = false) {
    if (!st || over) return;
    if (LANG !== drawn.lang) {
      applyStatic();
      force = true;
    }
    if (force || st.rev !== drawn.rev) {
      drawn.rev = st.rev;
      drawn.lang = LANG;
      drawSteps();
      ({ welcome: drawWelcome, computer: drawComputer, speaker: drawSpeaker, install: drawInstall,
        done: drawDone })[st.step]();
      drawError(force);
      tellChanges();
    }
    tick();
  }

  // (Docker Desktop on Windows and macOS; on Linux the distribution's Docker. None, and a few
  // minutes, when the speaker's software is here already or published: nothing is built.)
  function drawWelcome() {
    const build = st.software === 'build';
    setText($('w-2'), t(build ? 'w_2' : 'w_2_ready'));
    setText($('w-time'), t(build ? 'w_time' : 'w_time_ready'));
    setText($('need-docker'), t(has(`n_2_${st.os}`) ? `n_2_${st.os}` : 'n_2'));
    setProp($('need-docker'), 'hidden', !build);
  }

  function drawSteps() {
    $('s-loading').hidden = true;
    const at = STEPS.indexOf(st.step);
    for (const li of $('stepper').children) {
      const i = STEPS.indexOf(li.dataset.step);
      setClass(li, i < at ? 'done' : i === at ? 'current' : '');
      if (i === at) li.setAttribute('aria-current', 'step');
      else li.removeAttribute('aria-current');
      setProp(li.querySelector('[data-t="step_completed"]'), 'hidden', i >= at);
    }
    for (const sec of document.querySelectorAll('section.step')) setProp(sec, 'hidden', sec.dataset.step !== st.step);
    if (drawn.step !== st.step) {
      const first = drawn.step === '';
      drawn.step = st.step;
      notice('');
      if (!first) $(`h-${st.step}`).focus(); // (a new step: its heading, so it is read from the top)
    }
  }

  // What the page offers to fix a problem: the explanation (the computer names its key; a text for
  // this system first, where they differ), commands to copy, a link, a button.
  function fixKey(fix) {
    for (const k of fix ? [`${fix}_${st.os}`, fix] : []) if (has(k)) return k;
    return '';
  }
  function linkKey(problem) {
    const p = problem === 'docker_desktop_missing' ? 'docker_missing' : problem;
    for (const k of [`link_${p}_${st.os}`, `link_${p}`]) if (has(k)) return k;
    return '';
  }
  function cmdBlock(command) {
    const b = el('button', { type: 'button', class: 'copy', text: t('copy'), 'aria-label': t('copy_cmd') });
    b.addEventListener('click', () => copy(command, 'copied_cmd', b));
    return el('div', { class: 'cmd' }, el('pre', {}, el('code', { text: command })), b);
  }
  function fixNodes(problem, fix, link, commands, params) {
    const out = [];
    const fk = fixKey(fix);
    if (fk) out.push(el('p', { text: t(fk, { 0: ports(), ...params }) }));
    for (const c of commands || []) out.push(cmdBlock(c));
    if (link && /^https:\/\//.test(link) && linkKey(problem)) {
      out.push(el('p', {}, el('a', { href: link, target: '_blank', rel: 'noopener noreferrer', text: t(linkKey(problem)) })));
    }
    return out;
  }
  function button(key, onClick, cls = '') {
    const b = el('button', { type: 'button', class: cls || null, text: t(key) });
    b.addEventListener('click', onClick);
    return b;
  }

  // Lists whose items keep their elements: new ones are added, gone ones removed, the rest updated.
  function keyed(list, items, keyOf, make, fill) {
    const old = new Map([...list.children].map((n) => [n.dataset.key, n]));
    let prev = null;
    for (const item of items) {
      const k = keyOf(item);
      let node = old.get(k);
      if (!node) {
        node = make(item);
        node.dataset.key = k;
      }
      old.delete(k);
      fill(node, item);
      const want = prev ? prev.nextSibling : list.firstChild;
      if (node !== want) list.insertBefore(node, want);
      prev = node;
    }
    for (const n of old.values()) n.remove();
  }

  function drawComputer() {
    const c = st.computer;
    const task = st.task;
    const checking = running() && task.kind === 'check';
    const starting = running() && task.kind === 'start-docker';
    const status = $('computer-status');
    status.dataset.mode = starting ? 'starting' : '';
    if (checking || starting) {
      setText(status, checking ? t('checking') : t('starting_docker', fmt(clock() - task.started)));
      setClass(status, 'msg spin');
    } else if (c) {
      setText(status, t(c.ok ? 'computer_ok' : 'computer_bad'));
      setClass(status, `msg ${c.ok ? 'ok' : 'bad'}`);
    }
    setProp(status, 'hidden', !(checking || starting || c));
    keyed($('checks'), c ? c.checks : [], (x) => x.key, () => {
      const r = { pill: el('span', { class: 'pill' }), name: el('span'), msg: el('p'), fix: el('div', { class: 'fix' }) };
      const li = el('li', { class: 'check' }, el('h3', {}, r.name, r.pill), r.msg, r.fix);
      refs.set(li, r);
      return li;
    }, (li, x) => {
      const r = refs.get(li);
      setClass(li, `check ${x.status}`);
      setText(r.name, t(`c_${x.key}`));
      pill(r.pill, t(`st_${x.status}`), x.status);
      setText(r.msg, t(x.msg, x.params));
      const sig = [x.problem, LANG, x.link, (x.commands || []).join('|'), x.action, running()].join('/');
      if (li.dataset.sig === sig) return;
      li.dataset.sig = sig;
      const nodes = x.problem ? fixNodes(x.problem, x.fix, x.link, x.commands, {}) : [];
      if (x.action === 'start-docker') {
        const b = button('start_docker', () => act('/api/start-docker'), 'primary');
        b.disabled = running();
        nodes.push(el('div', { class: 'actions' }, b));
      }
      r.fix.replaceChildren(...nodes);
    });
    setProp($('btn-check'), 'disabled', running());
    setProp($('btn-next'), 'disabled', running() || !(c && c.ok));
  }

  // A typed address where nothing answers is no speaker found: it is shown by its address.
  const silent = (s) => s.supported === false && s.reason_key === 'not_found';
  const answered = () => st.speakers.filter((s) => !silent(s)).length;
  const verdict = (s) => (s.supported == null ? 'sp_checking' : s.supported ? 'sp_supported'
    : silent(s) ? 'sp_silent' : 'sp_unsupported');

  function drawSpeaker() {
    const task = st.task;
    const searching = running() && (task.kind === 'discover' || task.kind === 'probe');
    const status = $('speaker-status');
    if (searching) {
      setText(status, task.kind === 'discover' ? t('searching') : t('probing', lastHost || ''));
      setClass(status, 'msg spin');
    } else if (st.searched || st.speakers.length) {
      const n = answered();
      setText(status, n ? t('found_n', n) : t('none_found'));
      setClass(status, `msg ${n ? '' : 'warn'}`.trim());
    }
    setProp(status, 'hidden', !(searching || st.searched || st.speakers.length));
    keyed($('speakers'), st.speakers, (s) => s.host, () => {
      const r = { name: el('span', { class: 'name' }), pill: el('span', { class: 'pill' }), dtModel: el('dt'),
        model: el('dd'), dtAddr: el('dt'), addr: el('dd'), dtLithify: el('dt'), lithify: el('dd'),
        reason: el('p', { class: 'hint' }), use: el('button', { type: 'button', class: 'primary' }) };
      const li = el('li', { class: 'speaker' }, el('h3', {}, r.name, r.pill),
        el('dl', {}, r.dtModel, r.model, r.dtAddr, r.addr, r.dtLithify, r.lithify), r.reason, r.use);
      r.use.addEventListener('click', () => act('/api/choose', { host: li.dataset.key }));
      refs.set(li, r);
      return li;
    }, (li, s) => {
      const r = refs.get(li);
      const quiet = silent(s);
      const name = quiet ? s.host : s.name || t('unnamed');
      const chosen = Boolean(s.supported && st.chosen && st.chosen.host === s.host);
      setClass(li, `speaker ${s.supported ? 'ok' : ''} ${chosen ? 'chosen' : ''}`.trim().replace(/\s+/g, ' '));
      setText(r.name, name);
      pill(r.pill, t(verdict(s)), s.supported == null ? '' : s.supported ? 'ok' : quiet ? 'warn' : 'bad');
      setText(r.dtModel, t('k_model'));
      setText(r.model, s.model || t('model_unknown'));
      setText(r.dtAddr, t('k_address'));
      setText(r.addr, s.host);
      for (const n of [r.dtModel, r.model, r.dtAddr, r.addr]) setProp(n, 'hidden', quiet); // (no speaker: no details)
      setText(r.dtLithify, t('k_lithify'));
      setText(r.lithify, s.librespot_version ? t('lithify_installed', s.librespot_version) : '');
      setProp(r.dtLithify, 'hidden', !s.librespot_version);
      setProp(r.lithify, 'hidden', !s.librespot_version);
      const why = s.supported === false && s.reason_key ? t(`r_${s.reason_key}`, s.reason) : '';
      setText(r.reason, why);
      setProp(r.reason, 'hidden', !why);
      setText(r.use, t(chosen ? 'chosen_btn' : 'use_speaker'));
      r.use.setAttribute('aria-label', t(chosen ? 'chosen_aria' : 'use_aria', name));
      r.use.setAttribute('aria-pressed', String(chosen));
      setProp(r.use, 'hidden', !s.supported);
      setProp(r.use, 'disabled', running());
    });
    setProp($('btn-search'), 'disabled', running());
    setProp($('btn-add'), 'disabled', running());
    drawChoose();
  }

  // Under the list: the chosen speaker's name in Spotify, and the button that installs.
  function drawChoose() {
    const c = st.chosen || {};
    const ok = Boolean(c.host && st.speakers.some((s) => s.host === c.host && s.supported));
    setProp($('choose'), 'hidden', !ok);
    if (!ok) return;
    const input = $('spotify-name');
    if (nameFor !== c.host) { // (once per speaker: what is typed is never overwritten)
      input.value = c.spotify_name || c.default_name || '';
      nameFor = c.host;
    }
    const official = c.official_name ? t('name_official', c.official_name) : '';
    setText($('name-official'), official);
    setProp($('name-official'), 'hidden', !official);
    const current = c.current_name ? t('name_current', c.librespot_version || '?', c.current_name) : '';
    setText($('name-current'), current);
    setProp($('name-current'), 'hidden', !current);
    setText($('name-summary'), t('name_summary', c.name || t('unnamed'), c.model || t('model_unknown'), c.host || ''));
    setText($('before-install'), t(st.software === 'build' ? 'before_install' : 'before_install_ready'));
    // (Windows asks nothing when its firewall lets the speakers in already: the installer added the rule)
    const hint = st.os === 'windows' && st.firewall.ready ? '' : t(`hint_${st.os}`, ports());
    setText($('name-os'), hint);
    setProp($('name-os'), 'hidden', !hint);
    setProp($('btn-install'), 'disabled', running());
  }

  function phaseRow(key) {
    const r = { label: el('span', { class: 'label', id: `ph-${key}` }), state: el('span', { class: 'state' }),
      time: el('span', { class: 'time' }), bar: el('progress', { max: '1', value: '0', 'aria-labelledby': `ph-${key}` }),
      note: el('p', { class: 'note' }) };
    const li = el('li', { class: 'phase pending' }, el('div', { class: 'head' }, r.label, r.state, r.time), r.bar, r.note);
    refs.set(li, r);
    return li;
  }
  // A bar's value; null: under way, without a measure (an indeterminate bar).
  function setBar(bar, value) {
    if (value == null) {
      if (bar.hasAttribute('value')) bar.removeAttribute('value');
    } else if (!bar.hasAttribute('value') || bar.value !== value) {
      bar.value = value;
    }
  }

  function drawInstall() {
    const c = st.chosen || {};
    setText($('h-install'), t('install_title', c.name || c.spotify_name || c.host || ''));
    const task = st.task && st.task.kind === 'install' ? st.task : null;
    const list = $('phases');
    if (!list.children.length) for (const p of PHASES) list.append(phaseRow(p));
    PHASES.forEach((p, i) => {
      const li = list.children[i];
      const r = refs.get(li);
      const ph = task ? task.phases.find((x) => x.key === p) : { state: 'pending' };
      setClass(li, `phase ${ph.state}`);
      setText(r.label, phaseLabel(p));
      setText(r.state, t(`ps_${ph.state}`));
      if (ph.state === 'active') setBar(r.bar, p === 'build' && task.progress != null ? task.progress : null);
      else setBar(r.bar, ph.state === 'done' || ph.state === 'failed' ? 1 : 0);
      const noted = NOTES[p] && (p !== 'build' || st.software === 'build') && (ph.state === 'pending' || ph.state === 'active');
      const note = noted ? t(NOTES[p]) : '';
      setText(r.note, note);
      setProp(r.note, 'hidden', !note);
    });
    const live = task && !task.finished;
    const now = $('install-now');
    if (live && task.phase && PHASES.includes(task.phase)) {
      const label = phaseLabel(task.phase);
      setText(now, t('now', task.step ? t('now_step', label, t(`sub_${task.step}`)) : label));
    }
    setProp(now, 'hidden', !live);
    const cancel = $('btn-cancel');
    setProp(cancel, 'hidden', !live);
    setProp(cancel, 'disabled', Boolean(task && task.cancelled));
    setText(cancel, task && task.cancelled ? t('stopping') : t('cancel'));
    setProp($('btn-back-install'), 'hidden', live || !task);
    if (task) setLog(task.lines);
  }

  function setLog(lines) {
    const pre = $('log');
    const text = lines.join('\n');
    if (pre.textContent === text) return;
    const atEnd = pre.scrollHeight - pre.scrollTop - pre.clientHeight < 24; // (scrolled up: left there)
    pre.textContent = text;
    if (atEnd) pre.scrollTop = pre.scrollHeight;
  }

  // The time shown under way, every second (the computer is asked only every second or five).
  function tick() {
    if (!st || !st.task) return;
    const task = st.task;
    const end = task.finished ? task.ended : clock();
    if (st.step === 'install' && task.kind === 'install') {
      setText($('install-time'), t('elapsed', fmt(end - task.started)));
      PHASES.forEach((p, i) => {
        const ph = task.phases.find((x) => x.key === p);
        const li = $('phases').children[i];
        if (!li) return;
        const shown = ph.started && (ph.state === 'active' || ph.state === 'done' || ph.state === 'failed')
          ? fmt((ph.ended || end) - ph.started) : '';
        setText(refs.get(li).time, shown);
      });
    }
    const status = $('computer-status');
    if (st.step === 'computer' && status.dataset.mode === 'starting' && !task.finished) {
      setText(status, t('starting_docker', fmt(clock() - task.started)));
    }
  }

  function drawDone() {
    const o = st.outcome || {};
    const link = $('page-link');
    const url = /^http:\/\/[A-Za-z0-9.:[\]-]+\/$/.test(o.page_url || '') ? o.page_url : '';
    if (url) link.setAttribute('href', url);
    setProp(link, 'hidden', !url);
    setText($('page-help'), url ? t('page_help', url) : '');
    setProp($('page-quiet'), 'hidden', !o.page_quiet);
    setText($('how-3'), t('how_3', o.spotify_name || ''));
    const helper = $('helper');
    setText(helper, o.helper_ok ? t(has(`helper_ok_${st.os}`) ? `helper_ok_${st.os}` : 'helper_ok') : t(o.helper_key || 'helper_failed'));
    setClass(helper, `msg ${o.helper_ok ? 'ok' : 'warn'}`);
    setText($('helper-detail'), o.helper_detail || '');
    setProp($('helper-more'), 'hidden', o.helper_ok || !o.helper_detail);
  }

  // A step that failed: a red box in its step, with what happened, what to do, and "Try again".
  function drawError(force) {
    const task = st.task;
    const failed = Boolean(task && task.finished && task.ok === false && TASK_STEP[task.kind] === st.step);
    const sig = failed ? [st.step, task.kind, task.started, task.error_key, task.error_detail.length, LANG].join('|') : '';
    if (sig === drawn.error && !force) return;
    drawn.error = sig;
    for (const old of document.querySelectorAll('.errbox')) old.remove();
    if (!failed) return;
    const key = task.error_key || 'unknown';
    const box = el('div', { class: 'errbox', role: 'alert' });
    box.append(el('h3', { text: t(has(`err_${key}`) ? `err_${key}` : 'err_unknown') }));
    const known = has(`err_${key}`);
    box.append(...fixNodes(known ? key : 'unknown', known ? task.error_fix : 'fix_unknown', task.error_link,
      task.error_commands, task.error_params));
    if (task.error_detail) {
      const more = el('details', {}, el('summary', { text: t('details_title') }), el('pre', { text: task.error_detail }));
      if (key === 'build_failed') more.open = true; // (its last 30 lines say what failed)
      box.append(more);
    }
    const actions = el('div', { class: 'actions' }, button('try_again', retry, 'primary'));
    if (task.kind === 'install' && /^docker_/.test(key)) actions.append(button('check_computer', () => act('/api/check-computer')));
    if (task.kind === 'install' && (key === 'unreachable' || key === 'unsupported')) {
      actions.append(button('other_speaker', () => act('/api/discover')));
    }
    if (task.kind === 'start-docker' && task.error_action !== 'start-docker') actions.append(button('check_again', () => act('/api/check-computer')));
    if (task.lines.length) {
      const b = button('copy_log', () => copy(task.lines.join('\n'), 'copied_log', b));
      actions.append(b);
    }
    box.append(actions);
    document.querySelector(`#s-${st.step} .errslot`).append(box);
  }

  function retry() {
    const task = st.task;
    const c = st.chosen || {};
    ({
      check: () => act('/api/check-computer'),
      'start-docker': () => act('/api/start-docker'),
      discover: () => act('/api/discover'),
      probe: () => (lastHost ? act('/api/add-host', { host: lastHost }) : act('/api/discover')),
      install: () => act('/api/install', { host: c.host, name: c.spotify_name || $('spotify-name').value.trim() }),
    })[task.kind]();
  }

  // Said once to screen readers: a new phase, and how a step ended (never each line of the log).
  function tellChanges() {
    const task = st.task;
    if (!task) return;
    const id = `${task.kind}@${task.started}`;
    if (task.kind === 'install' && !task.finished && task.phase && task.phase !== drawn.phase && PHASES.includes(task.phase)) {
      drawn.phase = task.phase;
      announce(t('ann_phase', t(`ph_${task.phase}`)));
    }
    const asking = task.kind === 'install' && !task.finished && task.step === 'firewall' ? id : '';
    if (asking && asking !== drawn.ask) announce(t('sub_firewall')); // (Windows waits for an answer)
    drawn.ask = asking;
    const ended = task.finished ? `${id}:${task.ok}` : '';
    if (!ended || ended === drawn.task) return;
    const first = drawn.task === '';
    drawn.task = ended;
    if (first) return; // (it ended before this page was opened)
    if (task.kind === 'install') announce(task.ok ? t('ann_installed') : t('ann_failed', t(`err_${task.error_key || 'unknown'}`)));
    else if ((task.kind === 'check' || task.kind === 'start-docker') && task.ok) announce(t('ann_checked'));
    else if (task.kind === 'discover' && task.ok) announce(t('ann_found', answered()));
    else if (task.kind === 'probe' && task.ok && st.speakers.length) { // (a typed address is listed first)
      const s = st.speakers[0];
      announce(t('ann_probe', silent(s) ? s.host : s.name || s.host, t(verdict(s))));
    }
  }

  async function copy(text, okKey, b) {
    try {
      await navigator.clipboard.writeText(text);
    } catch (_) {
      announce(t('copy_failed'));
      return;
    }
    announce(t(okKey));
    if (b) {
      const was = b.textContent;
      b.textContent = t('copied');
      setTimeout(() => { b.textContent = was; }, 2000);
    }
  }

  function showEnd(titleKey, textKey) {
    over = titleKey === 'closed_title' || textKey === 'e_token';
    clearTimeout(timer);
    for (const sec of document.querySelectorAll('section.step, #s-loading')) sec.hidden = true;
    $('stepper').parentElement.hidden = true;
    notice('');
    const sec = $('s-closed');
    sec.dataset.title = titleKey;
    sec.dataset.text = textKey;
    setText($('h-closed'), t(titleKey));
    setText($('closed-text'), t(textKey));
    sec.hidden = false;
    $('h-closed').focus();
  }
  function showGone() {
    if (gone) return;
    gone = true;
    showEnd('gone_title', 'gone_text');
  }
  function leaveGone() { // (it answers again: back to where it is)
    gone = false;
    $('s-closed').hidden = true;
    $('stepper').parentElement.hidden = false;
    drawn.step = '';
    drawn.rev = -1;
  }

  // ── what the buttons do ──
  function addHost() {
    const input = $('manual-host');
    const host = input.value.trim();
    const ok = /^[A-Za-z0-9.:-]{1,253}$/.test(host) && !/^[-.]/.test(host);
    setText($('manual-err'), ok ? '' : t('bad_host'));
    setProp($('manual-err'), 'hidden', ok);
    input.setAttribute('aria-invalid', String(!ok));
    if (!ok) {
      input.focus();
      return;
    }
    lastHost = host;
    act('/api/add-host', { host });
  }
  function install() {
    const input = $('spotify-name');
    const name = input.value.trim();
    const n = [...name].length; // (characters, as the speaker counts them)
    const ok = n >= 1 && n <= 64;
    setProp($('name-err'), 'hidden', ok);
    input.setAttribute('aria-invalid', String(!ok));
    if (!ok) {
      input.focus();
      return;
    }
    act('/api/install', { host: (st.chosen || {}).host, name });
  }
  function cancel() {
    const phase = st && st.task && st.task.phase;
    if (!window.confirm(t(phase === 'install' || phase === 'restart' ? 'confirm_cancel_speaker' : 'confirm_cancel'))) return;
    act('/api/cancel');
  }
  async function finish() {
    try { await api('/api/quit', {}); } catch (_) { /* gone already */ }
    showEnd('closed_title', 'closed_text');
  }
  const ACTS = {
    start: () => act('/api/check-computer'),
    check: () => act('/api/check-computer'),
    'back-welcome': () => act('/api/step', { step: 'welcome' }),
    'to-speaker': () => act('/api/discover'),
    search: () => act('/api/discover'),
    'add-host': addHost,
    'back-computer': () => act('/api/step', { step: 'computer' }),
    'back-speaker': () => act('/api/step', { step: 'speaker' }),
    install,
    cancel,
    'copy-log': (b) => copy(st && st.task ? st.task.lines.join('\n') : '', 'copied_log', b),
    another: () => act('/api/discover'),
    finish,
  };
  document.addEventListener('click', (e) => {
    const b = e.target.closest('[data-act]');
    if (b && !b.disabled && ACTS[b.dataset.act]) ACTS[b.dataset.act](b);
  });
  $('manual-host').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      addHost();
    }
  });
  $('spotify-name').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      install();
    }
  });
  $('spotify-name').addEventListener('input', () => {
    setProp($('name-err'), 'hidden', true);
    $('spotify-name').removeAttribute('aria-invalid');
  });
  $('log-box').addEventListener('toggle', () => {
    if ($('log-box').open) $('log').scrollTop = $('log').scrollHeight;
  });
  for (const b of document.querySelectorAll('[data-lang]')) {
    b.addEventListener('click', () => {
      if (b.dataset.lang === LANG) return;
      LANG = b.dataset.lang;
      picked = true;
      store('lithify-lang', LANG);
      applyStatic();
      const end = $('s-closed');
      if (!end.hidden) {
        setText($('h-closed'), t(end.dataset.title));
        setText($('closed-text'), t(end.dataset.text));
      }
      draw(true);
    });
  }
  document.addEventListener('visibilitychange', () => { if (!document.hidden) poll(); });
  setInterval(tick, 1000);

  applyStatic();
  if (!token) showEnd('gone_title', 'e_token');
  else poll();
})();
