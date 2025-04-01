import os
import pandas as pd
from flask import Flask, render_template, request, flash, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta
from pathlib import Path
import logging

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'tajny-klucz-produkcyjny-zmien-to')

BASE_DIR = Path(__file__).parent
DB_DIR = BASE_DIR / "instance"
DB_DIR.mkdir(exist_ok=True)
DATABASE_PATH = DB_DIR / "magazyn.db"

app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{DATABASE_PATH}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class Towar(db.Model):
    __tablename__ = 'towary'
    id = db.Column(db.Integer, primary_key=True)
    rodzaj = db.Column(db.String(50))
    symbol = db.Column(db.String(50), unique=True, nullable=False)
    nazwa = db.Column(db.String(100))
    stan = db.Column(db.Integer, default=0)
    dostawca = db.Column(db.String(100))
    symbol_dostawcy = db.Column(db.String(50))

class Sprzedaz(db.Model):
    __tablename__ = 'sprzedaz'
    id = db.Column(db.Integer, primary_key=True)
    rodzaj = db.Column(db.String(50))
    symbol = db.Column(db.String(50), nullable=False)
    nazwa = db.Column(db.String(100))
    grupa = db.Column(db.String(50))
    ilosc = db.Column(db.Integer)
    jm = db.Column(db.String(20))
    data = db.Column(db.Date, default=datetime.utcnow)
    typ_okresu = db.Column(db.String(20))

def init_db():
    try:
        with app.app_context():
            db.create_all()
        logger.info("Baza danych zainicjalizowana pomyślnie")
    except Exception as e:
        logger.error(f"Błąd inicjalizacji bazy: {str(e)}")
        raise

def konwertuj_na_liczbe(wartosc, domyslna=0):
    try:
        return int(float(str(wartosc).replace(',', '.')))
    except (ValueError, TypeError):
        return domyslna

def waliduj_csv(plik, typ_pliku):
    try:
        df = pd.read_csv(plik)
        
        if typ_pliku == 'stan':
            required = ['Symbol', 'Stan']
            df = df.rename(columns={
                'Rodzaj': 'rodzaj',
                'Nazwa': 'nazwa',
                'Podstawowy dostawca': 'dostawca',
                'Symbol u dostawcy': 'symbol_dostawcy'
            })
        else:  # sprzedaż
            required = ['Symbol', 'Ilość']
            df = df.rename(columns={
                'Rodzaj': 'rodzaj',
                'Nazwa': 'nazwa',
                'Grupa': 'grupa',
                'Ilość': 'ilosc',
                'J.M.': 'jm'
            })
        
        brakujace = [kol for kol in required if kol not in df.columns]
        if brakujace:
            raise ValueError(f"Brak wymaganych kolumn: {', '.join(brakujace)}")
            
        return df
    except Exception as e:
        raise ValueError(f"Błąd pliku CSV: {str(e)}")

def aktualizuj_stan(df):
    bledy = []
    for _, wiersz in df.iterrows():
        try:
            symbol = str(wiersz['Symbol']).strip()
            towar = Towar.query.filter_by(symbol=symbol).first()
            
            dane = {
                'rodzaj': str(wiersz.get('rodzaj', '')).strip(),
                'nazwa': str(wiersz.get('nazwa', '')).strip(),
                'stan': konwertuj_na_liczbe(wiersz['Stan']),
                'dostawca': str(wiersz.get('dostawca', '')).strip(),
                'symbol_dostawcy': str(wiersz.get('symbol_dostawcy', '')).strip()
            }
            
            if towar:
                for key, value in dane.items():
                    setattr(towar, key, value)
            else:
                dane['symbol'] = symbol
                db.session.add(Towar(**dane))
        except Exception as e:
            bledy.append(f"Wiersz {_+1}: {str(e)}")
    
    db.session.commit()
    if bledy:
        flash("Błędy w wierszach: " + ", ".join(bledy))

