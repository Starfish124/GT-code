from flask import Flask, request, jsonify
import sqlite3

app = Flask(__name__)

def get_db_connection():
    conn = sqlite3.connect('library.db')
    conn.row_factory = sqlite3.Row
    return conn

@app.teardown_appcontext
def close_connection(exception):
    db = get_db_connection()
    try:
        db.close()
    except Exception as e:
        pass

@app.route('/', methods=['GET'])
def index():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS books (
            id INTEGER PRIMARY KEY,
            title TEXT NOT NULL,
            author TEXT NOT NULL
        )
    ''')
    conn.commit()
    return 'Table created'

@app.route('/search', methods=['GET'])
def search_books_by_author():
    conn = get_db_connection()
    cursor = conn.cursor()
    query = request.args.get('author')
    if not query:
        return jsonify({'error': 'Author parameter is missing'}), 400
    cursor.execute('''
        SELECT * FROM books WHERE author LIKE ?
    ''', ('%' + query + '%',))
    results = cursor.fetchall()
    conn.close()
    return jsonify(results)

if __name__ == '__main__':
    app.run(debug=True)
