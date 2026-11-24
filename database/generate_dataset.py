
import argparse
import random
import sqlite3
from pathlib import Path
from datetime import date, timedelta

from setup_db import create_schema


ROOT = Path(__file__).resolve().parent.parent

DATASET_SIZES = [100, 1000, 10000]
DEFAULT_SEED = 20261124


def generate_customers(connection, count, rng):
    customers = []

    for customer_id in range(1, count + 1):
        customers.append((
            customer_id,
            f"Customer_{customer_id}",
            rng.choices([1, 0], weights=[80, 20])[0],
            rng.choices([0, 1], weights=[90, 10])[0]
        ))

    connection.executemany(
        """
        INSERT INTO customers
        (customer_id, name, is_verified, is_deleted)
        VALUES (?, ?, ?, ?)
        """,
        customers
    )


def generate_orders(connection, order_count, customer_count, rng):
    orders = []
    start_date = date(2025, 1, 1)

    for order_id in range(1, order_count + 1):
        customer_id = rng.randint(1, customer_count)
        order_date = start_date + timedelta(days=rng.randint(0, 364))
        status = rng.choices(
            ["completed", "pending", "cancelled"],
            weights=[80, 10, 10]
        )[0]

        orders.append((
            order_id,
            customer_id,
            order_date.isoformat(),
            status
        ))

    connection.executemany(
        """
        INSERT INTO orders
        (order_id, customer_id, order_date, status)
        VALUES (?, ?, ?, ?)
        """,
        orders
    )


def generate_baseline_data(connection, order_count, rng):
    products = [
        "Laptop", "Keyboard", "Mouse", "Monitor",
        "Headphones", "Webcam", "USB Hub", "SSD"
    ]

    order_items = []
    payments = []
    refunds = []

    payment_id = 1
    refund_id = 1
    item_id = 1

    for order_id in range(1, order_count + 1):

        for _ in range(rng.randint(1, 4)):
            order_items.append((
                item_id,
                order_id,
                rng.choice(products),
                rng.randint(1, 5),
                round(rng.uniform(100, 50000), 2)
            ))
            item_id += 1

        total_payment = round(rng.uniform(500, 60000), 2)

        # Some orders have multiple payment records.
        payment_parts = rng.randint(1, 3)
        remaining = total_payment

        for part in range(payment_parts):
            if part == payment_parts - 1:
                amount = remaining
            else:
                amount = round(
                    remaining * rng.uniform(0.2, 0.6), 2
                )
                remaining = round(remaining - amount, 2)

            payments.append((payment_id, order_id, amount))
            payment_id += 1

        # Some orders have refunds, sometimes multiple.
        if rng.random() < 0.25:
            refund_total = round(
                total_payment * rng.uniform(0.05, 0.5), 2
            )
            refund_parts = rng.randint(1, 2)
            remaining = refund_total

            for part in range(refund_parts):
                if part == refund_parts - 1:
                    amount = remaining
                else:
                    amount = round(
                        remaining * rng.uniform(0.2, 0.6), 2
                    )
                    remaining = round(remaining - amount, 2)

                refunds.append((refund_id, order_id, amount))
                refund_id += 1

    connection.executemany(
        """
        INSERT INTO order_items
        (item_id, order_id, product_name, quantity, unit_price)
        VALUES (?, ?, ?, ?, ?)
        """,
        order_items
    )

    connection.executemany(
        """
        INSERT INTO payments (payment_id, order_id, amount)
        VALUES (?, ?, ?)
        """,
        payments
    )

    connection.executemany(
        """
        INSERT INTO refunds (refund_id, order_id, amount)
        VALUES (?, ?, ?)
        """,
        refunds
    )


