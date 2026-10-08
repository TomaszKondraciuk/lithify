# Rozwiązywanie problemów

Zacznij od strony WWW głośnika. `lithify ui` wypisuje jej adres, zwykle
`http://<głośnik>:8090`. Pasek u góry mówi, co jest nie tak, a każda karta pokazuje, co działa.
Na komputerze `lithify status` pokazuje to samo, a `lithify logs` wypisuje ostatnie wiersze logów
librespot i agenta Lithify.

Komunikaty polecenia `lithify` są po angielsku, więc poniżej podajemy je w oryginale.

## Kreator nie znajduje głośnika

- Komputer i głośnik muszą być w tej samej sieci. Kreator przeszukuje tylko sieć komputera (na
  przykład od 192.168.1.0 do 192.168.1.255).
- Wpisz adres głośnika w kreatorze. Znajdziesz go w aplikacji Lithe Audio albo Google Home (w
  ustawieniach głośnika, w informacjach o urządzeniu) albo na liście urządzeń w routerze.
- „Nie odpowiada na konsoli serwisowej” (`does not answer on the service console (TCP 23)`):
  głośnik nie jest modelem LS9 (zobacz [platforms.md](../platforms.md), po angielsku) albo coś
  między komputerem a głośnikiem blokuje port 23.
