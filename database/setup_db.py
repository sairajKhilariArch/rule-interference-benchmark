
import argparse
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
ORIGINAL_DB = ROOT / "database" / "research.db"


def create_schema(connection, profile="baseline"):
    """Create either the baseline or expanded database schema."""

    if profile not in {"baseline", "expanded"}:
        raise ValueError(f"Unknown database profile: {profile}")

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
    """)

    if profile == "baseline":
        connection.executescript("""
            CREATE TABLE order_items (
                item_id INTEGER PRIMARY KEY,
                order_id INTEGER NOT NULL,
                product_name TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                unit_price REAL NOT NULL,
                FOREIGN KEY (order_id)
                    REFERENCES orders(order_id)
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

    else:
        connection.executescript("""
            CREATE TABLE categories (
                category_id INTEGER PRIMARY KEY,
                category_name TEXT NOT NULL UNIQUE
            );

            CREATE TABLE products (
                product_id INTEGER PRIMARY KEY,
                product_name TEXT NOT NULL,
                category_id INTEGER NOT NULL,
                unit_price REAL NOT NULL CHECK (unit_price >= 0),
                FOREIGN KEY (category_id)
                    REFERENCES categories(category_id)
            );

            CREATE TABLE order_items (
                item_id INTEGER PRIMARY KEY,
                order_id INTEGER NOT NULL,
                product_id INTEGER NOT NULL,
                quantity INTEGER NOT NULL,
                FOREIGN KEY (order_id)
                    REFERENCES orders(order_id),
                FOREIGN KEY (product_id)
                    REFERENCES products(product_id)
            );

            CREATE TABLE payment_methods (
                payment_method_id INTEGER PRIMARY KEY,
                method_name TEXT NOT NULL UNIQUE
            );

            CREATE TABLE payments (
                payment_id INTEGER PRIMARY KEY,
                order_id INTEGER NOT NULL,
                payment_method_id INTEGER NOT NULL,
                amount REAL NOT NULL,
                FOREIGN KEY (order_id)
                    REFERENCES orders(order_id),
                FOREIGN KEY (payment_method_id)
                    REFERENCES payment_methods(payment_method_id)
            );

            CREATE TABLE refund_reasons (
                reason_id INTEGER PRIMARY KEY,
                reason_name TEXT NOT NULL UNIQUE
            );

            CREATE TABLE refunds (
                refund_id INTEGER PRIMARY KEY,
                order_id INTEGER NOT NULL,
                reason_id INTEGER NOT NULL,
                amount REAL NOT NULL,
                FOREIGN KEY (order_id)
                    REFERENCES orders(order_id),
                FOREIGN KEY (reason_id)
                    REFERENCES refund_reasons(reason_id)
            );
        """)

    connection.executescript("""
        CREATE INDEX idx_orders_customer
            ON orders(customer_id);

        CREATE INDEX idx_payments_order
            ON payments(order_id);

        CREATE INDEX idx_refunds_order
            ON refunds(order_id);
    """)


def create_database(output_path, profile):
    """Create a new database without overwriting an existing database."""

    output_path = Path(output_path).expanduser().resolve()

    if output_path == ORIGINAL_DB.resolve():
        raise ValueError("Refusing to modify the original research.db")

    if output_path.exists():
        raise FileExistsError(
            f"Database already exists: {output_path}"
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        with sqlite3.connect(output_path) as connection:
            create_schema(connection, profile)

        print(f"Database created successfully: {output_path}")
        print(f"Schema profile: {profile}")

    except Exception:
        if output_path.exists():
            output_path.unlink()
        raise


def main():
    parser = argparse.ArgumentParser(
        description="Create a research benchmark database."
    )

    parser.add_argument(
        "--profile",
        choices=["baseline", "expanded"],
        required=True,
        help="Database schema profile."
    )

    parser.add_argument(
        "--output",
        required=True,
        help="Path for the new database."
    )

    args = parser.parse_args()

    create_database(args.output, args.profile)


if __name__ == "__main__":
    main()
