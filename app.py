import os
import pandas as pd
from flask import Flask, render_template, request, flash, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta
import logging
from pathlib import Path

# Konfiguracja aplikacji
app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'tajny-klucz-produkcyjny-zmien-to')

# Ścieżka do bazy danych - wersja kompatybilna z Render
db_path = os.path.join(os.getcwd(), 'magazyn.db')
app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_size': 10,
    'max_overflow': 20,
    'pool_timeout': 30,
    'pool_recycle': 3600
}

db = SQLAlchemy(app)

# Konfiguracja logowania
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Modele danych
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

# Funkcje pomocnicze
def init_db(force=False):
    """Inicjalizacja bazy danych z obsługą błędów"""
    try:
        with app.app_context():
            # Sprawdź czy trzeba tworzyć bazę
            if force or not os.path.exists(db_path):
                logger.info("Tworzenie nowej bazy danych...")
                # Upewnij się że katalog istnieje
                os.makedirs(os.path.dirname(db_path), exist_ok=True)
                db.create_all()
                logger.info(f"Baza utworzona w: {db_path}")
                logger.info(f"Tabele: {db.engine.table_names()}")
            else:
                logger.info(f"Baza już istnieje w: {db_path}")
    except Exception as e:
        logger.error(f"Błąd inicjalizacji bazy: {str(e)}")
        raise

def konwertuj_na_liczbe(wartosc, domyslna=0):
    """Bezpieczna konwersja na liczbę całkowitą"""
    try:
        if pd.isna(wartosc):
            return domyslna
        return int(float(str(wartosc).replace(',', '.')))
    except (ValueError, TypeError):
        return domyslna

