import pandas as pd
from flask import Flask, render_template, request, redirect, url_for, flash
from io import StringIO
from datetime import datetime, timedelta
import os

app = Flask(__name__)
app.secret_key = 'supersecretkey'

# Przechowywanie danych w pamięci (w rzeczywistej aplikacji użyj bazy danych)
current_stock = None
monthly_sales = {}
last_30_days_sales = None

@app.route('/', methods=['GET'])
def index():
    return render_template('upload.html')

@app.route('/upload_stock', methods=['POST'])
def upload_stock():
    global current_stock
    if 'file' not in request.files:
        flash('Nie wybrano pliku')
        return redirect(url_for('index'))
    
    file = request.files['file']
    if file.filename == '':
        flash('Nie wybrano pliku')
        return redirect(url_for('index'))
    
    if file and file.filename.endswith('.csv'):
        try:
            df = pd.read_csv(file.stream)
            required_columns = ['Rodzaj', 'Symbol', 'Nazwa', 'Stan', 'Podstawowy dostawca', 'Symbol u dostawcy']
            if not all(col in df.columns for col in required_columns):
                flash('Nieprawidłowy format pliku - brak wymaganych kolumn')
                return redirect(url_for('index'))
            
            current_stock = df
            flash('Stan magazynowy został załadowany pomyślnie')
        except Exception as e:
            flash(f'Błąd podczas przetwarzania pliku: {str(e)}')
    
    return redirect(url_for('index'))

@app.route('/upload_sales', methods=['POST'])
def upload_sales():
    global monthly_sales, last_30_days_sales
    
    if 'file' not in request.files:
        flash('Nie wybrano pliku')
        return redirect(url_for('index'))
    
    file = request.files['file']
    if file.filename == '':
        flash('Nie wybrano pliku')
        return redirect(url_for('index'))
    
    if file and file.filename.endswith('.csv'):
        try:
            df = pd.read_csv(file.stream)
            required_columns = ['Rodzaj', 'Nazwa', 'Symbol', 'Grupa', 'Ilość', 'J.M.', 'Brutto', 'Netto', 'Koszt', 'Zysk', 'Marża']
            if not all(col in df.columns for col in required_columns):
                flash('Nieprawidłowy format pliku - brak wymaganych kolumn')
                return redirect(url_for('index'))
            
            # Sprawdź czy to plik z ostatnimi 30 dniami czy pełny miesiąc
            if request.form.get('sales_type') == '30days':
                last_30_days_sales = df
                flash('Dane sprzedaży z ostatnich 30 dni zostały załadowane')
            else:
                month = request.form.get('month')
                if not month:
                    flash('Proszę wybrać miesiąc')
                    return redirect(url_for('index'))
                
                monthly_sales[month] = df
                flash(f'Dane sprzedaży za miesiąc {month} zostały załadowane')
        except Exception as e:
            flash(f'Błąd podczas przetwarzania pliku: {str(e)}')
    
    return redirect(url_for('index'))

@app.route('/calculate', methods=['GET'])
def calculate():
    if current_stock is None:
        flash('Najpierw załaduj stan magazynowy')
        return redirect(url_for('index'))
    
    if not monthly_sales and last_30_days_sales is None:
        flash('Brak danych sprzedaży do obliczeń')
        return redirect(url_for('index'))
    
    try:
        # Przygotowanie danych
        stock = current_stock.copy()
        
        # Obliczenia dla różnych okresów
        results = {}
        
        # 1. Ostatnie 30 dni
        if last_30_days_sales is not None:
            sales_30 = last_30_days_sales.groupby('Symbol')['Ilość'].sum().reset_index()
            merged_30 = pd.merge(stock, sales_30, on='Symbol', how='left')
            merged_30['Ilość'] = merged_30['Ilość'].fillna(0)
            merged_30['Zapotrzebowanie (30 dni)'] = (merged_30['Ilość'] * 1.2 - merged_30['Stan']).clip(lower=0)
            results['30_days'] = merged_30
        
        # 2. Ostatnie 3 miesiące
        if len(monthly_sales) >= 3:
            last_3_months = sorted(monthly_sales.keys())[-3:]
            sales_3m = pd.concat([monthly_sales[m] for m in last_3_months])
            sales_3m = sales_3m.groupby('Symbol')['Ilość'].sum().reset_index()
            sales_3m['Ilość'] = sales_3m['Ilość'] / 3  # Średnia miesięczna
            merged_3m = pd.merge(stock, sales_3m, on='Symbol', how='left')
            merged_3m['Ilość'] = merged_3m['Ilość'].fillna(0)
            merged_3m['Zapotrzebowanie (3 miesiące)'] = (merged_3m['Ilość'] * 1.2 - merged_3m['Stan']).clip(lower=0)
            results['3_months'] = merged_3m
        
        # 3. Ostatnie 12 miesięcy
        if len(monthly_sales) >= 12:
            sales_12m = pd.concat(list(monthly_sales.values()))
            sales_12m = sales_12m.groupby('Symbol')['Ilość'].sum().reset_index()
            sales_12m['Ilość'] = sales_12m['Ilość'] / 12  # Średnia miesięczna
            merged_12m = pd.merge(stock, sales_12m, on='Symbol', how='left')
            merged_12m['Ilość'] = merged_12m['Ilość'].fillna(0)
            merged_12m['Zapotrzebowanie (12 miesięcy)'] = (merged_12m['Ilość'] * 1.2 - merged_12m['Stan']).clip(lower=0)
            results['12_months'] = merged_12m
        
        # Przygotowanie wyników do wyświetlenia
        if not results:
            flash('Niewystarczające dane do obliczeń')
            return redirect(url_for('index'))
        
        # Łączenie wyników
        final_result = stock.copy()
        for key, df in results.items():
            cols = ['Symbol'] + [c for c in df.columns if c.startswith('Zapotrzebowanie')]
            final_result = pd.merge(final_result, df[cols], on='Symbol', how='left')
        
        # Sortowanie - najpierw z dostawcami, potem alfabetycznie
        final_result['sort_key'] = final_result['Podstawowy dostawca'].isna().astype(int)
        final_result = final_result.sort_values(['sort_key', 'Symbol']).drop(columns=['sort_key'])
        
        return render_template('results.html', 
                           tables=[final_result.to_html(classes='data', index=False)],
                           titles=final_result.columns.values)
    
    except Exception as e:
        flash(f'Błąd podczas obliczeń: {str(e)}')
        return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(debug=True)