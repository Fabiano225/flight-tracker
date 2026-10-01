"""Regenerate tracker/airports.json: airports the flight source accepts, with German names.

Development only. Needs `pip install airportsdata` (MIT license) next to the pinned
requirements, plus Node.js for German country names (Intl.DisplayNames). The output
is committed so the tracker, workflows and website never download it at runtime.
"""
import json
from pathlib import Path
import subprocess

import airportsdata
from fli.models import Airport

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'tracker' / 'airports.json'

# German city names for common airports; the source data uses English or local names.
CITY = {
    'DUS': 'Düsseldorf', 'FRA': 'Frankfurt', 'MUC': 'München', 'CGN': 'Köln', 'NUE': 'Nürnberg',
    'FMO': 'Münster', 'SCN': 'Saarbrücken', 'MGL': 'Mönchengladbach', 'LBC': 'Lübeck', 'GUT': 'Gütersloh',
    'FKB': 'Karlsruhe/Baden-Baden', 'HHN': 'Frankfurt-Hahn', 'GWT': 'Sylt', 'VIE': 'Wien', 'KLU': 'Klagenfurt',
    'ZRH': 'Zürich', 'GVA': 'Genf', 'BSL': 'Basel', 'LHR': 'London', 'LGW': 'London', 'STN': 'London',
    'LTN': 'London', 'LCY': 'London', 'FCO': 'Rom', 'CIA': 'Rom', 'MXP': 'Mailand', 'LIN': 'Mailand',
    'BGY': 'Mailand-Bergamo', 'VCE': 'Venedig', 'NAP': 'Neapel', 'FLR': 'Florenz', 'LIS': 'Lissabon',
    'PMI': 'Palma de Mallorca', 'AGP': 'Málaga', 'TFS': 'Teneriffa', 'TFN': 'Teneriffa',
    'LPA': 'Gran Canaria', 'ACE': 'Lanzarote', 'FUE': 'Fuerteventura', 'BRU': 'Brüssel', 'CRL': 'Brüssel-Charleroi',
    'CPH': 'Kopenhagen', 'ATH': 'Athen', 'RHO': 'Rhodos', 'CFU': 'Korfu', 'KGS': 'Kos', 'IST': 'Istanbul',
    'SAW': 'Istanbul', 'CAI': 'Kairo', 'RAK': 'Marrakesch', 'CPT': 'Kapstadt', 'DEL': 'Neu-Delhi',
    'SGN': 'Ho-Chi-Minh-Stadt', 'SIN': 'Singapur', 'DPS': 'Bali', 'HKG': 'Hongkong', 'PEK': 'Peking',
    'PKX': 'Peking', 'NRT': 'Tokio', 'HND': 'Tokio', 'YYZ': 'Toronto', 'YVR': 'Vancouver', 'YUL': 'Montreal',
    'MEX': 'Mexiko-Stadt', 'CUN': 'Cancún', 'HAV': 'Havanna', 'GRU': 'São Paulo', 'EZE': 'Buenos Aires',
    'BOG': 'Bogotá', 'WAW': 'Warschau', 'KRK': 'Krakau', 'PRG': 'Prag', 'OTP': 'Bukarest', 'BEG': 'Belgrad',
    'KEF': 'Reykjavík', 'LUX': 'Luxemburg', 'NCE': 'Nizza', 'TLS': 'Toulouse', 'BOD': 'Bordeaux',
    'SXB': 'Straßburg', 'MLA': 'Malta', 'SVO': 'Moskau', 'DME': 'Moskau', 'VKO': 'Moskau',
    'LED': 'Sankt Petersburg', 'KBP': 'Kiew', 'USM': 'Ko Samui', 'SVQ': 'Sevilla', 'RUH': 'Riad',
    'KWI': 'Kuwait-Stadt', 'ULN': 'Ulaanbaatar', 'TAS': 'Taschkent', 'TBS': 'Tiflis', 'EVN': 'Jerewan',
    'SAO': 'São Paulo',
}
# Metropolitan codes the source accepts that have no single-airport entry.
EXTRA = {'SAO': ('BR', 'São Paulo (alle Flughäfen)')}


def city_for(code, entry):
    if code in CITY:
        return CITY[code]
    city = entry['city'].strip()
    if not city:
        city = entry['name'].replace(' International Airport', '').replace(' Airport', '').strip()
    city = city.split('/')[0].strip()
    return city.removesuffix(' Island').strip() or code


def german_countries(codes):
    script = ("const n=new Intl.DisplayNames(['de'],{type:'region'});"
              "const c=JSON.parse(require('fs').readFileSync(0,'utf8'));"
              "process.stdout.write(JSON.stringify(Object.fromEntries(c.map(x=>[x,n.of(x)]))));")
    result = subprocess.run(['node', '-e', script], input=json.dumps(sorted(codes)), capture_output=True,
                            text=True, check=True)
    return json.loads(result.stdout)


def main():
    source = airportsdata.load('IATA')
    airports = {}
    for code in sorted(a.name for a in Airport):
        if code in source:
            entry = source[code]
            airports[code] = [city_for(code, entry), entry['country'], entry['name']]
        elif code in EXTRA:
            airports[code] = [CITY[code], *EXTRA[code]]
        else:
            raise SystemExit(f'No location data for {code}')
    countries = german_countries({a[1] for a in airports.values()})
    data = {'source': 'airportsdata (MIT license) for airports supported by flights==0.9.0',
            'countries': countries, 'airports': airports}
    OUTPUT.write_text(json.dumps(data, ensure_ascii=False, separators=(',', ':')) + '\n', encoding='utf-8')
    print(f'Wrote {len(airports)} airports and {len(countries)} countries to {OUTPUT.relative_to(ROOT)}')


if __name__ == '__main__':
    main()
