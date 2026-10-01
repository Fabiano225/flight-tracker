"""Regenerate tracker/airports.json and tracker/airlines.json from the flight library.

Development only. Needs `pip install airportsdata` (MIT license) next to the pinned
requirements, plus Node.js for English country names (Intl.DisplayNames). The output
is committed so the tracker, workflows and website never download it at runtime.
"""
import json
from pathlib import Path
import subprocess

import airportsdata
from fli.models import Airline, Airport

ROOT = Path(__file__).resolve().parents[1]
AIRPORTS = ROOT / 'tracker' / 'airports.json'
AIRLINES = ROOT / 'tracker' / 'airlines.json'

# English city names where the source data uses local, outdated or suburb names.
CITY = {
    'DUS': 'Düsseldorf', 'FRA': 'Frankfurt', 'HHN': 'Frankfurt-Hahn', 'FMO': 'Münster', 'SCN': 'Saarbrücken',
    'MGL': 'Mönchengladbach', 'LBC': 'Lübeck', 'GUT': 'Gütersloh', 'FKB': 'Karlsruhe/Baden-Baden', 'GWT': 'Sylt',
    'KLU': 'Klagenfurt', 'BSL': 'Basel', 'IST': 'Istanbul', 'TFS': 'Tenerife', 'TFN': 'Tenerife', 'RHO': 'Rhodes',
    'CFU': 'Corfu', 'VCE': 'Venice', 'NAP': 'Naples', 'FLR': 'Florence', 'BEG': 'Belgrade', 'MLA': 'Malta',
    'USM': 'Ko Samui', 'DPS': 'Bali', 'YYZ': 'Toronto', 'YVR': 'Vancouver', 'YUL': 'Montreal',
    'EZE': 'Buenos Aires', 'PMI': 'Palma de Mallorca', 'GRU': 'São Paulo', 'SAO': 'São Paulo', 'BOG': 'Bogotá',
    'SVQ': 'Seville', 'ULN': 'Ulaanbaatar', 'BGY': 'Milan-Bergamo', 'CRL': 'Brussels-Charleroi',
}
# German names, used only to find airports in the settings form ("München" finds MUC).
ALIASES = {
    'MUC': 'München', 'CGN': 'Köln', 'NUE': 'Nürnberg', 'VIE': 'Wien', 'ZRH': 'Zürich', 'GVA': 'Genf',
    'BRU': 'Brüssel', 'CRL': 'Brüssel', 'CPH': 'Kopenhagen', 'FCO': 'Rom', 'CIA': 'Rom', 'MXP': 'Mailand',
    'LIN': 'Mailand', 'BGY': 'Mailand', 'VCE': 'Venedig', 'NAP': 'Neapel', 'FLR': 'Florenz', 'LIS': 'Lissabon',
    'ATH': 'Athen', 'RHO': 'Rhodos', 'CFU': 'Korfu', 'TFS': 'Teneriffa', 'TFN': 'Teneriffa', 'PRG': 'Prag',
    'WAW': 'Warschau', 'KRK': 'Krakau', 'OTP': 'Bukarest', 'BEG': 'Belgrad', 'NCE': 'Nizza', 'SXB': 'Straßburg',
    'LUX': 'Luxemburg', 'SVO': 'Moskau', 'DME': 'Moskau', 'VKO': 'Moskau', 'LED': 'Sankt Petersburg',
    'KBP': 'Kiew', 'CAI': 'Kairo', 'RAK': 'Marrakesch', 'CPT': 'Kapstadt', 'DEL': 'Neu-Delhi',
    'SGN': 'Ho-Chi-Minh-Stadt', 'SIN': 'Singapur', 'HKG': 'Hongkong', 'PEK': 'Peking', 'PKX': 'Peking',
    'NRT': 'Tokio', 'HND': 'Tokio', 'MEX': 'Mexiko-Stadt', 'HAV': 'Havanna', 'RUH': 'Riad', 'TBS': 'Tiflis',
    'EVN': 'Jerewan', 'TAS': 'Taschkent', 'KWI': 'Kuwait-Stadt', 'MLA': 'Malta', 'SVQ': 'Sevilla',
}
# Large and popular airports, roughly by passenger traffic. Breaks ties in the
# airport search so that "bang" lists Bangkok before Bangalore.
MAJOR = '''
ATL DXB DFW HND LHR DEN IST ORD LAX DEL CDG JFK CAN AMS PVG MAD ICN BOM SIN FRA PEK BKK LAS MCO CLT PKX SZX
JED MIA SEA CTU BCN SFO EWR PHX KMG DOH FCO IAH YYZ MUC BOS MEX CGK LGW KUL SGN MSP NRT SHA HKG MNL DMK RUH
BLR GRU CKG HGH XIY SYD MAN DTW AYT ORY PHL FLL JNB MEL LGA BWI SLC TPE DUB ZRH SAW CPH PMI OSL ARN LIS VIE
BRU MXP ATH HEL WAW PRG DUS HAM BER STR CGN NCE LYS MRS TLS AGP ALC VLC TFS LPA BUD OTP KRK BGY NAP VCE OPO
FAO HER RHO CFU SKG LCA TLV CAI HRG SSH RAK AUH MCT BAH KWI AMM CMB MLE KTM DAC ISB KHI LHE HAN DAD KTI SAI
CNX HKT KBV USM DPS CEB KIX NGO FUK CTS GMP PUS TAO XMN NKG WUH CSX SYX MFM KHH PER BNE AKL ADL CHC
YVR YUL YYC SJU CUN HAV PUJ BOG LIM SCL EZE GIG BSB NBO ADD LOS CMN TUN DJE ACC DKR MRU SEZ ZNZ JRO CPT DUR
HAJ NUE LEJ DRS BRE DTM FMO PAD NRN HHN FKB FDH FMM SCN GVA BSL SZG INN GRZ LNZ KLU EDI GLA BHX BRS STN LTN
LCY NCL LPL BFS CRL EIN RTM LUX BIO SVQ IBZ ACE FUE MAH FNC PDL SPU DBV ZAG LJU BEG SOF VAR BOJ TIA SJJ SKP
PRN RIX TLL VNO KEF BGO TRD GOT BLL AAR SVO DME VKO LED KBP TBS EVN GYD ALA NQZ TAS SVX OVB
'''.split()
# Metropolitan codes the source accepts that have no single-airport entry.
EXTRA = {'SAO': ('BR', 'São Paulo (all airports)')}
AIRLINE_NAMES = {'LH': 'Lufthansa'}