- Głośniki w osobnej sieci (VLAN): zobacz
  [Wymagania sieciowe](installation.md#wymagania-sieciowe).

## Nowe urządzenie nie pojawia się w Spotify

- Odczekaj minutę po instalacji albo ponownym uruchomieniu. Strona głośnika powinna pokazywać,
  że librespot działa.
- Telefon i głośnik muszą być w tej samej sieci. Spotify znajduje urządzenie przez mDNS, czyli
  sposób, w jaki urządzenia ogłaszają się w sieci lokalnej. „Izolacja klientów” w Wi-Fi („AP
  isolation”), niektóre sieci dla gości i niektóre systemy mesh to blokują.
- Szukaj nazwy, którą strona pokazuje w *Ustawienia → Nazwa w Spotify*. Domyślnie to nazwa
  głośnika z dopiskiem „(librespot)”.
- Gdy raz na nim zagrasz, głośnik zapamięta logowanie i po ponownym uruchomieniu połączy się sam.
  Strona pokazuje wtedy „Konto Spotify: zapisane”.

## Łączy się, ale nie ma dźwięku

- Sprawdź *Głośność głośnika* na karcie *Spotify Connect* na stronie. Może być na zerze: podnieś
  ją suwakiem w Spotify albo w aplikacji Lithe Audio.
- Głośnik może być zajęty przez inne źródło: Cast, AirPlay, Bluetooth albo wbudowany Spotify.
  Głośnik ma jedno wyjście audio, a wiersz *Teraz* na karcie mówi, kiedy gra inne źródło.
  librespot wstrzymuje to źródło, zanim zacznie grać. Jeśli mu się nie uda, wstrzymaj je w jego
  aplikacji.

## Gra za cicho

Przy domyślnym ustawieniu suwak w Spotify zmienia głośność samego głośnika w pełnym zakresie.
Jeśli *Regulacja głośności* jest ustawiona na „tylko w librespot” (`mixer = "softvol"`), librespot
może tylko ściszyć dźwięk poniżej głośności głośnika. Przełącz to z powrotem na stronie.

## Zacięcia, długi start, przeskakiwanie

- Kliknij **Sprawdź połączenia** na stronie. Problemem są serwery oznaczone jako „brak
  połączenia” albo „wolno”.
- Niektórzy dostawcy internetu słabo docierają do części serwerów z muzyką Spotify (CDN). Spotify
  czeka wtedy 10 sekund albo dłużej, zanim spróbuje innego. Jeśli ten sam serwer ciągle zawodzi,
  dodaj jego nazwę w *Ustawienia → Zaawansowane → Pomijane serwery CDN*, a głośnik od razu go
  pominie.
- Spójrz na kartę *Sieć*. Sygnał słabszy niż około −70 dBm (niższa liczba to słabszy sygnał)
  albo pasmo 2,4 GHz w budynku z wieloma sieciami powodują przerwy. Sieć 5 GHz zwykle pomaga.
- **Sprawdź działanie** na karcie librespot liczy niedobory bufora, czyli chwile, w których
  muzyka się skończyła, zanim dotarła kolejna porcja. Kilka przy zmianie utworu nie szkodzi. Wiele
  w trakcie odtwarzania wskazuje na sieć.

## Wbudowane urządzenie Spotify źle działa

To urządzenie to własny klient Spotify głośnika i Lithify nie może go naprawić. Może go tylko
uruchomić ponownie. Agent robi to sam, gdy klient się zawiesi albo padnie, a strona ma przycisk
ponownego uruchomienia. Używaj urządzenia librespot albo ukryj wbudowane na stronie.

## Po aktualizacji firmware Lithify zniknął

Aktualizacja firmware albo Google Cast może przywrócić listę programów, które głośnik uruchamia.
Pliki Lithify zostają na głośniku. Uruchom instalator ponownie albo `lithify update`: doda Lithify
z powrotem i raz uruchomi głośnik ponownie.

## Przyciski aktualizacji na stronie są wyszarzone

Potrzebują pomocnika Lithify na Twoim komputerze. Pomocnik to mała usługa w tle, którą instaluje
instalator. Sprawdź, czy komputer jest włączony, nie śpi i jest w tej samej sieci co głośnik.
Żeby uruchomić pomocnika ponownie:

```sh
lithify serve --install-service
```

| System | Pomocnik to | Jego log |
|---|---|---|
| Windows | zadanie „Lithify companion” w Harmonogramie zadań | `%LOCALAPPDATA%\lithify\logs\companion.log` |
| macOS | agent launchd | `~/Library/Logs/lithify/companion.log` |
| Linux | usługa użytkownika systemd `lithify-companion` | `systemctl --user status lithify-companion` |

Głośnik poznaje adres komputera przy instalacji Lithify. Jeśli adres komputera od tego czasu się
zmienił, uruchom raz `lithify update`.

## Głośnik nie może pobierać z komputera

Głośnik pobiera Lithify z Twojego komputera przez TCP 8095 i 18096–18099. Instalator sprawdza to,
zanim cokolwiek zmieni. Gdy sprawdzenie się nie uda, przerywa (`the speaker cannot download from
this computer`) i mówi, co zrobić w Twoim systemie. Częste przyczyny:

- **Windows:** brakuje reguł zapory „Lithify”, bo na pytanie o zgodę padła odpowiedź *Nie*,
  albo Windows zablokował Pythona, gdy ktoś kliknął *Anuluj* w pytaniu o Pythona. Uruchom
  instalator ponownie i odpowiedz *Tak*: doda reguły i usunie blokadę.
- **macOS:** zapora jest włączona, a Python nie dostał zgody. Otwórz *Ustawienia systemowe → Sieć
  → Zapora → Opcje* i pozwól Pythonowi na połączenia przychodzące.
- **Linux z ufw:** otwórz porty dla swojej sieci. Zamień `192.168.1.0/24` na swoją:
  `sudo ufw allow from 192.168.1.0/24 to any port 8095,18096:18099 proto tcp`
- **Każdy system:** VPN na komputerze albo Wi-Fi dla gości, które oddziela urządzenia od siebie.

## Ustawienie zmienione na stronie się nie utrzymało

Jeśli librespot nie może działać z nowymi ustawieniami (na przykład z dodatkową opcją, której nie
zna), agent po około 90 sekundach przywraca poprzednie ustawienia i zapisuje to w *Ostatnich
zdarzeniach* na stronie. Popraw wartość i zapisz ponownie.

## Nie pamiętam PIN-u strony

```sh
lithify settings reset-pin
```

To usuwa PIN. Potem ustaw nowy na stronie.

## „Zaktualizuj wszystko” się nie udało

Strona pokazuje dlaczego, a głośnik zostaje przy wersji, którą ma.

- **Lithify zainstalowany z wydania:** komputer nie mógł pobrać najnowszego, bo internet albo
  GitHub był nieosiągalny. Spróbuj później. Nic nie zostaje zastąpione, dopóki każdy plik nie
  dotrze w całości.
- **Lithify zbudowany na Twoim komputerze:** strona pokazuje ostatnie wiersze budowania. Gdy
  nowsze wersje librespot, alsa-lib albo Rusta się nie budują, Lithify zostaje przy poprzednich.
  Spróbuj później albo zgłoś błąd.

Nieudana instalacja zostawia działającą wersję, a **Przywróć poprzednią wersję** wraca do
wcześniejszej.

Gdy aktualizacja samego Lithify się nie uruchamia, pomocnik wraca do kodu, który działał
(`git reset --keep`, które nigdy nie rusza niezatwierdzonych zmian). Jeśli przeszkadzają Twoje
zmiany, zostawia wszystko tak, jak jest, i o tym informuje. Wróć sam przez
`git reset --keep <commit>` albo najpierw zatwierdź swoje zmiany.

## Budowanie Lithify na komputerze

Budujesz tylko wtedy, gdy nie da się pobrać gotowego wydania.

- **`Docker is required`:** zainstaluj Dockera. Na Windowsie i macOS to Docker Desktop i musi być
  uruchomiony. Albo zamiast tego pobierz wydanie: `lithify fetch`.
- **Budowanie się nie udaje albo komputer w tym czasie zwalnia:** budowanie potrzebuje około 4 GB
  wolnej pamięci. Zamknij inne programy i spróbuj ponownie; gdy pamięci jest mało, Lithify sam
  wybiera lżejszy tryb budowania. W Docker Desktop daj Dockerowi co najmniej 4 GB
  (*Settings → Resources*).
- **Błędy sieci podczas budowania:** uruchom `lithify build` ponownie później. Ukończone kroki
  zostaną wykorzystane.
- **`another build is running`:** budowanie uruchomione ze strony głośnika albo z innego
  terminala jeszcze trwa. Poczekaj, aż się skończy.
- **`local patch … does not apply`** albo **`librespot commit … is not on dev`:** kod librespot
  się zmienił albo GitHub był nieosiągalny przy pierwszym budowaniu. Głośnik zostaje przy swojej
  wersji. Zobacz [architecture.md](../architecture.md#build-reproducibility) (po angielsku).
- Pełny log budowania jest w `~/.cache/lithify/build.log`, na Windowsie w
  `%LOCALAPPDATA%\lithify\cache\build.log`.

## Cofnięcie wszystkiego

```sh
lithify rollback          # poprzednia wersja Lithify
lithify uninstall         # z powrotem fabryczne oprogramowanie głośnika
```

Usuwanie Lithify także z komputera: [installation.md](installation.md#usuwanie).