def waliduj_csv(plik, typ_pliku):
    """Ulepszona walidacja plików CSV z obsługą polskich znaków"""
    try:
        # Wczytaj tylko pierwszy wiersz aby sprawdzić nagłówki
        df = pd.read_csv(plik, nrows=1, encoding='utf-8')
        plik.seek(0)  # Rewind pliku
        
        df.columns = df.columns.str.strip().str.lower()
        logger.info(f"Znalezione kolumny: {df.columns.tolist()}")

        # Słownik mapowania kolumn
        mapowanie = {
            'stan': ['stan', 'ilosc', 'quantity'],
            'sprzedaz': ['ilość', 'ilosc', 'quantity', 'sztuki']
        }

        if typ_pliku == 'stan':
            required = ['symbol', 'stan']
            df = df.rename(columns={
                'rodzaj': 'rodzaj',
                'nazwa': 'nazwa',
                'podstawowy dostawca': 'dostawca',
                'symbol u dostawcy': 'symbol_dostawcy'
            })
        else:
            required = ['symbol', 'ilosc']
            # Znajdź kolumnę z ilością (uwzględniając polskie znaki)
            ilosc_col = next((col for col in df.columns 
                           if any(m in col.lower().replace('ść', 'sc') 
                                 for m in mapowanie['sprzedaz']), None)
            if not ilosc_col:
                raise ValueError(f"Nie znaleziono kolumny z ilością. Dostępne kolumny: {df.columns.tolist()}")
            
            df = df.rename(columns={
                'rodzaj': 'rodzaj',
                'nazwa': 'nazwa',
                'grupa': 'grupa',
                ilosc_col: 'ilosc',
                'j.m.': 'jm'
            })

        brakujace = [kol for kol in required if kol not in df.columns]
        if brakujace:
            raise ValueError(f"Brak wymaganych kolumn: {', '.join(brakujace)}. Dostępne kolumny: {', '.join(df.columns)}")

        # Wczytaj cały plik z poprawnymi nagłówkami
        df = pd.read_csv(plik, encoding='utf-8')
        return df.rename(columns=str.strip).rename(columns=str.lower)

    except Exception as e:
        logger.error(f"Błąd walidacji CSV: {str(e)}")
        raise ValueError(f"Błąd pliku CSV: {str(e)}")

def aktualizuj_stan(df):
    """Optymalizowana aktualizacja stanu magazynowego"""
    bledy = []
    with app.app_context():
        for _, wiersz in df.iterrows():
            try:
                symbol = str(wiersz.get('symbol', '')).strip()
                if not symbol:
                    continue

                towar = Towar.query.filter_by(symbol=symbol).first()
                dane = {
                    'rodzaj': str(wiersz.get('rodzaj', '')).strip(),
                    'nazwa': str(wiersz.get('nazwa', '')).strip(),
                    'stan': konwertuj_na_liczbe(wiersz.get('stan', 0)),
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
                logger.error(f"Błąd w wierszu {_+1}: {e}")

        try:
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            bledy.append(f"Błąd bazy danych: {str(e)}")
    
    if bledy:
        flash("Niektóre dane nie zostały załadowane. Problem z wierszami: " + ", ".join(bledy[:5]))

def dodaj_sprzedaz(df, typ_okresu):
    """Optymalizowane dodawanie sprzedaży"""
    bledy = []
    with app.app_context():
        for _, wiersz in df.iterrows():
            try:
                symbol = str(wiersz.get('symbol', '')).strip()
                if not symbol:
                    continue

                db.session.add(Sprzedaz(
                    rodzaj=str(wiersz.get('rodzaj', '')).strip(),
                    symbol=symbol,
                    nazwa=str(wiersz.get('nazwa', '')).strip(),
                    grupa=str(wiersz.get('grupa', '')).strip(),
                    ilosc=konwertuj_na_liczbe(wiersz.get('ilosc', 0)),
                    jm=str(wiersz.get('jm', 'szt')).strip(),
                    typ_okresu=typ_okresu
                ))

            except Exception as e:
                bledy.append(f"Wiersz {_+1}: {str(e)}")
                logger.error(f"Błąd w wierszu {_+1}: {e}")

        try:
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            bledy.append(f"Błąd bazy danych: {str(e)}")
    
    if bledy:
        flash("Niektóre rekordy sprzedaży nie zostały zapisane. Problem z wierszami: " + ", ".join(bledy[:5]))

# Widoki aplikacji
@app.route('/initdb')
def init_db_route():
    """Ręczna inicjalizacja bazy danych"""
    try:
        init_db(force=True)
        return "Baza danych została pomyślnie zainicjalizowana", 200
    except Exception as e:
        return f"Błąd inicjalizacji bazy: {str(e)}", 500

@app.route('/', methods=['GET', 'POST'])
def glowna():
    logger.info(f"Ścieżka do bazy: {db_path}")
    logger.info(f"Tabele w bazie: {db.engine.table_names()}")

    if request.method == 'POST':
        plik = request.files.get('plik')
        if not plik or plik.filename == '':
            flash('Nie wybrano pliku')
            return redirect(url_for('glowna'))

        try:
            # Sprawdź typ pliku
            df_sample = pd.read_csv(plik.stream, nrows=1, encoding='utf-8')
            plik.stream.seek(0)
            
            if 'stan' in [col.lower() for col in df_sample.columns]:
                dane = waliduj_csv(plik.stream, 'stan')
                aktualizuj_stan(dane)
                flash('Stan magazynowy zaktualizowany')
            else:
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
    """Optymalizowane obliczenia z paginacją"""
    try:
        wyniki = []
        with app.app_context():
            towary = Towar.query.paginate(page=1, per_page=100, error_out=False)

            for towar in towary.items:
                try:
                    # Optymalizacja zapytań
                    filtry = [Sprzedaz.symbol == towar.symbol]
                    
                    sprzedaz_30d = (db.session.query(db.func.sum(Sprzedaz.ilosc))
                                  .filter(*filtry, Sprzedaz.typ_okresu == '30dni')
                                  .scalar() or 0)
                    
                    sprzedaz_3m = (db.session.query(db.func.sum(Sprzedaz.ilosc))
                                 .filter(*filtry, 
                                        Sprzedaz.typ_okresu == 'miesiac',
                                        Sprzedaz.data >= datetime.utcnow() - timedelta(days=90))
                                 .scalar() or 0)
                    
                    sprzedaz_12m = (db.session.query(db.func.sum(Sprzedaz.ilosc))
                                  .filter(*filtry, Sprzedaz.typ_okresu == 'miesiac')
                                  .scalar() or 0)

                    wyniki.append({
                        'rodzaj': towar.rodzaj,
                        'symbol': towar.symbol,
                        'nazwa': towar.nazwa,
                        'stan': towar.stan,
                        'dostawca': towar.dostawca or 'BRAK DOSTAWCY',
                        'zamowienie_30d': max(0, round(sprzedaz_30d * 1.2 - towar.stan)),
                        'zamowienie_3m': max(0, round((sprzedaz_3m / 3) * 1.2 - towar.stan)),
                        'zamowienie_12m': max(0, round((sprzedaz_12m / 12) * 1.2 - towar.stan))
                    })

                except Exception as e:
                    logger.error(f"Błąd obliczeń dla towaru {towar.symbol}: {str(e)}")
                    continue

        posortowane = sorted(wyniki, key=lambda x: (x['dostawca'] == 'BRAK DOSTAWCY', x['symbol']))
        return render_template('results.html', wyniki=posortowane)
    
    except Exception as e:
        logger.error(f"Błąd obliczeń: {str(e)}")
        flash('Wystąpił błąd podczas obliczeń')
        return redirect(url_for('glowna'))

# Inicjalizacja
if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=5000, debug=True)