def generate_expanded_data(connection, order_count, rng):
    categories = [
        (1, "Electronics"),
        (2, "Accessories"),
        (3, "Storage"),
        (4, "Computers")
    ]

    products = [
        (1, "Laptop", 4, 50000.00),
        (2, "Keyboard", 2, 1500.00),
        (3, "Mouse", 2, 800.00),
        (4, "Monitor", 1, 12000.00),
        (5, "Headphones", 2, 2500.00),
        (6, "Webcam", 1, 3000.00),
        (7, "USB Hub", 2, 1000.00),
        (8, "SSD", 3, 6000.00)
    ]

    payment_methods = [
        (1, "Credit Card"),
        (2, "Debit Card"),
        (3, "UPI"),
        (4, "Bank Transfer")
    ]

    refund_reasons = [
        (1, "Damaged Product"),
        (2, "Wrong Product"),
        (3, "Customer Return"),
        (4, "Other")
    ]

    connection.executemany(
        "INSERT INTO categories VALUES (?, ?)", categories
    )
    connection.executemany(
        "INSERT INTO products VALUES (?, ?, ?, ?)", products
    )
    connection.executemany(
        "INSERT INTO payment_methods VALUES (?, ?)", payment_methods
    )
    connection.executemany(
        "INSERT INTO refund_reasons VALUES (?, ?)", refund_reasons
    )

    order_items = []
    payments = []
    refunds = []

    item_id = 1
    payment_id = 1
    refund_id = 1

    for order_id in range(1, order_count + 1):

        for _ in range(rng.randint(1, 4)):
            product_id = rng.randint(1, len(products))

            order_items.append((
                item_id,
                order_id,
                product_id,
                rng.randint(1, 5)
            ))
            item_id += 1

        total_payment = round(rng.uniform(500, 60000), 2)
        payment_parts = rng.randint(1, 3)
        remaining = total_payment

        for part in range(payment_parts):
            if part == payment_parts - 1:
                amount = remaining
            else:
                amount = round(
                    remaining * rng.uniform(0.2, 0.6), 2
                )
                remaining = round(remaining - amount, 2)

            payments.append((
                payment_id,
                order_id,
                rng.randint(1, len(payment_methods)),
                amount
            ))
            payment_id += 1

        if rng.random() < 0.25:
            refund_total = round(
                total_payment * rng.uniform(0.05, 0.5), 2
            )
            refund_parts = rng.randint(1, 2)
            remaining = refund_total

            for part in range(refund_parts):
                if part == refund_parts - 1:
                    amount = remaining
                else:
                    amount = round(
                        remaining * rng.uniform(0.2, 0.6), 2
                    )
                    remaining = round(remaining - amount, 2)

                refunds.append((
                    refund_id,
                    order_id,
                    rng.randint(1, len(refund_reasons)),
                    amount
                ))
                refund_id += 1

    connection.executemany(
        """
        INSERT INTO order_items
        (item_id, order_id, product_id, quantity)
        VALUES (?, ?, ?, ?)
        """,
        order_items
    )

    connection.executemany(
        """
        INSERT INTO payments
        (payment_id, order_id, payment_method_id, amount)
        VALUES (?, ?, ?, ?)
        """,
        payments
    )

    connection.executemany(
        """
        INSERT INTO refunds
        (refund_id, order_id, reason_id, amount)
        VALUES (?, ?, ?, ?)
        """,
        refunds
    )


def generate_dataset(order_count, profile, output_path, seed):
    output_path = Path(output_path).expanduser().resolve()

    if output_path.exists():
        raise FileExistsError(
            f"Dataset already exists: {output_path}"
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)

    rng = random.Random(seed + order_count)

    customer_count = max(20, order_count // 5)

    try:
        with sqlite3.connect(output_path) as connection:
            connection.execute("PRAGMA foreign_keys = ON")

            create_schema(connection, profile)

            generate_customers(
                connection, customer_count, rng
            )

            generate_orders(
                connection, order_count, customer_count, rng
            )

            if profile == "baseline":
                generate_baseline_data(
                    connection, order_count, rng
                )
            else:
                generate_expanded_data(
                    connection, order_count, rng
                )

            connection.commit()

        print(f"Created: {output_path}")
        print(f"Profile: {profile}")
        print(f"Orders: {order_count}")
        print(f"Customers: {customer_count}")

    except Exception:
        if output_path.exists():
            output_path.unlink()
        raise


def main():
    parser = argparse.ArgumentParser(
        description="Generate synthetic SQL research datasets."
    )

    parser.add_argument(
        "--profile",
        choices=["baseline", "expanded"],
        required=True
    )

    parser.add_argument(
        "--sizes",
        nargs="+",
        type=int,
        default=DATASET_SIZES
    )

    parser.add_argument(
        "--output-dir",
        default="results/datasets"
    )

    parser.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_SEED
    )

    args = parser.parse_args()

    for size in args.sizes:
        if size <= 0:
            parser.error("Dataset sizes must be positive.")

    output_dir = Path(args.output_dir)

    for size in args.sizes:
        output_path = (
            output_dir / f"{args.profile}_{size}_orders.db"
        )

        generate_dataset(
            size,
            args.profile,
            output_path,
            args.seed
        )


if __name__ == "__main__":
    main()
