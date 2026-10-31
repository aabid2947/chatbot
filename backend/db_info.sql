-- Everything in the chatbot database, in four views.
-- Run from the chatbot folder:
--   bash db.sh info

-- 1. Tables, exact row counts and size on disk
SELECT t.table_name AS `table`,
       CASE t.table_name
         WHEN 'conversations' THEN (SELECT COUNT(*) FROM conversations)
         WHEN 'messages'      THEN (SELECT COUNT(*) FROM messages)
       END AS `rows`,
       ROUND((t.data_length + t.index_length) / 1024, 1) AS size_kb
FROM information_schema.tables t
WHERE t.table_schema = DATABASE()
ORDER BY t.table_name;

-- 2. Columns of every table
SELECT c.table_name AS `table`, c.column_name AS `column`, c.column_type AS `type`,
       c.is_nullable AS nullable, c.column_key AS `key`, c.extra
FROM information_schema.columns c
WHERE c.table_schema = DATABASE()
ORDER BY c.table_name, c.ordinal_position;

-- 3. Every chat, newest first, with how many messages it has
SELECT c.id, c.title,
       COUNT(m.id)                         AS messages,
       SUM(m.role = 'user')                AS from_user,
       SUM(m.role = 'assistant')           AS from_bot,
       c.created_at, c.updated_at
FROM conversations c
LEFT JOIN messages m ON m.conversation_id = c.id
GROUP BY c.id, c.title, c.created_at, c.updated_at
ORDER BY c.updated_at DESC;

-- 4. Every message, grouped by chat, oldest first within each chat.
--    Content is cut to 120 characters with line breaks flattened, to keep the
--    table readable. Use m.content instead of the LEFT(...) for full text.
SELECT c.title AS chat, m.id, m.role,
       LEFT(REPLACE(REPLACE(m.content, '\r', ''), '\n', ' '), 120) AS content,
       CHAR_LENGTH(m.content) AS chars,
       m.created_at
FROM messages m
LEFT JOIN conversations c ON c.id = m.conversation_id
ORDER BY c.updated_at DESC, m.id;
