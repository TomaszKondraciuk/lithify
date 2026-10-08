# Instalacja

Krótka wersja jest w [README](README.md#instalacja). Tu znajdziesz pytania, które instalator
zadaje w każdym systemie, wymagania sieciowe głośnika, to, co zmienia się na komputerze, opcje
instalatora i instalację ręczną.

## Co jest potrzebne

- Obsługiwany głośnik ([platforms.md](../platforms.md), po angielsku). Kreator albo
  `lithify detect` powie, czy głośnik jest obsługiwany, zanim cokolwiek zmieni.
- **Spotify Premium.** librespot nie działa z darmowymi kontami.
- Komputer w tej samej sieci co głośnik, z Windowsem 10 lub 11, macOS albo Linuksem. Jest
  potrzebny do instalacji i aktualizacji. Głośnik gra bez niego.

## Windows

Pobierz **`Lithify-Windows.cmd`** z
[najnowszego wydania](https://github.com/OWNER/lithify/releases/latest) i kliknij go dwukrotnie.
Windows raz zapyta, czy uruchomić plik z internetu. W oknie „Nie można zweryfikować wydawcy”
kliknij *Uruchom*, a w oknie „System Windows ochronił ten komputer” *Więcej informacji →
Uruchom mimo to*.

Instalator pokazuje, czego komputerowi jeszcze brakuje, i raz prosi o zgodę:

- **Python**, instalowany dla Twojego użytkownika przez winget. Nie wymaga uprawnień
  administratora.
- **Reguła zapory**, żeby głośnik mógł pobrać oprogramowanie z tego komputera. Windows raz
  zapyta o zgodę: *Tak*.
- **Git, Docker Desktop i WSL 2**, tylko gdy Lithify trzeba zbudować na tym komputerze, bo nie da
  się pobrać żadnego wydania. Gdy WSL wymaga ponownego uruchomienia komputera, instalator to
  zaproponuje i sam wznowi pracę po ponownym zalogowaniu.

To samo w PowerShellu:

```powershell
irm https://raw.githubusercontent.com/OWNER/lithify/main/installer/get.ps1 | iex
```

## macOS

Otwórz *Terminal* (⌘ Spacja, „Terminal”), wklej to polecenie i naciśnij Return:

```sh
curl -fsSL https://raw.githubusercontent.com/OWNER/lithify/main/installer/install.sh | sh
```

Gdy na komputerze nie ma Pythona 3.11 lub nowszego, instalator zaproponuje osobnego Pythona tylko
dla Lithify. Nie wymaga on uprawnień administratora i nie zmienia niczego innego na Macu. Jeśli
zapora macOS jest włączona, macOS zapyta, czy Python może przyjmować połączenia przychodzące:
*Pozwól*. Głośnik pobiera oprogramowanie z Twojego komputera.

Instalacja dwuklikiem: otwórz **`Lithify-macOS.zip`** z
[najnowszego wydania](https://github.com/OWNER/lithify/releases/latest) (Safari sam go rozpakuje),
a potem znajdujący się w nim **`Lithify-macOS.command`**. Za pierwszym razem macOS go zablokuje:

- **macOS 15 i nowszy:** *Ustawienia systemowe → Prywatność i ochrona → Otwórz mimo to*.
- **macOS 14 i starszy:** kliknij plik prawym przyciskiem → *Otwórz* → *Otwórz*.

Gdy macOS zapyta, czy Terminal może korzystać z folderu Pobrane: *Pozwól*.

## Linux

W terminalu:

```sh
wget -qO- https://raw.githubusercontent.com/OWNER/lithify/main/installer/install.sh | sh
```

Ubuntu i Debian mają wget, ale nie mają curl. Tam, gdzie curl jest zainstalowany,
`curl -fsSL https://raw.githubusercontent.com/OWNER/lithify/main/installer/install.sh | sh`
działa tak samo. Gdy działa ufw albo firewalld, instalator pokaże polecenie, które otwiera porty
TCP 8095 i 18096–18099 dla sieci lokalnej, i zaproponuje, że je wykona (przez `sudo`).

Instalacja dwuklikiem: rozpakuj **`Lithify-Linux.zip`** z
[najnowszego wydania](https://github.com/OWNER/lithify/releases/latest) i uruchom znajdujący się w
nim **`Lithify-Linux.sh`**. GNOME (pulpit Ubuntu) otworzy go w edytorze tekstu: zamknij edytor,
kliknij plik prawym przyciskiem → *Uruchom jako program*.

## Wymagania sieciowe

W typowej sieci domowej, gdy komputer i głośniki są w tym samym Wi-Fi albo LAN-ie, nie trzeba
niczego ustawiać. Jeśli głośniki są w osobnej sieci (VLAN dla urządzeń IoT albo Wi-Fi dla gości),
sieci muszą przepuszczać taki ruch:

| Skąd | Dokąd | Port | Do czego |
|---|---|---|---|
| komputer | głośnik | TCP 23 | instalacja (konsola serwisowa głośnika) |
| komputer | głośnik | TCP 8008 | znajdowanie głośnika i pytanie, czym jest (Google Cast) |
| komputer lub telefon | głośnik | TCP 8090 | strona WWW głośnika |
| głośnik | komputer | TCP 8095, 18096–18099 | pobieranie Lithify i aktualizacji od pomocnika |
| telefon | głośnik | mDNS (UDP 5353) i TCP 4070 | Spotify znajduje librespot i się z nim łączy |
| głośnik | internet | TCP 443 i 4070 | Spotify |

Kreator przeszukuje tylko sieć komputera (jego /24, np. 192.168.1.0–255). Głośnik w innej sieci:
wpisz jego adres w kreatorze albo ustaw `LITHIFY_HOST=<adres głośnika>` przy uruchamianiu
instalatora. Spotify znajduje librespot przez mDNS: między sieciami wymaga to reflektora mDNS na
routerze (często nazywanego „mDNS repeater” albo „Bonjour gateway”).

VPN na komputerze albo „izolacja klientów” w Wi-Fi („AP isolation”) nie pozwalają głośnikowi
połączyć się z komputerem. Instalator sprawdza to, zanim cokolwiek zmieni, i przerywa z
komunikatem, że głośnik nie może pobierać z tego komputera.

## Co się zmienia na komputerze

| | Windows | macOS i Linux |
|---|---|---|
| Lithify | `%LOCALAPPDATA%\lithify\app` | `~/.local/share/lithify` |
| Polecenie `lithify` | `%LOCALAPPDATA%\lithify\bin` | `~/.local/bin` |
| Python, jeśli go nie było | Python 3.12 dla Twojego użytkownika (winget) | osobny Python w `~/.local/share/lithify-tools` |
| Konfiguracja | `%APPDATA%\lithify` | `~/.config/lithify` |
| Pobrane i zbudowane paczki | `%LOCALAPPDATA%\lithify\cache` | `~/.cache/lithify` |
| Pomocnik, dzięki któremu głośniki można aktualizować | zadanie w Harmonogramie zadań | agent launchd (macOS), usługa użytkownika systemd (Linux) |

Docker i git instalują się tylko wtedy, gdy Lithify jest budowany na komputerze. Żaden z nich nie
trafia na głośnik.

Okno instalatora pokazuje każdy krok i zostaje otwarte na końcu. Możesz go uruchomić ponownie w
każdej chwili. Pominie to, co już jest zrobione, i zaktualizuje Lithify oraz głośnik do
najnowszego wydania.

## Opcje

Instalatory czytają te zmienne środowiskowe:

| Zmienna | Działanie |
|---|---|
| `LITHIFY_YES=1` | „tak” na każde pytanie |
| `LITHIFY_LANG=pl` lub `en` | język komunikatów (domyślnie język komputera) |
| `LITHIFY_HOST=<adres>` | adres głośnika: bez szukania, terminal zamiast kreatora |
| `LITHIFY_NAME=<nazwa>` | jego nazwa w Spotify |
| `LITHIFY_NO_WIZARD=1` | terminal zamiast przeglądarki |
| `LITHIFY_NO_SETUP=1` | tylko Lithify i polecenie `lithify` |
| `LITHIFY_HOME` | gdzie trafia Lithify (`LITHIFY_BIN`: polecenie, na macOS i Linuksie) |
| `LITHIFY_REPO`, `LITHIFY_ARCHIVE_URL` | repozytorium git albo plik `.zip` (lub `.tar.gz`) używany zamiast gita |

Przez SSH, na Linuksie bez ekranu albo z ustawionym `LITHIFY_HOST` część dotycząca głośnika
działa w terminalu zamiast w przeglądarce. W kopii repozytorium `./installer/install.sh` (lub
`.\installer\install.ps1`) używa tej kopii na miejscu.

## Ręcznie

Gdy polecenie `lithify` jest już zainstalowane:

```sh
lithify wizard                     # kreator w przeglądarce
lithify install                    # albo w terminalu: za pierwszym razem znajduje głośnik
lithify serve --install-service    # pomocnik dla strony głośnika
lithify ui                         # adres strony głośnika
```

Wszystkie polecenia (po angielsku): [commands.md](../commands.md).

## Aktualizacja

*Zaktualizuj wszystko* na stronie głośnika instaluje najnowsze wydanie. Ponowne uruchomienie
instalatora albo `lithify update` robi to samo. Komputer pobiera wydanie po HTTPS, sprawdza podpis
Lithify na liście sum kontrolnych, a potem każdy plik z tą listą. Głośnik instaluje wydanie tylko
wtedy, gdy jest nowsze od tego, które ma, i zachowuje swoje ustawienia. *Przywróć poprzednią
wersję* na stronie albo `lithify rollback` wraca do poprzedniej wersji.

Aktualizacja firmware może przywrócić fabryczną listę programów uruchamianych przez głośnik i
wtedy Lithify przestaje się uruchamiać. Jego pliki zostają na głośniku: uruchom instalator
ponownie (albo `lithify update`), żeby go przywrócić.

Najnowsze wersje możesz też zbudować sam (Docker i git):

```sh
lithify build --latest && lithify update
```

To przenosi librespot, alsa-lib i Rusta na najnowsze wersje i je buduje. Pobrane wydanie nigdy
nie zastąpi kompilacji zrobionej na komputerze po tym wydaniu. Szczegóły (po angielsku):
[configuration.md](../configuration.md#build-pins).

## Usuwanie

1. **Głośnik.** Na komputerze uruchom:

   ```sh
   lithify uninstall
   ```

   To przywraca fabryczną listę programów głośnika, usuwa katalog Lithify z głośnika
   (`/lsync/lithify`) i pyta, zanim uruchomi głośnik ponownie. Przy kilku głośnikach dodaj
   `--speaker <id>` i powtórz dla każdego.
2. **Pomocnik.** Uruchom `lithify serve --uninstall-service`.
3. **Pliki.** Usuń katalogi wymienione w
   [Co się zmienia na komputerze](#co-się-zmienia-na-komputerze). Na Windowsie usuń też regułę
   zapory „Lithify” (*Zapora Windows Defender → Ustawienia zaawansowane → Reguły przychodzące*).

Python, a także Docker i git, jeśli dodał je instalator, zostają zainstalowane. Jeśli nie są Ci
już potrzebne, usuń je jak każdy inny program.