def city_for(code, entry):
    if code in CITY:
        return CITY[code]
    city = entry['city'].strip()
    if not city:
        city = entry['name'].replace(' International Airport', '').replace(' Airport', '').strip()
    city = city.split('/')[0].strip()
    return city.removesuffix(' Island').strip() or code


def country_names(codes):
    script = ("const n=new Intl.DisplayNames(['en'],{type:'region'});"
              "const c=JSON.parse(require('fs').readFileSync(0,'utf8'));"
              "process.stdout.write(JSON.stringify(Object.fromEntries(c.map(x=>[x,n.of(x)]))));")
    result = subprocess.run(['node', '-e', script], input=json.dumps(sorted(codes)), capture_output=True,
                            text=True, check=True)
    return json.loads(result.stdout)


def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, separators=(',', ':')) + '\n', encoding='utf-8')


def main():
    source = airportsdata.load('IATA')
    airports = {}
    # Iterating the enum skips alias codes: the library resolves those to another
    # airport's code, so a search for them could never return matching flights.
    for code in sorted(a.name for a in Airport):
        if code in source:
            entry = source[code]
            airports[code] = [city_for(code, entry), entry['country'], entry['name']]
        elif code in EXTRA:
            airports[code] = [CITY[code], *EXTRA[code]]
        else:
            raise SystemExit(f'No location data for {code}')
    missing = [code for code in MAJOR if code not in airports]
    if missing or len(set(MAJOR)) != len(MAJOR):
        raise SystemExit(f'Check the major airport list: {missing or "duplicates"}')
    write(AIRPORTS, {'source': 'airportsdata (MIT license) for airports supported by flights==0.9.0',
                     'countries': country_names({a[1] for a in airports.values()}), 'airports': airports,
                     'aliases': {code: ALIASES[code] for code in sorted(ALIASES) if code in airports},
                     'major': MAJOR})
    # Codes starting with a digit are stored as `_4U` in the library's enum.
    airlines = {a.name.lstrip('_'): AIRLINE_NAMES.get(a.name.lstrip('_'), a.value) for a in Airline}
    write(AIRLINES, {'source': 'flights==0.9.0 airline list', 'airlines': dict(sorted(airlines.items()))})
    print(f'Wrote {len(airports)} airports and {len(airlines)} airlines')


if __name__ == '__main__':
    main()
