
import sqlite3
import tempfile
from pathlib import Path


def test_join_multiplication():
    with tempfile.TemporaryDirectory() as temp:
        test_db = Path(temp) / "test.db"

        connection = sqlite3.connect(test_db)

        try:
            connection.execute("PRAGMA foreign_keys = ON")

            connection.executescript("""
                CREATE TABLE customers (
                    customer_id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    is_verified INTEGER NOT NULL,
                    is_deleted INTEGER NOT NULL
                );

                CREATE TABLE orders (
                    order_id INTEGER PRIMARY KEY,
                    customer_id INTEGER NOT NULL,
                    order_date TEXT NOT NULL,
                    status TEXT NOT NULL,
                    FOREIGN KEY (customer_id)
                        REFERENCES customers(customer_id)
                );

                CREATE TABLE payments (
                    payment_id INTEGER PRIMARY KEY,
                    order_id INTEGER NOT NULL,
                    amount REAL NOT NULL,
                    FOREIGN KEY (order_id)
                        REFERENCES orders(order_id)
                );

                CREATE TABLE refunds (
                    refund_id INTEGER PRIMARY KEY,
                    order_id INTEGER NOT NULL,
                    amount REAL NOT NULL,
                    FOREIGN KEY (order_id)
                        REFERENCES orders(order_id)
                );
            """)

            connection.execute("""
                INSERT INTO customers
                VALUES (999, 'Join Test', 1, 0)
            """)

            connection.execute("""
                INSERT INTO orders
                VALUES (999, 999, '2026-02-01', 'completed')
            """)

            connection.executemany("""
                INSERT INTO payments
                VALUES (?, 999, ?)
            """, [(9991, 600), (9992, 400)])

            connection.executemany("""
                INSERT INTO refunds
                VALUES (?, 999, ?)
            """, [(9991, 100), (9992, 50)])

            connection.commit()

            # Demonstrate the incorrect result from join fan-out.
            naive_result = connection.execute("""
                SELECT SUM(p.amount) - SUM(r.amount)
                FROM payments p
                JOIN refunds r ON r.order_id = p.order_id
                WHERE p.order_id = 999
            """).fetchone()[0]

            # Correct approach: aggregate each table independently.
            correct_result = connection.execute("""
                SELECT
                    (SELECT COALESCE(SUM(amount), 0)
                     FROM payments WHERE order_id = 999)
                    -
                    (SELECT COALESCE(SUM(amount), 0)
                     FROM refunds WHERE order_id = 999)
            """).fetchone()[0]

            assert naive_result == 1700
            assert correct_result == 850

        finally:
            connection.close()
