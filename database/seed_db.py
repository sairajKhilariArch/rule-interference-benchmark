
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "research.db"

def seed_database():
    connection = sqlite3.connect(DB_PATH)
    cursor = connection.cursor()

    cursor.execute("PRAGMA foreign_keys = ON")

    cursor.executescript("""
    DELETE FROM refunds;
    DELETE FROM payments;
    DELETE FROM order_items;
    DELETE FROM orders;
    DELETE FROM customers;
    """)

    customers = [
        (1, "Alice", 1, 0),
        (2, "Bob", 0, 0),
        (3, "Charlie", 1, 1),
        (4, "David", 1, 0),
        (5, "Eva", 0, 1)
    ]

    orders = [
        (101, 1, "2026-01-05", "completed"),
        (102, 1, "2026-01-10", "completed"),
        (103, 2, "2026-01-12", "completed"),
        (104, 3, "2026-01-15", "completed"),
        (105, 4, "2026-01-20", "completed")
    ]

    items = [
        (1, 101, "Laptop", 1, 50000),
        (2, 101, "Mouse", 2, 500),
        (3, 102, "Keyboard", 1, 2000),
        (4, 103, "Monitor", 1, 10000),
        (5, 104, "Phone", 1, 20000),
        (6, 105, "Headphones", 2, 1500)
    ]

    payments = [
        (1, 101, 51000),
        (2, 102, 2000),
        (3, 103, 10000),
        (4, 104, 20000),
        (5, 105, 3000)
    ]

    refunds = [
        (1, 101, 5000),
        (2, 105, 500)
    ]

    cursor.executemany(
        "INSERT INTO customers VALUES (?, ?, ?, ?)", customers
    )
    cursor.executemany(
        "INSERT INTO orders VALUES (?, ?, ?, ?)", orders
    )
    cursor.executemany(
        "INSERT INTO order_items VALUES (?, ?, ?, ?, ?)", items
    )
    cursor.executemany(
        "INSERT INTO payments VALUES (?, ?, ?)", payments
    )
    cursor.executemany(
        "INSERT INTO refunds VALUES (?, ?, ?)", refunds
    )

    connection.commit()
    connection.close()

    print("Experimental data inserted successfully.")

if __name__ == "__main__":
    seed_database()

