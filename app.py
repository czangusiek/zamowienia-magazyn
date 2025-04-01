import os
import pandas as pd
from flask import Flask, render_template, request, flash, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta
from werkzeug.utils import secure_filename
import logging

# Konfiguracja aplikacji
app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'tajny-klucz-produkcyjny-zmien-to')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:////data/magazyn.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# Konfiguracja logowania
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Modele danych
class Towar(db.Model):
    """Model przechowujący stan magazynowy"""
    __tablename__ = 'towary'
    id = db.Column(db.Integer, primary_key=True)
    symbol = db.Column(db.String(50), unique=True, nullable=False)
    nazwa = db.Column(db.String(100))
    stan = db.Column(db.Integer, default=0)
    dostawca = db.Column(db.String(100))
    symbol_dostawcy = db.Column(db.String(50))

class Sprzedaz(db.Model):
    """Model przechowujący dane sprzedaży"""
    __tablename__ = 'sprzedaz'
    id = db.Column(db.Integer, primary_key=True)
    symbol = db.Column(db.String(50), nullable=False)
    ilosc = db.Column(db.Integer)
    data = db.Column(db.Date, default=datetime.utcnow)
    typ_okresu = db.Column(db.String(20))  # '30dni' lub 'miesiac'

# Inicjalizacja bazy
with app.app_context():
    db.create_all()

# Narzędzia pomocnicze
def konwertuj_na_liczbe(wartosc, domyslna=0):
    """Bezpieczna konwersja wartości na liczbę całkowitą"""
    try:
        return int(float(str(wartosc).replace(',', '.')))
    except (ValueError, TypeError):
        return domyslna

def waliduj_csv(plik, wymagane_kolumny):
    """Sprawdza poprawność struktury pliku CSV"""
    try:
        df = pd.read_csv(plik)
        brakujace = [kol for kol in wymagane_kolumny if kol not in df.columns]
        if brakujace:
            raise ValueError(f"Brak wymaganych kolumn: {', '.join(brakujace)}")
        return df
    except Exception as e:
        raise ValueError(f"Błąd pliku CSV: {str(e)}")

# Widoki aplikacji
@app.route('/', methods=['GET', 'POST'])
def glowna():
    if request.method == 'POST':
        plik = request.files.get('plik')
        if not plik or plik.filename == '':
            flash('Nie wybrano pliku')
            return redirect(url_for('glowna'))

        try:
            # Sprawdź typ pliku
            plik.stream.seek(0)
            if 'stan' in pd.read_csv(plik.stream).columns:
                plik.stream.seek(0)
                dane = waliduj_csv(plik.stream, ['symbol', 'stan'])
                aktualizuj_stan(dane)
                flash('Stan magazynowy zaktualizowany')
            else:
                plik.stream.seek(0)
                dane = waliduj_csv(plik.stream, ['symbol', 'ilosc'])
                typ_okresu = '30dni' if request.form.get('typ_okresu') == '30dni' else 'miesiac'
                dodaj_sprzedaz(dane, typ_okresu)
                flash('Dane sprzedaży zaktualizowane')
        except Exception as e:
            logger.error(f"Błąd przetwarzania pliku: {str(e)}")
            flash(f'Błąd: {str(e)}')

        return redirect(url_for('glowna'))
    
    return render_template('laduj.html')

@app.route('/oblicz')
def oblicz():
    try:
        wyniki = []
        for towar in Towar.query.all():
            # Pobierz dane sprzedaży
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
            
            # Oblicz zapotrzebowanie
            aktualny_stan = konwertuj_na_liczbe(towar.stan)
            wyniki.append({
                'symbol': towar.symbol,
                'nazwa': towar.nazwa or '',
                'stan': aktualny_stan,
                'dostawca': towar.dostawca or 'BRAK DOSTAWCY',
                'zamowienie_30d': max(0, round(float(sprzedaz_30d) * 1.2 - aktualny_stan)),
                'zamowienie_3m': max(0, round((float(sprzedaz_3m)/3 * 1.2 - aktualny_stan)),
                'zamowienie_12m': max(0, round((float(sprzedaz_12m)/12 * 1.2 - aktualny_stan))
           })   # Dodany brakujący nawias
        
        # Sortuj wyniki
        posortowane = sorted(wyniki, key=lambda x: (x['dostawca'] == 'BRAK DOSTAWCY', x['symbol']))
        return render_template('wyniki.html', wyniki=posortowane)
    
    except Exception as e:
        logger.error(f"Błąd obliczeń: {str(e)}")
        flash('Wystąpił błąd podczas obliczeń')
        return redirect(url_for('glowna'))

# Funkcje pomocnicze
def aktualizuj_stan(df):
    """Aktualizuje stan magazynowy w bazie danych"""
    bledy = []
    for _, wiersz in df.iterrows():
        try:
            symbol = str(wiersz['symbol']).strip()
            stan = konwertuj_na_liczbe(wiersz.get('stan', 0))
            
            towar = Towar.query.filter_by(symbol=symbol).first()
            if towar:
                towar.stan = stan
                towar.dostawca = str(wiersz.get('dostawca', towar.dostawca or '')).strip()
            else:
                db.session.add(Towar(
                    symbol=symbol,
                    nazwa=str(wiersz.get('nazwa', '')).strip(),
                    stan=stan,
                    dostawca=str(wiersz.get('dostawca', '')).strip(),
                    symbol_dostawcy=str(wiersz.get('symbol_dostawcy', '')).strip()
                ))
        except Exception as e:
            bledy.append(f"Wiersz {_+1}: {str(e)}")
            continue
    
    db.session.commit()
    if bledy:
        flash("Niektóre dane nie zostały załadowane. Problem z wierszami: " + ", ".join(bledy))

def dodaj_sprzedaz(df, typ_okresu):
    """Dodaje rekordy sprzedaży do bazy"""
    bledy = []
    for _, wiersz in df.iterrows():
        try:
            symbol = str(wiersz['symbol']).strip()
            ilosc = konwertuj_na_liczbe(wiersz.get('ilosc', 0))
            
            db.session.add(Sprzedaz(
                symbol=symbol,
                ilosc=ilosc,
                typ_okresu=typ_okresu
            ))
        except Exception as e:
            bledy.append(f"Wiersz {_+1}: {str(e)}")
            continue
    
    db.session.commit()
    if bledy:
        flash("Niektóre rekordy sprzedaży nie zostały zapisane. Problem z wierszami: " + ", ".join(bledy))

# Uruchomienie aplikacji
if __name__ == '__main__':
    os.makedirs('/data', exist_ok=True)
    app.run(host='0.0.0.0', port=5000)