def dodaj_sprzedaz(df, typ_okresu):
    bledy = []
    for _, wiersz in df.iterrows():
        try:
            db.session.add(Sprzedaz(
                rodzaj=str(wiersz.get('rodzaj', '')).strip(),
                symbol=str(wiersz['Symbol']).strip(),
                nazwa=str(wiersz.get('nazwa', '')).strip(),
                grupa=str(wiersz.get('grupa', '')).strip(),
                ilosc=konwertuj_na_liczbe(wiersz['ilosc']),
                jm=str(wiersz.get('jm', '')).strip(),
                typ_okresu=typ_okresu
            ))
        except Exception as e:
            bledy.append(f"Wiersz {_+1}: {str(e)}")
    
    db.session.commit()
    if bledy:
        flash("Błędy w wierszach: " + ", ".join(bledy))

@app.route('/', methods=['GET', 'POST'])
def glowna():
    if request.method == 'POST':
        plik = request.files.get('plik')
        if not plik or plik.filename == '':
            flash('Nie wybrano pliku')
            return redirect(url_for('glowna'))

        try:
            plik.stream.seek(0)
            if 'Stan' in pd.read_csv(plik.stream).columns:
                plik.stream.seek(0)
                dane = waliduj_csv(plik.stream, 'stan')
                aktualizuj_stan(dane)
                flash('Stan magazynowy zaktualizowany')
            else:
                plik.stream.seek(0)
                dane = waliduj_csv(plik.stream, 'sprzedaz')
                typ_okresu = '30dni' if request.form.get('typ_okresu') == '30dni' else 'miesiac'
                dodaj_sprzedaz(dane, typ_okresu)
                flash('Dane sprzedaży zaktualizowane')
        except Exception as e:
            logger.error(f"Błąd przetwarzania pliku: {str(e)}")
            flash(f'Błąd: {str(e)}')

        return redirect(url_for('glowna'))
    
    return render_template('upload.html')

@app.route('/oblicz')
def oblicz():
    try:
        wyniki = []
        for towar in Towar.query.all():
            sprzedaz_30d = db.session.query(db.func.sum(Sprzedaz.ilosc)).filter(
                Sprzedaz.symbol == towar.symbol,
                Sprzedaz.typ_okresu == '30dni'
            ).scalar() or 0
            
            sprzedaz_3m = db.session.query(db.func.sum(Sprzedaz.ilosc)).filter(
                Sprzedaz.symbol == towar.symbol,
                Sprzedaz.typ_okresu == 'miesiac',
                Sprzedaz.data >= datetime.utcnow() - timedelta(days=90)
            ).scalar() or 0
            
            sprzedaz_12m = db.session.query(db.func.sum(Sprzedaz.ilosc)).filter(
                Sprzedaz.symbol == towar.symbol,
                Sprzedaz.typ_okresu == 'miesiac'
            ).scalar() or 0
            
            wyniki.append({
                'rodzaj': towar.rodzaj,
                'symbol': towar.symbol,
                'nazwa': towar.nazwa,
                'stan': towar.stan,
                'dostawca': towar.dostawca or 'BRAK DOSTAWCY',
                'zamowienie_30d': max(0, round(float(sprzedaz_30d) * 1.2 - towar.stan)),
                'zamowienie_3m': max(0, round((float(sprzedaz_3m)/3 * 1.2 - towar.stan)),
                'zamowienie_12m': max(0, round((float(sprzedaz_12m)/12 * 1.2 - towar.stan))
            })
        
        posortowane = sorted(wyniki, key=lambda x: (x['dostawca'] == 'BRAK DOSTAWCY', x['symbol']))
        return render_template('results.html', wyniki=posortowane)
    
    except Exception as e:
        logger.error(f"Błąd obliczeń: {str(e)}")
        flash('Wystąpił błąd podczas obliczeń')
        return redirect(url_for('glowna'))

init_db()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)