
import sqlite3
import tempfile
import shutil

from pathlib import Path

ROOT = Path(__file__).parent.parent
DB_PATH = ROOT / "database" / "research.db"


def test_join_multiplication():

    with tempfile.TemporaryDirectory() as temp:
        test_db = Path(temp) / "test.db"
        shutil.copy2(DB_PATH, test_db)

        connection = sqlite3.connect(test_db)

        try:
            connection.execute("PRAGMA foreign_keys = ON")

            # Create a dedicated test customer
            connection.execute("""
                INSERT INTO customers
                VALUES (999, 'Join Test', 1, 0)
            """)

            connection.execute("""
                INSERT INTO orders
                VALUES (999, 999, '2026-02-01', 'completed')
            """)

            # Two payments
            connection.executemany("""
                INSERT INTO payments
                VALUES (?, 999, ?)
            """, [
                (9991, 600),
                (9992, 400)
            ])

            # Two refunds
            connection.executemany("""
                INSERT INTO refunds
                VALUES (?, 999, ?)
            """, [
                (9991, 100),
                (9992, 50)
            ])

            connection.commit()

            # Correct calculation using separate aggregation
            result = connection.execute("""
                SELECT
                    (SELECT SUM(amount)
                     FROM payments
                     WHERE order_id = 999)
                    -
                    (SELECT SUM(amount)
                     FROM refunds
                     WHERE order_id = 999)
            """).fetchone()[0]

            expected = 850

            assert result == expected

            print("Join multiplication fixture test completed.")

        finally:
            connection.close()


if __name__ == "__main__":
    test_join_multiplication()
