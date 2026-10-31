# BC Logger: înregistrează ce vede iDrive-ul când folosești butonul BC / rotița de pe volan

Aplicația **doar ascultă**. Nu trimite nimic către mașină (fără UDS, fără apeluri de metode, fără scrieri).
Scrie fiecare schimbare de semnal, cu oră, într-un fișier `bclog-*.jsonl`. Un script de analiză îți
spune apoi ce semnale s-au schimbat la fiecare acțiune.

## Ce conține arhiva

```
apps/bclog/app.json            lista de semnale + ecranul + setările
apps/bclog/main.js             aplicația
apps/bclog/logger.js           scrierea în fișier (limită de rată, rotire, curățare)
kit/tools/gen_bclog_signals.py alege semnalele din catalogul unității tale
kit/tools/analyze_bclog.py     analiza logurilor
```

Copiază `apps/` și `kit/` peste repo-ul `idrive7-apps` (nu suprascrie nimic existent).

## 1. Alege semnalele (recomandat)

`app.json` vine cu semnale care se știe că există (FrontCCU, Kombi, CDS, FES, MDrive...). Ca să căutăm
semnalele de BC / volan / cluster, generează lista din catalogul unității tale:

```sh
mkdir -p reference
scp root@<unit>:/etc/oap/someip/someip-interfaces.json reference/
python3 kit/tools/gen_bclog_signals.py --list          # ce ar asculta, fără să schimbe nimic
python3 kit/tools/gen_bclog_signals.py --write         # scrie signals + config.watch în app.json
python3 kit/tools/check_signals.py                     # verifică flagurile față de catalog
```

Trimite-mi rezultatul comenzii `--list` (sau chiar catalogul): aleg eu interfețele potrivite.

## 2. Construiește și instalează

```sh
python3 kit/tools/build.py bclog
deploy/make-car-kit.sh bclog && HU=root@<unit> KEY=~/.ssh/id_mgu deploy/push-to-car.sh install
```

Mașina oprită din mers, contact pus, de preferat cu încărcător (platforma se repornește o dată).
Tile-ul **BC Logger** apare sub Apps.

## 3. Cum faci logul

Ține aplicația deschisă pe ecran cât timp înregistrezi. Ecranul arată: `LOGGING`, câte linii,
câte semnale, și ultimul semnal „rar" care s-a schimbat (zgomotul gen turație e ignorat).

1. **Bază, 30–60 s:** nu atingi nimic.
2. Pentru fiecare acțiune: apeși întâi **MARK** corespunzător pe ecran, apoi faci acțiunea pe mașină.
   - `MARK BC button pressed`, apoi apeși BC
   - `MARK wheel up` / `MARK wheel down`, apoi rotești rotița
   - Repetă fiecare de **5 ori**, cu pauză de 5+ secunde între ele.
3. Pentru ce arată clusterul: parcurge meniul și apasă `SHOWS multimap 1…4`, `SHOWS valve 0/1/2`,
   `SHOWS ethanol screen` când vezi respectiva poziție pe turometru / în meniu.
4. Notează procentul de etanol afișat (ex. 37). Îl căutăm după aceea în log.

## 4. Ia logurile și analizează-le

Folderul aplicației îl vezi cu `sh deploy-car.sh status` (de forma `oap_122xx`).

```sh
scp 'root@<unit>:/var/opt/public/oap/oap_122*/data/bclog-*.jsonl' .
python3 kit/tools/analyze_bclog.py bclog-*.jsonl
python3 kit/tools/analyze_bclog.py bclog-*.jsonl --find 37      # unde apare etanolul
```

Raportul are două părți: **ACȚIUNI** (semnalele care s-au schimbat după fiecare MARK, dar nu în repaus)
și **STĂRI** (semnalele care au valoare diferită pentru fiecare poziție multimap / valve). Dacă ai apăsat
MARK *după* acțiune, folosește `--pre 3 --post 1`.

Trimite-mi fișierele `bclog-*.jsonl` (sau raportul) și trec la pasul următor.

## Limite, pe scurt

- **N-a rulat încă pe o mașină reală.** Am testat logica (scriere, rotire, analiza pe date simulate)
  și build-ul, dar cu un SDK fals. Primul test real e al tău.
- **Poate să nu vadă nimic.** Dacă apăsarea BC circulă doar între maneta de pe volan, cluster și DME,
  iDrive-ul n-o primește deloc și logul nu va conține nimic relevant. Atunci știm că trebuie altă cale.
- Aplicația vede doar ce cere în `app.json`; interfețele necunoscute se adaugă cu generatorul.
- Dacă platforma oprește aplicația când pleci de pe ecran, logarea se oprește odată cu ea. Ține-o deschisă.
- Setări utile: *Min. gap per signal* (câte ms între două linii ale aceluiași semnal), *Max. lines per second*
  (peste limită liniile se pierd și apar ca `drop` în log), *New file after* (MB). Fișierele vechi se șterg
  singure peste 40 MB în total.
