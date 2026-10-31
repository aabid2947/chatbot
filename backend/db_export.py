"""Save everything in the chatbot database as CSV files.

Writes four files into ../db_export/ (or the folder given as the first
argument): tables.csv, columns.csv, chats.csv, messages.csv.

Run it with `bash db.sh info` from the chatbot folder.

Files are UTF-8 with a byte-order mark so Excel shows Hindi, emoji etc.
correctly, and every cell is quoted so commas and line breaks inside messages
stay in their cell.
"""

import csv
import os
import sys
from pathlib import Path

import pymysql
from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
load_dotenv(HERE / ".env")

QUERIES = {
    "tables": """
        SELECT t.table_name AS `table`,
               CASE t.table_name
                 WHEN 'conversations' THEN (SELECT COUNT(*) FROM conversations)
                 WHEN 'messages'      THEN (SELECT COUNT(*) FROM messages)
               END AS `rows`,
               ROUND((t.data_length + t.index_length) / 1024, 1) AS size_kb
        FROM information_schema.tables t
        WHERE t.table_schema = DATABASE()
        ORDER BY t.table_name
    """,
    "columns": """
        SELECT c.table_name AS `table`, c.column_name AS `column`,
               c.column_type AS `type`, c.is_nullable AS nullable,
               c.column_key AS `key`, c.extra
        FROM information_schema.columns c
        WHERE c.table_schema = DATABASE()
        ORDER BY c.table_name, c.ordinal_position
    """,
    "chats": """
        SELECT c.id, c.title,
               COUNT(m.id)               AS messages,
               SUM(m.role = 'user')      AS from_user,
               SUM(m.role = 'assistant') AS from_bot,
               c.created_at, c.updated_at
        FROM conversations c
        LEFT JOIN messages m ON m.conversation_id = c.id
        GROUP BY c.id, c.title, c.created_at, c.updated_at
        ORDER BY c.updated_at DESC
    """,
    "messages": """
        SELECT c.title AS chat, m.conversation_id AS chat_id, m.id, m.role,
               m.content, CHAR_LENGTH(m.content) AS chars, m.created_at
        FROM messages m
        LEFT JOIN conversations c ON c.id = m.conversation_id
        ORDER BY c.updated_at DESC, m.id
    """,
}


def main():
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE.parent / "db_export"
    out_dir.mkdir(parents=True, exist_ok=True)

    conn = pymysql.connect(
        host=os.getenv("DB_HOST", "127.0.0.1"),
        port=int(os.getenv("DB_PORT", "3306")),
        user=os.getenv("DB_USER", "chatbot"),
        password=os.getenv("DB_PASSWORD", "chatbot"),
        database=os.getenv("DB_NAME", "chatbot"),
        charset="utf8mb4",
    )
    try:
        with conn.cursor() as cur:
            for name, sql in QUERIES.items():
                cur.execute(sql)
                header = [d[0] for d in cur.description]
                rows = cur.fetchall()
                path = out_dir / f"{name}.csv"
                try:
                    with open(path, "w", newline="", encoding="utf-8-sig") as f:
                        writer = csv.writer(f, quoting=csv.QUOTE_ALL)
                        writer.writerow(header)
                        writer.writerows(rows)
                except PermissionError:
                    sys.exit(f"Can't write {path}. Is it open in Excel? Close it and run again.")
                print(f"  {name + '.csv':<14} {len(rows):>4} rows")
    finally:
        conn.close()

    print(f"\nSaved to {out_dir}")


if __name__ == "__main__":
    main()
