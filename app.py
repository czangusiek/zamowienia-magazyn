import os
import pandas as pd
from flask import Flask, render_template, request, flash, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'dev-secret-key')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:////data/database.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# Modele danych
class Stock(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    symbol = db.Column(db.String(50), unique=True, nullable=False)
    name = db.Column(db.String(100))
    current_stock = db.Column(db.Integer, default=0)
    supplier = db.Column(db.String(100))
    supplier_code = db.Column(db.String(50))

class Sales(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    symbol = db.Column(db.String(50), nullable=False)
    quantity = db.Column(db.Integer)
    record_date = db.Column(db.Date, default=datetime.utcnow)
    period_type = db.Column(db.String(20))  # '30days' lub 'full_month'

# Inicjalizacja bazy
with app.app_context():
    db.create_all()

def validate_numeric(value, default=0):
    """Konwertuje wartość na liczbę całkowitą z domyślną wartością"""
    try:
        return int(float(str(value).replace(',', '.')))
    except (ValueError, TypeError):
        return default

def validate_csv(file_stream, required_columns):
    """Walidacja struktury pliku CSV"""
    try:
        df = pd.read_csv(file_stream)
        missing = [col for col in required_columns if col not in df.columns]
        if missing:
            raise ValueError(f"Brak wymaganych kolumn: {', '.join(missing)}")
        return df
    except Exception as e:
        raise ValueError(f"Nieprawidłowy plik CSV: {str(e)}")

def update_stock(df):
    """Aktualizacja stanu magazynowego z walidacją danych"""
    errors = []
    for _, row in df.iterrows():
        try:
            symbol = str(row['Symbol']).strip()
            stock = validate_numeric(row.get('Stan', 0))
            
            item = Stock.query.filter_by(symbol=symbol).first()
            if item:
                item.current_stock = stock
                item.supplier = str(row.get('Podstawowy dostawca', item.supplier or '')).strip()
            else:
                db.session.add(Stock(
                    symbol=symbol,
                    name=str(row.get('Nazwa', '')).strip(),
                    current_stock=stock,
                    supplier=str(row.get('Podstawowy dostawca', '')).strip(),
                    supplier_code=str(row.get('Symbol u dostawcy', '')).strip()
                ))
        except Exception as e:
            errors.append(f"Wiersz {_+1}: {str(e)}")
            continue
    
    db.session.commit()
    if errors:
        flash("Część danych nie została załadowana. Szczegóły: " + " | ".join(errors))

def update_sales(df, period_type):
    """Aktualizacja danych sprzedaży z walidacją"""
    errors = []
    for _, row in df.iterrows():
        try:
            symbol = str(row['Symbol']).strip()
            quantity = validate_numeric(row.get('Ilość', 0))
            
            db.session.add(Sales(
                symbol=symbol,
                quantity=quantity,
                period_type=period_type
            ))
        except Exception as e:
            errors.append(f"Wiersz {_+1}: {str(e)}")
            continue
    
    db.session.commit()
    if errors:
        flash("Część sprzedaży nie została załadowana. Szczegóły: " + " | ".join(errors))

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        file = request.files.get('file')
        if not file or file.filename == '':
            flash('Nie wybrano pliku')
            return redirect(url_for('index'))
        
        try:
            file.stream.seek(0)
            if 'Stan' in pd.read_csv(file.stream).columns:
                file.stream.seek(0)
                df = validate_csv(file.stream, ['Symbol', 'Stan'])
                update_stock(df)
                flash('Stan magazynowy zaktualizowany')
            else:
                file.stream.seek(0)
                df = validate_csv(file.stream, ['Symbol', 'Ilość'])
                period_type = '30days' if request.form.get('sales_type') == '30days' else 'full_month'
                update_sales(df, period_type)
                flash('Dane sprzedaży zaktualizowane')
        except Exception as e:
            flash(f'Błąd: {str(e)}')
        
        return redirect(url_for('index'))
    
    return render_template('upload.html')

@app.route('/calculate')
def calculate():
    try:
        results = []
        for item in Stock.query.all():
            # Pobierz dane sprzedaży
            sales_30d = db.session.query(db.func.sum(Sales.quantity)).filter(
                Sales.symbol == item.symbol,
                Sales.period_type == '30days'
            ).scalar() or 0
            
            sales_3m = db.session.query(db.func.sum(Sales.quantity)).filter(
                Sales.symbol == item.symbol,
                Sales.period_type == 'full_month',
                Sales.record_date >= datetime.utcnow() - timedelta(days=90)
            ).scalar() or 0
            
            sales_12m = db.session.query(db.func.sum(Sales.quantity)).filter(
                Sales.symbol == item.symbol,
                Sales.period_type == 'full_month'
            ).scalar() or 0
            
            # Obliczenia z zabezpieczeniami
            current_stock = validate_numeric(item.current_stock)
            results.append({
                'symbol': item.symbol,
                'name': item.name or '',
                'current_stock': current_stock,
                'supplier': item.supplier or 'BRAK DOSTAWCY',
                'order_30d': max(0, round(float(sales_30d) * 1.2 - current_stock)),
                'order_3m': max(0, round((float(sales_3m)/3 * 1.2 - current_stock)),
                'order_12m': max(0, round((float(sales_12m)/12 * 1.2 - current_stock))
            })
        
        # Sortowanie wyników
        results_sorted = sorted(results, key=lambda x: (x['supplier'] == 'BRAK DOSTAWCY', x['symbol']))
        return render_template('results.html', results=results_sorted)
    
    except Exception as e:
        flash(f'Błąd obliczeń: {str(e)}')
        return redirect(url_for('index'))

if __name__ == '__main__':
    os.makedirs('/data', exist_ok=True)
    app.run(host='0.0.0.0', port=5000)