# Toolkit iDrive 8 + logger BC/ECU (id8-logger)

1. Dezarhiveaza TOT folderul (toolkit-ul si folderul `id8-logger` trebuie sa ramana unul langa altul;
   `bclog8` si `deploy-kit` pot ramane, dar nu mai sunt necesare pentru logger - vezi mai jos).
2. Instaleaza Python 3, Node.js LTS si ai clientul OpenSSH. Porneste `iDrive7-Toolkit 0.2 start.bat` ca Administrator.
3. Prima pagina, „iDrive 8 – Ghid", se urmeaza de sus in jos. Laptopul ruleaza comenzile; tu faci ce scrie la fiecare pas.
   Pasii 6 si 7 sunt opționali (vechiul pachet bclog8 prin mgu-store); loggerul de la pasul 8 nu mai trece prin ele.
4. Dupa pasul 5 (colectare date) trimite-mi `id8/probe/id8-probe-*.txt` si, dupa pasul 9, `id8/loguri/`.

## De ce s-a schimbat instalarea (pasul 8)

Varianta veche (bclog8, prin mgu-store) lega codul aplicatiei de module SDK cautate pe unitate dupa nume exact
(„donor”). Pe unitatea ta nu s-a gasit niciun donor cu acele nume si instalarea a esuat la verificare (nu s-a
scris nimic). Noua varianta (`id8-logger`, codul din `idrive8-app-test-main`) extrage tot SDK-ul dintr-o
aplicatie care ruleaza deja pe unitate (doar se citeste tabela ei de module, nu se executa nimic din ea), deci
nu mai depinde de module cu nume exacte. Se instaleaza cu propriul script (`mgu.sh`), fara mgu-store si fara
mgu-deploy.

Pasii 8a-8d, in ordine:
- **8a Extrage SDK**: aduce un `bundle.js` de la o aplicatie de pe unitate si scoate din el `oap-sdk` complet,
  local, in `id8/sdk/`.
- **8b Construiește pachetul**: `python tools/build.py --sdk id8/sdk` → `id8-logger/dist/id8-bench-test.tar.gz`.
- **8c Verifică (check)**: copiaza pachetul pe unitate si ruleaza `mgu.sh check` - doar citeste, nu schimba
  nimic. Verifica identitatea fisierelor de sistem (AppManagement, configuratia MainApp) fata de ce era
  cunoscut cand a fost scris scriptul; daca firmware-ul tau nu se potriveste, refuza aici, fara sa scrie nimic.
- **8d Instalează**: ruleaza `mgu.sh install` - face backup (uid-uri, inregistrarea aplicatiilor, copia
  originala a fisierului MainApp) inainte de orice schimbare, apoi scrie pachetul si, daca e nevoie, leaga
  (bind-mount) un hook in MainApp. „Dezinstalează” pune totul inapoi din acel backup.

## Ce face loggerul (id8-logger, aplicatia „BC / ECU Logger”)

- asculta semnalele SOME/IP (volan, ecran instrumente, Check Control, turatie, viteza, drive mode) si le scrie
  in `bclog-*.jsonl`;
- la butonul „ECU reading: start” CITESTE ECU-ul motor (DME 0x12) prin gateway-ul UDS al unitatii (modulul nativ
  `someip`, aceeasi cale dovedita in kit-ul idrive7): lampa check engine (= meniul BC: 1 clipire = meniul 1
  harti, 2 = valve, 3 = etanol; apare in log ca `derived#bcmenu`), turatie, etanol %, memoria de erori;
- „ECU sweep” cauta identificatori suplimentari (doar cu mașina oprita; se opreste daca viteza > 0 sau ECU nu
  raspunde) si ii salveaza in `bclog-dids.json`;
- trimite NUMAI servicii de citire (UDS 0x01, 0x22, 0x19 02) - codul refuza orice altceva. Nu scrie, nu codeaza.

Analiza (pasul 9) arata ACTIONS / STATES / BC MENU; `--timeline` si `--find` pentru cautari.

## Ce e verificat si ce nu

Verificat (pe calculator, nu pe masina): loggerul cu un SDK simulat si un ECU simulat (meniu BC 2 detectat din
clipiri), extragerea SDK-ului si construirea pachetului cu un bundle sintetic, sintaxa Node 8.17.

NEverificat pe o masina reala: `extract-sdk.js` pe un bundle real de pe unitatea ta; `mgu.sh check` - hash-urile
lui pentru AppManagement/MainApp sunt dintr-un firmware cunoscut cand a fost scris scriptul, s-ar putea sa nu se
potriveasca exact cu `23w50.3-1-4 mgu-high`; daca lampa check engine clipeste prin OBD (PID 01) cand BC face
meniul (altfel ramai pe marcajele manuale); daca butonul BC ajunge in MGU.

Daca `mgu.sh check` refuza cu un mesaj despre AppManagement sau configuratia MainApp, trimite-mi exact ce
afiseaza (hash-urile) - verific daca sunt doar o versiune mai noua a acelorasi fisiere stock si actualizez
scriptul.

Nu rula pe masina in mers. La pasul 9 trimite-mi si `bench.log` (logul propriu al aplicatiei).

## Flash/Enable (SSH) - neschimbat

Protecții înainte de Flash/Enable: rulează ca Administrator, OpenSSH și Python prezente, „Citește starea” făcut
cu același tip de unitate, VIN-ul scris = VIN-ul citit din mașină, confirmare prin ultimele 6 caractere ale
VIN-ului. Cancel în timpul flash-ului sau al instalării cere confirmare. `flash` poate șterge codarea unității -
fă backup la codare înainte (BimmerCode/ISTA/E-Sys) și restaurează-l dacă funcțiile se comportă ciudat după.
`enable`, `status`, `close`, `disable` nu reprogramează unitatea.

Ordinea: 1 laptop › 2 unealta de root › 3 status › 4 flash/enable › 5 probă › 6-7 opționale (bclog8/mgu-deploy) ›
8 aplicația (8a-8d) + repornire › 9 loguri.

## Dacă aplicația dispare după repornire
`mgu.sh install` fără `--persist` leagă hook-ul în MainApp doar până la următoarea repornire. Acum 8d instalează cu
`--persist`. Ddacă ai instalat înainte de această corecție: apasă **8e. Repară după repornire** (rulează
`mgu.sh persist`: reactivează hook-ul și îl face permanent), apoi repornește unitatea o dată.
