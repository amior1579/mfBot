# db.py
import os
import psycopg2
from psycopg2.extras import RealDictCursor

class DatabaseManager:
    def __init__(self, db_url=None):
        self.db_url = db_url or os.environ.get("DATABASE_URL")
        if not self.db_url:
            raise ValueError("DATABASE_URL environment variable not set")
        self.conn = None
        self.init_db()

    def get_connection(self):
        if self.conn is None or self.conn.closed:
            self.conn = psycopg2.connect(self.db_url)
        return self.conn

    def init_db(self):
        """ایجاد جداول و مقادیر پیش‌فرض در صورت عدم وجود"""
        conn = self.get_connection()
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS accounts (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR(100) NOT NULL,
                    username VARCHAR(100) NOT NULL,
                    password VARCHAR(100) NOT NULL
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id SERIAL PRIMARY KEY,
                    symbol VARCHAR(50) NOT NULL,
                    order_type VARCHAR(10) NOT NULL,
                    target_time VARCHAR(8) NOT NULL,
                    account_id INTEGER REFERENCES accounts(id) ON DELETE CASCADE,
                    invest_amount INTEGER,
                    quantity INTEGER,
                    amount INTEGER NOT NULL,
                    type VARCHAR(10) NOT NULL
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    key VARCHAR(50) PRIMARY KEY,
                    value TEXT NOT NULL
                );
            """)
            cur.execute("""
                INSERT INTO settings (key, value) VALUES 
                ('precision_ms', '20'),
                ('click_offset_ms', '0')
                ON CONFLICT (key) DO NOTHING;
            """)
            conn.commit()

    # ──────────── Accounts ────────────
    def get_accounts(self):
        conn = self.get_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id, name, username, password FROM accounts ORDER BY id;")
            return [dict(row) for row in cur.fetchall()]

    def add_account(self, name, username, password):
        conn = self.get_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "INSERT INTO accounts (name, username, password) VALUES (%s, %s, %s) RETURNING id;",
                (name, username, password)
            )
            row = cur.fetchone()
            conn.commit()
            return row['id']

    def delete_account(self, account_id):
        conn = self.get_connection()
        with conn.cursor() as cur:
            cur.execute("DELETE FROM accounts WHERE id = %s;", (account_id,))
            conn.commit()

    # ──────────── Trades ────────────
    def get_trades(self):
        conn = self.get_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM trades ORDER BY id;")
            return [dict(row) for row in cur.fetchall()]

    def add_trade(self, symbol, order_type, target_time, account_id, amount, invest_amount=None, quantity=None):
        conn = self.get_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """INSERT INTO trades 
                   (symbol, order_type, target_time, account_id, amount, invest_amount, quantity, type)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                   RETURNING id;""",
                (symbol, order_type, target_time, account_id, amount, invest_amount, quantity, order_type)
            )
            row = cur.fetchone()
            conn.commit()
            return row['id']

    def update_trade(self, trade_id, symbol, order_type, target_time, account_id, amount, invest_amount=None, quantity=None):
        conn = self.get_connection()
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE trades SET 
                   symbol=%s, order_type=%s, target_time=%s, account_id=%s, amount=%s, 
                   invest_amount=%s, quantity=%s, type=%s
                   WHERE id=%s;""",
                (symbol, order_type, target_time, account_id, amount, invest_amount, quantity, order_type, trade_id)
            )
            conn.commit()

    def delete_trade(self, trade_id):
        conn = self.get_connection()
        with conn.cursor() as cur:
            cur.execute("DELETE FROM trades WHERE id = %s;", (trade_id,))
            conn.commit()

    # ──────────── Settings ────────────
    def get_setting(self, key, default=None):
        conn = self.get_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT value FROM settings WHERE key = %s;", (key,))
            row = cur.fetchone()
            return row[0] if row else default

    def set_setting(self, key, value):
        conn = self.get_connection()
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value;",
                (key, value)
            )
            conn.commit()