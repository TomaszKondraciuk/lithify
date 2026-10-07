# Lithify – po polsku

Lithify instaluje na głośnikach Lithe Audio Wi-Fi nowoczesnego klienta Spotify Connect
([librespot](https://github.com/librespot-org/librespot)) obok oryginalnego oprogramowania
głośnika. W Spotify pojawia się drugie urządzenie, które szybciej się łączy, nie zawiesza się na
starcie utworu i daje się aktualizować. Na głośniku działa też prosta strona WWW: stan, testy,
aktualizacje i przywracanie poprzedniej wersji.

Nic nie jest flashowane. Firmware, Google Cast, AirPlay i oficjalny Spotify zostają; Lithify
dodaje pliki w jednym katalogu (`/lsync/lithify`) i dwa wpisy na liście usług. `lithify uninstall`
usuwa jedno i drugie.

## Czego potrzebujesz

- głośnik na platformie **LS9**: Wi-Fi Ceiling Speaker V2 (pojedynczy lub para), WiFi PRO,
  Micro Subwoofer (modele V3 / PRO 2 / iO1 – platforma LS10 – nie są jeszcze obsługiwane);
- komputer z **Windowsem, macOS albo Linuksem** w tej samej sieci. Niczego nie trzeba instalować
  wcześniej: instalator przygotuje komputer sam i **zapyta, zanim cokolwiek zainstaluje**:
  - **Python 3.11+** – ten, który komputer już ma, a jeśli go brak: prywatny Python 3.12 tylko dla
    Lithify, bez uprawnień administratora;
  - **Docker i git**, które budują Lithify dla głośnika (za pierwszym razem 10–20 minut; na głośnik
    nic z nich nie trafia) – instalator zaproponuje ich instalację i uruchomienie i powie, co
    zrobić, gdy sam nie da rady (restart, WSL na Windowsie, grupa `docker` na Linuksie). Nie są
    potrzebne, gdy gotowa paczka jest już na komputerze albo opublikowana;
  - **zapora** – głośnik pobiera pliki z komputera (TCP 8095 i 18096–18099): Windows raz zapyta o
    zgodę, na Linuksie instalator zaproponuje otwarcie portów w ufw/firewalld, na macOS kliknij
    *Pozwól*, gdy zapyta o Pythona.

## Instalacja

**Dwuklikiem:** pobierz Lithify (na GitHubie: *Code → Download ZIP*), rozpakuj i otwórz:

| | |
|---|---|
| **Windows** | `Lithify-Windows.cmd` – jeśli Windows ostrzeże („System Windows ochronił ten komputer”), wybierz *Więcej informacji → Uruchom mimo to* |
| **macOS** | `Lithify-macOS.command` – za pierwszym razem: prawy przycisk → *Otwórz* → *Otwórz* (macOS 15 i nowsze: *Ustawienia systemowe → Prywatność i ochrona → Otwórz mimo to*) |
| **Linux** | `Lithify-Linux.sh` – jeśli otworzy się w edytorze: prawy przycisk → *Uruchom jako program*, albo `sh Lithify-Linux.sh` w terminalu |

Okno pokazuje każdy krok i zostaje otwarte na końcu. Można je uruchomić ponownie w każdej chwili –
pominie to, co już zrobione.

Potem w przeglądarce otwiera się **kreator Lithify**: sprawdza komputer, znajduje głośnik, pyta o
jego nazwę w Spotify i instaluje Lithify, pokazując postęp (głośnik raz się zrestartuje – około
minuty bez dźwięku). Przy każdym problemie mówi zwykłymi słowami, co zrobić, i ma przycisk
„Spróbuj ponownie”. Na koniec: otwórz Spotify, wybierz „<głośnik> (librespot)” i zagraj coś –
głośnik zapamięta logowanie. Nazwę i wszystko inne zmienisz na stronie głośnika.

**Jednym poleceniem** – Windows (PowerShell):

```powershell
irm https://raw.githubusercontent.com/OWNER/lithify/main/install.ps1 | iex
```

macOS, Linux:

```sh
curl -fsSL https://raw.githubusercontent.com/OWNER/lithify/main/install.sh | sh
```

Przez SSH, na Linuksie bez ekranu albo z ustawionym `LITHIFY_HOST` część z głośnikiem przebiega w
terminalu zamiast w przeglądarce. To samo ręcznie: `lithify wizard`, albo w terminalu
`lithify install`, `lithify serve --install-service` (pomocnik aktualizacji: Harmonogram zadań na
Windowsie, agent launchd na macOS, usługa systemd na Linuksie) i `lithify ui`.

## Strona na głośniku

`http://<adres-głośnika>:8090` (adres podaje `lithify ui`). Pokazuje stan librespot i oficjalnego
Spotify, Wi-Fi (pasmo, sygnał), wyjście audio i zainstalowane wersje. Przyciski:

- **Testuj** – czy librespot odpowiada, ile było niedoborów bufora i błędów;
- **Testuj połączenia** – czas połączenia z serwerami Spotify i CDN; pokazuje, które zawodzą;
- **Ustawienia** – nazwa w Spotify, jakość dźwięku, regulacja głośności, pomijane serwery CDN,
  PIN strony i opcje zaawansowane; po zapisaniu librespot lub agent uruchamia się ponownie sam;
- **Zaktualizuj wszystko** – jeden przycisk: komputer buduje najnowsze stabilne librespot, Rust,
  alsa-lib i biblioteki, a głośnik je instaluje (jeśli nic się nie zmieniło, nic nie instaluje);
  **Przywróć** wraca do poprzedniej wersji.

Aktualizacje przychodzą wyłącznie z Twojego komputera (`lithify serve`). Ustawiony PIN chroni
ustawienia i akcje; zapomniany usuniesz poleceniem `lithify settings reset-pin`.

## Ustawienia

Ustawienia są zapisane na głośniku i zmienia się je na jego stronie (albo
`lithify settings set name="Kuchnia" bitrate=160`). Aktualizacje ich nie nadpisują. Gdy po zmianie
librespot nie chce działać, agent po chwili sam przywraca ostatnie działające ustawienia (a gdy
winna jest świeżo zainstalowana wersja – poprzednią wersję).

Plik `config.toml` (tworzy go pierwsze `lithify install`: Windows `%APPDATA%\lithify`, inne
`~/.config/lithify`) daje wartości na
pierwszą instalację; `lithify update --settings` wysyła je ponownie:

```toml
[defaults.librespot]
bitrate = 320            # 96 | 160 | 320
mixer = "alsa"           # suwak w Spotify = głośność głośnika

[[speakers]]
id = "lazienka"
host = "192.168.1.40"
name = "Łazienka"        # nazwa w Spotify Connect
```

Wszystkie opcje: [configuration.md](../configuration.md). Każdą można też wysłać zmienną
środowiskową, np. `LITHIFY_LIBRESPOT_BITRATE=160 lithify update`.

## Najczęstsze polecenia

| Polecenie | Co robi |
|---|---|
| `lithify status` | wersje i stan |
| `lithify settings` | ustawienia głośnika (`set klucz=wartość` – zmiana) |
| `lithify update` | zainstaluj nową kompilację (ustawienia głośnika zostają) |
| `lithify check-updates` | czy są nowsze librespot, alsa-lib, Rust, biblioteki, Lithify |
| `lithify logs` | logi agenta i librespot |
| `lithify rollback` | poprzednia wersja |
| `lithify uninstall` | przywrócenie fabrycznego stanu głośnika |

## Problemy

Najpierw strona na głośniku: „Testuj połączenia” wskazuje problemy z siecią, „Testuj” przy
librespot – niedobory bufora i błędy odtwarzania. Pełna lista: [troubleshooting.md](../troubleshooting.md).

## Bezpieczeństwo

Głośniki LS9 mają fabrycznie otwartą konsolę serwisową root bez hasła na porcie TCP 23 – każdy
w Twojej sieci może przejąć głośnik, z Lithify lub bez. Trzymaj je w zaufanej sieci (najlepiej
osobny VLAN/SSID dla IoT) i nigdy nie przekierowuj ich portów z internetu. Szczegóły:
[security.md](../security.md).
