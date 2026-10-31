# BC / ECU Logger (iDrive 8)

Aplicația `id8_bench_demo` înregistrează tot ce se vede când folosești meniurile butonului BC, ca să
poți reproduce comenzile în aplicația ta. **Doar citește**: către mașină pleacă numai cereri UDS de
citire (`01 <pid>`, `22 <did>`, `19 02 <mask>`). Orice alt serviciu (sesiune, scriere, rutine, ștergere
DTC) este refuzat în `src/ecu.js` înainte să ajungă la transport; testele verifică asta.

## Ce înregistrează

| Sursă | Ce | De unde |
|---|---|---|
| Semnale SOME/IP | controller/butoane, cluster, Check Control, turație, viteză, mod de conducere | `app/watch.json` |
| ECU motor (DME, `0x12`) | lampa MIL, turația, % etanol (OBD `52`), memoria de erori, orice DID găsit cu *sweep* | `src/ecu.js` |
| MARK-uri | etichete puse de tine de pe ecran ("ac la poziția 3") | butoanele de pe ecran |

Fișierele sunt `data/bclog-<data>-<ora>.jsonl` (rotire la 10 MB, maxim 40 MB în total). Fiecare linie are
`t` = milisecunde de la pornirea aplicației.

## Cum se recunoaște meniul BC

Meniul se anunță prin clipirea lămpii check engine, deci aplicația citește bitul MIL din ECU
(OBD `01 01`, octetul A, bit 7) și grupează clipirile:

| Clipiri | Meniu | Ce arată turometrul |
|---|---|---|
| 1 (după ținut BC ≥ 5 s) | 1 – hărți | poziția 1–4 = harta 1–4 |
| 2 (BC scurt) | 2 – clapete | 0 OEM, 1 deschis, 2 închis |
| 3 (BC scurt) | 3 – etanol | cantitatea de etanol |

Fiecare grup terminat devine în log `derived#bcmenu {"blinks":n}` plus un MARK automat
(`BC menu n (auto…)`); după 8 s fără clipiri apare `{"blinks":0}` (ai ieșit din meniu). Clipirile brute
rămân în log ca `derived#mil`, așa că analizorul le poate regrupa cu alte timpi.

## Ecranul

Lista nativă din aplicația ID8 Bench Test:

- **LOG / ECU / BC menu**: stare logare, stare citire ECU, meniul detectat acum și starea lămpii
- **ECU reading: start/stop**: pornește citirea ECU (nu pornește singură; vezi `autoEcu` mai jos)
- **Logging: pause/resume**, **Start a new log file**
- **ECU sweep**: citește o dată un interval de DID-uri (doar cu mașina oprită; oprește singur dacă
  viteza > 0). Fiecare apăsare pornește următorul interval: `F100-F1FF`, `F400-F4FF`, `D000-D0FF`,
  `4000-40FF`. DID-urile care răspund se adaugă la citirea continuă și se păstrează în
  `data/bclog-dids.json`.
- **MARK / SHOWS / NEEDLE**: etichete în log. `NEEDLE position n` = apeși când vezi acul la poziția n.

## Cum faci un log bun

1. Contact pus, mașina oprită, aplicația deschisă. Apasă **ECU reading: start**.
2. 30–60 s fără să atingi nimic (baza).
3. Opțional, o dată: **ECU sweep** pe fiecare interval, ca să afle ce DID-uri are DME-ul tău.
4. Țin BC ≥ 5 s → meniu 1. Pentru fiecare poziție a acului: apeși **NEEDLE position n** când ai
   ajuns acolo, apoi treci la următoarea. Repetă de 3–5 ori.
5. BC scurt → meniul 2, aceeași procedură (poziții 0, 1, 2). BC scurt → meniul 3 (etanol); notează
   procentul de etanol pe care îl citești.
6. Ținând BC nu poți apăsa pe ecran: apasă MARK-ul imediat după, analizorul folosește `--pre`/`--post`.

## Analiza

```sh
scp 'root@<unit>:/var/opt/public/oap/oap_<uid>/data/bclog-*.jsonl' .     # uid: sh deploy.sh status
python3 tools/analyze_bclog.py bclog-*.jsonl                    # ce valori ECU diferă între meniuri/poziții
python3 tools/analyze_bclog.py bclog-*.jsonl --timeline          # marcaje, meniuri și schimbări ECU în ordine
python3 tools/analyze_bclog.py bclog-*.jsonl --find 37           # unde apare etanolul (37 %)
python3 tools/analyze_bclog.py bclog-*.jsonl --blink-gap 1.4 --settle 2.2
```

Raportul are: **ACȚIUNI** (ce s-a schimbat după fiecare MARK de acțiune), **STĂRI** (valorile care
diferă între pozițiile hărților / clapetelor / meniuri) și **BC MENU** (meniurile deduse din lampă).

## Setări opționale: `data/bclog-settings.json`

```json
{ "autoStart": true, "autoEcu": false, "minMs": 20, "cap": 1000, "fileMB": 10, "capMB": 40,
  "ecu": { "addr": "12", "gapMs": 30, "blinkGapMs": 1000, "settleMs": 1500, "exitMs": 8000,
           "fastObd": ["01", "0C", "52"], "dids": ["F40C"] } }
```

`ecu.addr` este adresa de diagnostic (DME = `12`, al doilea DME `13`). Cheile din `ecu` sunt cele din
`DEFAULTS` din `src/ecu.js`.

## Ce nu știm încă (primul test pe mașină)

- **Nu a rulat pe o mașină reală.** Testat doar cu un ECU simulat, un SDK simulat și build-ul.
- **Lampa MIL din OBD poate să nu clipească.** Dacă tune-ul face lampa să clipească direct către
  cluster (nu prin DME), `derived#mil` rămâne 0. Atunci meniul se vede doar în `CheckControlMaster` /
  `KombiInterface_2` (sunt în `watch.json`) sau îl marchezi cu butoanele SHOWS.
- **Acul din meniu poate fi comandat pe alt drum** decât PID `0C`. De aceea există sweep-ul: caută
  DID-uri care se schimbă odată cu poziția (raportul STĂRI le arată).
- **Rata de citire.** O clipire trebuie prinsă între două citiri ale PID `01`; ciclul (afișat pe ecran,
  `cycle … ms`) trebuie să fie mult sub durata unei clipiri. Dacă lipsesc clipiri, scade `gapMs` sau
  lasă doar `01` în `fastObd`.
- **Interfețele SOME/IP ale iDrive 8** pot avea alt nume decât pe iDrive 7. La instalare, ce lipsește din
  catalogul unității este scos din manifest (și scris în `missing-interfaces.json`), nu oprește
  instalarea.
- Ecranul folosește doar `label` și `button`, ca ecranul verificat pe bancul de test, dar cu ~22
  componente în loc de 2; lista se derulează cu controllerul.
